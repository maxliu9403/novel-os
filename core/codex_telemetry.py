"""Ephemeral loopback receiver for content-free Codex OpenTelemetry observations.

Only this invocation's exporter is redirected. Raw telemetry never reaches a
callback, disk or console. The single bounded receiver thread queues an explicit
metadata projection; the process polling thread delivers it to the log sink.
Codex exports asynchronously, so receipt time is not the upstream event time and
missing events cannot prove that a remote model stopped working.
"""
from __future__ import annotations

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import queue
import re
import socket
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

try:
    from .codex_metric_activity import MetricActivityProjector
except ImportError:
    from codex_metric_activity import MetricActivityProjector


_MAX_BODY_BYTES = 1024 * 1024
_MAX_BATCH_RECORDS = 256
_MAX_QUEUE_EVENTS = 512
_MAX_COUNTER = 2**63 - 1
_EVENTS = frozenset({
    "codex.conversation_starts", "codex.api_request", "codex.sse_event",
    "codex.websocket_request", "codex.websocket_event", "codex.turn_ttft",
})
_EVENT_KINDS = frozenset({
    "response.created", "response.queued", "response.in_progress",
    "response.completed", "response.failed", "response.incomplete",
    "response.cancelled", "response.output_item.added", "response.output_item.done",
    "response.content_part.added", "response.content_part.done",
    "response.output_text.delta", "response.output_text.done",
    "response.reasoning_summary_text.delta", "response.reasoning_summary_text.done",
    "response.reasoning_summary_part.added", "response.reasoning_summary_part.done",
    "response.reasoning_text.delta", "response.reasoning_text.done", "error",
})
_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"})
_SUMMARIES = frozenset({"none", "auto", "concise", "detailed"})
_COUNTERS = (
    "input_token_count", "output_token_count", "cached_token_count",
    "cache_write_token_count", "reasoning_token_count", "tool_token_count",
)
_INTEGER = re.compile(r"[0-9]{1,19}\Z")


def _scalar(value):
    if not isinstance(value, dict):
        return None
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        candidate = value.get(key)
        if type(candidate) in (str, int, float, bool):
            return candidate
    return None


def _integer(value, maximum=_MAX_COUNTER):
    if isinstance(value, str) and _INTEGER.fullmatch(value):
        value = int(value)
    return value if type(value) is int and 0 <= value <= maximum else None


def _number(value):
    if isinstance(value, str) and len(value) <= 32:
        try:
            value = float(value)
        except ValueError:
            return None
    if type(value) in (int, float) and 0 <= value <= _MAX_COUNTER and math.isfinite(value):
        return value
    return None


def _boolean(value):
    if type(value) is bool:
        return value
    if value in ("true", "false"):
        return value == "true"
    return None


def _timestamp(value):
    # Never echo an arbitrary string from telemetry (even under a known key).
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None or not 2000 <= stamp.year <= 2200:
            return None
        return stamp.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _endpoint(value):
    if not isinstance(value, str) or len(value) > 8192:
        return None
    try:
        path = urlsplit(value).path.lower().rstrip("/")
    except ValueError:
        return None
    # Keep only known route categories; URL hosts, IDs, queries and credentials
    # are discarded. A /models success is never reported as generation success.
    segments = path.split("/")
    for name in ("models", "responses", "usage", "instructions"):
        if name in segments:
            return name
    return "other"


def project_record(record: dict) -> dict | None:
    """Pure allowlist projection of a single OTLP JSON log record."""
    if not isinstance(record, dict):
        return None
    attributes = record.get("attributes")
    if not isinstance(attributes, list):
        return None
    # Do not inspect resource attributes, which can include account identities.
    attrs = {
        entry.get("key"): _scalar(entry.get("value"))
        for entry in attributes[:128]
        if isinstance(entry, dict) and isinstance(entry.get("key"), str)
    }
    event = attrs.get("event.name", _scalar(record.get("body")))
    if not isinstance(event, str) or event not in _EVENTS:
        return None
    result = {"event": "upstream.event", "upstream_event": event}
    kind = attrs.get("event.kind")
    if isinstance(kind, str) and kind in _EVENT_KINDS:
        result["event_kind"] = kind
    for source, target in (("reasoning_effort", "reasoning_effort"),
                           ("model_reasoning_effort", "reasoning_effort")):
        value = attrs.get(source)
        if isinstance(value, str) and value in _EFFORTS:
            result[target] = value
    summary = attrs.get("reasoning_summary")
    if isinstance(summary, str) and summary in _SUMMARIES:
        result["reasoning_summary"] = summary
    for key in _COUNTERS:
        value = _integer(attrs.get(key))
        if value is not None:
            result[key] = value
    for key in ("duration_ms", "ttft_ms"):
        value = _number(attrs.get(key))
        if value is not None:
            result[key] = value
    status = _integer(attrs.get("http.response.status_code"), 599)
    if status is not None and status >= 100:
        result["http_status"] = status
    attempt = _integer(attrs.get("attempt"), 10000)
    if attempt is not None:
        result["upstream_attempt"] = attempt
    success = _boolean(attrs.get("success"))
    if success is not None:
        result["success"] = success
    for key in ("endpoint", "url.full", "http.url"):
        category = _endpoint(attrs.get(key))
        if category is not None:
            result["endpoint_category"] = category
            break
    timestamp = _timestamp(attrs.get("event.timestamp", attrs.get("timestamp")))
    if timestamp is None:
        nanos = _integer(record.get("timeUnixNano"))
        if nanos is not None:
            try:
                timestamp = _timestamp(datetime.fromtimestamp(nanos / 1e9, timezone.utc).isoformat())
            except (ValueError, OSError, OverflowError):
                pass
    if timestamp is not None:
        result["source_timestamp"] = timestamp
    return result


