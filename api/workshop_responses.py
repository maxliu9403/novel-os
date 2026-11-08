"""Private stage-response receipts, separate from public progress and checkpoints.

Only the final answer is retained, never transport headers or private reasoning.
Unvalidated receipts are diagnostic evidence, not story context or reusable stages.
The service supplies atomic persistence under its existing revision/job fence.
"""
from datetime import datetime, timezone
import hashlib
import traceback


MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class ResponseStorageError(RuntimeError):
    pass


class StageResponse:
    def __init__(self, session, *, attempt, stage, part, text, system, user):
        self.save = session.get("_save_delivery_response")
        self.record = {
            "schema_version": "workshop.stage-response.v1",
            "session_id": session["id"], "revision": session["revision"],
            "job_id": session.get("job_id"), "run_id": session.get("delivery_run_id"),
            "attempt": attempt, "stage": stage, "part": part,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "system_sha256": hashlib.sha256(system.encode()).hexdigest(),
            "user_sha256": hashlib.sha256(user.encode()).hexdigest(),
            "response_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "response_bytes": len(text.encode()), "raw": text,
            "validation": {"status": "pending"},
        }
        if self.record["response_bytes"] > MAX_RESPONSE_BYTES:
            raise ResponseStorageError("阶段回复超过 4 MiB 保存上限，未截断或继续处理。")
        self._persist()

    def _persist(self):
        if self.save is not None:
            try:
                self.save(self.record)
            except OSError as exc:
                raise ResponseStorageError("阶段原始回复未能安全保存，请检查存储；没有追加模型调用。") from exc

    def validated(self):
        self.record["validation"] = {"status": "valid"}
        self._persist()

    def failed(self, exc, *, phase, contract_error):
        validation = {"status": "invalid" if contract_error else "internal_error",
                      "phase": phase, "error_type": type(exc).__name__}
        if contract_error:
            validation["detail"] = str(exc)[:8000]
        # Frame names/line numbers help locate internal bugs without serializing
        # exception values, locals, prompts or provider data into metadata logs.
        validation["frames"] = [
            {"file": frame.filename.rsplit("/", 1)[-1], "function": frame.name, "line": frame.lineno}
            for frame in traceback.extract_tb(exc.__traceback__)[-12:]
        ]
        self.record["validation"] = validation
        self._persist()
