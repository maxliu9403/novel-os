import io

import pytest
from PIL import Image, ImageDraw

from core.cover_visual_similarity import compare_cover_images, ImageComparisonUnavailable


def fixture(*, quality=95, reversed_composition=False, tint=0):
    image = Image.new("RGB", (160, 240), (40 + tint, 52 + tint, 72 + tint))
    draw = ImageDraw.Draw(image)
    shift = 80 if reversed_composition else 0
    draw.rectangle((10 + shift, 40, 60 + shift, 210), fill=(210 + tint, 175 + tint, 132 + tint))
    draw.ellipse((85 - shift, 75, 145 - shift, 165), fill=(152 + tint, 178 + tint, 182 + tint))
    stream = io.BytesIO()
    image.save(stream, format="JPEG", quality=quality)
    return stream.getvalue()


def test_identical_image_bytes_are_exact_duplicates():
    data = fixture()
    result = compare_cover_images({"first": data, "second": data})
    assert result[0]["pair"] == ["first", "second"]
    assert result[0]["kind"] == "exact"
    assert result[0]["metric"] == "sha256"


def test_reencoding_or_small_tint_is_only_advisory_near_similarity():
    result = compare_cover_images({"first": fixture(), "reencoded": fixture(quality=70, tint=1)})
    assert result[0]["kind"] == "near"
    assert "not proof" in result[0]["uncertainty"]
    assert result[0]["distance"] <= result[0]["threshold"]


def test_same_palette_with_different_spatial_organization_is_not_a_duplicate():
    assert compare_cover_images({"first": fixture(), "second": fixture(reversed_composition=True)}) == []


def test_decode_failure_is_not_reported_as_a_completed_comparison():
    with pytest.raises(ImageComparisonUnavailable):
        compare_cover_images({"valid": fixture(), "broken": b"not an image"})


def test_single_candidate_does_not_need_a_comparison():
    assert compare_cover_images({"single": fixture()}) == []
