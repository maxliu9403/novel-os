"""Independently approved local typography; never an image-generation permission."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from PIL import Image, ImageColor, ImageOps

from .cover_store import CoverConflict
from .cover_typography import canonical_digest, compose_cover, prepare_title_layout, _region

SCHEMA = "cover-typography-amendment.v1"
RENDERER = "cover-local-layout.v1"
_ID = re.compile(r"typography-[0-9a-f]{32}\Z")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def validate_owner_root(project, root):
    """Check the un-resolved path before any lock or artifact can create files."""
    project = Path(project).resolve()
    root = Path(root).absolute()
    if not root.is_relative_to(project):
        raise CoverConflict("Typography cover root must stay in its owner")
    if any(part.is_symlink() for part in (root, *root.parents) if part.is_relative_to(project)):
        raise CoverConflict("Typography owner paths must not be symlinks")
    if not root.resolve().is_relative_to(project):
        raise CoverConflict("Typography cover root must stay in its owner")
    return root


def _path(root, amendment_id, name):
    if not _ID.fullmatch(amendment_id or "") or (name not in {
            "amendment.json", "approval.json", "preview.png", "review-artwork.png"}
            and not re.fullmatch(r"application-[0-9a-f]{32}\.json", name)):
        raise CoverConflict("Invalid typography amendment path")
    root = Path(root).absolute()
    if any(part.is_symlink() for part in (root, *root.parents)):
        raise CoverConflict("Typography owner paths must not be symlinks")
    path = root / "typography-amendments" / amendment_id / name
    if any(part.is_symlink() for part in (path.parent.parent, path.parent, path)):
        raise CoverConflict("Typography amendment paths must not be symlinks")
    if not path.resolve().is_relative_to(root):
        raise CoverConflict("Typography amendment must stay in its cover owner")
    return path


def _write_once(path, data):
    """Publish complete bytes once; a crash cannot leave a partial approval."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".typography-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # link is an atomic create-if-absent, never an overwrite/rename.
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != data:
                raise CoverConflict("Typography amendment records are immutable")
    finally:
        Path(temporary).unlink(missing_ok=True)


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8")


def _recipe(concept):
    recipe = concept.scene_plan.get("_compiled_recipe") or {}
    supplied = recipe.get("recipe_sha256")
    if not supplied or canonical_digest({key: value for key, value in recipe.items() if key != "recipe_sha256"}) != supplied:
        raise CoverConflict("Typography amendment requires an intact approved recipe")
    if recipe.get("title_render_mode") != "composed" or recipe.get("image_prompt") != concept.generation_prompt:
        raise CoverConflict("Typography amendment requires a composed, unchanged image recipe")
    binding = concept.scene_plan.get("_source_binding") or {}
    if not binding.get("direction_id") or not binding.get("direction_sha256"):
        raise CoverConflict("Typography amendment requires immutable source approval binding")
    return recipe


def _binding(cover_set, candidate, concept):
    recipe = _recipe(concept)
    return {"project_id": cover_set.project_id, "cover_set_id": cover_set.cover_set_id,
            "candidate_id": candidate.candidate_id, "concept_id": concept.concept_id,
            "source_binding": deepcopy(concept.scene_plan["_source_binding"]),
            "recipe_sha256": recipe["recipe_sha256"],
            "generation_prompt_sha256": _sha(concept.generation_prompt.encode("utf-8"))}


def validate_parent(record, cover_set, candidate, concept):
    if record["binding"] != _binding(cover_set, candidate, concept):
        raise CoverConflict("Typography amendment owner, source or recipe changed")
    if candidate.status == "selected" or cover_set.selected_candidate_id == candidate.candidate_id:
        raise CoverConflict("Typography amendment cannot replace a selected candidate")
    if canonical_digest(candidate.to_dict()) != record["parent_candidate_sha256"]:
        raise CoverConflict("Typography amendment parent candidate changed")


