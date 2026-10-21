"""Poster activity estimates retain attribution across real worker contexts."""
from threading import Event
import time
from types import SimpleNamespace

import pytest

from api import poster_advisory, poster_tasks
from api.jobs import JobRunner
from core.cover_store import CoverStore
from core.usage_costs import costs_payload
from core.usage_tracking import (POSTER_ACTIVITIES, pricing_scope, read_records, usage_activity, usage_scope)
from tests.test_poster_advisory import Adviser, case, response
from tests.test_poster_pricing_snapshot import emit, prices


def wait_job(runner, identifier):
    for _ in range(500):
        state = runner.get(identifier)
        if state["status"] != "running":
            assert state["status"] == "done", state
            return
        time.sleep(.01)
    raise AssertionError("Usage test worker did not finish")


def test_cover_activities_reconcile_with_total_and_leave_legacy_unknown_attribution(tmp_path):
    with usage_scope(tmp_path, "cover"), pricing_scope(prices(fx=6)):
        for index, activity in enumerate(POSTER_ACTIVITIES):
            with usage_activity(activity):
                emit(activity, status="failed" if index % 2 else "completed")
        emit("legacy")
    with usage_scope(tmp_path, "manuscript"), usage_activity("generate"):
        emit("manuscript")
    records = read_records(tmp_path)
    assert "activity" not in records["legacy"] and "activity" not in records["manuscript"]
    payload = costs_payload(list(records.values()), prices(fx=10))
    activities = {row["activity"]: row for row in payload["cover_activities"]}
    assert set(activities) == {*POSTER_ACTIVITIES, "legacy"}
    for row in activities.values():
        assert row["request_count"] == 1 and row["total_tokens"] == 2_000_000
        assert row["estimated_cost_cny"] == 72
    cover = next(row for row in payload["stages"] if row["stage"] == "cover")
    assert sum(row["request_count"] for row in activities.values()) == cover["request_count"]
    assert sum(row["estimated_cost_cny"] for row in activities.values()) == cover["estimated_cost_cny"]


def test_late_usage_preserves_first_activity_and_does_not_reclassify_legacy(tmp_path):
    with usage_scope(tmp_path, "cover"):
        with usage_activity("plan"), pricing_scope(prices(fx=6)):
            emit("request", usage={}, status="started")
        emit("legacy", usage={}, status="started")
        with usage_activity("advisory"), pricing_scope(prices(fx=9)):
            emit("request", usage={"input_tokens": 1_000_000, "cached_input_tokens": 0}, status="failed")
            emit("legacy")
        emit("request", usage={"output_tokens": 1_000_000}, status="failed")
        emit("unscoped")
    records = read_records(tmp_path)
    assert records["request"]["activity"] == "plan"
    assert records["request"]["pricing_snapshot"]["usd_to_cny"] == 6
    assert "activity" not in records["legacy"] and "pricing_snapshot" not in records["legacy"]
    assert "activity" not in records["unscoped"]


@pytest.mark.parametrize("kind", ["plan", "generate", "refine", "retry", "typography"])
def test_durable_job_binds_real_worker_owner_activity_and_frozen_prices(tmp_path, monkeypatch, kind):
    project = tmp_path / "project"
    flow = SimpleNamespace(store=CoverStore(project), context=SimpleNamespace(owner_scope="owner"))
    runner = JobRunner()
    monkeypatch.setattr("core.usage_costs.load_pricing", lambda: prices(fx=6))
    def operation(prepared, progress):
        if kind != "typography":
            emit("actual-attempt")
        progress({"completed": 1, "total": 1})
        return {"saved": True}
    # Ambient request context must not assign the job to a different project or
    # a manuscript bill. Local typography must never fabricate a model record.
    other = tmp_path / "other-owner"
    with usage_scope(other, "manuscript"), usage_activity("advisory"), pricing_scope(prices(fx=20)):
        started = poster_tasks.start(flow, runner, kind=kind, request_id="request-" + kind,
            inputs={}, preflight=lambda: {}, operation=operation, project_id="project")
    private = poster_tasks.read(flow, started["task_id"])
    wait_job(runner, private["job_id"])
    final = poster_tasks.project(poster_tasks.read(flow, started["task_id"]), runner)
    assert final["status"] == "complete" and final["pricing_snapshot"]["usd_to_cny"] == 6
    assert not read_records(other)
    records = read_records(project)
    if kind == "typography":
        assert not records
    else:
        actual = records["actual-attempt"]
        assert actual["stage"] == "cover" and actual["activity"] == kind
        assert actual["pricing_snapshot"]["usd_to_cny"] == 6


def test_optional_advisory_copied_worker_retains_price_and_owner_after_parent_finishes(case, monkeypatch):
    runner = JobRunner()
    entered, release = Event(), Event()
    monkeypatch.setattr("core.usage_costs.load_pricing", lambda: prices(fx=6))
    class TrackedAdviser(Adviser):
        def complete_with_images(self, *args):
            entered.set()
            assert release.wait(5), "Advisory worker was not released"
            emit("advice-attempt")
            return super().complete_with_images(*args)
    adviser = TrackedAdviser(response(case))
    def operation(prepared, progress):
        emit("image-attempt", model="codex_auto", kind="image", image_count=4, usage={})
        # Call schedule directly under generate to verify execute binds its own
        # activity, rather than relying only on the HTTP helper's wrapper.
        poster_advisory.schedule(case.flow, runner, case.result,
            client_factory=lambda: adviser, image_service=case.service)
        return {"cover_set_id": case.result.cover_set_id}
    try:
        started = poster_tasks.start(case.flow, runner, kind="generate", request_id="parent-generate",
            inputs={}, preflight=lambda: {}, operation=operation, project_id=case.result.project_id)
        private = poster_tasks.read(case.flow, started["task_id"])
        wait_job(runner, private["job_id"])
        assert entered.wait(3)
        monkeypatch.setattr("core.usage_costs.load_pricing", lambda: prices(fx=20))
        release.set()
        for _ in range(500):
            advice = poster_advisory.read(case.flow, case.result.cover_set_id, runner)
            if advice["status"] not in {"queued", "running"}:
                break
            time.sleep(.01)
        assert advice["status"] == "complete", advice
        records = read_records(case.project)
        assert records["image-attempt"]["activity"] == "generate"
        assert records["advice-attempt"]["activity"] == "advisory"
        assert records["advice-attempt"]["stage"] == "cover"
        assert records["advice-attempt"]["pricing_snapshot"]["usd_to_cny"] == 6
        totals = costs_payload(list(records.values()), prices(fx=20))
        rows = {row["activity"]: row for row in totals["cover_activities"]}
        assert rows["advisory"]["estimated_cost_cny"] == 72
        assert rows["generate"]["estimated_cost_cny"] == 6
    finally:
        release.set()
