"""Immutable reference-image selection for one approved cover refinement."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .cover_sources import _safe_path
from .cover_store import CoverConflict
from .image_binary import aspect_ratio_matches, content_type, dimensions
from .author_requirements import build_requirements, prompt_requirements, validate_requirements

MAX_REFINEMENT_PROMPT = 5000
MAX_REFERENCE_BYTES = 40 * 1024 * 1024


def normalize_refinement_prompt(value):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_REFINEMENT_PROMPT:
        raise ValueError(f"请填写 1–{MAX_REFINEMENT_PROMPT} 字的封面优化要求。")
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
    # Frozen schema-1 approvals retain their exact request on retry. New
    # approvals use bound_edit_prompt so the author's words reach the editor.
    return (
        "Edit the supplied reference image into the approved novel cover artwork below. "
        "This is a refinement of that existing image, not an unrelated new image. "
        "Preserve recognizable character appearance, pose, composition, setting and visual style "
        "where the approved plan does not request a change. Apply the approved changes precisely. "
        "The source-verified approved plan governs any conflict with the reference image. "
        "Follow its exact title policy; a text-free artwork must remain text-free for later title composition. "
        "Produce exactly one final edited image.\n\nAPPROVED COVER PLAN:\n" + approved_prompt
    )


def refinement_requirements(scene_plan: dict, prompt: str, *, source_sha256: str = "") -> dict:
    prior = scene_plan.get("_poster_refinement") or scene_plan.get("_image_refinement") or {}
    snapshot = prior.get("author_requirements") or scene_plan.get("_author_requirements")
    entries = []
    if snapshot is not None:
        entries = validate_requirements(snapshot, source_sha256=source_sha256)["entries"]
    elif prior.get("prompt"):
        entries = [{"id": "prior-refinement", "text": prior["prompt"], "source": "saved_refinement"}]
    entries.append({"id": f"refinement-{len(entries) + 1}", "text": prompt, "source": "author_refinement"})
    return build_requirements("cover_refinement", entries, source_sha256=source_sha256)


def bound_edit_prompt(approved_prompt: str, metadata: dict) -> str:
    if metadata.get("schema_version") == 1:
        return edit_prompt(approved_prompt)
    if metadata.get("schema_version") != 2:
        raise CoverConflict("精修请求版本无效。")
    requirements = validate_requirements(metadata.get("author_requirements"), scope="cover_refinement")
    if not requirements["entries"] or requirements["entries"][-1]["text"] != metadata.get("prompt"):
        raise CoverConflict("精修要求与保存的作者补充不一致。")
    return (
        "Edit this reference into one novel-cover image. The approved plan is a realization of the "
        "author's request, not authority to ignore that request. Preserve unspecified visual elements; "
        "explicit changes override old pose, composition, palette and styling. Preserve story identities, "
        "ages, core relationships and the plan's exact title/text-free policy; art edits cannot rewrite canon.\n"
        "AUTHOR REQUIREMENTS:\n" + json.dumps(prompt_requirements(requirements), ensure_ascii=False)
        + "\nAPPROVED VISUAL REALIZATION:\n" + approved_prompt
    )
