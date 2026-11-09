import json

import pytest

from artifacts import ArtifactStore
from planning_upgrade import can_upgrade_planning


def seed(tmp_path):
    state = tmp_path / "outputs/state/story_state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"chapters": {}, "characters": {}, "plot_threads": {}}))
    return state, {"status": "paused", "current_phase": "outline", "spec": {},
                   "stages": {"intake": {"status": "done"}, "outline": {"status": "failed"}}}


def test_only_uncommitted_legacy_outline_can_upgrade(tmp_path):
    _, manifest = seed(tmp_path)
    assert can_upgrade_planning(tmp_path, manifest)
    manifest["spec"]["outline_planning_mode"] = "staged"
    assert not can_upgrade_planning(tmp_path, manifest)


@pytest.mark.parametrize("field,value", [("status", "running"), ("status", "completed"),
                                         ("current_phase", "foundation.commit")])
def test_rejects_active_or_later_run(tmp_path, field, value):
    _, manifest = seed(tmp_path)
    manifest[field] = value
    assert not can_upgrade_planning(tmp_path, manifest)


@pytest.mark.parametrize("key", ["chapters", "characters", "plot_threads", "timeline"])
def test_durable_story_state_prevents_upgrade(tmp_path, key):
    state, manifest = seed(tmp_path)
    data = json.loads(state.read_text())
    data[key] = {"one": {"status": "planned"}}
    state.write_text(json.dumps(data))
    assert not can_upgrade_planning(tmp_path, manifest)


@pytest.mark.parametrize("name", ["foundation_journal", "foundation_receipts", "promotion_receipts"])
def test_interrupted_authority_commit_prevents_upgrade(tmp_path, name):
    state, manifest = seed(tmp_path)
    receipt = state.parent / name / "receipt.json"
    receipt.parent.mkdir()
    receipt.write_text("{}")
    assert not can_upgrade_planning(tmp_path, manifest)


def test_missing_corrupt_and_aliased_state_fail_closed(tmp_path):
    state, manifest = seed(tmp_path)
    state.unlink()
    assert not can_upgrade_planning(tmp_path, manifest)
    state.write_text("broken")
    assert not can_upgrade_planning(tmp_path, manifest)
    state.unlink()
    target = tmp_path / "other.json"
    target.write_text('{"chapters": {}, "characters": {}, "plot_threads": {}}')
    state.symlink_to(target)
    assert not can_upgrade_planning(tmp_path, manifest)


@pytest.mark.parametrize("field,value", [("characters", None), ("plot_threads", 0), ("timeline", {})])
def test_invalid_empty_state_is_not_proof_of_no_committed_work(tmp_path, field, value):
    state, manifest = seed(tmp_path)
    data = json.loads(state.read_text())
    data[field] = value
    state.write_text(json.dumps(data))
    assert not can_upgrade_planning(tmp_path, manifest)


def test_canon_ledger_prevents_upgrade_after_state_rollback(tmp_path):
    state, manifest = seed(tmp_path)
    (state.parent / "canon_ledger.jsonl").write_text('{"receipt":"committed"}\n')
    assert not can_upgrade_planning(tmp_path, manifest)


@pytest.mark.parametrize("artifact", ["outputs/manuscript/chapter_001_draft.md", "outputs/chapter_001_outline.md"])
def test_preserved_chapter_work_prevents_upgrade_after_state_rollback(tmp_path, artifact):
    _, manifest = seed(tmp_path)
    path = tmp_path / artifact
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Previously saved work")
    assert not can_upgrade_planning(tmp_path, manifest)


@pytest.mark.parametrize("kind", ["draft", "candidate_final", "final", "chapter_contract"])
def test_immutable_chapter_revision_blocks_upgrade_without_projection_or_head(tmp_path, kind):
    state, manifest = seed(tmp_path)
    artifacts = ArtifactStore(tmp_path)
    artifacts.put_text(chapter=1, kind=kind, text="Preserved chapter work", source="test")
    assert not artifacts.heads_path.exists()
    assert not (tmp_path / "outputs/manuscript").exists()
    assert json.loads(state.read_text())["chapters"] == {}
    assert not can_upgrade_planning(tmp_path, manifest)


