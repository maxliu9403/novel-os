from __future__ import annotations

from dataclasses import replace

import pytest

from core.cover_models import CoverBrief, CoverCandidate, CoverConcept, CoverSet
from core.cover_store import CoverConflict, CoverStore
from core.cover_director import CoverArtDirector
from tests.test_cover_director import _brief as direction_brief, adaptive_director_fixture


SHA = "a" * 64


def _set(project: str = "project-one") -> CoverSet:
    brief = CoverBrief.from_dict({
        "schema_version": 1,
        "title": "A House of Her Own",
        "language": "English",
        "genre": "domestic revenge",
        "target_audience": "women 30-50 rebuilding after betrayal",
        "market_scope": "English serialized fiction",
        "core_task": "A caregiver secures an independent home.",
        "core_conflict": "Two families demand her unpaid labor.",
        "emotional_promise": "recognition and boundary-setting",
        "protagonist": {
            "role": "caregiver",
            "visual_identity": "composed woman holding a house key",
            "agency_signal": "closing the old family door",
        },
        "relationship_or_power_contrast": "one woman facing two entitled families",
        "decisive_story_node": "she signs the lease in her own name",
        "secondary_task": {"story_function": "protect child", "visual_signal": "small backpack"},
        "world_signals": ["fictional commuter district"],
        "title_direction": {"hierarchy": "large", "preferred_zone": "top", "readability": "mobile_thumbnail"},
        "forbidden_elements": ["real city names"],
    }, source_prompt_sha256=SHA)
    concepts = [CoverConcept(
        concept_id=f"concept-{i}",
        visual_strategy=f"strategy-{i}",
        focal_scene=f"scene-{i}",
        composition="portrait",
        palette="red and charcoal",
        secondary_signal="house key",
        title_treatment="large exact title",
        generation_prompt=f'Render exact title "A House of Her Own" once, strategy {i}',
    ) for i in range(1, 5)]
    return CoverSet.new(project, brief, concepts)


def test_store_creates_loads_lists_and_updates_by_revision(tmp_path) -> None:
    store = CoverStore(tmp_path)
    created = store.create(_set())

    assert created.revision == 1
    assert store.load(created.cover_set_id) == created
    assert store.list() == [created]

    updated = store.save(replace(created, status="partial"), expected_revision=1)
    assert updated.revision == 2
    with pytest.raises(CoverConflict, match="revision"):
        store.save(replace(updated, status="ready"), expected_revision=1)


def test_store_rejects_changes_to_ready_image_provenance(tmp_path) -> None:
    store = CoverStore(tmp_path)
    created = store.create(_set())
    ready = CoverCandidate(
        candidate_id=created.candidates[0].candidate_id,
        concept_id=created.candidates[0].concept_id,
        status="ready",
        relative_path="outputs/deliverables/covers/pending/cover-01.jpg",
        media_id="media-1",
        sha256=SHA,
        width=2048,
        height=3072,
        content_type="image/jpeg",
        provider="openai_compatible",
        model="gpt-image-2",
        request_id="request-1",
        generation_prompt="prompt",
        safe_request_parameters={"size": "2048x3072"},
    )
    first = store.save(
        replace(created, candidates=(ready, *created.candidates[1:])),
        expected_revision=1,
    )
    tampered = replace(ready, sha256="b" * 64)

    with pytest.raises(CoverConflict, match="immutable"):
        store.save(
            replace(first, candidates=(tampered, *first.candidates[1:])),
            expected_revision=2,
        )


def test_active_pointer_is_revision_checked(tmp_path) -> None:
    store = CoverStore(tmp_path)
    first = store.create(_set())
    second = store.create(_set())

    pointer = store.set_active(first.cover_set_id, expected_revision=0)
    assert pointer.cover_set_id == first.cover_set_id
    assert pointer.revision == 1
    with pytest.raises(CoverConflict, match="active cover revision"):
        store.set_active(second.cover_set_id, expected_revision=0)
    updated = store.set_active(second.cover_set_id, expected_revision=1)
    assert updated.cover_set_id == second.cover_set_id
    assert updated.revision == 2


def test_direction_store_persists_visual_identity_and_evidence_as_reviewable_artifacts(tmp_path) -> None:
    brief = direction_brief()
    direction = CoverArtDirector.from_fixture(adaptive_director_fixture()).plan(
        brief, count=4,
    )

    created = CoverStore(tmp_path).create_direction(direction, brief=brief.to_dict())

    design = tmp_path / "outputs" / "covers" / "design"
    assert design.joinpath("visual-evidence-ledger.json").is_file()
    assert design.joinpath("book-visual-identity.json").is_file()
    assert design.joinpath(f"{created.direction_id}.evidence.json").is_file()
    assert design.joinpath(f"{created.direction_id}.identity.json").is_file()
