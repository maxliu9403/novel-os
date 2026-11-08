"""Content-free source-study observations, separate from durable result checkpoints.

Worker heartbeats prove local liveness only. Model content, transport activity and
saved steps are deliberately distinct; no synthetic token count or percentage.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from .reference_assets import atomic_json


def timestamp():
    return datetime.now(UTC).isoformat()


class StudyProgress:
    def __init__(self, root: Path, attempt_id: str, *, heartbeat_seconds=5):
        self.path = root / "progress.json"
        self.interval = heartbeat_seconds
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.last_write = 0.0
        self.value = {
            "attempt_id": attempt_id,
            "phase": "preparing",
            "received_chars": 0,
        }
        self.thread = None

    def _flush(self):
        # Telemetry failure must never invalidate received model work.
        try:
            atomic_json(self.path, self.value)
            self.last_write = time.monotonic()
        except OSError:
            pass

    def update(self, **values):
        with self.lock:
            self.value.update(values)
            self._flush()

    def __enter__(self):
        self.update(heartbeat_at=timestamp())

        def pulse():
            while not self.stopped.wait(self.interval):
                self.update(heartbeat_at=timestamp())

        self.thread = threading.Thread(target=pulse, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stopped.set()
        self.thread.join()
        self.update(heartbeat_at=timestamp(), phase="finished")

    def request(self, *, index: int, payload: dict, input_bytes: int):
        self.update(
            step=index + 1,
            phase="submitting",
            request_started_at=timestamp(),
            request_id=None,
            request_closed=False,
            received_chars=0,
            input_bytes=input_bytes,
            source_files=len({s["path"] for s in payload.get("segments", [])}),
            last_content_at=None,
            last_transport_at=None,
            last_activity_at=None,
            activity_kind=None,
            input_sent_at=None,
        )

    def observe(self, event):
        if not isinstance(event, dict) or self.stopped.is_set():
            return
        kind = event.get("event")
        with self.lock:
            if not self.value.get("request_started_at"):
                return
            if kind == "request.start":
                request_id = event.get("request_id")
                self.value["request_id"] = (
                    request_id
                    if isinstance(request_id, str) and len(request_id) <= 128
                    else None
                )
            elif (
                self.value.get("request_id")
                and event.get("request_id") != self.value["request_id"]
            ):
                return
            if self.value.get("request_closed"):
                return
            at = timestamp()
            if kind == "stdin_sent":
                self.value.update(input_sent_at=at, phase="waiting")
            if kind in ("cli.model_output", "content.progress", "request.completed"):
                count = event.get("model_output_chars", event.get("output_chars"))
                if (
                    type(count) is int
                    and 0 <= count <= 10_000_000
                    and count > self.value["received_chars"]
                ):
                    self.value.update(
                        received_chars=count, last_content_at=at, phase="receiving"
                    )
            if kind == "upstream.activity":
                activity = event.get("activity_kind")
                # These are observations of event arrival, not proof of continuous reasoning.
                if activity == "transport":
                    self.value["last_transport_at"] = at
                elif activity in ("text", "reasoning_summary"):
                    self.value.update(last_activity_at=at, activity_kind=activity)
            if kind in (
                "request.completed",
                "request.failed",
                "request.cancelled",
                "request.timeout",
            ):
                self.value.update(
                    request_closed=True,
                    phase="validating" if kind == "request.completed" else "stopped",
                )
            if (
                kind
                in (
                    "request.start",
                    "stdin_sent",
                    "request.completed",
                    "request.failed",
                    "request.cancelled",
                    "request.timeout",
                )
                or time.monotonic() - self.last_write >= 1
            ):
                self._flush()


def read_progress(root: Path, attempt_id: str | None):
    try:
        value = json.loads((root / "progress.json").read_text())
        return (
            value
            if isinstance(value, dict)
            and attempt_id
            and value.get("attempt_id") == attempt_id
            else {}
        )
    except (OSError, ValueError):
        return {}
