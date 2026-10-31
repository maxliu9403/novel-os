"""EPUB-first H5 import identity, chapter locators, and independent revisions.

This sidecar is independent of the older MD publication evidence snapshot.
Hashes describe exact bytes; no H5 text extraction algorithm is assumed.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

try:
    from .narrative_format import NarrativeFormat, chapter_binding
    from .project_identity import ensure_project_instance_id
    from .h5_contract import PROFILE, TOKEN, checked_archive, strict_json, xml_document, verify_xhtml, safe_path, require, bound, valid_text
except ImportError:
    from narrative_format import NarrativeFormat, chapter_binding
    from project_identity import ensure_project_instance_id
    from h5_contract import PROFILE, TOKEN, checked_archive, strict_json, xml_document, verify_xhtml, safe_path, require, bound, valid_text


SCHEMA_VERSION = "novel-h5-import.v1"
RELATIVE_PATH = "meta/h5-import.json"
NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}


class LegacyEpubMapError(ValueError):
    """An older EPUB can be imported by the legacy parser, without volume binding."""


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def object_digest(value: object) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8"))


def _entry(deliverables: Path, path: str) -> dict | None:
    source = deliverables / path
    if not source.exists():
        return None
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"H5 import file must be ordinary: {path}")
    data = source.read_bytes()
    return {"path": path, "size": len(data), "sha256": digest(data)}


def _member_path(opf_path: str, href: str) -> str:
    if not safe_path(href):
        raise ValueError("EPUB chapter href must name one local XHTML document")
    result = posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), href))
    if result.startswith("../") or result == "..":
        raise ValueError("EPUB href escapes its archive")
    return result


def epub_creator(data: bytes) -> str:
    """Return the first EPUB creator without deriving or inventing a name."""
    try:
        with checked_archive(data, epub=True) as archive:
            container = xml_document(archive.read("META-INF/container.xml"), path="META-INF/container.xml")
            roots = container.findall("{*}rootfiles/{*}rootfile")
            if len(roots) != 1:
                raise ValueError("EPUB must declare exactly one package document")
            opf_path = roots[0].get("full-path", "")
            if _member_path("", opf_path) != opf_path:
                raise ValueError("EPUB package path is not canonical")
            opf = xml_document(archive.read(opf_path), path=opf_path)
            creator = opf.findtext("opf:metadata/dc:creator", namespaces=NS)
            return creator if isinstance(creator, str) else ""
    except (KeyError, TypeError, ET.ParseError, zipfile.BadZipFile, UnicodeError) as exc:
        raise ValueError(f"Invalid EPUB creator metadata: {exc}") from exc


def _author_from_story_state(project: Path) -> str:
    state_path = project / "outputs/state/story_state.json"
    if not state_path.exists():
        return ""
    if state_path.is_symlink() or not state_path.is_file():
        raise ValueError("story state must be an ordinary JSON file")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("story state must contain valid JSON") from exc
    if not isinstance(state, dict):
        raise ValueError("story state must contain a JSON object")
    metadata = state.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    author = metadata.get("author")
    if author is None or author == "":
        return ""
    if not isinstance(author, str):
        raise ValueError("story state metadata.author must be a string")
    return author


def epub_index(data: bytes, serialization: dict | None, classification: dict | None) -> dict:
    """Read the compiler's explicit OPF map and validate every spine binding."""
    try:
        with checked_archive(data, epub=True) as archive:
            if len(set(archive.namelist())) != len(archive.namelist()):
                raise ValueError("EPUB contains duplicate member paths")
            require(archive.read("mimetype") == b"application/epub+zip", "epub_mimetype")
            container = xml_document(archive.read("META-INF/container.xml"), path="META-INF/container.xml")
            require(container.tag == "{urn:oasis:names:tc:opendocument:xmlns:container}container", "epub_container")
            roots = container.findall("{*}rootfiles/{*}rootfile")
            if len(roots) != 1:
                raise ValueError("EPUB must declare exactly one package document")
            require(roots[0].get("media-type") == "application/oebps-package+xml", "epub_container")
            opf_path = roots[0].get("full-path", "")
            if _member_path("", opf_path) != opf_path:
                raise ValueError("EPUB package path is not canonical")
            opf = xml_document(archive.read(opf_path), path=opf_path)
            require(opf.tag == f"{{{NS['opf']}}}package", "epub_package")
            titles = opf.findall("opf:metadata/dc:title", NS)
            require(len(titles) == 1 and valid_text((titles[0].text or '').strip(), 300), "epub_title")
            identifier = opf.findtext('opf:metadata/dc:identifier', namespaces=NS)
            require(valid_text(identifier, 300), 'epub_identifier')
            metadata = {}
            for meta in opf.findall("opf:metadata/opf:meta", NS):
                name = meta.get("property")
                if name and name.startswith("novel-os:"):
                    if name in metadata:
                        raise ValueError(f"Duplicate EPUB metadata: {name}")
                    metadata[name] = strict_json(meta.text or "null", path=name)
            for key, expected in (("classification", classification), ("serialization", serialization)):
                embedded = metadata.get(f"novel-os:{key}")
                if embedded != expected:
                    raise ValueError(f"EPUB {key} conflicts with package JSON; recompile before export")
            mapping = metadata.get("novel-os:chapter-map")
            if mapping is None:
                raise LegacyEpubMapError("Legacy EPUB has no explicit chapter map")
            if not isinstance(mapping, dict) or mapping.get("schema_version") != "novel-epub-map.v1":
                raise ValueError("EPUB chapter map is missing; recompile this legacy EPUB")
            items_list = opf.findall("opf:manifest/opf:item", NS)
            items = {item.get("id"): item for item in items_list}
            if len(items) != len(items_list):
                raise ValueError("EPUB has duplicate manifest item IDs")
            paths = set()
            for item_id, item in items.items():
                require(isinstance(item_id, str) and TOKEN.fullmatch(item_id), "epub_item_id")
                resource = _member_path(opf_path, item.get("href", ""))
                require(resource not in paths and resource in archive.namelist(), "epub_resource", path=resource)
                paths.add(resource)
            spine_nodes = opf.findall("opf:spine/opf:itemref", NS)
            bound(len(spine_nodes), PROFILE.spine_items, "epub_spine")
            require(all(node.get("linear", "yes") == "yes" for node in spine_nodes), "epub_non_linear")
            spine = [item.get("idref") for item in spine_nodes]
            if len(set(spine)) != len(spine):
                raise ValueError("EPUB has duplicate spine references")
            format_contract = NarrativeFormat.from_dict(serialization["format"]) if serialization else None
            records = []
            non_chapters = []
            seen = set()

            def locate(record: dict, kind: str) -> dict:
                item_id = record["item_id"]
                href = record["href"]
                item = items.get(item_id)
                if item_id in seen or item is None or item_id not in spine:
                    raise ValueError("EPUB map has a duplicate or missing spine item")
                if item.get("href") != href or item.get("media-type") != "application/xhtml+xml":
                    raise ValueError("EPUB map href or media type conflicts with manifest")
                seen.add(item_id)
                path = _member_path(opf_path, href)
                body = archive.read(path)
                verify_xhtml(body, kind, path)
                return {"item_id": item_id, "href": href, "zip_path": path,
                        "spine_index": spine.index(item_id), "xhtml_sha256": digest(body),
                        "xhtml_size": len(body)}

            for expected_number, record in enumerate(mapping["chapters"], 1):
                number = record["number"]
                if type(number) is not int or number != expected_number:
                    raise ValueError("EPUB story chapter numbers must be contiguous from 1")
                locator = locate(record, "chapter")
                binding = chapter_binding(format_contract, number, serialization["volumes"]) if format_contract else {
                    "volume_id": "volume_01", "volume_number": 1, "chapter_in_volume": number,
                    "volume_role": "unspecified", "series_id": "", "series_book_number": None,
                }
                records.append({"number": number, "kind": "chapter", **binding, "epub": locator})
            require(len(mapping["non_chapter_items"]) <= 1, "front_matter_count")
            for record in mapping["non_chapter_items"]:
                if record["kind"] != "introduction":
                    raise ValueError("EPUB non-chapter kind is unsupported")
                non_chapters.append({"kind": record["kind"], "epub": locate(record, "introduction")})
            require(not non_chapters or non_chapters[0]["epub"]["spine_index"] == 0, "introduction_position")
            if seen != set(spine) or not records:
                raise ValueError("EPUB chapter map must classify every spine item exactly once")
            if [r["epub"]["spine_index"] for r in records] != list(range(len(non_chapters), len(spine))):
                raise ValueError("EPUB chapter order conflicts with spine")
            if format_contract and format_contract.total_chapters != len(records):
                raise ValueError("EPUB story chapter count conflicts with serialization")
            return {
                "opf_path": opf_path,
                "dc_identifier": identifier,
                "dc_creator": opf.findtext("opf:metadata/dc:creator", namespaces=NS) or "",
                "chapter_count": len(records), "chapters": records,
                "non_chapter_items": non_chapters,
                "content_sha256": object_digest([
                    {"number": r["number"], "xhtml_sha256": r["epub"]["xhtml_sha256"]} for r in records
                ]),
                "front_matter_sha256": object_digest([
                    {"kind": r["kind"], "xhtml_sha256": r["epub"]["xhtml_sha256"]} for r in non_chapters
                ]),
            }
    except (KeyError, TypeError, ET.ParseError, zipfile.BadZipFile, UnicodeError) as exc:
        raise ValueError(f"Invalid EPUB import binding: {exc}") from exc


