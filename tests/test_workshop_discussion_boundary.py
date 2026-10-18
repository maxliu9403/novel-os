"""Regression coverage for the explicit workshop discussion/delivery boundary."""
from __future__ import annotations

import json
import re

import pytest

from api import workshop
from api.workshop_assembly import ASSEMBLY_PARTS
from test_workshop_api import client, complete_prompt, create, finish  # noqa: F401
from test_workshop_contract_repair import stub_model
from test_workshop_handoff import make_prompt
from workshop_assembly_fixtures import parts_from_prompt
from workshop_delivery_fixtures import stage_response


REPORTED_CHOICE = (
    "1. 职业清白与保护越界（推荐）\n"
    "2. 让 Ingrid 与 Kareem 稳定在一起\n"
    "3. 没有明确要求城市地点"
)
LEAKED_SKELETON = "PRIVATE_LEAKED_COMPLETE_SKELETON_7f09"


def discussion(reply="### 新的推进\n\nIngrid 先公开证据，Kareem 因此必须在关系与职业责任之间作出选择。",
               questions=None):
    return json.dumps({
        "reply": reply,
        "questions": [] if questions is None else questions,
        "prompt_md": None,
        "ready_for_confirmation": False,
    }, ensure_ascii=False)


def violation():
    return json.dumps({
        "reply": "### 内部交付\n\n这段不得发布。",
        "questions": [],
        "prompt_md": LEAKED_SKELETON,
        "ready_for_confirmation": True,
        "requests_delivery": True,
        "delivery_mode": "short_novel",
    }, ensure_ascii=False)


def ready_only_violation():
    return json.dumps({
        "reply": "### 尚未可交付\n\n这轮仍是讨论。",
        "questions": [],
        "prompt_md": None,
        "ready_for_confirmation": True,
    }, ensure_ascii=False)


def submit(client, data, message, *, intent=None):
    body = {"revision": data["revision"], "message": message}
    if intent is not None:
        body["intent"] = intent
    response = client.post(f'/api/workshop/sessions/{data["id"]}/turns', json=body)
    assert response.status_code == 202, response.text
    return response.json()


def persisted(tmp_path, session_id):
    return json.loads((tmp_path / "workshop_v2" / session_id / "session.json").read_text())


def candidate_files(tmp_path, session_id):
    directory = tmp_path / "workshop_v2" / session_id / "candidates"
    return list(directory.glob("*.md")) if directory.is_dir() else []


def delivery_responder(prompt, *, mode="short_novel", fail_chapter=False):
    parts = parts_from_prompt(prompt, mode=mode)
    failed = False

    def respond(_attempt, _system, user):
        nonlocal failed
        context = json.loads(user)
        stage = context.get("delivery_stage")
        if stage == "story_design":
            return stage_response("story_design", mode=mode)
        if stage == "chapter_plan":
            if fail_chapter and not failed:
                failed = True
                raise TimeoutError("deterministic chapter timeout")
            return stage_response("chapter_plan", mode=mode)
        if stage == "assembly":
            return json.dumps(parts[context["assembly_part"]], ensure_ascii=False)
        # Keep a regressed implementation bounded so the worker records a
        # normal result and the assertions below can report the extra handoff.
        return json.dumps({
            "reply": "### 内部交付\n\n进入完整交付。", "questions": [],
            "prompt_md": None, "ready_for_confirmation": False,
            "requests_delivery": True, "delivery_mode": "short_novel",
        }, ensure_ascii=False)

    return respond


def complete_prompt_for_mode(tmp_path, mode):
    prompt = complete_prompt(tmp_path)
    pattern = r"\[NARRATIVE_FORMAT_JSON\].*?\[/NARRATIVE_FORMAT_JSON\]"
    selected = re.search(pattern, make_prompt(tmp_path, mode=mode).read_text(), re.DOTALL)
    assert selected is not None
    return re.sub(pattern, lambda _: selected.group(), prompt, flags=re.DOTALL)


def assert_no_delivery_artifacts(tmp_path, current):
    assert candidate_files(tmp_path, current["id"]) == []
    assert "delivery_progress" not in current
    directory = tmp_path / "workshop_v2" / current["id"] / "delivery"
    assert not directory.exists() or list(directory.iterdir()) == []


