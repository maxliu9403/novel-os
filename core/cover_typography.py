"""Deterministic title composition using locally available, allowlisted fonts.

An approved layout pins the actual font bytes. No font is downloaded, no title is
rewritten, and fitting is limited to the approved size range and region.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps


class CoverTypographyError(ValueError):
    pass


_FONTS = {
    "sans-bold": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ),
    "serif-bold": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
    ),
    "cjk-sans": (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
    ),
}


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _font_path(font_id: str) -> Path:
    for candidate in _FONTS.get(font_id, ()):
        path = Path(candidate)
        if path.is_file():
            return path
    raise CoverTypographyError(f"Approved font is unavailable: {font_id}")


def _font_covers(data: bytes, text: str) -> bool:
    """Check the actual sfnt cmap rather than silently drawing missing-glyph boxes."""
    try:
        base = struct.unpack_from(">I", data, 12)[0] if data[:4] == b"ttcf" else 0
        count = struct.unpack_from(">H", data, base + 4)[0]
        cmap = None
        for index in range(count):
            offset = base + 12 + index * 16
            if data[offset:offset + 4] == b"cmap":
                cmap = struct.unpack_from(">I", data, offset + 8)[0]
                break
        if cmap is None:
            return False
        subtables = []
        for index in range(struct.unpack_from(">H", data, cmap + 2)[0]):
            platform, encoding, offset = struct.unpack_from(">HHI", data, cmap + 4 + index * 8)
            if platform == 0 or (platform == 3 and encoding in (1, 10)):
                subtables.append(cmap + offset)

        def present(code: int, table: int) -> bool:
            form = struct.unpack_from(">H", data, table)[0]
            if form == 12:
                groups = struct.unpack_from(">I", data, table + 12)[0]
                for index in range(groups):
                    start, end, glyph = struct.unpack_from(">III", data, table + 16 + index * 12)
                    if start <= code <= end:
                        return glyph + code - start != 0
                    if start > code:
                        break
            elif form == 4 and code < 65536:
                segments = struct.unpack_from(">H", data, table + 6)[0] // 2
                ends = table + 14
                starts = ends + segments * 2 + 2
                deltas = starts + segments * 2
                ranges = deltas + segments * 2
                for index in range(segments):
                    end = struct.unpack_from(">H", data, ends + index * 2)[0]
                    if code > end:
                        continue
                    start = struct.unpack_from(">H", data, starts + index * 2)[0]
                    if code < start:
                        return False
                    delta = struct.unpack_from(">h", data, deltas + index * 2)[0]
                    address = ranges + index * 2
                    distance = struct.unpack_from(">H", data, address)[0]
                    if not distance:
                        return (code + delta) % 65536 != 0
                    glyph = struct.unpack_from(">H", data, address + distance + (code - start) * 2)[0]
                    return bool(glyph and (glyph + delta) % 65536)
            return False

        return all(any(present(ord(char), table) for table in subtables)
                   for char in set(text) if not char.isspace())
    except (struct.error, IndexError):
        return False


def _region(value: Any, name: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise CoverTypographyError(f"{name} requires [x, y, width, height]")
    result = [float(part) for part in value]
    x, y, width, height = result
    # Match campaign bounds: tolerate only endpoint arithmetic roundoff, never
    # negative origins, empty rectangles or a measurable overhang.
    if (not all(math.isfinite(part) for part in result) or min(x, y) < 0 or max(x, y) >= 1
            or min(width, height) <= 0 or x + width > 1 + 1e-12 or y + height > 1 + 1e-12):
        raise CoverTypographyError(f"{name} must fit inside the normalized canvas")
    return result


def prepare_title_layout(layout: Mapping[str, Any], *, expected_title: str) -> dict[str, Any]:
    """Validate and pin a layout before its recipe is approved."""
    result = dict(layout)
    title = str(result.get("title") or "")
    lines = result.get("lines")
    if title != expected_title or not title.strip():
        raise CoverTypographyError("Layout title must exactly match the approved title")
    if not isinstance(lines, (list, tuple)) or not lines or any(
        not isinstance(line, str) or not line.strip() or "\n" in line for line in lines
    ):
        raise CoverTypographyError("Layout requires explicit nonempty title lines")
    # CJK titles can wrap without spaces; English word boundaries must survive.
    if " ".join(" ".join(lines).split()) != " ".join(title.split()):
        splits_word = any(left[-1].isascii() and left[-1].isalnum() and right[0].isascii() and right[0].isalnum()
                          for left, right in zip(lines, lines[1:]))
        if splits_word or "".join(lines) != title:
            raise CoverTypographyError("Title lines change the exact title or reading order")
    result["lines"] = list(lines)
    result["region"] = _region(result.get("region"), "title region")
    result["safe_zones"] = [_region(value, "protected story region") for value in result.get("safe_zones", ())]
    for name, default in (("font_size_min", .025), ("font_size_max", .10), ("letter_spacing", 0)):
        result[name] = float(result.get(name, default))
        if not math.isfinite(result[name]):
            raise CoverTypographyError(f"{name} must be finite")
    if not 0 < result["font_size_min"] <= result["font_size_max"] <= .3:
        raise CoverTypographyError("Font sizes must be approved fractions of canvas width")
    if not 0 <= result["letter_spacing"] <= .02:
        raise CoverTypographyError("Letter spacing must be between 0 and .02 canvas widths")
    if result.get("align", "left") not in {"left", "center", "right"}:
        raise CoverTypographyError("Unknown title alignment")
    result.setdefault("align", "left")
    result.setdefault("color", "#FFFFFF")
    try:
        ImageColor.getrgb(result["color"])
    except (ValueError, TypeError) as exc:
        raise CoverTypographyError("Invalid title color") from exc
    font_id = str(result.get("font_id") or "")
    expected_weight = "normal" if font_id == "cjk-sans" else "bold"
    if result.get("font_weight", expected_weight) != expected_weight:
        raise CoverTypographyError("Font weight does not match the approved font face")
    result["font_weight"] = expected_weight
    data = _font_path(font_id).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if result.get("font_sha256") and result["font_sha256"] != digest:
        raise CoverTypographyError("Approved font bytes changed; approve a new layout")
    if not _font_covers(data, title):
        raise CoverTypographyError("Approved font cannot render every title character")
    result["font_sha256"] = digest
    return result


@dataclass(frozen=True)
class ComposedCover:
    data: bytes
    artwork_sha256: str
    title_layout_sha256: str
    final_cover_sha256: str
    font_sha256: str
    text_bounds: tuple[tuple[int, int, int, int], ...]


def compose_cover(
    artwork: bytes, layout: Mapping[str, Any], *, expected_title: str,
    output_format: str = "JPEG",
) -> ComposedCover:
    approved = prepare_title_layout(layout, expected_title=expected_title)
    if not layout.get("font_sha256"):
        raise CoverTypographyError("Title font must be pinned before image generation")
    with Image.open(BytesIO(artwork)) as source:
        source.load()
        canvas = ImageOps.exif_transpose(source).convert("RGB")
    width, height = canvas.size
    draw = ImageDraw.Draw(canvas)
    x, y, rw, rh = approved["region"]
    left, top, right, bottom = x * width, y * height, (x + rw) * width, (y + rh) * height
    tracking = approved["letter_spacing"] * width
    chosen = None
    for size in range(int(approved["font_size_max"] * width), max(1, math.ceil(approved["font_size_min"] * width)) - 1, -1):
        font = ImageFont.truetype(str(_font_path(approved["font_id"])), size)
        metrics = []
        for line in approved["lines"]:
            box = draw.textbbox((0, 0), line, font=font)
            text_width = (draw.textlength(line, font=font) if not tracking else
                          sum(draw.textlength(char, font=font) for char in line) + tracking * (len(line) - 1))
            metrics.append((max(text_width, box[2] - box[0]), box))
        gap = math.ceil(size * .2)
        total_height = sum(box[3] - box[1] for _, box in metrics) + gap * (len(metrics) - 1)
        if max(item[0] for item in metrics) <= right - left and total_height <= bottom - top:
            chosen = (font, metrics, gap)
            break
    if chosen is None:
        raise CoverTypographyError("Title does not fit the approved size range and region")
    font, metrics, gap = chosen
    bounds = []
    for line, (text_width, box) in zip(approved["lines"], metrics):
        text_left = left if approved["align"] == "left" else right - text_width if approved["align"] == "right" else (left + right - text_width) / 2
        rect = (math.floor(text_left), math.floor(top), math.ceil(text_left + text_width), math.ceil(top + box[3] - box[1]))
        for sx, sy, sw, sh in approved["safe_zones"]:
            if rect[0] < (sx + sw) * width and rect[2] > sx * width and rect[1] < (sy + sh) * height and rect[3] > sy * height:
                raise CoverTypographyError("Title would cover a protected face or story action")
        origin = (text_left - box[0], top - box[1])
        if tracking:
            cursor = origin[0]
            for char in line:
                draw.text((cursor, origin[1]), char, font=font, fill=approved["color"])
                cursor += draw.textlength(char, font=font) + tracking
        else:
            draw.text(origin, line, font=font, fill=approved["color"])
        bounds.append(rect)
        top += box[3] - box[1] + gap
    output = BytesIO()
    if output_format.upper() not in {"PNG", "JPEG"}:
        raise CoverTypographyError("Unsupported composed cover format")
    canvas.save(output, format=output_format.upper(), **({"quality": 95, "subsampling": 0} if output_format.upper() == "JPEG" else {}))
    data = output.getvalue()
    return ComposedCover(data, hashlib.sha256(artwork).hexdigest(), canonical_digest(approved),
                         hashlib.sha256(data).hexdigest(), approved["font_sha256"], tuple(bounds))
