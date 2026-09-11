from __future__ import annotations

import hashlib
from io import BytesIO

from PIL import Image

from core.cover_quality import (
    build_llm_visual_evaluator,
    evaluate_binary_cover,
    project_cover_thumbnail,
    unavailable_evaluator,
)
from core.image_binary import dimensions
from core.cover_models_v2 import CoverQualityReport
from tests.test_cover_director import _brief, director_fixture
from tests.test_cover_director import adaptive_director_fixture
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


def test_non_photographic_cover_uses_medium_fidelity_for_recommendation() -> None:
    report = CoverQualityReport(
        status="recommended_for_human_review",
        canon_fidelity=90,
        required_cast_coverage=92,
        age_and_environment_fidelity=88,
        medium_fidelity=91,
        photorealism=20,
        anatomy_and_physics=86,
    )

    assert report.render_fidelity == 91
    assert CoverQualityReport.from_dict(report.to_dict()) == report


def test_multimodal_visual_evaluator_audits_rendered_conflict_not_prompt_intent() -> None:
    class Client:
        provider = "codex"
        model = "fixture-vision"

        def __init__(self) -> None:
            self.images = ()
            self.user = ""

        def complete_with_images(self, _system, user, images):
            self.images = images
            self.user = user
            return "```json\n" + __import__("json").dumps({
                "status": "blocked",
                "canon_fidelity": 72,
                "required_cast_coverage": 45,
                "age_and_environment_fidelity": 90,
                "medium_fidelity": 88,
                "photorealism": 88,
                "anatomy_and_physics": 91,
                "cinematic_storytelling": 70,
                "genre_emotion": 83,
                "thumbnail_clarity": 76,
                "hook_promise_alignment": 52,
                "core_conflict_fidelity": 40,
                "causal_relationship_clarity": 20,
                "protagonist_agency": 84,
                "title_legibility_advisory": 90,
                "blockers": ["causal_relationship_missing"],
                "repair_codes": ["causal_relationship_missing"],
                "evidence": ["Only the protagonist is visible in the thumbnail"],
                "findings": [],
                "evaluator_provider": "codex",
                "evaluator_model": "fixture-vision",
                "evaluated_at": "2026-09-05T00:00:00Z"
            }) + "\n```"

    client = Client()
    brief = _brief()
    direction = CoverArtDirector.from_fixture(adaptive_director_fixture()).plan(brief, count=4)

    report = build_llm_visual_evaluator(client).evaluate(
        image=b"full-image",
        thumbnail=b"thumbnail",
        brief=brief,
        scene=direction.plans[0],
    )

    assert report.status == "blocked"
    assert report.causal_relationship_clarity == 20
    assert report.repair_codes == ("causal_relationship_missing",)
    assert client.images == (b"full-image", b"thumbnail")
    assert "Judge only" not in client.user
    assert "causal_visibility" in client.user
    assert '"medium": "live_action_photography"' in client.user
    assert "fail the photographic contract" in client.user
    assert "human naturalness is separate from anatomy correctness" in client.user
    assert "generic_ai_face with concrete visual evidence" in client.user
    assert "do not replace every gesture with a raised palm" in client.user
    assert "not equally sharp" in client.user
