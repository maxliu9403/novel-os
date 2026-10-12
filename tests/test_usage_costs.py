from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest
from fastapi.testclient import TestClient

from core.usage_tracking import normalize_usage, read_records, record_usage, usage_scope
from core.usage_costs import costs_payload, default_pricing, diagnostic_records, owner_records, summarize, validate_pricing


def event(**changes):
    return {"request_id": "r", "stage": "cover", "provider": "codex", "model": "gpt-5.6-sol",
            "kind": "text", "status": "completed", "usage": {"input_tokens": 1000,
            "cached_input_tokens": 250, "output_tokens": 200, "reasoning_tokens": 50, "total_tokens": 1200}, **changes}


def test_cached_and_reasoning_are_subsets_not_added_to_totals():
    usage = normalize_usage({"prompt_tokens": 1000, "completion_tokens": 200,
        "prompt_tokens_details": {"cached_tokens": 250}, "completion_tokens_details": {"reasoning_tokens": 50}})
    result = summarize([event(usage=usage)], default_pricing())
    assert result["total_tokens"] == 1200
    assert result["estimated_cost_usd"] == pytest.approx(.0071)
    assert result["estimated_cost_cny"] == pytest.approx(.0497)
    assert result["cost_complete"]


def test_partial_usage_and_missing_price_remain_explicitly_incomplete():
    partial = event(usage={"input_tokens": 1000, "cached_input_tokens": 0})
    result = summarize([partial, event(model="unpriced", usage={})], default_pricing())
    assert result["estimated_cost_usd"] == .004
    assert result["unknown_usage_requests"] == 2
    assert not result["cost_complete"]
    unknown = summarize([event(model="unpriced")], default_pricing())
    assert unknown["estimated_cost_usd"] is None
    assert unknown["estimated_cost_cny"] is None
    assert not summarize([], default_pricing())["cost_complete"]


def test_missing_cache_rate_keeps_known_uncached_subtotal():
    pricing = default_pricing()
    pricing["models"][0]["cached_input_usd_per_million"] = None
    result = summarize([event()], pricing)
    assert result["estimated_cost_usd"] == .007
    assert not result["cost_complete"]


@pytest.mark.parametrize("relative,project", [("diagnostics", False),
    ("outputs/covers/analyses", True), ("outputs/covers/sets", True)])
def test_historical_intermediate_symlink_cannot_read_another_owner(tmp_path, relative, project):
    owner, foreign = tmp_path / "owner", tmp_path / "foreign"
    outside = foreign / relative
    outside.mkdir(parents=True)
    if relative == "diagnostics":
        (outside / "1.jsonl").write_text(json.dumps({"request_id": "foreign", "usage": {"input_tokens": 15}}))
    elif relative.endswith("analyses"):
        (outside / "analysis-1").mkdir()
        (outside / "analysis-1/analysis.json").write_text(json.dumps({"attempts": [{"attempt_id": "foreign"}]}))
    else:
        (outside / "cover-1.json").write_text(json.dumps({"candidates": [{"attempt_history": [{"attempt_id": "foreign", "created_at": "2020", "image_sha256": "hash"}]}]}))
    linked = owner / relative
    linked.parent.mkdir(parents=True)
    linked.symlink_to(outside, target_is_directory=True)
    assert owner_records(owner, project=project) == []


def test_image_estimates_never_charge_host_text_tokens_as_image_model_tokens():
    pricing = default_pricing()
    pricing["models"].append({"provider": "codex", "model": "gpt-image-2", "image_usd_per_image": .2})
    image = event(model="gpt-image-2", kind="image", usage={}, image_count=1)
    host = event(model="unknown", usage={"input_tokens": 80, "output_tokens": 20, "total_tokens": 100})
    result = summarize([image, host], pricing)
    assert result["images"] == 1 and result["total_tokens"] == 100
    assert result["estimated_cost_usd"] == .2
    assert not result["cost_complete"]


@pytest.mark.parametrize("invalid", [-1, True, float("nan"), float("inf")])
def test_invalid_prices_are_rejected(invalid):
    p = default_pricing()
    p["models"][0]["output_usd_per_million"] = invalid
    with pytest.raises(ValueError):
        validate_pricing(p)


def test_concurrent_duplicate_observations_and_owner_isolation(tmp_path):
    def emit(owner, number):
        with usage_scope(tmp_path / owner, "cover"):
            record_usage(provider="codex", model="gpt-5.6-sol", request_id="shared-request",
                         usage={"input_tokens": number, "cached_input_tokens": 0, "output_tokens": 5})
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: emit("a", 100), range(20)))
        list(pool.map(lambda _: emit("b", 900), range(20)))
    assert len(read_records(tmp_path / "a")) == 1
    assert read_records(tmp_path / "a")["shared-request"]["usage"]["input_tokens"] == 100
    assert read_records(tmp_path / "b")["shared-request"]["usage"]["input_tokens"] == 900


