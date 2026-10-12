"""Public progress reflects durable evidence, not an inferred model thought process."""
import json
import threading

from api import workshop, workshop_diagnostics as diagnostics
from test_workshop_api import client, create, finish, model_result  # noqa: F401


def session(tmp_path):
    return {"id": "a" * 32, "revision": 7, "job_id": "job-7", "status": "running",
            "active_operation": "brainstorm", "turn_timeout_seconds": 900,
            "logs": [{"event": "turn_started", "time": "2026-10-08T07:00:00+00:00"}],
            "_diagnostic_dir": tmp_path / "diagnostics"}


def progress(s):
    return diagnostics.runtime_progress(s, s["_diagnostic_dir"])


def activity(kind, count=1, at="2026-10-12T10:00:00+00:00"):
    return {"event": "upstream.activity", "activity_kind": kind, "metric_count": count,
            "metric_temporality": "delta", "observation_source": "otel_metrics", "observed_at": at}


def test_metric_activity_is_request_scoped_and_does_not_invent_output_chars_or_worker_checks(tmp_path):
    s = session(tmp_path)
    cb = diagnostics.make_diagnostic_callback(s, attempt=1, operation="brainstorm")
    cb({"event": "request.start", "request_id": "one"})
    started = progress(s)
    cb(activity("transport", 2))  # Includes response.created and prewarm completion.
    p = progress(s)
    assert p["last_transport_observed_at"] and p["transport_event_count"] == 2
    assert p["last_activity_observed_at"] is None and p["activity_kind"] is None
    assert p["last_worker_check_at"] == started["last_worker_check_at"]
    cb(activity("reasoning_summary", 3))
    cb(activity("text", 5, "2026-10-12T10:00:01+00:00"))
    cb(activity("transport", 1, "2026-10-12T10:00:02+00:00"))
    p = progress(s)
    assert p["reasoning_event_count"] == 3 and p["text_event_count"] == 5
    assert p["last_activity_observed_at"] == "2026-10-12T10:00:01+00:00"
    assert p["activity_kind"] == "text" and p["activity_time_basis"] == "batch_observed"
    assert p["content_chars"] == 0 and p["last_content_at"] is None
    assert p["phase"] == "starting"  # Metrics do not change the workflow state.
    cb({"event": "request.start", "request_id": "two"})
    reset = progress(s)
    assert reset["request_started_at"] >= started["request_started_at"]
    assert reset["text_event_count"] == reset["reasoning_event_count"] == 0
    assert reset["last_activity_observed_at"] is None
    cb({**activity("text", 999), "request_id": "one"})
    assert progress(s)["text_event_count"] == 0
    cb(activity("text", 1))
    assert progress(s)["text_event_count"] == 1  # Old ID cannot poison the callback.


def test_native_activity_availability_is_known_only_after_observer_start(tmp_path):
    s = session(tmp_path)
    cb = diagnostics.make_diagnostic_callback(s, attempt=1, operation="brainstorm")
    cb({"event": "request.start", "request_id": "one"})
    assert progress(s)["activity_time_basis"] is None
    cb({"event": "upstream.observer_started"})
    assert progress(s)["activity_time_basis"] == "batch_observed"
    assert progress(s)["last_activity_observed_at"] is None
    cb({"event": "request.start", "request_id": "two"})
    assert progress(s)["activity_time_basis"] is None


def test_metric_batches_and_ttft_never_claim_current_continuous_reasoning(tmp_path):
    s = session(tmp_path)
    cb = diagnostics.make_diagnostic_callback(s, attempt=1, operation="brainstorm")
    cb({"event": "request.start", "request_id": "one"})
    for row in ({"event": "heartbeat"}, {"event": "upstream.event", "upstream_event": "codex.api_request",
                                        "endpoint_category": "models", "http_status": 200},
                {"event": "upstream.event", "upstream_event": "codex.turn_ttft", "duration_ms": 10}):
        cb(row)
    assert progress(s)["last_activity_observed_at"] is None
    cb(activity("text", 2, "2026-10-12T10:00:03Z"))
    cb(activity("reasoning_summary", 1, "2026-10-12T10:00:03+00:00"))
    cb(activity("reasoning_summary", 1, "2026-10-12T10:00:00Z"))
    p = progress(s)
    assert p["last_activity_observed_at"] == "2026-10-12T10:00:03Z"
    assert p["activity_kind"] == "text"
    cb({"event": "attempt.timeout"})
    cb(activity("text", 20, "2026-10-12T10:00:04Z"))
    assert progress(s)["phase"] == "failed"
    assert progress(s)["text_event_count"] == 2  # Terminal progress is frozen.


