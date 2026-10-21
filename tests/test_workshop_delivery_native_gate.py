"""Real workshop orchestration serializes native requests until cancellation drains.

Only the LLM transport is fake. Session APIs, staged delivery, durable saves,
pause/resume fencing and the inference gate all execute production code.
"""
from contextlib import contextmanager
import json
import threading

import pytest

from api import workshop, workshop_checkpoints, workshop_delivery
from api.workshop_assembly import ASSEMBLY_PARTS
from core import provider_settings
from core.llm_client import LLMClient
from test_workshop_api import client, complete_prompt, create, finish  # noqa: F401
from workshop_assembly_fixtures import parts_from_prompt
from workshop_delivery_fixtures import stage_response


def fake_native_transport(monkeypatch, prompt):
    assembly_parts = parts_from_prompt(prompt)
    entered = threading.Event()
    release = threading.Event()
    drained = threading.Event()
    resumed = threading.Event()
    lock = threading.Lock()
    calls = []
    active = {}
    maximum_active = {}
    blocked = False
    monkeypatch.setattr(provider_settings, "resolve_text_route", lambda _: {
        "provider": "codex", "model": "offline-native-gate-test", "base_url": "", "api_key": "",
        "max_tokens": 16384, "reasoning_effort": "medium", "timeout_seconds": 900,
    })

    def initialize(self, **options):
        self.test_options = options

    def complete(self, _system, user):
        nonlocal blocked
        context = json.loads(user)
        title = context["initial_brief"]["title"]
        stage = context.get("delivery_stage")
        block = False
        with lock:
            calls.append({"title": title, "stage": stage, "context": context,
                          "cancel": self.test_options["cancellation_event"]})
            active[title] = active.get(title, 0) + 1
            maximum_active[title] = max(maximum_active.get(title, 0), active[title])
            if title == "Primary" and stage == "chapter_plan":
                if not blocked:
                    blocked = block = True
                else:
                    resumed.set()
        try:
            if block:
                entered.set()
                # Simulate a native request whose cancellation has been signaled
                # but whose process/stream teardown has not returned yet.
                assert release.wait(8), "Test did not release the simulated native request"
                drained.set()
            if stage in {"story_design", "chapter_plan"}:
                return stage_response(stage)
            if stage == "assembly":
                return json.dumps(assembly_parts[context["assembly_part"]], ensure_ascii=False)
            raise AssertionError(f"Explicit delivery made a non-stage model call: {context}")
        finally:
            with lock:
                active[title] -= 1

    monkeypatch.setattr(LLMClient, "__init__", initialize)
    monkeypatch.setattr(LLMClient, "complete", complete)
    return {"entered": entered, "release": release, "drained": drained, "resumed": resumed,
            "calls": calls, "maximum_active": maximum_active}


def observe_resumed_gate(monkeypatch):
    waiting, exited = threading.Event(), threading.Event()
    original = workshop_delivery.serialized_inference

    @contextmanager
    def observed(session, *, now):
        resumed = session["input"]["title"] == "Primary" and session["revision"] == 3
        if resumed:
            waiting.set()
        try:
            with original(session, now=now):
                yield
        finally:
            if resumed:
                exited.set()

    monkeypatch.setattr(workshop_delivery, "serialized_inference", observed)
    return waiting, exited


