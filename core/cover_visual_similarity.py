"""Conservative, local duplicate warnings for actual generated cover images.

Call with unlettered artwork when available. This detects byte duplicates and
near visual renderings; it does not measure story similarity or commercial value.
"""
from __future__ import annotations

import hashlib
import io
import math
from itertools import combinations

from PIL import Image, ImageOps


class ImageComparisonUnavailable(ValueError):
    pass


def _fingerprint(data: bytes):
    try:
        with Image.open(io.BytesIO(data)) as original:
            original.load()
            image = ImageOps.exif_transpose(original).convert("RGB")
            rgb = image.resize((32, 48), Image.Resampling.LANCZOS)
            gray = rgb.convert("L")
            tiny = image.convert("L").resize((17, 16), Image.Resampling.LANCZOS)
            pixels = tiny.tobytes()
            bits = sum((int(pixels[row * 17 + col] > pixels[row * 17 + col + 1]) << (row * 16 + col))
                       for row in range(16) for col in range(16))
            colors = rgb.tobytes()
            return bits, gray.tobytes(), list(zip(colors[0::3], colors[1::3], colors[2::3]))
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageComparisonUnavailable("Actual-image comparison could not decode a candidate") from exc


def compare_cover_images(images: dict[str, bytes]) -> list[dict]:
    """Return exact/near warnings, never an automatic redraw recommendation.

    Near warnings require agreement in edge shape, luminance, and color. The
    thresholds are conservative heuristics, not calibrated quality gates. A
    decode error is explicit so callers cannot silently report a completed test.
    """
    digests = {identifier: hashlib.sha256(data).hexdigest() for identifier, data in images.items()}
    fingerprints = {}
    warnings = []
    for first, second in combinations(images, 2):
        pair = [first, second]
        if digests[first] == digests[second]:
            warnings.append({"pair": pair, "kind": "exact", "metric": "sha256", "distance": 0,
                             "threshold": 0, "uncertainty": "The image bytes are identical; this does not explain the narrative cause."})
            continue
        for identifier in pair:
            if identifier not in fingerprints:
                fingerprints[identifier] = _fingerprint(images[identifier])
        first_hash, first_gray, first_rgb = fingerprints[first]
        second_hash, second_gray, second_rgb = fingerprints[second]
        distance = (first_hash ^ second_hash).bit_count()
        luminance_rmse = math.sqrt(sum((a - b) ** 2 for a, b in zip(first_gray, second_gray)) / len(first_gray)) / 255
        color_rmse = math.sqrt(sum((a - b) ** 2 for left, right in zip(first_rgb, second_rgb)
                                  for a, b in zip(left, right)) / (len(first_rgb) * 3)) / 255
        if distance <= 8 and luminance_rmse <= 0.045 and color_rmse <= 0.055:
            warnings.append({"pair": pair, "kind": "near", "metric": "dhash256+luminance_rgb_rmse",
                             "distance": distance, "threshold": 8,
                             "luminance_rmse": round(luminance_rmse, 6), "luminance_threshold": 0.045,
                             "color_rmse": round(color_rmse, 6), "color_threshold": 0.055,
                             "uncertainty": "Possible visual repetition only. A person must compare actions, relationship organization and composition; this is not proof of semantic duplication."})
    return warnings