def test_default_discussion_persists_intent_and_exact_reported_choice_without_delivery(
        client, monkeypatch, tmp_path):
    calls = stub_model(monkeypatch, lambda *_: discussion(questions=["是否让 Kareem 公开承担后果？"]))
    data = create(client, premise="完整设定：21章，结局和主要人物已定。", mode="short_novel")
    submit(client, data, REPORTED_CHOICE)
    current = finish(client, data["id"])

    assert current["status"] == "draft"
    assert current["turn_intent"] == "discuss"
    assert persisted(tmp_path, data["id"])["turn_intent"] == "discuss"
    assert current["messages"][-2] == {"role": "user", "content": REPORTED_CHOICE}
    assert current["messages"][-1]["content"].startswith("### 新的推进")
    assert current["questions"] == ["是否让 Kareem 公开承担后果？"]
    assert len(calls) == 1
    assert "CURRENT REQUEST PROFILE: AUTHOR-GUIDED EXPLORATION" in calls[0][0]
    assert "STAGED SKELETON DELIVERY" not in calls[0][0]
    assert_no_delivery_artifacts(tmp_path, current)


def test_legacy_complete_brief_and_just_do_that_remain_discussion_by_default(
        client, monkeypatch, tmp_path):
    calls = stub_model(monkeypatch, lambda attempt, *_: violation() if attempt == 1 else discussion())
    data = create(client, premise="完整设定：21章，每章550英文词，单本，结局已定。", mode="short_novel")
    submit(client, data, "就这样吧")
    current = finish(client, data["id"])

    assert current["status"] == "draft"
    assert current["turn_intent"] == "discuss"
    assert current["prompt_md"] == ""
    assert len(calls) == 2
    assert_no_delivery_artifacts(tmp_path, current)


def test_discussion_scope_violation_gets_one_narrow_correction_without_skeleton_leak(
        client, monkeypatch, tmp_path):
    calls = stub_model(monkeypatch, lambda attempt, *_: violation() if attempt == 1 else discussion())
    data = create(client, premise="探索人物如何主动选择。", mode="short_novel")
    submit(client, data, "请具体推进中段冲突", intent="discuss")
    current = finish(client, data["id"])

    assert current["status"] == "draft"
    assert len(calls) == 2
    assert LEAKED_SKELETON not in calls[1][0]
    assert LEAKED_SKELETON not in calls[1][1]
    assert LEAKED_SKELETON not in json.dumps(current, ensure_ascii=False)
    assert_no_delivery_artifacts(tmp_path, current)


def test_second_discussion_scope_violation_becomes_safe_draft_without_candidate_or_stage(
        client, monkeypatch, tmp_path):
    calls = stub_model(monkeypatch, lambda *_: violation())
    data = create(client, premise="保留开放探索。", mode="short_novel")
    pending = ["在两种职业代价中，你更愿意保留哪一种？"]
    path = tmp_path / "workshop_v2" / data["id"] / "session.json"
    saved = json.loads(path.read_text())
    saved["questions"] = pending
    path.write_text(json.dumps(saved, ensure_ascii=False, indent=2))
    submit(client, data, "继续探索，不要交付骨架", intent="discuss")
    current = finish(client, data["id"])

    assert current["status"] == "draft"
    assert current["ready_for_confirmation"] is False
    assert current["prompt_md"] == ""
    assert len(calls) == 2
    assert current["questions"] == pending
    assert current["messages"][-1]["questions"] == pending
    assert LEAKED_SKELETON not in json.dumps(current, ensure_ascii=False)
    assert_no_delivery_artifacts(tmp_path, current)


def test_discussion_ready_flag_without_prompt_is_also_repaired_as_scope_violation(
        client, monkeypatch, tmp_path):
    calls = stub_model(monkeypatch, lambda attempt, *_:
                       ready_only_violation() if attempt == 1 else discussion())
    data = create(client, premise="现在只比较两种人物选择。", mode="short_novel")
    submit(client, data, "先深化代价，不生成骨架", intent="discuss")
    current = finish(client, data["id"])

    assert current["status"] == "draft"
    assert current["ready_for_confirmation"] is False
    assert current["prompt_md"] == ""
    assert len(calls) == 2
    assert_no_delivery_artifacts(tmp_path, current)