@pytest.mark.parametrize("cancel_waiter", [False, True])
def test_resume_waits_for_old_native_call_then_uses_saved_stage(client, monkeypatch, tmp_path, cancel_waiter):
    transport = fake_native_transport(monkeypatch, complete_prompt(tmp_path))
    waiting, waiter_exited = observe_resumed_gate(monkeypatch)
    data = create(client, title="Primary")
    url = f'/api/workshop/sessions/{data["id"]}'
    first = client.post(url + "/turns", json={
        "revision": 0, "message": "采用完整方案并交付骨架", "intent": "deliver",
    }).json()
    try:
        assert transport["entered"].wait(3)
        paused = client.post(url + "/pause", json={"revision": 1, "job_id": first["job_id"]}).json()
        assert paused["status"] == "paused"
        assert transport["calls"][-1]["cancel"].is_set()
        checkpoint_dir = tmp_path / "workshop_v2" / data["id"] / "delivery"
        before = workshop_checkpoints.load_checkpoint(checkpoint_dir, paused["delivery_run_id"])
        assert list(before["completed"]) == ["story_design"]

        new = client.post(url + "/resume", json={"revision": paused["revision"]})
        assert new.status_code == 202, new.text
        assert waiting.wait(3), "Resumed worker must reach the real gate"
        assert not transport["resumed"].wait(0.15), "New LLM request overlapped undrained native call"
        assert not transport["drained"].is_set()
        assert [call["stage"] for call in transport["calls"]] == ["story_design", "chapter_plan"]

        if cancel_waiter:
            waiting_state = client.get(url).json()
            repaused = client.post(url + "/pause", json={"revision": waiting_state["revision"],
                                                        "job_id": new.json()["job_id"]}).json()
            assert repaused["status"] == "paused"
            assert waiter_exited.wait(3), "Waiting at the gate must remain cancellable"
            assert not transport["resumed"].is_set()
            assert workshop_checkpoints.load_checkpoint(checkpoint_dir, paused["delivery_run_id"]) == before
            transport["release"].set()
            assert transport["drained"].wait(3)
            assert client.get(url).json()["status"] == "paused"
            assert client.post(url + "/resume", json={"revision": repaused["revision"]}).status_code == 202
        else:
            transport["release"].set()

        assert transport["resumed"].wait(3)
        current = finish(client, data["id"])
        assert current["status"] == "ready", current.get("last_error")
        assert transport["drained"].is_set()
        assert transport["maximum_active"]["Primary"] == 1
        assert [call["stage"] for call in transport["calls"]] == [
            "story_design", "chapter_plan", "chapter_plan", *(["assembly"] * len(ASSEMBLY_PARTS)),
        ]
        assert [call["context"]["assembly_part"] for call in transport["calls"] if call["stage"] == "assembly"] == list(ASSEMBLY_PARTS)
        retry = transport["calls"][2]
        assert retry["context"]["saved_story_stages"]["story_design"] == before["completed"]["story_design"]["content"]
        after = workshop_checkpoints.load_checkpoint(checkpoint_dir, paused["delivery_run_id"])
        assert after["completed"]["story_design"] == before["completed"]["story_design"]
        assert current["delivery_progress"]["status"] == "complete"
        assert len(after["completed"]) == 3
    finally:
        transport["release"].set()


def test_independent_discussions_do_not_share_the_native_inference_gate(client, monkeypatch, tmp_path):
    transport = fake_native_transport(monkeypatch, complete_prompt(tmp_path))
    primary = create(client, title="Primary")
    independent = create(client, title="Independent")
    primary_url = f'/api/workshop/sessions/{primary["id"]}'
    independent_url = f'/api/workshop/sessions/{independent["id"]}'
    assert client.post(primary_url + "/turns", json={
        "revision": 0, "message": "完整交付", "intent": "deliver",
    }).status_code == 202
    try:
        assert transport["entered"].wait(3)
        assert client.post(independent_url + "/turns", json={
            "revision": 0, "message": "独立作品完整交付", "intent": "deliver",
        }).status_code == 202
        other = finish(client, independent["id"])
        assert other["status"] == "ready", other.get("last_error")
        assert not transport["release"].is_set() and not transport["drained"].is_set()
        assert client.get(primary_url).json()["status"] == "running"
        assert [call["stage"] for call in transport["calls"] if call["title"] == "Independent"] == [
            "story_design", "chapter_plan", *(["assembly"] * len(ASSEMBLY_PARTS)),
        ]
        transport["release"].set()
        assert finish(client, primary["id"])["status"] == "ready"
        assert transport["maximum_active"] == {"Primary": 1, "Independent": 1}
    finally:
        transport["release"].set()


def test_waiting_for_native_gate_spends_the_existing_turn_deadline(tmp_path):
    path = tmp_path / ".inference.lock"
    clock = [0.0]
    checked, finished = threading.Event(), threading.Event()
    errors, entered = [], []
    owner = {"_inference_lock_path": path, "_turn_deadline": 100.0}
    waiter = {"_inference_lock_path": path, "_turn_deadline": 1.0,
              "_cancellation_event": threading.Event()}

    def now():
        checked.set()
        return clock[0]

    def wait():
        try:
            with workshop_delivery.serialized_inference(waiter, now=now):
                entered.append(True)
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    with workshop_delivery.serialized_inference(owner, now=lambda: 0.0):
        thread = threading.Thread(target=wait, daemon=True)
        thread.start()
        assert checked.wait(2)
        clock[0] = 2.0
        assert finished.wait(2), "Expired wait must stop before the original owner exits"
        assert not entered and len(errors) == 1 and isinstance(errors[0], TimeoutError)
    thread.join(2)
    # The expired waiter never acquired or poisoned the shared lock.
    with workshop_delivery.serialized_inference({**waiter, "_turn_deadline": 3.0}, now=now):
        assert clock[0] == 2.0
