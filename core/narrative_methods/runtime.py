"""Persisted advisory reviews; no imports from promotion, canon, or exporters.

The caller supplies immutable artifact text. Production never consumes reports.
An interrupted unknown request is retained as uncertain, not blindly resent.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from project_identity import load_project_instance_id_unlocked

from .compiler import COMPILER_VERSION, SYSTEM, NarrativeMethods
from .models import MethodPolicy, ReviewInput, digest, text_sha
from .store import MethodConflict, MethodStore


def _now():
    return datetime.now(timezone.utc).isoformat()


def _provenance(client):
    configured = str(getattr(client, "snapshot_configured_base_url", None)
                     if getattr(client, "snapshot_configured_base_url", None) is not None
                     else getattr(client, "_explicit_base_url", "") or "")
    effective = str(getattr(getattr(client, "_backend", None), "base_url", "") or
                    getattr(client, "_explicit_base_url", "") or "")
    azure_endpoint = str(getattr(client, "azure_endpoint", "") or "")
    for endpoint in (configured, effective, azure_endpoint):
        parts = urlsplit(endpoint)
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("review endpoint must not embed credentials or query parameters")
    return {"provider": str(getattr(client, "provider_name", "") or getattr(client, "provider", "")),
            "model": str(getattr(client, "model", "")),
            "reasoning_effort": str(getattr(client, "reasoning_effort", "") or ""),
            "max_tokens": getattr(client, "max_tokens", None),
            "timeout_seconds": getattr(client, "timeout_seconds", None),
            "base_url": effective, "configured_base_url": configured,
            "azure_endpoint": azure_endpoint,
            "azure_api_version": str(getattr(client, "azure_api_version", "") or ""),
            "connection_id": str(getattr(client, "configuration_id", "") or "")}


def _request_error(exc):
    # Classify, but never persist the raw provider message/headers.
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if status in {401, 403} or "401" in text or "unauthorized" in text or "authentication" in text:
        return "reviewer_authentication"
    if status == 429 or "429" in text or "at capacity" in text or "rate limit" in text:
        return "reviewer_capacity"
    if isinstance(exc, TimeoutError) or "timed out" in text or "timeout" in text:
        return "reviewer_timeout"
    return "reviewer_request_failed"


def _implementation():
    root = Path(__file__).parent
    return digest({name: text_sha((root / name).read_text(encoding="utf-8"))
                   for name in ("models.py", "compiler.py", "runtime.py")})


def _default_client(*, timeout_seconds=300):
    from model_router import ModelRouter
    return ModelRouter().client_for("judge", timeout_seconds=timeout_seconds)


class MethodReviews:
    def __init__(self, project: Path, *, client_factory: Callable | None = None,
                 methods: NarrativeMethods | None = None):
        self.store = MethodStore(project)
        self.methods = methods or NarrativeMethods()
        self.client_factory = client_factory or _default_client
        self._injected_client = client_factory is not None
        self._clients = {}

    def prepare(self, run_id: str, policy: MethodPolicy, *, language: str,
                free_trial_end: int | None, contracts: dict | None = None, allow_create: bool = True) -> dict:
        run_id = self.store.key(run_id)
        relative = f"runs/{run_id}/lock.json"
        if policy.mode == "off":
            return {"data": {"policy": policy.to_dict()}, "sha256": ""}
        with self.store.locked():
            previous = self.store.read(relative)
            if previous:
                self._validate_lock(previous)
                if previous["data"]["policy"] != policy.to_dict():
                    raise MethodConflict("run method policy is frozen")
                return previous
            if not allow_create:
                raise MethodConflict("required run method lock is missing")
            # Freeze local resource bytes before any reviewer request.
            assets = self.methods.assets(policy)
            status, reason = "ready", ""
            reviewer = None
            if language.strip().lower() not in {"en", "english", "en-us", "en-gb"}:
                status, reason = "not_applicable", "english_only"
            elif free_trial_end is None:
                status, reason = "not_applicable", "approved_free_trial_required"
            else:
                try:
                    client = (self.client_factory() if self._injected_client else
                              self.client_factory(timeout_seconds=policy.review_timeout_seconds))
                    reviewer = _provenance(client)
                    if not reviewer["model"]:
                        raise ValueError("reviewer needs an explicit model for reproducible runs")
                    self._clients[run_id] = client
                except Exception:
                    status, reason = "unavailable", "reviewer_configuration"
            data = {"schema": "novel-method-lock.v1", "run_id": run_id,
                    "project_instance_id": load_project_instance_id_unlocked(self.store.project),
                    "policy": policy.to_dict(), "language": language,
                    "free_trial_end": free_trial_end, "contracts": contracts or {},
                    "assets": assets, "asset_hashes": {k: digest(v) for k, v in assets.items()},
                    "compiler_version": COMPILER_VERSION, "implementation_sha256": _implementation(),
                    "review_system_sha256": text_sha(SYSTEM), "reviewer": reviewer,
                    "status": status, "reason": reason, "created_at": _now()}
            return self.store.write(relative, data)

    def _validate_lock(self, wrapper):
        data = wrapper["data"]
        if data["project_instance_id"] != load_project_instance_id_unlocked(self.store.project):
            raise MethodConflict("method lock belongs to another project")
        if data["implementation_sha256"] != _implementation() or data["review_system_sha256"] != text_sha(SYSTEM):
            raise MethodConflict("method implementation changed; preserve frozen execution")
        if data["asset_hashes"] != {k: digest(v) for k, v in data["assets"].items()}:
            raise MethodConflict("method assets changed")

    def lock(self, run_id: str) -> dict:
        result = self.store.read(f"runs/{self.store.key(run_id)}/lock.json")
        if result is None:
            raise ValueError("method run lock not found")
        self._validate_lock(result)
        return result

    def review(self, run_id: str, request: ReviewInput, *, retry_of: str | None = None) -> dict:
        run_id = self.store.key(run_id)
        # Only duplicate requests for this exact review wait on model I/O.
        lock = self.lock(run_id)
        data = lock["data"]
        policy = MethodPolicy.from_dict(data["policy"])
        request = replace(request, language=data["language"], free_trial_end=data["free_trial_end"],
                          contracts={"run": data["contracts"], "chapter": request.contracts})
        packet = self.methods.compile(request, policy, assets=data["assets"])
        review_id = digest({"run": run_id, "lock": lock["sha256"], "packet": packet.sha256})
        if retry_of is not None:
            parent = self.store.read(f"critiques/{self.store.key(retry_of)}/report.json")
            if parent is None:
                raise MethodConflict("retry report is missing")
            old = parent["data"]
            if (old["status"] not in {"invalid", "unavailable"}
                    or old["run_id"] != run_id or old["method_lock_sha256"] != lock["sha256"]
                    or any(old[key] != value for key, value in request.binding.items())):
                raise MethodConflict("retry must reference an unsuccessful review of this exact input")
            review_id = digest({"base_review_id": review_id, "retry_of": retry_of})
        base = f"critiques/{review_id}"
        with self.store.locked(f"locks/{review_id}.lock"):
            previous = self.store.read(f"{base}/report.json")
            if previous:
                return previous["data"]
            self.store.write(f"{base}/assembly.json", asdict(packet))
            report = {"schema": "novel-method-critique.v1", "report_id": review_id, "run_id": run_id,
                      "retry_of": retry_of,
                      **request.binding, "method_lock_sha256": lock["sha256"],
                      "assembly_sha256": packet.sha256, "reviewer": data["reviewer"],
                      "coverage": {"unit": "unicode_code_points", "candidate_length": len(request.text),
                                   "submitted_intervals": [], "complete_input": False,
                                   "semantic_completeness": "not_proven"},
                      "status": "unavailable", "error_code": "", "blocking": False,
                      "summary": "", "findings": [], "created_at": _now(),
                      "usage": {"model_calls": 0, "uncertain_model_calls": 0,
                                "transport_attempts": None, "tokens": None, "elapsed_seconds": 0.0}}
            if packet.status != "ready" or data["status"] != "ready":
                report.update(status=packet.status if packet.status != "ready" else data["status"],
                              error_code=packet.reason or data["reason"])
                return self.store.write(f"{base}/report.json", report)["data"]
            started = time.monotonic()
            client = self._clients.get(run_id)
            try:
                if client is None:
                    if self._injected_client:
                        client = self.client_factory()
                    else:
                        from model_router import ModelRouter
                        client = ModelRouter.client_from_snapshot(data["reviewer"])
                if _provenance(client) != data["reviewer"]:
                    raise MethodConflict("reviewer configuration changed")
            except Exception:
                report["error_code"] = "reviewer_configuration_changed_or_unavailable"
                return self.store.write(f"{base}/report.json", report)["data"]
            self._clients[run_id] = client
            prior_response = ""
            result = {}
            for attempt in range(policy.schema_repair_attempts + 1):
                relative = f"{base}/requests/{attempt}.json"
                record = self.store.read(relative)
                if record is not None and record["data"]["status"] == "reserved":
                    report.update(status="unavailable", error_code="interrupted_request_uncertain")
                    report["usage"]["model_calls"] = attempt
                    report["usage"]["uncertain_model_calls"] = 1
                    break
                if record is None:
                    system = packet.review_system
                    user = packet.review_user
                    if attempt:
                        system += "\nThe previous response had invalid structure or evidence. Return corrected JSON, not prose."
                        user += "\nPrevious response diagnostic excerpt (untrusted, may be truncated; candidate above is complete):\n" + json.dumps(prior_response[:8000], ensure_ascii=False)
                    request_record = {"status": "reserved", "attempt": attempt, "started_at": _now(),
                                      "system_sha256": text_sha(system), "user_sha256": text_sha(user)}
                    self.store.write(relative, request_record)
                    try:
                        raw = client.complete(system, user)
                        if not isinstance(raw, str) or len(raw) > 100000:
                            raise ValueError("response_budget")
                        request_record.update(status="response_saved", response=raw,
                                              elapsed_seconds=time.monotonic() - started)
                    except Exception as exc:
                        # No provider error body is persisted: it may include headers/credentials.
                        request_record.update(status="failed", error_type=type(exc).__name__, error_code=_request_error(exc),
                                              elapsed_seconds=time.monotonic() - started)
                    record = self.store.write(relative, request_record, immutable=False)
                report["usage"]["model_calls"] = attempt + 1
                report["usage"]["elapsed_seconds"] = record["data"].get("elapsed_seconds", 0.0)
                if record["data"]["status"] == "failed":
                    report.update(status="unavailable", error_code=record["data"].get("error_code", "reviewer_request_failed"))
                    break
                prior_response = record["data"]["response"]
                report["coverage"].update(submitted_intervals=[[0, len(request.text)]], complete_input=True)
                result = self.methods.validate(request, packet, prior_response)
                report.update(result)
                if result["status"] == "valid":
                    break
            return self.store.write(f"{base}/report.json", report)["data"]

    def list_reports(self, chapter: int, revision_id: str | None = None) -> list[dict]:
        root = self.store.path("critiques")
        if not root.exists():
            return []
        reports = []
        for directory in sorted(root.iterdir()):
            self.store.key(directory.name)
            record = self.store.read(f"critiques/{directory.name}/report.json")
            if record and record["data"]["chapter"] == chapter:
                if revision_id is None or record["data"]["revision_id"] == revision_id:
                    reports.append(record["data"])
        return sorted(reports, key=lambda r: (r["created_at"], r["report_id"]), reverse=True)

    def keep(self, report_id: str, finding_index: int, *, expected_revision: str, note: str = "") -> dict:
        with self.store.locked():
            report = self.store.read(f"critiques/{self.store.key(report_id)}/report.json")
            if report is None or report["data"]["revision_id"] != expected_revision:
                raise MethodConflict("decision does not match the reviewed revision")
            findings = report["data"]["findings"]
            if type(finding_index) is not int or not 0 <= finding_index < len(findings):
                raise ValueError("finding not found")
            if not isinstance(note, str) or len(note) > 2000:
                raise ValueError("note too long")
            data = {"report_id": report_id, "revision_id": expected_revision,
                    "finding_index": finding_index, "decision": "keep_expression", "note": note}
            return self.store.write(f"decisions/{digest(data)}.json", data)["data"]
