"""Library route status is lightweight, workspace-scoped and read-only."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient
import pytest

from api.main import create_app
from api import source_library, source_routes
from api.services import ProjectService
from api.source_route_status import design_summary
from core.reference_assets import atomic_json
from core.source_design_store import SourceDesignStore
from core.source_library import SourceLibrary
from test_source_library import bundle


@pytest.fixture
def status_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_OS_SETTINGS_PATH", str(tmp_path / "settings.json"))
    projects = ProjectService(tmp_path / "projects")
    app = create_app(projects_root=projects.root, db_url=f"sqlite:///{tmp_path / 'test.db'}")
    library = source_library.store_for(projects)
    book = library.import_files(bundle())
    jobs = {}
    runner = SimpleNamespace(get=lambda key: deepcopy(jobs.get(key)))
    monkeypatch.setattr(source_library, "runner", runner)
    monkeypatch.setattr(source_routes, "runner", runner)
    return SimpleNamespace(client=TestClient(app), projects=projects, library=library,
                           id=book["id"], jobs=jobs, runner=runner,
                           store=SourceDesignStore(library.path(book["id"])))


def add_run(env, status, *, live=False, order=1, mode="original_rewrite", **extra):
    run_id = uuid.uuid4().hex
    value = {
        "run_id": run_id, "source_id": env.id, "status": status,
        "created_at": f"2026-11-12T00:{order:02d}:00+00:00",
        "updated_at": f"2026-11-12T00:{order:02d}:30+00:00",
        "request": {"mode": mode, "operation": "initial_routes", "feedback": "Private author words"},
        "completed": 2, "total": 5, **extra,
    }
    env.store.save_state(value)
    if live:
        job_id = uuid.uuid4().hex
        env.store.write({"run_id": run_id, "job_id": job_id}, "api-jobs", f"{run_id}.json")
        env.jobs[job_id] = {"job_id": job_id, "status": "running", "cancel_requested": False}
    return value


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_new_source_has_no_design_and_gets_create_no_files(status_env):
    env = status_env
    before = snapshot(env.library.root)
    listing = env.client.get("/api/source-books").json()["items"][0]
    detail = env.client.get(f"/api/source-books/{env.id}").json()
    assert listing["design"] == detail["design"] == {
        "status": "not_started", "active_count": 0, "total_runs": 0,
        "completed": 0, "total": 0,
    }
    assert snapshot(env.library.root) == before


def test_live_design_visible_beside_complete_legacy_analysis_and_matches_route_detail(status_env):
    env = status_env
    atomic_json(env.library.path(env.id) / "study/state.json", {"status": "complete", "completed": 1, "total": 1})
    run = add_run(env, "running", live=True)
    before = snapshot(env.library.root)
    listing = env.client.get("/api/source-books").json()["items"][0]
    detail = env.client.get(f"/api/source-books/{env.id}").json()
    route = env.client.get(f"/api/source-books/{env.id}/route-runs/{run['run_id']}").json()
    assert listing["study"]["status"] == "complete"
    assert listing["design"] == detail["design"] == {
        "status": "running", "active_count": 1, "total_runs": 1,
        "latest_run_id": run["run_id"], "completed": 2, "total": 5,
        "mode": "original_rewrite", "operation": "initial_routes", "updated_at": run["updated_at"],
    }
    assert route["worker_active"] and not route["interrupted"]
    assert "feedback" not in json.dumps(listing["design"])
    assert snapshot(env.library.root) == before


@pytest.mark.parametrize(("raw", "expected"), [
    ("queued", "paused"), ("running", "paused"), ("prepared", "paused"),
    ("paused", "paused"), ("cancelled", "paused"), ("interrupted", "paused"),
    ("failed", "needs_attention"), ("error", "needs_attention"),
    ("needs_capacity", "needs_attention"), ("pending_unknown", "needs_attention"),
    ("conflict", "needs_attention"), ("needs_author", "needs_attention"),
    ("ready", "ready"), ("complete", "ready"), ("completed", "ready"),
    ("future_status", "unknown"),
])
def test_stored_status_never_claims_an_orphan_worker_is_running(status_env, raw, expected):
    env = status_env
    run = add_run(env, raw)
    before = snapshot(env.library.root)
    value = design_summary(env.library, env.id, env.runner)
    assert value["status"] == expected
    assert value["active_count"] == 0 and value["completed"] == 2
    if raw in {"prepared", "queued", "running"}:
        detailed = source_routes.public_state(env.projects, env.id, run)
        assert detailed["interrupted"] and not detailed["worker_active"]
    assert snapshot(env.library.root) == before


def test_multiple_modes_prioritize_live_work_then_latest_updated_attempt(status_env):
    env = status_env
    first = add_run(env, "running", live=True, order=1, mode="source_development")
    second = add_run(env, "queued", live=True, order=2, mode="original_rewrite")
    add_run(env, "ready", order=3, mode="reference_creation")
    value = design_summary(env.library, env.id, env.runner)
    assert (value["status"], value["active_count"], value["total_runs"]) == ("running", 2, 3)
    assert value["latest_run_id"] == second["run_id"] and value["mode"] == "original_rewrite"
    env.jobs.clear()
    first.update(status="paused", updated_at="2026-11-12T01:00:00+00:00")
    env.store.save_state(first)
    value = design_summary(env.library, env.id, env.runner)
    assert value["status"] == "paused" and value["latest_run_id"] == first["run_id"]
    first.update(status="failed")
    env.store.save_state(first)
    assert design_summary(env.library, env.id, env.runner)["status"] == "needs_attention"


def test_resumed_older_run_and_multiple_modes_choose_same_work_in_library_and_details(status_env):
    env = status_env
    resumed = add_run(env, "running", live=True, order=1, mode="original_rewrite",
                      updated_at="2026-11-12T01:00:00+00:00")
    another = add_run(env, "queued", live=True, order=2, mode="source_development")
    newer = add_run(env, "failed", order=3, mode="original_rewrite")
    before = snapshot(env.library.root)
    summary = env.client.get("/api/source-books").json()["items"][0]["design"]
    listing = env.client.get(f"/api/source-books/{env.id}/route-sets").json()
    runs = env.client.get(f"/api/source-books/{env.id}/route-runs").json()["items"]
    assert summary["latest_run_id"] == listing["active_run"]["run_id"] == resumed["run_id"]
    assert summary["active_count"] == 2
    expected_order = [resumed["run_id"], another["run_id"], newer["run_id"]]
    assert [row["run_id"] for row in listing["runs"]] == expected_order
    assert [row["run_id"] for row in runs] == expected_order
    assert snapshot(env.library.root) == before
    env.jobs.clear()
    listing = env.client.get(f"/api/source-books/{env.id}/route-sets").json()
    assert listing["active_run"] is None
    assert listing["runs"][0]["run_id"] == resumed["run_id"]
    assert listing["runs"][0]["interrupted"]
    assert env.client.get("/api/source-books").json()["items"][0]["design"]["status"] == "paused"


def test_projection_only_reads_run_and_job_metadata(status_env, monkeypatch):
    env = status_env
    add_run(env, "running", live=True)
    original = Path.read_text
    reads = []

    def guarded(path, *args, **kwargs):
        assert path.name == "state.json" or path.parent.name == "api-jobs", path
        reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    monkeypatch.setattr(SourceLibrary, "texts", lambda *args: pytest.fail("loaded full source"))
    monkeypatch.setattr(SourceDesignStore, "artifacts", lambda *args: pytest.fail("loaded route artifacts"))
    value = design_summary(env.library, env.id, env.runner)
    assert value["status"] == "running" and len(reads) == 2


@pytest.mark.parametrize("damage", ["state", "job", "identity", "progress", "status"])
def test_corrupt_metadata_shows_unknown_without_rewriting_or_hiding_the_book(status_env, damage):
    env = status_env
    run = add_run(env, "running", live=True)
    if damage == "state":
        env.store.path("runs", run["run_id"], "state.json").write_text("{")
    elif damage == "job":
        env.store.write({"run_id": "other", "job_id": "private-credential"}, "api-jobs", run["run_id"] + ".json")
    else:
        run.update({"identity": {"source_id": "other"}, "progress": {"completed": "two"},
                    "status": {"status": []}}[damage])
        env.store.save_state(run)
    before = snapshot(env.library.root)
    response = env.client.get("/api/source-books")
    assert response.status_code == 200
    design = response.json()["items"][0]["design"]
    assert design["status"] == "unknown" and design["active_count"] == 0
    assert "private-credential" not in json.dumps(design)
    assert snapshot(env.library.root) == before


def test_separate_workspaces_with_same_source_do_not_share_design_status(status_env, tmp_path):
    env = status_env
    add_run(env, "running", live=True)
    other_projects = ProjectService(tmp_path / "other/projects")
    other_library = source_library.store_for(other_projects)
    assert other_library.import_files(bundle())["id"] == env.id
    other_app = create_app(projects_root=other_projects.root, db_url=f"sqlite:///{tmp_path / 'other.db'}")
    other = TestClient(other_app)
    assert env.client.get("/api/source-books").json()["items"][0]["design"]["status"] == "running"
    assert other.get("/api/source-books").json()["items"][0]["design"]["status"] == "not_started"


def test_symlinked_run_metadata_is_not_followed(status_env, tmp_path):
    env = status_env
    run = add_run(env, "running")
    path = env.store.path("runs", run["run_id"], "state.json")
    target = tmp_path / "private.json"
    target.write_text("private-data")
    path.unlink()
    path.symlink_to(target)
    assert design_summary(env.library, env.id, env.runner)["status"] == "unknown"
    assert target.read_text() == "private-data"
