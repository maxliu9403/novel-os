"""Exercise malformed skeleton repair through the real workshop model seam."""
import json
import re
import threading
from types import SimpleNamespace

import pytest

from api import workshop
from core import provider_settings
from core.llm_client import LLMClient
from test_workshop_api import client, complete_prompt, create, finish, model_result  # noqa: F401


def aliased_contract(prompt):
    """Mirror a model confusing helper arguments with serialized engine fields."""
    match = re.search(r"\[NARRATIVE_FORMAT_JSON\](.*?)\[/NARRATIVE_FORMAT_JSON\]", prompt, re.S)
    contract = json.loads(match.group(1))
    contract.pop("format_id", None)
    contract["chapters"] = contract.pop("total_chapters")
    contract["source"] = contract.pop("selection_source")
    contract.pop("long_form_suitability")
    for volume in contract["volumes"]:
        for actual, alias in (("volume_number", "number"), ("chapter_start", "start_chapter"),
                              ("chapter_end", "end_chapter"), ("structural_role", "story_function")):
            volume[alias] = volume.pop(actual)
    return prompt[:match.start(1)] + json.dumps(contract, ensure_ascii=False) + prompt[match.end(1):]


def response(prompt):
    return json.dumps(model_result(prompt)[0], ensure_ascii=False)


def stub_model(monkeypatch, responder):
    calls = []
    monkeypatch.setattr(provider_settings, "resolve_text_route", lambda name: {
        "provider": "codex", "model": "contract-test", "base_url": "", "api_key": "",
        "max_tokens": 16384, "reasoning_effort": "medium", "timeout_seconds": 900,
    })
    monkeypatch.setattr(LLMClient, "__init__", lambda self, **kwargs: None)

    def complete(self, system, user):
        calls.append((system, user))
        return responder(len(calls), system, user)

    monkeypatch.setattr(LLMClient, "complete", complete)
    return calls


def submit(client, data, message):
    result = client.post(f'/api/workshop/sessions/{data["id"]}/turns',
                         json={"revision": data["revision"], "message": message})
    assert result.status_code == 202, result.text
    return result.json()


def contains_complete_text(container, text):
    # A repair message may include the previous response directly or JSON-quote
    # it as a nested artifact; either representation must retain the whole text.
    variants = [text]
    for _ in range(2):
        variants.append(json.dumps(variants[-1], ensure_ascii=False)[1:-1])
    return any(value in container for value in variants)


def test_aliased_structure_is_repaired_with_precise_error_and_complete_candidate(client, monkeypatch, tmp_path):
    valid = complete_prompt(tmp_path)
    bad = aliased_contract(valid)
    calls = stub_model(monkeypatch, lambda attempt, *_: response(bad if attempt == 1 else valid))
    data = create(client, premise="最早决定：第三章以前保留秘密。")
    submit(client, data, "基于全部决定生成完整骨架")
    current = finish(client, data["id"])

    assert current["status"] == "ready", current["last_error"]
    assert len(calls) == 2
    assert "total_chapters" in calls[1][1]
    assert "总章节数" in calls[1][1]
    assert contains_complete_text(calls[1][1], bad)
    assert "最早决定：第三章以前保留秘密。" in calls[1][1]
    assert current["prompt_md"] == valid
    assert len(current["messages"]) == 2
    saved = [p.read_text() for p in (tmp_path / "workshop_v2" / data["id"] / "candidates").glob("*.md")]
    assert bad in saved and valid in saved


def test_later_invalid_transport_does_not_erase_complete_repair_candidate(client, monkeypatch, tmp_path):
    valid = complete_prompt(tmp_path)
    bad = aliased_contract(valid + "\n本轮设计的独特选择不能在格式修复中丢失。\n")
    replies = [response(bad), "not valid JSON", response(valid)]
    calls = stub_model(monkeypatch, lambda attempt, *_: replies[attempt - 1])
    data = create(client)
    submit(client, data, "设计完整骨架")
    current = finish(client, data["id"])

    assert current["status"] == "ready", current["last_error"]
    assert len(calls) == 3
    assert contains_complete_text(calls[2][1], bad)
    assert "not valid JSON" in calls[2][1]
    assert current["prompt_md"] == valid


