from __future__ import annotations

from dataclasses import replace

import pytest

from core.cover_models import CoverBrief, CoverCandidate, CoverConcept, CoverSet


SHA_A = "a" * 64
SHA_B = "b" * 64


def _brief(**overrides) -> CoverBrief:
    data = {
        "schema_version": 1,
        "title": "The Contract She Burned",
        "author": "A. Writer",
        "language": "English",
        "genre": "workplace revenge romance",
        "target_audience": "women 25-44 who enjoy competence and revenge",
        "market_scope": "English-language serialized fiction",
        "core_task": "A dismissed founder returns to reclaim her company.",
        "core_conflict": "Her former partner controls the board and her reputation.",
        "emotional_promise": "anger followed by earned reversal",
        "protagonist": {
            "role": "founder",
            "visual_identity": "decisive executive in a dark tailored suit",
            "agency_signal": "burning the fraudulent transfer contract",
        },
        "relationship_or_power_contrast": "isolated founder against a hostile board",
        "decisive_story_node": "she presents the original voting agreement",
        "secondary_task": {
            "story_function": "prove the betrayal",
            "visual_signal": "a scorched contract with an intact signature",
        },
        "world_signals": ["fictional coastal finance capital", "glass boardroom"],
        "title_direction": {
            "hierarchy": "large title",
            "preferred_zone": "top",
            "readability": "mobile_thumbnail",
        },
        "forbidden_elements": ["real city names", "logos", "ending spoilers"],
    }
    data.update(overrides)
    return CoverBrief.from_dict(data, source_prompt_sha256=SHA_A)


def _concept(index: int, title: str = "The Contract She Burned") -> CoverConcept:
    strategy = f"strategy-{index}"
    return CoverConcept(
        concept_id=f"concept-{index}",
        visual_strategy=strategy,
        focal_scene=f"focal scene {index}",
        composition="portrait confrontation",
        palette="black, white, and signal red",
        secondary_signal="scorched contract",
        title_treatment="large exact English title at top",
        generation_prompt=f'Commercial cover. Render the exact title "{title}" once. {strategy}',
    )


def test_brief_requires_confirmed_story_signals() -> None:
    for field in ("title", "target_audience", "core_conflict", "decisive_story_node"):
        with pytest.raises(ValueError, match=field):
            _brief(**{field: ""})

    with pytest.raises(ValueError, match="protagonist.visual_identity"):
        _brief(protagonist={"role": "founder", "agency_signal": "acts"})


def test_brief_rejects_real_story_facing_locations() -> None:
    with pytest.raises(ValueError, match="fictional"):
        _brief(world_signals=["New York boardroom"])


def test_brief_accepts_three_to_five_distinct_concepts_with_exact_title() -> None:
    brief = _brief()
    assert brief.validate_concepts([_concept(1), _concept(2), _concept(3)])
    assert brief.validate_concepts([_concept(i) for i in range(1, 6)])

    with pytest.raises(ValueError, match="between 3 and 5"):
        brief.validate_concepts([_concept(1), _concept(2)])
    with pytest.raises(ValueError, match="exact title"):
        brief.validate_concepts([_concept(1), _concept(2), _concept(3, "Wrong Title")])
    with pytest.raises(ValueError, match="distinct"):
        brief.validate_concepts([_concept(1), _concept(1), _concept(1)])


def test_cover_set_json_round_trip_and_stale_detection() -> None:
    cover_set = CoverSet.new("project-one", _brief(), [_concept(i) for i in range(1, 5)])
    restored = CoverSet.from_dict(cover_set.to_dict())

    assert restored == cover_set
    assert restored.status == "generating"
    stale = restored.with_source_hashes(SHA_B, "")
    assert stale.status == "stale"
    assert stale.source_prompt_sha256 == SHA_A


def test_ready_candidate_requires_complete_immutable_provenance() -> None:
    with pytest.raises(ValueError, match="ready candidate"):
        CoverCandidate(candidate_id="candidate-1", concept_id="concept-1", status="ready")

    candidate = CoverCandidate(
        candidate_id="candidate-1",
        concept_id="concept-1",
        status="ready",
        relative_path="outputs/deliverables/covers/pending/cover-01.webp",
        media_id="media-1",
        sha256=SHA_A,
        width=2048,
        height=3072,
        content_type="image/webp",
        provider="openai_compatible",
        model="gpt-image-2",
        request_id="req-1",
        generation_prompt="prompt",
        safe_request_parameters={"size": "2048x3072"},
    )
    assert CoverCandidate.from_dict(candidate.to_dict()) == candidate