def propose_amendment(root, cover_set, candidate, concept, artwork, proposal):
    """Render reviewable bytes without changing the candidate or its old recipe."""
    if "_cover_import" in concept.scene_plan:
        raise CoverConflict("Imported cover history cannot receive a local typography amendment")
    if candidate.status not in {"ready", "failed", "rejected"} or cover_set.selected_candidate_id == candidate.candidate_id:
        raise CoverConflict("Typography amendment requires an unselected retained artwork")
    if any(item.status == "pending" for item in cover_set.candidates):
        raise CoverConflict("Finish the active image batch before proposing typography")
    validate_candidate_amendment(root, cover_set, candidate, concept)
    assets = (candidate.quality_report or {}).get("assets") or {}
    if _sha(artwork) != assets.get("artwork_sha256"):
        raise CoverConflict("Typography amendment original artwork hash changed")
    allowed = {"schema_version", "mode", "title_layout", "artwork_region", "background_color"}
    if not isinstance(proposal, dict) or set(proposal) - allowed:
        raise ValueError("Typography amendment proposal contains unsupported fields")
    if proposal.get("schema_version", SCHEMA) != SCHEMA or proposal.get("mode") not in {"overlay", "contain_panel"}:
        raise ValueError("Typography amendment mode must be overlay or contain_panel")
    layout = prepare_title_layout(proposal.get("title_layout") or {}, expected_title=cover_set.brief.title)
    with Image.open(BytesIO(artwork)) as original:
        original.load()
        source = ImageOps.exif_transpose(original).convert("RGB")
    width, height = source.size
    transform = {"mode": proposal["mode"], "canvas_size": [width, height],
                 "source_crop": [0, 0, 1, 1], "placed_region": [0, 0, 1, 1]}
    review_artwork = artwork
    if proposal["mode"] == "overlay":
        if "artwork_region" in proposal or "background_color" in proposal:
            raise ValueError("Overlay cannot move, crop or recolor the original artwork")
    else:
        region = _region(proposal.get("artwork_region"), "artwork region")
        color = proposal.get("background_color")
        try:
            rgb = ImageColor.getrgb(color)
            if len(rgb) != 3:
                raise ValueError("opaque RGB required")
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("Contain panel requires an opaque solid background color") from exc
        x, y, rw, rh = region
        box = (round(x * width), round(y * height), round((x + rw) * width), round((y + rh) * height))
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError("Artwork region is smaller than a pixel")
        scale = min((box[2] - box[0]) / width, (box[3] - box[1]) / height)
        fitted = source.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.LANCZOS)
        left = box[0] + (box[2] - box[0] - fitted.width) // 2
        top = box[1] + (box[3] - box[1] - fitted.height) // 2
        placed = [left / width, top / height, fitted.width / width, fitted.height / height]
        tx, ty, tw, th = layout["region"]
        px, py, pw, ph = placed
        if tx < px + pw and tx + tw > px and ty < py + ph and ty + th > py:
            raise ValueError("Contain-panel title must stay outside the complete fitted artwork")
        canvas = Image.new("RGB", (width, height), rgb)
        canvas.paste(fitted, (left, top))
        output = BytesIO()
        canvas.save(output, format="PNG")
        review_artwork = output.getvalue()
        transform.update(artwork_region=region, background_color=color, placed_region=placed)
    composed = compose_cover(review_artwork, layout, expected_title=cover_set.brief.title, output_format="PNG")
    amendment_id = "typography-" + uuid.uuid4().hex
    record = {"schema_version": SCHEMA, "renderer_version": RENDERER,
        "amendment_id": amendment_id, "binding": _binding(cover_set, candidate, concept),
        "parent_candidate_sha256": canonical_digest(candidate.to_dict()),
        "parent_image_sha256": candidate.sha256, "parent_revision": cover_set.revision,
        "parent_attempt_history_sha256": canonical_digest(list(candidate.attempt_history)),
        "source_artwork_sha256": _sha(artwork), "transform": transform,
        "title_layout": layout, "title_layout_sha256": composed.title_layout_sha256,
        "font_sha256": composed.font_sha256, "preview_sha256": composed.final_cover_sha256,
        "review_artwork_sha256": _sha(review_artwork), "title_bounds": [list(x) for x in composed.text_bounds],
        "image_calls": 0, "created_at": datetime.now(timezone.utc).isoformat()}
    record["amendment_sha256"] = canonical_digest(record)
    _write_once(_path(root, amendment_id, "review-artwork.png"), review_artwork)
    _write_once(_path(root, amendment_id, "preview.png"), composed.data)
    _write_once(_path(root, amendment_id, "amendment.json"), _json_bytes(record))
    return record