@pytest.mark.parametrize("has_previous", [False, True])
def test_contract_repair_stops_after_three_attempts_and_preserves_context(client, monkeypatch, tmp_path, has_previous):
    valid = complete_prompt(tmp_path)
    stub_model(monkeypatch, lambda *_: response(valid))
    data = create(client, premise="保留原始完整故事提示。")
    if has_previous:
        submit(client, data, "先完善初稿")
        data = finish(client, data["id"])
        assert data["status"] == "ready", data["last_error"]

    prior_prompt = data["prompt_md"]
    prior_messages = data["messages"]
    bad = aliased_contract(valid + "\n本轮新增的选择必须留存用于修复。\n")
    calls = stub_model(monkeypatch, lambda *_: response(bad))
    submit(client, data, "保留早期决定并完善人物动机")
    current = finish(client, data["id"])

    assert len(calls) == 3
    assert current["status"] == "error"
    assert "total_chapters" in current["last_error"]
    assert current["prompt_md"] == prior_prompt
    assert current["input"] == data["input"]
    assert current["messages"] == prior_messages + [
        {"role": "user", "content": "保留早期决定并完善人物动机"},
    ]
    retained = "上一版完整骨架已保留" if has_previous else "尚未生成完整骨架"
    assert retained in current["last_error"]
    for _, user in calls[1:]:
        assert "total_chapters" in user
        assert contains_complete_text(user, bad)
    saved = [p.read_text() for p in (tmp_path / "workshop_v2" / data["id"] / "candidates").glob("*.md")]
    assert sum(item == bad for item in saved) >= 3


@pytest.mark.parametrize("late_valid", [False, True])
def test_pause_during_contract_repair_fences_late_output_and_further_retries(client, monkeypatch, tmp_path, late_valid):
    valid = complete_prompt(tmp_path)
    stub_model(monkeypatch, lambda *_: response(valid))
    data = create(client, premise="暂停后仍需保留的基础提示。")
    submit(client, data, "生成初版")
    data = finish(client, data["id"])
    assert data["status"] == "ready", data["last_error"]

    bad = aliased_contract(valid)
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()

    def delayed(attempt, *_):
        if attempt == 1:
            return response(bad)
        entered.set()
        assert release.wait(5)
        return response(valid + "\n晚到结果不应生效。\n" if late_valid else bad)

    calls = stub_model(monkeypatch, delayed)
    original = workshop.WorkshopService._run_turn

    def observe_worker(self, *args):
        try:
            return original(self, *args)
        finally:
            exited.set()

    monkeypatch.setattr(workshop.WorkshopService, "_run_turn", observe_worker)
    job = submit(client, data, "完善骨架但允许暂停")
    url = f'/api/workshop/sessions/{data["id"]}'
    try:
        assert entered.wait(2), "A malformed contract must reach the bounded repair request"
        before = client.get(url).json()
        paused_response = client.post(url + "/pause", json={"revision": before["revision"], "job_id": job["job_id"]})
        assert paused_response.status_code == 200, paused_response.text
        paused = paused_response.json()
        assert paused["status"] == "paused"
        assert paused["messages"] == before["messages"]
        assert paused["prompt_md"] == data["prompt_md"]
        release.set()
        assert exited.wait(2)
        assert len(calls) == 2
        assert client.get(url).json() == paused
        assert workshop.runner.get(job["job_id"])["error"] == "任务已暂停。"
    finally:
        release.set()
        exited.wait(2)


