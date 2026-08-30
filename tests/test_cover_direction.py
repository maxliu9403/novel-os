from __future__ import annotations

from dataclasses import replace

import pytest

from core.cover_director import CoverArtDirector
from core.cover_store import CoverConflict, CoverStore
from core.cover_validator import validate_direction
from tests.test_cover_director import _brief, director_fixture


def test_store_persists_direction_with_content_hash_and_approves_exact_hash(tmp_path) -> None:
    brief = _brief()
    direction = CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4)
    store = CoverStore(tmp_path / "project")

    created = store.create_direction(direction)

    assert created.direction_id.startswith("direction-")
    assert created.direction_sha256 == created.content_hash()
    approved = store.approve_direction(
        created.direction_id,
        expected_brief_sha256=brief.source_prompt_sha256,
        approved_direction_sha256=created.direction_sha256,
    )

    assert approved.status == "approved"
    assert store.load_direction(created.direction_id).status == "approved"


def test_store_rejects_direction_approval_hash_mismatch(tmp_path) -> None:
    brief = _brief()
    direction = CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4)
    store = CoverStore(tmp_path / "project")
    created = store.create_direction(direction)

    with pytest.raises(CoverConflict, match="hash"):
        store.approve_direction(
            created.direction_id,
            expected_brief_sha256=brief.source_prompt_sha256,
            approved_direction_sha256="b" * 64,
        )


def test_store_forces_new_direction_to_awaiting_approval(tmp_path) -> None:
    direction = replace(
        CoverArtDirector.from_fixture(director_fixture()).plan(_brief(), count=4),
        status="approved",
        created_at="2100-01-01T00:00:00+00:00",
    )

    created = CoverStore(tmp_path / "project").create_direction(direction)

    assert created.status == "awaiting_approval"
    assert created.created_at != direction.created_at


def test_store_only_allows_latest_direction_to_be_approved(tmp_path) -> None:
    brief = _brief()
    store = CoverStore(tmp_path / "project")
    first = store.create_direction(
        CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4)
    )
    store.create_direction(
        CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4)
    )

    with pytest.raises(CoverConflict, match="latest"):
        store.approve_direction(
            first.direction_id,
            expected_brief_sha256=brief.source_prompt_sha256,
            approved_direction_sha256=first.direction_sha256,
        )


def test_store_marks_direction_stale_when_brief_hash_changes(tmp_path) -> None:
    direction = CoverArtDirector.from_fixture(director_fixture()).plan(_brief(), count=4)
    store = CoverStore(tmp_path / "project")
    created = store.create_direction(direction)

    stale = store.mark_direction_stale(created.direction_id, "c" * 64)

    assert stale.status == "stale"
    assert store.load_direction(created.direction_id).status == "stale"


def test_direction_validation_is_required_before_approval(tmp_path) -> None:
    brief = _brief()
    payload = director_fixture()
    payload["plans"][0]["story_evidence_refs"] = ["node:unknown"]
    direction = CoverArtDirector.from_fixture(payload).plan(brief, count=4)

    assert validate_direction(brief, direction)
