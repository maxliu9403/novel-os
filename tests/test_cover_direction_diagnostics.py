from __future__ import annotations

from dataclasses import replace
import json

import pytest

from core.cover_direction_diagnostics import CoverDirectionDiagnostics, DirectionDiagnosticError
from core.cover_store import CoverConflict
from tests.test_cover_analysis_snapshot import source
from tests.test_cover_workflow import workflow


def sink_for(root, **overrides):
    metadata = dict(owner_scope="default:project:book", analysis_id="analysis-fixture",
                    snapshot_id="snapshot-fixture", snapshot_sha256="a" * 64,
                    source_content_sha256="b" * 64, source_bundle_sha256="c" * 64,
                    analysis_sha256="d" * 64, model="fixture-director", count=4)
    return CoverDirectionDiagnostics(root, **{**metadata, **overrides})


def records(root):
    return [json.loads(p.read_text()) for p in sorted(root.glob("direction_attempts/*/[0-9]*.json"))]


def test_sink_is_lazy_and_preserves_exact_raw_response_and_allowlisted_metadata(tmp_path):
    root = tmp_path / "covers"
    sink = sink_for(root)
    assert not root.exists()
    raw = '  {"scene":"门口、两个孩子", "quote":"line\\nline"}\n'
    sink({"schema_version": 1, "stage": "draft_response", "attempt": 1,
          "raw_response": raw, "api_key": "secret-do-not-store", "configuration": {"token": "no"}})
    sink({"schema_version": 1, "stage": "draft_validation", "attempt": 1,
          "findings": [{"code": "interaction_evidence", "evidence": "edge-a"}]})
    sink({"schema_version": 1, "stage": "failure", "attempt": 1, "error": "invalid direction"})
    items = records(root)
    assert [x["sequence"] for x in items] == [1, 2, 3]
    assert [x["stage"] for x in items] == ["draft_response", "draft_validation", "failure"]
    assert items[0]["raw_response"] == raw
    manifest = json.loads(next(root.glob("direction_attempts/*/manifest.json")).read_text())
    assert manifest["model"] == "fixture-director" and manifest["count"] == 4
    assert manifest["analysis_id"] == "analysis-fixture"
    assert manifest["source_content_sha256"] == "b" * 64
    assert manifest["record_count"] == 3 and manifest["last_stage"] == "failure"
    assert "raw_response" not in manifest
    serialized = "".join(p.read_text() for p in root.rglob("*.json"))
    assert "secret-do-not-store" not in serialized and '"configuration"' not in serialized
    assert not list(root.rglob("*.tmp"))
    assert not (root / "directions").exists()


def test_record_survives_manifest_failure_and_sink_refuses_more_work(tmp_path, monkeypatch):
    import core.cover_direction_diagnostics as module
    root = tmp_path / "covers"
    sink = sink_for(root)
    real_write = module._atomic_json

    def broken_manifest(path, payload):
        if path.name == "manifest.json" and payload["record_count"]:
            raise OSError("disk full")
        return real_write(path, payload)

    monkeypatch.setattr(module, "_atomic_json", broken_manifest)
    with pytest.raises(DirectionDiagnosticError, match="diagnostic"):
        sink({"schema_version": 1, "stage": "draft_response", "attempt": 1, "raw_response": "received draft"})
    assert records(root)[0]["raw_response"] == "received draft"
    with pytest.raises(DirectionDiagnosticError):
        sink({"schema_version": 1, "stage": "semantic_response", "attempt": 2, "raw_response": "never continue"})
    assert len(records(root)) == 1


@pytest.mark.parametrize("event", [
    {"schema_version": 1, "stage": "unknown", "attempt": 1},
    {"schema_version": 1, "stage": "draft_response", "attempt": 0, "raw_response": "x"},
    {"schema_version": 1, "stage": "draft_response", "attempt": 1, "raw_response": {"wrong": "shape"}},
])
def test_invalid_event_never_creates_storage(tmp_path, event):
    with pytest.raises(ValueError):
        sink_for(tmp_path / "covers")(event)
    assert not (tmp_path / "covers").exists()


