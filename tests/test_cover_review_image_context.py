"""Blind artwork observations and final-cover checks have distinct provenance."""
from copy import deepcopy
from dataclasses import replace
import hashlib
from io import BytesIO
import json

from PIL import Image
import pytest

from core.cover_director import CoverArtDirector
from core.cover_quality import CoverVisualEvaluator, build_llm_visual_evaluator
from tests.test_cover_campaign import context
from tests.test_cover_quality import _blind_observation, _scores


def image_bytes(color):
    stream = BytesIO()
    Image.new("RGB", (240, 360), color).save(stream, format="PNG")
    return stream.getvalue()


def source_scene():
    brief, ledger, payload, analysis = context()
    scene = CoverArtDirector.from_fixture(payload).plan(
        brief, count=4, evidence_ledger=ledger, story_analysis=analysis).plans[0]
    return brief, scene


@pytest.mark.parametrize("kind", ["artwork", "native_mask"])
def test_verification_receives_exact_blind_and_final_asset_identity_without_leaking_to_blind(kind):
    brief, scene = source_scene()
    final, artwork = image_bytes("navy"), image_bytes("ivory")
    if kind == "native_mask":
        campaign = deepcopy(scene.campaign)
        campaign["title_render_mode"] = "native"
        campaign["title_layout"].update(mask_verified=True, region=[0, 0, 1, .1], safe_zones=[])
        scene = replace(scene, campaign=campaign)
        artwork = None
    calls, checkpoints = [], []
    observation = _blind_observation()
    observation["uncertainty"].append("No title is visible in the observed image")
    class Client:
        provider = "offline"
        model = "fixture"

        def complete_with_images(self, system, user, images):
            calls.append((system, json.loads(user), images))
            return deepcopy(observation) if len(calls) == 1 else _scores()

    def checkpoint(value):
        checkpoints.append(deepcopy(value))
        return {"saved": True}

    review = build_llm_visual_evaluator(Client()).review(image=final, thumbnail=b"final-thumb",
        artwork=artwork, brief=brief, scene=scene, checkpoint=checkpoint)
    assert len(calls) == 2
    blind_system, blind_request, blind_images = calls[0]
    _, verification, final_images = calls[1]
    identity = verification["review_image_context"]
    assert identity == review.evaluation["review_image_context"] == checkpoints[0]["review_image_context"]
    assert identity["blind_view"]["kind"] == kind
    assert identity["blind_view"]["image_sha256"] == hashlib.sha256(blind_images[0]).hexdigest()
    assert identity["blind_view"]["thumbnail_sha256"] == hashlib.sha256(blind_images[1]).hexdigest()
    assert identity["verification_view"]["kind"] == "final_cover"
    assert identity["verification_view"]["image_sha256"] == hashlib.sha256(final).hexdigest()
    assert identity["verification_view"]["thumbnail_sha256"] == hashlib.sha256(b"final-thumb").hexdigest()
    assert final_images == (final, b"final-thumb")
    assert blind_images[0] != final
    assert "not a contradiction" in identity["comparison_rule"]
    assert "final" in identity["comparison_rule"] and "title" in identity["comparison_rule"]
    assert "unreadable incidental print" in json.dumps(blind_request)
    assert verification["independent_blind_read"] == observation
    assert review.evaluation["blind_read"] == observation
    frozen = json.dumps(observation, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert review.evaluation["blind_read_sha256"] == hashlib.sha256(frozen.encode()).hexdigest()
    blind_text = blind_system + json.dumps(blind_request)
    for private in (brief.title, brief.core_conflict, "scene_contract", "review_image_context", "independent_blind_read"):
        assert private not in blind_text
    assert review.evaluation["blindness_level"] == ("artwork" if kind == "artwork" else "partial")
    if kind == "native_mask":
        assert identity["blind_view"]["masked_region"] == [0, 0, 1, .1]
        assert identity["blind_view"]["limitations"]


def test_contextual_verifier_cannot_rewrite_persisted_observation_or_asset_identity():
    brief, scene = source_scene()
    checkpoints = []
    original = _blind_observation()
    def verify(image, thumb, received_brief, received_scene, observation, image_context):
        assert received_brief == brief and received_scene == scene
        observation["actions"] = ["fabricated action"]
        image_context["blind_view"]["image_sha256"] = "rewritten"
        image_context["verification_view"]["kind"] = "artwork"
        return _scores()

    evaluator = CoverVisualEvaluator(complete=lambda *_: _scores(), blind_complete=lambda *_: deepcopy(original),
                                     verify_context_complete=verify)
    artwork = image_bytes("ivory")
    result = evaluator.review(image=image_bytes("navy"), thumbnail=b"final-thumb", artwork=artwork,
        brief=brief, scene=scene, checkpoint=lambda value: checkpoints.append(deepcopy(value)) or {"saved": True})
    assert result.evaluation["blind_read"] == original
    assert result.evaluation["review_image_context"] == checkpoints[0]["review_image_context"]
    assert result.evaluation["review_image_context"]["blind_view"]["image_sha256"] == hashlib.sha256(artwork).hexdigest()
    assert result.evaluation["review_image_context"]["verification_view"]["kind"] == "final_cover"


def test_existing_five_argument_verifier_and_uncertain_semantic_blockers_are_preserved():
    brief, scene = source_scene()
    original = _blind_observation()
    original["text_contamination"]["uncertain"] = True
    calls = []
    def verify(image, thumbnail, actual_brief, actual_scene, observation):
        calls.append((image, thumbnail, actual_brief, actual_scene, deepcopy(observation)))
        return {**_scores(), "status": "blocked", "blockers": ["core_conflict_missing"],
            "repair_codes": ["core_conflict_missing"], "findings": [{"code": "core_conflict_missing",
                "severity": "blocker", "message": "Action is not legible", "evidence": "Hands rest on a page"}]}

    evaluator = CoverVisualEvaluator(complete=lambda *_: _scores(), blind_complete=lambda *_: deepcopy(original),
                                     verify_complete=verify)
    result = evaluator.review(image=image_bytes("navy"), thumbnail=b"final-thumb", artwork=image_bytes("ivory"),
                              brief=brief, scene=scene)
    assert len(calls) == 1 and calls[0][-1] == original
    assert result.evaluation["blindness_level"] == "unverified"
    assert result.evaluation["text_contamination"] == original["text_contamination"]
    assert result.report.status == "blocked" and result.report.blockers == ("core_conflict_missing",)
    assert result.evaluation["review_image_context"]["blind_view"]["kind"] == "artwork"