def project_batch(payload):
    """Bound work per batch; unrecognized records are discarded, never logged."""
    if not isinstance(payload, dict):
        return
    resources = payload.get("resourceLogs")
    if not isinstance(resources, list):
        return
    visited = 0
    for resource in resources[:_MAX_BATCH_RECORDS]:
        scopes = resource.get("scopeLogs", []) if isinstance(resource, dict) else []
        if not isinstance(scopes, list):
            continue
        for scope in scopes[:_MAX_BATCH_RECORDS]:
            records = scope.get("logRecords", []) if isinstance(scope, dict) else []
            if not isinstance(records, list):
                continue
            for record in records:
                visited += 1
                if visited > _MAX_BATCH_RECORDS:
                    return
                safe = project_record(record)
                if safe is not None:
                    yield safe


class CodexTelemetry:
    """Best-effort one-call collector, with bounded memory and shutdown time."""

    def __init__(self, emit, *, enable_metrics=True):
        self._emit = emit
        self._events = queue.Queue(maxsize=_MAX_QUEUE_EVENTS)
        self._stop = threading.Event()
        self._started_at = time.monotonic()
        self._server = None
        self._thread = None
        self._closed = False
        self._dropped = 0
        self._connection_lock = threading.Lock()
        self._connection = None
        self._path = "/novel-os/" + uuid4().hex + "/v1/logs"
        self._metrics_path = self._path.removesuffix("logs") + "metrics"
        self.metrics_enabled = enable_metrics is True
        self._metrics = MetricActivityProjector() if self.metrics_enabled else None
        self.config_args: list[str] = []

    def _publish(self, record):
        try:
            self._emit(record)
        except Exception:
            pass

    def start(self):
        collector = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(0.3)
                super().setup()

            def log_message(self, *_):
                pass

            def do_POST(self):
                # Reject compressed/chunked payloads instead of parsing an
                # unbounded expansion; Codex's JSON exporter sends plain JSON.
                if self.path != collector._path and not (collector.metrics_enabled and self.path == collector._metrics_path):
                    self.send_error(404)
                    return
                size = _integer(self.headers.get("Content-Length"), _MAX_BODY_BYTES)
                if size is None or self.headers.get("Transfer-Encoding") or self.headers.get("Content-Encoding"):
                    self.send_error(413)
                    return
                try:
                    data = self.rfile.read(size)
                    if len(data) != size:
                        return
                    payload = json.loads(data)
                    observed_at = datetime.now(timezone.utc).isoformat()
                    records = (collector._metrics.project(payload, observed_at=observed_at)
                               if self.path == collector._metrics_path else project_batch(payload))
                    for safe in records:
                        safe["observed_seconds"] = round(time.monotonic() - collector._started_at, 3)
                        try:
                            collector._events.put_nowait(safe)
                        except queue.Full:
                            collector._dropped += 1
                except (ValueError, UnicodeError, RecursionError, OSError):
                    self.send_error(400)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        class Server(HTTPServer):
            # HTTPServer is deliberately single-threaded: one slow or malformed
            # client cannot create unbounded handler threads or keep us alive.
            request_queue_size = 4

            def get_request(self):
                connection, address = super().get_request()
                with collector._connection_lock:
                    if collector._stop.is_set():
                        connection.close()
                        raise OSError("collector closed")
                    collector._connection = connection
                return connection, address

            def close_request(self, request):
                with collector._connection_lock:
                    if collector._connection is request:
                        collector._connection = None
                super().close_request(request)

            def handle_error(self, *_):
                pass  # Base server would print raw exceptions to stderr.

        try:
            self._server = Server(("127.0.0.1", 0), Handler)
            self._server.timeout = 0.1
            endpoint = f"http://127.0.0.1:{self._server.server_port}{self._path}"
            metrics_endpoint = f"http://127.0.0.1:{self._server.server_port}{self._metrics_path}"
            self.config_args = [
                "--config", "otel.log_user_prompt=false",
            ]
            if self.metrics_enabled:
                self.config_args.extend([
                    "--config", 'otel.metrics_exporter={otlp-http={endpoint="' + metrics_endpoint + '",protocol="json"}}',
                ])
            self.config_args.extend([
                "--config", 'otel.exporter={otlp-http={endpoint="' + endpoint + '",protocol="json"}}',
            ])

            def receive():
                while not self._stop.is_set():
                    try:
                        self._server.handle_request()
                    except OSError:
                        break

            self._thread = threading.Thread(target=receive, daemon=True, name="codex-otel-local")
            self._thread.start()
        except Exception:
            self.config_args = []
            self.close()
            self._publish({"event": "upstream.observer_unavailable"})
        else:
            self._publish({"event": "upstream.observer_started"})
        return self

    def poll(self):
        # The caller, not the HTTP thread, invokes the external callback. A bad
        # log sink cannot block telemetry HTTP replies or leak receiver threads.
        for _ in range(_MAX_QUEUE_EVENTS):
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return
            self._publish(event)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        with self._connection_lock:
            if self._connection is not None:
                try:
                    self._connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        if self._thread is not None and self._thread.ident is not None:
            self._thread.join(timeout=0.6)
        if self._server is not None:
            try:
                self._server.server_close()
            except OSError:
                pass
        self.poll()
        if self._thread is not None:
            self._publish({"event": "upstream.observer_stopped", "dropped_event_count": self._dropped})
