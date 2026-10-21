"""Read-only library projection: a saved image is not an approved cover."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from core.cover_sources import CoverSourceContext
from core.cover_store import CoverStore
from core.cover_workflow import CoverWorkflow

from . import cover_direction_tasks, cover_generation_tasks, cover_repair_tasks
from .jobs import runner
from .models import ProjectCoverSummary
from .project_task_summary import parse_task_time


def _safe_file(root: Path, path: Path) -> bool:
    """Never project content through an alias to another owner's files."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    current = root
    for part in relative.parts:
        if part in {"..", "."}:
            return False
        current = current / part
        if current.is_symlink():
            return False
    return current.is_file() and current.resolve().is_relative_to(root.resolve())


def _read(root: Path, path: Path) -> dict | None:
    if not _safe_file(root, path):
        return None
    try:
        value = json.loads(path.read_text("utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _timestamp(value: object) -> float:
    parsed = parse_task_time(value)
    return parsed.timestamp() if parsed else 0


def _direction_problem(direction: dict | None) -> str | None:
    if direction is None:
        return "修复方案对应的美术方向无法读取，请重新检查修复任务。"
    status = direction.get("status")
    if status == "stale":
        return "美术方向已失效，需要核对来源并重新规划。"
    if status == "rejected":
        return "美术方向已被拒绝，需要修改后重新规划。"
    if not isinstance(status, str) or status not in {"awaiting_approval", "approved"}:
        return "美术方向状态无法识别，请在封面工作室检查。"
    return None


def project_cover_summary(project: Path, project_id: str, *, workspace_id: str) -> ProjectCoverSummary:
    """Derive a coarse next-action state without loading sources or writing files.

    Active work takes precedence. Otherwise the newest set/direction/task owns
    the next action; an older formal selection remains the library artwork.
    """
    project = Path(project).absolute()
    root = project / "outputs" / "covers"
    if any(path.is_symlink() for path in (project, project / "outputs", root)):
        return ProjectCoverSummary(status="needs_attention", detail="封面资料路径异常，请检查作品资料。")
    store = CoverStore(project)
    sets = []
    damaged = False
    for path in (root / "sets").glob("cover-*.json"):
        if not _safe_file(project, path):
            damaged = True
            continue
        try:
            item = store.load(path.stem)
            if item.project_id != project_id:
                damaged = True
                continue
            sets.append(item)
        except (OSError, ValueError, TypeError, KeyError):
            damaged = True
    sets.sort(key=lambda item: (_timestamp(item.created_at), item.cover_set_id), reverse=True)
    latest = sets[0] if sets else None
    active_record = _read(project, store.index_path) or {}
    active = next((item for item in sets if item.cover_set_id == active_record.get("cover_set_id")), None)
    selected = next((item for item in active.candidates if item.candidate_id == active.selected_candidate_id
                     and item.status == "selected"), None) if active else None

    def image_exists(candidate):
        return (bool(candidate.media_id and candidate.relative_path and candidate.sha256)
                and _safe_file(project, project / candidate.relative_path))

    selected = selected if selected and image_exists(selected) else None
    preview = selected or (next((item for item in latest.candidates
                               if item.status in {"ready", "selected"} and image_exists(item)), None) if latest else None)
    preview_set = active if selected else latest
    summary = ProjectCoverSummary(
        candidate_count=len(latest.candidates) if latest else 0,
        cover_set_id=preview_set.cover_set_id if preview else (latest.cover_set_id if latest else None),
        candidate_id=preview.candidate_id if preview else None,
        image_url=f"/api/projects/{quote(project_id, safe='')}/media/{quote(preview.media_id, safe='')}/raw" if preview else None,
        image_kind="selected" if selected else "candidate" if preview else None,
    )

    def result(status, detail):
        if selected and status != "selected":
            detail += "；已保留原选定封面。"
        return summary.model_copy(update={"status": status, "detail": detail})

    jobs = runner.running_for_project(project_id, project_path=str(project.resolve()))
    if any(str(job.get("kind", "")).startswith("cover.") for job in jobs):
        return result("processing", "封面任务正在后台处理，可打开工作室查看进度。")

    workflow = CoverWorkflow(CoverSourceContext(root, f"{workspace_id}:project:{project_id}"), store)
    events = []
    from . import poster_tasks
    from core.cover_poster_store import list_plans
    try:
        for task in poster_tasks.list_tasks(workflow, runner):
            if task.get("status") in {"queued", "running"}:
                return result("processing", "封面任务正在后台处理，已完成的图片可直接选择。")
            if task.get("status") in {"failed", "interrupted"}:
                events.append((_timestamp(task.get("finished_at") or task.get("updated_at")), "task", task))
        for plan in list_plans(workflow):
            events.append((_timestamp(plan.get("created_at")), "poster_plan", plan))
    except (OSError, ValueError, TypeError, KeyError):
        damaged = True
    latest_repair = None
    for module in (cover_direction_tasks, cover_generation_tasks, cover_repair_tasks):
        try:
            task = module.latest(workflow, runner)
        except (OSError, ValueError, TypeError, KeyError):
            damaged = True
            continue
        if module is cover_repair_tasks:
            latest_repair = task
        if task and task.get("status") in {"queued", "running", "planning", "generating"}:
            return result("processing", "封面任务正在后台处理，可打开工作室查看进度。")
        if task and task.get("status") in {"failed", "interrupted"}:
            events.append((_timestamp(task.get("finished_at") or task.get("updated_at") or task.get("created_at")), "task", task))
        if task and task.get("status") == "awaiting_approval":
            events.append((_timestamp(task.get("updated_at") or task.get("created_at")), "repair_proposal", task))
    directions = {}
    for path in (root / "directions").glob("direction-*.json"):
        if path.name.endswith(".brief.json"):
            continue
        value = _read(project, path)
        if value and value.get("direction_id") == path.stem:
            directions[path.stem] = value
            events.append((_timestamp(value.get("created_at")), "direction", value))
    for path in (root / "analyses").glob("analysis-*/analysis.json"):
        value = _read(project, path)
        if value and value.get("analysis_id") == path.parent.name:
            events.append((_timestamp(value.get("created_at")), "analysis", value))
    if latest:
        events.append((_timestamp(latest.created_at), "set", latest))
    events.sort(key=lambda event: event[0], reverse=True)
    if events:
        _, kind, value = events[0]
        if kind == "poster_plan" and (not latest or _timestamp(value.get("created_at")) > _timestamp(latest.created_at)):
            return result("awaiting_review", "封面方案已保存，可按方案生成候选图。")
        if kind == "repair_proposal":
            direction_id = value.get("direction_id")
            problem = _direction_problem(directions.get(direction_id) if isinstance(direction_id, str) else None)
            if problem:
                return result("needs_attention", problem)
            return result("awaiting_review", "单张修复方案已保存，等待审核后执行。")
        if kind == "task" and value.get("status") in {"failed", "interrupted"}:
            # A selection/change made after a failure resolves its old warning.
            if not latest or _timestamp(latest.updated_at) < _timestamp(value.get("finished_at") or value.get("updated_at")):
                return result("needs_attention", "最近的封面任务失败或中断，请查看原因后继续。")
        if kind == "direction" and (not latest or _timestamp(value.get("created_at")) > _timestamp(latest.created_at)):
            problem = _direction_problem(value)
            if problem:
                return result("needs_attention", problem)
            binding = value.get("source_binding") or {}
            if not isinstance(binding, dict):
                return result("needs_attention", "美术方向来源记录异常，请在封面工作室检查。")
            if "single_candidate_repair" in binding:
                if (latest_repair and latest_repair.get("direction_id") == value["direction_id"]
                        and latest_repair.get("status") == "awaiting_approval"):
                    return result("awaiting_review", "单张修复方案已保存，请在修复任务中审核后执行。")
                return result("needs_attention", "单张修复方向需要对应的有效修复任务，不能用于普通整批生成。")
            return result("awaiting_review", "美术方向已批准，可生成封面候选。" if value["status"] == "approved"
                          else "美术方向已保存，等待审核确认。")
        if kind == "analysis" and (not latest or _timestamp(value.get("created_at")) > _timestamp(latest.created_at)):
            if value.get("coverage_status") != "complete":
                return result("needs_attention", "骨架分析尚未完成，请在封面工作室继续处理。")
            return result("not_started", "骨架分析已完成，可以创建设计方向。")

    if latest:
        if latest.status == "stale":
            return result("needs_attention", "封面来源已变化，需要重新核对。")
        if selected and active and active.cover_set_id == latest.cover_set_id:
            return result("selected", "已选定作品封面。")
        usable = [item for item in latest.candidates if item.status in {"ready", "selected"} and image_exists(item)]
        damaged_candidates = any(item.status in {"failed", "pending"}
                                 or (item.quality_report or {}).get("status") == "blocked"
                                 or (item.status in {"ready", "selected"} and not image_exists(item))
                                 for item in latest.candidates)
        if damaged_candidates or not usable:
            return result("needs_attention", "候选中存在待修复、失败或未完成的封面。")
        return result("awaiting_review", "候选已保存，可直接选择喜欢的封面。")
    if damaged:
        return result("needs_attention", "部分封面资料无法读取，请在封面工作室检查。")
    return result("not_started", "尚未设计封面。")
