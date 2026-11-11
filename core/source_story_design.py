"""Shared, resumable whole-story design for library and Workshop source intake.

The public interface owns source coverage, proposal versions and model receipts.
No legacy analysis, discussion, manuscript or global model setting is mutated.
"""
from __future__ import annotations

from copy import deepcopy
import json
import threading
from uuid import uuid4

from .creation_mode import RULES
from .creative_types import digest
from .llm_client import LLMInputLimitError
from .planning_capacity import input_limit
from .source_design_store import SourceDesignStore, now
from .source_evidence import bind_observations, bisect_segment, source_snapshot, verify_coverage
from .source_route_prompts import system_prompt
from .source_routes import (
    VERSION, InitialResponse, IntegrationResponse, ReadingResponse, RouteResponse,
    SourceRouteCapacityError, SourceRouteConflict, SourceRouteError,
    SourceRouteIncompleteError, SourceRouteProtocolError, SourceRouteUnknownError, content_view, parse_response,
    ROUTE_PAGES, RouteContent, route_page_contract, route_version, validate_routes,
)
from .source_study import lease
from .source_request_recovery import classify_failure

OPERATIONS = {"initial_routes", "revise_routes", "expand_routes", "revisit_source"}
MODEL_KEYS = {"provider", "provider_name", "model", "model_id", "connection_id", "route_id",
              "reasoning_effort", "max_tokens", "planning_input_bytes", "timeout_seconds"}


def model_receipt(model) -> dict:
    if isinstance(model, dict):
        return {k: deepcopy(v) for k, v in model.items() if k in MODEL_KEYS and isinstance(v, (str, int, float, bool, type(None)))}
    return {k: getattr(model, k) for k in MODEL_KEYS if isinstance(getattr(model, k, None), (str, int, float, bool))}