def intake_artifacts(tmp_path):
    artifacts = ArtifactStore(tmp_path)
    revision = artifacts.put_text(chapter=0, kind="story_contract", text="Approved intake", source="test")
    artifacts.set_head(0, "story_contract", revision.revision_id, expected_revision_id=None)
    return artifacts, revision


def test_intake_contract_history_and_head_allow_read_only_upgrade_check(tmp_path):
    _, manifest = seed(tmp_path)
    artifacts, _ = intake_artifacts(tmp_path)
    artifacts.lock_path.unlink()
    before = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert can_upgrade_planning(tmp_path, manifest)
    after = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before
    assert not artifacts.lock_path.exists()


@pytest.mark.parametrize("damage", ["revision_json", "revision_metadata", "heads_json", "orphan_head", "blob", "missing_log"])
def test_damaged_artifact_history_cannot_prove_empty_story(tmp_path, damage):
    _, manifest = seed(tmp_path)
    artifacts, revision = intake_artifacts(tmp_path)
    if damage == "revision_json":
        artifacts.revisions_path.write_text("broken\n")
    elif damage == "revision_metadata":
        record = json.loads(artifacts.revisions_path.read_text())
        record["source"] = "tampered"
        artifacts.revisions_path.write_text(json.dumps(record) + "\n")
    elif damage == "heads_json":
        artifacts.heads_path.write_text("{}")
    elif damage == "orphan_head":
        artifacts.revisions_path.unlink()
    elif damage == "missing_log":
        artifacts.revisions_path.unlink()
        artifacts.heads_path.unlink()
    else:
        (artifacts.blob_root / revision.sha256).write_text("tampered")
    assert not can_upgrade_planning(tmp_path, manifest)


@pytest.mark.parametrize("alias", ["root", "revisions", "heads", "blobs", "blob"])
def test_aliased_artifact_history_fails_closed(tmp_path, alias):
    _, manifest = seed(tmp_path)
    artifacts, revision = intake_artifacts(tmp_path)
    path = {"root": artifacts.artifact_root, "revisions": artifacts.revisions_path,
            "heads": artifacts.heads_path, "blobs": artifacts.blob_root,
            "blob": artifacts.blob_root / revision.sha256}[alias]
    target = tmp_path / "alias-target"
    path.rename(target)
    path.symlink_to(target, target_is_directory=target.is_dir())
    assert not can_upgrade_planning(tmp_path, manifest)


def test_completed_outline_or_later_stage_prevents_upgrade(tmp_path):
    _, manifest = seed(tmp_path)
    manifest["stages"]["outline"]["status"] = "done"
    assert not can_upgrade_planning(tmp_path, manifest)
    manifest["stages"]["outline"]["status"] = "failed"
    manifest["stages"]["foundation.commit"] = {"status": "failed"}
    assert not can_upgrade_planning(tmp_path, manifest)


def saved_run(tmp_path):
    from pipeline_models import ManifestStore, RunManifest, RunSpec, StageResult
    seed(tmp_path)
    manifest = RunManifest.new(RunSpec(project_path=str(tmp_path), outline_planning_mode="legacy"), "old-run")
    manifest.status = "paused"
    manifest.record(StageResult(phase="outline", status="failed", error="old failure"))
    store = ManifestStore(tmp_path / "outputs/runs/old-run/run.json")
    store.save(manifest)
    return manifest, store


def test_explicit_upgrade_is_durable_preserves_artifacts_and_policy(tmp_path, monkeypatch):
    from pipeline_runner import PipelineRunner
    manifest, store = saved_run(tmp_path)
    original = manifest.to_dict()
    raw = tmp_path / "outputs/outline.md.raw"
    raw.write_text("saved model response")
    monkeypatch.setattr(PipelineRunner, "_execute", lambda self, manifest, project, store: manifest)
    result = PipelineRunner(tmp_path).resume("old-run", outline_planning_mode="staged")
    assert result.spec.outline_planning_mode == "staged"
    assert result.spec.approval_policy == original["spec"]["approval_policy"]
    assert store.load().spec.outline_planning_mode == "staged"
    assert json.loads((store.path.parent / "pre-staged-planning-run.json").read_text()) == original
    assert raw.read_text() == "saved model response"


