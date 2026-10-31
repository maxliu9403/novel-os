"""Regression coverage for the library's bounded artifact projection."""

from api.services import ArtifactStore, ProjectService
from tests.test_project_task_summary import _project


def _finalize_chapters(project, texts):
    store = ArtifactStore(project)
    for number, text in enumerate(texts, 1):
        revision = store.put_text(
            chapter=number,
            kind="final",
            text=text,
            source="test",
        )
        store.set_head(
            number,
            "final",
            revision.revision_id,
            expected_revision_id=None,
        )
    return store


def test_project_list_validates_artifact_journal_once_per_project(tmp_path, monkeypatch):
    project = _project(tmp_path, "book", chapter_statuses=("planned",) * 3)
    _finalize_chapters(project, ("one two", "three four five", "six"))
    original = ArtifactStore._load_revisions
    calls = 0

    def counted(store):
        nonlocal calls
        calls += 1
        return original(store)

    monkeypatch.setattr(ArtifactStore, "_load_revisions", counted)

    summary = ProjectService(tmp_path).list_projects()[0]

    assert calls == 1
    assert summary.word_count == 6
    assert summary.chapter_count == summary.drafted_count == 3
    assert summary.status == "completed"


def test_project_list_reads_new_artifact_head_on_each_request(tmp_path):
    project = _project(tmp_path, "book", chapter_statuses=("planned",))
    store = _finalize_chapters(project, ("first version",))
    service = ProjectService(tmp_path)
    first = service.list_projects()[0]
    previous = store.get_head(1, "final")
    replacement = store.put_text(
        chapter=1,
        kind="final",
        text="replacement now has five words",
        source="test",
        parent_revision_id=previous.revision_id,
    )
    store.set_head(
        1,
        "final",
        replacement.revision_id,
        expected_revision_id=previous.revision_id,
    )

    second = service.list_projects()[0]

    assert first.word_count == 2
    assert second.word_count == 5
