"""Transport activity and heartbeat stay distinct across refreshed task views."""
import json

from tests.test_poster_studio_api import studio, plan, overview  # noqa: F401
from tests.test_cover_poster_planner import response


def test_heartbeat_does_not_replace_last_upstream_evidence(studio):
    def complete(system, user):
        studio.planner.calls.append((system, json.loads(user)))
        callback = studio.planner.diagnostic_callback
        callback({"event": "upstream.activity", "activity_kind": "text", "metric_count": 3,
                  "metric_temporality": "delta", "observation_source": "otel_metrics",
                  "observed_at": "2026-10-16T10:00:00+00:00", "raw_prompt": "do not expose"})
        callback({"event": "heartbeat", "elapsed_seconds": 30})
        return json.dumps(response())
    studio.planner.complete = complete
    plan(studio)
    task = overview(studio)["tasks"][0]
    assert task["last_model_event"]["event"] == "heartbeat"
    assert task["last_model_event_at"]
    assert task["last_upstream_event"]["event"] == "upstream.activity"
    assert task["last_upstream_event"]["observed_at"] == "2026-10-16T10:00:00+00:00"
    assert task["last_upstream_event"]["received_at"]
    assert "raw_prompt" not in task["last_upstream_event"]
    assert task["completed_model_calls"] == 1
