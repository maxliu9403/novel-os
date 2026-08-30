"""Advisory binary and visual quality gates for generated cover candidates.

The binary layer is deterministic and runs locally.  The visual layer is an
explicit provider boundary: an unavailable evaluator produces a review-needed
report instead of inventing scores or silently approving an image.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Callable, Mapping

from PIL import Image, ImageOps, UnidentifiedImageError

from .cover_models_v2 import (
    COVER_REPAIR_CODES,
    CoverBriefV2,
    CoverQualityReport,
    CoverScenePlan,
    QualityFinding,
)
from .image_binary import aspect_ratio_matches, content_type, dimensions


MAX_COVER_BYTES = 10 * 1024 * 1024
MAX_THUMBNAIL_SOURCE_PIXELS = 50_000_000


class ThumbnailProjectionError(ValueError):
    """The source image could not be decoded into the review projection."""


def project_cover_thumbnail(data: bytes, *, width: int, height: int) -> bytes:
    """Return a decoded, fixed-size JPEG review projection.

    The original provider bytes are read-only. The projection strips metadata,
    applies EXIF orientation, and uses a high-quality resample so the visual
    evaluator sees the same small-format hierarchy as a mobile reader.
    """
    if width < 1 or height < 1:
        raise ThumbnailProjectionError("Thumbnail dimensions must be positive")
    try:
        with Image.open(BytesIO(data)) as source:
            source.load()
            if source.width * source.height > MAX_THUMBNAIL_SOURCE_PIXELS:
                raise ThumbnailProjectionError("Cover exceeds the thumbnail decoder pixel limit")
            oriented = ImageOps.exif_transpose(source)
            if "A" in oriented.getbands():
                rgba = oriented.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                rgb = background
            else:
                rgb = oriented.convert("RGB")
            projected = ImageOps.fit(
                rgb,
                (width, height),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            output = BytesIO()
            projected.save(
                output,
                format="JPEG",
                quality=88,
                optimize=True,
                progressive=True,
            )
            return output.getvalue()
    except ThumbnailProjectionError:
        raise
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise ThumbnailProjectionError(f"Cover thumbnail projection failed: {exc}") from exc


def evaluate_binary_cover(
    data: bytes,
    *,
    expected_sha256: str,
    expected_content_type: str,
    max_bytes: int = MAX_COVER_BYTES,
) -> tuple[QualityFinding, ...]:
    """Validate bytes and provenance without decoding or transforming them."""
    findings: list[QualityFinding] = []
    actual_type = content_type(data)
    width, height = dimensions(data)
    if not data or not actual_type or not width or not height:
        findings.append(QualityFinding(
            "decode_failure", "blocker", "Cover bytes are empty, truncated, or not a supported image",
            "image header and intrinsic dimensions",
        ))
    if len(data) > max_bytes:
        findings.append(QualityFinding(
            "size_failure", "blocker", f"Cover exceeds the {max_bytes} byte limit", str(len(data)),
        ))
    actual_sha = hashlib.sha256(data).hexdigest()
    if expected_sha256 and actual_sha != expected_sha256:
        findings.append(QualityFinding(
            "sha_mismatch", "blocker", "Cover digest does not match its persisted provenance",
            f"expected {expected_sha256}; actual {actual_sha}",
        ))
    if expected_content_type and actual_type != expected_content_type:
        findings.append(QualityFinding(
            "format_failure", "blocker", "Cover format does not match the requested output format",
            f"expected {expected_content_type}; actual {actual_type or 'unknown'}",
        ))
    if not aspect_ratio_matches(width, height):
        findings.append(QualityFinding(
            "portrait_ratio_failure", "blocker", "Cover must preserve portrait 2:3 framing",
            f"received {width}x{height}",
        ))
    return tuple(findings)


class CoverEvaluatorUnavailable(RuntimeError):
    """Raised when no compatible multimodal evaluator is configured."""


class CoverVisualEvaluator:
    """Provider-neutral visual evaluator protocol with strict report parsing.

    ``complete`` is intentionally injected in tests and adapters.  It receives
    the original image, a thumbnail projection, and the structured brief/scene;
    it must return a JSON-like mapping containing the report fields.
    """

    provider = ""
    model = ""

    def __init__(
        self,
        complete: Callable[[bytes, bytes, CoverBriefV2, CoverScenePlan], Mapping[str, Any]] | None = None,
        *,
        provider: str = "",
        model: str = "",
    ) -> None:
        self._complete = complete
        self.provider = provider.strip()
        self.model = model.strip()

    @property
    def available(self) -> bool:
        return self._complete is not None

    def evaluate(
        self,
        *,
        image: bytes,
        thumbnail: bytes,
        brief: CoverBriefV2,
        scene: CoverScenePlan,
    ) -> CoverQualityReport:
        if self._complete is None:
            raise CoverEvaluatorUnavailable("cover visual evaluator is not configured")
        try:
            payload = self._complete(image, thumbnail, brief, scene)
            if not isinstance(payload, Mapping):
                raise ValueError("visual evaluator response must be an object")
            report = CoverQualityReport.from_dict(payload)
        except CoverEvaluatorUnavailable:
            raise
        except Exception as exc:
            raise CoverEvaluatorUnavailable(f"cover visual evaluator failed: {exc}") from exc
        return report


class UnavailableCoverVisualEvaluator(CoverVisualEvaluator):
    provider = "unavailable"
    model = "unavailable"

    def __init__(self, reason: str = "No compatible visual evaluator is configured") -> None:
        super().__init__(provider=self.provider, model=self.model)
        self.reason = reason

    def evaluate(
        self,
        *,
        image: bytes,
        thumbnail: bytes,
        brief: CoverBriefV2,
        scene: CoverScenePlan,
    ) -> CoverQualityReport:
        del image, thumbnail, brief, scene
        return CoverQualityReport(
            status="human_review_required",
            blockers=(),
            repair_codes=(),
            evidence=(self.reason,),
            evaluator_provider=self.provider,
            evaluator_model=self.model,
            evaluated_at=_now(),
        )


def unavailable_evaluator(reason: str = "No compatible visual evaluator is configured") -> UnavailableCoverVisualEvaluator:
    """Return the explicit no-score evaluator used by local and CI workflows."""
    return UnavailableCoverVisualEvaluator(reason)


def human_review_report(reason: str) -> CoverQualityReport:
    return CoverQualityReport(
        status="human_review_required",
        blockers=(),
        repair_codes=(),
        evidence=(reason,),
        evaluator_provider="unavailable",
        evaluator_model="unavailable",
        evaluated_at=_now(),
    )


def report_from_binary_findings(
    findings: tuple[QualityFinding, ...],
    *,
    provider: str = "local-binary",
    model: str = "image-binary.v1",
) -> CoverQualityReport:
    blockers = tuple(item.code for item in findings if item.severity == "blocker")
    return CoverQualityReport(
        status="blocked" if blockers else "human_review_required",
        blockers=blockers,
        repair_codes=tuple(item.code for item in findings if item.code in COVER_REPAIR_CODES),
        evidence=tuple(item.evidence for item in findings if item.evidence),
        findings=findings,
        evaluator_provider=provider,
        evaluator_model=model,
        evaluated_at=_now(),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "CoverEvaluatorUnavailable",
    "ThumbnailProjectionError",
    "CoverVisualEvaluator",
    "MAX_COVER_BYTES",
    "UnavailableCoverVisualEvaluator",
    "evaluate_binary_cover",
    "human_review_report",
    "project_cover_thumbnail",
    "report_from_binary_findings",
    "unavailable_evaluator",
]
