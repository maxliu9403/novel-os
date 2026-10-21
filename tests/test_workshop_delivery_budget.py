"""Delivery requests must not inherit the exhausted discussion/stage allowance."""
from types import SimpleNamespace

import pytest

from api import workshop
from core.llm_client import LLMClient
from test_workshop_contract_repair import stub_model
from test_workshop_prompt_profiles import snapshot
from workshop_delivery_fixtures import stage_response


def test_third_stage_gets_its_own_request_allowance(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(workshop, "time", SimpleNamespace(monotonic=lambda: now[0]))
    allowances = []

    def respond(attempt, *_):
        if attempt == 1:
            now[0] += 414
            return stage_response("story_design")
        if attempt == 2:
            now[0] += 398
            return stage_response("chapter_plan")
        raise TimeoutError("synthetic third-stage stop")

    stub_model(monkeypatch, respond)
    monkeypatch.setattr(LLMClient, "__init__", lambda self, **kw: allowances.append(kw["timeout_seconds"]))
    with pytest.raises(TimeoutError):
        workshop._complete(snapshot(), workshop._skill_bundle()[0])
    assert allowances == [900, 900, 900]


def test_delivery_total_budget_does_not_reset_at_each_part(monkeypatch, tmp_path):
    from test_workshop_api import complete_prompt
    from workshop_assembly_fixtures import parts_from_prompt
    import json

    parts = parts_from_prompt(complete_prompt(tmp_path))
    now = [100.0]
    monkeypatch.setattr(workshop, "time", SimpleNamespace(monotonic=lambda: now[0]))
    allowances = []

    def respond(attempt, system, user):
        now[0] += 450
        context = json.loads(user)
        return (json.dumps(parts[context["assembly_part"]]) if "assembly_part" in context
                else stage_response(context["delivery_stage"]))

    calls = stub_model(monkeypatch, respond)
    monkeypatch.setattr(LLMClient, "__init__", lambda self, **kw: allowances.append(kw["timeout_seconds"]))
    with pytest.raises(workshop.DeliveryTimeoutError) as error:
        workshop._complete(snapshot(), workshop._skill_bundle()[0])
    assert error.value.task_expired
    assert len(calls) == 6 and allowances == [900] * 5 + [450]


def test_delivery_budget_is_projected_without_resetting_start(tmp_path):
    from api.workshop_diagnostics import make_diagnostic_callback, runtime_progress
    from datetime import datetime

    session = {"id": "a" * 32, "revision": 3, "job_id": "job", "status": "running",
               "turn_timeout_seconds": 900, "_diagnostic_dir": tmp_path,
               "logs": [{"event": "turn_started", "time": "2026-10-13T01:00:00+00:00"}]}
    callback = make_diagnostic_callback(session, attempt=4, operation="brainstorm", route={})
    callback({"event": "attempt.start", "timeout_seconds": 900,
              "task_budget_seconds": 2700, "request_timeout_seconds": 900})
    progress = runtime_progress(session, tmp_path)
    assert progress["task_budget_seconds"] == 2700
    assert progress["request_timeout_seconds"] == 900
    assert (datetime.fromisoformat(progress["deadline_at"]) - datetime.fromisoformat(progress["started_at"])).total_seconds() == 2700
    callback({"event": "delivery.part.started", "delivery_stage": "assembly", "stage_index": 3,
              "completed_stages": 2, "assembly_part": "publication", "completed_parts": 0, "total_parts": 6})
    callback({"event": "attempt.completed"})
    callback({"event": "delivery.part.completed", "delivery_stage": "assembly", "stage_index": 3,
              "completed_stages": 2, "assembly_part": "publication", "completed_parts": 1, "total_parts": 6})
    assert runtime_progress(session, tmp_path)["phase"] == "delivery"
