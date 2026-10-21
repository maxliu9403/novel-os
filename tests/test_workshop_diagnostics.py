"""Diagnostic metadata remains durable, private and independent of workshop state."""
from concurrent.futures import ThreadPoolExecutor
import json
import stat
import subprocess

from api import workshop, workshop_diagnostics as diagnostics, workshop_titles
from core import llm_client, provider_settings
from test_workshop_api import client, create, finish, model_result, ready  # noqa: F401


ROUTE = {"provider": "codex", "model": "diagnostic-model", "base_url": "https://private.invalid",
         "api_key": "private-api-key", "max_tokens": 16384, "reasoning_effort": "high",
         "timeout_seconds": 900}


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def snapshot(tmp_path):
    return {"id": "a" * 32, "revision": 7, "job_id": "job-7", "_diagnostic_dir": tmp_path / "diagnostics"}


def test_activity_projection_never_accepts_content_or_token_accounting():
    value = {"event": "upstream.activity", "activity_kind": "reasoning_summary", "metric_count": 2,
             "metric_temporality": "delta", "observation_source": "otel_metrics",
             "observed_at": "2026-10-12T10:00:00Z", "request_id": "current",
             "usage": {"input_tokens": 99}, "output_token_count": 100,
             "model_output_chars": 50, "reasoning": "PRIVATE", "body": "PRIVATE"}
    safe = diagnostics._safe_event(value)
    assert safe["metric_count"] == 2 and safe["request_id"] == "current"
    assert not ({"usage", "output_token_count", "model_output_chars", "reasoning", "body"} & safe.keys())
    for changes in ({"metric_count": True}, {"metric_count": -1}, {"metric_count": 0},
                    {"metric_count": 2**63}, {"activity_kind": "reasoning_text"}, {"activity_kind": []},
                    {"metric_temporality": "unknown"}, {"metric_temporality": {}},
                    {"observed_at": "PRIVATE"}, {"observation_source": "local_heartbeat"}):
        assert diagnostics._safe_event({**value, **changes}) == {}


def test_stage_projection_accepts_only_bounded_metadata():
    value = {"event": "delivery.stage.completed", "delivery_stage": "story_design", "stage_index": 1,
             "completed_stages": 1, "story": "PRIVATE", "request_id": "PRIVATE"}
    assert diagnostics._safe_event(value) == {key: value[key] for key in (
        "event", "delivery_stage", "stage_index", "completed_stages")}
    for changes in ({"stage_index": True}, {"stage_index": 2}, {"completed_stages": 4},
                    {"completed_stages": -1}, {"delivery_stage": "PRIVATE"}, {"delivery_stage": []}):
        assert diagnostics._safe_event({**value, **changes}) == {}


def test_observer_reports_bounded_queue_loss_without_provider_payload():
    assert diagnostics._safe_event({"event": "upstream.observer_stopped", "dropped_event_count": 3,
                                    "dropped_payload": "PRIVATE"}) == {
        "event": "upstream.observer_stopped", "dropped_event_count": 3}


def fake_model(monkeypatch, respond):
    calls = []
    monkeypatch.setattr(provider_settings, "resolve_text_route", lambda _: ROUTE.copy())

    class FakeClient:
        def __init__(self, **options):
            self.options = options

        def complete(self, system, user):
            calls.append((self.options, system, user))
            callback = self.options.get("diagnostic_callback")
            if callback:
                callback({"event": "request.start", "request_id": f"request-{len(calls)}",
                          "system_chars": len(system), "system_bytes": len(system.encode()),
                          "user_chars": len(user), "user_bytes": len(user.encode())})
            return respond(len(calls), callback)

    monkeypatch.setattr(llm_client, "LLMClient", FakeClient)
    return calls


