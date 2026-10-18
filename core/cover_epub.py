"""Attach the selected, exact cover bytes to an existing EPUB without reflowing prose."""
from __future__ import annotations

from copy import copy
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import posixpath
import re
import tempfile
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo

from PIL import Image

try:
    from .image_binary import content_type
except ImportError:  # Legacy compiler imports modules by their top-level names.
    from image_binary import content_type


_OPF = "http://www.idpf.org/2007/opf"
_OPS = "http://www.idpf.org/2007/ops"
_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
_MAX_EPUB_BYTES = 512 * 1024 * 1024


def _xml(data):
    if re.search(br"<!\s*(DOCTYPE|ENTITY)\b", data, re.I):
        raise ValueError("EPUB cover update does not accept XML entity declarations")
    return ET.fromstring(data, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True)))


def _member(base, href):
    url = urlsplit(href)
    path = unquote(url.path)
    if url.scheme or url.netloc or url.query or not path or path.startswith("/") or "\\" in path:
        raise ValueError("EPUB cover reference must be a local archive member")
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(base), path))
    if joined in {".", ".."} or joined.startswith("../"):
        raise ValueError("EPUB cover reference escapes the archive")
    return joined


def _tag(name):
    return f"{{{_OPF}}}{name}"


def _unique_id(stem, items):
    chosen, number = stem, 2
    while chosen in items:
        chosen, number = f"{stem}-{number}", number + 1
    return chosen


def _fresh_href(stem, suffix, opf_path, members):
    href, number = f"novel-os/{stem}{suffix}", 2
    while _member(opf_path, href) in members:
        href, number = f"novel-os/{stem}-{number}{suffix}", number + 1
    return href