def test_discussion_with_existing_skeleton_is_exploration_and_preserves_exact_bytes(
        client, monkeypatch, tmp_path):
    existing = "# 已核定骨架\n\n原样保留的空格  \n\n不可改写。\n"
    calls = stub_model(monkeypatch, lambda *_: discussion())
    data = create(client, premise="继续讨论", mode="short_novel")
    path = tmp_path / "workshop_v2" / data["id"] / "session.json"
    saved = json.loads(path.read_text())
    saved["prompt_md"] = existing
    path.write_text(json.dumps(saved, ensure_ascii=False, indent=2))

    submit(client, data, "只探索 Ingrid 的职业代价", intent="discuss")
    current = finish(client, data["id"])

    assert current["prompt_md"].encode() == existing.encode()
    assert len(calls) == 1
    assert "CURRENT REQUEST PROFILE: AUTHOR-GUIDED EXPLORATION" in calls[0][0]
    assert json.loads(calls[0][1])["current_complete_skeleton"] == existing
    assert_no_delivery_artifacts(tmp_path, current)


def test_known_mode_delivery_starts_with_story_design_and_never_calls_handoff(
        client, monkeypatch, tmp_path):
    prompt = complete_prompt(tmp_path)
    calls = stub_model(monkeypatch, delivery_responder(prompt))
    data = create(client, premise="完成21章的家庭剧骨架。", mode="short_novel", chapters=21)
    submit(client, data, "交付完整骨架", intent="deliver")
    current = finish(client, data["id"])

    contexts = [json.loads(user) for _, user in calls]
    assert contexts[0]["delivery_stage"] == "story_design"
    assert contexts[0].get("delivery_mode") in (None, "")
    assert [item["delivery_stage"] for item in contexts[:2]] == ["story_design", "chapter_plan"]
    assert len(calls) == 2 + len(ASSEMBLY_PARTS)
    assert current["status"] == "ready", current["last_error"]
    assert current["turn_intent"] == "deliver"
    assert current["delivery_progress"]["completed_stages"] == ["story_design", "chapter_plan", "assembly"]


def test_unknown_mode_delivery_selects_valid_mode_in_first_story_design_stage(
        client, monkeypatch, tmp_path):
    prompt = complete_prompt(tmp_path)
    calls = stub_model(monkeypatch, delivery_responder(prompt))
    data = create(client, premise="请根据完整故事决定最合适的组织形式并交付。", chapters=21)
    submit(client, data, "完整交付", intent="deliver")
    current = finish(client, data["id"])

    contexts = [json.loads(user) for _, user in calls]
    assert contexts[0]["delivery_stage"] == "story_design"
    assert contexts[0].get("delivery_mode") in (None, "")
    assert len(calls) == 2 + len(ASSEMBLY_PARTS)
    assert current["status"] == "ready", current["last_error"]
    assert current["delivery_progress"]["completed_stages"] == ["story_design", "chapter_plan", "assembly"]


def test_first_story_design_can_apply_later_conversation_mode_over_older_form_without_handoff(
        client, monkeypatch, tmp_path):
    prompt = complete_prompt_for_mode(tmp_path, "series_installment")
    calls = stub_model(monkeypatch, delivery_responder(prompt, mode="series_installment"))
    data = create(client, premise="最早表单选了短篇。", mode="short_novel", chapters=21)
    submit(client, data, "根据后续讨论，现在按系列首部的完整形式交付。", intent="deliver")
    current = finish(client, data["id"])

    contexts = [json.loads(user) for _, user in calls]
    assert contexts[0]["delivery_stage"] == "story_design"
    assert contexts[0].get("delivery_mode") in (None, "")
    assert contexts[1]["delivery_stage"] == "chapter_plan"
    assert contexts[1]["delivery_mode"] == "series_installment"
    assert len(calls) == 2 + len(ASSEMBLY_PARTS)
    assert current["status"] == "ready", current["last_error"]
    assert current["delivery_progress"]["completed_stages"] == ["story_design", "chapter_plan", "assembly"]