def test_safe_allowlist_context_and_permissions(tmp_path):
    session = snapshot(tmp_path)
    callback = diagnostics.make_diagnostic_callback(session, attempt=2, operation="brainstorm", route=ROUTE)
    callback({"event": "request.start", "request_id": "request-2", "system_chars": 250,
              "user_chars": 100, "prompt": "PRIVATE PROMPT", "output": "PRIVATE OUTPUT",
              "error": "PRIVATE ERROR", "api_key": "PRIVATE KEY", "authorization": "PRIVATE AUTH",
              "private_reasoning": "PRIVATE REASONING", "attempt": 99, "revision": 99,
              "session_id": "forged", "job_id": "forged", "operation": "forged",
              "last_cli_event": "private-provider-event", "last_item_type": "PRIVATE ITEM",
              "phase": "awaiting_cli_event", "input_sent_seconds": 0.03,
              "output_chars": 12, "output_bytes": 36,
              "usage": {"input_tokens": 250, "output_tokens": 30, "private_text": "PRIVATE TOKEN"}})
    callback({"event": "PRIVATE UNKNOWN EVENT"})
    callback({"event": "request.timeout", "error_category": "timeout", "elapsed_seconds": 900})
    path = session["_diagnostic_dir"] / "7-brainstorm.jsonl"
    rows = records(path)
    assert len(rows) == 2
    assert all(row["session_id"] == session["id"] and row["task_id"] == session["id"]
               and row["job_id"] == "job-7" and row["revision"] == 7 and row["attempt"] == 2
               and row["operation"] == "brainstorm" and row["route"] == "workshop"
               and row["request_id"] == "request-2" for row in rows)
    assert rows[0]["last_cli_event"] == rows[0]["last_item_type"] == "other"
    assert rows[0]["usage"] == {"input_tokens": 250, "output_tokens": 30}
    assert rows[1]["error_category"] == "timeout"
    assert rows[0]["reasoning_effort"] == "high"
    assert rows[0]["phase"] == "awaiting_cli_event" and rows[0]["input_sent_seconds"] == 0.03
    assert rows[0]["output_chars"] == 12 and rows[0]["output_bytes"] == 36
    assert "PRIVATE" not in path.read_text()
    assert "private-api-key" not in path.read_text()
    assert "private.invalid" not in path.read_text()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_parallel_appends_and_file_cap_preserve_latest_terminal(tmp_path, monkeypatch):
    session = snapshot(tmp_path)
    callbacks = [diagnostics.make_diagnostic_callback(session, attempt=i, operation="brainstorm")
                 for i in range(1, 4)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda i: callbacks[i % 3]({"event": "heartbeat", "stdout_bytes": i}), range(120)))
    path = session["_diagnostic_dir"] / "7-brainstorm.jsonl"
    assert len(records(path)) == 120
    assert {row["stdout_bytes"] for row in records(path)} == set(range(120))

    # Exercise the overflow path cheaply; production limits remain 5 MiB / 64 KiB.
    monkeypatch.setattr(diagnostics, "MAX_FILE_BYTES", 8192)
    monkeypatch.setattr(diagnostics, "TERMINAL_RESERVE_BYTES", 2048)
    monkeypatch.setattr(diagnostics, "MAX_RECORD_BYTES", 1024)
    callback = diagnostics.make_diagnostic_callback(session, attempt=1, operation="titles")
    path = session["_diagnostic_dir"] / "7-titles.jsonl"
    callback({"event": "request.start"})
    for _ in range(100):
        callback({"event": "heartbeat", "stdout_bytes": 100000})
    # The capacity marker is visible even if the worker dies before a terminal.
    assert records(path)[-1]["event"] == "diagnostics.capped"
    for _ in range(30):
        callback({"event": "cli.failed", "exit_code": 1})
    callback({"event": "request.timeout", "error_category": "timeout", "elapsed_seconds": 900})
    rows = records(path)
    assert path.stat().st_size <= diagnostics.MAX_FILE_BYTES
    assert rows[-1]["event"] == "request.timeout"
    assert rows[-1]["dropped_events"] >= 70
    assert any(row.get("tail_compacted") for row in rows)
    assert rows[0]["event"] == "request.start"


def test_timeout_logs_survive_worker_and_keep_full_context(client, monkeypatch, tmp_path):
    real_complete = workshop._complete
    data = ready(client, monkeypatch, tmp_path, premise="PRIVATE original author brief")
    monkeypatch.setattr(workshop, "_complete", real_complete)

    def fail(_attempt, callback):
        callback({"event": "process_started", "pid": 987, "stdout_bytes": 0, "stderr_bytes": 0})
        callback({"event": "heartbeat", "stdout_bytes": 30, "stderr_bytes": 20,
                  "last_cli_event": "turn.started", "idle_seconds": 45})
        raise llm_client.LLMError("Codex timed out; PRIVATE provider error") from subprocess.TimeoutExpired(
            ["PRIVATE command with credentials"], 900)

    calls = fake_model(monkeypatch, fail)
    message = "PRIVATE latest author instruction"
    response = client.post(f'/api/workshop/sessions/{data["id"]}/turns',
                           json={"revision": data["revision"], "message": message})
    assert response.status_code == 202
    result = finish(client, data["id"])
    assert result["status"] == "error"
    assert len(calls) == 1
    context = json.loads(calls[0][2])
    expected_messages = data["messages"] + [{"role": "user", "content": message}]
    assert context["initial_brief"] == data["input"]
    assert context["current_complete_skeleton"] == data["prompt_md"]
    assert context["conversation"] == expected_messages
    assert result["prompt_md"] == data["prompt_md"]
    assert result["messages"] == expected_messages
    directory = tmp_path / "workshop_v2" / data["id"]
    path = directory / "diagnostics" / f'{data["revision"] + 1}-brainstorm.jsonl'
    rows = records(path)
    assert rows[-1]["event"] == "attempt.timeout"
    assert any(row["event"] == "heartbeat" for row in rows)
    assert all(row["revision"] == data["revision"] + 1 and row["attempt"] == 1
               and row["job_id"] == response.json()["job_id"] for row in rows)
    assert "PRIVATE" not in path.read_text()
    saved = json.loads((directory / "session.json").read_text())
    assert "_diagnostic_dir" not in saved and "diagnostic_callback" not in saved
    reloaded = workshop.WorkshopService(workshop.ProjectService(tmp_path / "projects")).get(data["id"])
    assert reloaded["messages"] == expected_messages
    assert records(path)[-1]["event"] == "attempt.timeout"


