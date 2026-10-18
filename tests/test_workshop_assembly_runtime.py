"""Interrupted assembly exercises the real service, checkpoint and final validator."""
from copy import deepcopy
import json

import pytest

from api import workshop
from api.workshop_assembly import ASSEMBLY_PARTS
from api.workshop_checkpoints import load_checkpoint
from test_workshop_api import client, complete_prompt, create, finish  # noqa: F401
from test_workshop_contract_repair import stub_model, submit
from workshop_assembly_fixtures import parts_from_prompt
from workshop_delivery_fixtures import stage_response, chapter_plan


def start_reply(parts, user):
    context = json.loads(user)
    if context.get("assembly_part"):
        return json.dumps(parts[context["assembly_part"]], ensure_ascii=False)
    stage = context.get("delivery_stage")
    assert stage in {"story_design", "chapter_plan"}
    return stage_response(stage)


def checkpoint(tmp_path, data):
    return load_checkpoint(tmp_path / "workshop_v2" / data["id"] / "delivery", data["delivery_run_id"])


def test_partial_assembly_timeout_and_resume_never_repeat_saved_parts(client, monkeypatch, tmp_path):
    parts = parts_from_prompt(complete_prompt(tmp_path))

    def interrupted(attempt, system, user):
        if json.loads(user).get("assembly_part") == "secrets_payoffs":
            raise TimeoutError("synthetic request deadline")
        return start_reply(parts, user)

    calls = stub_model(monkeypatch, interrupted)
    data = create(client, premise="不能丢失的初始决定")
    submit(client, data, "接受推荐，完整交付")
    failed = finish(client, data["id"])
    assert failed["status"] == "error" and len(calls) == 5
    assert "单次请求上限 900 秒" in failed["last_error"]
    assert failed["prompt_md"] == "" and failed["delivery_progress"]["assembly"]["completed_parts"] == 2
    original = checkpoint(tmp_path, failed)
    for _ in range(3):
        assert client.get(f'/api/workshop/sessions/{data["id"]}').json()["status"] == "error"
    assert len(calls) == 5
    calls = stub_model(monkeypatch, lambda attempt, system, user: start_reply(parts, user))
    assert client.post(f'/api/workshop/sessions/{data["id"]}/resume', json={"revision": failed["revision"]}).status_code == 202
    result = finish(client, data["id"])
    assert result["status"] == "ready", result.get("last_error")
    assert [json.loads(user)["assembly_part"] for _, user in calls] == list(ASSEMBLY_PARTS[2:])
    saved = checkpoint(tmp_path, result)
    for stage, artifact in original["completed"].items():
        assert saved["completed"][stage] == artifact
    for part, artifact in original["assembly_parts"]["completed"].items():
        assert saved["assembly_parts"]["completed"][part] == artifact
    for chapter in chapter_plan()["chapters"]:
        assert result["prompt_md"].count(chapter["plan"]) == 1
    assert result["delivery_progress"]["assembly"]["completed_parts"] == 6
    assert result["messages"][:-1] == failed["messages"]


@pytest.mark.parametrize("interrupt_repair", [False, True])
def test_final_validation_repairs_only_one_part_and_preserves_revision_history(client, monkeypatch, tmp_path, interrupt_repair):
    parts = parts_from_prompt(complete_prompt(tmp_path))
    fixed = deepcopy(parts["cover_handoff"])
    fixed["sections"]["15"] += "\n\n已完成指定交付说明修订。"
    original_validate = workshop._validate_candidate

    def validate(path):
        result = original_validate(path)
        if "已完成指定交付说明修订。" not in path.read_text():
            raise workshop.ModelResponseError("封面交接说明需要补充（离线注入跨合同失败）。")
        return result

    monkeypatch.setattr(workshop, "_validate_candidate", validate)

    def respond(attempt, system, user):
        context = json.loads(user)
        if context.get("validation_error"):
            assert context["assembly_part"] == "cover_handoff"
            assert "prompt_md" not in json.loads(context["previous_model_response"])
            if interrupt_repair:
                raise TimeoutError("synthetic repair timeout")
            return json.dumps(fixed, ensure_ascii=False)
        return start_reply(parts, user)

    calls = stub_model(monkeypatch, respond)
    data = create(client)
    submit(client, data, "交付骨架")
    current = finish(client, data["id"])
    assert len(calls) == 9
    if interrupt_repair:
        assert current["status"] == "error" and current["prompt_md"] == ""
        before = checkpoint(tmp_path, current)
        assert before["assembly_parts"]["active_part"] == "cover_handoff"
        calls = stub_model(monkeypatch, lambda attempt, system, user: json.dumps(fixed))
        assert client.post(f'/api/workshop/sessions/{data["id"]}/resume', json={"revision": current["revision"]}).status_code == 202
        current = finish(client, data["id"])
        assert len(calls) == 1 and json.loads(calls[0][1])["assembly_part"] == "cover_handoff"
    assert current["status"] == "ready", current.get("last_error")
    record = checkpoint(tmp_path, current)
    assert record["assembly_parts"]["completed"]["cover_handoff"]["content"] == parts["cover_handoff"]
    assert len(record["assembly_parts"]["revisions"]) == 1
    assert record["assembly_parts"]["revisions"][0]["content"] == fixed
    assert record["assembly_parts"]["active_part"] is None
    for chapter in chapter_plan()["chapters"]:
        assert current["prompt_md"].count(chapter["plan"]) == 1
