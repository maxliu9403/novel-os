from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace

import pytest

from core.cover_sources import (
    CoverSourceContext, CoverSourceError, CoverSourceSelector, capture_source,
    check_source_head, load_snapshot, preview_source,
)


SESSION = "a" * 32


@pytest.fixture
def sources(tmp_path):
    project = tmp_path / "workspace" / "book"
    (project / "outputs/input").mkdir(parents=True)
    (project / "outputs/input/prompt.md").write_bytes("# 项目\r\nMara closes the door.\r\n".encode())
    workshop = tmp_path / "workshop" / SESSION
    (workshop / "versions").mkdir(parents=True)
    text = "# Current\nMara refuses Oren.\n"
    digest = hashlib.sha256(text.encode()).hexdigest()
    session = {"id": SESSION, "revision": 17, "prompt_md": text, "status": "ready",
               "prepared": {"revision": 17, "prompt_sha256": digest}, "project_id": None}
    (workshop / "session.json").write_text(json.dumps(session))
    (workshop / "prepared.md").write_text(text)
    (workshop / "versions/12.md").write_text("# Earlier\nMara opens the door.\n")
    (workshop / "versions/17.md").write_text(text)
    def project_resolver(project_id):
        if project_id != "book":
            raise CoverSourceError("invalid_scope", "wrong project")
        return project
    context = CoverSourceContext(tmp_path / "covers", "workspace-1:book", project_resolver,
                                 lambda session_id: workshop if session_id == SESSION else None)
    return context, project, workshop, session


def test_four_selectors_keep_exact_distinct_content_and_revision(sources):
    context, project, _, session = sources
    project_source = capture_source(context, {"source_type": "project_input", "project_id": "book"})
    assert project_source.primary_bytes == (project / "outputs/input/prompt.md").read_bytes()
    assert b"\r\n" in project_source.primary_bytes
    current = preview_source(context, CoverSourceSelector("workshop_current", session_id=SESSION))
    assert current.primary_text == current.to_dict()["primary_text"] == session["prompt_md"]
    prepared = capture_source(context, CoverSourceSelector("workshop_prepared", session_id=SESSION))
    archived = capture_source(context, CoverSourceSelector("workshop_archived", session_id=SESSION,
                                                          archive_revision=12, freshness_policy="pinned_snapshot"))
    assert prepared.source_content_sha256 == current.source_content_sha256
    assert archived.source_content_sha256 != current.source_content_sha256
    assert archived.origin["archive_revision"] == 12
    assert archived.origin["observed_session_revision"] == 17
    assert archived.origin["approval_state"] == "unknown"
    assert str(context.cover_root) not in json.dumps(prepared.to_dict())


def test_prepared_capture_checks_metadata_and_file_but_head_does_not_revoke_cover_approval(sources):
    context, _, workshop, session = sources
    selector = CoverSourceSelector("workshop_prepared", session_id=SESSION)
    snapshot = capture_source(context, selector)
    session.update(revision=18, prepared=None)
    (workshop / "session.json").write_text(json.dumps(session))
    with pytest.raises(CoverSourceError) as error:
        capture_source(context, selector)
    assert error.value.code == "source_unapproved"
    assert check_source_head(context, snapshot).source_content_sha256 == snapshot.source_content_sha256
    session["prompt_md"] += "A changed ending."
    (workshop / "session.json").write_text(json.dumps(session))
    with pytest.raises(CoverSourceError) as error:
        check_source_head(context, snapshot)
    assert error.value.code == "source_changed"


def test_missing_archive_never_substitutes_current_version(sources):
    context, _, _, _ = sources
    with pytest.raises(CoverSourceError) as error:
        preview_source(context, CoverSourceSelector("workshop_archived", session_id=SESSION, archive_revision=16))
    assert error.value.code == "archive_not_found"
    assert error.value.details["available_revisions"] == [12, 17]
    with pytest.raises(CoverSourceError) as error:
        preview_source(context, CoverSourceSelector("project_input", project_id="book", expected_content_sha256="0" * 64))
    assert error.value.code == "source_changed"
    with pytest.raises(CoverSourceError) as error:
        preview_source(context, CoverSourceSelector("workshop_current", session_id=SESSION, expected_session_revision=16))
    assert error.value.code == "source_changed"


