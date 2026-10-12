"""Saved delivery stages survive failures without crossing author revisions."""
import hashlib
import json
import threading

import pytest

from api import workshop, workshop_checkpoints as checkpoints
from core.llm_client import LLMCancelledError
from test_workshop_api import client, complete_prompt, create, finish, model_result  # noqa: F401


def delivery_record(snapshot, count=1, prompt=""):
    from api.workshop_delivery import checkpoint_fingerprint

    return {
        "schema_version": checkpoints.SCHEMA_VERSION,
        "run_id": snapshot["delivery_run_id"], "session_id": snapshot["id"],
        "source_revision": snapshot["delivery_source_revision"],
        "input_sha256": checkpoint_fingerprint(snapshot, snapshot["skill_version"]),
        "skill_version": snapshot["skill_version"], "narrative_mode": "short_novel",
        "active_stage": checkpoints.STAGES[count] if count < 3 else None,
        "completed": {stage: {
            "content": {"stage": stage, **({"prompt_md": prompt} if stage == "assembly" else {})},
            "content_md": prompt if stage == "assembly" else f"# {stage}\n保留完整的故事设计与因果。",
            "saved_at": "2026-10-12T10:00:00Z",
        } for stage in checkpoints.STAGES[:count]},
    }


def fail_after_saving(client, monkeypatch, tmp_path, *, count=1, prompt=""):
    snapshots = []

    def fail(snapshot, _skill):
        snapshots.append(snapshot)
        snapshot["_save_delivery_checkpoint"](delivery_record(snapshot, count, prompt))
        raise TimeoutError("PRIVATE MODEL PROMPT")

    monkeypatch.setattr(workshop, "_complete", fail)
    data = create(client)
    url = f'/api/workshop/sessions/{data["id"]}'
    submitted = client.post(url + "/turns", json={"revision": 0, "message": "开始完整推导"})
    assert submitted.status_code == 202
    failed = finish(client, data["id"])
    assert failed["status"] == "error"
    path = tmp_path / "workshop_v2" / data["id"] / "delivery" / f'{failed["delivery_run_id"]}.json'
    return url, failed, path, snapshots


def test_error_resume_reuses_valid_saved_stages_and_publishes_complete_result(client, monkeypatch, tmp_path):
    prompt = complete_prompt(tmp_path)
    url, failed, path, original = fail_after_saving(client, monkeypatch, tmp_path)
    assert failed["delivery_progress"]["resumable"]
    assert failed["delivery_progress"]["completed_stages"] == ["story_design"]
    saved = checkpoints.load_checkpoint(path.parent, failed["delivery_run_id"])
    resumed = []

    def complete(snapshot, _skill):
        resumed.append(snapshot)
        assert snapshot["_delivery_record"] == saved
        assert "_previous_candidate" not in snapshot
        assert snapshot["_inference_lock_path"].name == ".inference.lock"
        snapshot["_save_delivery_checkpoint"](delivery_record(snapshot, 3, prompt))
        return model_result(prompt)

    # An old unvalidated candidate must never replace the saved stage context.
    candidate_dir = path.parent.parent / "candidates"
    candidate_dir.mkdir()
    (candidate_dir / "0.md").write_text("STALE UNVALIDATED CANDIDATE")
    monkeypatch.setattr(workshop, "_complete", complete)
    response = client.post(url + "/resume", json={"revision": failed["revision"]})
    assert response.status_code == 202, response.text
    finished = finish(client, failed["id"])
    assert finished["status"] == "ready", finished
    assert finished["prompt_md"] == prompt
    assert finished["delivery_published_run_id"] == failed["delivery_run_id"]
    assert finished["delivery_progress"]["status"] == "complete"
    assert not finished["delivery_progress"]["resumable"]
    assert resumed[0]["delivery_source_revision"] == original[0]["revision"]
    assert resumed[0]["delivery_run_id"] == original[0]["delivery_run_id"]
    assert len(resumed) == 1
    original_bundle = workshop._skill_bundle
    monkeypatch.setattr(workshop, "_skill_bundle", lambda phase="design": (original_bundle(phase)[0], "new-skill"))
    assert client.get(url).json()["delivery_progress"]["status"] == "complete"


def test_full_assembly_crash_before_publish_can_resume_without_recreating_artifacts(client, monkeypatch, tmp_path):
    prompt = complete_prompt(tmp_path)
    url, failed, path, _ = fail_after_saving(client, monkeypatch, tmp_path, count=3, prompt=prompt)
    assert failed["prompt_md"] == ""
    assert failed["delivery_progress"]["status"] == "complete"
    assert failed["delivery_progress"]["resumable"]
    before = path.read_bytes()

    def publish(snapshot, _skill):
        assert list(snapshot["_delivery_record"]["completed"]) == ["assembly", "chapter_plan", "story_design"]
        return model_result(snapshot["_delivery_record"]["completed"]["assembly"]["content"]["prompt_md"])

    monkeypatch.setattr(workshop, "_complete", publish)
    assert client.post(url + "/resume", json={"revision": failed["revision"]}).status_code == 202
    finished = finish(client, failed["id"])
    assert finished["status"] == "ready"
    assert path.read_bytes() == before
    assert finished["delivery_progress"]["status"] == "complete"


def test_zero_stage_failure_does_not_claim_saved_work(client, monkeypatch, tmp_path):
    url, failed, _, _ = fail_after_saving(client, monkeypatch, tmp_path, count=0)
    assert not failed["delivery_progress"]["resumable"]
    assert client.post(url + "/resume", json={"revision": failed["revision"]}).status_code == 409


