"""OTLP counters describe observed public stream events, never token usage."""
import json

from core.codex_metric_activity import MetricActivityProjector


BASE = 1_791_770_000_000_000_000


def payload(count=1, *, kind="response.output_text.delta", start=0, end=1,
            temporality=1, metric="codex.websocket.event", success="true", **overrides):
    point = {"startTimeUnixNano": str(BASE + start * 10**9),
             "timeUnixNano": str(BASE + end * 10**9), "asInt": str(count),
             "attributes": [{"key": "kind", "value": {"stringValue": kind}},
                            {"key": "success", "value": {"stringValue": success}},
                            {"key": "model", "value": {"stringValue": "PRIVATE_MODEL"}}]}
    point.update(overrides)
    return {"resourceMetrics": [{"resource": {"attributes": [{"key": "email", "value": {
        "stringValue": "PRIVATE_ACCOUNT"}}]}, "scopeMetrics": [{"metrics": [{
            "name": metric, "sum": {"aggregationTemporality": temporality,
                                     "isMonotonic": True, "dataPoints": [point]}}]}]}]}


def project(projector, **args):
    return list(projector.project(payload(**args), observed_at="2026-10-12T10:00:00+00:00"))


def test_delta_counts_are_not_subtracted_and_duplicate_or_overlapping_windows_do_not_replay():
    projector = MetricActivityProjector()
    first = project(projector, count=4)
    assert first[0]["metric_count"] == 4
    assert first[0]["activity_kind"] == "text"
    assert first[0]["metric_temporality"] == "delta"
    assert first[0]["observed_at"] == "2026-10-12T10:00:00+00:00"
    assert first[0]["observation_source"] == "otel_metrics"
    assert "PRIVATE" not in json.dumps(first)
    assert project(projector, count=4) == []  # Export retry.
    assert project(projector, count=9, start=0, end=2) == []  # Cannot split overlap safely.
    assert project(projector, count=2, start=1, end=2)[0]["metric_count"] == 2
    assert project(projector, count=1, start=0, end=1) == []  # Late batch.
    assert project(projector, count=0, start=2, end=3) == []


def test_cumulative_counts_emit_only_new_events_and_track_explicit_counter_reset():
    projector = MetricActivityProjector()
    assert project(projector, count=4, temporality=2)[0]["metric_count"] == 4
    assert project(projector, count=7, end=2, temporality=2)[0]["metric_count"] == 3
    assert project(projector, count=7, end=3, temporality=2) == []
    assert project(projector, count=1, end=4, temporality=2) == []  # Same epoch regression.
    assert project(projector, count=2, start=4, end=5, temporality=2)[0]["metric_count"] == 2
    assert project(projector, count=100, end=6, temporality=2) == []  # Old epoch delayed.
    assert project(projector, count=3, start=4, end=6, temporality=2)[0]["metric_count"] == 1


def test_only_public_summary_and_body_deltas_are_model_activity():
    expected = {"response.reasoning_summary_text.delta": "reasoning_summary",
                "response.output_text.delta": "text", "response.created": "transport",
                "response.completed": "transport", "response.output_item.added": "transport"}
    for kind, activity in expected.items():
        rows = project(MetricActivityProjector(), kind=kind)
        assert rows[0]["activity_kind"] == activity
        assert rows[0]["event"] == "upstream.activity"
        assert "usage" not in rows[0] and "output_token_count" not in rows[0]
    for kind in ("PRIVATE_KIND", "response.reasoning_text.delta", "response.reasoning_text.done", "ping"):
        assert project(MetricActivityProjector(), kind=kind) == []
    for name in ("codex.api_request", "codex.websocket.request", "codex.sse_event", "PRIVATE_METRIC"):
        assert project(MetricActivityProjector(), metric=name) == []


def test_malformed_points_and_unbounded_series_are_rejected():
    for changes in ({"asInt": True}, {"asInt": "-1"}, {"asInt": "nan"},
                    {"asInt": "1.2"}, {"asInt": str(2**63)}, {"asInt": None},
                    {"startTimeUnixNano": None}, {"timeUnixNano": "PRIVATE_TIME"},
                    {"attributes": {}}, {"attributes": [None]}, {"flags": 1}):
        assert project(MetricActivityProjector(), **changes) == []
    assert project(MetricActivityProjector(), temporality=0) == []
    assert project(MetricActivityProjector(), success="false") == []
    projector = MetricActivityProjector(max_series=2)
    for index in range(8):
        value = payload()
        attrs = value["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["sum"]["dataPoints"][0]["attributes"]
        attrs[-1]["value"]["stringValue"] = str(index)
        rows = list(projector.project(value, observed_at="2026-10-12T10:00:00Z"))
        assert bool(rows) is (index < 2)
    assert len(projector._series) == 2
    assert project(projector) == []  # Full tracker does not evict/replay old series.


def test_batch_work_is_bounded_even_for_deep_lists_and_empty_nodes():
    projector = MetricActivityProjector()
    value = payload()
    resource = value["resourceMetrics"][0]
    value["resourceMetrics"] = [{"scopeMetrics": []}] * 300 + [resource]
    assert list(projector.project(value, observed_at="2026-10-12T10:00:00Z")) == []
