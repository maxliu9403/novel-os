"""Bounded OTLP counter projection for public Codex WebSocket activity.

Counts are stream events, not characters, tokens or proof of continuous work.
OTLP collection windows are kept separate from the local batch receipt time.
Only fingerprints of metric-series attributes survive projection; resource
attributes and content never leave the receiver.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import re


_LIMIT = 2**63 - 1
_INTEGER = re.compile(r"[0-9]{1,19}\Z")
_TRANSPORT = frozenset({
    "response.created", "response.queued", "response.in_progress", "response.completed",
    "response.failed", "response.incomplete", "response.cancelled", "response.output_item.added",
    "response.output_item.done", "response.content_part.added", "response.content_part.done",
    "response.output_text.done", "response.reasoning_summary_text.done",
    "response.reasoning_summary_part.added", "response.reasoning_summary_part.done", "error",
})
_CONTENT = {"response.output_text.delta": "text",
            "response.reasoning_summary_text.delta": "reasoning_summary"}


def _integer(value):
    if isinstance(value, str) and _INTEGER.fullmatch(value):
        value = int(value)
    return value if type(value) is int and 0 <= value <= _LIMIT else None


def _timestamp(nanos):
    try:
        stamp = datetime.fromtimestamp(nanos // 10**9, timezone.utc)
        if 2000 <= stamp.year <= 2200:
            return stamp.replace(microsecond=(nanos % 10**9) // 1000).isoformat()
    except (ValueError, OSError, OverflowError):
        pass
    return None


class MetricActivityProjector:
    """Track delta windows or cumulative epochs without replaying old counts.

    A collector belongs to one CLI invocation. Once its bounded series table
    fills, new series are dropped rather than evicting replay protection.
    """

    def __init__(self, max_series=256):
        self._max_series = max(1, min(max_series, 256))
        self._series = {}

    def _point(self, point, temporality, observed_at):
        if not isinstance(point, dict):
            return None
        attrs = point.get("attributes")
        if not isinstance(attrs, list) or not 1 <= len(attrs) <= 64:
            return None
        values = {}
        for attr in attrs:
            if not isinstance(attr, dict):
                return None
            key, value = attr.get("key"), attr.get("value")
            if not isinstance(key, str) or not 1 <= len(key) <= 128 or key in values or not isinstance(value, dict):
                return None
            if len(value) != 1:
                return None
            scalar = next(iter(value.values()))
            if (next(iter(value)) not in {"stringValue", "intValue", "doubleValue", "boolValue"}
                    or type(scalar) not in {str, int, float, bool}
                    or isinstance(scalar, str) and len(scalar) > 1024
                    or type(scalar) in {int, float} and (abs(scalar) > _LIMIT or not math.isfinite(scalar))):
                return None
            values[key] = scalar
        kind = values.get("kind")
        if not isinstance(kind, str) or kind not in _TRANSPORT | _CONTENT.keys():
            return None
        if values.get("success") is not True and values.get("success") != "true":
            return None
        count = _integer(point.get("asInt"))
        if count is None and "asInt" not in point:
            number = point.get("asDouble")
            if type(number) in {int, float} and 0 <= number <= _LIMIT and math.isfinite(number) and int(number) == number:
                count = int(number)
        flags = point.get("flags", 0)
        if type(flags) is not int or flags != 0:
            return None
        start, end = _integer(point.get("startTimeUnixNano")), _integer(point.get("timeUnixNano"))
        if count is None or start is None or end is None or start >= end:
            return None
        window_start, window_end = _timestamp(start), _timestamp(end)
        if window_start is None or window_end is None:
            return None
        # Do not retain arbitrary labels such as model names or account IDs.
        key = (temporality, hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).digest())
        previous = self._series.get(key)
        if previous is None:
            if len(self._series) >= self._max_series:
                return None
            delta = count
        else:
            prior_start, prior_end, prior_count = previous
            if end <= prior_end:
                return None
            if temporality == 1:
                if start < prior_end:
                    return None  # Overlapping delta windows cannot be split.
                delta = count
            elif start == prior_start:
                if count < prior_count:
                    return None  # A decrease without a new epoch is malformed.
                delta = count - prior_count
            elif start >= prior_end:
                delta = count  # Explicit new cumulative epoch (counter reset).
            else:
                return None  # Delayed old epoch or ambiguous overlapping reset.
        self._series[key] = (start, end, count)
        if not delta:
            return None
        return {"event": "upstream.activity", "activity_kind": _CONTENT.get(kind, "transport"),
                "metric_count": delta, "metric_temporality": "delta" if temporality == 1 else "cumulative",
                "observation_source": "otel_metrics", "observed_at": observed_at,
                "metric_window_start": window_start, "metric_window_end": window_end}

    def project(self, payload, *, observed_at):
        """Discard unknown metrics and bound traversal, including empty nodes."""
        if not isinstance(payload, dict):
            return
        try:
            observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            if observed.tzinfo is None or not 2000 <= observed.year <= 2200:
                return
        except (AttributeError, TypeError, ValueError):
            return
        budget = 256

        def items(parent, field):
            nonlocal budget
            values = parent.get(field) if isinstance(parent, dict) else None
            if isinstance(values, list):
                for value in values:
                    if budget <= 0:
                        return
                    budget -= 1
                    yield value

        for resource in items(payload, "resourceMetrics"):
            for scope in items(resource, "scopeMetrics"):
                for metric in items(scope, "metrics"):
                    if not isinstance(metric, dict) or metric.get("name") != "codex.websocket.event":
                        continue
                    total = metric.get("sum")
                    if not isinstance(total, dict) or total.get("isMonotonic") is not True:
                        continue
                    temporality = total.get("aggregationTemporality")
                    if type(temporality) is not int or temporality not in {1, 2}:
                        continue
                    for point in items(total, "dataPoints"):
                        result = self._point(point, temporality, observed_at)
                        if result is not None:
                            yield result