def test_started_request_is_durable_before_provider_finishes(tmp_path):
    from core.usage_tracking import usage_request
    with usage_scope(tmp_path, "cover"), usage_request("codex", "model") as request:
        assert read_records(tmp_path)[request.request_id]["status"] == "started"
        assert read_records(tmp_path)[request.request_id]["usage"] == {}
    assert len(read_records(tmp_path)) == 1
    assert read_records(tmp_path)[request.request_id]["status"] == "completed"


def test_diagnostic_heartbeats_deduplicate_and_live_observation_wins(tmp_path):
    path = tmp_path / "diagnostics/1-brainstorm.jsonl"
    path.parent.mkdir()
    base = {"request_id": "same", "provider": "codex", "model": "gpt-5.6-sol"}
    rows = [{**base, "event": "request.start"}, *[
        {**base, "event": "heartbeat", "usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 10}}
        for _ in range(4)], {**base, "event": "upstream.event", "input_token_count": 100}]
    path.write_text("\n".join(json.dumps(r) for r in rows))
    assert len(diagnostic_records(tmp_path)) == 1
    with usage_scope(tmp_path, "brainstorming"):
        record_usage(provider="codex", model="gpt-5.6-sol", request_id="same",
                     usage={"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 10})
    report = costs_payload(owner_records(tmp_path), default_pricing())
    assert report["totals"]["request_count"] == 1
    assert report["totals"]["total_tokens"] == 110


def fake_client(monkeypatch, *, usage, fail=False, cancel_thread=False):
    from core.llm_client import LLMClient
    monkeypatch.setenv("NOVEL_OS_LLM_STREAMING", "0")
    client = object.__new__(LLMClient)
    client.provider_name, client.model, client.max_tokens = "openai", "gpt-5.6-sol", 100
    client.cancellation_event = threading.Event() if cancel_thread else None
    def create(**kwargs):
        if fail:
            raise TimeoutError("fake timeout")
        return SimpleNamespace(usage=usage, model=None, choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])
    client._backend = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return client


def test_http_usage_survives_cancellation_worker_context_and_counts_retries(tmp_path, monkeypatch):
    usage = {"prompt_tokens": 25, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 0}}
    client = fake_client(monkeypatch, usage=usage, cancel_thread=True)
    with usage_scope(tmp_path, "manuscript"):
        assert client.complete("private prompt", "private text") == "ok"
        failed = fake_client(monkeypatch, usage=None, fail=True)
        with pytest.raises(TimeoutError):
            failed.complete("private prompt", "private text")
        assert client.complete("private prompt", "private text") == "ok"
    rows = list(read_records(tmp_path).values())
    assert len(rows) == 3
    report = summarize(rows, default_pricing())
    assert report["total_tokens"] == 60
    assert report["unknown_usage_requests"] == 1
    assert "private" not in "".join(p.read_text() for p in (tmp_path / "outputs/usage").glob("*.json"))


def test_shared_client_concurrent_requests_do_not_cross_contaminate_owners(tmp_path, monkeypatch):
    client = fake_client(monkeypatch, usage={"prompt_tokens": 2, "completion_tokens": 3})
    def run(name):
        with usage_scope(tmp_path / name, "cover"):
            return client.complete("", "")
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run, ("a", "b"))) == ["ok", "ok"]
    a, b = read_records(tmp_path / "a"), read_records(tmp_path / "b")
    assert len(a) == len(b) == 1 and set(a).isdisjoint(b)


def test_usage_directory_symlink_cannot_write_outside_owner(tmp_path):
    owner, outside = tmp_path / "owner", tmp_path / "outside"
    (owner / "outputs").mkdir(parents=True)
    outside.mkdir()
    (owner / "outputs/usage").symlink_to(outside)
    with usage_scope(owner, "cover"):
        record_usage(provider="codex", model="x", request_id="r", usage={})
    assert list(outside.iterdir()) == []