@pytest.mark.parametrize("legacy", [False, True], ids=["current-intent", "legacy-verified-checkpoint"])
def test_delivery_resume_preserves_intent_and_accepts_legacy_verified_checkpoint(
        client, monkeypatch, tmp_path, legacy):
    prompt = complete_prompt(tmp_path)
    first_calls = stub_model(monkeypatch, delivery_responder(prompt, fail_chapter=True))
    data = create(client, premise="交付后可从已验证阶段继续。", mode="short_novel", chapters=21)
    submit(client, data, "完成骨架", intent="deliver")
    failed = finish(client, data["id"])
    assert failed["status"] == "error"
    assert failed["delivery_progress"]["completed_stages"] == ["story_design"]
    assert len(first_calls) == 2

    if legacy:
        path = tmp_path / "workshop_v2" / data["id"] / "session.json"
        raw = json.loads(path.read_text())
        raw.pop("turn_intent", None)
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2))

    resumed_calls = stub_model(monkeypatch, delivery_responder(prompt))
    response = client.post(f'/api/workshop/sessions/{data["id"]}/resume',
                           json={"revision": failed["revision"]})
    assert response.status_code == 202, response.text
    current = finish(client, data["id"])

    assert current["status"] == "ready", current["last_error"]
    assert current["turn_intent"] == "deliver"
    assert json.loads(resumed_calls[0][1])["delivery_stage"] == "chapter_plan"
    assert all(json.loads(user).get("delivery_stage") in {"chapter_plan", "assembly"}
               for _, user in resumed_calls)


def test_legacy_no_intent_paused_candidate_defaults_to_discussion(client, monkeypatch, tmp_path):
    legacy_candidate = "# LEGACY FAILED CANDIDATE\n\n" + LEAKED_SKELETON
    calls = stub_model(monkeypatch, lambda *_: discussion())
    data = create(client, premise="旧会话应继续讨论。", mode="short_novel")
    root = tmp_path / "workshop_v2" / data["id"]
    path = root / "session.json"
    raw = json.loads(path.read_text())
    raw.update(revision=1, status="paused", active_operation="brainstorm",
               messages=[{"role": "user", "content": "只继续讨论"}],
               delivery_run_id=None, delivery_source_revision=None)
    raw.pop("turn_intent", None)
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2))
    candidate = root / "candidates" / "0.md"
    candidate.parent.mkdir()
    candidate.write_text(legacy_candidate)

    response = client.post(f'/api/workshop/sessions/{data["id"]}/resume', json={"revision": 1})
    assert response.status_code == 202, response.text
    current = finish(client, data["id"])

    assert current["status"] == "draft"
    assert current["turn_intent"] == "discuss"
    assert len(calls) == 1
    assert "CURRENT REQUEST PROFILE: AUTHOR-GUIDED EXPLORATION" in calls[0][0]
    assert "previous_unvalidated_skeleton" not in json.loads(calls[0][1])
    assert candidate.read_text() == legacy_candidate


def test_discussion_has_no_arbitrary_round_limit_through_real_model_seam(client, monkeypatch):
    calls = stub_model(monkeypatch, lambda attempt, *_: discussion(
        reply=f"### 第 {attempt} 轮推进\n\n新选择改变了下一轮阻力。"))
    data = create(client, premise="逐轮开拓。", mode="short_novel")
    for number in range(12):
        submit(client, data, f"继续第 {number + 1} 轮", intent="discuss")
        data = finish(client, data["id"])
        assert data["status"] == "draft"

    assert len(calls) == 12
    assert len(data["messages"]) == 24
    assert data["turn_intent"] == "discuss"


def test_turn_intent_api_rejects_unknown_value(client, monkeypatch):
    calls = stub_model(monkeypatch, lambda *_: pytest.fail("invalid input must not invoke the model"))
    data = create(client)
    response = client.post(f'/api/workshop/sessions/{data["id"]}/turns', json={
        "revision": data["revision"], "message": "继续", "intent": "maybe",
    })

    assert response.status_code == 422
    assert calls == []
    assert client.get(f'/api/workshop/sessions/{data["id"]}').json()["revision"] == 0