def test_get_does_not_replay_model_or_rewrite_checkpoint(client, monkeypatch, tmp_path):
    url, failed, path, snapshots = fail_after_saving(client, monkeypatch, tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns
    for _ in range(3):
        read = client.get(url)
        assert read.status_code == 200
        assert read.json()["delivery_progress"]["stages"][0]["content_md"].startswith("# story_design")
        assert "PRIVATE MODEL PROMPT" not in read.text
    assert len(snapshots) == 1
    assert before == (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
    assert failed["delivery_run_id"] in path.name


@pytest.mark.parametrize("mutation", ["corrupt", "context", "skill", "source", "mode", "session"])
def test_invalid_checkpoint_cannot_resume_or_replace_saved_files(client, monkeypatch, tmp_path, mutation):
    url, failed, path, snapshots = fail_after_saving(client, monkeypatch, tmp_path)
    session_path = path.parent.parent / "session.json"
    if mutation == "corrupt":
        path.write_text("PRIVATE MALFORMED RECORD")
    elif mutation == "context":
        data = json.loads(session_path.read_text())
        data["input"]["premise"] = "A different story"
        session_path.write_text(json.dumps(data))
    elif mutation == "skill":
        skill_bundle = workshop._skill_bundle
        monkeypatch.setattr(workshop, "_skill_bundle", lambda phase="design": (skill_bundle(phase)[0], "new-skill"))
    else:
        data = checkpoints.load_checkpoint(path.parent, failed["delivery_run_id"])
        # Persist a structurally valid but session-incompatible record at a new
        # file identity to isolate the service's own binding check.
        field, value = {"source": ("source_revision", 0), "mode": ("narrative_mode", "unsupported"),
                        "session": ("session_id", "c" * 32)}[mutation]
        data[field] = value
        for artifact in data["completed"].values():
            artifact.pop("sha256")
        path.unlink()
        checkpoints.save_checkpoint(path.parent, failed["delivery_run_id"], data)
    before = path.read_bytes()
    read = client.get(url)
    progress = read.json()["delivery_progress"]
    assert not progress["resumable"] and progress["invalid_reason"]
    assert "PRIVATE MALFORMED RECORD" not in read.text
    assert client.post(url + "/resume", json={"revision": failed["revision"]}).status_code == 409
    assert path.read_bytes() == before and len(snapshots) == 1


def test_new_turn_and_confirmed_basics_restart_get_new_delivery_identity(client, monkeypatch, tmp_path):
    url, failed, path, _ = fail_after_saving(client, monkeypatch, tmp_path)
    snapshots = []
    monkeypatch.setattr(workshop, "_complete", lambda snapshot, _: snapshots.append(snapshot) or model_result(questions=["结局？"]))
    assert client.post(url + "/turns", json={"revision": failed["revision"], "message": "换一个完整方案"}).status_code == 202
    next_turn = finish(client, failed["id"])
    assert next_turn["delivery_run_id"] != failed["delivery_run_id"]
    assert snapshots[0]["_delivery_record"] is None
    edited = client.post(url + "/input", json={"revision": next_turn["revision"],
                                              "input": {**next_turn["input"], "premise": "New premise"}})
    assert edited.status_code == 200, edited.text
    pending = edited.json()
    assert pending["delivery_run_id"] is None and pending["delivery_source_revision"] is None
    assert client.post(url + "/resume", json={"revision": pending["revision"]}).status_code == 409
    assert "delivery_progress" not in client.get(url).json()
    assert client.post(url + "/restart", json={"revision": pending["revision"]}).status_code == 202
    restarted = finish(client, failed["id"])
    assert restarted["delivery_run_id"] not in {None, failed["delivery_run_id"], next_turn["delivery_run_id"]}
    assert path.is_file()


def test_late_checkpoint_callback_is_fenced_after_pause(client, monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()
    snapshots = []

    def complete(snapshot, _skill):
        snapshots.append(snapshot)
        snapshot["_save_delivery_checkpoint"](delivery_record(snapshot))
        entered.set()
        assert release.wait(5)
        return model_result(questions=["下一步？"])

    monkeypatch.setattr(workshop, "_complete", complete)
    data = create(client)
    url = f'/api/workshop/sessions/{data["id"]}'
    job = client.post(url + "/turns", json={"revision": 0, "message": "推导"}).json()
    try:
        assert entered.wait(2)
        paused = client.post(url + "/pause", json={"revision": 0, "job_id": job["job_id"]}).json()
        assert paused["status"] == "paused"
        path = tmp_path / "workshop_v2" / data["id"] / "delivery" / f'{paused["delivery_run_id"]}.json'
        before = path.read_bytes()
        with pytest.raises(LLMCancelledError):
            snapshots[0]["_save_delivery_checkpoint"](delivery_record(snapshots[0], 2))
        assert path.read_bytes() == before
        assert client.get(url).json()["delivery_progress"]["resumable"]
    finally:
        release.set()


def test_legacy_paused_session_gets_identity_without_changing_author_context(client, monkeypatch, tmp_path):
    data = create(client)
    path = tmp_path / "workshop_v2" / data["id"] / "session.json"
    legacy = json.loads(path.read_text())
    legacy.update(status="paused", messages=[{"role": "user", "content": "旧版讨论"}])
    path.write_text(json.dumps(legacy))
    received = []
    monkeypatch.setattr(workshop, "_complete", lambda snapshot, _: received.append(snapshot) or model_result(questions=["下一步？"]))
    response = client.post(f'/api/workshop/sessions/{data["id"]}/resume', json={"revision": 0})
    assert response.status_code == 202
    current = finish(client, data["id"])
    assert len(current["delivery_run_id"]) == 32
    assert received[0]["messages"] == legacy["messages"]
    assert received[0]["_delivery_record"] is None
