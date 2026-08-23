"""Behavior tests for immutable, content-addressed artifact revisions."""

import hashlib
import json
import threading
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path

import pytest

import core.artifacts as artifacts_module
from core.artifacts import (
    ArtifactCorruptionError,
    ArtifactHead,
    ArtifactIntegrityError,
    ArtifactStore,
    DuplicateArtifactRevision,
    StaleArtifactHead,
)


def _wait_for_race(barrier: threading.Barrier) -> None:
    try:
        barrier.wait(timeout=0.5)
    except threading.BrokenBarrierError:
        pass


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


def test_two_store_instances_linearize_competing_head_updates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    first_store = ArtifactStore(tmp_path)
    second_store = ArtifactStore(tmp_path)
    base = first_store.put_text(
        chapter=2,
        kind="final",
        text="Base",
        source="import",
    )
    first_candidate = first_store.put_text(
        chapter=2,
        kind="final",
        text="First candidate",
        source="editor",
    )
    second_candidate = first_store.put_text(
        chapter=2,
        kind="final",
        text="Second candidate",
        source="repair",
    )
    first_store.set_head(
        2,
        "final",
        base.revision_id,
        expected_revision_id=None,
    )

    original_load_heads = ArtifactStore._load_heads
    race_barrier = threading.Barrier(2)

    def load_heads_at_same_base(store, revisions):
        heads = original_load_heads(store, revisions)
        _wait_for_race(race_barrier)
        return heads

    monkeypatch.setattr(ArtifactStore, "_load_heads", load_heads_at_same_base)
    start_barrier = threading.Barrier(3)
    outcomes = []
    outcomes_guard = threading.Lock()

    def promote(store: ArtifactStore, revision_id: str) -> None:
        try:
            start_barrier.wait(timeout=2)
            store.set_head(
                2,
                "final",
                revision_id,
                expected_revision_id=base.revision_id,
            )
        except StaleArtifactHead:
            outcome = "stale"
        except Exception as exc:  # pragma: no cover - reported by the assertion
            outcome = f"unexpected:{type(exc).__name__}:{exc}"
        else:
            outcome = "success"
        with outcomes_guard:
            outcomes.append(outcome)

    threads = [
        threading.Thread(
            target=promote,
            args=(first_store, first_candidate.revision_id),
            daemon=True,
        ),
        threading.Thread(
            target=promote,
            args=(second_store, second_candidate.revision_id),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()
    start_barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=3)
    monkeypatch.setattr(ArtifactStore, "_load_heads", original_load_heads)

    assert not any(thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["stale", "success"]
    assert first_store.get_head(2, "final").revision_id in {
        first_candidate.revision_id,
        second_candidate.revision_id,
    }


@pytest.mark.parametrize("damage", ["deleted", "tampered"])
def test_set_head_rejects_candidate_with_invalid_blob(
    tmp_path: Path,
    damage: str,
):
    store = ArtifactStore(tmp_path)
    base = store.put_text(
        chapter=2,
        kind="final",
        text="Base",
        source="import",
    )
    candidate = store.put_text(
        chapter=2,
        kind="final",
        text="Candidate",
        source="editor",
    )
    store.set_head(
        2,
        "final",
        base.revision_id,
        expected_revision_id=None,
    )
    candidate_blob = (
        tmp_path / "outputs" / "artifacts" / "sha256" / candidate.sha256
    )
    if damage == "deleted":
        candidate_blob.unlink()
    else:
        candidate_blob.write_bytes(b"Tampered candidate")

    with pytest.raises(ArtifactIntegrityError, match=candidate.revision_id):
        store.set_head(
            2,
            "final",
            candidate.revision_id,
            expected_revision_id=base.revision_id,
        )

    assert store.get_head(2, "final").revision_id == base.revision_id


def test_head_directory_fsync_failure_reports_uncertain_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = ArtifactStore(tmp_path)
    base = store.put_text(
        chapter=2,
        kind="final",
        text="Base",
        source="import",
    )
    candidate = store.put_text(
        chapter=2,
        kind="final",
        text="Candidate",
        source="editor",
    )
    store.set_head(
        2,
        "final",
        base.revision_id,
        expected_revision_id=None,
    )

    def fail_directory_fsync(_path):
        raise OSError("simulated post-replace directory fsync failure")

    monkeypatch.setattr(
        artifacts_module,
        "_fsync_directory",
        fail_directory_fsync,
    )
    with pytest.raises(artifacts_module.ArtifactCommitUncertain) as raised:
        store.set_head(
            2,
            "final",
            candidate.revision_id,
            expected_revision_id=base.revision_id,
        )

    assert raised.value.operation == "head_update"
    assert raised.value.revision_id == candidate.revision_id
    assert store.get_head(2, "final").revision_id == candidate.revision_id

    reconciled = store.set_head(
        2,
        "final",
        candidate.revision_id,
        expected_revision_id=base.revision_id,
    )
    assert reconciled.revision_id == candidate.revision_id


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


def test_two_store_instances_serialize_duplicate_revision_puts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    fixed_now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    first_store = ArtifactStore(tmp_path, clock=lambda: fixed_now)
    second_store = ArtifactStore(tmp_path, clock=lambda: fixed_now)
    original_load_revisions = ArtifactStore._load_revisions
    race_barrier = threading.Barrier(2)

    def load_revisions_at_same_base(store):
        revisions = original_load_revisions(store)
        _wait_for_race(race_barrier)
        return revisions

    monkeypatch.setattr(
        ArtifactStore,
        "_load_revisions",
        load_revisions_at_same_base,
    )
    start_barrier = threading.Barrier(3)
    outcomes = []
    outcomes_guard = threading.Lock()

    def put(store: ArtifactStore) -> None:
        try:
            start_barrier.wait(timeout=2)
            store.put_text(
                chapter=1,
                kind="draft",
                text="Same record",
                source="scribe",
            )
        except DuplicateArtifactRevision:
            outcome = "duplicate"
        except Exception as exc:  # pragma: no cover - reported by the assertion
            outcome = f"unexpected:{type(exc).__name__}:{exc}"
        else:
            outcome = "success"
        with outcomes_guard:
            outcomes.append(outcome)

    threads = [
        threading.Thread(target=put, args=(first_store,), daemon=True),
        threading.Thread(target=put, args=(second_store,), daemon=True),
    ]
    for thread in threads:
        thread.start()
    start_barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=3)
    monkeypatch.setattr(
        ArtifactStore,
        "_load_revisions",
        original_load_revisions,
    )

    assert not any(thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["duplicate", "success"]
    assert len(
        (
            tmp_path / "outputs" / "artifacts" / "revisions.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ) == 1


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


@pytest.mark.parametrize("failure_stage", ["fsync", "replace"])
def test_failed_revision_log_rewrite_preserves_existing_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
):
    store = ArtifactStore(tmp_path)
    first = store.put_text(
        chapter=1,
        kind="draft",
        text="Shared bytes",
        source="scribe",
    )
    log = tmp_path / "outputs" / "artifacts" / "revisions.jsonl"
    original_log = log.read_bytes()

    if failure_stage == "fsync":

        def fail_fsync(_fd):
            raise OSError("simulated revision log fsync failure")

        monkeypatch.setattr(artifacts_module.os, "fsync", fail_fsync)
    else:
        original_replace = artifacts_module.os.replace

        def fail_revision_log_replace(source, destination):
            if Path(destination) == log:
                raise OSError("simulated revision log replace failure")
            return original_replace(source, destination)

        monkeypatch.setattr(
            artifacts_module.os,
            "replace",
            fail_revision_log_replace,
        )

    with pytest.raises(ArtifactCorruptionError):
        store.put_text(
            chapter=1,
            kind="revised",
            text="Shared bytes",
            source="editor",
            parent_revision_id=first.revision_id,
        )

    assert log.read_bytes() == original_log
    assert store.read_text(first.revision_id) == "Shared bytes"
    assert list(log.parent.glob(".revisions.jsonl.tmp-*")) == []


def test_revision_log_directory_fsync_failure_reports_uncertain_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = ArtifactStore(tmp_path)
    first = store.put_text(
        chapter=1,
        kind="draft",
        text="Shared bytes",
        source="scribe",
    )

    def fail_directory_fsync(_path):
        raise OSError("simulated post-replace directory fsync failure")

    monkeypatch.setattr(
        artifacts_module,
        "_fsync_directory",
        fail_directory_fsync,
    )
    with pytest.raises(artifacts_module.ArtifactError) as raised:
        store.put_text(
            chapter=1,
            kind="revised",
            text="Shared bytes",
            source="editor",
            parent_revision_id=first.revision_id,
        )

    log = tmp_path / "outputs" / "artifacts" / "revisions.jsonl"
    records = [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
    ]
    committed_revision_id = records[-1]["revision_id"]
    committed = store.get_revision(committed_revision_id)
    assert committed.parent_revision_id == first.revision_id

    uncertain_type = getattr(artifacts_module, "ArtifactCommitUncertain", None)
    assert uncertain_type is not None
    assert isinstance(raised.value, uncertain_type)
    assert raised.value.operation == "revision_log_append"
    assert raised.value.revision_id == committed_revision_id
    assert "reconcile" in str(raised.value)
    assert "ArtifactCommitUncertain" in artifacts_module.__all__


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


@pytest.mark.parametrize(
    ("parent_case", "error_match"),
    [
        ("unknown", "parent.*unknown"),
        ("other_chapter", "parent.*chapter"),
    ],
)
def test_corrupt_revision_parent_chain_fails_explicitly(
    tmp_path: Path,
    parent_case: str,
    error_match: str,
):
    store = ArtifactStore(tmp_path)
    local_parent = store.put_text(
        chapter=1,
        kind="draft",
        text="Parent",
        source="scribe",
    )
    child = store.put_text(
        chapter=1,
        kind="revised",
        text="Child",
        source="editor",
        parent_revision_id=local_parent.revision_id,
    )
    later_other_chapter = store.put_text(
        chapter=2,
        kind="draft",
        text="Later revision",
        source="scribe",
    )
    bad_parent_id = (
        "0" * 64
        if parent_case == "unknown"
        else later_other_chapter.revision_id
    )
    log = tmp_path / "outputs" / "artifacts" / "revisions.jsonl"
    records = [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
    ]
    child_record = next(
        record for record in records if record["revision_id"] == child.revision_id
    )
    child_record["parent_revision_id"] = bad_parent_id
    identity = {
        key: value
        for key, value in child_record.items()
        if key != "revision_id"
    }
    child_record["revision_id"] = hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    log.write_text(
        "".join(
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for record in records
        ),
        encoding="utf-8",
    )

    with pytest.raises(ArtifactCorruptionError, match=error_match):
        store.get_revision(child_record["revision_id"])


def test_corrupt_head_file_fails_explicitly(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    revision = store.put_text(chapter=1, kind="final", text="Valid", source="import")
    store.set_head(1, "final", revision.revision_id, expected_revision_id=None)
    heads = tmp_path / "outputs" / "artifacts" / "heads.json"
    heads.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ArtifactCorruptionError, match="heads.json"):
        store.get_head(1, "final")
