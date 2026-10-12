"""Runtime disclosure reduces exploration cost without restricting delivery intent."""
import json
import re
from types import SimpleNamespace

import pytest

from api import workshop
from core.llm_client import LLMCancelledError, LLMClient
from test_workshop_api import client, complete_prompt, create, finish, model_result  # noqa: F401
from test_workshop_contract_repair import aliased_contract, response, stub_model, submit
from test_workshop_handoff import make_prompt
from workshop_delivery_fixtures import stage_response


def snapshot(**updates):
    return {"id": "profile-test", "revision": 1, "input": {"premise": "原始人物与完整主线"},
            "messages": [{"role": "user", "content": "继续这个方向"}], "prompt_md": "", **updates}


def handoff(mode="short_novel"):
    return json.dumps({"reply": "### 内部交付\n\n进入完整交付。", "questions": [], "prompt_md": None,
                       "ready_for_confirmation": False, "requests_delivery": True, "delivery_mode": mode})


def test_exploration_prompt_is_smaller_and_keeps_every_context_message(monkeypatch):
    calls = stub_model(monkeypatch, lambda *_: json.dumps(model_result()[0]))
    skill, version = workshop._skill_bundle("design")
    history = [{"role": "user", "content": "最早的不可改身份与结局"},
               {"role": "assistant", "content": "尚未选择的备选走线", "questions": ["旧问题"]},
               {"role": "user", "content": "只开拓中段的新可能性"}]
    workshop._complete(snapshot(messages=history, questions=["保留但还没回答的问题"]), skill)
    system, user = calls[0]
    assert len(system.encode()) < len(skill.encode()) * 0.5
    assert "--- references/story-exploration.md ---" in system
    assert "--- references/character-and-emotion.md ---" in system
    assert "--- references/hooks-and-payoffs.md ---" in system
    assert "--- references/prompt-contract.md ---" not in system
    assert "ENGINE NARRATIVE FORMAT REFERENCE" not in system
    assert "COMMERCIAL STORY SERIALIZATION" not in system
    context = json.loads(user)
    assert context["conversation"] == history
    assert context["initial_brief"] == snapshot()["input"]
    assert context["previous_pending_questions"] == ["保留但还没回答的问题"]
    assert workshop._skill_bundle("exploration")[1] == workshop._skill_bundle("delivery")[1] == version


@pytest.mark.parametrize("mode", workshop._NARRATIVE_MODES)
def test_narrative_reference_contains_one_complete_serialized_mode(mode):
    reference = workshop._narrative_contract_reference(mode)
    examples = json.loads(reference[reference.index("{\n"):])
    assert list(examples) == [mode]
    contract = examples[mode]
    assert contract["total_chapters"] == 20
    assert len(contract["long_form_suitability"]["dimensions"]) == 6
    assert all({"volume_number", "chapter_start", "chapter_end", "structural_role"} <= span.keys()
               for span in contract["volumes"])
    assert "format_id" not in contract
    if mode == "series_installment":
        assert contract["series"]["planned_books"] == 3


def test_full_brief_delivery_is_automatic_with_no_keyword_gate_or_extra_author_turn(client, monkeypatch, tmp_path):
    valid = complete_prompt(tmp_path)
    replies = [handoff(), stage_response("story_design"), stage_response("chapter_plan"), response(valid)]
    calls = stub_model(monkeypatch, lambda attempt, *_: replies[attempt - 1])
    # This intentionally contains no launch/delivery keywords. The model can
    # infer delegated intent from the complete prose context, not a regex gate.
    data = create(client, premise="完整设定：21章，每章约550英文词，美国成年读者；单本连续。按我的完整细纲完成。")
    submit(client, data, "就这样吧，按照已经选定的方向处理。")
    current = finish(client, data["id"])
    assert current["status"] == "ready", current["last_error"]
    assert len(calls) == 4
    assert current["prompt_md"] == valid
    assert len(current["messages"]) == 2
    assert "内部交付" not in json.dumps(current["messages"], ensure_ascii=False)
    for field in ("requests_delivery", "delivery_mode"):
        assert field not in current
    first, second = [json.loads(user) for _, user in (calls[0], calls[-1])]
    for field in ("initial_brief", "conversation", "current_complete_skeleton", "previous_pending_questions"):
        assert first[field] == second[field]
    assert "--- assets/novel-skeleton-template.md ---" in calls[-1][0]
    assert "--- references/prompt-contract.md ---" in calls[-1][0]
    assert "--- references/novel-classification.md ---" in calls[-1][0]
    assert "--- references/launch-command.md ---" not in calls[-1][0]
    assert '"mode": "short_novel"' in calls[-1][0]
    assert '"mode": "standalone_long"' not in calls[-1][0]
    for system, user in calls[1:3]:
        assert "--- references/prompt-contract.md ---" not in system
        assert json.loads(user)["conversation"] == first["conversation"]
    assert current["delivery_progress"]["completed_stages"] == ["story_design", "chapter_plan", "assembly"]


