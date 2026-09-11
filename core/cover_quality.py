"""Advisory binary and visual quality gates for generated cover candidates.

The binary layer is deterministic and runs locally.  The visual layer is an
explicit provider boundary: an unavailable evaluator produces a review-needed
report instead of inventing scores or silently approving an image.
"""

from __future__ import annotations

import hashlib
import json
import re
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
from .cover_render_policy import RENDER_POLICY, PHOTOGRAPHIC_RENDER_CONTRACT, HUMAN_PERFORMANCE_CONTRACT


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


def automatic_repair_improves(
    prior: CoverQualityReport, repaired: CoverQualityReport,
) -> bool:
    """Conservative promotion, not an aesthetic ranking or publication approval.

    Missing scores are unknown, never a pass. A semantic gain must not buy a
    regression in light/composition, genre emotion, thumbnail or title craft.
    Both attempts remain available for human review regardless of this result.
    """
    if repaired.blockers or repaired.repair_codes or repaired.status == "blocked":
        return False
    dimensions = tuple(name for name in CoverQualityReport._DIMENSIONS
                       if name not in {"medium_fidelity", "photorealism"}) + ("render_fidelity",)
    for dimension in dimensions:
        after = getattr(repaired, dimension)
        before = getattr(prior, dimension)
        if after is None or after < 80 or (before is not None and after < before):
            return False
    return True


def build_llm_visual_evaluator(client: Any) -> CoverVisualEvaluator:
    """Bind a multimodal LLM client to the strict cover review contract."""

    def complete(
        image: bytes,
        thumbnail: bytes,
        brief: CoverBriefV2,
        scene: CoverScenePlan,
    ) -> Mapping[str, Any]:
        system = (
            "You are a forensic book-cover art director. Compare the first attached full cover and the "
            "second attached mobile thumbnail against the supplied source-bound scene contract. Judge only "
            "what is visibly present. Do not reward prompt intent that the image failed to render. Return one "
            "JSON object and no Markdown. " + PHOTOGRAPHIC_RENDER_CONTRACT + " " + HUMAN_PERFORMANCE_CONTRACT
        )
        user = json.dumps({
            "task": "Audit cover semantics, cast, story causality, craft, anatomy, title, and thumbnail reading.",
            "story": {
                "title": brief.title,
                "genre": brief.genre,
                "core_conflict": brief.core_conflict,
                "emotional_promise": brief.emotional_promise,
            },
            "approved_characters": [item.to_dict() for item in brief.principal_characters],
            "scene_contract": scene.to_dict(),
            "render_policy": RENDER_POLICY,
            "required_output": {
                "status": "blocked when any semantic or fidelity blocker exists; otherwise human_review_required",
                "scores_0_to_100": list(CoverQualityReport._DIMENSIONS),
                "blockers": "array of visible failure codes",
                "repair_codes": sorted(COVER_REPAIR_CODES),
                "evidence": "short array describing visible evidence in the images",
                "findings": [{
                    "code": "stable code",
                    "severity": "blocker, warning, or info",
                    "message": "specific visible finding",
                    "evidence": "where it appears",
                }],
                "evaluator_provider": str(getattr(client, "provider", "multimodal")),
                "evaluator_model": str(getattr(client, "model", "")),
                "evaluated_at": _now(),
            },
            "semantic_rules": [
                "core_conflict_fidelity measures whether the image communicates both cause and consequence, not generic sadness",
                "causal_relationship_clarity measures whether the planned pressure person, group, institution, force, or evidence is actually legible",
                "protagonist_agency measures whether the protagonist visibly acts, chooses, refuses, discovers, confronts, or departs rather than merely poses",
                "when causal_visibility is direct and planned conflict characters are absent or unreadable, add blocker and repair code causal_relationship_missing",
                "when the cover reads only as separation, loneliness, or atmosphere instead of the planned conflict, add core_conflict_missing",
                "when protagonist_action_visible is true but the planned response is absent, add protagonist_action_missing; an approved visible reaction is not required to become a new plot action",
                "indirect plans may communicate pressure through their approved visible evidence; do not demand absent pressure characters or rewrite them as ensemble scenes",
                "cinematic_storytelling evaluates designed hierarchy, motivated key/fill lighting, tonal separation and expressive performance, not simply the number of literal story details",
                "genre_emotion evaluates the novel-specific emotional atmosphere and color relationships, not generic sadness or sepia grading",
                "thumbnail_clarity evaluates readable faces, gestures, uncluttered hierarchy and integrated lettering at mobile size",
                "title_legibility_advisory includes exact spelling AND exact reading order: English left-to-right then top-to-bottom; visually reversed or competing title clauses require blocker title_failure and repair code title_failure, even when every word exists",
                "return numeric scores for dimensions you can assess from the images; use null for genuinely unassessable dimensions, never assume a pass",
                "human naturalness is separate from anatomy correctness: inspect stiffness, actual eyeline targets, expression differences, body balance and hand/prop contact; staged gesture readability alone is not good acting",
                "for visibly waxy or overprocessed faces, glassy eyes, repeated facial affect or synthetic cutout edges, use generic_ai_face with concrete visual evidence; do not demand beauty filtering or younger faces",
                "for mannequin posing or implausible simultaneous actions, use weak_story_action with concrete visual evidence; retain the scene and do not replace every gesture with a raised palm",
                "subtle, asymmetric, partially turned expressions can be believable and readable; do not penalize natural focus falloff merely because all background textures are not equally sharp",
                "use only repair codes relevant to visible failures",
                "painting, illustration, CGI, sculpted figures, canvas grain or artificial skin fail the photographic contract even when an older scene contract requested them; report genre_drift and visible evidence",
            ],
        }, ensure_ascii=False, sort_keys=True)
        raw = client.complete_with_images(system, user, (image, thumbnail))
        text = str(raw or "").strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1)
        payload = json.loads(text)
        if not isinstance(payload, Mapping):
            raise ValueError("cover visual evaluator response must be a JSON object")
        result = dict(payload)
        result["evaluator_provider"] = str(getattr(client, "provider", "multimodal"))
        result["evaluator_model"] = str(getattr(client, "model", ""))
        result["evaluated_at"] = _now()
        return result

    return CoverVisualEvaluator(
        complete=complete,
        provider=str(getattr(client, "provider", "multimodal")),
        model=str(getattr(client, "model", "")),
    )


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
    "build_llm_visual_evaluator",
    "evaluate_binary_cover",
    "human_review_report",
    "project_cover_thumbnail",
    "report_from_binary_findings",
    "unavailable_evaluator",
]
