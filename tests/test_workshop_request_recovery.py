"""A current physical request, never an unrelated past error, explains the page."""
from copy import deepcopy
import json

import pytest

from api import workshop_request_recovery as projection
from core.creative_types import digest


SAFE_MESSAGE = "模型工作区已停用（HTTP 402）；已有资料和进度已保留。"


def fixture(tmp_path, monkeypatch):
    request_id, attempt_id = "a" * 32, "b" * 32
    session = {"id": "session-1", "job_id": "job-1", "revision": 16, "status": "error",
               "last_error": "generic model failure", "logs": [{"event": "turn_failed", "provider": "codex",
                   "error_type": "LLMError", "message": "generic model failure"}]}
    terminal = {"event": "request.failed", "request_id": request_id, "error_type": "LLMError",
                "session_id": session["id"], "job_id": session["job_id"], "revision": session["revision"]}
    diagnostic = tmp_path / "diagnostics" / "16-brainstorm.jsonl"
    diagnostic.parent.mkdir()
    diagnostic.write_text(json.dumps(terminal) + "\n")
    system, user = "full original source", "full original instructions"
    identity = digest({"system": system, "user": user})
    group = tmp_path / "source-evidence" / "requests" / ("c" * 64)
    progress = group / "execution" / identity / "attempts" / attempt_id / "progress.json"
    progress.parent.mkdir(parents=True)
    progress.write_text(json.dumps({"event": "request.failed", "request_id": request_id}))
    receipt = group / f"{identity}.json"
    receipt.write_text(json.dumps({"system": system, "user": user, "state": "dispatched", "active_attempt_id": attempt_id}))
    calls = []

    def verified(request):
        # Keep the real immutable request integrity check while isolating the
        # terminal evidence parser, which has its own adapter/receipt tests.
        record, _ = request._read_snapshot()
        assert record["active_attempt_id"] == attempt_id
        calls.append(request.identity)
        return {"can_resume": True, "can_retry_unknown": False, "message": SAFE_MESSAGE,
                "execution": {"http_status": 402, "provider_code": "deactivated_workspace"}}
    monkeypatch.setattr(projection.SourceRequestSession, "recovery", verified)
    return session, diagnostic, progress, receipt, calls


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_current_failure_is_read_only_and_safe(tmp_path, monkeypatch):
    session, _, _, _, calls = fixture(tmp_path, monkeypatch)
    previous, snapshot = deepcopy(session), files(tmp_path)
    assert projection.failure_message(session, tmp_path) == SAFE_MESSAGE
    assert len(calls) == 1
    assert session == previous and files(tmp_path) == snapshot


@pytest.mark.parametrize("field,value", [("revision", 15), ("job_id", "old-job"),
    ("session_id", "another-session"), ("event", "request.completed"),
    ("error_type", "ValueError"), ("request_id", "d" * 32)])
def test_stale_or_different_diagnostic_cannot_replace_current_error(tmp_path, monkeypatch, field, value):
    session, diagnostic, _, _, calls = fixture(tmp_path, monkeypatch)
    data = json.loads(diagnostic.read_text())
    data[field] = value
    diagnostic.write_text(json.dumps(data))
    assert projection.failure_message(session, tmp_path) is None
    assert not calls


@pytest.mark.parametrize("field,value", [("error_type", "ModelResponseError"),
    ("event", "turn_completed"), ("provider", "openai"), ("message", "earlier failure")])
def test_business_failure_keeps_its_own_error(tmp_path, monkeypatch, field, value):
    session, _, _, _, calls = fixture(tmp_path, monkeypatch)
    session["logs"][-1][field] = value
    assert projection.failure_message(session, tmp_path) is None
    assert not calls


def test_new_attempt_or_changed_prompt_is_not_the_failed_request(tmp_path, monkeypatch):
    session, _, _, receipt, calls = fixture(tmp_path, monkeypatch)
    original = json.loads(receipt.read_text())
    receipt.write_text(json.dumps({**original, "active_attempt_id": "d" * 32}))
    assert projection.failure_message(session, tmp_path) is None
    receipt.write_text(json.dumps({**original, "user": "different input"}))
    assert projection.failure_message(session, tmp_path) is None
    assert not calls


def test_unknown_result_stays_unknown_and_completed_result_is_not_a_failure(tmp_path, monkeypatch):
    session, _, _, _, _ = fixture(tmp_path, monkeypatch)
    for recovery in (
        {"can_resume": False, "can_retry_unknown": True, "message": "unknown", "execution": {}},
        {"can_resume": True, "can_retry_unknown": False, "message": "complete", "execution": {}},
    ):
        monkeypatch.setattr(projection.SourceRequestSession, "recovery", lambda _, value=recovery: value)
        assert projection.failure_message(session, tmp_path) is None


def test_corrupt_or_linked_diagnostics_do_not_break_the_page(tmp_path, monkeypatch):
    session, diagnostic, _, _, calls = fixture(tmp_path, monkeypatch)
    diagnostic.write_text("invalid JSON")
    assert projection.failure_message(session, tmp_path) is None
    target = tmp_path / "unrelated.jsonl"
    diagnostic.rename(target)
    diagnostic.symlink_to(target)
    assert projection.failure_message(session, tmp_path) is None
    assert not calls


def test_session_response_projects_message_without_mutating_session(tmp_path, monkeypatch):
    from api import workshop
    session, _, _, _, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(workshop.workshop_development, "recovery_view", lambda *_: None)
    monkeypatch.setattr(workshop.workshop_development, "enabled", lambda *_: False)
    monkeypatch.setattr(workshop.workshop_next_step, "project", lambda *_: {"action": "resume"})
    previous = deepcopy(session)
    result = workshop._session_response(session, tmp_path)
    assert result["last_error"] == SAFE_MESSAGE
    assert session == previous
