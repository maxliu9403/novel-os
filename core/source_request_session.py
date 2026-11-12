"""One execution/recovery boundary for source design and downstream evidence.

The immutable prompt and historical receipt formats remain authoritative. Each
physical attempt owns private durable output; unknown remote results are never
silently reissued. A user retry authorizes exactly one attempt of one receipt.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from .creative_types import digest
from .llm_attempt import completion_attempt, recover_completion, recover_terminal_failure
from .reference_assets import atomic_bytes, atomic_json
from .source_request_recovery import classify_failure, failure_message
from .source_routes import VERSION
from .source_study import lease, now


class RequestOutcomeUnknown(ValueError):
    pass


class RequestReceiptError(ValueError):
    pass


class SourceRequestSession:
    def __init__(self, path: Path, identity: str, *, kind="design"):
        if kind not in {"design", "evidence"} or not re.fullmatch(r"[a-f0-9]{64}", identity):
            raise ValueError("模型请求身份无效。")
        self.path, self.identity, self.kind = Path(path), identity, kind
        self._safe(self.path)
        self.directory = self.path.parent / "execution" / identity
        self.status_key = "status" if kind == "design" else "state"
        self.response_key = "raw" if kind == "design" else "response"

    @staticmethod
    def _safe(path):
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("模型请求路径异常。")
        return path

    def _read_snapshot(self):
        snapshot = self._safe(self.path).read_bytes()
        try:
            record = json.loads(snapshot)
        except (ValueError, UnicodeError) as exc:
            raise RequestReceiptError("保存的模型请求无法读取，未重新发送。") from exc
        if not isinstance(record, dict):
            raise RequestReceiptError("保存的模型请求格式无效，未重新发送。")
        actual = (digest([VERSION, record.get("system"), record.get("payload")])
                  if self.kind == "design" else digest({"system": record.get("system"), "user": record.get("user")}))
        if actual != self.identity or (self.kind == "design" and record.get("request_sha256") != self.identity):
            raise RequestReceiptError("保存的模型请求身份不匹配，未重新发送。")
        raw = record.get(self.response_key)
        if self.response_key in record and (not isinstance(raw, str) or record.get("response_sha256") != self._response_hash(raw)):
            raise RequestReceiptError("保存的模型响应完整性校验失败，未重发请求。")
        if (record.get(self.status_key) in {"response_received", "parsed", "received"}
                and self.response_key not in record and self._recovered(record) is None):
            raise RequestReceiptError("已完成请求的完整响应缺失，未重新发送。")
        return record, snapshot

    def _read(self):
        return self._read_snapshot()[0]

    def _response_hash(self, text):
        return digest(text) if self.kind == "design" else hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _write(self, record):
        atomic_json(self._safe(self.path), record)

    def _attempt_path(self, record):
        attempt_id = record.get("active_attempt_id")
        if attempt_id is None:
            return None
        if not isinstance(attempt_id, str) or not re.fullmatch(r"[a-f0-9]{32}", attempt_id):
            raise ValueError("执行记录身份无效。")
        return self._safe(self.directory / "attempts" / attempt_id)

    def _unknown(self, record):
        return record.get(self.status_key) in {"dispatched", "pending_unknown"}

    def _recovered(self, record):
        directory = self._attempt_path(record)
        return recover_completion(directory, self.identity) if directory else None

    def _terminal_failure(self, record):
        if not self._unknown(record):
            return None
        directory = self._attempt_path(record)
        proof = recover_terminal_failure(directory, self.identity) if directory else None
        if proof is None:
            return None
        from .llm_client import LLMHTTPError
        return classify_failure(LLMHTTPError(proof.status_code, provider_code=proof.provider_code))

    def _archive(self, snapshot):
        identity = hashlib.sha256(snapshot).hexdigest()
        history = self._safe(self.directory / 'history' / (identity + '.json'))
        if history.exists() and history.read_bytes() != snapshot:
            raise RequestReceiptError('历史请求完整性校验失败。')
        if not history.exists():
            atomic_bytes(history, snapshot)
        return identity

    def _receive(self, record, raw):
        if not isinstance(raw, str):
            raise ValueError("模型回复不是文本，完整请求已保留。")
        record.update({self.response_key: raw, "response_sha256": self._response_hash(raw),
                       self.status_key: "response_received" if self.kind == "design" else "received", "received_at": now()})
        if record.get("attempts"):
            if record.get("failure"):
                record["attempts"][-1].setdefault("failure", record["failure"])
            record["attempts"][-1]["outcome"] = {"outcome": "response_received"}
            if self.kind == "evidence":
                record["attempts"][-1]["state"] = "received"
        record.pop("failure", None)
        self._write(record)
        return raw

    def recovery(self):
        record, snapshot = self._read_snapshot()
        raw = record.get(self.response_key)
        local = self._recovered(record) if raw is None else raw
        terminal_failure = self._terminal_failure(record) if local is None else None
        unknown = self._unknown(record) and local is None and terminal_failure is None
        authorized = any(a.get("status") == "pending" for a in record.get("retry_authorizations", []))
        failure = (terminal_failure or record.get("failure") or (record.get("attempts") or [{}])[-1].get("failure", {})) if local is None else {}
        reason = failure.get("reason", "missing_terminal_evidence" if unknown else "resumable")
        message = failure_message(failure) if failure else (
            "旧请求未保存可确认的完整回复和详细失败原因；原资料与请求仍保留。" if unknown else "可从已保存的步骤继续。")
        if local is not None and raw is None:
            reason, message = "saved_completion", "完整回复已保存，继续时将直接复用，无需重复调用模型。"
        execution = {k: failure[k] for k in ("local_outcome", "timeout_scope", "timeout_seconds", "http_status", "provider_code") if k in failure}
        execution.update(reason=reason, message=message, attempt_count=len(record.get("attempts", [])))
        observed = record.get("observed_at") or record.get("received_at") or record.get("dispatched_at")
        if observed:
            execution["observed_at"] = observed
        return {"can_resume": not unknown or authorized, "can_retry_unknown": unknown and not authorized,
                "request_sha256": self.identity, "receipt_sha256": hashlib.sha256(snapshot).hexdigest(),
                "reason": reason, "message": message, "execution": execution}

    def retry_authorization(self, operation_id, receipt_sha256):
        """Recognize repeated HTTP delivery even while the worker holds its lease."""
        for authorization in self._read().get("retry_authorizations", []):
            if authorization.get("operation_id") == operation_id:
                if authorization.get("receipt_sha256") != receipt_sha256:
                    raise ValueError("同一操作标识不能用于不同请求版本。")
                return authorization.get("status")
        return None

    def authorize_retry(self, operation_id, receipt_sha256):
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 200:
            raise ValueError("重新执行操作标识无效。")
        with lease(self._safe(self.path.with_suffix(".execution.lock")), blocking=False):
            record = self._read()
            for authorization in record.get("retry_authorizations", []):
                if authorization.get("operation_id") == operation_id:
                    if authorization.get("receipt_sha256") != receipt_sha256:
                        raise ValueError("同一操作标识不能用于不同请求版本。")
                    return authorization.get("status") == "pending"
            snapshot = self.path.read_bytes()
            current = hashlib.sha256(snapshot).hexdigest()
            if receipt_sha256 != current:
                raise ValueError("请求状态已变化，请刷新后查看最新恢复操作。")
            if not self.recovery()["can_retry_unknown"]:
                raise ValueError("此请求不需要重新执行，请从现有进度继续。")
            history = self._safe(self.directory / "history" / (current + ".json"))
            if history.exists() and history.read_bytes() != snapshot:
                raise ValueError("历史请求完整性校验失败。")
            if not history.exists():
                atomic_bytes(history, snapshot)
            record.setdefault("retry_authorizations", []).append({"operation_id": operation_id,
                "receipt_sha256": current, "status": "pending", "authorized_at": now()})
            self._write(record)
            return True

    def execute(self, client, *, before_send=None, observer=None):
        with lease(self._safe(self.path.with_suffix(".execution.lock")), blocking=False):
            record, snapshot = self._read_snapshot()
            if record.get(self.response_key) is not None:
                return record[self.response_key]
            local = self._recovered(record)
            if local is not None:
                return self._receive(record, local)
            failure = self._terminal_failure(record)
            if failure is not None:
                archived = self._archive(snapshot)
                record.update({self.status_key: 'failed', 'failure': failure,
                               'terminal_failure_recovery': {'receipt_sha256': archived,
                                   'attempt_id': record.get('active_attempt_id'), 'observed_at': now()}})
                # Keep historical attempts exactly as originally recorded. The
                # verified projection is an additional receipt, not a rewrite.
                self._write(record)
            authorization = next((a for a in record.get("retry_authorizations", []) if a.get("status") == "pending"), None)
            if self._unknown(record) and not authorization:
                recover = getattr(client, "recover_response", None)
                if callable(recover):
                    try:
                        raw = recover(self.identity)
                    except Exception as exc:
                        raise RequestOutcomeUnknown("原请求结果暂时无法查询；资料和记录已保留。") from exc
                    if isinstance(raw, str):
                        return self._receive(record, raw)
                raise RequestOutcomeUnknown(self.recovery()["message"])
            if before_send:
                before_send()
            attempt_id = uuid4().hex
            model = {k: getattr(client, k) for k in ("provider", "provider_name", "model", "model_id", "connection_id", "route_id", "reasoning_effort", "timeout_seconds", "max_tokens", "planning_input_bytes")
                     if isinstance(getattr(client, k, None), (str, int, float, bool))}
            attempt = {"attempt_id": attempt_id, "model": model, "dispatched_at": now()}
            if self.kind == "evidence":
                attempt["state"] = "dispatched"
            record.setdefault("attempts", []).append(attempt)
            record.update({"active_attempt_id": attempt_id, "model": model,
                           self.status_key: "dispatched", "dispatched_at": attempt["dispatched_at"]})
            # Historical failures belong to their attempts, not the new one.
            record.pop("failure", None)
            if authorization:
                authorization.update(status="consumed", attempt_id=attempt_id, consumed_at=now())
                attempt["retry_operation_id"] = authorization["operation_id"]
            self._write(record)
            directory = self._attempt_path(record)
            try:
                with completion_attempt(directory, self.identity, observer=observer) as execution:
                    complete = getattr(client, "complete", client)
                    user = json.dumps(record["payload"], ensure_ascii=False) if self.kind == "design" else record["user"]
                    raw = complete(record["system"], user)
                    execution.save_response(raw)
            except Exception as exc:
                failure = classify_failure(exc)
                attempt["outcome"] = failure
                attempt["failure"] = failure
                if self.kind == "evidence":
                    attempt.update(state=failure["outcome"], failure=failure)
                state = failure["outcome"]
                if state == "unknown":
                    state = "pending_unknown" if self.kind == "design" else "dispatched"
                elif state == "not_sent" and self.kind == "evidence":
                    state = "prepared"
                record.update({self.status_key: state, "failure": failure, "observed_at": now()})
                self._write(record)
                raise
            return self._receive(record, raw)