def test_failed_bound_workflow_archives_draft_without_creating_direction(workflow):
    flow, analysis, _director, _project = workflow

    class FailingDirector:
        model = "fixture-failed"

        def plan(self, brief, *, count, evidence_ledger, story_analysis, diagnostic_sink):
            diagnostic_sink({"schema_version": 1, "stage": "draft_response", "attempt": 1,
                             "raw_response": "raw failed direction"})
            diagnostic_sink({"schema_version": 1, "stage": "draft_validation", "attempt": 1,
                             "findings": [{"code": "campaign_interaction_evidence", "evidence": "edge-1"}]})
            raise ValueError("campaign failed review")

    with pytest.raises(ValueError, match="campaign failed review"):
        flow.plan(analysis.analysis_id, FailingDirector())
    saved = records(flow.store.root)
    assert [x["stage"] for x in saved] == ["draft_response", "draft_validation", "failure"]
    assert saved[-1]["error"] == "campaign failed review"
    assert not flow.store.directions_dir.exists()
    manifest = json.loads(next(flow.store.root.glob("direction_attempts/*/manifest.json")).read_text())
    assert manifest["owner_scope"] == flow.context.owner_scope
    assert manifest["analysis_id"] == analysis.analysis_id
    assert manifest["snapshot_id"] == analysis.snapshot_id


def test_bound_workflow_preserves_receipt_and_request_local_sinks(workflow, monkeypatch):
    flow, analysis, legacy, _project = workflow
    receipt = {"schema_version": 1, "source_sha256": "receipt-source", "edges": [{"id": "edge-1"}]}
    observed = []

    class Director:
        model = "fixture-success"

        def plan(self, brief, *, count, evidence_ledger, story_analysis, diagnostic_sink, source_snapshot):
            observed.append(diagnostic_sink)
            diagnostic_sink({"schema_version": 1, "stage": "draft_response", "attempt": 1,
                             "raw_response": "successful response"})
            result = legacy.plan(brief, count=count, evidence_ledger=evidence_ledger, story_analysis=story_analysis)
            diagnostic_sink({"schema_version": 1, "stage": "completed", "attempt": 1})
            return replace(result, source_binding={"campaign_evidence_review": receipt, "owner_scope": "untrusted-owner"})

    # This seam tests workflow transport of an opaque, director-owned receipt;
    # receipt validity is separately tested by the production validator.
    monkeypatch.setattr("core.cover_validator.validate_direction", lambda *a, **k: [])
    shared = Director()
    first = flow.plan(analysis.analysis_id, shared)
    second = flow.plan(analysis.analysis_id, shared)
    assert first.source_binding["campaign_evidence_review"] == receipt
    assert second.source_binding["owner_scope"] == flow.context.owner_scope
    receipt["edges"][0]["id"] = "later mutation"
    assert first.source_binding["campaign_evidence_review"]["edges"][0]["id"] == "edge-1"
    assert len(observed) == 2 and observed[0] is not observed[1]
    assert len(list(flow.store.root.glob("direction_attempts/*/manifest.json"))) == 2
    assert not hasattr(shared, "diagnostic_sink")


def test_legacy_director_signature_works_without_new_diagnostic_directory(workflow):
    flow, analysis, legacy, _project = workflow
    result = flow.plan(analysis.analysis_id, legacy)
    assert result.direction_id and result.status == "awaiting_approval"
    assert not (flow.store.root / "direction_attempts").exists()


