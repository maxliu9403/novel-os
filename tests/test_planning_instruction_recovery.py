"""Replay a windowed outline review across an application prompt upgrade."""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

import pytest

import outline_planning as planning
from core.planning_historical_instructions import historical_instructions
from core.request_recovery import (
    authorize_request_retry,
    project_request_recovery,
    request_recovery_scope,
)
from core.source_route_context import SourceEvidenceCoordinator
from test_outline_planning import PlanningModel, SOURCE, make_planner, review
from test_source_route_handoff import evidence_fixture


@pytest.mark.parametrize("scoped, story_first, pre, post", [
    (False, False, "e5cc323d403e1adeaf4b61c1e0ea07019a49c9ab993608ebc9195d3e3fb4b1c0",
     "9ef5992d95049f2cd30cc9a3158701997df8ba9e610f5994a20b1c0c535ba815"),
    (False, True, "f11c6d626296c8077f5ee88262970abd561ef5b9489fb5d8dafecba43d5025c7",
     "8d03a30a812b49f540732adeaf1c1a62f02580f972032bdeadb5519ef83129f6"),
    (True, False, "078d4d6b4826c40bb16b8e7877f67ec3c7718929c820575d5ed3197fe57b0b92",
     "a5d7b3d98688965fac25ae28b495015020d7dfe34ddbd0032b2e11b0ebca72a6"),
    (True, True, "5563689939bd3abe952c123fd48195424abdc60870b01551fd9dd1b5c764c2bf",
     "2095e3fc0f4044514340c61fe9bf6625063f05ab8ba80020b8e3dddb40acf18a"),
])
def test_archived_rules_match_the_shipped_prompt_bytes(scoped, story_first, pre, post):
    # Golden digests generated from Git 098f770 and 041ba18, including seed
    # and review fields that the old namespace identity did not hash.
    policies = historical_instructions(scoped=scoped, story_first=story_first)
    assert tuple(planning.content_hash(asdict(policy)) for policy in policies) == (pre, post)


class RecordingModel(PlanningModel):
    def __init__(self):
        super().__init__(mutate_global=self.historical_ending)
        self.sent = []
        self.interrupt = True

    @staticmethod
    def historical_ending(global_design):
        global_design["ending_contract"]["character_arcs"][0]["required_arc_stage"] = "completed"

    def complete(self, system, user):
        self.sent.append((system, user))
        task, _, evidence = user.rpartition("\n\n[SOURCE_EVIDENCE]\n")
        assert evidence.endswith("\n[/SOURCE_EVIDENCE]")
        context = json.loads(task.rsplit("\n\n", 1)[1])
        if self.interrupt and context.get("review_window", {}).get("requested_chapters") == [5, 6, 7, 8]:
            raise TimeoutError("second review window timed out")
        return super().complete(system, task)


class EvidenceClient:
    provider = "fake"
    model = "fixture-planner"

    def __init__(self, root, context, model):
        self.root, self.context, self.physical = root, context, model

    def complete(self, system, user):
        return SourceEvidenceCoordinator(self.root, self.context).complete(self.physical, system, user)


def two_windows(planner):
    planner._review_fits = lambda instruction, context, **kwargs: (
        "review_window" in context and len(context["review_window"]["requested_chapters"]) <= 4
    )
    return planner


