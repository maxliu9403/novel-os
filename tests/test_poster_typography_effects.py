"""Local title contrast effects stay bounded and respect frozen old recipes."""
from copy import deepcopy
from io import BytesIO

from PIL import Image, ImageChops, ImageDraw
import pytest

from core.cover_poster_typography import compose_poster_cover, prepare_poster_layout
from core.cover_typography import CoverTypographyError, canonical_digest


TITLE = "A House of Her Own"


def art(color=(160, 160, 160)):
    stream = BytesIO()
    Image.new("RGB", (400, 600), color).save(stream, format="PNG")
    return stream.getvalue()


def prepare(value):
    return prepare_poster_layout(value, expected_title=TITLE)


@pytest.mark.parametrize("face,weight", [("sans-bold", "bold"), ("serif-bold", "bold"), ("cjk-sans", "normal")])
def test_weight_matches_fixed_face_and_is_never_silently_ignored(face, weight):
    result = prepare({"font_id": face, "font_weight": weight})
    assert result["font_weight"] == weight
    assert prepare(result) == result
    with pytest.raises(CoverTypographyError, match="font_weight"):
        prepare({"font_id": face, "font_weight": "normal" if weight == "bold" else "bold"})


def test_new_white_letters_get_subtle_shadow_but_pinned_historical_recipe_is_identical():
    fresh = prepare({})
    assert fresh["shadow"]["opacity"] == .38
    assert "shadow" not in prepare({"color": "#101010"})
    historical = deepcopy(fresh)
    historical.pop("shadow")
    assert prepare(historical) == historical
    assert "font_weight" not in historical and "mode" not in historical["backdrop"]
    original = compose_poster_cover(art(), historical, expected_title=TITLE)
    assert original.title_layout_sha256 == canonical_digest(historical)
    explicitly_disabled = prepare({**historical, "shadow": {"opacity": 0}, "stroke": {"width": 0}})
    assert compose_poster_cover(art(), explicitly_disabled, expected_title=TITLE).data == original.data


def test_stroke_and_soft_shadow_pixels_are_within_reported_effect_bounds():
    layout = prepare({"lines": ["A House of", "Her Own"], "font_size_min": .07, "font_size_max": .07,
        "stroke": {"color": "#442211", "width": .004},
        "shadow": {"color": "#223344", "opacity": .7, "blur": .007, "offset_x": -.01, "offset_y": .012}})
    before = Image.open(BytesIO(art())).convert("RGB")
    result = compose_poster_cover(art(), layout, expected_title=TITLE)
    after = Image.open(BytesIO(result.data)).convert("RGB")
    mask = Image.new("L", before.size)
    draw = ImageDraw.Draw(mask)
    for left, top, right, bottom in result.text_bounds:
        draw.rectangle((left, top, right - 1, bottom - 1), fill=255)
    difference = ImageChops.difference(before, after)
    assert difference.getbbox() is not None
    outside = Image.composite(difference, Image.new("RGB", before.size), ImageChops.invert(mask))
    assert outside.getbbox() is None


def test_shadow_envelope_is_checked_against_protected_story_region():
    ordinary = prepare({"lines": [TITLE], "font_size_min": .045, "font_size_max": .045,
                        "shadow": {"opacity": 0}, "align": "left"})
    plain = compose_poster_cover(art(), ordinary, expected_title=TITLE)
    bottom = plain.text_bounds[0][3] / 600
    protected = [.06, bottom + .002, .6, .025]
    compose_poster_cover(art(), prepare({**ordinary, "safe_zones": [protected]}), expected_title=TITLE)
    shadowed = prepare({**ordinary, "safe_zones": [protected],
                        "shadow": {"opacity": .5, "blur": .005, "offset_y": .012}})
    with pytest.raises(CoverTypographyError, match="protected"):
        compose_poster_cover(art(), shadowed, expected_title=TITLE)


@pytest.mark.parametrize("direction", ["top", "bottom", "left", "right"])
def test_backdrop_gradient_fades_in_requested_direction_without_changing_other_art(direction):
    layout = prepare({"shadow": {"opacity": 0}, "color": "#FFFFFF",
                      "region": [.1, .1, .8, .7], "font_size_max": .045,
                      "backdrop": {"color": "#000000", "opacity": .6, "mode": "gradient", "direction": direction}})
    output = compose_poster_cover(art((200, 200, 200)), layout, expected_title=TITLE)
    image = Image.open(BytesIO(output.data))
    assert image.getpixel((0, 0)) == (200, 200, 200)
    # Sample away from top-aligned glyphs.
    if direction in {"top", "bottom"}:
        start, end = image.getpixel((41, 62))[0], image.getpixel((41, 478))[0]
    else:
        start, end = image.getpixel((42, 470))[0], image.getpixel((358, 470))[0]
    assert start > end if direction in {"bottom", "right"} else start < end


@pytest.mark.parametrize("effects", [
    {"stroke": {"width": .1}}, {"stroke": {"width": float("nan")}},
    {"shadow": {"blur": .1}}, {"shadow": {"offset_x": -.1}}, {"shadow": {"opacity": 1}},
    {"backdrop": {"mode": "gradient", "direction": "diagonal"}},
])
def test_effect_values_are_bounded_before_rendering(effects):
    with pytest.raises(CoverTypographyError):
        prepare(effects)