def test_diagnostic_write_failure_stops_before_followup_model_call(workflow, monkeypatch):
    import core.cover_direction_diagnostics as module
    flow, analysis, _director, _project = workflow
    calls = []

    class Director:
        model = "fixture"

        def plan(self, brief, *, count, evidence_ledger, story_analysis, diagnostic_sink):
            calls.append("first expensive completion")
            diagnostic_sink({"schema_version": 1, "stage": "draft_response", "attempt": 1, "raw_response": "draft"})
            calls.append("second expensive completion")

    def disk_failure(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(module, "_atomic_json", disk_failure)
    with pytest.raises(DirectionDiagnosticError):
        flow.plan(analysis.analysis_id, Director())
    assert calls == ["first expensive completion"]
    assert not flow.store.directions_dir.exists()


@pytest.mark.parametrize("operation", ["approve", "generate"])
def test_bound_receipt_rechecked_before_approval_or_generation(workflow, monkeypatch, operation):
    from core.cover_campaign_evidence import build_evidence_claims, validate_evidence_reviews
    from tests.test_cover_workflow import approve
    flow, analysis, legacy, _project = workflow

    class ReviewedDirector:
        def plan(self, brief, *, count, evidence_ledger, story_analysis):
            result = legacy.plan(brief, count=count, evidence_ledger=evidence_ledger, story_analysis=story_analysis)
            reviews = [{"claim_id": claim["claim_id"], "verdict": "supported",
                        "reasoning": "The exact source supports this claim.",
                        "evidence_refs": claim["evidence_refs"]}
                       for claim in build_evidence_claims(result, story_analysis)]
            receipt, findings = validate_evidence_reviews(result, story_analysis, reviews)
            assert receipt and not findings
            return replace(result, source_binding={"campaign_evidence_review": receipt})

    direction = flow.plan(analysis.analysis_id, ReviewedDirector())
    if operation == "generate":
        direction = approve(flow, direction)
        flow.generation_arguments(direction.direction_id, direction.direction_sha256, reserve=False)
    corrupted = json.loads(json.dumps(direction.source_binding))
    corrupted["campaign_evidence_review"]["evidence_reviews"][0]["verdict"] = "uncertain"
    # Simulate a value reaching this trust boundary, independently of file and
    # direction digest checks; the receipt must enforce its own full contract.
    monkeypatch.setattr(flow.store, "require_latest_direction",
                        lambda _id: replace(direction, source_binding=corrupted))
    with pytest.raises(CoverConflict, match="campaign_evidence_review_invalid"):
        if operation == "approve":
            approve(flow, direction)
        else:
            flow.generation_arguments(direction.direction_id, direction.direction_sha256, reserve=False)


def test_workflow_failure_after_director_completed_is_durably_recorded(workflow, monkeypatch):
    flow, analysis, legacy, _project = workflow

    class Director:
        def plan(self, brief, *, count, evidence_ledger, story_analysis, diagnostic_sink):
            result = legacy.plan(brief, count=count, evidence_ledger=evidence_ledger, story_analysis=story_analysis)
            diagnostic_sink({"schema_version": 1, "stage": "completed", "attempt": 2})
            return result

    def cannot_persist(*args, **kwargs):
        raise OSError("direction store unavailable")

    monkeypatch.setattr(flow.store, "create_direction", cannot_persist)
    with pytest.raises(OSError, match="direction store unavailable"):
        flow.plan(analysis.analysis_id, Director())
    saved = records(flow.store.root)
    assert [x["stage"] for x in saved] == ["completed", "failure"]
    assert saved[-1]["attempt"] == 2


def test_director_terminal_failure_is_not_recorded_twice(workflow):
    flow, analysis, _legacy, _project = workflow

    class Director:
        def plan(self, brief, *, count, evidence_ledger, story_analysis, diagnostic_sink):
            diagnostic_sink({"schema_version": 1, "stage": "failure", "attempt": 3, "error": "already recorded"})
            raise ValueError("already recorded")

    with pytest.raises(ValueError, match="already recorded"):
        flow.plan(analysis.analysis_id, Director())
    assert len(records(flow.store.root)) == 1


@pytest.mark.parametrize("stage", ["draft_started", "semantic_started"])
def test_call_start_is_persisted_without_payload_before_model_work(tmp_path, stage):
    sink = sink_for(tmp_path / "covers")
    sink({"schema_version": 1, "stage": stage, "attempt": 2,
          "raw_response": "not received yet", "configuration": {"api_key": "do-not-record"}})
    saved = records(tmp_path / "covers")
    assert len(saved) == 1 and saved[0]["stage"] == stage and saved[0]["attempt"] == 2
    assert "raw_response" not in saved[0] and "configuration" not in saved[0]
    sink.record_failure("transport failed before response")
    assert records(tmp_path / "covers")[-1]["attempt"] == 2


def test_start_storage_failure_prevents_first_model_call(workflow, monkeypatch):
    import core.cover_direction_diagnostics as module
    flow, analysis, _director, _project = workflow
    calls = []
    visible_progress = []

    class Director:
        def plan(self, brief, *, count, evidence_ledger, story_analysis, diagnostic_sink):
            diagnostic_sink({"schema_version": 1, "stage": "draft_started", "attempt": 1})
            calls.append("first expensive completion")

    def disk_failure(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(module, "_atomic_json", disk_failure)
    with pytest.raises(DirectionDiagnosticError):
        flow.plan(analysis.analysis_id, Director(), progress_sink=visible_progress.append)
    assert calls == []
    assert visible_progress == []


def test_second_call_transport_failure_uses_started_attempt(workflow):
    flow, analysis, _director, _project = workflow

    class Director:
        def plan(self, brief, *, count, evidence_ledger, story_analysis, diagnostic_sink):
            diagnostic_sink({"schema_version": 1, "stage": "draft_started", "attempt": 1})
            diagnostic_sink({"schema_version": 1, "stage": "draft_response", "attempt": 1, "raw_response": "first draft"})
            diagnostic_sink({"schema_version": 1, "stage": "draft_started", "attempt": 2})
            raise ConnectionError("second call transport failed")

    with pytest.raises(ConnectionError, match="second call"):
        flow.plan(analysis.analysis_id, Director())
    saved = records(flow.store.root)
    assert [x["stage"] for x in saved] == ["draft_started", "draft_response", "draft_started", "failure"]
    assert saved[-1]["attempt"] == 2
