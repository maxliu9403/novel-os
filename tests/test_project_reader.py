"""Read-only book reader API behavior and integrity boundaries."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.project_reader import router as project_reader_router
from api.routes import get_service
from api.services import ProjectService
from core.artifacts import ArtifactStore


def _seed_project(
    root: Path,
    *,
    project_id: str = "novel",
    chapters: dict[str, dict] | None = None,
) -> Path:
    project = root / project_id
    state_dir = project / "outputs" / "state"
    state_dir.mkdir(parents=True)
    state = {
        "metadata": {"title": "The Reader Test", "author": "A. Writer"},
        "chapters": chapters
        or {
            "1": {"number": 1, "title": "Arrival", "status": "complete"},
        },
    }
    (state_dir / "story_state.json").write_text(
        json.dumps(state),
        encoding="utf-8",
    )
    return project


def _write_legacy(project: Path, chapter: int, version: str, text: str) -> Path:
    manuscript = project / "outputs" / "manuscript"
    manuscript.mkdir(parents=True, exist_ok=True)
    path = manuscript / f"chapter_{chapter:03d}_{version}.md"
    path.write_text(text, encoding="utf-8")
    return path


def _client(root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(project_reader_router)
    app.dependency_overrides[get_service] = lambda: ProjectService(root)
    return TestClient(app)


def _tree_contents(root: Path) -> tuple[tuple[str, str, bytes | str], ...]:
    rows: list[tuple[str, str, bytes | str]] = []
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in sorted(directories):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                rows.append((relative, "symlink", os.readlink(path)))
            else:
                rows.append((relative, "directory", ""))
        for name in sorted(files):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                rows.append((relative, "symlink", os.readlink(path)))
            else:
                rows.append((relative, "file", path.read_bytes()))
    return tuple(sorted(rows))


def test_reader_returns_full_sanitized_body_and_marks_missing_chapters(tmp_path: Path):
    project = _seed_project(
        tmp_path,
        chapters={
            "1": {"number": 1, "title": "", "status": "revised"},
            "2": {"number": 2, "title": "The Empty Room", "status": "planned"},
        },
    )
    body = ("A complete paragraph, with punctuation—unchanged.\n\n" * 2_000).rstrip()
    _write_legacy(
        project,
        1,
        "revised",
        "<!--\nCHAPTER: 1 - The Long Arrival\nPOV: Mara\n-->\n"
        + body
        + "\n[EDITOR_STATE_UPDATE]\nDo not expose this.\n[/EDITOR_STATE_UPDATE]\n",
    )
    client = _client(tmp_path)

    index = client.get("/api/projects/novel/reader")
    chapter = client.get("/api/projects/novel/reader/chapters/1")
    missing = client.get("/api/projects/novel/reader/chapters/2")

    assert index.status_code == 200
    assert index.json() == {
        "title": "The Reader Test",
        "author": "A. Writer",
        "chapters": [
            {
                "number": 1,
                "title": "The Long Arrival",
                "status": "revised",
                "available": True,
            },
            {
                "number": 2,
                "title": "The Empty Room",
                "status": "planned",
                "available": False,
            },
        ],
    }
    assert chapter.status_code == 200
    assert chapter.json() == {
        "number": 1,
        "title": "The Long Arrival",
        "status": "revised",
        "version": "revised",
        "markdown": body,
    }
    assert missing.status_code == 200
    assert missing.json() == {
        "number": 2,
        "title": "The Empty Room",
        "status": "planned",
        "version": None,
        "markdown": "",
    }


def test_canonical_final_wins_over_legacy_final(tmp_path: Path):
    project = _seed_project(tmp_path)
    _write_legacy(project, 1, "final", "Legacy projection must not be shown.")
    store = ArtifactStore(project)
    revision = store.put_text(
        chapter=1,
        kind="final",
        text="<!-- internal note -->\nCanonical final.\n\nStill complete.",
        source="test",
    )
    store.set_head(1, "final", revision.revision_id, expected_revision_id=None)

    response = _client(tmp_path).get("/api/projects/novel/reader/chapters/1")

    assert response.status_code == 200
    assert response.json()["version"] == "final"
    assert response.json()["markdown"] == "Canonical final.\n\nStill complete."


def test_corrupt_canonical_head_is_409_and_never_falls_back(tmp_path: Path):
    project = _seed_project(tmp_path)
    _write_legacy(project, 1, "final", "Legacy fallback would hide corruption.")
    store = ArtifactStore(project)
    revision = store.put_text(
        chapter=1,
        kind="final",
        text="Canonical final.",
        source="test",
    )
    store.set_head(1, "final", revision.revision_id, expected_revision_id=None)
    (store.blob_root / revision.sha256).write_text("tampered", encoding="utf-8")
    client = _client(tmp_path)

    detail = client.get("/api/projects/novel/reader/chapters/1")
    index = client.get("/api/projects/novel/reader")

    assert detail.status_code == 409
    assert detail.json()["detail"]["code"] == "reader_source_invalid"
    assert index.status_code == 409


def test_empty_canonical_head_does_not_resurrect_legacy_projection(tmp_path: Path):
    project = _seed_project(tmp_path)
    _write_legacy(project, 1, "final", "Persisted legacy final.")
    store = ArtifactStore(project)
    revision = store.put_text(chapter=1, kind="final", text="", source="import")
    store.set_head(1, "final", revision.revision_id, expected_revision_id=None)

    response = _client(tmp_path).get("/api/projects/novel/reader/chapters/1")

    assert response.status_code == 200
    assert response.json()["version"] == "final"
    assert response.json()["markdown"] == ""


def test_empty_legacy_final_does_not_resurrect_revised_projection(tmp_path: Path):
    project = _seed_project(tmp_path)
    _write_legacy(project, 1, "final", "<!-- metadata only -->")
    _write_legacy(project, 1, "revised", "Older revised prose.")

    response = _client(tmp_path).get("/api/projects/novel/reader/chapters/1")

    assert response.status_code == 200
    assert response.json()["version"] == "final"
    assert response.json()["markdown"] == ""


def test_reader_does_not_create_artifact_storage_or_change_project_contents(tmp_path: Path):
    project = _seed_project(tmp_path)
    _write_legacy(project, 1, "draft", "Only persisted prose.")
    before = _tree_contents(project)
    service = ProjectService(tmp_path)

    # Calling the reader must not enter the state-ingestion service path.
    service._load = lambda _project_id: pytest.fail("reader called ProjectService._load")  # type: ignore[method-assign]
    app = FastAPI()
    app.include_router(project_reader_router)
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    assert client.get("/api/projects/novel/reader").status_code == 200
    assert client.get("/api/projects/novel/reader/chapters/1").status_code == 200
    assert _tree_contents(project) == before
    assert not (project / "outputs" / "artifacts").exists()


def test_reader_leaves_existing_canonical_artifacts_byte_identical(tmp_path: Path):
    project = _seed_project(tmp_path)
    store = ArtifactStore(project)
    revision = store.put_text(
        chapter=1,
        kind="final",
        text="Immutable canonical prose.",
        source="test",
    )
    store.set_head(1, "final", revision.revision_id, expected_revision_id=None)
    before = _tree_contents(project)

    response = _client(tmp_path).get("/api/projects/novel/reader/chapters/1")

    assert response.status_code == 200
    assert _tree_contents(project) == before


def test_escaping_legacy_symlink_is_rejected(tmp_path: Path):
    project = _seed_project(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("Outside project prose.", encoding="utf-8")
    target = project / "outputs" / "manuscript" / "chapter_001_final.md"
    target.parent.mkdir(parents=True)
    target.symlink_to(outside)

    response = _client(tmp_path).get("/api/projects/novel/reader/chapters/1")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "reader_source_invalid"


def test_escaping_canonical_blob_symlink_is_rejected(tmp_path: Path):
    project = _seed_project(tmp_path)
    store = ArtifactStore(project)
    revision = store.put_text(
        chapter=1,
        kind="final",
        text="Canonical prose.",
        source="test",
    )
    store.set_head(1, "final", revision.revision_id, expected_revision_id=None)
    blob = store.blob_root / revision.sha256
    blob.unlink()
    outside = tmp_path / "outside-blob"
    outside.write_text("Canonical prose.", encoding="utf-8")
    blob.symlink_to(outside)

    response = _client(tmp_path).get("/api/projects/novel/reader/chapters/1")

    assert response.status_code == 409


def test_symlinked_story_state_is_rejected(tmp_path: Path):
    project = tmp_path / "novel"
    state_dir = project / "outputs" / "state"
    state_dir.mkdir(parents=True)
    outside = tmp_path / "outside-state.json"
    outside.write_text(
        json.dumps({
            "metadata": {"title": "Outside"},
            "chapters": {},
        }),
        encoding="utf-8",
    )
    (state_dir / "story_state.json").symlink_to(outside)

    response = _client(tmp_path).get("/api/projects/novel/reader")

    assert response.status_code == 409


def test_story_state_swap_to_symlink_cannot_win_validation_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    project = _seed_project(tmp_path)
    state = project / "outputs" / "state" / "story_state.json"
    outside = tmp_path / "outside-race-state.json"
    secret = "LEAKED-RACE-TITLE"
    outside.write_text(json.dumps({
        "metadata": {"title": secret},
        "chapters": {},
    }))
    from core import safe_project_io

    original_open = safe_project_io.os.open
    swapped = False

    def swap_before_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "story_state.json" and not swapped:
            swapped = True
            state.rename(state.with_suffix(".safe"))
            state.symlink_to(outside)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(safe_project_io.os, "open", swap_before_open)

    response = _client(tmp_path).get("/api/projects/novel/reader")

    assert swapped is True
    assert response.status_code == 409
    assert secret not in response.text


def test_legacy_swap_to_symlink_cannot_win_validation_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    project = _seed_project(tmp_path)
    draft = _write_legacy(project, 1, "draft", "Safe draft.")
    outside = tmp_path / "outside-race-draft.md"
    secret = "LEAKED-RACE-PROSE"
    outside.write_text(secret)
    from core import safe_project_io

    original_open = safe_project_io.os.open
    swapped = False

    def swap_before_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "chapter_001_draft.md" and not swapped:
            swapped = True
            draft.rename(draft.with_suffix(".safe"))
            draft.symlink_to(outside)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(safe_project_io.os, "open", swap_before_open)

    response = _client(tmp_path).get("/api/projects/novel/reader/chapters/1")

    assert swapped is True
    assert response.status_code == 409
    assert secret not in response.text


def test_artifact_metadata_swap_to_symlink_cannot_win_validation_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    project = _seed_project(tmp_path)
    store = ArtifactStore(project)
    revision = store.put_text(
        chapter=1,
        kind="final",
        text="Canonical prose.",
        source="test",
    )
    store.set_head(1, "final", revision.revision_id, expected_revision_id=None)
    heads = store.heads_path
    outside = tmp_path / "outside-race-heads.json"
    secret = "LEAKED-RACE-HEADS"
    outside.write_text(json.dumps({"secret": secret}))
    from core import safe_project_io

    original_open = safe_project_io.os.open
    swapped = False

    def swap_before_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "heads.json" and not swapped:
            swapped = True
            heads.rename(heads.with_suffix(".safe"))
            heads.symlink_to(outside)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(safe_project_io.os, "open", swap_before_open)

    response = _client(tmp_path).get("/api/projects/novel/reader/chapters/1")

    assert swapped is True
    assert response.status_code == 409
    assert secret not in response.text


def test_missing_project_and_unknown_chapter_are_404(tmp_path: Path):
    _seed_project(tmp_path)
    client = _client(tmp_path)

    assert client.get("/api/projects/missing/reader").status_code == 404
    assert client.get("/api/projects/novel/reader/chapters/999").status_code == 404
    assert client.get("/api/projects/../reader").status_code in {404, 422}


def test_reader_routes_are_registered_on_the_main_application(tmp_path: Path):
    _seed_project(tmp_path)
    _write_legacy(tmp_path / "novel", 1, "final", "Registered reader prose.")
    from api.main import create_app

    app = create_app(
        projects_root=tmp_path,
        db_url=f"sqlite:///{tmp_path / 'reader.db'}",
    )

    response = TestClient(app).get("/api/projects/novel/reader/chapters/1")

    assert response.status_code == 200
    assert response.json()["markdown"] == "Registered reader prose."