def load_amendment(root, amendment_id, *, expected_sha256=None):
    try:
        record = json.loads(_path(root, amendment_id, "amendment.json").read_text("utf-8"))
        supplied = record.get("amendment_sha256")
        if (record.get("schema_version") != SCHEMA or record.get("renderer_version") != RENDERER
                or record.get("amendment_id") != amendment_id or record.get("image_calls") != 0
                or canonical_digest({k: v for k, v in record.items() if k != "amendment_sha256"}) != supplied
                or (expected_sha256 is not None and supplied != expected_sha256)):
            raise CoverConflict("Typography amendment hash or contract mismatch")
        pinned = prepare_title_layout(record["title_layout"], expected_title=record["title_layout"]["title"])
        if canonical_digest(pinned) != record["title_layout_sha256"] or pinned["font_sha256"] != record["font_sha256"]:
            raise CoverConflict("Typography amendment font or layout changed")
        for name, key in (("preview.png", "preview_sha256"), ("review-artwork.png", "review_artwork_sha256")):
            if _sha(_path(root, amendment_id, name).read_bytes()) != record[key]:
                raise CoverConflict("Typography amendment preview bytes changed")
        return record
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise CoverConflict("Invalid typography amendment record") from exc


def approve_amendment(root, record, cover_set, candidate, concept, *, confirm_layout):
    if confirm_layout is not True:
        raise CoverConflict("Confirm the exact typography preview before approval")
    record = load_amendment(root, record["amendment_id"], expected_sha256=record["amendment_sha256"])
    validate_parent(record, cover_set, candidate, concept)
    path = _path(root, record["amendment_id"], "approval.json")
    if path.exists():
        return approval_receipt(root, record)
    receipt = {"schema_version": SCHEMA, "amendment_id": record["amendment_id"],
        "amendment_sha256": record["amendment_sha256"], "preview_sha256": record["preview_sha256"],
        "binding_sha256": canonical_digest(record["binding"]), "confirmed_layout": True, "image_calls": 0,
        "approved_at": datetime.now(timezone.utc).isoformat()}
    receipt["approval_sha256"] = canonical_digest(receipt)
    _write_once(path, _json_bytes(receipt))
    return receipt


def approval_receipt(root, record):
    try:
        receipt = json.loads(_path(root, record["amendment_id"], "approval.json").read_text("utf-8"))
        if (receipt.get("schema_version") != SCHEMA or receipt.get("amendment_id") != record["amendment_id"]
                or receipt.get("amendment_sha256") != record["amendment_sha256"]
                or receipt.get("preview_sha256") != record["preview_sha256"]
                or receipt.get("binding_sha256") != canonical_digest(record["binding"])
                or receipt.get("confirmed_layout") is not True or receipt.get("image_calls") != 0
                or canonical_digest({k: v for k, v in receipt.items() if k != "approval_sha256"}) != receipt.get("approval_sha256")):
            raise CoverConflict("Typography amendment approval receipt changed")
        return receipt
    except FileNotFoundError as exc:
        raise CoverConflict("Typography amendment has not been approved") from exc


