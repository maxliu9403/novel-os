from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from io import BytesIO

from PIL import Image
import pytest

from core.cover_quality import (
    build_llm_visual_evaluator,
    evaluate_binary_cover,
    project_cover_thumbnail,
    unavailable_evaluator,
    normalize_quality_payload, CoverEvaluationProtocolError, CoverVisualEvaluator,
    classify_cover_repair,
)
from core.image_binary import dimensions
from core.cover_models_v2 import CoverBriefV2, CoverQualityReport
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


def _real_image():
    output = BytesIO()
    Image.new("RGB", (240, 360), (30, 50, 60)).save(output, format="PNG")
    return output.getvalue()


def _blind_observation(*_args):
    return {"relationships": ["one person blocks another"], "actions": ["an interrupted reach"],
            "stakes": ["access seems withheld"], "anomalies": [], "open_questions": ["Will the barrier hold?"],
            "uncertainty": ["identities cannot be determined"], "regions": [],
            "text_contamination": {"detected": False, "uncertain": False, "visible_text": []}}


def _scores():
    return {"status": "human_review_required", **{name: 90 for name in CoverQualityReport._DIMENSIONS},
        "story_assessment": {"conflict": "The barrier shows exclusion", "curiosity": "Will the person leave?", "promise": "Refusal is visible without disclosing the outcome"},
        "craft_assessment": {"hierarchy": "The hand leads to the latch", "performance": "The two eyelines differ", "typography": "Title clears the action"}}


@pytest.mark.parametrize("container", ["scores", "scores_0_to_100"])
def test_score_adapter_preserves_nested_scores_and_rejects_conflicting_root(container):
    payload = {"status": "human_review_required", container: {name: 90 for name in CoverQualityReport._DIMENSIONS}}
    parsed = normalize_quality_payload(payload)
    assert all(parsed[name] == 90 for name in CoverQualityReport._DIMENSIONS)
    with pytest.raises(CoverEvaluationProtocolError, match="Conflicting"):
        normalize_quality_payload({**payload, "canon_fidelity": 10})


@pytest.mark.parametrize("score", [True, "90", 90.5, -1, 101])
def test_score_protocol_does_not_coerce_invalid_values(score):
    with pytest.raises(CoverEvaluationProtocolError):
        normalize_quality_payload({"status": "human_review_required", "canon_fidelity": score})


def test_successful_info_finding_does_not_create_a_repair():
    result = normalize_quality_payload({"status": "human_review_required", "repair_codes": ["title_failure"],
        "findings": [{"code": "title_failure", "severity": "info", "message": "No title failure is visible"}]})
    assert result["repair_codes"] == []


def test_two_phase_review_keeps_story_out_of_blind_request_and_freezes_observations():
    class Client:
        provider = "fixture"
        model = "vision-fixture"
        def __init__(self):
            self.calls = []
        def complete_with_images(self, system, user, images):
            self.calls.append((system, json.loads(user), images))
            return json.dumps(_blind_observation() if len(self.calls) == 1 else _scores())
    client = Client()
    brief = _brief()
    scene = CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4).plans[0]
    image = _real_image()
    reviewed = build_llm_visual_evaluator(client).review(image=image, thumbnail=b"final-thumbnail", artwork=image, brief=brief, scene=scene)
    assert len(client.calls) == 2
    blind_system, blind_request, blind_images = client.calls[0]
    assert brief.title not in blind_system + json.dumps(blind_request)
    assert brief.core_conflict not in json.dumps(blind_request)
    assert "scene_contract" not in blind_request and "story" not in blind_request
    assert blind_images[0] == image and blind_images[1] != b"final-thumbnail"
    assert client.calls[1][1]["independent_blind_read"] == reviewed.evaluation["blind_read"]
    assert client.calls[1][1]["story"]["title"] == brief.title
    assert reviewed.evaluation["evaluation_status"] == "complete"
    assert reviewed.evaluation["blindness_level"] == "artwork"


def test_facts_aware_quality_review_receives_constraints_but_blind_read_does_not():
    class Client:
        provider = "fixture"
        model = "vision-fixture"

        def __init__(self):
            self.calls = []

        def complete_with_images(self, _system, user, _images):
            request = json.loads(user)
            self.calls.append(request)
            return json.dumps(_blind_observation() if len(self.calls) == 1 else _scores())

    payload = _brief().to_dict()
    payload["cover_constraints"] = {
        "constraint_version": "cover-constraints.v1",
        "must_preserve_story_facts": ["the spouses are estranged"],
        "must_show_if_supported": ["door_choice"],
        "must_not_show": ["final reconciliation"],
        "spoiler_ceiling": "Do not reveal the final relationship outcome.",
        "user_visual_preferences": ["restrained photography"],
        "preferred_story_node_ids": ["door_choice"],
        "preferred_character_ids": ["char_mara"],
        "source_refs": ["node:door_choice"],
    }
    brief = CoverBriefV2.from_dict(payload, source_prompt_sha256=_brief().source_prompt_sha256)
    scene = CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4).plans[0]
    client = Client()
    reviewed = build_llm_visual_evaluator(client).review(
        image=_real_image(), thumbnail=b"thumb", artwork=_real_image(), brief=brief, scene=scene,
    )

    assert reviewed.evaluation["evaluation_status"] == "complete"
    assert "author_constraints" not in client.calls[0]
    assert client.calls[1]["author_constraints"]["must_not_show"] == ["final reconciliation"]


