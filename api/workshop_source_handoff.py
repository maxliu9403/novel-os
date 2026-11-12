"""Durable route selection -> discussion; no legacy analysis migration."""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import threading
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, StringConstraints, model_serializer
from typing import Annotated, Literal

from core.creation_mode import SourceMode, source_policy
from core.creative_types import StrictModel, digest
from core.reference_assets import atomic_json
from core.source_route_context import (VERSION, RULES, _managed, make_packet, save_packet,
                                       validate_context, execution_policy)
from .routes import get_service
from .services import ProjectService
from .source_routes import design_for
from .source_library import store_for as source_store
from .workshop import SessionInput, TurnInput, WorkshopService, _now

router = APIRouter(prefix="/api/workshop/source-route-handoffs", tags=["workshop"])
_LOCK = threading.RLock()


class RouteHandoffInput(StrictModel):
    operation_id: str = Field(min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    draft_id: str | None = Field(default=None, min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    source_id: str = Field(pattern=r"^source-[0-9a-f]{64}$")
    route_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    route_id: str = Field(min_length=1)
    latest_route_set_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    pending_feedback_run_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    mode: SourceMode | None = None
    use_cover: bool = True
    input: SessionInput
    author_note: Annotated[str, StringConstraints(strip_whitespace=False)] = ""
    session_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    revision: int | None = Field(default=None, ge=0)
    adoption_scope: Literal['core_and_payoffs'] | None = None

    @model_serializer(mode='wrap')
    def serialize(self, handler):
        value = handler(self)
        if self.adoption_scope is None:
            value.pop('adoption_scope', None)
        return value


@contextmanager
def _locked(service):
    directory = _managed(service.root, "source-handoffs")
    directory.mkdir(parents=True, exist_ok=True)
    with _LOCK, _managed(directory, ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield directory
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def freeze_route_source(projects, body):
    design = design_for(projects, body.source_id)
    selected_set = design.get_route_set(body.route_set_sha256)
    if body.mode is not None and body.mode != selected_set["mode"]:
        raise ValueError("路线创作模式已变化，请重新选择对应版本。")
    route = next((r for r in selected_set["routes"] if r["route_id"] == body.route_id), None)
    if route is None:
        raise ValueError("所选路线不属于此版本。")
    latest = design.get_route_set(body.latest_route_set_sha256) if body.latest_route_set_sha256 else selected_set
    if latest["mode"] != selected_set["mode"] or latest["source_sha256"] != selected_set["source_sha256"]:
        raise ValueError("最新意见与所选路线的来源或创作模式不同。")
    understanding = latest["understanding_sha256"]
    packet = make_packet(design.export_evidence(understanding), source_id=body.source_id,
                         source_sha256=selected_set["source_sha256"], understanding_sha256=understanding)
    manifest = source_store(projects).get(body.source_id)
    context = {"schema": VERSION, "source_id": body.source_id, "title": manifest["title"],
               "source_sha256": selected_set["source_sha256"], "understanding_sha256": understanding,
               "route_set_sha256": body.route_set_sha256, "mode": selected_set["mode"],
               "selected_proposal": deepcopy(route), "proposal_status": "direction_selected_details_proposed",
               "proposal_understanding_sha256": selected_set["understanding_sha256"],
               "latest_route_set_sha256": latest["sha256"],
               "author_feedback": deepcopy(latest.get("feedback_history", [])),
               "author_context": deepcopy(latest.get("author_context", {})),
               "selection": {"action_id": body.operation_id, "route_id": body.route_id,
                             "scope": "discussion_direction", "author_note": body.author_note},
               "evidence": {"schema": packet["schema"], "sha256": packet["sha256"]},
               "execution_policy": execution_policy(),
               "cover_asset": manifest.get("cover_asset") if body.use_cover else None,
               "rule": source_policy({"mode": selected_set["mode"]}) + "\n" + RULES}
    if body.pending_feedback_run_id:
        pending = design.get_run_context(body.pending_feedback_run_id)
        if pending['source_sha256'] != context['source_sha256'] or pending['request']['mode'] != context['mode']:
            raise ValueError('补充意见属于其他来源或创作模式。')
        context.update(author_feedback=deepcopy(pending['feedback_history']),
                       author_context=deepcopy(pending['request']['author_context']),
                       pending_feedback_run_id=body.pending_feedback_run_id,
                       pending_feedback_notice='作者补充已接收；旧路线尚未据此修订，请在本轮协调。')
    context['context_sha256'] = digest(context)
    return validate_context(context), packet


def _seed_direction(data, context):
    state = (data.get("creative_design") or {}).get("story_state")
    if state is None:
        return
    route = context["selected_proposal"]
    direction = {"area": "direction", "summary": route["title"], "status": "settled",
                 "evidence": [{"author_action_ref": context["selection"]["action_id"]}]}
    if state.get('map'):
        state.setdefault('route_selection_history', []).append({
            'map': deepcopy(state['map']), 'pending': deepcopy(state.get('pending')),
            'revision': data['revision'], 'selection': deepcopy(context['selection'])})
        state['map']['nodes'] = [direction if node['area'] == 'direction' else node for node in state['map']['nodes']]
        state['map'].update(focus='direction', source_revision=data['revision'], reviewed_revision=data['revision'])
        state['pending'] = None
        state['route_reconciliation'] = {
            'action_id': context['selection']['action_id'],
            'rule': 'The author explicitly changed the discussion direction. Other nodes are historical designs '
                    'to reconcile with this new direction, not proof of compatibility. Preserve all compatible '
                    'explicit author requirements; revise model assumptions automatically before asking for '
                    'a genuinely irreconcilable author decision. Prior questions and map are retained in history.'}
        return
    state["map"] = {"synopsis": route["reader_promise"], "focus": "opening", "changes": [],
                    "source_revision": data["revision"], "reviewed_revision": data["revision"],
                    "nodes": [{"area": area, "summary": route["title"] if area == "direction" else "围绕选中方向继续完善。",
                               "status": "settled" if area == "direction" else "proposed",
                               "evidence": [{"author_action_ref": context["selection"]["action_id"]}]
                               if area == "direction" else []}
                              for area in ("direction", "opening", "middle", "climax", "ending")]}


def _read_session(service, session_id):
    # A handoff GET must not run WorkshopService.get's interruption migration.
    with service._locked(session_id) as data:
        return deepcopy(data)


def _public(record, service):
    data = _read_session(service, record["session_id"])
    status = "running" if data["status"] == "running" else "error" if data["status"] == "error" else "ready"
    from .workshop import _session_response
    return {"operation_id": record["operation_id"], "session_id": data["id"],
            "status": status, "stage": record["stage"], "session": _session_response(data)}


def handoff(projects, body):
    service = WorkshopService(projects)
    payload = body.model_dump()
    fingerprint = digest(payload)
    if body.input.source_book is not None:
        raise HTTPException(422, "整书路线使用独立来源绑定，请勿同时提交旧分析选择。")
    with _locked(service) as directory:
        path = _managed(directory, body.operation_id + ".json")
        draft_path = _managed(directory, "draft-" + body.draft_id + ".json") if body.draft_id else None
        if not path.exists() and draft_path is not None and draft_path.exists():
            draft = json.loads(draft_path.read_text("utf-8"))
            if draft["payload_sha256"] != digest({k: v for k, v in payload.items() if k != "operation_id"}):
                raise HTTPException(409, "此创建草稿已进入讨论；意见保留，请在已有讨论继续。")
            # Continue the original operation, including a crash before session
            # creation; merely returning its public view would strand the draft.
            body = body.model_copy(update={'operation_id': draft['operation_id']})
            payload = body.model_dump()
            fingerprint = digest(payload)
            path = _managed(directory, body.operation_id + '.json')
        if path.exists():
            record = json.loads(path.read_text("utf-8"))
            if record["request_sha256"] != fingerprint:
                raise HTTPException(409, "同一操作标识不能用于不同路线或作者意见。")
        else:
            # Validate all expensive/read-only bindings before allocating a session.
            if body.session_id is not None and body.revision is None:
                raise HTTPException(422, "绑定现有讨论需要其当前版本。")
            context, packet = freeze_route_source(projects, body)
            initial = body.input.model_dump()
            initial['creation_flow'] = 'genre_tags.v2'
            frozen_types = service._type_snapshot(initial)
            if not initial.get("audience_channel") or not initial.get("creative_types"):
                raise HTTPException(422, "请选择读者方向、一个 Genre 和 1–2 个 Tags。")
            identifier = body.session_id or uuid.uuid5(uuid.NAMESPACE_URL, str(service.root.resolve()) + "/" + body.operation_id).hex
            record = {"operation_id": body.operation_id, "request_sha256": fingerprint,
                      "session_id": identifier, "stage": "prepared", "created_at": _now(),
                      "source_context": context, "evidence_packet": packet,
                      "first_turn_operation_id": "source-" + hashlib.sha256(body.operation_id.encode()).hexdigest()[:40],
                      "input": initial, "type_snapshot": frozen_types, "author_note": body.author_note,
                      "development_version": "creative-development.v1",
                      "first_turn_message": body.author_note or "请在我选中的整书方向上继续完善人物选择、情绪看点和因果骨架，并主动推荐适合完整故事的总篇幅、每章字数及章数，供我修改。"}
            if context.get('cover_asset'):
                from core.reference_assets import ReferenceAssets
                original = ReferenceAssets(source_store(projects).path(body.source_id) / 'images').read(context['cover_asset']['asset_id'])
                record['cover_snapshot'] = ReferenceAssets(_managed(directory, 'covers')).put(original, origin='source_book')
            atomic_json(path, record)
            if draft_path is not None:
                atomic_json(draft_path, {"operation_id": body.operation_id,
                            "payload_sha256": digest({k: v for k, v in payload.items() if k != "operation_id"})})

        identifier = record["session_id"]
        context, packet = record["source_context"], record["evidence_packet"]
        session_path = service._path(identifier)
        if not (session_path / "session.json").exists():
            if body.session_id is not None:
                raise HTTPException(404, "找不到要绑定的讨论。")
            service.create(body.input, _session_id=identifier, _source_context=context, _source_packet=packet,
                           _frozen_type_snapshot=record['type_snapshot'])
        with service._locked(identifier) as data:
            applied = data.get("source_route_operations", {}).get(body.operation_id)
            if not applied:
                if body.session_id is not None:
                    service._available(data, body.revision)
                    if data.get("restart_required"):
                        raise HTTPException(409, "请先处理当前基础设定修改；新路线和意见已保存。")
                    data.setdefault("source_context_history", []).append({"revision": data["revision"],
                        "source_context": deepcopy(data.get("source_context")), "input": deepcopy(data["input"])})
                    initial = deepcopy(record["input"])
                    snapshot = service._bind_frozen_types(initial, record['type_snapshot'])
                    data["input"] = initial
                    design = data.get("creative_design")
                    if design:
                        design["policy"]["type_snapshot"] = snapshot
                        from .workshop_design import new_design
                        design['policy']['guidance'] = new_design(initial, snapshot)['policy']['guidance']
                        design["policy_sha256"] = hashlib.sha256(json.dumps(design["policy"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                    data["revision"] += 1
                save_packet(session_path, packet)
                if record.get('cover_snapshot'):
                    from core.reference_assets import ReferenceAssets
                    original = ReferenceAssets(_managed(directory, 'covers')).read(record['cover_snapshot']['asset_id'])
                    ReferenceAssets(_managed(session_path, 'source-cover')).put(original, origin='source_book')
                data["source_context"] = deepcopy(context)
                data.setdefault("source_route_operations", {})[body.operation_id] = fingerprint
                data["source_route_selection"] = deepcopy(context["selection"])
                if body.adoption_scope:
                    # New selection consent is separate from the immutable old
                    # source/evidence contract. Legacy operations keep their scope.
                    data['source_route_selection']['adoption_scope'] = body.adoption_scope
                    data['source_route_selection']['adopted_at_message_index'] = len(data.get('messages', []))
                data.update(prepared=None, ready_for_confirmation=False, design_needs_revision=True)
                _seed_direction(data, context)
                service._save(data)
            record.update(stage="session_created", session_revision=data["revision"])
            atomic_json(path, record)
        # An older saved first-turn operation must replay its exact old payload.
        message = record.get('first_turn_message') or record["author_note"] or "请在我选中的整书方向上继续完善人物选择、情绪看点和因果骨架。"
        # Persist the exact accepted first-turn revision before dispatch. A replay
        # sends the same operation identity even after the session has advanced.
        if "first_turn_revision" not in record:
            record["first_turn_revision"] = record["session_revision"]
            atomic_json(path, record)
        service.turn(identifier, TurnInput(revision=record["first_turn_revision"], message=message,
                                          operation_id=record["first_turn_operation_id"], intent="discuss"),
                     **({'_source_action': {'author_note': record['author_note'], 'action_id': record['operation_id']}}
                        if record.get('development_version') else {}))
        record["stage"] = "first_turn_queued"
        atomic_json(path, record)
        return _public(record, service)


def carry_route_cover(session_path, project_path, source):
    """Use the discussion-owned immutable image, independent of library lifetime."""
    if not source.get('cover_asset'):
        return
    from core.reference_assets import ReferenceAssets
    from core.creation_mode import cover_intent
    original = ReferenceAssets(_managed(session_path, 'source-cover')).read(source['cover_asset']['asset_id'])
    store = ReferenceAssets(project_path / 'outputs/private/references')
    saved = store.put(original, origin='source_book')
    store.select(saved['asset_id'], cover_intent(source['mode']))


@router.post("", status_code=202)
def create_handoff(body: RouteHandoffInput, projects: ProjectService = Depends(get_service)):
    try:
        return handoff(projects, body)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{operation_id}")
def get_handoff(operation_id: str, projects: ProjectService = Depends(get_service)):
    import re
    if not re.fullmatch(r"[a-zA-Z0-9_-]{8,64}", operation_id):
        raise HTTPException(404, "找不到路线交接。")
    service = WorkshopService(projects)
    try:
        path = _managed(service.root, "source-handoffs", operation_id + ".json")
        return _public(json.loads(path.read_text("utf-8")), service)
    except (ValueError, OSError):
        raise HTTPException(404, "找不到已保存的路线交接。") from None
