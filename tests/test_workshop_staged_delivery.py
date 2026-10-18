"""Stage boundaries exercise real workshop orchestration without model/network calls."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from api import workshop
from api.workshop_checkpoints import load_checkpoint
from api.workshop_delivery import DeliveryError, DeliveryPlan, checkpoint_fingerprint, parse_stage
from core.llm_client import LLMClient, LLMCancelledError
from test_workshop_api import client, complete_prompt, create, finish  # noqa: F401
from test_workshop_contract_repair import response, stub_model, submit
from test_workshop_prompt_profiles import snapshot
from workshop_delivery_fixtures import chapter_plan, stage_response, story_design
from workshop_assembly_fixtures import parts_from_prompt
from api.workshop_assembly import ASSEMBLY_PARTS, AssemblyError


def test_stage_timeout_retains_story_and_explicit_resume_skips_saved_work(client, monkeypatch, tmp_path):
    valid = complete_prompt(tmp_path)

    def initial(attempt, *_):
        if attempt == 2:
            raise TimeoutError("native request timed out")
        return stage_response("story_design")

    calls = stub_model(monkeypatch, initial)
    data = create(client, premise="保留最初故事事实和完整历史，不得在恢复时删减。")
    submit(client, data, "接受推荐，完成骨架")
    failed = finish(client, data["id"])
    assert failed["status"] == "error" and len(calls) == 2
    assert failed["prompt_md"] == "" and len(failed["messages"]) == 1
    assert failed["delivery_progress"]["completed_stages"] == ["story_design"]
    assert failed["delivery_progress"]["resumable"]
    saved_path = tmp_path / "workshop_v2" / data["id"] / "delivery"
    before = load_checkpoint(saved_path, failed["delivery_run_id"])
    for _ in range(3):
        assert client.get(f'/api/workshop/sessions/{data["id"]}').status_code == 200
    assert len(calls) == 2

    parts = parts_from_prompt(valid)
    resumed_calls = stub_model(monkeypatch, lambda attempt, system, user: stage_response("chapter_plan")
                               if attempt == 1 else json.dumps(parts[json.loads(user)["assembly_part"]]))
    res = client.post(f'/api/workshop/sessions/{data["id"]}/resume', json={"revision": failed["revision"]})
    assert res.status_code == 202, res.text
    current = finish(client, data["id"])
    assert current["status"] == "ready", current["last_error"]
    assert len(resumed_calls) == 1 + len(ASSEMBLY_PARTS)
    assert [json.loads(user)["delivery_stage"] for _, user in resumed_calls] == ["chapter_plan"] + ["assembly"] * len(ASSEMBLY_PARTS)
    assert current["delivery_run_id"] == failed["delivery_run_id"]
    assert current["messages"][:-1] == failed["messages"]
    after = load_checkpoint(saved_path, failed["delivery_run_id"])
    assert before["completed"]["story_design"] == after["completed"]["story_design"]
    for _, user in resumed_calls:
        value = json.loads(user)
        assert value["initial_brief"] == failed["input"]
        assert value["conversation"] == failed["messages"]
        assert value["saved_story_stages"]["story_design"] == before["completed"]["story_design"]["content"]


def test_stage_repairs_share_one_global_allowance_and_cannot_publish_partial(client, monkeypatch):
    replies = ["bad stage", stage_response("story_design"), "bad chapters", "still bad"]
    calls = stub_model(monkeypatch, lambda attempt, *_: replies[attempt - 1])
    data = create(client)
    submit(client, data, "完整交付")
    failed = finish(client, data["id"])
    assert failed["status"] == "error"
    assert len(calls) == 4  # two total format repairs, not two extra per stage
    assert failed["prompt_md"] == ""
    assert failed["delivery_progress"]["completed_stages"] == ["story_design"]
    assert "阶段结果" in failed["last_error"]
    assert "previous_model_response" not in json.loads(calls[2][1])


def test_cancellation_after_model_return_does_not_save_partial_stage(monkeypatch):
    import threading
    cancel = threading.Event()
    writes = []

    def respond(*_):
        cancel.set()
        return stage_response("story_design")

    calls = stub_model(monkeypatch, respond)
    current = snapshot(_cancellation_event=cancel, _save_delivery_checkpoint=lambda record: writes.append(deepcopy(record)))
    with pytest.raises(LLMCancelledError):
        workshop._complete(current, workshop._skill_bundle()[0])
    assert len(calls) == 1
    assert writes == []


def test_stage_save_failure_never_triggers_another_model_call(monkeypatch):
    calls = stub_model(monkeypatch, lambda *_: stage_response("story_design"))

    def fail_after_story(record):
        if record["completed"]:
            raise OSError("disk write failed")

    with pytest.raises(OSError):
        workshop._complete(snapshot(_save_delivery_checkpoint=fail_after_story), workshop._skill_bundle()[0])
    assert len(calls) == 1


def test_checkpoint_io_cannot_extend_deadline_before_next_model_call(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(workshop, "time", SimpleNamespace(monotonic=lambda: now[0]))
    calls = stub_model(monkeypatch, lambda *_: stage_response("story_design"))

    def slow_save(_):
        now[0] += 2701

    with pytest.raises(TimeoutError):
        workshop._complete(snapshot(_save_delivery_checkpoint=slow_save), workshop._skill_bundle()[0])
    assert len(calls) == 1


def test_resume_assembly_is_local_and_revalidates_full_contract(monkeypatch, tmp_path):
    valid = complete_prompt(tmp_path)
    current = snapshot(skill_version=workshop._skill_bundle()[1])
    plan = DeliveryPlan(current, "short_novel")
    plan.finish("story_design", story_design())
    plan.finish("chapter_plan", chapter_plan())
    result = json.loads(response(valid))
    plan.finish("assembly", result)
    current["_delivery_record"] = deepcopy(plan.record)
    calls = stub_model(monkeypatch, lambda *_: pytest.fail("saved assembly must not call model"))
    published, _ = workshop._complete(current, workshop._skill_bundle()[0])
    assert published["prompt_md"] == valid and not calls
    current["_delivery_record"]["completed"]["assembly"]["content"]["prompt_md"] = "incomplete"
    with pytest.raises(workshop.ModelResponseError, match="已保存整合结果"):
        workshop._complete(current, workshop._skill_bundle()[0])
    assert not calls


@pytest.mark.parametrize("mutate", [
    lambda d: d["chapters"].pop(),
    lambda d: d["chapters"].reverse(),
    lambda d: d["chapters"][1].update(number=1),
    lambda d: d["chapters"][0].update(number=True),
    lambda d: d["chapters"][0].update(plan=""),
    lambda d: d.update(total_chapters=22),
    lambda d: d.update(narrative_mode="standalone_long"),
])
def test_incomplete_or_drifting_chapter_maps_cannot_be_checkpointed(mutate):
    data = chapter_plan()
    mutate(data)
    with pytest.raises(DeliveryError):
        parse_stage(json.dumps(data), "chapter_plan", "short_novel", 21)


def test_fingerprint_covers_author_input_and_ignores_transient_runtime():
    session = snapshot()
    digest = checkpoint_fingerprint(session, "skill1")
    assert checkpoint_fingerprint({**session, "revision": 90, "job_id": "new", "status": "running"}, "skill1") == digest
    for key, value in (("input", {"premise": "changed"}), ("messages", []), ("questions", ["new"]),
                       ("prompt_md", "different"), ("context_start_revision", 2), ("pending_input_fields", ["mode"]),
                       ("input_needs_revision", True)):
        assert checkpoint_fingerprint({**session, key: value}, "skill1") != digest
    assert checkpoint_fingerprint(session, "skill2") != digest


def test_final_assembly_cannot_change_saved_mode_or_chapter_count(monkeypatch, tmp_path):
    valid = complete_prompt(tmp_path)
    part = json.dumps(parts_from_prompt(valid)["publication"])
    replies = [stage_response("story_design", 20), stage_response("chapter_plan", 20),
               part, part, part]
    calls = stub_model(monkeypatch, lambda attempt, *_: replies[attempt - 1])
    writes = []
    with pytest.raises(AssemblyError, match="章节数"):
        workshop._complete(snapshot(_save_delivery_checkpoint=lambda record: writes.append(deepcopy(record))), workshop._skill_bundle()[0])
    assert len(calls) == 5
    assert set(writes[-1]["completed"]) == {"story_design", "chapter_plan"}
