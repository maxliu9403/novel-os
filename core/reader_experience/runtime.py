"""Durable advisory review. No manuscript mutations, automatic transport retries or GET writes."""
from __future__ import annotations
import os
import threading
import time
import uuid
from importlib.metadata import version
from pathlib import Path
from core.model_router import ModelRouter
from core.project_identity import load_project_instance_id_unlocked, ProjectIdentityNotInitialized
from core.usage_costs import load_pricing, summarize
from core.usage_tracking import read_records, usage_scope, usage_activity, pricing_scope, usage_request_identity
from .models import (ACTIVE, DIMENSIONS, VERSION, ReviewOptions, Reading, Report, Digest,
                     canonical, digest, parse_reply, text_sha)
from .sources import snapshot, units_for, resolve_quote, tokens
from .store import ReviewConflict, ReviewStore
from .prompts import system


class ReviewStopped(RuntimeError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def implementation():
    paths = [Path(__file__).with_name(name) for name in ("models.py", "sources.py", "runtime.py", "prompts.py", "store.py")]
    paths += [Path(__file__).parents[1] / "manuscript_reader.py", Path(__file__).parents[1] / "prose_sanitize.py"]
    return digest({p.name: text_sha(p.read_text()) for p in paths})


def identity(project, *, legacy=False):
    if legacy:
        stat = project.stat()
        return {"kind": "legacy_directory", "path": str(project.resolve()), "device": stat.st_dev, "inode": stat.st_ino}
    try:
        return {"kind": "project_instance", "id": load_project_instance_id_unlocked(project)}
    except ProjectIdentityNotInitialized:
        # Read-only historical identity; no legacy migration/lock creation.
        return identity(project, legacy=True)


def reference_cost(model, pricing, input_count, output_count):
    price = next((p for p in pricing["models"] if p["provider"] == model["provider"] and p["model"] == model["model"]), None)
    if not price or pricing.get("usd_to_cny") is None:
        return None
    a, b = price.get("input_usd_per_million"), price.get("output_usd_per_million")
    if a is None or b is None:
        return None
    return (a * input_count + b * output_count) * pricing["usd_to_cny"] / 1_000_000


class ReaderReviews:
    def __init__(self, project, project_id, *, client_factory=None, model_factory=None, clock=time.time):
        self.project, self.project_id = Path(project), project_id
        self.store = ReviewStore(self.project)
        self.clock = clock
        self.client_factory = client_factory or (lambda model, cancel: ModelRouter.client_from_snapshot(model, cancellation_event=cancel))
        self.model_factory = model_factory or (lambda: ModelRouter.public_snapshot(ModelRouter().client_for("judge", timeout_seconds=300)))

    def _prepared(self, options, *, mappings=True):
        source = snapshot(self.project, self.project_id, options, mappings=mappings)
        units = units_for(source, options.segment_tokens)
        model = self.model_factory()
        model = {**model, "max_tokens": options.output_tokens, "timeout_seconds": options.request_seconds}
        pricing = load_pricing()
        minimum = len(units) + 1
        if minimum > options.max_calls:
            raise ValueError(f"至少需要 {minimum} 次调用，当前上限 {options.max_calls}；请缩小范围或明确增加预算。")
        if options.input_tokens < options.segment_tokens + tokens(system("reading")) + 2500:
            raise ValueError("输入预算不足以容纳原文、评议协议与前文事实，请增加输入预算或缩小分段。")
        reservation = reference_cost(model, pricing, options.input_tokens, options.output_tokens)
        if options.max_estimated_cny is not None and reservation is None:
            raise ValueError("当前模型没有完整单价或汇率，无法启用人民币估算预算。请先设置价格，或只采用调用数/时长上限。")
        if options.max_estimated_cny is not None and minimum * reservation > options.max_estimated_cny:
            raise ValueError("人民币预算不足以预留最低阅读调用；请缩小范围或调整预算。")
        binding = {"source_sha256": source["source_sha256"], "options": options.model_dump(),
                   "model": model, "pricing": pricing, "implementation": implementation(),
                   "project_identity": identity(self.project), "schema": VERSION,
                   "title": source["title"], "public_genres": source["public_genres"],
                   "tokenizer": "cl100k_base", "tokenizer_version": version("tiktoken")}
        return source, units, binding

    def preview(self, options):
        source, units, binding = self._prepared(options, mappings=False)
        options_dict = options.model_dump()
        return {"proposal_sha256": digest(binding), "model": binding["model"], "options": options_dict,
                "versions": {version: sum(c["version"] == version for c in source["chapters"])
                             for version in ("final", "revised", "draft")},
                "minimum_calls_timeout_ceiling": (len(units) + 1) * options.request_seconds,
                "source_sha256": source["source_sha256"], "expected_chapters": source["expected_chapters"],
                "available_chapters": len(source["chapters"]), "missing_chapters": source["missing_chapters"],
                "complete_book": source["complete_book"], "units": len(units), "minimum_calls": len(units) + 1,
                "source_reference_tokens": sum(tokens(u["text"]) for u in units),
                "maximum_reserved_cny": reference_cost(binding["model"], binding["pricing"],
                      options.input_tokens * options.max_calls, options.output_tokens * options.max_calls),
                "notes": ["分段完整覆盖所选正文；缺章不会伪装成全书已完成。",
                          "cl100k_base 为参考分词，非所有模型的原生计费分词；预留输出和协议空间，不代表服务商窗口保证。",
                          "调用数包含应用发起的失败和格式更正；SDK 重试关闭，CLI 内部上游重试可能不可辨认。",
                          "人民币为单价估算预留，未知用量不释放预留；订阅/CLI 无法保证单次输出或实际扣费上限。",
                          "总时限从创建起计算，包含等待和暂停；恢复不会增加原预算。"]}

    def create(self, options, idempotency_key, proposal_sha256):
        key = self.store.key(idempotency_key)
        request_hash = digest({"options": options.model_dump(), "proposal": proposal_sha256})
        with self.store.locked():
            existing = self.store.read(f"requests/{key}.json")
            if existing:
                if existing["request_sha256"] != request_hash:
                    raise ReviewConflict("同一请求标识不能用于不同评议，请重新预览。")
                return existing["review_id"], False
            source, units, binding = self._prepared(options)
            if digest(binding) != proposal_sha256:
                raise ReviewConflict("正文或模型/价格配置已变化，请重新预览后创建评议。")
            review_id, now = uuid.uuid4().hex, self.clock()
            manifest = {"schema": VERSION, "id": review_id, "project_id": self.project_id, "status": "queued",
                        "created_at": now, "updated_at": now, "deadline": now + options.total_seconds,
                        "binding": binding, "binding_sha256": digest(binding), "unit_count": len(units),
                        "completed_units": 0, "calls": [], "error": None, "pause_requested": False,
                        "format_corrections": 0, "synthesis_calls": 0, "report_ready": False}
            manifest["source_summary"] = {k: source[k] for k in
                ("title", "source_sha256", "complete_book", "missing_chapters", "expected_chapters")}
            manifest["source_summary"]["chapter_numbers"] = [c["number"] for c in source["chapters"]]
            self.store.write(f"{review_id}/snapshot.json", source, immutable=True)
            self.store.write(f"{review_id}/units.json", units, immutable=True)
            self.store.write(f"{review_id}/manifest.json", manifest)
            self.store.write(f"requests/{key}.json", {"review_id": review_id, "request_sha256": request_hash}, immutable=True)
            return review_id, True

    def _manifest(self, review_id):
        item = self.store.read(f"{self.store.key(review_id)}/manifest.json")
        if item is None:
            raise KeyError(review_id)
        expected = item["binding"]["project_identity"]
        if item["binding_sha256"] != digest(item["binding"]) or expected != identity(self.project, legacy=expected["kind"] == "legacy_directory"):
            raise ReviewConflict("评议与当前作品实例不匹配")
        return item

    def _update(self, review_id, **changes):
        with self.store.locked():
            manifest = self._manifest(review_id)
            manifest.update(changes, updated_at=self.clock())
            self.store.write(f"{review_id}/manifest.json", manifest)
            return manifest

    def list(self):
        ledger = read_records(self.project)
        return [self.get(m["id"], freshness=False, detail=False, ledger=ledger) for m in self.store.manifests()]

    def get(self, review_id, *, freshness=True, detail=True, ledger=None):
        m = self._manifest(review_id)
        source = m["source_summary"]
        active = self.store.worker_active(review_id)
        status = m["status"]
        if status in ACTIVE and not active:
            status = "interrupted"
        result = {k: m[k] for k in ("id", "created_at", "updated_at", "unit_count", "completed_units", "error", "report_ready", "deadline", "pause_requested")}
        result.update(status=status, worker_active=active, call_count=len(m["calls"]), model=m["binding"]["model"],
                      draining=bool(m.get("draining")),
                      title=source["title"],
                      can_resume=not active and status in {"paused", "interrupted", "failed"} and self.clock() < m["deadline"],
                      options=m["binding"]["options"], source_sha256=source["source_sha256"],
                      complete_book=source["complete_book"], missing_chapters=source["missing_chapters"],
                      available_chapters=len(source["chapter_numbers"]), expected_chapters=source["expected_chapters"],
                      reviewed_chapters=source["chapter_numbers"] if m["report_ready"] else [],
                      token_counter="cl100k_base_reference", evidence_status="verified" if m["report_ready"] else "pending",
                      human_feedback="not_collected", advisory=True,
                      last_call_started_at=m["calls"][-1]["started_at"] if m["calls"] else None)
        ids = {c["id"] for c in m["calls"]}
        ledger = read_records(self.project) if ledger is None else ledger
        records = [r for key, r in ledger.items() if key in ids or any(key.startswith(i + ":retry:") for i in ids)]
        result["usage"] = summarize(records, m["binding"]["pricing"])
        result["unrecorded_calls"] = len(ids - {r["request_id"].split(":retry:")[0] for r in records})
        if freshness:
            try:
                current = snapshot(self.project, self.project_id, ReviewOptions(**m["binding"]["options"]), mappings=False)
                result["source_current"] = current["source_sha256"] == source["source_sha256"]
            except (ValueError, OSError, RuntimeError):
                result["source_current"] = None
        if detail:
            result["report"] = self.store.read(f"{review_id}/report.json")
        return result

    def source(self, review_id, chapter):
        self._manifest(review_id)
        source = self.store.read(f"{review_id}/snapshot.json")
        found = next((c for c in source["chapters"] if c["number"] == chapter), None)
        if found is None:
            raise KeyError(chapter)
        return {**found, "snapshot_sha256": source["source_sha256"], "offset_unit": "unicode_codepoints", "extractor": source["extractor"]}

    def pause(self, review_id):
        with self.store.locked():
            m = self._manifest(review_id)
            if m["status"] in ACTIVE:
                m.update(pause_requested=True, updated_at=self.clock())
                if not self.store.worker_active(review_id):
                    m["status"] = "paused"
                self.store.write(f"{review_id}/manifest.json", m)
        return self.get(review_id, freshness=False)

    def claim(self, review_id):
        """Reserve worker before JobRunner dispatch. Caller closes FD if dispatch fails."""
        with self.store.locked():
            m = self._manifest(review_id)
            if m["status"] == "completed":
                raise ReviewConflict("评议已完成")
            if (m["binding"]["implementation"] != implementation()
                    or m["binding"]["tokenizer_version"] != version("tiktoken")):
                raise ReviewConflict("评议实现已更新，旧结果仍可查看；请明确创建新评议。")
            if self.clock() >= m["deadline"]:
                raise ReviewConflict("原评议时长预算已用尽；旧结果保留，请重新预览创建新评议。")
            fd = self.store.acquire_worker(review_id)
            try:
                m.update(status="queued", pause_requested=False, error=None, updated_at=self.clock())
                self.store.write(f"{review_id}/manifest.json", m)
            except BaseException:
                os.close(fd)
                raise
            return fd

    def _check(self, review_id):
        m = self._manifest(review_id)
        if m["pause_requested"]:
            raise ReviewStopped("paused", "已暂停；当前响应与已完成段落保留，恢复不会增加原预算。")
        if self.clock() >= m["deadline"]:
            raise ReviewStopped("budget_exhausted", "总时长预算已用尽；已完成内容保留。")
        return m

    def _call(self, review_id, stage, key, payload, schema, validator):
        m = self._check(review_id)
        prompt = system(stage)
        request = {"system": prompt, "user": canonical(payload).decode()}
        request_hash = digest(request)
        relative = f"{review_id}/steps/{key}"
        saved = self.store.read(f"{relative}/result.json")
        if saved:
            if saved["request_sha256"] != request_hash:
                raise ReviewConflict("评议检查点依赖发生变化")
            return saved["result"]
        self.store.write(f"{relative}/request.json", request, immutable=True)
        opts = ReviewOptions(**m["binding"]["options"])
        # One shared corrective call across the whole job, never an infinite retry loop.
        for correction in (False, True):
            raw_key = f"{relative}/{'correction' if correction else 'response'}.json"
            raw_saved = self.store.read(raw_key)
            if raw_saved is None:
                m = self._check(review_id)
                if correction and m["format_corrections"] >= 1:
                    raise ValueError("评议格式/引用校验未通过，唯一一次格式更正额度已使用。")
                actual = tokens(request["system"] + "\n" + request["user"]) + 256
                if actual > opts.input_tokens:
                    raise ReviewStopped("budget_exhausted", f"评议请求需要 {actual} 参考 tokens，超过输入预算 {opts.input_tokens}；未截断正文或证据。")
                if len(m["calls"]) >= opts.max_calls:
                    raise ReviewStopped("budget_exhausted", "调用次数预算已用尽")
                if stage == "synthesis" and m["synthesis_calls"] >= 2:
                    raise ReviewStopped("budget_exhausted", "综合调用上限已使用")
                reserved = reference_cost(m["binding"]["model"], m["binding"]["pricing"], actual, opts.output_tokens)
                spent = sum(c.get("reserved_cny") or 0 for c in m["calls"])
                # Do not release reservations for unknown/failed usage; reconcile known overruns.
                ledger = read_records(self.project)
                for c in m["calls"]:
                    rows = [r for rid, r in ledger.items() if rid == c["id"] or rid.startswith(c["id"] + ":retry:")]
                    known = summarize(rows, m["binding"]["pricing"])["estimated_cost_cny"]
                    spent += max(0, (known or 0) - (c.get("reserved_cny") or 0))
                if opts.max_estimated_cny is not None and (reserved is None or spent + reserved > opts.max_estimated_cny):
                    raise ReviewStopped("budget_exhausted", "人民币估算预留预算已用尽；未知用量未按零计")
                call_id = uuid.uuid4().hex
                call = {"id": call_id, "stage": stage, "step": key, "started_at": self.clock(),
                        "reserved_cny": reserved, "reference_input_tokens": actual, "status": "reserved"}
                m = self._update(review_id, calls=[*m["calls"], call],
                                 format_corrections=m["format_corrections"] + int(correction),
                                 synthesis_calls=m["synthesis_calls"] + int(stage == "synthesis"))
                cancel, finished = threading.Event(), threading.Event()
                client = None
                def watch():
                    while not finished.wait(0.25):
                        try:
                            self._check(review_id)
                        except Exception:
                            cancel.set()
                            return
                watcher = threading.Thread(target=watch, daemon=True)
                watcher.start()
                try:
                    model = {**m["binding"]["model"], "timeout_seconds": min(opts.request_seconds, max(0.1, m["deadline"] - self.clock()))}
                    with usage_scope(self.project, "manuscript"), usage_activity("reader_review"), pricing_scope(m["binding"]["pricing"]), usage_request_identity(call_id):
                        client = self.client_factory(model, cancel)
                        raw = client.complete(request["system"], request["user"])
                    self.store.write(raw_key, {"request_id": call_id, "text": raw, "received_at": self.clock()}, immutable=True)
                    calls = self._manifest(review_id)["calls"]
                    calls[-1].update(status="response_saved", received_at=self.clock())
                    self._update(review_id, calls=calls)
                finally:
                    drain = getattr(client, "wait_for_local_completion", None)
                    if drain and not drain(0):
                        self._update(review_id, draining=True)
                        # An HTTP cancellation releases the adapter wait, not necessarily
                        # its SDK worker. Keep the lease and deletion fence until it exits.
                        drain()
                        self._update(review_id, draining=False)
                    finished.set()
                    watcher.join(timeout=1)
                self._check(review_id)
            else:
                raw = raw_saved["text"]
            try:
                if tokens(raw) > opts.output_tokens:
                    raise ReviewStopped("budget_exhausted", "返回内容超过预留输出 token；已保留原始响应，未静默截断。")
                result = validator(parse_reply(raw, schema))
                self.store.write(f"{relative}/result.json", {"request_sha256": request_hash, "result": result}, immutable=True)
                return result
            except ReviewStopped:
                raise
            except (ValueError, KeyError, TypeError) as error:
                if correction:
                    raise ValueError(f"评议更正后仍未通过结构/引用核查：{error}") from error
                request = {"system": prompt, "user": canonical({"original_request": payload, "invalid_response": raw,
                           "validation_error": str(error), "instruction": "Correct format/evidence only. Do not invent quotes or add unsupported claims."}).decode()}
        raise AssertionError("unreachable")

    def run(self, review_id, worker_fd):
        try:
            self._execute(review_id)
        except ReviewStopped as error:
            self._update(review_id, status=error.status, error=str(error))
        except Exception as error:
            m = self._manifest(review_id)
            state = "paused" if m["pause_requested"] else "budget_exhausted" if self.clock() >= m["deadline"] else "failed"
            self._update(review_id, status=state, error=f"{type(error).__name__}: {error}")
        finally:
            os.close(worker_fd)
        return self.get(review_id, freshness=False)

    def _execute(self, review_id):
        m = self._check(review_id)
        opts = ReviewOptions(**m["binding"]["options"])
        source = self.store.read(f"{review_id}/snapshot.json")
        units = self.store.read(f"{review_id}/units.json")
        if source["source_sha256"] != m["binding"]["source_sha256"]:
            raise ReviewConflict("正文快照绑定不匹配")
        observations, evidence, continuity = [], {}, ""
        self._update(review_id, status="reading")
        for index, unit in enumerate(units):
            payload = {"stage": "reading", "language": opts.language, "audience": opts.audience,
                       "previous_facts": {"summary": continuity, "lossy": True, "context_only": True,
                                          "covered_units": [u["id"] for u in units[:index]]}, "unit": unit}
            def validate_reading(result):
                catalog = {}
                for group in ("facts", "strengths", "issues"):
                    for row in result[group]:
                        spans = [resolve_quote(q, source, units, {unit["id"]}) for q in row.pop("evidence")]
                        row["evidence_ids"] = [span["id"] for span in spans]
                        catalog.update({span["id"]: span for span in spans})
                return {**result, "evidence": catalog, "covered_units": [unit["id"]]}
            reading = self._call(review_id, "reading", unit["id"], payload, Reading, validate_reading)
            continuity = reading["continuity_summary"]
            evidence.update(reading["evidence"])
            observations.append({k: v for k, v in reading.items() if k not in {"continuity_summary", "evidence"}})
            self._update(review_id, completed_units=max(self._manifest(review_id)["completed_units"], index + 1))
        self._update(review_id, status="synthesizing")
        # Multi-level grouping keeps all ordered units represented. It is explicitly lossy.
        level, records = 0, observations
        public = {"title": source["title"], "genres": source["public_genres"],
                  "description": opts.public_description, "audience": opts.audience}
        def synthesis_payload(rows):
            ids = _evidence_ids(rows)
            return {"stage": "synthesis", "language": opts.language, "records": rows,
                    "public_expectations": public, "coverage": {"complete_book": source["complete_book"],
                      "missing_chapters": source["missing_chapters"], "chapters": [c["number"] for c in source["chapters"]]},
                    "lossy_summary_levels": level, "evidence_catalog": {i: evidence[i] for i in sorted(ids)}}
        while tokens(system("synthesis") + canonical(synthesis_payload(records)).decode()) + 256 > opts.input_tokens:
            groups, current = [], []
            for record in records:
                candidate = [*current, record]
                payload = _digest_payload(candidate, opts.language)
                if tokens(system("digest") + canonical(payload).decode()) + 256 > opts.input_tokens // 2:
                    if not current:
                        groups.append([record])
                        continue
                    groups.append(current)
                    current = [record]
                else:
                    current = candidate
            if current:
                groups.append(current)
            reduced = []
            for index, group in enumerate(groups):
                payload = _digest_payload(group, opts.language)
                def validate_digest(result):
                    if result["covered_units"] != payload["covered_units"] or not set(result["evidence_ids"]) <= _evidence_ids(group):
                        raise ValueError("综合摘要遗漏覆盖范围或引用无效")
                    return result
                reduced.append(self._call(review_id, "digest", f"digest-{level}-{index}", payload, Digest, validate_digest))
            if tokens(reduced) >= tokens(records):
                raise ReviewStopped("budget_exhausted", "综合摘要未减少上下文占用；原文和观察保留，不重复压缩调用。")
            records, level = reduced, level + 1
        payload = synthesis_payload(records)
        allowed = set(payload["evidence_catalog"])
        def validate_report(report):
            if sorted(d["dimension"] for d in report["dimensions"]) != sorted(DIMENSIONS):
                raise ValueError("八个阅读维度必须各出现一次")
            for row in [*report["strengths"], *report["issues"], *report["dimensions"]]:
                if not set(row["evidence_ids"]) <= allowed:
                    raise ValueError("报告引用不在已核实的证据目录中")
                if row.get("status") in {"strength", "improve"} and not row["evidence_ids"]:
                    raise ValueError("有结论的维度必须有原文依据")
                if row.get("scope") == "cross_chapter" and len({evidence[i]["chapter"] for i in row["evidence_ids"]}) < 2:
                    raise ValueError("跨章节结论至少需要两个章节的证据")
            return report
        report = self._call(review_id, "synthesis", "report", payload, Report, validate_report)
        self._update(review_id, status="verifying")
        self._check(review_id)
        final = {**report, "schema": VERSION, "source_sha256": source["source_sha256"], "advisory": True,
                 "evidence": evidence, "covered_units": [u["id"] for u in units], "lossy_summary_levels": level,
                 "coverage_complete": True, "source_complete_book": source["complete_book"],
                 "human_feedback": "not_collected", "private_contract_review": "not_performed",
                 "limitations": ["每段原文完整提交；前文上下文与跨段综合摘要可能损失细节。",
                                 "引用由代码核对；引用真实不等于文学判断正确。", "吸引力是模型阅读意见，尚未经目标读者验证。"]}
        self.store.write(f"{review_id}/report.json", final, immutable=True)
        self._update(review_id, status="completed", report_ready=True, error=None)


def _evidence_ids(value):
    if isinstance(value, dict):
        result = set(value.get("evidence_ids", []))
        for key, child in value.items():
            if key != "evidence_ids":
                result.update(_evidence_ids(child))
        return result
    if isinstance(value, list):
        return set().union(*(_evidence_ids(child) for child in value))
    return set()


def _digest_payload(records, language):
    return {"stage": "digest", "language": language, "records": records,
            "covered_units": [unit for row in records for unit in row["covered_units"]]}