def test_delivery_handoff_and_two_repairs_share_the_original_deadline(monkeypatch, tmp_path):
    now = [100.0]
    monkeypatch.setattr(workshop, "time", SimpleNamespace(monotonic=lambda: now[0]))
    valid = complete_prompt(tmp_path)
    bad = aliased_contract(valid)
    replies = [handoff(), stage_response("story_design"), stage_response("chapter_plan"), response(bad), "invalid JSON", response(valid)]

    def respond(attempt, *_):
        now[0] += 40
        return replies[attempt - 1]

    calls = stub_model(monkeypatch, respond)
    timeouts = []
    monkeypatch.setattr(LLMClient, "__init__", lambda self, **kwargs: timeouts.append(kwargs["timeout_seconds"]))
    result, _ = workshop._complete(snapshot(), workshop._skill_bundle()[0])
    assert result["prompt_md"] == valid
    assert set(result) == {"reply", "questions", "prompt_md", "ready_for_confirmation"}
    assert timeouts == [900, 860, 820, 780, 740, 700]
    assert len(calls) == 6
    assert json.loads(calls[-1][1])["previous_unvalidated_skeleton"] == bad


def test_delivery_handoff_never_starts_after_shared_deadline(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(workshop, "time", SimpleNamespace(monotonic=lambda: now[0]))

    def respond(*_):
        now[0] += 901
        return handoff()

    calls = stub_model(monkeypatch, respond)
    with pytest.raises(TimeoutError):
        workshop._complete(snapshot(), workshop._skill_bundle()[0])
    assert len(calls) == 1


@pytest.mark.parametrize("previous_candidate", [False, True])
@pytest.mark.parametrize("mode", workshop._NARRATIVE_MODES)
def test_existing_and_failed_skeletons_get_delivery_contracts_directly(monkeypatch, tmp_path, previous_candidate, mode):
    valid = complete_prompt(tmp_path)
    block = r"\[NARRATIVE_FORMAT_JSON\].*?\[/NARRATIVE_FORMAT_JSON\]"
    selected = re.search(block, make_prompt(tmp_path, mode=mode).read_text(), re.DOTALL).group()
    valid = re.sub(block, lambda _: selected, valid, flags=re.DOTALL)
    previous = aliased_contract(valid) if previous_candidate else valid
    calls = stub_model(monkeypatch, lambda *_: response(valid))
    session = snapshot(**({"_previous_candidate": previous} if previous_candidate else {"prompt_md": previous}))
    result, _ = workshop._complete(session, workshop._skill_bundle()[0])
    assert len(calls) == 1
    assert result["prompt_md"] == valid
    assert "--- references/prompt-contract.md ---" in calls[0][0]
    assert "CURRENT REQUEST PROFILE: EXPLORATION" not in calls[0][0]
    for other in workshop._NARRATIVE_MODES:
        assert (f'"mode": "{other}"' in calls[0][0]) == (other == mode)
    context = json.loads(calls[0][1])
    assert context["previous_unvalidated_skeleton" if previous_candidate else "current_complete_skeleton"] == previous
    assert context["conversation"] == session["messages"]


def test_pause_prevents_internal_delivery_request(monkeypatch):
    import threading
    cancellation = threading.Event()

    def respond(*_):
        cancellation.set()
        return handoff()

    calls = stub_model(monkeypatch, respond)
    with pytest.raises(LLMCancelledError):
        workshop._complete(snapshot(_cancellation_event=cancellation), workshop._skill_bundle()[0])
    assert len(calls) == 1


def test_invalid_delivery_mode_repairs_stay_bounded(monkeypatch):
    calls = stub_model(monkeypatch, lambda *_: handoff("invented_series_mode"))
    with pytest.raises(workshop.ModelResponseError):
        workshop._complete(snapshot(), workshop._skill_bundle()[0])
    assert len(calls) == 3