def validate_candidate_amendment(root, cover_set, candidate, concept):
    report = candidate.quality_report or {}
    marker = report.get("typography_amendment")
    latest = candidate.attempt_history[-1] if candidate.attempt_history else {}
    attempt_marker = latest.get("typography_amendment")
    # The independent application ledger detects deletion of both in-candidate
    # markers. Failed/uncommitted applications have no matching attempt ID.
    applied = []
    for path in (Path(root) / "typography-amendments").glob("typography-*/application-*.json"):
        ledger_path = _path(root, path.parent.name, path.name)
        try:
            ledger = json.loads(ledger_path.read_text("utf-8"))
        except (ValueError, OSError) as exc:
            raise CoverConflict("Invalid typography application ledger") from exc
        if ledger.get("attempt_id") == latest.get("attempt_id"):
            if canonical_digest({k: v for k, v in ledger.items() if k != "application_sha256"}) != ledger.get("application_sha256"):
                raise CoverConflict("Typography application ledger changed")
            applied.append(ledger)
    present = ("typography_amendment" in report or "typography_amendment" in latest
               or "typography_transform" in (report.get("assets") or {})
               or "typography_amendment" in (latest.get("quality_report") or {})
               or _ID.fullmatch(Path(candidate.relative_path).stem) or applied)
    if not present:
        return None
    if not isinstance(marker, dict) or not marker or marker != attempt_marker:
        raise CoverConflict("Typography amendment receipt is missing from candidate or attempt")
    record = load_amendment(root, marker.get("amendment_id"), expected_sha256=marker.get("amendment_sha256"))
    receipt = approval_receipt(root, record)
    if len(applied) != 1 or applied[0].get("marker") != marker or applied[0].get("binding") != record["binding"]:
        raise CoverConflict("Typography application ledger does not match the candidate")
    assets = report.get("assets") or {}
    if (record["binding"] != _binding(cover_set, candidate, concept)
            or marker.get("approval_sha256") != receipt["approval_sha256"]
            or candidate.sha256 != record["preview_sha256"] or latest.get("image_sha256") != candidate.sha256
            or latest.get("operation") != "typography_recomposition"
            or canonical_digest(list(candidate.attempt_history[:-1])) != record["parent_attempt_history_sha256"]
            or assets.get("artwork_sha256") != record["source_artwork_sha256"]
            or assets.get("title_layout_sha256") != record["title_layout_sha256"]
            or assets.get("font_sha256") != record["font_sha256"]
            or assets.get("final_cover_sha256") != record["preview_sha256"]
            or assets.get("review_artwork_sha256") != record["review_artwork_sha256"]
            or assets.get("typography_transform") != record["transform"]):
        raise CoverConflict("Candidate does not match its approved typography amendment")
    return record


def record_application(root, record, candidate):
    latest = candidate.attempt_history[-1]
    ledger = {"schema_version": SCHEMA, "binding": record["binding"],
              "attempt_id": latest["attempt_id"], "marker": latest["typography_amendment"]}
    ledger["application_sha256"] = canonical_digest(ledger)
    # Unique immutable attempts permit recovery after a crash between this write
    # and the candidate CAS. Orphan ledgers never match a committed attempt.
    suffix = latest["attempt_id"].removeprefix("composition-")
    _write_once(_path(root, record["amendment_id"], f"application-{suffix}.json"), _json_bytes(ledger))


def read_amendment_preview(root, amendment_id, *, cover_set_id, candidate_id):
    record = load_amendment(root, amendment_id)
    if record["binding"]["cover_set_id"] != cover_set_id or record["binding"]["candidate_id"] != candidate_id:
        raise CoverConflict("Typography preview belongs to another candidate")
    return _path(root, amendment_id, "preview.png").read_bytes()


def amendment_inputs(root, record, concept):
    """The evaluator sees the actual new geometry and layout, never stale type."""
    scene = deepcopy(concept.scene_plan)
    campaign = scene["campaign"]
    x, y, width, height = record["transform"]["placed_region"]
    for layer in campaign.get("layers", ()):
        lx, ly, lw, lh = layer["region"]
        layer["region"] = [x + lx * width, y + ly * height, lw * width, lh * height]
    campaign["title_layout"] = deepcopy(record["title_layout"])
    scene["title_safe_zone"] = "The separately approved local title layout defines the exact title region; preserve the retained artwork and its explicit placement."
    return (_path(root, record["amendment_id"], "preview.png").read_bytes(),
            _path(root, record["amendment_id"], "review-artwork.png").read_bytes(), scene)


def amendment_summary(root, project, record, *, approved=False):
    preview = _path(root, record["amendment_id"], "preview.png")
    return {**record, "approved": approved, "preview_path": str(preview),
            "preview_relative_path": preview.relative_to(Path(project).resolve()).as_posix()}
