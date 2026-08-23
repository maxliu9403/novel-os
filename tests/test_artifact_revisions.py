"""Behavior tests for immutable, content-addressed artifact revisions."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.artifacts import (
    ArtifactCorruptionError,
    ArtifactHead,
    ArtifactIntegrityError,
    ArtifactStore,
    DuplicateArtifactRevision,
    StaleArtifactHead,
)


def test_same_text_reuses_content_blob_but_creates_traceable_revision(
    tmp_path: Path,
):
    store = ArtifactStore(tmp_path)

    first = store.put_text(
        chapter=1,
        kind="draft",
        text="A choice.",
        source="scribe",
    )
    second = store.put_text(
        chapter=1,
        kind="revised",
        text="A choice.",
        source="editor",
        parent_revision_id=first.revision_id,
    )

    assert first.sha256 == second.sha256
    assert first.revision_id != second.revision_id
    assert store.get_revision(second.revision_id) == second
    assert store.read_text(second.revision_id) == "A choice."
    assert (
        tmp_path / "outputs" / "artifacts" / "sha256" / first.sha256
    ).read_bytes() == b"A choice."


def test_head_promotion_rejects_stale_expected_revision(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    first = store.put_text(chapter=2, kind="final", text="One", source="import")
    second = store.put_text(chapter=2, kind="final", text="Two", source="repair")

    store.set_head(2, "final", first.revision_id, expected_revision_id=None)

    with pytest.raises(StaleArtifactHead):
        store.set_head(
            2,
            "final",
            second.revision_id,
            expected_revision_id="wrong",
        )


def test_modified_blob_is_rejected_on_read(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    revision = store.put_text(
        chapter=1,
        kind="draft",
        text="Original",
        source="scribe",
    )
    blob = tmp_path / "outputs" / "artifacts" / "sha256" / revision.sha256
    blob.write_bytes(b"Tampered")

    with pytest.raises(ArtifactIntegrityError, match=revision.revision_id):
        store.read_text(revision.revision_id)


def test_put_refuses_to_overwrite_existing_corrupt_blob(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    first = store.put_text(
        chapter=1,
        kind="draft",
        text="Shared bytes",
        source="scribe",
    )
    blob = tmp_path / "outputs" / "artifacts" / "sha256" / first.sha256
    blob.write_bytes(b"Corrupt bytes")

    with pytest.raises(ArtifactIntegrityError, match="refusing to overwrite"):
        store.put_text(
            chapter=1,
            kind="revised",
            text="Shared bytes",
            source="editor",
            parent_revision_id=first.revision_id,
        )

    assert blob.read_bytes() == b"Corrupt bytes"
    assert len(
        (
            tmp_path / "outputs" / "artifacts" / "revisions.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ) == 1


def test_fixed_timestamp_exposes_and_rejects_duplicate_revision_id(tmp_path: Path):
    fixed_now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    store = ArtifactStore(tmp_path, clock=lambda: fixed_now)
    first = store.put_text(
        chapter=1,
        kind="draft",
        text="Same record",
        source="scribe",
    )

    with pytest.raises(DuplicateArtifactRevision, match=first.revision_id):
        store.put_text(
            chapter=1,
            kind="draft",
            text="Same record",
            source="scribe",
        )


def test_duplicate_revision_log_entry_is_not_last_write_wins(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    revision = store.put_text(
        chapter=1,
        kind="draft",
        text="Recorded once",
        source="scribe",
    )
    log = tmp_path / "outputs" / "artifacts" / "revisions.jsonl"
    original_line = log.read_bytes()
    with log.open("ab") as handle:
        handle.write(original_line)

    with pytest.raises(DuplicateArtifactRevision, match=revision.revision_id):
        store.get_revision(revision.revision_id)


def test_repeated_identical_head_update_is_idempotent_with_old_expectation(
    tmp_path: Path,
):
    store = ArtifactStore(tmp_path)
    first = store.put_text(chapter=2, kind="final", text="One", source="import")
    second = store.put_text(chapter=2, kind="final", text="Two", source="repair")
    store.set_head(2, "final", first.revision_id, expected_revision_id=None)
    promoted = store.set_head(
        2,
        "final",
        second.revision_id,
        expected_revision_id=first.revision_id,
    )

    repeated = store.set_head(
        2,
        "final",
        second.revision_id,
        expected_revision_id=first.revision_id,
    )

    assert isinstance(repeated, ArtifactHead)
    assert repeated == promoted
    assert store.get_head(2, "final") == promoted


def test_revision_and_head_values_are_immutable(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    revision = store.put_text(
        chapter=1,
        kind="final",
        text="Canonical",
        source="import",
    )
    head = store.set_head(
        1,
        "final",
        revision.revision_id,
        expected_revision_id=None,
    )

    with pytest.raises(FrozenInstanceError):
        revision.kind = "draft"
    with pytest.raises(FrozenInstanceError):
        head.revision_id = "0" * 64


def test_put_json_is_canonical_and_story_contract_head_is_separate(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    revision = store.put_json(
        chapter=0,
        kind="story_contract",
        value={"z": "café", "a": [2, 1]},
        source="architect",
    )

    assert store.read_text(revision.revision_id) == '{"a":[2,1],"z":"café"}'
    assert (
        tmp_path / "outputs" / "artifacts" / "sha256" / revision.sha256
    ).read_bytes() == b'{"a":[2,1],"z":"caf\xc3\xa9"}'
    assert store.get_head(0, "story_contract") is None

    head = store.set_head(
        0,
        "story_contract",
        revision.revision_id,
        expected_revision_id=None,
    )

    assert head.chapter == 0
    assert head.kind == "story_contract"
    assert head.revision_id == revision.revision_id


def test_chapter_contract_uses_its_actual_chapter_for_revision_and_head(
    tmp_path: Path,
):
    store = ArtifactStore(tmp_path)
    revision = store.put_json(
        chapter=3,
        kind="chapter_contract",
        value={"chapter": 3, "goal": "Choose"},
        source="architect",
    )

    head = store.set_head(
        3,
        "chapter_contract",
        revision.revision_id,
        expected_revision_id=None,
    )

    assert store.get_head(3, "chapter_contract") == head
    with pytest.raises(ValueError, match="story_contract.*chapter 0"):
        store.put_json(
            chapter=3,
            kind="story_contract",
            value={},
            source="architect",
        )
    with pytest.raises(ValueError, match="chapter_contract.*positive"):
        store.put_json(
            chapter=0,
            kind="chapter_contract",
            value={},
            source="architect",
        )


def test_contract_revision_references_must_resolve_to_matching_artifacts(
    tmp_path: Path,
):
    store = ArtifactStore(tmp_path)
    story_contract = store.put_json(
        chapter=0,
        kind="story_contract",
        value={"title": "The Choice"},
        source="architect",
    )
    chapter_contract = store.put_json(
        chapter=3,
        kind="chapter_contract",
        value={"chapter": 3},
        source="architect",
        story_contract_revision_id=story_contract.revision_id,
    )
    referenced = store.put_text(
        chapter=3,
        kind="draft",
        text="A choice.",
        source="scribe",
        story_contract_revision_id=story_contract.revision_id,
        chapter_contract_revision_id=chapter_contract.revision_id,
    )

    assert referenced.story_contract_revision_id == story_contract.revision_id
    assert referenced.chapter_contract_revision_id == chapter_contract.revision_id

    with pytest.raises(KeyError, match="unknown story_contract_revision_id"):
        store.put_text(
            chapter=3,
            kind="draft",
            text="Unknown contract",
            source="scribe",
            story_contract_revision_id="0" * 64,
        )
    with pytest.raises(ValueError, match="story_contract_revision_id.*story_contract"):
        store.put_text(
            chapter=3,
            kind="draft",
            text="Wrong contract kind",
            source="scribe",
            story_contract_revision_id=chapter_contract.revision_id,
        )

    other_chapter_contract = store.put_json(
        chapter=4,
        kind="chapter_contract",
        value={"chapter": 4},
        source="architect",
        story_contract_revision_id=story_contract.revision_id,
    )
    with pytest.raises(ValueError, match="chapter_contract_revision_id.*chapter 3"):
        store.put_text(
            chapter=3,
            kind="draft",
            text="Wrong contract chapter",
            source="scribe",
            chapter_contract_revision_id=other_chapter_contract.revision_id,
        )


@pytest.mark.parametrize(
    ("chapter", "kind"),
    [(4, "final"), (3, "draft")],
)
def test_head_target_must_match_revision_chapter_and_kind(
    tmp_path: Path,
    chapter: int,
    kind: str,
):
    store = ArtifactStore(tmp_path)
    revision = store.put_text(
        chapter=3,
        kind="final",
        text="Canonical",
        source="promotion",
    )

    with pytest.raises(ValueError, match="does not match head target"):
        store.set_head(
            chapter,
            kind,
            revision.revision_id,
            expected_revision_id=None,
        )


def test_corrupt_revision_metadata_fails_explicitly(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    revision = store.put_text(
        chapter=1,
        kind="draft",
        text="Valid",
        source="scribe",
    )
    log = tmp_path / "outputs" / "artifacts" / "revisions.jsonl"
    payload = log.read_text(encoding="utf-8").replace(
        '"byte_length":5',
        '"byte_length":6',
    )
    log.write_text(payload, encoding="utf-8")

    with pytest.raises(ArtifactCorruptionError, match="revisions.jsonl"):
        store.get_revision(revision.revision_id)


def test_corrupt_head_file_fails_explicitly(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    revision = store.put_text(chapter=1, kind="final", text="Valid", source="import")
    store.set_head(1, "final", revision.revision_id, expected_revision_id=None)
    heads = tmp_path / "outputs" / "artifacts" / "heads.json"
    heads.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ArtifactCorruptionError, match="heads.json"):
        store.get_head(1, "final")
