"""Scoped selection and immutable capture of the exact skeleton used by a cover.

Resolvers and guards belong to the caller (Studio, Workshop, or CLI). This
module never discovers a project by title and never reads another live source
after a snapshot has been captured.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, ContextManager, Mapping


SOURCE_SCHEMA = "cover-skeleton-snapshot.v1"
SOURCE_TYPES = frozenset({"project_input", "workshop_current", "workshop_prepared", "workshop_archived"})
_SHA = re.compile(r"^[0-9a-f]{64}$")
_SNAPSHOT = re.compile(r"^snapshot-[0-9a-f]{32}$")
_DOCUMENT = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}$")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


class CoverSourceError(ValueError):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code, self.details = code, details


@dataclass(frozen=True)
class CoverSourceSelector:
    source_type: str
    project_id: str = ""
    session_id: str = ""
    archive_revision: int | None = None
    expected_content_sha256: str = ""
    expected_session_revision: int | None = None
    freshness_policy: str = "require_current_head"

    def __post_init__(self) -> None:
        if self.source_type not in SOURCE_TYPES:
            raise CoverSourceError("invalid_selector", "Unknown skeleton source type")
        if self.freshness_policy not in {"require_current_head", "pinned_snapshot"}:
            raise CoverSourceError("invalid_selector", "Unknown source freshness policy")
        if self.source_type == "project_input":
            if (not self.project_id or self.project_id in {".", ".."}
                    or any(c in self.project_id for c in "/\\\x00") or self.session_id
                    or self.archive_revision is not None):
                raise CoverSourceError("invalid_selector", "Project selection needs one scoped project id")
        elif not re.fullmatch(r"[0-9a-f]{32}", self.session_id) or self.project_id:
            raise CoverSourceError("invalid_selector", "Workshop selection needs one full session UUID")
        if self.source_type == "workshop_archived":
            if type(self.archive_revision) is not int or self.archive_revision < 0:
                raise CoverSourceError("invalid_selector", "Archive revision must be a nonnegative integer")
        elif self.archive_revision is not None:
            raise CoverSourceError("invalid_selector", "Archive revision is only valid for archived sources")
        if self.expected_content_sha256 and not _SHA.fullmatch(self.expected_content_sha256):
            raise CoverSourceError("invalid_selector", "Expected content hash must be SHA-256")
        if self.expected_session_revision is not None and (
                type(self.expected_session_revision) is not int or self.expected_session_revision < 0):
            raise CoverSourceError("invalid_selector", "Expected session revision must be nonnegative")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CoverSourceSelector":
        return cls(**{key: data[key] for key in cls.__dataclass_fields__ if key in data})

    def to_dict(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in self.__dataclass_fields__}


@dataclass(frozen=True)
class CoverSourceContext:
    cover_root: Path
    owner_scope: str
    project_resolver: Callable[[str], Path] | None = None
    workshop_resolver: Callable[[str], Path] | None = None
    project_guard: Callable[[str], ContextManager[Any]] | None = None
    workshop_guard: Callable[[str], ContextManager[Any]] | None = None
    # Trusted adapters may declare required narrative attachments. Paths remain
    # relative to the resolved project/session root; arbitrary URLs are rejected.
    dependency_resolver: Callable[[CoverSourceSelector, str], Mapping[str, str]] | None = None
    max_source_bytes: int = 16 * 1024 * 1024

    def __post_init__(self) -> None:
        if not self.owner_scope.strip():
            raise CoverSourceError("invalid_scope", "An explicit owner scope is required")
        object.__setattr__(self, "cover_root", Path(self.cover_root))


@dataclass(frozen=True)
class CoverSourcePreview:
    selector: CoverSourceSelector
    source_content_sha256: str
    source_bundle_sha256: str
    origin: Mapping[str, Any]
    documents: tuple[Mapping[str, Any], ...]
    primary_text: str

    def to_dict(self) -> dict[str, Any]:
        return {"selector": self.selector.to_dict(), "source_content_sha256": self.source_content_sha256,
                "source_bundle_sha256": self.source_bundle_sha256, "origin": dict(self.origin),
                "documents": [dict(x) for x in self.documents], "primary_text": self.primary_text}


@dataclass(frozen=True)
class CoverSkeletonSnapshot:
    snapshot_id: str
    snapshot_sha256: str
    source_content_sha256: str
    source_bundle_sha256: str
    owner_scope: str
    selector: CoverSourceSelector
    origin: Mapping[str, Any]
    documents: tuple[Mapping[str, Any], ...]
    created_at: str
    _bodies: Mapping[str, bytes] = field(repr=False, compare=False)
    schema_version: str = SOURCE_SCHEMA

    @property
    def primary_bytes(self) -> bytes:
        return self._bodies["primary"]

    @property
    def primary_text(self) -> str:
        return self.primary_bytes.decode("utf-8")

    @property
    def prompt_text(self) -> str:
        return self.primary_text

    def document_bytes(self, document_id: str) -> bytes:
        return self._bodies[document_id]

    def document_text(self, document_id: str) -> str:
        return self.document_bytes(document_id).decode("utf-8")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "snapshot_id": self.snapshot_id,
                "snapshot_sha256": self.snapshot_sha256, "owner_scope": self.owner_scope,
                "source_content_sha256": self.source_content_sha256,
                "source_bundle_sha256": self.source_bundle_sha256,
                "selector": self.selector.to_dict(), "origin": dict(self.origin),
                "documents": [dict(x) for x in self.documents], "created_at": self.created_at}


def _safe_path(root: Path, relative: str) -> Path:
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(x in {"..", "."} for x in parts):
        raise CoverSourceError("invalid_scope", "Source path escapes its authorized root")
    root = root.resolve()
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise CoverSourceError("invalid_scope", "Source paths cannot contain symlinks")
    if not path.resolve().is_relative_to(root):
        raise CoverSourceError("invalid_scope", "Source path escapes its authorized root")
    return path


def _read_file(root: Path, relative: str, *, missing_code: str = "source_missing") -> bytes:
    path = _safe_path(root, relative)
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise CoverSourceError(missing_code, f"Skeleton source is missing: {relative}") from exc
    except OSError as exc:
        raise CoverSourceError("source_unreadable", f"Cannot read skeleton source: {relative}") from exc


def _utf8(body: bytes, *, name: str, maximum: int) -> str:
    if len(body) > maximum:
        raise CoverSourceError("source_unreadable", f"Source exceeds configured byte limit: {name}")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CoverSourceError("source_unreadable", f"Source is not UTF-8: {name}") from exc
    if not text.strip():
        raise CoverSourceError("source_unreadable", f"Skeleton source is empty: {name}")
    return text


def _declared_dependencies(text: str) -> dict[str, str]:
    """Read the explicit, scoped narrative attachment manifest.

    Supported Markdown forms are a ``COVER_SOURCE_DEPENDENCIES`` fence, or a
    heading of that name followed by a JSON fence. The JSON is an object mapping
    stable document ids to paths relative to the project/discussion directory.
    Ordinary prose links and runtime paths are deliberately not file reads.
    """
    blocks = re.findall(r"(?ms)^```COVER_SOURCE_DEPENDENCIES\s*\n(.*?)^```\s*$", text)
    blocks += re.findall(r"(?ms)^#{1,6}\s+COVER_SOURCE_DEPENDENCIES\s*\n\s*```json\s*\n(.*?)^```\s*$", text)
    dependencies: dict[str, str] = {}
    for block in blocks:
        try:
            mapping = json.loads(block)
            if not isinstance(mapping, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                                   for key, value in mapping.items()):
                raise ValueError("Dependency manifest must map document ids to relative paths")
            for key, value in mapping.items():
                if key in dependencies and dependencies[key] != value:
                    raise ValueError("Conflicting narrative dependency declarations")
                dependencies[key] = value
        except ValueError as exc:
            raise CoverSourceError("source_incomplete", f"Invalid COVER_SOURCE_DEPENDENCIES: {exc}") from exc
    return dependencies


def _read_source(context: CoverSourceContext, selector: CoverSourceSelector, *, head_only: bool = False) -> tuple[dict, dict[str, bytes], dict[str, str]]:
    origin: dict[str, Any] = {"source_type": selector.source_type, "approval_state": "unknown"}
    if selector.source_type == "project_input":
        if context.project_resolver is None:
            raise CoverSourceError("invalid_scope", "No scoped project adapter is configured")
        root = Path(context.project_resolver(selector.project_id)).resolve()
        primary = _read_file(root, "outputs/input/prompt.md")
        origin.update(project_id=selector.project_id, relative_path="outputs/input/prompt.md",
                      approval_state="project_input")
    else:
        if context.workshop_resolver is None:
            raise CoverSourceError("invalid_scope", "No scoped workshop adapter is configured")
        root = Path(context.workshop_resolver(selector.session_id)).resolve()
        try:
            session = json.loads(_read_file(root, "session.json"))
        except (ValueError, UnicodeError) as exc:
            if isinstance(exc, CoverSourceError):
                raise
            raise CoverSourceError("source_unreadable", "Workshop session is unreadable") from exc
        if (not isinstance(session, dict) or session.get("id") != selector.session_id
                or type(session.get("revision")) is not int):
            raise CoverSourceError("invalid_scope", "Workshop session identity does not match selector")
        revision = session["revision"]
        if selector.expected_session_revision is not None and selector.expected_session_revision != revision:
            raise CoverSourceError("source_changed", "Workshop session revision changed", actual_revision=revision)
        origin.update(session_id=selector.session_id, observed_session_revision=revision,
                      session_status=session.get("status", ""), project_id=session.get("project_id"))
        current = session.get("prompt_md")
        if not isinstance(current, str):
            raise CoverSourceError("source_unreadable", "Workshop prompt is not text")
        if selector.source_type == "workshop_archived":
            relative = f"versions/{selector.archive_revision}.md"
            try:
                primary = _read_file(root, relative, missing_code="archive_not_found")
            except CoverSourceError as exc:
                if exc.code == "archive_not_found":
                    folder = _safe_path(root, "versions")
                    exc.details["available_revisions"] = sorted(int(p.stem) for p in folder.glob("*.md")
                                                               if p.stem.isdigit() and not p.is_symlink())
                raise
            origin.update(relative_path=relative, archive_revision=selector.archive_revision)
        elif selector.source_type == "workshop_prepared":
            prepared = session.get("prepared")
            if not head_only and (not isinstance(prepared, dict) or prepared.get("revision") != revision
                    or hashlib.sha256(current.encode("utf-8")).hexdigest() != prepared.get("prompt_sha256")):
                raise CoverSourceError("source_unapproved", "The workshop prepared source is no longer valid")
            primary = _read_file(root, "prepared.md", missing_code="source_unapproved")
            primary_sha = hashlib.sha256(primary).hexdigest()
            if head_only and hashlib.sha256(current.encode("utf-8")).hexdigest() != primary_sha:
                raise CoverSourceError("source_changed", "The current workshop content differs from its captured prepared source")
            if not head_only and primary_sha != prepared["prompt_sha256"]:
                raise CoverSourceError("source_unapproved", "Prepared file does not match the confirmed skeleton")
            origin.update(relative_path="prepared.md", approval_state="prepared" if not head_only else "unknown",
                          prepared_revision=revision, prepared_prompt_sha256=primary_sha)
        else:
            if not current.strip():
                raise CoverSourceError("source_missing", "The workshop skeleton has not been generated")
            primary = current.encode("utf-8")
            origin.update(relative_path="session.json:prompt_md", approval_state="draft")
    text = _utf8(primary, name="primary", maximum=context.max_source_bytes)
    digest = hashlib.sha256(primary).hexdigest()
    if selector.expected_content_sha256 and digest != selector.expected_content_sha256:
        raise CoverSourceError("source_changed", "Skeleton content changed", actual_content_sha256=digest,
                               expected_content_sha256=selector.expected_content_sha256)
    bodies, paths = {"primary": primary}, {"primary": origin["relative_path"]}
    dependencies = _declared_dependencies(text)
    for key, path in (context.dependency_resolver(selector, text) if context.dependency_resolver else {}).items():
        if key in dependencies and dependencies[key] != path:
            raise CoverSourceError("source_incomplete", "Narrative dependency conflicts with scoped adapter")
        dependencies[key] = path
    for document_id, relative in sorted(dependencies.items()):
        if document_id == "primary" or not _DOCUMENT.fullmatch(document_id):
            raise CoverSourceError("source_incomplete", "Invalid required document identifier")
        body = _read_file(root, relative, missing_code="source_incomplete")
        _utf8(body, name=document_id, maximum=context.max_source_bytes)
        bodies[document_id], paths[document_id] = body, relative
    if sum(map(len, bodies.values())) > context.max_source_bytes:
        raise CoverSourceError("source_unreadable", "Skeleton bundle exceeds configured byte limit")
    return origin, bodies, paths


def _stable_read(context: CoverSourceContext, selector: CoverSourceSelector, *, head_only: bool = False):
    guard = (context.project_guard if selector.source_type == "project_input" else context.workshop_guard)
    owner_id = selector.project_id if selector.source_type == "project_input" else selector.session_id
    for _ in range(3):
        with guard(owner_id) if guard else nullcontext():
            before = _read_source(context, selector, head_only=head_only)
            after = _read_source(context, selector, head_only=head_only)
            if before == after:
                return before
    raise CoverSourceError("source_changed", "Skeleton changed while being captured")


def _preview(selector: CoverSourceSelector, origin: dict, bodies: dict, paths: dict) -> CoverSourcePreview:
    documents = tuple({"document_id": key, "source_relative_path": paths[key],
                       "relative_path": f"documents/{key}.md", "sha256": hashlib.sha256(body).hexdigest(),
                       "byte_length": len(body)} for key, body in sorted(bodies.items()))
    bundle = canonical_hash({x["document_id"]: x["sha256"] for x in documents})
    return CoverSourcePreview(selector, hashlib.sha256(bodies["primary"]).hexdigest(), bundle, origin, documents,
                              bodies["primary"].decode("utf-8"))


def preview_source(context: CoverSourceContext, selector: CoverSourceSelector | Mapping[str, Any]) -> CoverSourcePreview:
    selector = selector if isinstance(selector, CoverSourceSelector) else CoverSourceSelector.from_dict(selector)
    return _preview(selector, *_stable_read(context, selector))


def _store_root(context: CoverSourceContext, name: str) -> Path:
    root = context.cover_root
    # Only adapter-owned storage paths are accepted, but do not follow a
    # subsequently substituted covers/source directory symlink.
    if root.is_symlink():
        raise CoverSourceError("invalid_scope", "Cover storage cannot be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    target = _safe_path(root, name)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def capture_source(context: CoverSourceContext, selector: CoverSourceSelector | Mapping[str, Any]) -> CoverSkeletonSnapshot:
    selector = selector if isinstance(selector, CoverSourceSelector) else CoverSourceSelector.from_dict(selector)
    origin, bodies, paths = _stable_read(context, selector)
    preview = _preview(selector, origin, bodies, paths)
    snapshot_id = f"snapshot-{uuid.uuid4().hex}"
    folder = _store_root(context, "sources") / snapshot_id
    folder.mkdir(exist_ok=False)
    (folder / "documents").mkdir()
    for item in preview.documents:
        with (folder / item["relative_path"]).open("xb") as stream:
            stream.write(bodies[item["document_id"]])
            stream.flush()
            os.fsync(stream.fileno())
    snapshot = CoverSkeletonSnapshot(snapshot_id, "", preview.source_content_sha256,
                                     preview.source_bundle_sha256, context.owner_scope, selector, origin,
                                     preview.documents, datetime.now(timezone.utc).isoformat(), bodies)
    payload = snapshot.to_dict()
    payload.pop("snapshot_sha256")
    payload["snapshot_sha256"] = canonical_hash(payload)
    _atomic_json(folder / "manifest.json", payload)
    return load_snapshot(context, snapshot_id)


def load_snapshot(context: CoverSourceContext, snapshot_id: str) -> CoverSkeletonSnapshot:
    if not _SNAPSHOT.fullmatch(snapshot_id):
        raise CoverSourceError("invalid_snapshot", "Invalid snapshot id")
    folder = _safe_path(context.cover_root, f"sources/{snapshot_id}")
    try:
        payload = json.loads(_read_file(folder, "manifest.json"))
        expected = payload.pop("snapshot_sha256")
        if (payload["schema_version"] != SOURCE_SCHEMA or payload["snapshot_id"] != snapshot_id
                or payload["owner_scope"] != context.owner_scope or canonical_hash(payload) != expected):
            raise ValueError("snapshot manifest identity mismatch")
        bodies: dict[str, bytes] = {}
        for item in payload["documents"]:
            key = item["document_id"]
            if not _DOCUMENT.fullmatch(key) or key in bodies or item["relative_path"] != f"documents/{key}.md":
                raise ValueError("invalid snapshot document")
            body = _read_file(folder, item["relative_path"])
            _utf8(body, name=key, maximum=context.max_source_bytes)
            if hashlib.sha256(body).hexdigest() != item["sha256"] or len(body) != item["byte_length"]:
                raise ValueError("snapshot document hash mismatch")
            bodies[key] = body
        bundle = canonical_hash({key: hashlib.sha256(body).hexdigest() for key, body in sorted(bodies.items())})
        if ("primary" not in bodies or bundle != payload["source_bundle_sha256"]
                or hashlib.sha256(bodies["primary"]).hexdigest() != payload["source_content_sha256"]):
            raise ValueError("snapshot source hash mismatch")
        return CoverSkeletonSnapshot(snapshot_id, expected, payload["source_content_sha256"], bundle,
                                     context.owner_scope, CoverSourceSelector.from_dict(payload["selector"]),
                                     payload["origin"], tuple(payload["documents"]), payload["created_at"], bodies)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        if isinstance(exc, CoverSourceError) and exc.code == "source_missing":
            raise CoverSourceError("snapshot_missing", "Snapshot does not exist") from exc
        raise CoverSourceError("snapshot_invalid", f"Snapshot integrity validation failed: {exc}") from exc


def check_source_head(context: CoverSourceContext, snapshot: CoverSkeletonSnapshot) -> CoverSourcePreview:
    """Check freshness separately from immutable integrity; never rebind it."""
    load_snapshot(context, snapshot.snapshot_id)
    raw = snapshot.selector.to_dict()
    raw.update(expected_content_sha256="", expected_session_revision=None)
    selector = CoverSourceSelector.from_dict(raw)
    current = _preview(selector, *_stable_read(context, selector, head_only=True))
    if current.source_bundle_sha256 != snapshot.source_bundle_sha256:
        raise CoverSourceError("source_changed", "Selected skeleton head changed",
                               actual_content_sha256=current.source_content_sha256,
                               expected_content_sha256=snapshot.source_content_sha256)
    return current