def snapshot(paths):
    return {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def use_shipped_release(patch, release):
    def shipped(*, scoped, story_first):
        return historical_instructions(scoped=scoped, story_first=story_first)[release]
    patch.setattr(planning.StagedOutlinePlanner, "current_instructions", staticmethod(shipped))


def change_runtime_rules(patch):
    from core import planning_review
    for key in ("_GLOBAL_PROMPT", "_GLOBAL_SEED_PROMPT", "_BATCH_PROMPT", "_REVIEW_PROMPT",
                "_LEGACY_REVIEW_PROMPT", "BLUEPRINT_CRAFT"):
        patch.setattr(planning, key, getattr(planning, key) + "\nFuture editorial rules.\n")
    patch.setattr(planning_review, "WINDOW_RULES", planning_review.WINDOW_RULES + "\nFuture window rules.\n")


def test_saved_policy_survives_upgrade_and_fresh_books_take_the_new_rules(tmp_path, monkeypatch):
    original_model = PlanningModel(fail_batch=5)
    original = make_planner(tmp_path / "book", original_model, count=8)
    with pytest.raises(TimeoutError, match="fixture timeout"):
        original.run()
    old_cache = original.cache
    accepted = snapshot([old_cache / "global.json", old_cache / "batch-001.json"])
    original_batch = original.batch_prompt

    # A deployment can revise more than its version number.
    change_runtime_rules(monkeypatch)

    resumed_model = PlanningModel()
    resumed = make_planner(tmp_path / "book", resumed_model, count=8)
    assert len(resumed.run()["chapters"]) == 8
    assert resumed.cache == old_cache
    assert resumed.batch_prompt == original_batch
    assert [(stage, ctx.get("requested_chapters")) for stage, ctx in resumed_model.calls] == [
        ("batch", [5, 6, 7, 8]), ("review", None)
    ]
    assert accepted == snapshot(accepted)

    fresh = make_planner(tmp_path / "other-book", PlanningModel(), count=8)
    fresh.run()
    assert fresh.identity_hash != original.identity_hash
    assert "Future editorial rules." in fresh.global_prompt
    assert "Future editorial rules." in fresh.batch_prompt


@pytest.mark.parametrize("change", ["model", "source"])
def test_policy_pin_preserves_model_switch_and_new_author_input_semantics(tmp_path, monkeypatch, change):
    first = make_planner(tmp_path, PlanningModel(fail_batch=5), count=8)
    with pytest.raises(TimeoutError):
        first.run()
    accepted = snapshot([first.cache / "global.json", first.cache / "batch-001.json"])
    change_runtime_rules(monkeypatch)
    new_model = PlanningModel()
    source = SOURCE
    if change == "model":
        new_model.model = "replacement-planner"
    else:
        source += " She also restores the neighboring theater."

    resumed = make_planner(tmp_path, new_model, count=8, source=source)
    assert len(resumed.run()["chapters"]) == 8
    assert resumed.cache != first.cache
    assert accepted == snapshot(accepted)
    if change == "model":
        assert [s for s, _ in new_model.calls] == ["batch", "review"]
        assert new_model.calls[0][1]["requested_chapters"] == [5, 6, 7, 8]
    else:
        assert new_model.calls[0][0] == "global"
        assert new_model.calls[0][1]["complete_adopted_source"] == source


@dataclass
class InterruptedReview:
    host: tuple
    client: EvidenceClient
    previous: planning.StagedOutlinePlanner
    scratch: Path
    request: Path
    accepted: dict

    def resume(self):
        with request_recovery_scope(*self.host):
            planner = two_windows(make_planner(self.previous.directory.parent, self.client, count=8))
            foundation = planner.run()
        return planner, foundation


@pytest.fixture
def interrupted_review(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    run = root / "outputs/runs/fixture"
    host = root, run / "request-recovery/current.json", {"host": "novel", "run_id": "fixture", "phase": "outline"}
    private = root / "outputs/private"
    context, _ = evidence_fixture(private, "The complete reference text, including its last sentence.")
    client = EvidenceClient(private, context, RecordingModel())
    with monkeypatch.context() as later_release:
        use_shipped_release(later_release, 1)
        current = make_planner(root / "unrelated", client, count=8)
    with monkeypatch.context() as previous_release:
        use_shipped_release(previous_release, 0)
        previous = two_windows(make_planner(run, client, count=8))
        with pytest.raises(TimeoutError, match="second review window"), request_recovery_scope(*host):
            previous.run()
    assert '"required_arc_stage":"completed"' in client.physical.sent[-1][1]
    detail = project_request_recovery(*host)
    assert detail["can_retry_unknown"] and not detail["response_available"]
    request = root / json.loads(host[1].read_text())["request"]["path"]
    accepted = snapshot([previous.cache / name for name in (
        "global.json", "batch-001.json", "batch-002.json", "review-initial-window-001.json",
    )])

    # Pre-pin release restarted and pointed at an empty new namespace.
    pin, = (previous.directory / "instruction-policies").glob("*.instruction-policy.json")
    pin.unlink()
    scratch = previous.cache.parent / current.identity_hash
    scratch.mkdir()
    (scratch / "identity.json").write_text(json.dumps(current.identity))
    progress = previous.directory / "progress.json"
    state = json.loads(progress.read_text())
    state.pop("instruction_policy_sha256", None)
    state.update(identity_hash=current.identity_hash, completed_batches=0, active_step="global_design")
    progress.write_text(json.dumps(state))
    change_runtime_rules(monkeypatch)
    client.physical.interrupt = False
    return InterruptedReview(host, client, previous, scratch, request, accepted)


def authorize(review):
    detail = project_request_recovery(*review.host)
    authorize_request_retry(*review.host, request_sha256=detail["request_sha256"],
                            receipt_sha256=detail["receipt_sha256"], operation_id="retry-window-2")


@pytest.mark.parametrize("late_reply", [False, True], ids=["explicit-retry", "reply-arrived-late"])
def test_second_window_resumes_after_upgrade_and_stale_progress_pointer(interrupted_review, late_reply):
    item = interrupted_review
    sent = item.client.physical.sent
    target, before = sent[-1], len(sent)
    old_receipt = item.request.read_bytes()
    if late_reply:
        record = json.loads(old_receipt)
        raw = json.dumps(review())
        record.update(state="received", response=raw, response_sha256=hashlib.sha256(raw.encode()).hexdigest())
        item.request.write_text(json.dumps(record))
        assert project_request_recovery(*item.host)["response_available"]
    else:
        authorize(item)

    resumed, result = item.resume()
    assert len(result["chapters"]) == 8
    arc, = result["ending_contract"]["character_arcs"]
    assert (arc["required_arc_stage"], arc["reviewed_stage_description"]) == ("resolution", "completed")
    assert item.accepted == snapshot(item.accepted)
    assert resumed.cache == item.previous.cache
    assert sent[before:] == ([] if late_reply else [target])
    record = json.loads(item.request.read_bytes())
    if late_reply:
        assert not record.get("retry_authorizations") and len(record["attempts"]) == 1
    else:
        assert [a["status"] for a in record["retry_authorizations"]] == ["consumed"]
        assert len(record["attempts"]) == 2
        history = item.request.parent / "execution" / item.request.stem / "history"
        assert (history / (hashlib.sha256(old_receipt).hexdigest() + ".json")).read_bytes() == old_receipt

    # A later process with no pending authorization consumes the saved review.
    again = two_windows(make_planner(resumed.directory.parent, item.client, count=8))
    assert again.run() == result
    assert sent[before:] == ([] if late_reply else [target])


def test_two_families_with_same_archived_request_block_without_consuming_retry(interrupted_review):
    item = interrupted_review
    authorize(item)
    (item.scratch / "raw").mkdir()
    prompt, = (item.previous.cache / "raw").glob("review-initial-window-002.*.prompt.txt")
    (item.scratch / "raw" / prompt.name).write_bytes(prompt.read_bytes())
    for name in ("global.json", "batch-001.json"):
        checkpoint = json.loads((item.previous.cache / name).read_bytes())
        checkpoint["identity_hash"] = item.scratch.name
        (item.scratch / name).write_text(json.dumps(checkpoint))
    before = snapshot([item.request, item.host[1], item.previous.directory / "progress.json", *item.accepted])
    count = len(item.client.physical.sent)
    with pytest.raises(ValueError, match="(?i)ambiguous"):
        item.resume()
    assert len(item.client.physical.sent) == count and before == snapshot(before)


@pytest.mark.parametrize("damage", ["checksum", "different-valid-version", "symlink", "missing"])
def test_unverifiable_policy_stops_before_changing_checkpoints(tmp_path, damage):
    first = make_planner(tmp_path, PlanningModel(fail_batch=5), count=8)
    with pytest.raises(TimeoutError):
        first.run()
    saved, = (first.directory / "instruction-policies").glob("*.instruction-policy.json")
    progress = first.directory / "progress.json"
    old = snapshot([progress, first.cache / "global.json", first.cache / "batch-001.json"])
    if damage in {"checksum", "different-valid-version"}:
        value = json.loads(saved.read_bytes())
        if damage == "checksum":
            value["sha256"] = "0" * 64
        else:
            # A coherent but different pin must not supersede the digest
            # actually recorded alongside this interrupted plan.
            value["rules"]["batch_prompt"] += "\nIndependently saved instructions.\n"
            value["sha256"] = planning.content_hash({k: v for k, v in value.items() if k != "sha256"})
        saved.write_text(json.dumps(value))
    elif damage == "symlink":
        outside = tmp_path / "displaced.json"
        saved.rename(outside)
        saved.symlink_to(outside)
    else:
        saved.unlink()
    fresh = PlanningModel()
    with pytest.raises(ValueError):
        make_planner(tmp_path, fresh, count=8).run()
    assert not fresh.calls and old == snapshot(old)


def test_damaged_saved_global_cannot_be_replaced_by_an_upgraded_runtime(tmp_path, monkeypatch):
    interrupted = make_planner(tmp_path, PlanningModel(fail_batch=5), count=8)
    with pytest.raises(TimeoutError):
        interrupted.run()
    saved = interrupted.cache / "global.json"
    record = json.loads(saved.read_bytes())
    record["payload"]["premise"] = "Changed independently of its saved checksum"
    saved.write_text(json.dumps(record))
    original = saved.read_bytes()
    change_runtime_rules(monkeypatch)
    model = PlanningModel()
    with pytest.raises(planning.OutlinePlanningError, match="payload hash mismatch"):
        make_planner(tmp_path, model, count=8).run()
    assert model.calls == [] and saved.read_bytes() == original


def test_dispatched_review_keeps_both_editorial_and_window_rules_after_upgrade(tmp_path, monkeypatch):
    from core import planning_review

    root = tmp_path.resolve()
    host = root, root / "recovery.json", {"phase": "outline", "run_id": "pinned"}
    context, _ = evidence_fixture(root / "private", "Complete source chapter.")
    physical = RecordingModel()
    client = EvidenceClient(root / "private", context, physical)
    first = two_windows(make_planner(root / "pinned", client, count=8))
    with pytest.raises(TimeoutError), request_recovery_scope(*host):
        first.run()
    target = physical.sent[-1]
    before = len(physical.sent)
    detail = project_request_recovery(*host)
    authorize_request_retry(*host, request_sha256=detail["request_sha256"],
                            receipt_sha256=detail["receipt_sha256"], operation_id="retry-pinned-window")
    monkeypatch.setattr(planning, "_REVIEW_PROMPT", planning._REVIEW_PROMPT + "\nUpdated review rules.\n")
    monkeypatch.setattr(planning_review, "WINDOW_RULES", planning_review.WINDOW_RULES + "\nUpdated window rules.\n")
    physical.interrupt = False
    with request_recovery_scope(*host):
        resumed = two_windows(make_planner(root / "pinned", client, count=8))
        result = resumed.run()
    assert len(result["chapters"]) == 8 and physical.sent[before:] == [target]

    fresh = two_windows(make_planner(root / "fresh", client, count=8))
    fresh.run()
    assert "Updated review rules." in physical.sent[-1][1]
    assert "Updated window rules." in physical.sent[-1][1]
