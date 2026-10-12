"""Immutable reference-image selection for one approved cover refinement."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .cover_sources import _safe_path
from .cover_store import CoverConflict
from .image_binary import aspect_ratio_matches, content_type, dimensions

MAX_REFINEMENT_PROMPT = 2000
MAX_REFERENCE_BYTES = 40 * 1024 * 1024


def normalize_refinement_prompt(value):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_REFINEMENT_PROMPT:
        raise ValueError("请填写 1–2000 字的封面优化要求。")
    return value.strip()


def validate_reference(project: Path, reference: dict) -> Path:
    if not isinstance(reference, dict) or reference.get("kind") not in {"artwork", "cover"}:
        raise CoverConflict("精修原图记录无效，请重新选择候选图。")
    path = _safe_path(Path(project).resolve(), reference.get("relative_path", ""))
    if not path.is_file() or path.stat().st_size > MAX_REFERENCE_BYTES:
        raise CoverConflict("精修原图不可用或过大，请重新读取封面。")
    with path.open("rb") as stream:
        data = stream.read(MAX_REFERENCE_BYTES + 1)
    width, height = dimensions(data)
    if (len(data) > MAX_REFERENCE_BYTES or content_type(data) not in {"image/png", "image/jpeg"}
            or content_type(data) != reference.get("content_type")
            or hashlib.sha256(data).hexdigest() != reference.get("sha256")
            or not aspect_ratio_matches(width, height)
            or [width, height] != [reference.get("width"), reference.get("height")]):
        raise CoverConflict("精修原图已变化或未通过完整性检查，请重新创建方案。")
    return path


def reference_for_candidate(project, candidate, concept):
    if candidate.status not in {"ready", "selected", "rejected"} or not candidate.sha256:
        raise CoverConflict("请先选择一张已生成的候选图进行精修。")
    recipe = concept.scene_plan.get("_compiled_recipe") or {}
    assets = (candidate.quality_report or {}).get("assets") or {}
    if recipe.get("title_render_mode") == "composed":
        if not assets.get("artwork_sha256"):
            raise CoverConflict("此封面缺少可验证的原画，暂不能精修；请先生成保留原画的新候选。")
        reference = {"kind": "artwork", "sha256": assets["artwork_sha256"],
            "relative_path": assets.get("artwork_relative_path", ""), "content_type": assets.get("artwork_content_type", "")}
    elif recipe.get("title_render_mode") == "native":
        reference = {"kind": "cover", "sha256": candidate.sha256,
            "relative_path": candidate.relative_path, "content_type": candidate.content_type}
    else:
        raise CoverConflict("历史候选缺少可验证的绘制方式，请先创建来源绑定的新方向。")
    path = _safe_path(Path(project).resolve(), reference["relative_path"])
    if not path.is_file() or path.stat().st_size > MAX_REFERENCE_BYTES:
        raise CoverConflict("精修原图不可用或过大，请重新读取封面。")
    with path.open("rb") as stream:
        reference["width"], reference["height"] = dimensions(stream.read(MAX_REFERENCE_BYTES + 1))
    validate_reference(project, reference)
    return reference


def edit_prompt(approved_prompt: str) -> str:
    # Raw user requests pass through the reviewed direction first. They are not
    # forwarded as a second, potentially conflicting image-generation recipe.
    return (
        "Edit the supplied reference image into the approved novel cover artwork below. "
        "This is a refinement of that existing image, not an unrelated new image. "
        "Preserve recognizable character appearance, pose, composition, setting and visual style "
        "where the approved plan does not request a change. Apply the approved changes precisely. "
        "The source-verified approved plan governs any conflict with the reference image. "
        "Follow its exact title policy; a text-free artwork must remain text-free for later title composition. "
        "Produce exactly one final edited image.\n\nAPPROVED COVER PLAN:\n" + approved_prompt
    )
