"""Owner-local, append-only records for one direction planning attempt.

These records are diagnostics, never approved directions or publication inputs.
No provider configuration is accepted. Received text is saved before advancing
the manifest so a manifest failure can still leave a recoverable model reply.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any
from uuid import uuid4


_STAGES = {
    "draft_started", "draft_response", "draft_validation", "semantic_started", "semantic_response",
    "semantic_validation", "failure", "completed",
}


class DirectionDiagnosticError(RuntimeError):
    """Durable planning diagnostics failed; do not proceed to another call."""


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    content = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=".record-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class CoverDirectionDiagnostics:
    """A lazy callable sink, created separately for each bound plan request."""

    def __init__(
        self, cover_root: str | Path, *, owner_scope: str, analysis_id: str,
        snapshot_id: str, snapshot_sha256: str, source_content_sha256: str,
        source_bundle_sha256: str, analysis_sha256: str, model: str, count: int,
    ) -> None:
        self.attempt_id = uuid4().hex
        self.root = Path(cover_root) / "direction_attempts" / self.attempt_id
        self._metadata = {
            "schema_version": 1, "attempt_id": self.attempt_id,
            "owner_scope": owner_scope, "analysis_id": analysis_id,
            "snapshot_id": snapshot_id, "snapshot_sha256": snapshot_sha256,
            "source_content_sha256": source_content_sha256,
            "source_bundle_sha256": source_bundle_sha256,
            "analysis_sha256": analysis_sha256, "model": model, "count": count,
        }
        self._lock = threading.RLock()
        self._sequence = 0
        self._started_at: str | None = None
        self._failure: DirectionDiagnosticError | None = None
        self.last_stage: str | None = None
        self.last_attempt = 1

    def __call__(self, event: Mapping[str, Any]) -> None:
        with self._lock:
            if self._failure is not None:
                raise self._failure
            payload = self._event_payload(event)
            now = datetime.now(timezone.utc).isoformat()
            sequence = self._sequence + 1
            record = {
                **payload, "attempt_id": self.attempt_id,
                "sequence": sequence, "recorded_at": now,
            }
            try:
                if self._started_at is None:
                    # Never follow a substituted diagnostics directory outside
                    # this owner root. The fresh UUID directory must be new.
                    if self.root.parent.is_symlink():
                        raise OSError("Direction diagnostics directory must not be a symlink")
                    self.root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    self.root.mkdir(mode=0o700)
                    self._started_at = now
                _atomic_json(self.root / f"{sequence:06d}.json", record)
                self._sequence = sequence
                self.last_stage = payload["stage"]
                self.last_attempt = payload["attempt"]
                _atomic_json(self.root / "manifest.json", {
                    **self._metadata, "created_at": self._started_at,
                    "updated_at": now, "record_count": sequence,
                    "last_stage": self.last_stage, "last_attempt": self.last_attempt,
                })
            except (OSError, ValueError, TypeError) as exc:
                self._failure = DirectionDiagnosticError(
                    "Cover direction diagnostic storage failed; planning stopped before any further model call"
                )
                raise self._failure from exc

    def record_failure(self, error: str) -> None:
        """Capture workflow failures without duplicating the director terminal."""
        with self._lock:
            if self.last_stage != "failure":
                self({"schema_version": 1, "stage": "failure",
                      "attempt": self.last_attempt, "error": error})

    @staticmethod
    def _event_payload(event: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(event, Mapping) or event.get("schema_version") != 1:
            raise ValueError("Invalid direction diagnostic schema")
        stage = event.get("stage")
        attempt = event.get("attempt")
        if stage not in _STAGES or type(attempt) is not int or attempt < 1:
            raise ValueError("Invalid direction diagnostic stage or attempt")
        payload = {"schema_version": 1, "stage": stage, "attempt": attempt}
        if stage.endswith("_response"):
            if not isinstance(event.get("raw_response"), str):
                raise ValueError("Direction diagnostic response must be text")
            payload["raw_response"] = event["raw_response"]
        elif stage.endswith("_validation"):
            if not isinstance(event.get("findings"), list):
                raise ValueError("Direction diagnostic findings must be a list")
            payload["findings"] = event["findings"]
        elif stage == "failure":
            if not isinstance(event.get("error"), str):
                raise ValueError("Direction diagnostic failure must contain text")
            payload["error"] = event["error"]
        if "request_bytes" in event:
            if type(event["request_bytes"]) is not int or event["request_bytes"] <= 0:
                raise ValueError("Direction diagnostic request size must be positive")
            payload["request_bytes"] = event["request_bytes"]
        if "review_scope" in event:
            if not isinstance(event["review_scope"], Mapping):
                raise ValueError("Direction diagnostic review scope must be an object")
            payload["review_scope"] = dict(event["review_scope"])
        if "advisories" in event:
            if stage != "semantic_validation" or not isinstance(event["advisories"], list):
                raise ValueError("Direction diagnostic advisories require semantic validation")
            payload["advisories"] = event["advisories"]
        # JSON round-trip freezes mutable findings and rejects non-JSON values
        # before any directory or numbered record can be created.
        return json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