def test_format_retries_log_categories_and_request_attempts(tmp_path, monkeypatch):
    session = {**snapshot(tmp_path), "input": {"premise": "PRIVATE full author brief"},
               "messages": [{"role": "user", "content": "PRIVATE history"}],
               "prompt_md": "", "turn_timeout_seconds": 900}
    replies = ["PRIVATE invalid JSON", json.dumps({"reply": "PRIVATE missing fields"}),
               json.dumps(model_result()[0])]
    calls = fake_model(monkeypatch, lambda attempt, _: replies[attempt - 1])
    result, _ = workshop._complete(session, "PRIVATE complete skill")
    assert result["prompt_md"] is None
    assert len(calls) == 3
    rows = records(session["_diagnostic_dir"] / "7-brainstorm.jsonl")
    failures = [row for row in rows if row["event"] == "validation.failure"]
    assert [(row["attempt"], row["request_id"], row["error_category"]) for row in failures] == [
        (1, "request-1", "json_decode"), (2, "request-2", "response_schema")]
    assert [row["attempt"] for row in rows if row["event"] == "format_retry"] == [1, 2]
    assert rows[-1]["event"] == "attempt.completed" and rows[-1]["attempt"] == 3
    assert "PRIVATE" not in json.dumps(rows)
    assert json.loads(calls[-1][2])["conversation"] == session["messages"]
    assert "PRIVATE invalid JSON" in calls[1][2]


def test_title_worker_logs_retry_without_changing_naming_context(client, monkeypatch, tmp_path):
    data = ready(client, monkeypatch, tmp_path)
    candidates = [{"title": title, "reason": "PRIVATE title reason"} for title in ("One", "Two", "Three")]
    calls = fake_model(monkeypatch, lambda attempt, _: "PRIVATE invalid title output" if attempt == 1
                       else json.dumps({"candidates": candidates}))
    instructions = "PRIVATE naming instructions"
    response = client.post(f'/api/workshop/sessions/{data["id"]}/titles',
                           json={"revision": data["revision"], "instructions": instructions,
                                 "current_title": "PRIVATE current title"})
    assert response.status_code == 202
    result = finish(client, data["id"])
    assert result["status"] == "ready", result["last_error"]
    assert len(calls) == 2
    assert result["messages"] == data["messages"]
    assert result["naming"]["candidates"] == candidates
    context = json.loads(calls[0][2])
    assert context["confirmed_skeleton"] == result["prompt_md"]
    assert context["author_brief"] == data["input"]
    assert context["naming_history"][-1]["instructions"] == instructions
    assert context["naming_history"][-1]["current_title"] == "PRIVATE current title"
    path = tmp_path / "workshop_v2" / data["id"] / "diagnostics" / f'{data["revision"] + 1}-titles.jsonl'
    rows = records(path)
    assert {row["operation"] for row in rows} == {"titles"}
    assert {row["attempt"] for row in rows} == {1, 2}
    assert rows[-1]["event"] == "attempt.completed"
    assert "PRIVATE" not in path.read_text()


def test_disabled_and_unwritable_diagnostics_do_not_change_completion(tmp_path, monkeypatch):
    assert diagnostics.make_diagnostic_callback({"revision": 1}, attempt=1, operation="brainstorm") is None
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("keep")
    session = {**snapshot(tmp_path), "_diagnostic_dir": blocker / "diagnostics", "input": {},
               "messages": [], "prompt_md": "", "turn_timeout_seconds": 900}
    fake_model(monkeypatch, lambda *_: json.dumps(model_result()[0]))
    result, _ = workshop._complete(session, "rules")
    assert result["reply"]
    assert blocker.read_text() == "keep"