def test_delivery_stage_progress_survives_requests_and_later_attempts(tmp_path):
    s = session(tmp_path)
    cb = diagnostics.make_diagnostic_callback(s, attempt=1, operation="brainstorm")
    cb({"event": "delivery.stage.started", "delivery_stage": "story_design", "stage_index": 1,
        "completed_stages": 0, "story": "PRIVATE"})
    assert progress(s)["phase"] == "delivery"
    cb({"event": "request.start", "request_id": "one"})
    cb(activity("text", 4))
    cb({"event": "request.completed", "output_chars": 12})
    cb({"event": "attempt.completed"})
    cb({"event": "delivery.stage.completed", "delivery_stage": "story_design", "stage_index": 1,
        "completed_stages": 1})
    assert progress(s)["phase"] == "delivery" and progress(s)["completed_stages"] == 1
    cb(activity("text", 40))  # Saved-stage phase must not reopen completed request activity.
    assert progress(s)["text_event_count"] == 4
    later = diagnostics.make_diagnostic_callback(s, attempt=6, operation="brainstorm")
    assert later is not None
    later({"event": "attempt.start"})
    later({"event": "delivery.stage.started", "delivery_stage": "chapter_plan", "stage_index": 2,
           "completed_stages": 1})
    later({"event": "request.start", "request_id": "six"})
    p = progress(s)
    assert p["delivery_stage"] == "chapter_plan" and p["stage_index"] == 2
    assert p["completed_stages"] == 1 and p["current_stage_status"] == "started"
    later({"event": "delivery.stage.reused", "delivery_stage": "chapter_plan", "stage_index": 2,
           "completed_stages": 2})
    assert progress(s)["completed_stages"] == 2
    assert "PRIVATE" not in json.dumps(progress(s))


def test_durable_progress_keeps_worker_cli_and_model_content_separate(tmp_path):
    s = session(tmp_path)
    cb = diagnostics.make_diagnostic_callback(s, attempt=1, operation="brainstorm")
    initial = progress(s)
    assert initial["phase"] == "preparing"
    assert initial["started_at"] == "2026-10-08T07:00:00+00:00"
    assert initial["deadline_at"] == "2026-10-08T07:15:00+00:00"
    cb({"event": "request.start", "request_id": "request-1", "configured_max_tokens": 8192,
        "effective_max_tokens": None, "max_tokens_supported": False})
    cb({"event": "stdin_sent"})
    cb({"event": "cli_event", "last_cli_event": "turn.started"})
    cb({"event": "heartbeat", "stdout_bytes": 101})
    waiting = progress(s)
    assert waiting["phase"] == "waiting"
    assert waiting["last_worker_check_at"] and waiting["last_cli_event_at"]
    assert waiting["last_content_at"] is None and waiting["content_chars"] == 0
    assert waiting["configured_max_tokens"] == 8192
    assert waiting["effective_max_tokens"] is None and waiting["output_limit_supported"] is False
    cb({"event": "upstream.event", "upstream_event": "codex.sse_event",
        "event_kind": "response.completed", "output_token_count": 0,
        "source_timestamp": "2026-10-08T07:01:00Z", "prompt": "PRIVATE"})
    assert progress(s)["last_content_at"] is None
    assert progress(s)["last_upstream_event_at"] == "2026-10-08T07:01:00Z"
    cb({"event": "cli.model_output", "model_output_chars": 123, "body": "PRIVATE"})
    received = progress(s)
    assert received["phase"] == "receiving" and received["content_chars"] == 123
    assert received["last_content_at"]
    # Refreshing the page or reinstantiating a service cannot restart the clock.
    assert progress(dict(s)) == received
    assert received["started_at"] == initial["started_at"]
    assert "PRIVATE" not in json.dumps(received)