@pytest.mark.parametrize("exhaust_after", [1, 2])
def test_contract_repair_shares_turn_deadline_and_does_not_start_after_budget(client, monkeypatch, tmp_path, exhaust_after):
    now = [100.0]
    # Patch the workshop's clock reference, not the global time module used by
    # TestClient's event loop and synchronization primitives.
    monkeypatch.setattr(workshop, "time", SimpleNamespace(monotonic=lambda: now[0]))
    bad = aliased_contract(complete_prompt(tmp_path))

    def respond(attempt, *_):
        now[0] += 901 if attempt == exhaust_after else 50
        return response(bad)

    calls = stub_model(monkeypatch, respond)
    data = create(client, premise="超时后也保留完整基础设定。")
    submit(client, data, "完善骨架")
    current = finish(client, data["id"])

    assert current["status"] == "error"
    assert "超时" in current["last_error"]
    assert len(calls) == exhaust_after
    assert current["prompt_md"] == ""
    assert current["input"] == data["input"]
    assert current["messages"] == [{"role": "user", "content": "完善骨架"}]


def test_each_repair_constructs_client_with_remaining_timeout(client, monkeypatch, tmp_path):
    now = [100.0]
    monkeypatch.setattr(workshop, "time", SimpleNamespace(monotonic=lambda: now[0]))
    valid = complete_prompt(tmp_path)
    bad = aliased_contract(valid)

    def respond(attempt, *_):
        if attempt < 3:
            now[0] += 100 if attempt == 1 else 200
            return response(bad)
        return response(valid)

    calls = stub_model(monkeypatch, respond)
    timeouts = []
    monkeypatch.setattr(LLMClient, "__init__", lambda self, **kwargs: timeouts.append(kwargs["timeout_seconds"]))
    data = create(client)
    submit(client, data, "生成完整骨架")
    current = finish(client, data["id"])

    assert current["status"] == "ready", current["last_error"]
    assert len(calls) == 3
    assert timeouts == [900.0, 800.0, 600.0]


def test_resume_reuses_failed_candidate_as_unvalidated_context(client, monkeypatch, tmp_path):
    valid = complete_prompt(tmp_path)
    bad = aliased_contract(valid + "\n暂停前产生的独特情节需要保留。\n")
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()

    def delayed(attempt, *_):
        if attempt == 1:
            return response(bad)
        entered.set()
        assert release.wait(5)
        return response(valid)

    stub_model(monkeypatch, delayed)
    original = workshop.WorkshopService._run_turn

    def observe_worker(self, *args):
        try:
            return original(self, *args)
        finally:
            exited.set()

    monkeypatch.setattr(workshop.WorkshopService, "_run_turn", observe_worker)
    data = create(client, premise="最初的故事要求持续有效。")
    job = submit(client, data, "完成完整骨架")
    url = f'/api/workshop/sessions/{data["id"]}'
    try:
        assert entered.wait(2)
        paused_response = client.post(url + "/pause", json={"revision": data["revision"], "job_id": job["job_id"]})
        assert paused_response.status_code == 200
        paused = paused_response.json()
        assert paused["prompt_md"] == ""
        release.set()
        assert exited.wait(2)
        monkeypatch.setattr(workshop.WorkshopService, "_run_turn", original)

        calls = stub_model(monkeypatch, lambda *_: response(valid))
        resumed = client.post(url + "/resume", json={"revision": paused["revision"]})
        assert resumed.status_code == 202, resumed.text
        current = finish(client, data["id"])

        assert current["status"] == "ready", current["last_error"]
        assert len(calls) == 1
        context = json.loads(calls[0][1])
        assert context["previous_unvalidated_skeleton"] == bad
        assert "NOT an approved skeleton" in context["candidate_instruction"]
        assert context["current_complete_skeleton"] == ""
        assert context["conversation"] == paused["messages"]
        assert context["initial_brief"] == paused["input"]
        assert current["prompt_md"] == valid
        assert len(current["messages"]) == 2
    finally:
        release.set()
        exited.wait(2)