def test_blind_observation_cannot_be_changed_by_verification_callback():
    def verify(_image, _thumb, _brief, _scene, observation):
        observation["relationships"] = ["invented correct answer"]
        return _scores()
    evaluator = CoverVisualEvaluator(complete=lambda *_: _scores(), blind_complete=_blind_observation, verify_complete=verify)
    brief = _brief()
    scene = CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4).plans[0]
    reviewed = evaluator.review(image=_real_image(), thumbnail=b"thumb", artwork=_real_image(), brief=brief, scene=scene)
    assert reviewed.evaluation["blind_read"]["relationships"] == ["one person blocks another"]


def test_text_contamination_and_missing_scores_are_not_claimed_as_clean_complete_review():
    def blind(*_args):
        return {**_blind_observation(), "text_contamination": {"detected": True, "uncertain": False, "visible_text": ["Secret Wife"]}}
    evaluator = CoverVisualEvaluator(complete=lambda *_: {}, blind_complete=blind,
        verify_complete=lambda *_: {"status": "human_review_required", "scores": {"canon_fidelity": 95}})
    brief = _brief()
    scene = CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4).plans[0]
    reviewed = evaluator.review(image=_real_image(), thumbnail=b"thumb", artwork=_real_image(), brief=brief, scene=scene)
    assert reviewed.evaluation["blindness_level"] == "contaminated"
    assert reviewed.evaluation["evaluation_status"] == "incomplete"
    assert "thumbnail_clarity" in reviewed.evaluation["missing_dimensions"]
    assert reviewed.report.canon_fidelity == 95


def test_native_title_mask_requires_verification_and_cannot_cover_story_region():
    calls = []
    evaluator = CoverVisualEvaluator(complete=lambda *_: _scores(),
        blind_complete=lambda *args: calls.append(args) or _blind_observation(), verify_complete=lambda *_: _scores())
    brief = _brief()
    scene = CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4).plans[0]
    for layout in ({"region": [0, 0, 1, .2]}, {"region": [0, 0, 1, .2], "mask_verified": True, "safe_zones": [[0, 0, .2, .2]]}):
        reviewed = evaluator.review(image=_real_image(), thumbnail=b"thumb", artwork=None,
            brief=brief, scene=replace(scene, campaign={"title_layout": layout}))
        assert reviewed.evaluation["evaluation_status"] == "protocol_error"
    assert calls == []


def test_repair_classification_never_redraws_for_evaluation_errors_or_new_story_design():
    story = CoverQualityReport(status="blocked", blockers=("core_conflict_missing",), repair_codes=("core_conflict_missing",))
    assert classify_cover_repair(story)["action"] == "new_direction_approval"
    unknown = CoverQualityReport(status="human_review_required")
    assert classify_cover_repair(unknown, evaluation_status="protocol_error")["action"] == "reevaluate"
    title = CoverQualityReport(status="blocked", blockers=("title_layout_failure",))
    assert classify_cover_repair(title, composed=True)["action"] == "recompose_approved_layout"


@pytest.mark.parametrize("missing", ["story_assessment", "craft_assessment"])
def test_new_review_requires_separate_story_and_craft_judgement(missing):
    payload = _scores()
    payload.pop(missing)
    evaluator = CoverVisualEvaluator(complete=lambda *_: payload,
        blind_complete=_blind_observation, verify_complete=lambda *_: payload)
    brief = _brief()
    scene = CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4).plans[0]
    reviewed = evaluator.review(image=_real_image(), thumbnail=b"thumb", artwork=_real_image(), brief=brief, scene=scene)
    assert reviewed.evaluation["evaluation_status"] == "incomplete"
    assert any(name.startswith(missing) for name in reviewed.evaluation["missing_assessments"])


def test_new_review_marks_failure_without_visible_evidence_incomplete():
    payload = {**_scores(), "status": "blocked", "blockers": ["title_failure"], "repair_codes": ["title_failure"]}
    evaluator = CoverVisualEvaluator(complete=lambda *_: payload,
        blind_complete=_blind_observation, verify_complete=lambda *_: payload)
    brief = _brief()
    scene = CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4).plans[0]
    reviewed = evaluator.review(image=_real_image(), thumbnail=b"thumb", artwork=_real_image(), brief=brief, scene=scene)
    assert reviewed.evaluation["evaluation_status"] == "incomplete"
    assert reviewed.evaluation["findings_without_evidence"] == ["title_failure"]
