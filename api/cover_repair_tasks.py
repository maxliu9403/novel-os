"""Durable, explicitly approved single-slot repair. Reads never replay paid work."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
import re
import uuid

from core.cover_store import CoverConflict
from core.cover_workflow import digest
from core.cover_refinement import normalize_refinement_prompt, reference_for_candidate, validate_reference
from . import cover_generation_tasks as generation

_PROCESS_ID = uuid.uuid4().hex
_ID = re.compile(r"^repair-task-[0-9a-f]{32}$")
_ACTIVE = {"planning", "generating"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _path(flow, task_id):
    if not _ID.fullmatch(task_id):
        raise ValueError("Invalid cover repair task id")
    directory = flow.store.root / "repair_tasks"
    path = directory / f"{task_id}.json"
    if directory.is_symlink() or path.is_symlink():
        raise ValueError("Invalid cover repair task path")
    return path


def _read(flow, task_id):
    value = json.loads(_path(flow, task_id).read_text("utf-8"))
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or not isinstance(value.get("status"), str)
            or not isinstance(value.get("kind"), str)
            or value.get("status") not in _ACTIVE | {"awaiting_approval", "completed", "failed", "interrupted"}
            or value.get("kind") not in {"design", "render", "typography", "evaluation", "refinement"}
            or type(value.get("image_calls")) is not int or value["image_calls"] not in {0, 1}
            or any(not isinstance(value.get(key), str) for key in
                   ("created_at", "updated_at", "parent_cover_set_id", "parent_candidate_id", "parent_sha256", "concept_id"))):
        raise ValueError("Invalid cover repair task record")
    if value["kind"] == "refinement":
        if (not value.get("refinement_prompt") or not isinstance(value.get("reference_image"), dict)
                or value["image_calls"] != 1):
            raise ValueError("Invalid cover refinement task record")
        normalize_refinement_prompt(value["refinement_prompt"])
    if value.get("task_id") != task_id or value.get("owner_scope") != flow.context.owner_scope:
        raise FileNotFoundError("Cover repair task does not belong to this owner")
    return value


def _write(flow, value):
    flow.store._write_json(_path(flow, value["task_id"]), value)


def _records(flow):
    result = []
    for path in (flow.store.root / "repair_tasks").glob("repair-task-*.json"):
        try:
            result.append(_read(flow, path.stem))
        except (OSError, ValueError):
            continue
    return sorted(result, key=lambda x: (x["created_at"], x["task_id"]), reverse=True)


def _project(value, runner):
    result = dict(value)
    if value["status"] in _ACTIVE:
        job = runner.get(value.get("job_id", "")) if value.get("process_id") == _PROCESS_ID else None
        if not (job and job.get("status") == "running" and
                (job.get("repair_task_id") or (job.get("meta") or {}).get("repair_task_id")) == value["task_id"]):
            result.update(status="interrupted", error="单张修复已中断；已保存原图和进度，不会自动重新调用模型。")
    return {key: val for key, val in result.items() if key not in {"owner_scope", "process_id", "parent", "job_id"}}


def get(flow, task_id, runner):
    with generation._task_lock(flow):
        return _project(_read(flow, task_id), runner)


def latest(flow, runner):
    with generation._task_lock(flow):
        records = _records(flow)
        return _project(records[0], runner) if records else None


def for_candidate(flow, runner, cover_set_id, candidate_id):
    """Resolve a produced or provenance-verified retained result, read-only."""
    from core.cover_models import CoverSet
    from core.cover_repair_lineage import validate_lineage
    with generation._task_lock(flow):
        cover_set = flow.store.load(cover_set_id)
        records, visited = _records(flow), set()
        while cover_set.cover_set_id not in visited:
            visited.add(cover_set.cover_set_id)
            candidate = next((item for item in cover_set.candidates if item.candidate_id == candidate_id), None)
            if candidate is None:
                return None
            validate_lineage(flow.store, cover_set, candidate_id=candidate_id)
            match = next((task for task in records if task.get("cover_set_id") == cover_set.cover_set_id
                          and task.get("result_candidate_id") == candidate_id), None)
            if match:
                return {**_project(match, runner), "lookup_cover_set_id": cover_set_id,
                        "lookup_candidate_id": candidate_id}
            lineage = cover_set.repair_lineage or {}
            if not lineage or lineage["target_concept_id"] == candidate.concept_id:
                return None
            # Only unchanged retained slots may traverse the exact frozen parent.
            # validate_lineage verifies its safe path, digest, owner and slots.
            checksum = lineage["parent_snapshot_sha256"]
            payload = json.loads((flow.store.root / "repair_sources" / f"{checksum}.json").read_text("utf-8"))
            if digest(payload) != checksum:
                raise CoverConflict("Cover repair parent snapshot changed")
            cover_set = CoverSet.from_dict(payload)
        raise CoverConflict("Cover repair lineage contains a cycle")


class RefinementRetryConflict(CoverConflict):
    def __init__(self, message, *, code, **context):
        super().__init__(message)
        self.detail = {"code": code, "message": message, **context}


def retry(flow, runner, task_id, *, project_id="", session_id=""):
    """Prepare one separately approved retry; never replay a failed paid job."""
    with generation._task_lock(flow), flow.store.mutation_lock():
        task = _read(flow, task_id)
        if task["kind"] != "refinement" or _project(task, runner)["status"] not in {"failed", "interrupted"}:
            raise CoverConflict("仅失败或中断的原图精修可以重试。")
        # Stable identities also recover a direction saved before its task write
        # failed. The old task and its consumed approval are never changed.
        retry_key = uuid.uuid5(uuid.NAMESPACE_URL, f"{flow.context.owner_scope}:refinement-retry:{task_id}").hex
        retry_id, direction_id = f"repair-task-{retry_key}", f"direction-{retry_key}"
        if _path(flow, retry_id).exists():
            successor = _read(flow, retry_id)
            if successor.get("retry_of_task_id") != task_id:
                raise CoverConflict("精修重试记录的来源不一致。")
            return _project(successor, runner)
        assert_idle(flow, runner)
        batch = generation.latest(flow, runner)
        from . import cover_direction_tasks
        # Do not invert direction-task -> store lock order. Atomic task records
        # can be projected without taking that task's separate mutation lock.
        direction_records = cover_direction_tasks._records(flow)
        direction_task = cover_direction_tasks._project(direction_records[0], runner) if direction_records else None
        if (batch and batch["status"] in {"running", "queued"}
                or direction_task and direction_task["status"] in {"running", "queued"}):
            raise CoverConflict("本作品封面任务正在执行，请等待完成后重试。")
        parent, candidate, concept = _candidate(flow, task["parent_cover_set_id"], task["parent_candidate_id"])
        if parent.project_id != (project_id or f"workshop-{session_id}"):
            raise CoverConflict("封面批次不属于当前作品。")
        reference = reference_for_candidate(flow.store.project_path, candidate, concept)
        if reference != task["reference_image"]:
            raise CoverConflict("精修原图与失败任务不一致，请重新选择原候选。")
        if task.get("cover_set_id") and task.get("result_candidate_id"):
            result_set = flow.store.load(task["cover_set_id"])
            result = next((item for item in result_set.candidates if item.candidate_id == task["result_candidate_id"]), None)
            if result and result.sha256 and result.relative_path:
                raise RefinementRetryConflict("已保存精修图片，请查看结果并重新评估，无需再次出图。",
                    code="cover_refinement_result_available",
                    result_context={"cover_set_id": result_set.cover_set_id, "candidate_id": result.candidate_id})
            artwork = ((result.quality_report or {}).get("assets") or {}) if result else {}
            if artwork.get("artwork_sha256") and artwork.get("artwork_relative_path"):
                raise RefinementRetryConflict("已保存精修原画，请查看原画并修复排版，无需再次出图。",
                    code="cover_refinement_result_available",
                    result_context={"cover_set_id": result_set.cover_set_id, "candidate_id": result.candidate_id})
        context = {"parent_cover_set_id": parent.cover_set_id, "parent_candidate_id": candidate.candidate_id,
                   "expected_revision": parent.revision, "refinement_prompt": task["refinement_prompt"]}

        def replan(message):
            raise RefinementRetryConflict(message, code="cover_refinement_replan_required", replan_context=context)

        if digest(parent.to_dict()) != task["parent_sha256"]:
            replan("原封面或审核已变化，请使用保留的原图和精修要求重新规划。")
        proposal = task.get("proposal")
        if not isinstance(proposal, dict) or not task.get("direction_id"):
            replan("上次未完成精修方案，请使用保留的原图和要求重新规划。")
        if (digest(proposal) != task.get("proposal_sha256")
                or proposal.get("direction_id") != task["direction_id"]
                or proposal.get("image_calls") != 1
                or proposal.get("refinement_prompt") != task["refinement_prompt"]
                or proposal.get("reference_image") != reference
                or digest(task.get("quality_report")) != task["quality_sha256"]):
            raise CoverConflict("精修提案完整性校验失败，请重新创建。")
        saved_copy = flow.store._direction_path(direction_id).exists()
        try:
            direction, _ = flow.validate(task["direction_id"], require_latest=not saved_copy,
                require_approval=bool(task.get("approved_at") or flow.approval(task["direction_id"])),
                approved_hash=proposal["direction_sha256"])
        except (ValueError, FileNotFoundError):
            replan("已审精修方案已失效，请使用保留的原图和要求重新规划；不会自动调用模型。")
        repair_binding = direction.source_binding.get("single_candidate_repair") or {}
        expected_binding = {"cover_set_id": parent.cover_set_id, "candidate_id": candidate.candidate_id,
            "concept_id": task["concept_id"], "parent_sha256": task["parent_sha256"],
            "quality_sha256": task["quality_sha256"], "refinement_prompt_sha256": digest(task["refinement_prompt"]),
            "reference_image_sha256": reference["sha256"]}
        if (direction.direction_sha256 != proposal["direction_sha256"]
                or direction.brief_sha256 != proposal["brief_sha256"]
                or next((p.to_dict() for p in direction.plans if p.concept_id == task["concept_id"]), None) != proposal.get("plan")
                or any(repair_binding.get(key) != value for key, value in expected_binding.items())):
            raise CoverConflict("精修方案与原图、要求或目标候选的绑定不一致。")
        binding = deepcopy(direction.source_binding)
        binding["refinement_retry"] = {"task_id": retry_id, "retry_of_task_id": task_id,
            "direction_id": direction.direction_id, "direction_sha256": direction.direction_sha256}
        clone = replace(direction, direction_id=direction_id, source_binding=binding)
        brief = flow.store.load_direction_brief(direction.direction_id)
        if saved_copy:
            try:
                recovered = flow.store.require_latest_direction(direction_id)
            except CoverConflict:
                replan("待恢复的精修方案已被新方向取代，请使用保留的原图和要求重新规划。")
            expected, actual = clone.to_dict(), recovered.to_dict()
            for field in ("direction_sha256", "created_at", "status"):
                expected.pop(field, None)
                actual.pop(field, None)
            if actual != expected or recovered.status != "awaiting_approval" or flow.approval(direction_id):
                raise CoverConflict("待恢复的精修重试方向不一致，请重新规划。")
            # create_direction writes its brief after the direction. Restore only
            # that verified companion if the earlier write failed at this point.
            if not flow.store._direction_brief_path(direction_id).exists():
                flow.store._write_json(flow.store._direction_brief_path(direction_id), brief)
            clone = recovered
        else:
            clone = flow.store.create_direction(clone, brief=brief)
        flow.validate(clone.direction_id)
        renewed = deepcopy(proposal)
        renewed.update(direction_id=clone.direction_id, direction_sha256=clone.direction_sha256)
        now = _now()
        successor = deepcopy(task)
        successor.pop("approved_at", None)
        successor.pop("result_quality_status", None)
        successor.update(task_id=retry_id, retry_of_task_id=task_id, proposal_reused=True,
            direction_id=clone.direction_id, proposal=renewed, proposal_sha256=digest(renewed),
            status="awaiting_approval", phase="awaiting_approval", process_id=_PROCESS_ID, job_id="",
            cover_set_id=None, result_candidate_id=None, error=None,
            created_at=now, updated_at=now, finished_at=None)
        _write(flow, successor)
        return _project(successor, runner)


def assert_idle(flow, runner):
    for task in _records(flow):
        if _project(task, runner)["status"] in _ACTIVE:
            raise CoverConflict("本作品已有单张封面修复正在执行，请等待完成。")


def _candidate(flow, set_id, candidate_id, revision=None):
    parent = flow.store.load(set_id)
    if revision is not None and parent.revision != revision:
        raise CoverConflict("封面批次已变化，请刷新后重新创建修复。")
    candidate = next((x for x in parent.candidates if x.candidate_id == candidate_id), None)
    if candidate is None:
        raise FileNotFoundError("Cover candidate not found")
    if candidate.status not in {"ready", "failed", "selected", "rejected"}:
        raise CoverConflict("候选仍在生成中。")
    concept = next(x for x in parent.concepts if x.concept_id == candidate.concept_id)
    flow.validate_candidate(set_id, candidate_id)
    return parent, candidate, concept


def _parent(flow, task):
    current = flow.store.load(task["parent_cover_set_id"])
    if digest(current.to_dict()) != task["parent_sha256"]:
        raise CoverConflict("原封面或审核已变化；此修复提案已过期，请重新创建。")
    return current


def _dispatch(flow, runner, task, operation, *, project_id, session_id, phase):
    task.update(status=phase, process_id=_PROCESS_ID, error=None, updated_at=_now(), finished_at=None)
    _write(flow, task)
    dispatched = False

    def checkpoint(changes):
        with generation._task_lock(flow):
            current = _read(flow, task["task_id"])
            if current["status"] != phase or current["process_id"] != _PROCESS_ID:
                raise RuntimeError("Repair task no longer owns execution")
            current.update(**changes, updated_at=_now())
            _write(flow, current)

    def execute():
        try:
            # Submission is not permission to spend until its exact job identity
            # has been saved. A lost dispatch write must poison the worker.
            with generation._task_lock(flow):
                live = _read(flow, task["task_id"])
                if (not dispatched or live.get("job_id") != task.get("job_id")
                        or live["status"] != phase or live["process_id"] != _PROCESS_ID):
                    raise RuntimeError("Repair dispatch checkpoint was not durably saved")
            result = operation(checkpoint)
            checkpoint(result)
            return result
        except Exception as exc:
            with generation._task_lock(flow):
                current = _read(flow, task["task_id"])
                current.update(status="failed", error=str(exc), updated_at=_now(), finished_at=_now())
                _write(flow, current)
            raise

    try:
        task["job_id"] = runner.submit("cover.repair", execute,
            meta={"project_id": project_id, "project_path": str(flow.store.project_path) if project_id else "",
                  "session_id": session_id, "repair_task_id": task["task_id"]},
            unique_key=f"cover-repair:{task['task_id']}:{phase}")
        _write(flow, task)
        dispatched = True
    except Exception as exc:
        task.update(status="failed", error=str(exc), finished_at=_now())
        try:
            _write(flow, task)
        except (OSError, ValueError):
            pass
        raise
    return _project(task, runner)


def start(flow, runner, director, *, cover_set_id, candidate_id, expected_revision, project_id="", session_id="", refinement_prompt=None):
    refinement_prompt = normalize_refinement_prompt(refinement_prompt)
    with generation._task_lock(flow), flow.store.mutation_lock():
        for previous in _records(flow):
            if _project(previous, runner)["status"] in _ACTIVE:
                if (previous["parent_cover_set_id"] != cover_set_id or previous["parent_candidate_id"] != candidate_id
                        or previous.get("refinement_prompt") != refinement_prompt):
                    raise CoverConflict("本作品已有另一张封面或另一组要求正在处理，请等待完成后再提交。")
                return _project(previous, runner)
        batch = generation.latest(flow, runner)
        if batch and batch["status"] in {"running", "queued"}:
            raise CoverConflict("整批封面正在生成，请等待完成后修复单张。")
        parent, candidate, concept = _candidate(flow, cover_set_id, candidate_id, expected_revision)
        expected_owner = project_id or f"workshop-{session_id}"
        if parent.project_id != expected_owner:
            raise CoverConflict("封面批次不属于当前作品。")
        for previous in _records(flow):
            if (previous["status"] == "awaiting_approval"
                    and previous["parent_cover_set_id"] == cover_set_id
                    and previous["parent_candidate_id"] == candidate_id
                    and previous.get("refinement_prompt") == refinement_prompt
                    and previous["parent_sha256"] == digest(parent.to_dict())):
                try:
                    if previous["image_calls"]:
                        flow.validate(previous["direction_id"])
                    if previous["kind"] == "refinement":
                        validate_reference(flow.store.project_path, previous["reference_image"])
                except (ValueError, FileNotFoundError) as exc:
                    previous.update(status="failed", error=f"修复提案已失效，请重新规划此图：{exc}",
                                    updated_at=_now(), finished_at=_now())
                    _write(flow, previous)
                    continue
                return _project(previous, runner)
        report = deepcopy(candidate.quality_report or {})
        kind = "refinement" if refinement_prompt else (report.get("repair_decision") or {}).get("kind")
        if kind not in {"design", "render", "typography", "evaluation", "refinement"}:
            raise CoverConflict("此候选没有可执行的正式修复建议，请先重新评估。")
        if (report.get("typography_amendment") or concept.scene_plan.get("_cover_import")):
            raise CoverConflict("此候选含独立排版修订或导入绑定，请先使用原排版修订流程核验。")
        binding = concept.scene_plan.get("_source_binding") or {}
        if not binding.get("direction_id"):
            raise CoverConflict("历史候选缺少完整来源绑定，请先创建并批准来源绑定方向。")
        direction, _ = flow.validate(binding["direction_id"], require_latest=False)
        reference = reference_for_candidate(flow.store.project_path, candidate, concept) if refinement_prompt else None
        if refinement_prompt:
            report["user_refinement_request"] = {"request": refinement_prompt,
                "reference_image_sha256": reference["sha256"],
                "rule": "Refine this existing image through a changed, source-supported target plan. Preserve unrequested elements and every other candidate. User preferences cannot create story facts."}
        now = _now()
        task = {"schema_version": 1, "task_id": f"repair-task-{uuid.uuid4().hex}",
            "owner_scope": flow.context.owner_scope, "process_id": _PROCESS_ID, "job_id": "",
            "status": "planning", "kind": kind, "phase": "planning", "parent_cover_set_id": cover_set_id,
            "parent_candidate_id": candidate_id, "candidate_number": next(i + 1 for i, x in enumerate(parent.candidates) if x.candidate_id == candidate_id),
            "parent_sha256": digest(parent.to_dict()), "quality_sha256": digest(report),
            "concept_id": concept.concept_id, "direction_id": None, "cover_set_id": None,
            "result_candidate_id": None, "proposal": None, "proposal_sha256": None,
            "image_calls": 1 if kind in {"design", "render", "refinement"} else 0,
            "refinement_prompt": refinement_prompt, "reference_image": reference,
            "quality_report": report, "error": None,
            "created_at": now, "updated_at": now, "finished_at": None}

        def plan(checkpoint):
            _parent(flow, task)
            if kind in {"design", "render", "refinement"}:
                # Rendering repairs also return through reviewed direction planning;
                # new spend never replenishes an old immutable approval.
                base = direction.to_dict()
                # A sibling may already have been repaired under a newer per-slot
                # direction. Review the actual retained portfolio, not the old
                # target direction's historical versions of those siblings.
                base["plans"] = [{key: deepcopy(value) for key, value in item.scene_plan.items()
                                  if not key.startswith("_")} for item in parent.concepts]
                for key in ("source_binding", "compiled_recipes", "direction_id", "direction_sha256", "created_at", "status", "novelty_report"):
                    base.pop(key, None)
                repair = {"base": base, "quality_report": report, "concept_id": concept.concept_id,
                    "cover_set_id": cover_set_id, "candidate_id": candidate_id,
                    "parent_sha256": task["parent_sha256"], "quality_sha256": task["quality_sha256"]}
                if refinement_prompt:
                    validate_reference(flow.store.project_path, reference)
                    repair.update(refinement_prompt=refinement_prompt, reference_image=reference)
                revised = flow.plan(direction.source_binding["analysis_id"], director,
                    count=len(direction.plans), freshness_policy=direction.source_binding["freshness_policy"],
                    targeted_repair=repair,
                    progress_sink=lambda event: checkpoint({"phase": event["phase"]}))
                _parent(flow, task)
                flow.validate(revised.direction_id)
                proposed = next(x.to_dict() for x in revised.plans if x.concept_id == concept.concept_id)
                target_before = next(x for x in base["plans"] if x["concept_id"] == concept.concept_id)
                if kind in {"design", "refinement"} and proposed == target_before:
                    raise CoverConflict("修复规划未修改失败方向，不能重复使用旧方案出图。")
                proposal = {"direction_id": revised.direction_id, "direction_sha256": revised.direction_sha256,
                    "brief_sha256": revised.brief_sha256, "plan": proposed, "image_calls": 1}
                review = revised.source_binding.get("campaign_evidence_review") or {}
                if review.get("policy_version") == "cover-commercial-review.v1":
                    proposal["commercial_review"] = {"policy_version": review["policy_version"],
                        "advisories": deepcopy(review.get("advisories", []))}
                if refinement_prompt:
                    proposal.update(refinement_prompt=refinement_prompt, reference_image=reference)
            else:
                proposal = {"direction_id": direction.direction_id, "direction_sha256": direction.direction_sha256,
                    "brief_sha256": direction.brief_sha256, "plan": next(x.to_dict() for x in direction.plans if x.concept_id == concept.concept_id),
                    "image_calls": 0}
            return {"status": "awaiting_approval", "phase": "awaiting_approval",
                    "proposal": proposal, "direction_id": proposal["direction_id"],
                    "proposal_sha256": digest(proposal)}
        return _dispatch(flow, runner, task, plan, project_id=project_id, session_id=session_id, phase="planning")


def approve(flow, runner, covers, task_id, *, expected_proposal_sha256, confirm_image_calls,
            project_id="", session_id="", image_service_factory=None):
    with generation._task_lock(flow), flow.store.mutation_lock():
        task = _read(flow, task_id)
        if (type(confirm_image_calls) is not int or expected_proposal_sha256 != task.get("proposal_sha256")
                or confirm_image_calls != task["image_calls"]):
            raise CoverConflict("请审核当前修复方案并确认准确的新增图片调用次数。")
        if task["status"] in {"generating", "completed", "failed"} and task.get("approved_at"):
            return _project(task, runner)
        if task["status"] != "awaiting_approval":
            raise CoverConflict("修复提案尚未完成或已中断，不能批准出图。")
        assert_idle(flow, runner)
        batch = generation.latest(flow, runner)
        if batch and batch["status"] in {"running", "queued"}:
            raise CoverConflict("整批封面正在生成，请等待完成。")
        proposal = task["proposal"]
        try:
            _parent(flow, task)
            flow.validate_candidate(task["parent_cover_set_id"], task["parent_candidate_id"])
            if digest(proposal) != task["proposal_sha256"]:
                raise CoverConflict("修复提案完整性校验失败。")
            if task["kind"] == "refinement":
                if (proposal.get("refinement_prompt") != task["refinement_prompt"]
                        or proposal.get("reference_image") != task["reference_image"]):
                    raise CoverConflict("精修要求或原图与待批准方案不一致，请重新创建。")
                validate_reference(flow.store.project_path, task["reference_image"])
            if task["image_calls"]:
                flow.approve(proposal["direction_id"], expected_brief_sha256=proposal["brief_sha256"],
                    approved_direction_sha256=proposal["direction_sha256"], confirm_source=True,
                    single_candidate=task["concept_id"])
        except (ValueError, FileNotFoundError) as exc:
            task.update(status="failed", error=f"修复提案已过期：{exc}", updated_at=_now(), finished_at=_now())
            _write(flow, task)
            raise
        task["approved_at"] = _now()

        def execute(checkpoint):
            parent = _parent(flow, task)
            flow.validate_candidate(task["parent_cover_set_id"], task["parent_candidate_id"])
            media_owner = project_id or f"workshop-{session_id}"
            kwargs = {"cover_root": flow.store.root}
            if task["image_calls"]:
                image_covers = image_service_factory() if image_service_factory else covers
                brief, concepts, options = flow.generation_arguments(proposal["direction_id"],
                    proposal["direction_sha256"], single_candidate=task["concept_id"])
                if task["kind"] == "refinement":
                    options["refinement"] = {"prompt": task["refinement_prompt"], "reference_image": task["reference_image"]}
                with generation.progress_scope(lambda event: checkpoint({"phase": event["phase"]})):
                    result = image_covers.repair_one(media_owner, flow.store.project_path, parent,
                        task["parent_candidate_id"], brief=brief, concepts=concepts, **options, **kwargs,
                        checkpoint=checkpoint)
                target = next(x for x in result.candidates if x.concept_id == task["concept_id"])
            else:
                # Review/recompose an isolated sibling batch. Original selected media
                # and delivery remain untouched even when a title changes locally.
                original = next(x for x in parent.candidates if x.candidate_id == task["parent_candidate_id"])
                from core.cover_repair_lineage import record_parent
                lineage = record_parent(flow.store, parent, candidate_id=original.candidate_id, operation=task["kind"])
                clone = flow.store.create(replace(parent, cover_set_id=f"cover-{uuid.uuid4().hex}", revision=0,
                    repair_lineage=lineage,
                    selected_candidate_id="", status="ready", created_at=_now(), updated_at=_now(),
                    candidates=tuple(replace(x,
                        status="ready" if x.status == "selected" else x.status,
                        quality_report={key: value for key, value in (x.quality_report or {}).items()
                                        if key != "human_acceptance"}) if x.candidate_id == original.candidate_id
                        else replace(x, status="ready") if x.status == "selected" else x
                        for x in parent.candidates)))
                checkpoint({"cover_set_id": clone.cover_set_id, "result_candidate_id": original.candidate_id,
                            "phase": "composing" if task["kind"] == "typography" else "reviewing"})
                result = covers.reevaluate_candidate(media_owner, flow.store.project_path, clone.cover_set_id,
                    original.candidate_id, expected_revision=clone.revision, preserve_siblings=True, **kwargs)
                target = next(x for x in result.candidates if x.candidate_id == original.candidate_id)
            return {"status": "failed" if target.status == "failed" else "completed", "phase": "completed",
                    "cover_set_id": result.cover_set_id, "result_candidate_id": target.candidate_id,
                    "result_quality_status": (target.quality_report or {}).get("status"),
                    "error": target.error or None, "finished_at": _now()}
        return _dispatch(flow, runner, task, execute, project_id=project_id, session_id=session_id, phase="generating")
