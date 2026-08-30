"""Small, dependency-free image header inspection shared by media workflows."""

from __future__ import annotations

import struct


ASPECT_RATIO_TOLERANCE = 0.005


def content_type(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def dimensions(data: bytes) -> tuple[int, int]:
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            width, height = struct.unpack(">II", data[16:24])
            return int(width), int(height)
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return _webp_dimensions(data)
        if data[:2] == b"\xff\xd8":
            return _jpeg_dimensions(data)
    except (IndexError, struct.error, ValueError):
        pass
    return 0, 0


def aspect_ratio_matches(
    width: int,
    height: int,
    *,
    ratio: tuple[int, int] = (2, 3),
    tolerance: float = ASPECT_RATIO_TOLERANCE,
) -> bool:
    """Return whether dimensions preserve the requested width:height ratio.

    A small relative tolerance handles providers that round one raster edge by
    a pixel while still rejecting materially distorted or landscape images.
    """
    if width <= 0 or height <= 0:
        return False
    ratio_width, ratio_height = ratio
    if ratio_width <= 0 or ratio_height <= 0 or tolerance < 0:
        return False
    expected = width * ratio_height
    actual = height * ratio_width
    return abs(expected - actual) <= max(expected, actual) * tolerance


def _webp_dimensions(data: bytes) -> tuple[int, int]:
    fourcc = data[12:16]
    if fourcc == b"VP8X" and len(data) >= 30:
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    if fourcc == b"VP8 " and len(data) >= 30:
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if fourcc == b"VP8L" and len(data) >= 25:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return 0, 0


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[index + 5:index + 9])
            return int(width), int(height)
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        index += 2 + struct.unpack(">H", data[index + 2:index + 4])[0]
    return 0, 0