def test_api_reads_linked_workshop_once_and_does_not_cross_workspace(tmp_path, monkeypatch):
    from api.main import create_app
    from api.routes import get_service
    from api.services import ProjectService
    from api.workshop import WorkshopService
    from api import db
    monkeypatch.setenv("NOVEL_OS_PRICING_PATH", str(tmp_path / "pricing.json"))
    root = tmp_path / "projects"
    client = TestClient(create_app(projects_root=root, db_url=f"sqlite:///{tmp_path / 'db.sqlite'}"))
    project = client.post("/api/projects", json={"title": "Book", "genre": "Drama"}).json()["id"]
    svc = ProjectService(root)
    workshop = WorkshopService(svc)
    session_id = "a" * 32
    folder = workshop._path(session_id)
    folder.mkdir(parents=True)
    (folder / "session.json").write_text(json.dumps({"id": session_id, "project_id": project, "status": "launched"}))
    with usage_scope(folder, "brainstorming"):
        record_usage(provider="codex", model="gpt-5.6-sol", request_id="r",
                     usage={"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 20})
    first = client.get(f"/api/projects/{project}/costs")
    assert first.status_code == 200, first.text
    assert first.json()["totals"]["total_tokens"] == 30
    assert client.get(f"/api/projects/{project}/costs").json() == first.json()
    ws = db.workspace_create(id="other", name="Other", slug="other")
    client.app.dependency_overrides[get_service] = lambda: ProjectService(root, workspace=ws)
    assert client.get(f"/api/projects/{project}/costs").status_code == 404
    assert client.get(f"/api/workshop/sessions/{session_id}/costs").status_code == 404


def test_pricing_roundtrip_and_unknown_exchange_rate(tmp_path, monkeypatch):
    from api.main import create_app
    monkeypatch.setenv("NOVEL_OS_PRICING_PATH", str(tmp_path / "pricing.json"))
    client = TestClient(create_app(projects_root=tmp_path / "projects", db_url=f"sqlite:///{tmp_path / 'db.sqlite'}"))
    value = client.get("/api/studio/pricing").json()
    assert value["exchange_rate_status"] == "assumed"
    value["usd_to_cny"] = None
    result = client.put("/api/studio/pricing", json=value)
    assert result.status_code == 200
    assert client.get("/api/studio/pricing").json()["exchange_rate_status"] == "unset"
    assert summarize([event()], result.json())["estimated_cost_cny"] is None


def test_sdk_internal_retry_is_two_real_attempts_without_double_usage(tmp_path, monkeypatch):
    import httpx
    from openai import OpenAI
    from core.llm_client import LLMClient
    count = []
    def respond(request):
        count.append(request)
        if len(count) == 1:
            return httpx.Response(429, json={"error": {"message": "retry", "type": "rate_limit"}}, headers={"retry-after-ms": "1"})
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 0,
            "model": "gpt-5.6-sol", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
                      "prompt_tokens_details": {"cached_tokens": 0}}})
    backend = OpenAI(api_key="test-not-real", max_retries=1,
                     http_client=httpx.Client(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(LLMClient, "_build_backend", lambda *a: (backend, "gpt-5.6-sol"))
    monkeypatch.setenv("NOVEL_OS_LLM_STREAMING", "0")
    client = LLMClient(provider="openai", model="gpt-5.6-sol")
    with usage_scope(tmp_path, "manuscript"):
        assert client.complete("", "") == "ok"
    rows = list(read_records(tmp_path).values())
    assert len(count) == len(rows) == 2
    assert sum(r["usage"].get("total_tokens", 0) for r in rows) == 15
    assert {r["status"] for r in rows} == {"failed", "completed"}


def test_cancelled_worker_preserves_late_actual_usage(tmp_path, monkeypatch):
    from core.llm_client import LLMCancelledError
    import time
    client = fake_client(monkeypatch, usage=None, cancel_thread=True)
    started, release = threading.Event(), threading.Event()
    def create(**kwargs):
        started.set()
        assert release.wait(3)
        return SimpleNamespace(usage={"prompt_tokens": 17, "completion_tokens": 8}, model=None,
                               choices=[SimpleNamespace(message=SimpleNamespace(content="late"))])
    client._backend.chat.completions.create = create
    def run():
        with usage_scope(tmp_path, "cover"):
            with pytest.raises(LLMCancelledError):
                client.complete("", "")
    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(3)
    client.cancellation_event.set()
    thread.join(3)
    assert not thread.is_alive()
    assert list(read_records(tmp_path).values())[0]["usage"] == {}
    release.set()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        row = list(read_records(tmp_path).values())[0]
        if row["usage"].get("total_tokens") == 25:
            break
        time.sleep(.01)
    assert row["status"] == "failed" and row["usage"]["total_tokens"] == 25


def test_cli_multiple_turns_sum_once_and_history_does_not_sum_heartbeats(tmp_path):
    from core.llm_diagnostics import CLIDiagnostics
    rows = []
    observer = CLIDiagnostics(lambda row: rows.append({**row, "request_id": "multi", "provider": "codex", "model": "gpt-5.6-sol"}))
    lines = [
        {"type": "turn.started"},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 3}},
        {"type": "turn.started"},
        {"type": "turn.completed", "usage": {"input_tokens": 20, "cached_input_tokens": 4, "output_tokens": 7}},
    ]
    output = "\n".join(json.dumps(row) for row in lines) + "\n"
    observer.observe(output, None)
    observer.observe(output, None)
    terminal = observer.finish("cli.completed")
    assert terminal["usage"] == {"input_tokens": 30, "cached_input_tokens": 6, "output_tokens": 10}
    directory = tmp_path / "diagnostics"
    directory.mkdir()
    (directory / "1-brainstorm.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
    records = diagnostic_records(tmp_path)
    assert records["multi"]["usage"]["total_tokens"] == 40


def test_usage_component_coverage_distinguishes_real_zero_from_missing():
    result = summarize([event(usage={"input_tokens": 0, "output_tokens": 2})], default_pricing())
    assert result["input_tokens"] == result["cached_input_tokens"] == 0
    assert result["usage_coverage"]["input_tokens"] == 1
    assert result["usage_coverage"]["cached_input_tokens"] == 0
    assert result["usage_coverage"]["reasoning_tokens"] == 0


def test_multiturn_partial_usage_keeps_observed_sums_without_claiming_completeness(tmp_path):
    from core.llm_diagnostics import CLIDiagnostics
    from core.usage_tracking import observe_usage, usage_request
    history = []
    def receive(row):
        history.append({**row, "request_id": "multi", "provider": "codex", "model": "gpt-5.6-sol"})
        observe_usage(row.get("usage"), provider="codex", replace=True,
                      incomplete_fields=row.get("usage_incomplete_fields", ()))
    with usage_scope(tmp_path, "brainstorming"), usage_request("codex", "gpt-5.6-sol"):
        observer = CLIDiagnostics(receive)
        lines = [
            {"type": "turn.started"},
            {"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 3}},
            {"type": "turn.started"},
            {"type": "turn.completed", "usage": {"input_tokens": 20}},
        ]
        observer.observe("\n".join(json.dumps(row) for row in lines) + "\n", None)
        observer.finish("cli.completed")
    record = next(iter(read_records(tmp_path).values()))
    assert record["usage"]["input_tokens"] == 30
    assert record["usage"]["output_tokens"] == 3
    assert record["usage"]["cached_input_tokens"] == 2
    assert set(record["usage_incomplete_fields"]) == {"output_tokens", "cached_input_tokens", "total_tokens"}
    summary = summarize([record], default_pricing())
    assert summary["known_usage_requests"] == 0
    assert summary["total_tokens"] == 33
    assert not summary["cost_complete"]
    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    (diagnostics / "1.jsonl").write_text("\n".join(json.dumps(row) for row in history))
    assert diagnostic_records(tmp_path)["multi"]["usage_incomplete_fields"] == record["usage_incomplete_fields"]


def test_standalone_and_package_imports_share_usage_scope(tmp_path):
    import os
    import subprocess
    import sys
    core = Path(__file__).resolve().parents[1] / "core"
    code = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import llm_client, pipeline_runner, usage_tracking
sys.path.insert(0, str(Path(sys.argv[1]).parent))
from core import usage_tracking as packaged
assert packaged is usage_tracking
with usage_tracking.usage_scope(sys.argv[2], 'cover'):
    assert packaged.tracking_enabled()
"""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    subprocess.run([sys.executable, "-c", code, str(core), str(tmp_path / "owner")],
                   cwd=tmp_path, env=env, check=True, capture_output=True, text=True)


def test_analysis_worker_scope_and_durable_attempt_ids_prevent_historical_double_count(tmp_path, monkeypatch):
    from core.cover_analysis_execution import AnalysisCall, execute_calls
    client = fake_client(monkeypatch, usage={"prompt_tokens": 2, "completion_tokens": 3})
    # Keep the real LLMClient accounting boundary while supplying valid JSON.
    client._backend.chat.completions.create = lambda **kwargs: SimpleNamespace(
        usage={"prompt_tokens": 2, "completion_tokens": 3}, model=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))])
    calls = [AnalysisCall(str(i), "segment", {}, lambda value: value) for i in range(3)]
    attempts = [{"attempt_id": "frozen-" + call.key, "status": "completed"} for call in calls]
    with usage_scope(tmp_path, "cover"):
        results = list(execute_calls(calls, max_workers=3, max_repairs=0, complete=client.complete,
            complete_factory=None, begin=lambda call, request: (attempts[int(call.key)], "system", "user"),
            finish=lambda *args: None, cancel_event=None, cancellation_error=RuntimeError,
            is_terminal_error=lambda error: True))
    path = tmp_path / "outputs/covers/analyses/original/analysis.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"attempts": attempts, "configuration": {"provider": "openai", "model": "gpt-5.6-sol"}}))
    assert len(results) == 3
    rows = owner_records(tmp_path, project=True)
    assert len(rows) == 3
    assert {row["request_id"] for row in rows} == {attempt["attempt_id"] for attempt in attempts}
    assert summarize(rows, default_pricing())["total_tokens"] == 15
