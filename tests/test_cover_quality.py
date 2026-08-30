from __future__ import annotations

import hashlib
from io import BytesIO

from PIL import Image

from core.cover_quality import (
    evaluate_binary_cover,
    project_cover_thumbnail,
    unavailable_evaluator,
)
from core.image_binary import dimensions
from core.cover_models_v2 import CoverQualityReport
from tests.test_cover_director import _brief, director_fixture
from core.cover_director import CoverArtDirector


def _jpeg(width: int = 2048, height: int = 3072) -> bytes:
    return (
        b"\xff\xd8\xff\xc0\x00\x11\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00\xff\xd9"
    )


def test_quality_report_blocks_bad_digest_and_ratio() -> None:
    findings = evaluate_binary_cover(
        b"not-an-image",
        expected_sha256="a" * 64,
        expected_content_type="image/jpeg",
    )
    assert {item.code for item in findings} >= {"decode_failure", "sha_mismatch", "format_failure"}


def test_thumbnail_projection_decodes_and_resizes_without_mutating_source() -> None:
    source = BytesIO()
    Image.new("RGB", (20, 30), (104, 72, 61)).save(source, format="PNG")
    image = source.getvalue()
    original_sha = hashlib.sha256(image).hexdigest()

    thumbnail = project_cover_thumbnail(image, width=120, height=180)

    assert thumbnail != image
    assert dimensions(thumbnail) == (120, 180)
    assert hashlib.sha256(image).hexdigest() == original_sha


def test_unavailable_visual_evaluator_requires_human_review() -> None:
    brief = _brief()
    direction = CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4)
    report = unavailable_evaluator().evaluate(
        image=b"fixture", thumbnail=b"fixture", brief=brief, scene=direction.plans[0]
    )
    assert report.status == "human_review_required"
    assert report.blockers == ()
    assert report.canon_fidelity is None


def test_quality_report_round_trip_preserves_scores_and_findings() -> None:
    report = CoverQualityReport(
        status="recommended_for_human_review",
        canon_fidelity=90,
        required_cast_coverage=95,
        age_and_environment_fidelity=85,
        photorealism=88,
        anatomy_and_physics=84,
        blockers=(),
        repair_codes=("title_failure",),
        evidence=("title safe zone",),
        evaluator_provider="fixture",
        evaluator_model="visual-v1",
    )
    assert CoverQualityReport.from_dict(report.to_dict()) == report
