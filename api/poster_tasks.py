"""Owner-scoped durable poster work. Reconnection never implies another model call."""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone

from core.cover_sources import canonical_hash
from core.cover_store import CoverConflict

_PROCESS = uuid.uuid4().hex
_ACTIVE = {"queued", "running"}
_ID = re.compile(r"^poster-task-[0-9a-f]{32}$")


def now():
    return datetime.now(timezone.utc).isoformat()


def error_message(exc):
    # Provider exceptions occasionally contain request URLs or authorization
    # headers. Keep diagnostic categories and useful byte counts, not secrets.
    from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

    value = str(exc)
    secret_names = {"key", "token", "apikey", "accesstoken", "refreshtoken", "authorization", "auth",
                    "password", "passwd", "clientsecret", "secret", "credential", "credentials",
                    "signature", "sig", "xamzcredential", "xamzsecuritytoken", "xamzsignature"}

    def redact_url(match):
        try:
            url = urlsplit(match[0])
            authority = "[redacted]@" + url.netloc.rsplit("@", 1)[1] if "@" in url.netloc else url.netloc
            query = []
            for key, content in parse_qsl(url.query, keep_blank_values=True):
                name = re.sub(r"[-_\s]", "", unquote(key).lower())
                query.append((key, "[redacted]" if name in secret_names else content))
            return urlunsplit((url.scheme, authority, url.path, urlencode(query), url.fragment))
        except ValueError:
            return "[redacted-url]"

    value = re.sub(r"https?://[^\s\"'<>]+", redact_url, value, flags=re.IGNORECASE)
    keys = (r"(?:x[-_])?(?:api[-_]?key|auth[-_]?token)|access[-_]?token|refresh[-_]?token|"
            r"authorization|password|passwd|client[-_]?secret|secret|token|(?:set[-_])?cookie")
    # Match both JSON and Python-repr dictionaries, including quoted values
    # containing spaces or escaped quotes. Do not confuse input_tokens with a
    # credential or trust a nonstandard provider key to have an sk- prefix.
    credential = (r"(?i)(?<![\w-])([\"']?(?:" + keys + r")[\"']?\s*[:=]\s*)"
                  r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|\[redacted\]|"
                  r"(?:(?:Bearer|Basic|Digest)\s+)?[^\s,;&}\]]+)")
    value = re.sub(credential, r"\1[redacted]", value)
    value = re.sub(r"(?i)(bearer\s+)[^\s,;\"'<>]+", r"\1[redacted]", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[redacted]", value)
    details = getattr(exc, "details", {})
    if not isinstance(details, dict):
        details = {}
    budget = [f"{key}={details[key]}" for key in ("actual_bytes", "reserved_bytes", "limit_bytes", "input_tokens", "reserved_tokens", "limit_tokens")
              if type(details.get(key)) is int]
    return (value[:3300] + (" [" + ", ".join(budget) + "]" if budget else ""))[:4000]


def _path(flow, task_id):
    if not _ID.fullmatch(task_id):
        raise ValueError("Invalid poster task id")
    root = flow.store.root / "poster" / "tasks"
    if any(p.is_symlink() for p in (flow.store.root, root.parent, root)):
        raise ValueError("Invalid poster task directory")
    path = root / f"{task_id}.json"
    if path.is_symlink():
        raise ValueError("Invalid poster task path")
    return path


def read(flow, task_id):
    record = json.loads(_path(flow, task_id).read_text("utf-8"))
    if record.get("task_id") != task_id or record.get("owner_scope") != flow.context.owner_scope:
        raise FileNotFoundError("Poster task does not belong to this owner")
    return record


def write(flow, record):
    flow.store._write_json(_path(flow, record["task_id"]), record)


def records(flow):
    root = _path(flow, "poster-task-" + "0" * 32).parent
    result = [read(flow, path.stem) for path in root.glob("poster-task-*.json")]
    return sorted(result, key=lambda row: (row["created_at"], row["task_id"]), reverse=True)


def project(record, runner):
    result = dict(record)
    if result["status"] in _ACTIVE:
        job = runner.get(result.get("job_id", "")) if result.get("process_id") == _PROCESS else None
        if not (job and job.get("status") == "running"
                and (job.get("poster_task_id") or job.get("meta", {}).get("poster_task_id")) == result["task_id"]):
            result.update(status="interrupted", error="执行已中断，远端结果可能未知。已保存图片会保留；请检查后手动重试，不会自动追加调用。")
    return {key: value for key, value in result.items()
            if key not in {"owner_scope", "process_id", "job_id", "intent_hash"}}


def list_tasks(flow, runner):
    return [project(row, runner) for row in records(flow)]


def start(flow, runner, *, kind, request_id, inputs, preflight, operation, project_id):
    """Reserve intent atomically, then run outside the lock. Same key is forever once.

    A second tab with the same active intent is aliased to the first receipt;
    different active work receives a conflict, never an unrelated success.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,120}", request_id):
        raise ValueError("request_id must contain 8–120 URL-safe characters")
    intent = canonical_hash({"kind": kind, "input": inputs})
    with flow.store.mutation_lock():
        previous = records(flow)
        for row in previous:
            if request_id in [row["request_id"], *row.get("request_aliases", [])]:
                if row["intent_hash"] != intent:
                    raise CoverConflict("该请求标识已用于不同操作，请刷新后明确提交新操作。")
                return project(row, runner)
        for row in previous:
            state = project(row, runner)
            if state["status"] in _ACTIVE:
                if row["intent_hash"] != intent:
                    raise CoverConflict("封面任务正在执行，请等待完成后再提交新操作。")
                row.setdefault("request_aliases", []).append(request_id)
                write(flow, row)
                return project(row, runner)
        from core.usage_costs import load_pricing
        pricing = load_pricing()
        # Validate local configuration before allocating an immutable source or
        # batch. Preflight never calls models.
        prepared = preflight()
        stamp = now()
        row = {"schema_version": 1, "task_id": f"poster-task-{uuid.uuid4().hex}",
               "owner_scope": flow.context.owner_scope, "process_id": _PROCESS,
               "request_id": request_id, "request_aliases": [], "intent_hash": intent,
               "kind": kind, "input": inputs, "status": "queued", "stage": "queued",
               "created_at": stamp, "updated_at": stamp, "finished_at": None,
               "completed": 0, "total": 4 if kind in {"generate", "plan"} else 1,
               "error": None, "result": dict(prepared.get("result") or {}), "job_id": "",
               "pricing_snapshot": pricing}
        write(flow, row)

        def progress(event):
            allowed = {"stage", "completed", "total", "result", "last_model_event", "completed_model_calls"}
            with flow.store.mutation_lock():
                current = read(flow, row["task_id"])
                if current["status"] != "running" or current.get("process_id") != _PROCESS:
                    return
                if event.get("model_event"):
                    from .workshop_diagnostics import _safe_event
                    diagnostic = _safe_event(event["model_event"])
                    if diagnostic:
                        current["last_model_event"] = diagnostic
                        current["last_model_event_at"] = now()
                        if diagnostic["event"] in {"upstream.event", "upstream.activity"}:
                            current["last_upstream_event"] = {**diagnostic, "received_at": now()}
                        if diagnostic["event"] in {"cli.model_output", "request.completed"}:
                            current["last_content_event"] = {**diagnostic, "received_at": now()}
                        folder = _path(flow, row["task_id"]).parent / row["task_id"]
                        if folder.is_symlink():
                            raise ValueError("Invalid diagnostics directory")
                        folder.mkdir(exist_ok=True)
                        log = folder / "events.jsonl"
                        if log.is_symlink():
                            raise ValueError("Invalid diagnostics path")
                        if not log.exists() or log.stat().st_size < 1024 * 1024:
                            fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                            with os.fdopen(fd, "a") as stream:
                                stream.write(json.dumps(diagnostic, ensure_ascii=False) + "\n")
                checkpoint = event.get("response_checkpoint")
                if isinstance(checkpoint, dict) and type(checkpoint.get("call_index")) is int:
                    index = checkpoint["call_index"]
                    if 1 <= index <= 2 and isinstance(checkpoint.get("raw"), str):
                        folder = _path(flow, row["task_id"]).parent / row["task_id"]
                        if folder.is_symlink():
                            raise ValueError("Invalid response checkpoint directory")
                        flow.store._write_json(folder / f"response-{index}.json", checkpoint)
                if event.get("phase"):
                    current["stage"] = event["phase"]
                if event.get("result_cover_set_id"):
                    current["result"] = {**current["result"], "cover_set_id": event["result_cover_set_id"]}
                if event.get("result_candidate_id"):
                    current["result"]["candidate_id"] = event["result_candidate_id"]
                current.update({key: value for key, value in event.items() if key in allowed})
                current["updated_at"] = now()
                write(flow, current)

        def execute():
            with flow.store.mutation_lock():
                current = read(flow, row["task_id"])
                current.update(status="running", stage="starting", updated_at=now())
                write(flow, current)
            try:
                from core.usage_tracking import pricing_scope, usage_activity
                with pricing_scope(pricing), usage_activity(kind):
                    result = operation(prepared, progress)
                with flow.store.mutation_lock():
                    current = read(flow, row["task_id"])
                    current.update(status="complete", stage="complete", result=result,
                                   updated_at=now(), finished_at=now())
                    write(flow, current)
                return result
            except Exception as exc:
                with flow.store.mutation_lock():
                    current = read(flow, row["task_id"])
                    # Never discard a previously checkpointed cover_set_id.
                    current.update(status="failed", error=error_message(exc), updated_at=now(), finished_at=now())
                    write(flow, current)
                raise

        try:
            row["job_id"] = runner.submit("cover.poster." + kind, execute,
                meta={"project_id": project_id, "project_path": str(flow.store.project_path),
                      "poster_task_id": row["task_id"]}, unique_key=row["task_id"],
                result_mapper=lambda result: result)
        except Exception as exc:
            row.update(status="failed", error=error_message(exc), updated_at=now(), finished_at=now())
            write(flow, row)
            raise
        write(flow, row)
        return project(row, runner)