def test_retry_resets_attempt_counters_and_rejects_late_old_events(tmp_path):
    s = session(tmp_path)
    first = diagnostics.make_diagnostic_callback(s, attempt=1, operation="brainstorm")
    second = diagnostics.make_diagnostic_callback(s, attempt=2, operation="brainstorm")
    first({"event": "request.start", "request_id": "first"})
    first({"event": "cli.model_output", "model_output_chars": 20})
    second({"event": "attempt.start"})
    second({"event": "request.start", "request_id": "second"})
    first({"event": "cli.model_output", "model_output_chars": 999})
    p = progress(s)
    assert p["attempt"] == 2 and p["request_id"] == "second"
    assert p["content_chars"] == 0 and p["last_content_at"] is None
    assert p["cumulative_content_chars"] == 20 and p["last_turn_content_at"]
    assert p["started_at"] == "2026-10-08T07:00:00+00:00"
    second({"event": "validation.start", "phase": "skeleton_validation"})
    second({"event": "upstream.event", "upstream_event": "codex.websocket_request", "success": True})
    assert progress(s)["phase"] == "validating"
    second({"event": "attempt.timeout"})
    second({"event": "heartbeat"})
    assert progress(s)["phase"] == "failed"


def test_completed_reply_survives_format_repair_as_turn_content(tmp_path):
    s = session(tmp_path)
    second = diagnostics.make_diagnostic_callback(s, attempt=2, operation="brainstorm")
    third = diagnostics.make_diagnostic_callback(s, attempt=3, operation="brainstorm")
    second({"event": "request.start", "request_id": "second"})
    second({"event": "cli.model_output", "model_output_chars": 42684})
    second({"event": "cli.completed", "output_chars": 42684})
    second({"event": "request.completed", "output_chars": 42684})
    received = progress(s)
    assert received["cumulative_content_chars"] == 42684  # Three snapshots, one body.
    assert received["last_completed_attempt"] == 2
    second({"event": "validation.failure", "error_category": "skeleton_contract"})
    second({"event": "attempt.failed", "error_category": "skeleton_contract"})
    second({"event": "format_retry"})
    assert progress(s)["phase"] == "repairing"
    third({"event": "attempt.start"})
    third({"event": "request.start", "request_id": "third"})
    third({"event": "cli_event", "last_cli_event": "turn.started"})
    second({"event": "request.completed", "output_chars": 99999})  # Delayed old request.
    repairing = progress(s)
    assert repairing["phase"] == "waiting" and repairing["repairing_response"] is True
    assert repairing["content_chars"] == 0 and repairing["last_content_at"] is None
    assert repairing["cumulative_content_chars"] == 42684
    assert repairing["last_turn_content_at"] == received["last_turn_content_at"]
    assert repairing["last_completed_attempt"] == 2
    assert progress(dict(s)) == repairing  # Restore does not forget earlier receipt.
    third({"event": "cli.model_output", "model_output_chars": 25})
    third({"event": "cli.model_output", "model_output_chars": 50})
    third({"event": "request.completed", "output_chars": 50})
    assert progress(s)["cumulative_content_chars"] == 42734
    assert progress(s)["last_completed_attempt"] == 3
    third({"event": "attempt.completed"})
    assert progress(s)["repairing_response"] is False


def test_turn_content_is_reset_for_a_new_job_or_revision(tmp_path):
    s = session(tmp_path)
    cb = diagnostics.make_diagnostic_callback(s, attempt=1, operation="brainstorm")
    cb({"event": "request.completed", "output_chars": 42684})
    cb({"event": "format_retry"})
    for changed in ({**s, "job_id": "different-job"}, {**s, "revision": 8}):
        p = progress(changed)
        assert p["cumulative_content_chars"] == 0
        assert p["last_turn_content_at"] is None and p["last_completed_attempt"] is None
        assert p["repairing_response"] is False
        new = diagnostics.make_diagnostic_callback(changed, attempt=1, operation="brainstorm")
        new({"event": "request.start", "request_id": "new-request"})
        new({"event": "cli.model_output", "model_output_chars": 17})
        assert progress(changed)["cumulative_content_chars"] == 17


def test_upgrades_legacy_sidecar_without_erasing_received_content(tmp_path):
    s = session(tmp_path)
    first = diagnostics.make_diagnostic_callback(s, attempt=1, operation="brainstorm")
    first({"event": "cli.model_output", "model_output_chars": 123})
    path = s["_diagnostic_dir"] / "7-brainstorm.progress.json"
    old = json.loads(path.read_text())
    for key in ("cumulative_content_chars", "last_turn_content_at", "last_completed_attempt", "repairing_response"):
        old.pop(key, None)
    path.write_text(json.dumps(old))
    assert progress(s)["cumulative_content_chars"] == 123
    second = diagnostics.make_diagnostic_callback(s, attempt=2, operation="brainstorm")
    second({"event": "attempt.start"})
    p = progress(s)
    assert p["cumulative_content_chars"] == 123
    assert p["last_turn_content_at"] == old["last_content_at"]
    assert p["content_chars"] == 0


