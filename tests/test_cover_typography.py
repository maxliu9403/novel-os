from io import BytesIO

import pytest
from PIL import Image, ImageChops

from core.cover_typography import CoverTypographyError, compose_cover, prepare_title_layout


def artwork():
    stream = BytesIO()
    Image.new("RGB", (400, 600), (25, 35, 45)).save(stream, format="PNG")
    return stream.getvalue()


def layout(title="The Door", **changes):
    return {"title": title, "lines": [title], "font_id": "sans-bold", "font_weight": "bold",
            "region": [.1, .05, .8, .25], "font_size_min": .035, "font_size_max": .1,
            "align": "center", "color": "#FFFFFF", "safe_zones": [[0, .5, 1, .5]], **changes}


def test_composition_pins_font_preserves_artwork_and_only_changes_title_region():
    original = artwork()
    approved = prepare_title_layout(layout(), expected_title="The Door")
    first = compose_cover(original, approved, expected_title="The Door", output_format="PNG")
    second = compose_cover(original, approved, expected_title="The Door", output_format="PNG")
    assert first == second
    assert first.artwork_sha256 != first.final_cover_sha256
    assert len(first.font_sha256) == 64
    before = Image.open(BytesIO(original))
    after = Image.open(BytesIO(first.data))
    box = ImageChops.difference(before, after).getbbox()
    assert box and box[0] >= 40 and box[2] <= 360 and box[1] >= 30 and box[3] <= 180
    assert before.getpixel((200, 500)) == after.getpixel((200, 500))


@pytest.mark.parametrize("changes", [
    {"lines": ["Door The"]}, {"lines": ["The", "Door", "Again"]},
    {"font_id": "/tmp/unapproved.ttf"}, {"font_sha256": "0" * 64},
    {"font_size_min": .2, "font_size_max": .1}, {"region": [.9, .1, .5, .2]},
])
def test_unapproved_typography_fails_before_composition(changes):
    with pytest.raises(CoverTypographyError):
        prepare_title_layout(layout(**changes), expected_title="The Door")


def test_unpinned_font_overflow_and_protected_story_regions_are_explicit_failures():
    with pytest.raises(CoverTypographyError, match="pinned"):
        compose_cover(artwork(), layout(), expected_title="The Door")
    overflow = prepare_title_layout(layout(region=[.1, .1, .01, .01]), expected_title="The Door")
    with pytest.raises(CoverTypographyError, match="does not fit"):
        compose_cover(artwork(), overflow, expected_title="The Door")
    protected = prepare_title_layout(layout(safe_zones=[[0, 0, 1, 1]]), expected_title="The Door")
    with pytest.raises(CoverTypographyError, match="protected"):
        compose_cover(artwork(), protected, expected_title="The Door")


def test_cjk_title_retains_exact_characters_and_uses_a_font_with_real_glyphs():
    title = "被争夺的时间"
    approved = prepare_title_layout(layout(title, lines=["被争夺的", "时间"], font_id="cjk-sans", font_weight="normal"), expected_title=title)
    result = compose_cover(artwork(), approved, expected_title=title, output_format="PNG")
    assert len(result.text_bounds) == 2
    with pytest.raises(CoverTypographyError, match="every title character"):
        prepare_title_layout(layout("Unknown \U0010ffff"), expected_title="Unknown \U0010ffff")