def embed_cover(epub: bytes, image: bytes, *, title: str, expected_sha256: str = "") -> bytes:
    """Update OPF/cover page only; every other member's uncompressed bytes stay.

    Existing unused cover assets stay present rather than risking shared images
    referenced by chapters. There is exactly one active cover-image declaration.
    A supported chapter map gains one non-chapter title-page entry. Its chapter,
    classification and serialization bindings are preserved.
    """
    if len(epub) > _MAX_EPUB_BYTES or len(image) > 40 * 1024 * 1024:
        raise ValueError("EPUB or selected cover exceeds its byte limit")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("EPUB cover needs the exact book title")
    if expected_sha256 and sha256(image).hexdigest() != expected_sha256:
        raise ValueError("Selected cover bytes do not match their SHA-256")
    mime = content_type(image)
    if mime not in _EXT:
        raise ValueError("EPUB selected cover must be PNG, JPEG or WebP")
    with Image.open(BytesIO(image)) as decoded:
        decoded.verify()
    try:
        with ZipFile(BytesIO(epub)) as source:
            infos = source.infolist()
            names = [item.filename for item in infos]
            if len(names) != len(set(names)) or len(names) > 10000:
                raise ValueError("EPUB contains duplicate or excessive member paths")
            if sum(item.file_size for item in infos) > _MAX_EPUB_BYTES:
                raise ValueError("EPUB expanded content exceeds its byte limit")
            members = {item.filename: source.read(item) for item in infos}
            comment = source.comment
        if members.get("mimetype") != b"application/epub+zip":
            raise ValueError("EPUB has an invalid mimetype")
        container = _xml(members["META-INF/container.xml"])
        roots = container.findall("{*}rootfiles/{*}rootfile")
        if len(roots) != 1:
            raise ValueError("EPUB must declare one package document")
        opf_path = _member("", roots[0].get("full-path", ""))
        opf = _xml(members[opf_path])
        metadata, manifest, spine = (opf.find(_tag(name)) for name in ("metadata", "manifest", "spine"))
        if metadata is None or manifest is None or spine is None:
            raise ValueError("EPUB package is missing metadata, manifest or spine")
        items = {item.get("id"): item for item in manifest.findall(_tag("item"))}
        if None in items or len(items) != len(manifest.findall(_tag("item"))):
            raise ValueError("EPUB manifest IDs must be unique")
        mappings = [node for node in metadata.findall(_tag("meta")) if node.get("property") == "novel-os:chapter-map"]
        if len(mappings) > 1:
            raise ValueError("EPUB has duplicate chapter maps")
        mapping = json.loads(mappings[0].text or "null") if mappings else None
        if mapping is not None and (not isinstance(mapping, dict) or mapping.get("schema_version") != "novel-epub-map.v1"):
            raise ValueError("EPUB chapter map cannot be safely updated")

        guide = opf.find(_tag("guide"))
        cover_refs = [node for node in guide.findall(_tag("reference")) if node.get("type") == "cover"] if guide is not None else []
        page = None
        if cover_refs:
            path = _member(opf_path, cover_refs[0].get("href", ""))
            for item in items.values():
                try:
                    matches = _member(opf_path, item.get("href", "")) == path
                except ValueError:
                    continue  # Unrelated remote resources remain untouched.
                if matches:
                    page = item
                    break
            if page is None or page.get("media-type") != "application/xhtml+xml":
                raise ValueError("EPUB cover guide does not identify an XHTML cover page")
        if page is None and len(spine):
            first = items.get(spine[0].get("idref"))
            if first is not None and first.get("media-type") == "application/xhtml+xml":
                body = _xml(members[_member(opf_path, first.get("href", ""))]).find("{*}body")
                if body is not None and "cover" in body.get(f"{{{_OPS}}}type", "").split():
                    page = first
        if page is not None and mapping and page.get("id") in {row["item_id"] for row in mapping["chapters"]}:
            raise ValueError("EPUB cover page is also a story chapter; refusing to overwrite prose")
        if page is None:
            page_id = _unique_id("novel-os-cover-page", items)
            page = ET.SubElement(manifest, _tag("item"), {"id": page_id,
                "href": _fresh_href("cover", ".xhtml", opf_path, members), "media-type": "application/xhtml+xml"})
            items[page_id] = page
        page_id, page_href = page.get("id"), page.get("href")

        covers = [item for item in items.values() if "cover-image" in item.get("properties", "").split()]
        legacy = [node for node in metadata.findall(_tag("meta")) if node.get("name") == "cover"]
        old_image = covers[0] if covers else items.get(legacy[0].get("content")) if legacy else None
        if old_image is not None and not old_image.get("media-type", "").startswith("image/"):
            raise ValueError("EPUB cover-image declaration must reference an image")
        old_href = old_image.get("href", "") if old_image is not None else ""
        # Only overwrite this module's active managed asset. An older cover may
        # also be referenced inside a chapter; keep its href and manifest entry.
        managed_active = bool(old_image is not None and re.fullmatch(
            r"novel-os/cover(?:-\d+)?" + re.escape(_EXT[mime]), old_href))
        image_item = old_image if managed_active else None
        if image_item is None:
            image_id = _unique_id("novel-os-cover-image", items)
            image_item = ET.SubElement(manifest, _tag("item"), {"id": image_id})
        if managed_active:
            image_href = old_href
        else:
            image_href = _fresh_href("cover", _EXT[mime], opf_path, members)
        for item in covers:
            properties = [value for value in item.get("properties", "").split() if value != "cover-image"]
            if properties:
                item.set("properties", " ".join(properties))
            else:
                item.attrib.pop("properties", None)
        image_item.set("href", image_href)
        image_item.set("media-type", mime)
        image_item.set("properties", " ".join([*image_item.get("properties", "").split(), "cover-image"]))
        for node in legacy:
            metadata.remove(node)
        ET.SubElement(metadata, _tag("meta"), {"name": "cover", "content": image_item.get("id")})
        for itemref in list(spine):
            if itemref.get("idref") == page_id:
                spine.remove(itemref)
        spine.insert(0, ET.Element(_tag("itemref"), {"idref": page_id, "linear": "yes"}))
        if mapping is not None:
            entries = mapping["non_chapter_items"]
            entries[:] = [entry for entry in entries if entry["item_id"] != page_id]
            entries.insert(0, {"item_id": page_id, "href": page_href, "kind": "title_page"})
            mappings[0].text = json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if guide is None:
            guide = ET.SubElement(opf, _tag("guide"))
        for node in cover_refs:
            guide.remove(node)
        ET.SubElement(guide, _tag("reference"), {"type": "cover", "title": "Cover", "href": page_href})
        image_path, page_path = _member(opf_path, image_href), _member(opf_path, page_href)
        local_image = posixpath.relpath(image_path, posixpath.dirname(page_path))
        page_bytes = (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="{_OPS}"><head>'
            f'<title>{escape(title)}</title><style>html,body{{margin:0;padding:0;height:100%;}}'
            'body{display:flex;align-items:center;justify-content:center;}'
            'img{max-width:100%;max-height:100vh;object-fit:contain;}</style></head>'
            f'<body epub:type="cover"><img src={quoteattr(local_image)} alt={quoteattr(title)}/></body></html>').encode()
        replacement = {opf_path: ET.tostring(opf, encoding="utf-8", xml_declaration=True),
                       page_path: page_bytes, image_path: image}
        output = BytesIO()
        with ZipFile(output, "w") as target:
            target.comment = comment
            mime_info = copy(next(item for item in infos if item.filename == "mimetype"))
            mime_info.compress_type = ZIP_STORED
            mime_info.extra = b""
            target.writestr(mime_info, members["mimetype"])
            for info in infos:
                if info.filename != "mimetype":
                    target.writestr(copy(info), replacement.pop(info.filename, members[info.filename]))
            for name, data in replacement.items():
                info = ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                target.writestr(info, data)
        return output.getvalue()
    except (KeyError, TypeError, ET.ParseError, BadZipFile, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot update EPUB cover safely: {exc}") from exc


def sync_selected_epub(project_path: str | Path, image: bytes, *, title: str, expected_sha256: str) -> bool:
    """Atomically replace only an existing book.epub; no manuscript is invented."""
    project = Path(project_path).resolve()
    path = project / "outputs/deliverables/book.epub"
    if path.is_symlink() or any(parent.is_symlink() for parent in (path.parent, path.parent.parent)):
        raise ValueError("EPUB cover update must remain in its owner directory")
    if not path.exists():
        return False
    original = path.read_bytes()
    updated = embed_cover(original, image, title=title, expected_sha256=expected_sha256)
    if updated == original:
        return False
    fd, temporary = tempfile.mkstemp(prefix=".book.epub.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True