def test_upgrade_rechecks_state_inside_execution_lock(tmp_path, monkeypatch):
    from pipeline_runner import PipelineRunner, PipelineError
    manifest, store = saved_run(tmp_path)
    before = store.path.read_bytes()
    assert can_upgrade_planning(tmp_path, manifest.to_dict())
    state = tmp_path / "outputs/state/story_state.json"
    state.write_text('{"chapters":{"1":{"status":"complete"}}}')
    monkeypatch.setattr(PipelineRunner, "_execute", lambda *a: pytest.fail("must not execute"))
    with pytest.raises(PipelineError, match="Cannot upgrade"):
        PipelineRunner(tmp_path).resume("old-run", outline_planning_mode="staged")
    assert store.path.read_bytes() == before


def test_executor_rejects_preserved_revision_after_state_rollback(tmp_path, monkeypatch):
    from pipeline_runner import PipelineRunner, PipelineError
    manifest, store = saved_run(tmp_path)
    before = store.path.read_bytes()
    assert can_upgrade_planning(tmp_path, manifest.to_dict())
    ArtifactStore(tmp_path).put_text(chapter=1, kind="candidate_final", text="Retained prose", source="test")
    monkeypatch.setattr(PipelineRunner, "_execute", lambda *a: pytest.fail("must not execute"))
    with pytest.raises(PipelineError, match="Cannot upgrade"):
        PipelineRunner(tmp_path).resume("old-run", outline_planning_mode="staged")
    assert store.path.read_bytes() == before
    assert not (store.path.parent / "pre-staged-planning-run.json").exists()


def test_plain_resume_upgrades_a_failed_pre_foundation_run(tmp_path, monkeypatch):
    from pipeline_runner import PipelineRunner
    original, store = saved_run(tmp_path)
    draft = tmp_path / "outputs/outline.md"
    draft.write_bytes("仍在校验的原始大纲\r\n".encode())
    monkeypatch.setattr(PipelineRunner, "_execute", lambda self, manifest, project, store: manifest)
    assert PipelineRunner(tmp_path).resume("old-run").spec.outline_planning_mode == "staged"
    assert json.loads((store.path.parent / "pre-staged-planning-run.json").read_text()) == original.to_dict()
    assert (store.path.parent / "pre-staged-outline.md").read_bytes() == draft.read_bytes()


def test_plain_resume_preserves_legacy_work_when_planning_has_already_published(tmp_path, monkeypatch):
    from pipeline_runner import PipelineRunner
    _, store = saved_run(tmp_path)
    foundation = tmp_path / "outputs/input/foundation.json"
    foundation.parent.mkdir(exist_ok=True)
    foundation.write_text('{"title":"Existing story"}')
    monkeypatch.setattr(PipelineRunner, "_execute", lambda self, manifest, project, store: manifest)
    assert PipelineRunner(tmp_path).resume("old-run").spec.outline_planning_mode == "legacy"
    assert foundation.read_text() == '{"title":"Existing story"}'
    assert not (store.path.parent / "pre-staged-planning-run.json").exists()


def test_api_offers_upgrade_only_for_empty_legacy_outline(tmp_path, monkeypatch):
    import threading
    from fastapi.testclient import TestClient
    from api.main import create_app
    from api.jobs import JobRunner
    from api import project_tasks, project_task_summary
    from pipeline_runner import PipelineRunner
    root = tmp_path / "book"
    manifest, _ = saved_run(root)
    jobs = JobRunner()
    monkeypatch.setattr(project_tasks, "runner", jobs)
    monkeypatch.setattr(project_task_summary, "runner", jobs)
    release = threading.Event()
    calls = []
    def execute(self, run_id, project, **kwargs):
        calls.append(kwargs)
        release.wait(3)
        manifest.status = "completed"
        return manifest
    monkeypatch.setattr(PipelineRunner, "resume", execute)
    client = TestClient(create_app(projects_root=tmp_path, db_url=f"sqlite:///{tmp_path / 'test.db'}"))
    url = "/api/projects/book/tasks/old-run"
    assert client.get(url).json()["can_upgrade_planning"] is True
    try:
        response = client.post(url + "/resume")
        assert response.status_code == 202
        duplicate = client.post(url + "/resume", json={"outline_planning_mode": "staged"})
        assert duplicate.json()["job_id"] == response.json()["job_id"]
    finally:
        release.set()
    assert calls == [{"outline_planning_mode": "staged"}]
