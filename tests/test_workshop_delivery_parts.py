"""Part persistence uses the same fenced save seam as full delivery stages."""
from copy import deepcopy
import json

import pytest

from api import workshop_delivery as delivery
from api.workshop_assembly import ASSEMBLY_PARTS
from api.workshop_checkpoints import load_checkpoint, save_checkpoint
from workshop_delivery_fixtures import chapter_plan, story_design


def session(tmp_path):
    directory = tmp_path / "delivery"
    return {
        "id": "a" * 32, "revision": 7, "delivery_run_id": "b" * 32,
        "skill_version": "skill1", "input": {"premise": "全本故事"}, "messages": [],
        "_save_delivery_checkpoint": lambda record: save_checkpoint(directory, record["run_id"], record),
    }


@pytest.fixture
def persisted_plan(tmp_path, monkeypatch):
    # This suite isolates durability from the separate part schema test suite.
    monkeypatch.setattr(delivery, "parse_part", lambda text, *_: json.loads(text))
    monkeypatch.setattr(delivery, "render_part", lambda part, data: f"## {part}\n{data['text']}")
    current = session(tmp_path)
    plan = delivery.DeliveryPlan(current, "short_novel")
    plan.finish("story_design", story_design())
    plan.finish("chapter_plan", chapter_plan())
    return current, plan


def test_partial_resume_keeps_old_stages_and_skips_completed_parts(persisted_plan, tmp_path):
    current, plan = persisted_plan
    directory = tmp_path / "delivery"
    legacy = load_checkpoint(directory, current["delivery_run_id"])
    for part in ASSEMBLY_PARTS[:2]:
        plan.begin_part(part)
        plan.finish_part(part, {"text": f"完整的{part}"})
    saved = load_checkpoint(directory, current["delivery_run_id"])
    assert saved["completed"] == legacy["completed"]
    restored = delivery.DeliveryPlan({**current, "_delivery_record": saved}, "short_novel")
    assert restored.active_part == ASSEMBLY_PARTS[2]
    assert list(restored.saved_parts) == list(ASSEMBLY_PARTS[:2])
    assert restored.saved_parts[ASSEMBLY_PARTS[1]]["text"] == f"完整的{ASSEMBLY_PARTS[1]}"
    copy = restored.saved_parts
    copy[ASSEMBLY_PARTS[0]]["text"] = "not persisted"
    assert restored.saved_parts[ASSEMBLY_PARTS[0]]["text"] != "not persisted"
    event = restored.part_event("started", restored.active_part)
    assert event["assembly_part"] == ASSEMBLY_PARTS[2]
    assert event["completed_parts"] == 2 and event["total_parts"] == len(ASSEMBLY_PARTS)
    assert event["event"] == "delivery.part.started"


def test_targeted_repair_adds_revision_without_replacing_saved_part(persisted_plan, tmp_path):
    current, plan = persisted_plan
    for part in ASSEMBLY_PARTS:
        plan.begin_part(part)
        plan.finish_part(part, {"text": part})
    original = deepcopy(plan.record["assembly_parts"]["completed"])
    first = ASSEMBLY_PARTS[0]
    with pytest.raises(delivery.DeliveryError, match="不可覆盖"):
        plan.finish_part(first, {"text": "修订"})
    plan.begin_part(first)
    saved = load_checkpoint(tmp_path / "delivery", current["delivery_run_id"])
    resumed = delivery.DeliveryPlan({**current, "_delivery_record": saved}, "short_novel")
    assert resumed.active_part == first
    assert resumed.saved_parts[first] == {"text": first}
    resumed.revise_part(first, {"text": "修订"})
    assert resumed.saved_parts[first] == {"text": "修订"}
    assert resumed.active_part is None
    assert {key: {k: v for k, v in item.items() if k != "sha256"}
            for key, item in resumed.record["assembly_parts"]["completed"].items()} == original
    assert len(resumed.record["assembly_parts"]["revisions"]) == 1
    assert resumed.context({})["saved_assembly_parts"][first] == {"text": "修订"}


def test_partial_results_never_count_as_finalized_assembly(persisted_plan):
    _, plan = persisted_plan
    with pytest.raises(delivery.DeliveryError, match="跳过"):
        plan.begin_part(ASSEMBLY_PARTS[1])
    plan.begin_part(ASSEMBLY_PARTS[0])
    plan.finish_part(ASSEMBLY_PARTS[0], {"text": "完整片段"})
    with pytest.raises(delivery.DeliveryError, match="所有整合小块"):
        plan.finish("assembly", {"prompt_md": "Incomplete"})
    with pytest.raises(delivery.DeliveryError, match="修订"):
        plan.revise_part(ASSEMBLY_PARTS[0], {"text": "revision"})


def test_invalid_part_is_not_saved_and_save_callback_failure_is_propagated(persisted_plan, tmp_path, monkeypatch):
    current, plan = persisted_plan
    part = ASSEMBLY_PARTS[0]
    plan.begin_part(part)
    directory = tmp_path / "delivery"
    before = load_checkpoint(directory, current["delivery_run_id"])

    def invalid(*_):
        raise ValueError("Malformed part")

    monkeypatch.setattr(delivery, "parse_part", invalid)
    with pytest.raises(ValueError, match="Malformed"):
        plan.finish_part(part, {"text": "invalid"})
    assert load_checkpoint(directory, current["delivery_run_id"]) == before

    def fenced(_):
        raise RuntimeError("cancelled or stale job")

    current["_save_delivery_checkpoint"] = fenced
    with pytest.raises(RuntimeError, match="cancelled"):
        plan.begin_part(part)
    assert load_checkpoint(directory, current["delivery_run_id"]) == before


def test_local_revalidation_can_clear_pending_repair_without_replacing_content(persisted_plan, tmp_path):
    current, plan = persisted_plan
    with pytest.raises(delivery.DeliveryError):
        plan.clear_part_repair()
    for part in ASSEMBLY_PARTS:
        plan.begin_part(part)
        plan.finish_part(part, {"text": part})
    plan.begin_part(ASSEMBLY_PARTS[2])
    before = deepcopy(plan.record["assembly_parts"])
    plan.clear_part_repair()
    assert plan.active_part is None
    assert plan.record["assembly_parts"]["completed"] == before["completed"]
    assert plan.record["assembly_parts"]["revisions"] == before["revisions"]
    plan.finish("assembly", {"prompt_md": "Validated complete artifact"})
    saved = load_checkpoint(tmp_path / "delivery", current["delivery_run_id"])
    assert saved["assembly_parts"]["active_part"] is None
    with pytest.raises(delivery.DeliveryError):
        plan.begin_part(ASSEMBLY_PARTS[0])
    with pytest.raises(delivery.DeliveryError):
        plan.clear_part_repair()