def build_h5_import(project: Path, *, classification: dict | None,
                    serialization: dict | None, files: list[dict], cover_set=None,
                    deliverables: Path | None = None) -> dict:
    deliverables = deliverables or project / "outputs/deliverables"
    book_id = "novel-os:" + ensure_project_instance_id(project)
    epub_file = _entry(deliverables, "book.epub")
    index = None
    epub_author = ""
    status = "metadata_only"
    if epub_file:
        epub_data = (deliverables / "book.epub").read_bytes()
        epub_author = epub_creator(epub_data)
        try:
            index = epub_index(epub_data, serialization, classification)
            status = "ready"
        except LegacyEpubMapError:
            status = "legacy_epub"
    author = _author_from_story_state(project) or epub_author
    selected_files = [file for file in files if file["role"] == "selected_cover"]
    if len(selected_files) > 1:
        raise ValueError("Multiple selected cover files conflict")
    selected = selected_files[0] if selected_files else None
    if cover_set and cover_set.selected_candidate_id:
        candidates = [c for c in cover_set.candidates if c.candidate_id == cover_set.selected_candidate_id]
        if len(candidates) != 1 or not selected or candidates[0].sha256 != selected["sha256"]:
            raise ValueError("Selected cover bytes conflict with selected_candidate_id")
    by_role = {file["role"]: file for file in files if file["role"] in {
        "novel_classification", "novel_serialization", "publication_copy", "cover_metadata",
    }}
    versions = {
        "author_sha256": object_digest({"author": author}),
        "epub_sha256": epub_file["sha256"] if epub_file else None,
        "content_sha256": index["content_sha256"] if index else None,
        "front_matter_sha256": index["front_matter_sha256"] if index else None,
        "selected_cover_sha256": selected["sha256"] if selected else None,
        **{f"{name}_sha256": by_role.get(role, {}).get("sha256") for name, role in (
            ("classification", "novel_classification"), ("serialization", "novel_serialization"),
            ("publication_copy", "publication_copy"), ("cover_metadata", "cover_metadata"),
        )},
    }
    chapters = [
        {**record, "chapter_id": f"{book_id}:chapter:{record['number']}"}
        for record in (index or {}).get("chapters", [])
    ]
    return {
        "schema_version": SCHEMA_VERSION, "book_id": book_id,
        "author": author,
        "body_source": "epub", "status": status,
        "epub": {**epub_file, "opf_path": (index or {}).get("opf_path"),
                 "dc_identifier": (index or {}).get("dc_identifier")} if epub_file else None,
        "chapter_count": len(chapters), "chapters": chapters,
        "non_chapter_items": (index or {}).get("non_chapter_items", []),
        "classification": by_role.get("novel_classification"),
        "serialization": by_role.get("novel_serialization"),
        "cover": {
            "selection_action": "replace" if selected else "keep_existing",
            "selected_candidate_id": cover_set.selected_candidate_id if cover_set else "",
            "selected": selected, "metadata": by_role.get("cover_metadata"),
        },
        "versions": versions,
        "import_revision_sha256": object_digest({"book_id": book_id, "versions": versions}),
    }
