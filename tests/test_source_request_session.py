"""Request recovery keeps historical receipts and never guesses remote failure."""
import hashlib
import json

import pytest

from core.creative_types import digest
from core.reference_assets import atomic_json
from core.source_routes import VERSION
from core.source_request_session import SourceRequestSession, RequestOutcomeUnknown


def session(tmp_path, status="prepared"):
    system, payload = "all source unchanged", {"source": "原文全文", "feedback": "keep ending"}
    key = digest([VERSION, system, payload])
    path = tmp_path / (key + ".json")
    atomic_json(path, {"request_sha256": key, "system": system, "payload": payload, "status": status})
    return SourceRequestSession(path, key, kind="design"), path


class Model:
    model = "current-model"
    def __init__(self, error=None):
        self.calls = []
        self.error = error
    def complete(self, system, user):
        self.calls.append((system, user))
        if self.error:
            raise self.error
        return '{"complete":true}'


def test_legacy_unknown_never_resends_and_reads_without_mutation(tmp_path):
    request, path = session(tmp_path, "pending_unknown")
    before = path.read_bytes()
    model = Model()
    with pytest.raises(RequestOutcomeUnknown):
        request.execute(model)
    assert not request.recovery()["can_resume"]
    assert request.recovery()["can_retry_unknown"]
    assert path.read_bytes() == before and model.calls == []


def test_explicit_retry_bound_to_receipt_is_idempotent_and_retains_original(tmp_path):
    request, path = session(tmp_path, "pending_unknown")
    original = path.read_bytes()
    token = request.recovery()["receipt_sha256"]
    assert request.authorize_retry("retry-1", token)
    assert request.authorize_retry("retry-1", token)
    with pytest.raises(ValueError):
        request.authorize_retry("another", token)
    model = Model(TimeoutError())
    with pytest.raises(TimeoutError):
        request.execute(model)
    assert not request.authorize_retry("retry-1", token)
    with pytest.raises(RequestOutcomeUnknown):
        request.execute(model)
    assert len(model.calls) == 1
    assert any(p.read_bytes() == original for p in tmp_path.rglob("*.json"))
    assert request.recovery()["execution"]["local_outcome"] == "timeout"


def test_complete_response_survives_business_receipt_write_crash(tmp_path, monkeypatch):
    import core.source_request_session as module
    request, path = session(tmp_path)
    write = module.atomic_json
    def crash(target, value):
        if target == path and value.get("raw") is not None:
            raise OSError("business checkpoint unavailable")
        return write(target, value)
    monkeypatch.setattr(module, "atomic_json", crash)
    model = Model()
    with pytest.raises(OSError):
        request.execute(model)
    monkeypatch.setattr(module, "atomic_json", write)
    assert request.recovery()["can_resume"]
    assert request.execute(model) == '{"complete":true}'
    assert len(model.calls) == 1
    record = json.loads(path.read_text())
    assert record["response_sha256"] == digest(record["raw"])


def test_corrupt_cached_reply_cannot_trigger_repurchase(tmp_path):
    request, path = session(tmp_path)
    record = json.loads(path.read_text())
    record.update(raw="changed", response_sha256=digest("original"), status="parsed")
    atomic_json(path, record)
    model = Model()
    with pytest.raises(ValueError):
        request.execute(model)
    assert not model.calls


def test_evidence_receipt_keeps_legacy_hash_and_full_input(tmp_path):
    system, user = "full system", "full source\n" * 1000
    identity = digest({"system": system, "user": user})
    path = tmp_path / (identity + ".json")
    atomic_json(path, {"system": system, "user": user, "state": "prepared"})
    request = SourceRequestSession(path, identity, kind="evidence")
    model = Model()
    result = request.execute(model)
    record = json.loads(path.read_text())
    assert record["response_sha256"] == hashlib.sha256(result.encode()).hexdigest()
    assert model.calls == [(system, user)]
    assert request.execute(model) == result and len(model.calls) == 1


def test_complete_response_query_retains_original_timeout_evidence(tmp_path):
    request, path = session(tmp_path)
    with pytest.raises(TimeoutError):
        request.execute(Model(TimeoutError()))
    model = Model()
    model.recover_response = lambda _: '{"restored":true}'
    assert request.execute(model) == '{"restored":true}'
    record = json.loads(path.read_text())
    assert record["attempts"][0]["failure"]["local_outcome"] == "timeout"
    assert not model.calls


def test_swapped_receipt_and_symlink_cannot_dispatch(tmp_path):
    request, path = session(tmp_path)
    record = json.loads(path.read_text())
    record["payload"]["source"] = "different book"
    atomic_json(path, record)
    model = Model()
    with pytest.raises(ValueError):
        request.execute(model)
    path.unlink()
    other = tmp_path / "other.json"
    atomic_json(other, record)
    path.symlink_to(other)
    with pytest.raises(ValueError):
        request.execute(model)
    assert not model.calls


@pytest.mark.parametrize("status", ["response_received", "parsed"])
def test_missing_completed_reply_cannot_trigger_repurchase(tmp_path, status):
    request, path = session(tmp_path, status)
    model = Model()
    with pytest.raises(ValueError, match="完整"):
        request.execute(model)
    assert not model.calls