def normalize_request(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("路线请求必须是对象。")
    request = deepcopy(raw)
    op_id = request.get("operation_id")
    if not isinstance(op_id, str) or not op_id or len(op_id) > 200:
        raise ValueError("缺少有效的操作标识。")
    op = request.setdefault("operation", "initial_routes")
    if op not in OPERATIONS or request.setdefault("mode", "source_development") not in RULES:
        raise ValueError("未知路线操作或创作方式。")
    if not isinstance(request.setdefault("author_context", {}), dict):
        raise ValueError("作者上下文必须是对象。")
    if not isinstance(request.setdefault("feedback", ""), str):
        raise ValueError("补充意见必须是文本。")
    request.setdefault("target_route_ids", [])
    if not isinstance(request["target_route_ids"], list) or any(not isinstance(v, str) for v in request["target_route_ids"]):
        raise ValueError("路线修改范围无效。")
    count = request.setdefault("requested_count", 2 if op == "expand_routes" else 3)
    if type(count) is not int or count < 1:
        raise ValueError("本次路线数量须为正整数。")
    actions = request.setdefault("requested_actions", [])
    if not isinstance(actions, list):
        raise ValueError("复合动作必须是数组。")
    for action in actions:
        if not isinstance(action, dict) or action.get("operation") not in OPERATIONS:
            raise ValueError("复合动作无效。")
        if "requested_count" in action and (type(action["requested_count"]) is not int or action["requested_count"] < 1):
            raise ValueError("本次路线数量须为正整数。")
    # UI origin is navigation metadata, not semantic model input or cache identity.
    request.pop("origin", None)
    request.pop("entry_point", None)
    return request


class _Paused(Exception):
    pass


class _Replan(Exception):
    def __init__(self, actions):
        self.actions = actions


class SourceStoryDesign:
    def __init__(self, library, source_id: str):
        self.library, self.source_id = library, source_id
        self.store = SourceDesignStore(library.path(source_id))
        self.root = self.store.root

    def prepare(self, request: dict, *, model: dict | None = None, catalog: list[dict] | None = None) -> dict:
        request = normalize_request(request)
        self.store.path().mkdir(parents=True, exist_ok=True)
        with lease(self.store.path(".prepare.lock")):
            key = digest(request["operation_id"])
            previous = self.store.read("operations", key + ".json")
            if previous:
                if previous["request_sha256"] != digest(request):
                    raise SourceRouteConflict("同一操作标识已有不同内容，请提交新的操作。")
                return self.status(previous["run_id"])
            snapshot = source_snapshot(self.library, self.source_id)
            base = None
            if request.get("base_route_set_sha256"):
                base = self.get_route_set(request["base_route_set_sha256"])
                if base["mode"] != request["mode"] and request["operation"] != "initial_routes":
                    raise SourceRouteConflict("更换创作模式请创建新的整书设计，旧路线仍保留。")
            elif request["operation"] in {"revise_routes", "expand_routes", "revisit_source"}:
                raise ValueError("继续分析须指定已有路线版本。")
            available = {r["route_id"] for r in base["routes"]} if base else set()
            for ids in [request["target_route_ids"], *[a.get("target_route_ids", []) for a in request["requested_actions"]]]:
                if not isinstance(ids, list) or not set(ids) <= available:
                    raise SourceRouteConflict("修改对象不属于所指定的路线版本。")
            catalog = deepcopy(catalog or [])
            if not catalog or not any(i.get("kind", i.get("type")) == "genre" for i in catalog) or not any(i.get("kind", i.get("type")) == "tag" for i in catalog):
                raise ValueError("请先启用至少一个 Genre 和一个 Tag。")
            if len({i.get("id") for i in catalog}) != len(catalog):
                raise ValueError("题材目录 ID 重复。")
            run_id = uuid4().hex
            history = deepcopy(base.get("feedback_history", [])) if base else []
            history.append({"feedback_id": "feedback_" + uuid4().hex,
                            "exact_text": request["feedback"],
                            "author_context": deepcopy(request["author_context"]),
                            "operation": request["operation"],
                            "target_route_ids": request["target_route_ids"],
                            "base_route_set_sha256": request.get("base_route_set_sha256"),
                            "created_at": now()})
            inputs = {"schema": VERSION, "request": request, "snapshot": snapshot,
                      "catalog": catalog, "model": model_receipt(model or {}),
                      "feedback_history": history, "base_route_set": base,
                      "prompt_policy": {op: system_prompt(request["mode"], op)
                                        for op in sorted(OPERATIONS | {"read_source", "integrate"})}}
            inputs["sha256"] = digest(inputs)
            state = {"schema": VERSION, "run_id": run_id, "source_id": self.source_id,
                     "status": "queued", "stage": "created", "completed": 0, "total": 0,
                     "model_calls": 0, "created_at": now(), "updated_at": now(),
                     "request": request, "input_sha256": inputs["sha256"],
                     "route_set_sha256": None, "understanding_sha256": None, "error": None}
            self.store.immutable(inputs, "runs", run_id, "inputs.json")
            self.store.save_state(state)
            self.store.immutable({"request_sha256": digest(request), "run_id": run_id}, "operations", key + ".json")
            return state

    def status(self, run_id: str | None = None) -> dict:
        if run_id:
            return self.store.state(run_id)
        runs = self.list_runs()
        return runs[-1] if runs else {"status": "pending", "stage": "created", "completed": 0, "total": 0, "model_calls": 0}

    def list_runs(self) -> list[dict]:
        root = self.store.path("runs")
        return sorted((self.store.state(p.parent.name) for p in root.glob("*/state.json")), key=lambda s: s["created_at"]) if root.exists() else []

    def list_route_sets(self) -> list[dict]:
        return self.store.artifacts("route-sets")

    def get_route_set(self, sha: str) -> dict:
        value = self.store.get_route_set(sha)
        if value["source_id"] != self.source_id:
            raise ValueError("路线版本属于其他来源。")
        return value

    def get_understanding(self, sha: str) -> dict:
        value = self.store.get_understanding(sha)
        if value["source_id"] != self.source_id:
            raise ValueError("来源理解属于其他资料。")
        return value

    def export_evidence(self, understanding_sha: str) -> dict:
        return deepcopy(self.get_understanding(understanding_sha))

    def get_run_context(self, run_id: str) -> dict:
        state = self.store.state(run_id)
        inputs = self.store.read("runs", run_id, "inputs.json")
        if inputs.get("sha256") != state["input_sha256"] or inputs["sha256"] != digest({k: v for k, v in inputs.items() if k != "sha256"}):
            raise SourceRouteError("设计输入快照校验失败。")
        return {"request": deepcopy(inputs["request"]), "feedback_history": deepcopy(inputs["feedback_history"]),
                "source_sha256": inputs["snapshot"]["source_sha256"]}

    def pause(self, run_id: str) -> dict:
        # A separate cancellation marker avoids losing concurrently saved progress.
        self.store.state(run_id)
        self.store.write({"requested_at": now()}, "runs", run_id, "pause.json")
        state = self.status(run_id)
        return {**state, "pause_requested": True}

    def _check(self):
        if (self._cancel is not None and self._cancel.is_set()) or self.store.read("runs", self._run_id, "pause.json"):
            raise _Paused()

    def _save(self, **updates):
        self._state.update(updates, updated_at=now())
        self.store.save_state(self._state)

    def _fits(self, system: str, payload: dict) -> bool:
        # Reserve covers the wire envelope and the response budget separately from
        # semantic material. Unknown byte capacity is left for the provider guard.
        size = len(system.encode()) + len(json.dumps(payload, ensure_ascii=False).encode()) + 1024 + self._output_reserve
        return self._limit is None or size <= self._limit

    def _system(self, operation):
        policy = self._inputs.get("prompt_policy")
        if policy is not None:
            return policy[operation]
        return system_prompt(self._inputs["request"]["mode"], operation)

    def _payload(self, operation: str, contract, *, slots=(), source=(), ledger=(), routes=(), extra=None) -> dict:
        request = self._inputs["request"]
        task = {"operation": operation, "creation_mode": request["mode"],
                "analysis_language": request["author_context"].get("analysis_language", "zh-CN"),
                "allowed_output_slots": list(slots), "requested_count": len(slots),
                "additional_count": len(slots), "generation_operation": request["operation_id"],
                "completed_actions": deepcopy(self._executed_actions)}
        value = {"task": task, "author_context": request["author_context"],
                 "feedback": request["feedback"], "history": self._inputs["feedback_history"],
                 "source_view": list(source), "evidence_view": list(ledger),
                 "comparison_routes": list(routes), "valid_type_catalog": [] if operation in {"read_source", "revisit_source"} else self._inputs["catalog"],
                 "response_schema": contract.model_json_schema()}
        if extra:
            value.update(extra)
        return value

    def _catalog_manifest(self):
        # Identity/name navigation is not a compressed substitute for the full
        # definitions; complete records are processed in the integration sweep.
        return [{"id": row["id"], "kind": row.get("kind", row.get("type")),
                 "name": row.get("name", row["id"]),
                 **({"audience_channels": row["audience_channels"]} if "audience_channels" in row else {})}
                for row in self._inputs["catalog"]]

    def _request(self, operation: str, payload: dict, contract, *, repair=True) -> dict:
        self._check()
        system = self._system(operation)
        key = digest([VERSION, system, payload])
        parts = ("runs", self._run_id, "requests", key + ".json")
        receipt = self.store.read(*parts)
        if receipt is None:
            receipt = {"request_sha256": key, "system": system, "payload": payload,
                       "model": model_receipt(self._client), "created_at": now(),
                       "capacity_bytes": self._limit, "status": "prepared"}
            self.store.write(receipt, *parts)
        elif receipt.get("request_sha256") != key or digest([VERSION, receipt.get("system"), receipt.get("payload")]) != key:
            raise SourceRouteError("保存的模型请求身份不匹配，未重新发送。")
        if receipt.get("raw") is not None:
            if digest(receipt["raw"]) != receipt.get("response_sha256"):
                raise SourceRouteError("保存的模型响应完整性校验失败，未重发请求。")
        elif receipt["status"] in {"dispatched", "pending_unknown"}:
            # A provider can offer an idempotent status query. Never guess failure.
            recover = getattr(self._client, "recover_response", None)
            recovered = recover(key) if callable(recover) else None
            if not isinstance(recovered, str):
                raise SourceRouteUnknownError("模型请求结果尚未确认，已有内容全部保留；系统将复用可查询的原请求结果。")
            receipt.update(raw=recovered, response_sha256=digest(recovered), status="response_received", received_at=now())
            self.store.write(receipt, *parts)
        else:
            if not self._fits(system, payload):
                raise SourceRouteCapacityError("完整作者要求、路线与必要依据超过当前连接容量；资料和进度已保留，请换更大容量连接继续。")
            while True:
                self._check()
                receipt.setdefault("attempts", []).append({"model": model_receipt(self._client), "dispatched_at": now()})
                receipt.update(status="dispatched", dispatched_at=now(), model=model_receipt(self._client))
                self.store.write(receipt, *parts)
                self._save(model_calls=self._state["model_calls"] + 1)
                try:
                    complete = getattr(self._client, "complete", self._client)
                    raw = complete(system, json.dumps(payload, ensure_ascii=False))
                except LLMInputLimitError as exc:
                    outcome = classify_failure(exc)
                    receipt["attempts"][-1]["outcome"] = outcome
                    receipt.update(status="not_sent", limit_bytes=exc.limit_bytes, failure=outcome)
                    self.store.write(receipt, *parts)
                    self._limit = exc.limit_bytes
                    self._save(model_calls=self._state["model_calls"] - 1)
                    raise SourceRouteCapacityError(str(exc)) from exc
                except Exception as exc:
                    outcome = classify_failure(exc)
                    receipt["attempts"][-1]["outcome"] = outcome
                    receipt.update(status="pending_unknown" if outcome["outcome"] == "unknown" else outcome["outcome"], failure=outcome)
                    self.store.write(receipt, *parts)
                    if outcome["outcome"] == "unknown":
                        raise SourceRouteUnknownError("模型请求结果尚未确认；完整请求已保存。当前连接如支持查询将核对原响应，否则不会盲目重发。") from exc
                    if outcome["retryable"] and not self._automatic_retry_used:
                        self._automatic_retry_used = True
                        continue
                    status = outcome.get("http_status")
                    detail = f"（HTTP {status}）" if status else ""
                    raise SourceRouteError(f"模型请求已明确失败{detail}，已保留全部进度；恢复时可继续未完成步骤。") from exc
                receipt["attempts"][-1]["outcome"] = {"outcome": "response_received"}
                break
            # Persist before inspecting cancellation or parsing a paid response.
            receipt.update(raw=raw, response_sha256=digest(raw), status="response_received", received_at=now())
            self.store.write(receipt, *parts)
        try:
            result = parse_response(receipt["raw"], contract)
            self._validate_response(result, payload, contract)
        except SourceRouteIncompleteError:
            raise
        except SourceRouteProtocolError as exc:
            if not repair:
                raise
            result = None
            for correction_key in reversed(receipt.get("correction_requests", [])):
                previous = self.store.read("runs", self._run_id, "requests", correction_key + ".json")
                if previous is None:
                    raise SourceRouteError("协议修复请求回执缺失，未重新购买请求。")
                if previous.get("raw") is None:
                    result = self._request(operation, previous["payload"], contract, repair=False)
                    break
                if previous.get("response_sha256") != digest(previous["raw"]):
                    raise SourceRouteError("协议修复响应完整性校验失败。")
                try:
                    recovered = parse_response(previous["raw"], contract)
                    self._validate_response(recovered, previous["payload"], contract)
                except SourceRouteProtocolError:
                    continue
                result = recovered
                break
            if result is None:
                correction = deepcopy(payload)
                correction["protocol_correction"] = {"raw_response": receipt["raw"], "error": str(exc),
                    "recovery_revision": self._state.get("recovery_revision", 0),
                    "repair_model": model_receipt(self._client),
                    "instruction": "保留完整文学内容与判断，仅修正输出合同。完整返回本轮授权产物，不靠删减结局或来源事项适配。"}
                correction_key = digest([VERSION, system, correction])
                correction_parts = ("runs", self._run_id, "requests", correction_key + ".json")
                if self.store.read(*correction_parts) is None:
                    self.store.write({"request_sha256": correction_key, "system": system, "payload": correction,
                        "model": model_receipt(self._client), "created_at": now(),
                        "capacity_bytes": self._limit, "status": "prepared"}, *correction_parts)
                receipt.setdefault("correction_requests", []).append(correction_key)
                self.store.write(receipt, *parts)
                result = self._request(operation, correction, contract, repair=False)
        receipt.update(status="parsed", parsed_sha256=digest(result))
        self.store.write(receipt, *parts)
        self._check()
        return result

    def _saved_request(self, operation: str, payload: dict) -> bool:
        system = self._system(operation)
        key = digest([VERSION, system, payload])
        receipt = self.store.read("runs", self._run_id, "requests", key + ".json")
        return receipt is not None and (receipt.get("raw") is not None or receipt.get("status") in {"dispatched", "pending_unknown"})

    def _validate_response(self, result: dict, payload: dict, contract):
        source = payload.get("source_view", [])
        records = source + payload.get("evidence_view", []) + payload.get("requested_evidence", [])
        available = {row["id"] for row in records if "id" in row}
        available.update(ref for route in payload.get("comparison_routes", []) + payload.get("historical_comparison", []) for ref in route.get("source_refs", []))
        if "routes" in result:
            if result.get("defer_current") and (result["routes"] or not result.get("followup_actions")):
                raise SourceRouteProtocolError("延后当前动作必须返回明确执行计划，不能同时发布路线。")
            if contract is IntegrationResponse and (result.get("followup_actions") or result.get("defer_current")):
                raise SourceRouteProtocolError("证据整合不能自行新增作者动作。")
            validate_routes(result["routes"], slots=payload["task"]["allowed_output_slots"],
                source_ids=available, catalog=payload["valid_type_catalog"], complete=contract is not IntegrationResponse and not result.get("defer_current"))
            if result.get("recommended_slot") and result["recommended_slot"] not in payload["task"]["allowed_output_slots"]:
                raise SourceRouteProtocolError("推荐路线超出本轮输出范围。")
        for action in result.get("followup_actions", []):
            if action["author_quote"] not in payload.get("feedback", ""):
                raise SourceRouteProtocolError("追加动作缺少本轮作者原话依据。")
            if not set(action["target_route_ids"]) <= self._available_route_ids:
                raise SourceRouteProtocolError("追加动作指向未提供或不存在的路线。")
        if "understanding" in result:
            bind_observations(result["understanding"], source,
                              prior_ids={r["id"] for r in payload.get("prior_observations", [])})
        if "source_refs" in result and not set(result["source_refs"]) <= available:
            raise SourceRouteProtocolError("路线分页引用了未提供的证据。")

    def _partition(self, spans: list[dict], operation: str, contract, *, extra=None) -> list[list[dict]]:
        pending = deepcopy(spans)
        groups, current = [], []
        system = self._system(operation)
        while pending:
            span = pending.pop(0)
            payload = self._payload(operation, contract, source=current + [span], extra=extra)
            if self._fits(system, payload):
                current.append(span)
            elif current:
                groups.append(current)
                current = []
                pending.insert(0, span)
            elif len(span["text"]) > 1:
                pending[0:0] = bisect_segment(span)
            else:
                raise SourceRouteCapacityError("当前连接无法容纳完整创作规则、作者要求与最小证据单元；未裁剪任何内容。")
        if current:
            groups.append(current)
        return groups

    def _understanding(self, parts: list[dict], spans: list[dict], *, parent=None) -> dict:
        ledger = deepcopy(parent.get("ledger", [])) if parent else []
        ids = {row["id"] for row in ledger}
        for part in parts:
            for row in part["ledger"]:
                if row["id"] not in ids:
                    ledger.append(row)
                    ids.add(row["id"])
        # All statements are retained, not recursively squeezed into a summary.
        values = [p["content"] for p in parts]
        ordered_spans = [s for s in spans if s.get("scope") == "listed_chapter"]
        final_span = max(ordered_spans, key=lambda s: (s.get("index") or 0, s["end"])) if ordered_spans else None
        endpoint_parts = [p for p in parts if final_span and final_span["id"] in p["segment_ids"]]
        endpoint = endpoint_parts[-1]["content"]["narrative_endpoint"] if endpoint_parts else (parent or {}).get("narrative_endpoint", "已提供资料没有有序正文终点。")
        summary = "\n\n".join(v["story_summary"] for v in values)
        current_ids = {s["id"] for s in spans}
        inherited_ranges = {s["id"]: s for s in ((parent or {}).get("segments", []) + (parent or {}).get("additional_segments", []))
                            if s["id"] not in current_ids}
        historical_threads = deepcopy((parent or {}).get("historical_open_threads", []))
        if parent:
            historical_threads.extend({"understanding_sha256": parent["sha256"], "text": text,
                "authority": "historical_interpretation_only"} for text in parent["open_threads"])
        return self.store.put_artifact("understandings", {
            "schema": "source-understanding.v1", "source_id": self.source_id,
            "source_sha256": self._inputs["snapshot"]["source_sha256"],
            "source_index": self._inputs["snapshot"]["source_index"],
            "segments": spans, "additional_segments": list(inherited_ranges.values()),
            "ledger": ledger, "reading_parts": parts,
            "story_summary": summary, "summary_projection": summary,
            "narrative_endpoint": endpoint,
            "endpoint_source_refs": [final_span["id"]] if final_span else [],
            "opening_analysis": "\n\n".join(v["opening_analysis"] for v in values),
            "open_threads": list(dict.fromkeys(t for v in values for t in v["open_threads"])),
            "historical_open_threads": historical_threads,
            "parent_understanding_sha256": parent["sha256"] if parent else None,
            "coverage": {"all_supplied_text_sent": True, "segment_ids": [s["id"] for s in spans]},
            "created_at": self._state["created_at"],
        })

    def _read(self, *, parent=None) -> dict:
        original = self._inputs["snapshot"]["segments"]
        operation = "revisit_source" if parent else "read_source"
        plan_key = digest([operation, parent["sha256"] if parent else None, self._executed_actions])
        plan_path = ("runs", self._run_id, "reading-plans", plan_key + ".json")
        saved_plan = self.store.read(*plan_path)
        groups = saved_plan["groups"] if saved_plan else self._partition(original, operation, ReadingResponse)
        self.store.write({"groups": groups}, *plan_path)
        parts, i = [], 0
        self._save(stage="reading", completed=0, total=len(groups))
        prior_ranges = {s["id"]: s for s in ((parent or {}).get("segments", []) + (parent or {}).get("additional_segments", []))}

        def related(row, group):
            return any(old.get("path") == new["path"] and max(old.get("start", 0), new["start"]) < min(old.get("end", 0), new["end"])
                       for ref in row["source_refs"] for old in [prior_ranges.get(ref, {})] for new in group)

        def read_payload(group, prior_page, page=0, previous=()):
            return self._payload(operation, ReadingResponse, source=group,
                extra={"reading_scope": {"range_id": digest(group), "prior_page_id": digest(prior_page), "page": page},
                       "previous_page_receipts": list(previous),
                       "prior_observations": [r for r in prior_page if "historical_thread" not in r],
                       "prior_open_threads": [r["historical_thread"] for r in prior_page if "historical_thread" in r],
                       "thread_scope": "prior_open_threads 是待核对的历史编辑理解，不是当前必须成立的事实。按实际原文范围重新记录仍开放的线索；错误或已解决解释留在历史，不作为当前open_threads。"})

        while i < len(groups):
            group = groups[i]
            prior = [row for row in (parent or {}).get("ledger", []) if related(row, group)]
            prior.extend({"historical_thread": {"understanding_sha256": parent["sha256"], "text": text}}
                         for text in (parent or {}).get("open_threads", []))
            prior_path = ("runs", self._run_id, "reading-priors", digest([plan_key, group]) + ".json")
            prior_plan = self.store.read(*prior_path)
            split_source = False
            try:
                if prior_plan is not None:
                    prior_pages = prior_plan["pages"]
                else:
                    prior_pages, current = [], []
                    for row in prior:
                        if self._fits(self._system(operation), read_payload(group, current + [row])):
                            current.append(row)
                        elif current:
                            prior_pages.append(current)
                            current = [row]
                        else:
                            raise SourceRouteCapacityError("单个完整旧观察与当前原文范围无法同时容纳。")
                    if current or not prior_pages:
                        prior_pages.append(current)
                    self.store.write({"pages": prior_pages}, *prior_path)
                prior_index = 0
                while prior_index < len(prior_pages):
                    prior_page = prior_pages[prior_index]
                    page, previous, split_prior = 0, [], False
                    while True:
                        payload = read_payload(group, prior_page, page, previous)
                        try:
                            result = self._request(operation, payload, ReadingResponse)
                        except (SourceRouteIncompleteError, SourceRouteCapacityError):
                            if len(prior_page) > 1:
                                middle = len(prior_page) // 2
                                prior_pages[prior_index:prior_index + 1] = [prior_page[:middle], prior_page[middle:]]
                                self.store.write({"pages": prior_pages}, *prior_path)
                                split_prior = True
                                break
                            minimum = deepcopy(payload)
                            minimum["source_view"] = []
                            if not self._fits(self._system(operation), minimum):
                                raise SourceRouteCapacityError("单个完整观察/作者要求已超过容量，已保留原文与已付费阅读。")
                            raise
                        content = result["understanding"]
                        ledger = bind_observations(content, group, prior_ids={r["id"] for r in prior_page if "id" in r})
                        if page and not {r["id"] for r in ledger} - {r["id"] for p in parts for r in p["ledger"]}:
                            raise SourceRouteProtocolError("来源分页未增加新内容，完整回复与已完成阅读保留。")
                        parts.append({"segment_ids": [s["id"] for s in group], "prior_observation_ids": [r["id"] for r in prior_page if "id" in r],
                                      "content": content, "ledger": ledger})
                        if not result["more"]:
                            break
                        previous.append({"sha256": digest(content), "observation_ids": [r["id"] for r in ledger],
                                         "continuation": result["continuation"]})
                        page += 1
                    if not split_prior:
                        prior_index += 1
            except (SourceRouteIncompleteError, SourceRouteCapacityError):
                if len(group) > 1:
                    middle = len(group) // 2
                    replacement = [group[:middle], group[middle:]]
                elif len(group[0]["text"]) > 1:
                    replacement = [[piece] for piece in bisect_segment(group[0])]
                else:
                    raise
                # Keep completed pages' evidence addresses even when later pages
                # need a finer range, and never discard their raw paid responses.
                for old_span in group:
                    self._additional_spans[old_span["id"]] = old_span
                groups[i:i + 1] = replacement
                self.store.write({"groups": groups}, *plan_path)
                self._save(total=len(groups))
                split_source = True
            if not split_source:
                i += 1
                self._save(completed=i)
        spans = [s for group in groups for s in group]
        verify_coverage(original, spans)
        value = self._understanding(parts, spans, parent=parent)
        self._save(understanding_sha256=value["sha256"])
        return value

    def _route_ids(self, routes: list[dict], slots: list[str], operation: str, old: list[dict]) -> list[dict]:
        old_map = {r["route_id"]: r for r in old}
        result = []
        for content in routes:
            slot = content["slot"]
            prior = old_map.get(slot) if operation == "revise_routes" else None
            # Deterministic within this operation, including crash after raw response.
            rid = prior["route_id"] if prior else "route_" + digest([self._run_id, slot])[:24]
            result.append(route_version(content, rid, parent_sha256=prior["sha256"] if prior else None))
        return result

    def _source_ids(self, understanding: dict) -> set[str]:
        return {r["id"] for r in understanding["segments"] + understanding["ledger"] + understanding.get("additional_segments", [])} | set(self._additional_spans)

    def _design(self, operation: str, slots: list[str], understanding: dict, old: list[dict]) -> tuple[list[dict], str, str, list[dict]]:
        payload = self._payload(operation, RouteResponse, slots=slots,
            source=understanding["segments"], ledger=understanding["ledger"], routes=old,
            extra={"source_understanding": {k: understanding[k] for k in ("story_summary", "narrative_endpoint", "opening_analysis", "open_threads")},
                   "historical_open_threads": understanding.get("historical_open_threads", [])})
        system = self._system(operation)
        if self._fits(system, payload) or self._saved_request(operation, payload):
            try:
                result = self._request(operation, payload, RouteResponse)
            except SourceRouteCapacityError:
                if self._fits(system, payload):
                    raise
                return self._design_partitioned(operation, slots, understanding, old)
            except SourceRouteIncompleteError:
                if len(slots) == 1:
                    return self._paged_route(operation, slots[0], payload)
                middle = len(slots) // 2
                left = self._design(operation, slots[:middle], understanding, old)
                right = self._design(operation, slots[middle:], understanding, old + self._route_ids(left[0], slots[:middle], operation, old))
                return left[0] + right[0], left[1] or right[1], left[2] or right[2], left[3] + right[3]
            if result["defer_current"]:
                raise _Replan(result["followup_actions"])
            validate_routes(result["routes"], slots=slots, source_ids=self._source_ids(understanding), catalog=self._inputs["catalog"])
            return result["routes"], result["recommended_slot"], result["recommendation_reason"], result["followup_actions"]
        return self._design_partitioned(operation, slots, understanding, old)

    def _paged_route(self, operation, slot, original_payload):
        """Recover output truncation with full narrative sections, not shorter fiction."""
        accumulated = {}
        followups = []
        for page_number, (name, fields) in enumerate(ROUTE_PAGES, 1):
            contract = route_page_contract(name, fields)
            payload = deepcopy(original_payload)
            payload["response_schema"] = contract.model_json_schema()
            payload["output_page"] = {"number": page_number, "total": len(ROUTE_PAGES),
                "fields": fields, "complete_previous_fields": accumulated,
                "instruction": "只输出本页完整字段。已保存字段作为设计前提，不能丢掉结局、情绪兑现或作者要求来缩短内容；按完整剧情而非截断前文补括号。"}
            try:
                content = self._request(operation, payload, contract)
            except SourceRouteIncompleteError:
                revision = self._state.get("recovery_revision", 0)
                if not revision:
                    raise
                # A previous resume may have finished this page before a later
                # page/publication failed. Reuse that paid complete artifact.
                for prior_revision in range(1, revision):
                    retry = deepcopy(payload)
                    retry["output_page"]["recovery_revision"] = prior_revision
                    if not self._saved_request(operation, retry):
                        continue
                    try:
                        content = self._request(operation, retry, contract)
                        break
                    except SourceRouteIncompleteError:
                        continue
                else:
                    retry = deepcopy(payload)
                    retry["output_page"]["recovery_revision"] = revision
                    content = self._request(operation, retry, contract)
                # Only an explicit recovery of a failed run advances revision.
                # Unknown remote outcomes retain the same attempt; changing the
                # current connection cannot turn them into a blind duplicate.
            if content.get("defer_current"):
                if not content.get("followup_actions"):
                    raise SourceRouteProtocolError("路线分页的延后动作缺少执行计划。")
                raise _Replan(content["followup_actions"])
            followups.extend(content.pop("followup_actions", []))
            content.pop("defer_current", None)
            if content["slot"] != slot:
                raise SourceRouteProtocolError("路线分页对应了错误的输出位置。")
            accumulated = {**accumulated, **content}
        route = RouteContent.model_validate(accumulated).model_dump()
        return [route], "", "", followups

    def _integration_payloads(self, understanding, candidate, slots):
        """Pack actual candidate + complete source/ledger records, never a read-only budget."""
        system = self._system("integrate")
        extra = {"source_unit_index": understanding["source_index"],
                 "scope_instruction": "来源原文与完整观察按声明范围分批提供，所有范围都会处理。只返回需修改的候选；无变化返回 routes=[]。未知依赖按 ID 申请原文，不把未见内容视为已检查。"}
        pending = [("source", deepcopy(s)) for s in understanding["segments"]]
        pending.extend(("ledger", deepcopy(row)) for row in understanding["ledger"])
        # Editorial interpretation is complete input too, not silently dropped
        # because fact records happened to fit better than narrative metadata.
        pending.extend(("interpretation", {"segment_ids": p["segment_ids"],
            **{k: v for k, v in p["content"].items() if k != "observations"}})
            for p in understanding["reading_parts"])
        pending.extend(("catalog", deepcopy(row)) for row in self._inputs["catalog"])
        pending.extend(("historical_thread", deepcopy(row)) for row in understanding.get("historical_open_threads", []))
        groups, current = [], []
        def payload(records):
            value = self._payload("integrate", IntegrationResponse, slots=slots,
                source=[v for k, v in records if k == "source"],
                ledger=[v for k, v in records if k == "ledger"], routes=candidate,
                extra={**extra, "reading_interpretations": [v for k, v in records if k == "interpretation"],
                       "catalog_records": [v for k, v in records if k == "catalog"],
                       "historical_open_threads": [v for k, v in records if k == "historical_thread"],
                       "catalog_scope": "完整目录条目分批处理；目录是备选机制，不要求把所有标签加入故事。"})
            value["valid_type_catalog"] = self._catalog_manifest()
            return value
        while pending:
            kind, item = pending.pop(0)
            if self._fits(system, payload(current + [(kind, item)])):
                current.append((kind, item))
            elif current:
                groups.append(payload(current))
                current = []
                pending.insert(0, (kind, item))
            elif kind == "source" and len(item["text"]) > 1:
                children = bisect_segment(item)
                for child in children:
                    self._additional_spans[child["id"]] = child
                pending[0:0] = [("source", child) for child in children]
            else:
                raise SourceRouteCapacityError("单个完整证据/解释与候选无法同时容纳，未删减记录；可换更大容量连接恢复。")
        if current:
            groups.append(payload(current))
        return groups

    def _design_partitioned(self, operation, slots, understanding, old):
        """Close the current candidate revision over every complete input range.

        Prefer all routes together. Only a candidate that cannot coexist with a
        smallest evidence view is split by route. Changed candidates invalidate
        earlier coverage; a stable sweep closes the exact revision being saved.
        """
        all_spans = understanding["segments"]
        ordered = [s for s in all_spans if s.get("scope") == "listed_chapter"]
        endpoint_span = max(ordered, key=lambda s: (s.get("index") or 0, s["end"])) if ordered else all_spans[-1]
        prior = [content_view(r) for r in old if operation == "revise_routes" and r["route_id"] in slots]
        source = [endpoint_span]
        seed_extra = {"source_unit_index": understanding["source_index"],
            "draft_scope": "这是待整合的候选；随后处理全部来源、旧方案与完整观察，不能宣布完成。",
            "narrative_endpoint": understanding["narrative_endpoint"]}
        while True:
            seed_payload = self._payload(operation, RouteResponse, slots=slots, source=source,
                                         routes=prior, extra=seed_extra)
            seed_payload["valid_type_catalog"] = self._catalog_manifest()
            seed_payload["catalog_scope"] = "本轮先提供全部选项名称；完整目录定义将在后续证据整合逐条完整提供，此候选尚未完成。"
            if self._fits(self._system(operation), seed_payload):
                break
            if len(source[0]["text"]) > 1:
                _, last = bisect_segment(source[0])
                self._additional_spans[last["id"]] = last
                source = [last]
                continue
            if len(slots) > 1:
                midpoint = len(slots) // 2
                left = self._design_partitioned(operation, slots[:midpoint], understanding, old)
                # Include the newly proposed first half for substantive comparison.
                proposed = self._route_ids(left[0], slots[:midpoint], operation, old)
                right = self._design_partitioned(operation, slots[midpoint:], understanding, old + proposed)
                return left[0] + right[0], left[1] or right[1], left[2] or right[2], left[3] + right[3]
            raise SourceRouteCapacityError("单条路线、作者要求及最小终点依据无法同时容纳，未裁剪关键内容。")
        try:
            seed = self._request(operation, seed_payload, RouteResponse)
        except SourceRouteIncompleteError:
            if len(slots) > 1:
                middle = len(slots) // 2
                left = self._design_partitioned(operation, slots[:middle], understanding, old)
                right = self._design_partitioned(operation, slots[middle:], understanding, old + self._route_ids(left[0], slots[:middle], operation, old))
                return left[0] + right[0], left[1] or right[1], left[2] or right[2], left[3] + right[3]
            pages, selected, reason, followups = self._paged_route(operation, slots[0], seed_payload)
            seed = {"routes": pages, "recommended_slot": selected, "recommendation_reason": reason,
                    "followup_actions": followups, "defer_current": False}
        if seed["defer_current"]:
            raise _Replan(seed["followup_actions"])
        validate_routes(seed["routes"], slots=slots,
                        source_ids={s["id"] for s in source} | {ref for r in prior for ref in r["source_refs"]},
                        catalog=self._inputs["catalog"])
        candidate = seed["routes"]
        seen = set()
        while True:
            before = digest(candidate)
            if before in seen:
                raise SourceRouteProtocolError("候选在相同版本间重复变化；完整方案与证据已保存，可恢复继续。")
            seen.add(before)
            changed, coverage = False, []
            try:
                payloads = self._integration_payloads(understanding, candidate, slots)
            except SourceRouteCapacityError:
                if len(slots) == 1:
                    raise
                midpoint = len(slots) // 2
                left = self._design_partitioned(operation, slots[:midpoint], understanding, old)
                proposed = self._route_ids(left[0], slots[:midpoint], operation, old)
                right = self._design_partitioned(operation, slots[midpoint:], understanding, old + proposed)
                return left[0] + right[0], left[1] or right[1], left[2] or right[2], left[3] + right[3]
            self._save(stage="integrating", completed=0, total=len(payloads))
            for index, payload in enumerate(payloads):
                # A previous batch can change the candidate size; recheck actual
                # capacity rather than sending a payload planned for an old draft.
                payload["comparison_routes"] = candidate
                result = self._request("integrate", payload, IntegrationResponse)
                if result["evidence_requests"]:
                    requested = set(result["evidence_requests"])
                    if not requested <= self._source_ids(understanding):
                        raise SourceRouteProtocolError("证据请求超出本来源范围。")
                    requested.update(ref for row in understanding["ledger"] if row["id"] in requested for ref in row["source_refs"])
                    all_records = all_spans + understanding["ledger"] + understanding.get("additional_segments", []) + list(self._additional_spans.values())
                    payload["requested_evidence"] = [r for r in all_records if r["id"] in requested]
                    result = self._request("integrate", payload, IntegrationResponse)
                    if result["evidence_requests"]:
                        raise SourceRouteCapacityError("需要进一步的关联证据，取证请求和候选已保留。")
                validate_routes(result["routes"], slots=slots, source_ids=self._source_ids(understanding),
                                catalog=self._inputs["catalog"], complete=False)
                replacements = {r["slot"]: r for r in result["routes"]}
                revised = [replacements.get(r["slot"], r) for r in candidate]
                if digest(revised) != digest(candidate):
                    candidate, changed, coverage = revised, True, []
                    # Repack every remaining source against the new exact version.
                    # Starting another sweep is conservative when the model cannot
                    # prove the semantic effect is confined to one node.
                    break
                coverage.append({"input_view_sha256": digest(payload), "candidate_sha256": digest(candidate)})
                self._save(completed=index + 1)
            if not changed:
                for prior_route in old:
                    comparison = self._payload("integrate", IntegrationResponse, slots=slots, routes=candidate,
                        extra={"historical_comparison": [prior_route],
                               "scope_instruction": ("本轮修订原方案：保留作者未要求改变的方向，检查因果兼容性。" if operation == "revise_routes" else "比较新增路线与这一份完整旧方案，确保实质差异。")
                               + "只可修改当前候选，不得修改历史；无需修改返回 routes=[]。"})
                    comparison["valid_type_catalog"] = self._catalog_manifest()
                    result = self._request("integrate", comparison, IntegrationResponse)
                    if result["evidence_requests"]:
                        raise SourceRouteProtocolError("历史比较要求补充证据，当前请求已保存。")
                    validate_routes(result["routes"], slots=slots, source_ids=self._source_ids(understanding),
                                    catalog=self._inputs["catalog"], complete=False)
                    replacements = {r["slot"]: r for r in result["routes"]}
                    revised = [replacements.get(r["slot"], r) for r in candidate]
                    if digest(revised) != digest(candidate):
                        candidate, changed, coverage = revised, True, []
                        break
            self.store.write({"candidate": candidate, "coverage": coverage},
                             "runs", self._run_id, "candidate-" + digest(slots) + ".json")
            if not changed:
                break
        return candidate, seed["recommended_slot"], seed["recommendation_reason"], seed["followup_actions"]

    def run(self, run_id: str, client, cancel: threading.Event | None = None) -> dict:
        # A SourceStoryDesign may be shared by an HTTP adapter. Execution-local
        # fields must never let concurrent books/runs share models or candidates.
        execution = SourceStoryDesign(self.library, self.source_id)
        return execution._run(run_id, client, cancel)

    def _run(self, run_id: str, client, cancel: threading.Event | None = None) -> dict:
        self._run_id, self._client, self._cancel = run_id, client, cancel
        state = self.store.state(run_id)
        if state["status"] == "ready":
            return state
        root = self.store.path("runs", run_id)
        with lease(root / ".run.lock", blocking=False):
            self._state = self.store.state(run_id)
            if self._state["status"] == "failed":
                self._state["recovery_revision"] = self._state.get("recovery_revision", 0) + 1
            self._inputs = self.store.read("runs", run_id, "inputs.json")
            if self._inputs.get("sha256") != digest({k: v for k, v in self._inputs.items() if k != "sha256"}):
                raise SourceRouteError("设计输入快照校验失败。")
            pause = self.store.path("runs", run_id, "pause.json")
            if pause.exists():
                pause.unlink()
            self._limit = input_limit(client, negotiate=True)
            self._additional_spans = {}
            self._automatic_retry_used = False
            self._executed_actions = []
            self._available_route_ids = set()
            self._output_reserve = getattr(client, "source_design_output_reserve_bytes", 4096)
            self._save(status="running", error=None)
            try:
                self._execute()
            except _Paused:
                self._save(status="paused", error=None)
            except SourceRouteError as exc:
                self._save(status=exc.status, error=str(exc))
            except Exception:
                self._save(status="failed", error="整书设计遇到技术错误，来源、方案和模型回复已保留。")
                raise
            return self.status(run_id)

    def _action(self, action):
        operation = action["operation"]
        value = {"operation": operation, "target_route_ids": action.get("target_route_ids", []),
                 "requested_count": action.get("requested_count") or (2 if operation == "expand_routes" else 3)}
        if action.get("author_quote"):
            value["author_quote"] = action["author_quote"]
        return value

    def _action_key(self, action):
        value = self._action(action)
        return digest({"operation": value["operation"], "targets": sorted(value["target_route_ids"]),
                       "count": value["requested_count"] if value["operation"] in {"initial_routes", "expand_routes"} else None})

    def _record_plan(self, actions, *, deferred):
        value = {"schema": "source-author-action-plan.v1", "actions": actions, "deferred": deferred,
                 "author_text": self._inputs["request"]["feedback"],
                 "parent_route_set_sha256": self._inputs["request"].get("base_route_set_sha256"),
                 "completed_actions": self._executed_actions}
        self.store.immutable(value, "runs", self._run_id, "action-plans", digest(value) + ".json")

    def _execute(self):
        request = self._inputs["request"]
        base = self._inputs["base_route_set"]
        old = deepcopy(base["routes"]) if base else []
        self._available_route_ids = {r["route_id"] for r in old}
        understanding = self.get_understanding(base["understanding_sha256"]) if base else None
        actions = [self._action(a) for a in (request["requested_actions"] or [{"operation": request["operation"],
            "target_route_ids": request["target_route_ids"], "requested_count": request["requested_count"]}])]
        recommendation = base.get("recommended_route_id", "") if base else ""
        reason = base.get("recommendation_reason", "") if base else ""
        finished = set()
        # Complete-source reading and design share one paid call when possible.
        if not understanding and len(actions) == 1 and actions[0]["operation"] == "initial_routes":
            slots = [f"new_{i + 1}" for i in range(actions[0]["requested_count"])]
            spans = self._inputs["snapshot"]["segments"]
            payload = self._payload("initial_routes", InitialResponse, slots=slots, source=spans)
            if self._fits(self._system("initial_routes"), payload) or self._saved_request("initial_routes", payload):
                self._save(stage="designing", total=len(slots), completed=0)
                try:
                    result = self._request("initial_routes", payload, InitialResponse)
                except SourceRouteCapacityError:
                    if self._fits(self._system("initial_routes"), payload):
                        raise
                    result = None
                except SourceRouteIncompleteError:
                    result = None
                if result is not None:
                    content = result["understanding"]
                    observations = bind_observations(content, spans)
                    understanding = self._understanding([{"segment_ids": [s["id"] for s in spans], "content": content, "ledger": observations}], spans)
                    if result["defer_current"]:
                        self._record_plan(result["followup_actions"], deferred=True)
                        actions = [self._action(a) for a in result["followup_actions"]]
                    else:
                        validate_routes(result["routes"], slots=slots, source_ids=self._source_ids(understanding), catalog=self._inputs["catalog"])
                        old = self._route_ids(result["routes"], slots, "initial_routes", [])
                        mapping = dict(zip([c["slot"] for c in result["routes"]], [r["route_id"] for r in old]))
                        recommendation = mapping.get(result["recommended_slot"], "")
                        reason = result["recommendation_reason"]
                        finished.add(self._action_key(actions[0]))
                        self._executed_actions.append(actions[0])
                        self._save(completed=len(slots))
                        self._record_plan(result["followup_actions"], deferred=False)
                        actions = [self._action(a) for a in result["followup_actions"]]
        if understanding is None:
            understanding = self._read()
        action_index = 0
        deferred_plans = set()
        while action_index < len(actions):
            self._check()
            action = actions[action_index]
            action_key = self._action_key(action)
            if action_key in finished:
                action_index += 1
                continue
            operation = action["operation"]
            self._available_route_ids = {r["route_id"] for r in old}
            if not set(action["target_route_ids"]) <= self._available_route_ids:
                raise SourceRouteProtocolError("后续动作的目标已不属于当前路线集合，旧结果保留。")
            if operation == "revisit_source":
                understanding = self._read(parent=understanding)
                finished.add(action_key)
                self._executed_actions.append(action)
                # Source correction updates the derived proposals automatically;
                # historical versions are immutable and remain selectable.
                if old and not any(a["operation"] == "revise_routes" for a in actions[action_index + 1:]):
                    actions.insert(action_index + 1, self._action({"operation": "revise_routes",
                        "target_route_ids": action["target_route_ids"] or [r["route_id"] for r in old]}))
                action_index += 1
                continue
            if operation == "revise_routes":
                slots = action["target_route_ids"] or [r["route_id"] for r in old]
            else:
                slots = [f"action_{action_index}_new_{i + 1}" for i in range(action["requested_count"])]
            self._save(stage="designing", completed=0, total=len(slots))
            try:
                content, selected, note, followups = self._design(operation, slots, understanding, old)
            except _Replan as plan:
                self._record_plan(plan.actions, deferred=True)
                plan_key = digest([self._executed_actions, plan.actions])
                replacement = [self._action(a) for a in plan.actions if self._action_key(a) not in finished]
                if not replacement or plan_key in deferred_plans:
                    raise SourceRouteProtocolError("补充意见的动作解释重复且无进展；计划、原话和已有方案已保存。")
                deferred_plans.add(plan_key)
                actions[action_index:action_index + 1] = replacement
                continue
            updated = self._route_ids(content, slots, operation, old)
            if operation == "revise_routes":
                replacements = {r["route_id"]: r for r in updated}
                old = [replacements.get(r["route_id"], r) for r in old]
            elif operation == "initial_routes":
                old = updated
            else:
                old.extend(updated)
            mapping = dict(zip([r["slot"] for r in content], [r["route_id"] for r in updated]))
            recommendation = mapping.get(selected, recommendation)
            reason = note or reason
            self._record_plan(followups, deferred=False)
            finished.add(action_key)
            self._executed_actions.append(action)
            queued = {self._action_key(a) for a in actions[action_index + 1:]}
            for followup in followups:
                key = self._action_key(followup)
                if key not in finished | queued:
                    actions.append(self._action(followup))
                    queued.add(key)
            self._save(completed=len(slots))
            action_index += 1
        if self._additional_spans:
            body = {k: v for k, v in understanding.items() if k != "sha256"}
            additional = {s["id"]: s for s in understanding.get("additional_segments", [])}
            additional.update(self._additional_spans)
            body["additional_segments"] = list(additional.values())
            body["parent_understanding_sha256"] = understanding["sha256"]
            understanding = self.store.put_artifact("understandings", body)
        self._save(stage="saving", understanding_sha256=understanding["sha256"])
        value = self.store.put_artifact("route-sets", {
            "schema": "source-route-set.v1", "source_id": self.source_id,
            "source_sha256": self._inputs["snapshot"]["source_sha256"],
            "understanding_sha256": understanding["sha256"], "mode": request["mode"],
            "parent_route_set_sha256": request.get("base_route_set_sha256"),
            "author_context": request["author_context"], "feedback_history": self._inputs["feedback_history"],
            "routes": old, "recommended_route_id": recommendation,
            "recommendation_reason": reason, "catalog": self._inputs["catalog"],
            "executed_actions": deepcopy(self._executed_actions),
            "created_at": self._state["created_at"], "run_id": self._run_id,
        })
        self._save(status="ready", stage="ready", route_set_sha256=value["sha256"], error=None)
