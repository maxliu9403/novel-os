"""Pinned H5 input boundaries shared by compilation and independent inspection.

No model, filesystem mutation, or inferred compatibility is involved here.
Keep limits synchronized with the target zipimport contract tests.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
import json
import math
import posixpath
import re
import stat
import struct
import zlib
from xml.etree import ElementTree as ET
import zipfile


@dataclass(frozen=True)
class H5Profile:
    id: str = "novel-h5-export.2026-10.v1"
    catalog_version: str = "novel-types.2026-10"
    catalog_sha256: str = "f629048c79c235caf5fda427f32c7ba0ff38e54c6674581e0f10cec1362fef4d"
    validator_revision: str = "ea4e76903789d5885a39a9e07c9772db50a9f732"
    zip_bytes: int = 64 * 1024 * 1024
    zip_expanded: int = 128 * 1024 * 1024
    zip_entries: int = 1024
    epub_bytes: int = 16 * 1024 * 1024
    epub_expanded: int = 32 * 1024 * 1024
    epub_entries: int = 600
    spine_items: int = 512
    document_bytes: int = 4 * 1024 * 1024
    json_depth: int = 64
    xml_depth: int = 128
    cover_bytes: int = 8 * 1024 * 1024
    cover_dimension: int = 8192
    cover_pixels: int = 16 * 1024 * 1024
    cover_candidates: int = 24


PROFILE = H5Profile()
TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._:-]{0,159}\Z")
BOOK_ID = re.compile(r"novel-os:[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
XHTML = "http://www.w3.org/1999/xhtml"
EPUB_TYPE = "http://www.idpf.org/2007/ops"


class ContractError(ValueError):
    def __init__(self, code: str, *, path: str = "", actual=None, expected=None):
        self.code, self.path = code, path
        self.actual, self.expected = actual, expected
        detail = f"{code}" + (f" [{path}]" if path else "")
        if actual is not None or expected is not None:
            detail += f"; actual={actual!r}; expected={expected!r}"
        super().__init__(detail)

    def to_dict(self) -> dict:
        return {"code": self.code, "path": self.path,
                "actual": self.actual, "expected": self.expected}


def require(condition, code: str, **details) -> None:
    if not condition:
        raise ContractError(code, **details)


def bound(actual: int, limit: int, code: str, path: str = "") -> None:
    require(actual <= limit, code, path=path, actual=actual, expected=f"<= {limit}")


def safe_path(name: str) -> bool:
    return (isinstance(name, str) and bool(name) and len(name.encode("utf-8")) <= 512
            and not any(c in name for c in "\\:%?#") and not name.startswith("/")
            and not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in name)
            and posixpath.normpath(name) == name and name not in {".", ".."}
            and ".." not in name.split("/"))


def valid_text(value: str, limit: int, *, utf8_bytes: bool = False) -> bool:
    return (isinstance(value, str) and bool(value.strip())
            and (len(value.encode('utf-8')) if utf8_bytes else len(value)) <= limit
            and not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value))


@contextmanager
def checked_archive(raw: bytes, *, epub: bool = False, profile: H5Profile = PROFILE):
    """Bound compressed/expanded data before reading, then check every CRC."""
    prefix = "epub" if epub else "archive"
    bound(len(raw), profile.epub_bytes if epub else profile.zip_bytes, prefix + "_bytes")
    expanded = profile.epub_expanded if epub else profile.zip_expanded
    count = profile.epub_entries if epub else profile.zip_entries
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            require(bool(entries), prefix + "_empty")
            bound(len(entries), count, prefix + "_entries")
            require(len({e.filename for e in entries}) == len(entries), "duplicate_zip_path")
            names = {e.filename for e in entries}
            total = 0
            for entry in entries:
                name = entry.filename
                require(safe_path(name), "unsafe_zip_path", path=name)
                mode = entry.external_attr >> 16
                require(not stat.S_ISLNK(mode) and not entry.is_dir()
                        and stat.S_IFMT(mode) in {0, stat.S_IFREG}, "unsafe_zip_member", path=name)
                require(not entry.flag_bits & 1, "encrypted_zip_member", path=name)
                require(entry.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED},
                        'zip_compression', path=name)
                parents = name.split('/')[:-1]
                require(all('/'.join(parents[:i]) not in names for i in range(1, len(parents) + 1)),
                        'zip_path_collision', path=name)
                total += entry.file_size
                bound(total, expanded, prefix + "_expanded", name)
                # ZipExtFile truncates at the declared size, so it cannot prove
                # that an adversarial stream contains no additional decoded data.
                # open() checks local headers/overlap; validate the raw stream too.
                with archive.open(entry):
                    pass
                offset = entry.header_offset
                require(0 <= offset <= len(raw) - 30, 'invalid_zip_header', path=name)
                name_length, extra_length = struct.unpack_from('<HH', raw, offset + 26)
                start = offset + 30 + name_length + extra_length
                end = start + entry.compress_size
                require(end <= len(raw), 'zip_member_size', path=name)
                compressed = raw[start:end]
                if entry.compress_type == zipfile.ZIP_STORED:
                    decoded = compressed
                else:
                    decoder = zlib.decompressobj(-15)
                    decoded = decoder.decompress(compressed, entry.file_size + 1)
                    require(decoder.eof and not decoder.unconsumed_tail and not decoder.unused_data,
                            'zip_member_size', path=name)
                require(len(decoded) == entry.file_size, 'zip_member_size', path=name)
                require(zlib.crc32(decoded) == entry.CRC, 'zip_member_crc', path=name)
                del decoded, compressed
            yield archive
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, zlib.error) as exc:
        raise ContractError("invalid_zip", path='EPUB' if epub else 'ZIP') from exc


def strict_json(raw: bytes | str, *, path: str = "", profile: H5Profile = PROFILE):
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    bound(len(raw), profile.document_bytes, "json_bytes", path)
    try:
        text = raw.decode("utf-8")
        # Bound nesting before the recursive JSON decoder allocates objects.
        depth = 0
        quoted = escaped = False
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
                bound(depth, profile.json_depth, "json_depth", path)
            elif char in "]}":
                depth -= 1

        def unique(pairs):
            result = {}
            for key, value in pairs:
                require(key not in result, "duplicate_json_key", path=path)
                result[key] = value
            return result

        def number(value):
            parsed = float(value)
            require(math.isfinite(parsed), "nonfinite_json", path=path)
            return parsed

        result = json.loads(text, object_pairs_hook=unique, parse_float=number,
                            parse_constant=lambda _: require(False, "nonfinite_json", path=path))
        # Reject invalid surrogate escapes instead of replacing author evidence.
        json.dumps(result, ensure_ascii=False).encode("utf-8")
        return result
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ContractError("invalid_json", path=path) from exc


def xml_document(raw: bytes, *, path: str = "", profile: H5Profile = PROFILE):
    bound(len(raw), profile.document_bytes, "xml_bytes", path)
    require(b"<!DOCTYPE" not in raw.upper() and b"<!ENTITY" not in raw.upper(), "xml_dtd", path=path)
    try:
        raw.decode("utf-8")
        parser = ET.XMLPullParser(events=("start", "end"))
        root, depth = None, 0
        for offset in range(0, len(raw), 8192):
            parser.feed(raw[offset:offset + 8192])
            for event, element in parser.read_events():
                if event == "start":
                    if root is None:
                        root = element
                    depth += 1
                    bound(depth, profile.xml_depth, "xml_depth", path)
                else:
                    depth -= 1
        parser.close()
        require(root is not None and depth == 0, "invalid_xml", path=path)
        return root
    except (ET.ParseError, UnicodeError) as exc:
        raise ContractError("invalid_xml", path=path) from exc


def verify_xhtml(raw: bytes, kind: str, path: str, *, profile: H5Profile = PROFILE):
    root = xml_document(raw, path=path, profile=profile)
    require(root.tag == f"{{{XHTML}}}html", "xhtml_namespace", path=path)
    bodies = list(root.iter(f"{{{XHTML}}}body"))
    require(len(bodies) == 1, "xhtml_body", path=path)
    body_nodes = set(bodies[0].iter())
    markers = []
    for element in root.iter():
        for marker in element.get(f"{{{EPUB_TYPE}}}type", "").split():
            if marker in {"chapter", "introduction", "title_page", "volume_title_page", "copyright", "afterword"}:
                require(element in body_nodes and marker == kind, "xhtml_kind", path=path)
                markers.append(marker)
    require(len(markers) == 1, "xhtml_kind", path=path)
    require(bool("".join(bodies[0].itertext()).strip()), "xhtml_empty", path=path)


def verify_image(raw: bytes, media_type: str, path: str, *, profile: H5Profile = PROFILE) -> tuple[int, int]:
    from PIL import Image, UnidentifiedImageError
    require(media_type in {"image/png", "image/jpeg"}, "cover_media_type", path=path,
            actual=media_type, expected="PNG/JPEG")
    bound(len(raw), profile.cover_bytes, "cover_bytes", path)
    try:
        with Image.open(BytesIO(raw)) as image:
            require({"PNG": "image/png", "JPEG": "image/jpeg"}.get(image.format) == media_type,
                    "cover_media_type", path=path)
            width, height = image.size
            require(width > 0 and height > 0, "cover_dimensions", path=path)
            bound(max(width, height), profile.cover_dimension, "cover_dimensions", path)
            bound(width * height, profile.cover_pixels, "cover_pixels", path)
            image.verify()
        with Image.open(BytesIO(raw)) as image:
            image.load()  # Header verification alone does not detect truncated image data.
        return width, height
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ContractError("invalid_cover", path=path) from exc
