"""Versioned, local poster lettering; original art and exact title remain intact.

The allowlisted fonts are provided by the existing deployment. Each face is
pinned by its bytes; a later host/font change requires a fresh local recipe.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import math
from typing import Any, Mapping

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont, ImageOps

from .cover_typography import CoverTypographyError, _font_covers, _font_path, _region, canonical_digest


SCHEMA = "poster-typography.v1"
FONT_IDS = ("serif-bold", "sans-bold", "cjk-sans")
_REGIONS = {"top": [.06, .05, .88, .28], "middle": [.06, .36, .88, .28], "bottom": [.06, .68, .88, .27]}
_FIELDS = {"schema_version", "title", "lines", "font_id", "font_sha256", "fonts", "font_weight",
           "font_size_min", "font_size_max", "color", "align", "region", "position", "line_spacing",
           "letter_spacing", "line_styles", "backdrop", "max_lines", "safe_zones", "stroke", "shadow"}


def _number(value, name, minimum, maximum):
    if isinstance(value, bool):
        raise CoverTypographyError(f"{name} must be numeric")
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise CoverTypographyError(f"{name} must be numeric") from None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise CoverTypographyError(f"{name} must be between {minimum} and {maximum}")
    return number


def _color(value):
    try:
        rgb = ImageColor.getrgb(value)
        if len(rgb) != 3:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise CoverTypographyError("Title colors must be opaque RGB colors") from None
    return value


def _lines(title, maximum):
    """Balanced line breaks without removing, rewriting or reordering words."""
    words = title.split()
    if len(words) == 1:
        if any("\u2e80" <= char <= "\u9fff" for char in title):
            length = max(12, math.ceil(len(title) / maximum))
            return [title[i:i + length] for i in range(0, len(title), length)]
        return [title]
    target = max(18, math.ceil(len(title) / maximum))
    result, current = [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > target and len(result) < maximum - 1:
            result.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    return [*result, current]


def prepare_poster_layout(layout: Mapping[str, Any] | None, *, expected_title: str) -> dict:
    if not isinstance(layout, (Mapping, type(None))):
        raise CoverTypographyError("Title layout must be an object")
    raw = dict(layout or {})
    if set(raw) - _FIELDS:
        raise CoverTypographyError("Unsupported title layout fields: " + ", ".join(sorted(set(raw) - _FIELDS)))
    if raw.get("schema_version", SCHEMA) != SCHEMA:
        raise CoverTypographyError("Unsupported poster typography version")
    if not isinstance(expected_title, str) or not expected_title.strip() or len(expected_title) > 500:
        raise CoverTypographyError("An exact title of 1–500 characters is required")
    if raw.get("title", expected_title) != expected_title:
        raise CoverTypographyError("Layout title must exactly match the book title")
    maximum = raw.get("max_lines", 8)
    if type(maximum) is not int or not 1 <= maximum <= 12:
        raise CoverTypographyError("max_lines must be between 1 and 12")
    lines = raw.get("lines") or _lines(expected_title, maximum)
    if not isinstance(lines, (list, tuple)) or not 1 <= len(lines) <= maximum or any(
            not isinstance(line, str) or not line.strip() or "\n" in line for line in lines):
        raise CoverTypographyError("Title requires nonempty, ordered lines within max_lines")
    if " ".join(" ".join(lines).split()) != " ".join(expected_title.split()):
        split_word = any(a[-1].isascii() and a[-1].isalnum() and b[0].isascii() and b[0].isalnum()
                         for a, b in zip(lines, lines[1:]))
        if split_word or "".join(lines) != expected_title:
            raise CoverTypographyError("Title lines change the exact title or reading order")
    position = raw.get("position", "bottom")
    if position not in _REGIONS:
        raise CoverTypographyError("Unknown title position")
    has_cjk = any("\u2e80" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff" for char in expected_title)
    font_id = raw.get("font_id") or ("cjk-sans" if has_cjk else "sans-bold")
    actual_weight = "normal" if font_id == "cjk-sans" else "bold"
    if "font_weight" in raw and raw["font_weight"] not in (
            ("normal", 400, "400") if actual_weight == "normal" else ("bold", 700, "700")):
        raise CoverTypographyError("font_weight must match the selected font face: " + actual_weight)
    align = raw.get("align", "center")
    if align not in {"left", "center", "right"}:
        raise CoverTypographyError("Unknown title alignment")
    minimum = _number(raw.get("font_size_min", .025), "font_size_min", .015, .3)
    maximum_size = _number(raw.get("font_size_max", .11), "font_size_max", minimum, .3)
    color = _color(raw.get("color", "#FFFFFF"))
    styles = raw.get("line_styles") or [{} for _ in lines]
    if not isinstance(styles, (list, tuple)) or len(styles) != len(lines):
        raise CoverTypographyError("line_styles must correspond to every title line")
    faces, normalized, font_data = {}, [], {}
    for line, style in zip(lines, styles):
        if not isinstance(style, Mapping) or set(style) - {"font_id", "scale", "color"}:
            raise CoverTypographyError("Unsupported title line style")
        face = style.get("font_id", font_id)
        if face not in FONT_IDS:
            raise CoverTypographyError("Unknown title font")
        if face not in font_data:
            font_data[face] = _font_path(face).read_bytes()
        data = font_data[face]
        if not _font_covers(data, line):
            raise CoverTypographyError("Chosen font cannot render all title characters")
        faces[face] = sha256(data).hexdigest()
        normalized.append({"font_id": face, "scale": _number(style.get("scale", 1), "line scale", .5, 2),
                           "color": _color(style.get("color", color))})
    if font_id not in FONT_IDS:
        raise CoverTypographyError("Unknown title font")
    if font_id not in font_data:
        font_data[font_id] = _font_path(font_id).read_bytes()
    faces[font_id] = sha256(font_data[font_id]).hexdigest()
    if raw.get("fonts") and raw["fonts"] != faces:
        raise CoverTypographyError("Pinned title font bytes changed")
    if raw.get("font_sha256") and raw["font_sha256"] != faces[font_id]:
        raise CoverTypographyError("Pinned title font bytes changed")
    backdrop = raw.get("backdrop") or {"color": "#10162C", "opacity": 0}
    if not isinstance(backdrop, Mapping) or set(backdrop) - {"color", "opacity", "mode", "direction"}:
        raise CoverTypographyError("Unsupported title backdrop")
    backdrop_mode = backdrop.get("mode", "solid")
    if backdrop_mode not in {"solid", "gradient"} or backdrop.get("direction", "bottom") not in {"top", "bottom", "left", "right"}:
        raise CoverTypographyError("Unsupported title backdrop gradient")
    prepared = {"schema_version": SCHEMA, "title": expected_title, "lines": list(lines), "max_lines": maximum,
            "font_id": font_id, "font_sha256": faces[font_id], "fonts": faces,
            "font_size_min": minimum, "font_size_max": maximum_size, "color": color, "align": align,
            "position": position, "region": _region(raw.get("region", _REGIONS[position]), "title region"),
            "line_spacing": _number(raw.get("line_spacing", .2), "line_spacing", 0, 1),
            "letter_spacing": _number(raw.get("letter_spacing", 0), "letter_spacing", 0, .02),
            "line_styles": normalized, "safe_zones": [_region(z, "protected region") for z in raw.get("safe_zones", ())],
            "backdrop": {"color": _color(backdrop.get("color", "#10162C")),
                         "opacity": _number(backdrop.get("opacity", 0), "backdrop opacity", 0, .95)}}
    # Omitted fields on a pinned historical recipe must stay omitted: adding an
    # effect or even a default key would change its pixels or recipe digest.
    if "font_weight" in raw:
        prepared["font_weight"] = actual_weight
    if "mode" in backdrop or "direction" in backdrop:
        prepared["backdrop"].update(mode=backdrop_mode, direction=backdrop.get("direction", "bottom"))
    if "stroke" in raw:
        stroke = raw["stroke"]
        if not isinstance(stroke, Mapping) or set(stroke) - {"color", "width"}:
            raise CoverTypographyError("Unsupported title stroke")
        prepared["stroke"] = {"color": _color(stroke.get("color", "#000000")),
                              "width": _number(stroke.get("width", 0), "stroke width", 0, .008)}
    shadow = raw.get("shadow")
    if "shadow" not in raw and not raw.get("font_sha256") and not raw.get("fonts") and all(
            sum(ImageColor.getrgb(style["color"])) >= 3 * 200 for style in normalized):
        shadow = {"color": "#000000", "opacity": .38, "blur": .002, "offset_x": 0, "offset_y": .002}
    if shadow is not None or "shadow" in raw:
        if not isinstance(shadow, Mapping) or set(shadow) - {"color", "opacity", "blur", "offset_x", "offset_y"}:
            raise CoverTypographyError("Unsupported title shadow")
        prepared["shadow"] = {"color": _color(shadow.get("color", "#000000")),
            "opacity": _number(shadow.get("opacity", 0), "shadow opacity", 0, .8),
            "blur": _number(shadow.get("blur", 0), "shadow blur", 0, .012),
            "offset_x": _number(shadow.get("offset_x", 0), "shadow offset_x", -.025, .025),
            "offset_y": _number(shadow.get("offset_y", 0), "shadow offset_y", -.025, .025)}
    return prepared


@dataclass(frozen=True)
class PosterComposition:
    data: bytes
    artwork_sha256: str
    title_layout_sha256: str
    final_cover_sha256: str
    font_sha256: str
    text_bounds: tuple


def compose_poster_cover(artwork: bytes, layout: Mapping, *, expected_title: str) -> PosterComposition:
    approved = prepare_poster_layout(layout, expected_title=expected_title)
    if not layout.get("font_sha256"):
        raise CoverTypographyError("Title fonts must be pinned before rendering")
    with Image.open(BytesIO(artwork)) as image:
        image.load()
        canvas = ImageOps.exif_transpose(image).convert("RGB")
    width, height = canvas.size
    draw = ImageDraw.Draw(canvas)
    x, y, rw, rh = approved["region"]
    left, top, right, bottom = x * width, y * height, (x + rw) * width, (y + rh) * height
    tracking = approved["letter_spacing"] * width
    stroke = approved.get("stroke", {})
    stroke_px = round(stroke.get("width", 0) * width)
    shadow = approved.get("shadow", {})
    shadow_alpha = shadow.get("opacity", 0)
    blur = shadow.get("blur", 0) * width if shadow_alpha else 0
    dx, dy = (round(shadow.get(key, 0) * width) if shadow_alpha else 0 for key in ("offset_x", "offset_y"))
    radius = math.ceil(3 * blur)
    pads = (stroke_px + max(0, radius - dx), stroke_px + max(0, radius - dy),
            stroke_px + max(0, radius + dx), stroke_px + max(0, radius + dy))
    pad_left, pad_top, pad_right, pad_bottom = pads
    chosen = None
    maximum_px = math.floor(approved["font_size_max"] * width + 1e-9)
    minimum_px = max(1, math.ceil(approved["font_size_min"] * width - 1e-9))
    for base in range(maximum_px, minimum_px - 1, -1):
        metrics = []
        for line, style in zip(approved["lines"], approved["line_styles"]):
            font = ImageFont.truetype(str(_font_path(style["font_id"])), max(1, round(base * style["scale"])))
            box = draw.textbbox((0, 0), line, font=font)
            length = (draw.textlength(line, font=font) if not tracking else
                      sum(draw.textlength(char, font=font) for char in line) + tracking * (len(line) - 1))
            metrics.append((font, max(length, box[2] - box[0]) + pad_left + pad_right, box))
        gap = math.ceil(base * approved["line_spacing"])
        total = sum(box[3] - box[1] + pad_top + pad_bottom for _, _, box in metrics) + gap * (len(metrics) - 1)
        if total <= bottom - top and max(length for _, length, _ in metrics) <= right - left:
            chosen = metrics, gap
            break
    if chosen is None:
        raise CoverTypographyError("完整书名无法放入所选区域，请增加文字区域、换行或减小字号。")
    backdrop = approved["backdrop"]
    if backdrop["opacity"]:
        overlay = Image.new("RGBA", canvas.size)
        painter = ImageDraw.Draw(overlay)
        color = ImageColor.getrgb(backdrop["color"])
        if backdrop.get("mode", "solid") == "gradient":
            direction = backdrop.get("direction", "bottom")
            vertical = direction in {"top", "bottom"}
            start, end = (math.ceil(top), math.floor(bottom)) if vertical else (math.ceil(left), math.floor(right))
            for point in range(start, end + 1):
                factor = (point - start) / max(1, end - start)
                if direction in {"top", "left"}:
                    factor = 1 - factor
                alpha = round(255 * backdrop["opacity"] * factor)
                segment = ((left, point), (right, point)) if vertical else ((point, top), (point, bottom))
                painter.line(segment, fill=(*color, alpha))
        else:
            painter.rectangle((left, top, right, bottom), fill=(*color, round(255 * backdrop["opacity"])))
        canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(canvas)
    metrics, gap = chosen
    bounds = []
    for line, style, (font, length, box) in zip(approved["lines"], approved["line_styles"], metrics):
        origin_x = left if approved["align"] == "left" else right - length if approved["align"] == "right" else (left + right - length) / 2
        rect = (math.floor(origin_x), math.floor(top), math.ceil(origin_x + length),
                math.ceil(top + box[3] - box[1] + pad_top + pad_bottom))
        for sx, sy, sw, sh in approved["safe_zones"]:
            if rect[0] < (sx + sw) * width and rect[2] > sx * width and rect[1] < (sy + sh) * height and rect[3] > sy * height:
                raise CoverTypographyError("Title would cover a protected face or story action")
        if stroke_px or shadow_alpha:
            # Render each effect inside its measured tile. Bounds include the
            # complete stroke and a conservative 3-sigma shadow envelope; a
            # tile prevents Gaussian tails from touching protected artwork.
            tile_size = (rect[2] - rect[0], rect[3] - rect[1])
            mask = Image.new("L", tile_size)
            mask_draw = ImageDraw.Draw(mask)
            glyph = Image.new("RGBA", tile_size)
            glyph_draw = ImageDraw.Draw(glyph)
            cursor = origin_x + pad_left - box[0] - rect[0]
            baseline = top + pad_top - box[1] - rect[1]
            units = line if tracking else [line]
            for unit in units:
                point = (cursor, baseline)
                mask_draw.text(point, unit, font=font, fill=255, stroke_width=stroke_px, stroke_fill=255)
                glyph_draw.text(point, unit, font=font, fill=style["color"], stroke_width=stroke_px,
                                stroke_fill=stroke.get("color", "#000000"))
                cursor += draw.textlength(unit, font=font) + tracking
            effect = Image.new("RGBA", tile_size)
            if shadow_alpha:
                shifted = Image.new("L", tile_size)
                shifted.paste(mask, (dx, dy))
                if blur:
                    shifted = shifted.filter(ImageFilter.GaussianBlur(blur))
                effect = Image.new("RGBA", tile_size, (*ImageColor.getrgb(shadow["color"]), 0))
                effect.putalpha(shifted.point(lambda alpha: round(alpha * shadow_alpha)))
            effect = Image.alpha_composite(effect, glyph)
            combined = canvas.convert("RGBA")
            combined.alpha_composite(effect, (rect[0], rect[1]))
            canvas = combined.convert("RGB")
            draw = ImageDraw.Draw(canvas)
        else:
            if tracking:
                cursor = origin_x - box[0]
                for char in line:
                    draw.text((cursor, top - box[1]), char, font=font, fill=style["color"])
                    cursor += draw.textlength(char, font=font) + tracking
            else:
                draw.text((origin_x - box[0], top - box[1]), line, font=font, fill=style["color"])
        bounds.append(rect)
        top += box[3] - box[1] + pad_top + pad_bottom + gap
    output = BytesIO()
    canvas.save(output, format="PNG")
    data = output.getvalue()
    return PosterComposition(data, sha256(artwork).hexdigest(), canonical_digest(approved), sha256(data).hexdigest(),
                             approved["font_sha256"], tuple(bounds))
