import json

import pytest

from foundation_canon import (
    FoundationCanonError,
    FoundationCanonService,
    FoundationIdempotencyConflict,
)
from state_manager import StoryState, initialize_project


def _foundation() -> dict:
    return {
        "title": "North Door",
        "premise": "Mara decides what the locked room means.",
        "themes": ["agency"],
        "setting": {"time_period": "present", "primary_location": "Boston"},
        "characters": [
            {"id": "char_001", "name": "Mara Vale", "role": "protagonist"},
            {"id": "char_002", "name": "Owen Vale", "role": "antagonist"},
        ],
        "plot_threads": [
            {
                "id": "plot_001",
                "name": "The North Door",
                "description": "Mara must decide whether to open it.",
                "type": "main",
                "priority": 5,
                "resolution_chapter": 2,
            }
        ],
        "style": {"tone": "intimate", "pov": "third_limited"},
        "chapters": [
            {"number": 1, "title": "The Key", "pov": "Mara Vale"},
            {"number": 2, "title": "The Door", "pov": "Mara Vale"},
        ],
    }


def test_foundation_initialization_is_durable_and_idempotent(tmp_path):
    project = tmp_path / "project"
    initialize_project(str(project), "North Door", "suspense")
    service = FoundationCanonService(project)

    receipt = service.initialize(
        _foundation(),
        target_words=200,
        idempotency_key="pipeline-run-foundation",
    )

    state = StoryState(str(project))
    assert sorted(state.characters) == ["char_001", "char_002"]
    assert sorted(state.plot_threads) == ["plot_001"]
    assert sorted(state.chapters) == [1, 2]
    assert state.chapters[1].status == "planned"
    assert receipt.old_canon_sha != receipt.new_canon_sha
    assert receipt.foundation_sha256

    receipt_path = (
        project
        / "outputs/state/foundation_receipts/pipeline-run-foundation.json"
    )
    journal_path = (
        project
        / "outputs/state/foundation_journal/pipeline-run-foundation.json"
    )
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["receipt"][
        "receipt_id"
    ] == receipt.receipt_id
    assert json.loads(journal_path.read_text(encoding="utf-8"))["journal"][
        "state"
    ] == "committed"

    assert service.initialize(
        _foundation(),
        target_words=200,
        idempotency_key="pipeline-run-foundation",
    ) == receipt


def test_foundation_idempotency_key_rejects_different_content(tmp_path):
    project = tmp_path / "project"
    initialize_project(str(project), "North Door", "suspense")
    service = FoundationCanonService(project)
    service.initialize(
        _foundation(),
        target_words=200,
        idempotency_key="pipeline-run-foundation",
    )
    changed = _foundation()
    changed["premise"] = "A different story."

    with pytest.raises(FoundationIdempotencyConflict):
        service.initialize(
            changed,
            target_words=200,
            idempotency_key="pipeline-run-foundation",
        )


def test_foundation_rejects_symlink_transaction_directory(tmp_path):
    project = tmp_path / "project"
    initialize_project(str(project), "North Door", "suspense")
    external = tmp_path / "external-journal"
    external.mkdir()
    journal_dir = project / "outputs/state/foundation_journal"
    journal_dir.symlink_to(external, target_is_directory=True)

    with pytest.raises(FoundationCanonError, match="real directory"):
        FoundationCanonService(project).initialize(
            _foundation(),
            target_words=200,
            idempotency_key="pipeline-run-foundation",
        )

    assert list(external.iterdir()) == []


def test_foundation_rejects_symlink_canonical_state(tmp_path):
    project = tmp_path / "project"
    initialize_project(str(project), "North Door", "suspense")
    state_path = project / "outputs/state/story_state.json"
    external_state = tmp_path / "external-state.json"
    external_state.write_bytes(state_path.read_bytes())
    state_path.unlink()
    state_path.symlink_to(external_state)

    with pytest.raises(FoundationCanonError, match="canonical story state"):
        FoundationCanonService(project).initialize(
            _foundation(),
            target_words=200,
            idempotency_key="pipeline-run-foundation",
        )

    assert external_state.read_bytes() == state_path.read_bytes()