def test_format_repair_and_out_of_order_upstream_batches(tmp_path):
    s = session(tmp_path)
    cb = diagnostics.make_diagnostic_callback(s, attempt=1, operation="brainstorm")
    cb({"event": "attempt.failed", "error_category": "skeleton_contract"})
    cb({"event": "format_retry"})
    assert progress(s)["phase"] == "repairing"
    cb({"event": "upstream.event", "upstream_event": "codex.sse_event",
        "event_kind": "response.in_progress", "source_timestamp": "2026-10-08T07:05:00Z"})
    cb({"event": "upstream.event", "upstream_event": "codex.sse_event",
        "event_kind": "response.queued", "source_timestamp": "2026-10-08T07:01:00Z"})
    p = progress(s)
    assert p["last_upstream_event_at"] == "2026-10-08T07:05:00Z"
    assert p["upstream_event"] == "response.in_progress"
    assert p["last_content_at"] is None


def test_wrong_job_revision_and_unsafe_sidecar_are_not_exposed(tmp_path):
    s = session(tmp_path)
    cb = diagnostics.make_diagnostic_callback(s, attempt=1, operation="brainstorm")
    cb({"event": "request.start", "request_id": "private-request"})
    changed = {**s, "job_id": "new-job"}
    assert progress(changed)["request_id"] is None
    changed["revision"] = 8
    assert progress(changed)["request_id"] is None
    other = {**s, "id": "b" * 32}
    assert progress(other)["request_id"] is None
    path = s["_diagnostic_dir"] / "7-brainstorm.progress.json"
    path.unlink()
    target = tmp_path / "private.json"
    target.write_text('{"secret":"PRIVATE"}')
    path.symlink_to(target)
    cb({"event": "heartbeat"})
    assert target.read_text() == '{"secret":"PRIVATE"}'
    assert "PRIVATE" not in json.dumps(progress(s))


def test_progress_survives_log_cap_and_fourth_delivery_repair_attempt(tmp_path, monkeypatch):
    s = session(tmp_path)
    monkeypatch.setattr(diagnostics, "MAX_FILE_BYTES", 8192)
    monkeypatch.setattr(diagnostics, "TERMINAL_RESERVE_BYTES", 2048)
    monkeypatch.setattr(diagnostics, "MAX_RECORD_BYTES", 1024)
    cb = diagnostics.make_diagnostic_callback(s, attempt=4, operation="brainstorm")
    cb({"event": "request.start", "request_id": "fourth"})
    for _ in range(100):
        cb({"event": "heartbeat"})
    cb({"event": "cli.model_output", "model_output_chars": 800})
    p = progress(s)
    assert p["attempt"] == 4 and p["content_chars"] == 800
    assert p["last_content_at"] is not None
    assert (s["_diagnostic_dir"] / "7-brainstorm.jsonl").stat().st_size <= 8192


def test_api_progress_is_projected_without_mutating_session_or_restarting_work(client, monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()
    def complete(s, _skill):
        cb = diagnostics.make_diagnostic_callback(s, attempt=1, operation="brainstorm")
        cb({"event": "request.start", "request_id": "test-request"})
        cb({"event": "cli_event", "last_cli_event": "turn.started"})
        cb({"event": "heartbeat"})
        entered.set()
        assert release.wait(5)
        return model_result()
    monkeypatch.setattr(workshop, "_complete", complete)
    s = create(client)
    url = f'/api/workshop/sessions/{s["id"]}'
    client.post(url + '/turns', json={"message": "Explore", "revision": s["revision"]})
    try:
        assert entered.wait(2)
        first = client.get(url).json()
        second = client.get(url).json()
        assert first["runtime_progress"] == second["runtime_progress"]
        assert first["runtime_progress"]["phase"] == "waiting"
        assert first["runtime_progress"]["last_content_at"] is None
        saved = json.loads((tmp_path / "workshop_v2" / s["id"] / "session.json").read_text())
        assert "runtime_progress" not in saved
        assert len(saved["messages"]) == 1
    finally:
        release.set()
    result = finish(client, s["id"])
    assert result.get("runtime_progress") is None