def test_snapshot_remains_readable_after_source_changes_but_detects_tampering(sources):
    context, project, _, _ = sources
    snapshot = capture_source(context, CoverSourceSelector("project_input", project_id="book"))
    (project / "outputs/input/prompt.md").write_text("changed")
    assert load_snapshot(context, snapshot.snapshot_id).primary_bytes == snapshot.primary_bytes
    with pytest.raises(CoverSourceError) as error:
        check_source_head(context, snapshot)
    assert error.value.code == "source_changed"
    copy = context.cover_root / "sources" / snapshot.snapshot_id / "documents/primary.md"
    copy.write_text("tampered")
    with pytest.raises(CoverSourceError) as error:
        load_snapshot(context, snapshot.snapshot_id)
    assert error.value.code == "snapshot_invalid"


def test_owner_scope_and_path_validation(sources):
    context, project, _, _ = sources
    snapshot = capture_source(context, CoverSourceSelector("project_input", project_id="book"))
    with pytest.raises(CoverSourceError):
        load_snapshot(replace(context, owner_scope="another-workspace:book"), snapshot.snapshot_id)
    with pytest.raises(CoverSourceError):
        capture_source(context, CoverSourceSelector("project_input", project_id="other-book"))
    with pytest.raises(CoverSourceError):
        CoverSourceSelector("project_input", project_id="../book")
    prompt = project / "outputs/input/prompt.md"
    prompt.unlink()
    prompt.symlink_to(context.cover_root / "sources" / snapshot.snapshot_id / "documents/primary.md")
    with pytest.raises(CoverSourceError) as error:
        preview_source(context, CoverSourceSelector("project_input", project_id="book"))
    assert error.value.code == "invalid_scope"


def test_guards_and_required_dependencies_are_applied_to_whole_capture(sources):
    context, project, _, _ = sources
    held = []
    @contextmanager
    def guard(project_id):
        held.append(project_id)
        try:
            yield
        finally:
            held.pop()
    def dependencies(selector, text):
        assert held == ["book"]
        return {"characters": "characters.md"}
    context = replace(context, project_guard=guard, dependency_resolver=dependencies)
    with pytest.raises(CoverSourceError) as error:
        capture_source(context, CoverSourceSelector("project_input", project_id="book"))
    assert error.value.code == "source_incomplete"
    (project / "characters.md").write_text("Mara is a 34-year-old administrator.")
    snapshot = capture_source(context, CoverSourceSelector("project_input", project_id="book"))
    assert snapshot.document_text("characters").startswith("Mara")
    assert len(snapshot.documents) == 2 and held == []
    with pytest.raises(CoverSourceError) as error:
        capture_source(replace(context, dependency_resolver=lambda *_: {"escape": "../outside.md"}),
                       CoverSourceSelector("project_input", project_id="book"))
    assert error.value.code == "invalid_scope"


@pytest.mark.parametrize("manifest", [
    '```COVER_SOURCE_DEPENDENCIES\n{"outline":"outline.md"}\n```\n',
    '## COVER_SOURCE_DEPENDENCIES\n```json\n{"outline":"outline.md"}\n```\n',
])
def test_explicit_narrative_manifest_is_required_without_adapter(sources, manifest):
    context, project, _, _ = sources
    (project / "outputs/input/prompt.md").write_text("# Story\n" + manifest)
    selector = CoverSourceSelector("project_input", project_id="book")
    with pytest.raises(CoverSourceError) as error:
        capture_source(context, selector)
    assert error.value.code == "source_incomplete"
    (project / "outline.md").write_text("The final witness turns out to be the antagonist.")
    snapshot = capture_source(context, selector)
    assert snapshot.document_text("outline").startswith("The final witness")
    (project / "outline.md").write_text("The witness is innocent.")
    with pytest.raises(CoverSourceError, match="changed"):
        check_source_head(context, snapshot)


def test_unstable_sources_fail_instead_of_mixing_revisions(sources):
    context, project, _, _ = sources
    count = 0
    def resolver(_):
        nonlocal count
        count += 1
        (project / "outputs/input/prompt.md").write_text(f"version {count}")
        return project
    with pytest.raises(CoverSourceError) as error:
        capture_source(replace(context, project_resolver=resolver), CoverSourceSelector("project_input", project_id="book"))
    assert error.value.code == "source_changed"
    assert count == 6


@pytest.mark.parametrize("body", [b"", b"   \n", b"\xff"])
def test_empty_or_non_utf8_source_rejected(sources, body):
    context, project, _, _ = sources
    (project / "outputs/input/prompt.md").write_bytes(body)
    with pytest.raises(CoverSourceError) as error:
        preview_source(context, CoverSourceSelector("project_input", project_id="book"))
    assert error.value.code == "source_unreadable"
