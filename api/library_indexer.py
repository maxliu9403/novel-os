"""Bounded, read-only reconciliation into the rebuildable library catalog.

Only this background adapter reads story files. HTTP catalog queries never call
it. Source files, identities, locks, quality receipts and manuscripts are never
created or changed by indexing; derived counts and images live beside the query
database. A failed or racing read preserves the previous projection.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import re
import shutil
import threading
import time
from urllib.parse import quote
import weakref

from core.safe_project_io import project_file_stamp, read_project_bytes

from .tenancy import valid_project_id

_LOG = logging.getLogger(__name__)
INDEX_POLICY = "library-source-projection.v1"
COUNT_POLICY = "library-display-count.v1"
DELIVERY_POLICY = "library-delivery-snapshot.v1"
COVER_SOURCE_POLICY = "library-cover-source.v2"
THUMBNAIL_POLICY = "library-cover-webp.v1"
THUMBNAIL_WIDTHS = (160, 256, 384)
_SHA = re.compile(r"^[a-f0-9]{64}$")
_STATE = "outputs/state/story_state.json"
_IDENTITY = "outputs/state/project_identity.json"
_HEADS = "outputs/artifacts/heads.json"
_JOURNAL = "outputs/artifacts/revisions.jsonl"
_RECEIPT = "outputs/publication/export-validation.json"
_ARCHIVE = "outputs/deliverables/book-package.zip"
_COVER_INDEX = "outputs/covers/index.json"
_DELIVERY_STATUS = "outputs/covers/delivery-status.json"
_CACHE_LOCKS = weakref.WeakValueDictionary()
_CACHE_GUARD = threading.Lock()
_BASE_FILES = (_STATE, _IDENTITY, _HEADS, _JOURNAL, _RECEIPT,
               _ARCHIVE, _COVER_INDEX, _DELIVERY_STATUS,
               "outputs/private/source-context.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode()).hexdigest()


def _invalid_constant(value):
    raise ValueError(f"Non-finite JSON constant is invalid: {value}")


def _parts(relative: str) -> tuple[str, ...]:
    # safe_project_io also validates components and rejects every symlink.
    return tuple(relative.split("/"))


def _integer(value, default=0):
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _timestamp(value):
    if not isinstance(value, str):
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None
                else parsed.astimezone(timezone.utc)).isoformat()
    except ValueError:
        return ""


def _instance(snapshot):
    value = snapshot.document(_IDENTITY, maximum=64_000)
    if value is None:
        return ""
    identity = value.get("identity")
    if (set(value) != {"identity", "record_sha256"} or not isinstance(identity, dict)
            or set(identity) != {"schema_version", "project_instance_id"}
            or type(identity.get("schema_version")) is not int or identity["schema_version"] != 1
            or not isinstance(identity.get("project_instance_id"), str)
            or not re.fullmatch(r"[a-f0-9]{32}", identity["project_instance_id"])
            or value["record_sha256"] != _digest(identity)):
        raise ValueError("Project instance identity is invalid")
    return identity["project_instance_id"]


def count_unit(language: str) -> str:
    value = language.strip().casefold().replace("_", "-")
    return "characters" if (value.startswith(("zh", "ja", "ko")) or
        any(marker in value for marker in ("中文", "汉语", "簡體", "繁體", "简体", "繁体",
                                            "chinese", "japanese", "korean", "日文", "日语", "韩文", "韩语"))) else "words"


def display_count(text: str, unit: str) -> int:
    if unit == "words":
        return len(text.split())
    # Count CJK characters, plus each non-CJK word as one unit. This is a
    # display policy only: export/publication counters retain their contracts.
    cjk = r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]"
    return len(re.findall(cjk, text)) + len(re.findall(r"[^\W_]+(?:['’-][^\W_]+)*", re.sub(cjk, " ", text)))


class SnapshotChanged(ValueError):
    pass


class SourceSnapshot:
    """Optimistic snapshot of only sources actually used for this card."""
    def __init__(self, project: Path):
        self.project = project
        self.stamps: dict[str, tuple | None] = {}
        self._tracks: list[set[str]] = []

    def stamp(self, relative: str):
        for tracker in self._tracks:
            tracker.add(relative)
        stamp = project_file_stamp(self.project, _parts(relative))
        previous = self.stamps.setdefault(relative, stamp)
        if previous != stamp:
            raise SnapshotChanged("作品仍在保存，摘要将在下一轮同步。")
        return stamp

    @contextmanager
    def track(self):
        accessed: set[str] = set()
        self._tracks.append(accessed)
        try:
            yield accessed
        finally:
            self._tracks.pop()

    def read(self, relative: str, *, required=False, maximum=64 * 1024 * 1024):
        self.stamp(relative)
        raw = read_project_bytes(self.project, _parts(relative), missing_ok=not required,
                                 max_bytes=maximum)
        self.stamp(relative)
        return raw

    def document(self, relative: str, *, required=False, maximum=64 * 1024 * 1024):
        raw = self.read(relative, required=required, maximum=maximum)
        if raw is None:
            return None
        value = json.loads(raw, parse_constant=_invalid_constant)
        if not isinstance(value, dict):
            raise ValueError(f"{relative}: expected a JSON object")
        return value

    def verify(self):
        for relative, expected in self.stamps.items():
            if project_file_stamp(self.project, _parts(relative)) != expected:
                raise SnapshotChanged("作品仍在保存，摘要将在下一轮同步。")


def _children(project: Path, relative: str, *, directories=False) -> list[str]:
    """List names without following aliases; every eventual read uses fd IO."""
    path = project
    for part in _parts(relative):
        if part in {"", ".", ".."}:
            raise ValueError("Invalid source directory")
        path /= part
        if path.is_symlink():
            raise ValueError("Source directory aliases are not indexed")
    if not path.is_dir():
        return []
    with os.scandir(path) as entries:
        return sorted(entry.name for entry in entries if not entry.is_symlink()
                      and (entry.is_dir(follow_symlinks=False) if directories
                           else entry.is_file(follow_symlinks=False)))


def _metadata_paths(project: Path) -> list[str]:
    paths = list(_BASE_FILES)
    paths.extend(f"outputs/manuscript/{name}" for name in _children(project, "outputs/manuscript")
                 if re.fullmatch(r"chapter_\d+_(?:final|revised|draft)\.md", name))
    paths.extend(f"outputs/runs/{name}/run.json" for name in _children(project, "outputs/runs", directories=True))
    for directory in ("sets", "directions", "direction_tasks", "generation_tasks", "repair_tasks",
                      "poster/plans", "poster/tasks"):
        root = f"outputs/covers/{directory}"
        paths.extend(f"{root}/{name}" for name in _children(project, root)
                     if name.endswith(".json") and not name.endswith(".brief.json"))
    paths.extend(f"outputs/covers/analyses/{name}/analysis.json"
                 for name in _children(project, "outputs/covers/analyses", directories=True))
    return sorted(set(paths))


class LibraryIndexer:
    def __init__(self, catalog, root: Path, cache_root: Path, *, scan_interval=15.0,
                 scan_budget=32, work_budget=4):
        self.catalog = catalog
        self.root = Path(root).absolute()
        self.cache_root = Path(cache_root).absolute()
        if self.cache_root.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("Library cache must be outside canonical projects")
        self.scan_interval = max(.1, float(scan_interval))
        self.scan_budget = max(1, int(scan_budget))
        self.work_budget = max(1, int(work_budget))
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._scan_ids: list[str] = []
        self._scan_at = 0
        self.last_scan_at = None
        self.last_error = None
        self._next_scan = 0.0
        self._purged_epochs: set[tuple[str, int]] = set()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"library-index-{self.catalog.scope[:8]}", daemon=True)
        self._thread.start()

    def stop(self, timeout=5):
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=timeout)

    def wake(self):
        self._wake.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                if self._scan_at < len(self._scan_ids) or time.monotonic() >= self._next_scan:
                    self.scan_once()
                    if self._scan_at >= len(self._scan_ids):
                        self._next_scan = time.monotonic() + self.scan_interval
                count = self.process_once()
                delay = (.1 if self._scan_at < len(self._scan_ids) or count == self.work_budget
                         else max(.1, self._next_scan - time.monotonic()))
            except Exception as error:  # keep a single malformed book from stopping compensation
                self.last_error = str(error)
                _LOG.exception("Library index reconciliation failed")
                delay = self.scan_interval
            self._wake.wait(delay)
            self._wake.clear()

    def _project(self, project_id):
        if not valid_project_id(project_id):
            raise ValueError("Invalid project id")
        project = self.root / project_id
        if project.is_symlink() or project.resolve().parent != self.root.resolve():
            raise ValueError("Project directory aliases are not indexed")
        return project

    def _fingerprint(self, project_id, *, paths=None):
        project = self._project(project_id)
        paths = _metadata_paths(project) if paths is None else paths
        dependency = self.catalog.get_component(project_id, "dependencies") or {}
        paths = sorted(set(paths) | set(dependency.get("paths", ())))
        stamps = []
        for path in paths:
            stamps.append((path, project_file_stamp(project, _parts(path))))
        return _digest([INDEX_POLICY, COVER_SOURCE_POLICY, stamps])

    def scan_once(self):
        """Discover cheaply, then rotate through a bounded metadata-only audit.

        This method is explicitly background work, never a GET fallback. All
        projects get placeholders before the first manuscript is read.
        """
        if self._scan_at >= len(self._scan_ids):
            if not self.root.is_dir() or self.root.is_symlink():
                for record in self.catalog.list_records():
                    if record.get("tombstone"):
                        self._purge_discovered_tombstone(record)
                    if not record.get("tombstone") and record.get("freshness") not in {"stale", "unavailable"}:
                        self.catalog.invalidate(record.get("project_id") or record["id"], domains=("all",))
                self.catalog.mark_scan_complete()
                return 0
            found = []
            with os.scandir(self.root) as entries:
                for entry in entries:
                    if self._stop.is_set():
                        break
                    if not entry.is_dir(follow_symlinks=False) or not valid_project_id(entry.name):
                        continue
                    project = self._project(entry.name)
                    try:
                        state = project_file_stamp(project, _parts(_STATE))
                    except (OSError, ValueError):
                        state = None
                    if state is not None:
                        found.append(entry.name)
                        existing = self.catalog.get_record(entry.name)
                        if existing is None:
                            self.catalog.discover(entry.name, fingerprint=_digest([(_STATE, state)]))
            found.sort()
            found_set = set(found)
            for record in self.catalog.list_records():
                project_id = record.get("project_id") or record["id"]
                if record.get("tombstone"):
                    self._purge_discovered_tombstone(record)
                    continue
                if project_id not in found_set and not record.get("tombstone"):
                    # Only actual directory removal is a tombstone. A malformed
                    # state or transient file replacement remains a stale card.
                    if not (self.root / project_id).exists():
                        self.catalog.tombstone(project_id)
                        self.purge_project(project_id)
                    elif record.get("freshness") not in {"stale", "unavailable"}:
                        self.catalog.invalidate(project_id, domains=("metadata",))
            self._scan_ids, self._scan_at = found, 0
        stop = min(len(self._scan_ids), self._scan_at + self.scan_budget)
        for project_id in self._scan_ids[self._scan_at:stop]:
            record = self.catalog.get_record(project_id)
            if not record or record.get("tombstone"):
                continue
            try:
                fingerprint = self._fingerprint(project_id)
                instance_id = _instance(SourceSnapshot(self._project(project_id)))
                if fingerprint != record.get("fingerprint") or instance_id != record.get("instance_id", ""):
                    self.catalog.discover(project_id, instance_id=instance_id, fingerprint=fingerprint)
            except (OSError, ValueError) as error:
                if record.get("freshness") not in {"stale", "unavailable"}:
                    self.catalog.invalidate(project_id, domains=("all",))
                self.last_error = str(error)
        processed = stop - self._scan_at
        self._scan_at = stop
        self.last_scan_at = _now()
        if self._scan_at >= len(self._scan_ids):
            self.catalog.mark_scan_complete()
        return processed

    def process_once(self, limit=None):
        processed = 0
        for token in self.catalog.pending(limit=self.work_budget if limit is None else limit):
            if self._stop.is_set():
                break
            project_id = token["project_id"]
            try:
                summary, fingerprint = self.build(project_id, token)
                self.catalog.publish(project_id, summary, expected_epoch=token["epoch"],
                                     expected_generation=token["generation"], fingerprint=fingerprint,
                                     error="; ".join(f"{d['domain']}: {d['message']}" for d in summary.get("index_diagnostics", ())))
            except Exception as error:
                self.catalog.fail(token, str(error))
                self.last_error = str(error)
            processed += 1
        return processed

    def _cache(self, project_id, key, value, token):
        self.catalog.set_component(project_id, key, value, expected_epoch=token["epoch"])

    def _domain(self, snapshot, project_id, token, key, paths, compute, *, extra=None):
        """Reuse a domain only while every source it consumed is unchanged.

        Notification labels prioritize work, but never justify overlooking an
        external edit. Saved dependency stamps provide that proof, including
        the exact selected blob/image and every delivery-bound source.
        """
        cached = self.catalog.get_component(project_id, f"domain:{key}")
        dependencies = sorted(set(paths) | set((cached or {}).get("dependencies", ())))
        fingerprint = _digest([INDEX_POLICY, extra, [(p, snapshot.stamp(p)) for p in dependencies]])
        if cached and cached.get("fingerprint") == fingerprint:
            derivatives_present = True
            if key == "cover" and (cached.get("value") or {}).get("thumbnail_urls"):
                for width, url in cached["value"]["thumbnail_urls"].items():
                    try:
                        self.thumbnail_path(project_id, url.split("/")[-2], int(width))
                    except (ValueError, IndexError, FileNotFoundError):
                        derivatives_present = False
                        break
            if derivatives_present:
                return cached["value"]
        with snapshot.track() as accessed:
            value = compute()
        dependencies = sorted(set(paths) | accessed)
        fingerprint = _digest([INDEX_POLICY, extra, [(p, snapshot.stamp(p)) for p in dependencies]])
        self._cache(project_id, f"domain:{key}", {
            "fingerprint": fingerprint, "dependencies": dependencies, "value": value,
        }, token)
        return value

    def _state(self, snapshot):
        state = snapshot.document(_STATE, required=True)
        metadata = state.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("Story metadata must be an object")
        chapters = state.get("chapters") or {}
        if not isinstance(chapters, dict) or any(not isinstance(c, dict) for c in chapters.values()):
            raise ValueError("Chapter collection is invalid")
        fields = ("title", "language", "genre", "genres", "author", "content_rating", "created", "status",
                  "target_word_count", "session_word_target", "target_chapters", "classification", "narrative_format")
        metadata = {key: metadata[key] for key in fields if key in metadata} | {
            "premise": str(metadata.get("premise") or (state.get("story_bible") or {}).get("premise") or "")[:600],
        }
        return {"metadata": metadata, "chapters": {key: {name: c[name] for name in ("number", "status", "word_count") if name in c}
                                                     for key, c in chapters.items()}}

    def _heads(self, snapshot, project_id, token):
        stamp = _digest([snapshot.stamp(_HEADS), snapshot.stamp(_JOURNAL)])
        cached = self.catalog.get_component(project_id, "artifact-heads")
        if cached and cached.get("stamp") == stamp:
            return cached["heads"]
        heads = snapshot.document(_HEADS)
        if heads is None:
            return {}
        if type(heads.get("schema_version")) is not int or heads.get("schema_version") != 1 or not isinstance(heads.get("heads"), list):
            raise ValueError("Artifact heads format is not supported")
        raw = snapshot.read(_JOURNAL, required=True, maximum=256 * 1024 * 1024)
        from core.artifacts import _derive_revision_id, _revision_from_record
        revisions = {}
        for line in raw.decode("utf-8").splitlines():
            revision = _revision_from_record(json.loads(line))
            value = revision.to_dict()
            if revision.revision_id != _derive_revision_id({k: v for k, v in value.items() if k != "revision_id"}):
                raise ValueError("Artifact revision metadata does not match its id")
            if revision.revision_id in revisions:
                raise ValueError("Duplicate artifact revision")
            revisions[revision.revision_id] = value
        selected = {}
        for head in heads["heads"]:
            if not isinstance(head, dict):
                raise ValueError("Malformed artifact head")
            revision = revisions.get(head.get("revision_id"))
            if not revision or any(revision.get(key) != head.get(key) for key in ("chapter", "kind")):
                raise ValueError("Artifact head does not match its revision")
            key = f"{head['chapter']}:{head['kind']}"
            if key in selected:
                raise ValueError("Duplicate artifact head")
            selected[key] = revision
        self._cache(project_id, "artifact-heads", {"stamp": stamp, "heads": selected}, token)
        return selected

    def _chapters(self, snapshot, project_id, token, state, language):
        chapters = state.get("chapters") or {}
        if not isinstance(chapters, dict):
            raise ValueError("Chapter collection is invalid")
        heads = self._heads(snapshot, project_id, token)
        unit = count_unit(language)
        total = drafted = final = 0
        statuses = []
        for key, chapter in chapters.items():
            if self._stop.is_set():
                raise SnapshotChanged("Index worker stopped at a chapter boundary")
            if not isinstance(chapter, dict):
                raise ValueError("Chapter record is invalid")
            number = _integer(chapter.get("number", key))
            if number <= 0:
                raise ValueError("Chapter number must be positive")
            selected = None
            for kind in ("final", "revised", "draft"):
                revision = heads.get(f"{number}:{kind}")
                if revision:
                    digest = revision.get("sha256", "")
                    if not _SHA.fullmatch(digest):
                        raise ValueError("Invalid artifact blob digest")
                    path = f"outputs/artifacts/sha256/{digest}"
                    selected = (kind, path, revision)
                    break
                path = f"outputs/manuscript/chapter_{number:03d}_{kind}.md"
                if snapshot.stamp(path) is not None:
                    selected = (kind, path, None)
                    break
            status = str(chapter.get("status") or "planned")
            words = _integer(chapter.get("word_count"))
            if selected:
                kind, path, revision = selected
                stamp = snapshot.stamp(path)
                identity = _digest([COUNT_POLICY, unit, kind, revision, stamp])
                cached = self.catalog.get_component(project_id, f"chapter:{number}")
                if cached and cached.get("identity") == identity:
                    words, has_text = cached["count"], cached["has_text"]
                else:
                    raw = snapshot.read(path, required=True)
                    if revision and (len(raw) != revision["byte_length"] or sha256(raw).hexdigest() != revision["sha256"]):
                        raise ValueError(f"Chapter {number} artifact blob integrity mismatch")
                    text = raw.decode("utf-8")
                    has_text = bool(text.strip())
                    words = display_count(text, unit)
                    self._cache(project_id, f"chapter:{number}", {"identity": identity,
                        "count": words, "has_text": has_text, "stage": kind,
                        "revision_id": revision.get("revision_id") if revision else None,
                        "policy": COUNT_POLICY, "unit": unit}, token)
                if kind == "final":
                    status = "complete"
                    final += 1
                elif has_text and status == "planned":
                    status = "drafted"
            total += words
            drafted += int(status != "planned")
            statuses.append(status)
        return {"word_count": total, "drafted_count": drafted, "chapter_count": len(chapters),
                "final_count": final, "count_unit": unit, "count_policy": COUNT_POLICY}, statuses

    def _latest_task(self, snapshot, paths):
        latest = None
        for path in paths:
            if not path.startswith("outputs/runs/") or not path.endswith("/run.json"):
                continue
            data = snapshot.document(path)
            if data is None:
                continue
            if not isinstance(data.get("spec"), dict) or data.get("run_id") != path.split("/")[-2]:
                raise ValueError("Task record does not match its owner")
            if data["spec"].get("dry_run"):
                continue
            if data.get("status") not in {"pending", "running", "paused", "completed", "failed", "cancelled"}:
                raise ValueError("Task status is invalid")
            if any(data.get(field) is not None and not isinstance(data[field], str) for field in ("error", "current_phase")):
                raise ValueError("Task display fields are invalid")
            order = (_timestamp(data.get("created_at")), data["run_id"])
            if latest is None or order > latest[0]:
                latest = (order, data)
        return ({key: latest[1].get(key) for key in ("status", "run_id", "created_at", "current_phase", "error")}
                if latest else None)

    def _task(self, data, metadata, statuses):
        if data:
            saved_status = data["status"]
            return {"status": "interrupted" if saved_status == "running" else saved_status,
                    "saved_status": saved_status, "task_id": data["run_id"],
                    "task_started_at": _timestamp(data.get("created_at")) or None,
                    "task_phase": data.get("current_phase"), "task_error": data.get("error"),
                    "saved_task_error": data.get("error"),
                    # A card is not a recovery authorization. Actual resume
                    # route performs the unchanged request/quality checks.
                    "task_can_resume": saved_status in {"paused", "failed"},
                    "task_resume_job_id": None}
        status = metadata.get("status")
        if status not in {"completed", "failed", "paused", "cancelled", "interrupted"}:
            target = max(len(statuses), _integer(metadata.get("target_chapters")))
            status = ("completed" if statuses and len(statuses) >= target and all(s == "complete" for s in statuses)
                      else "in_progress" if any(s != "planned" for s in statuses) else "draft")
        return {"status": status, "saved_status": status, "task_can_resume": False}

    def _cover(self, snapshot, project_id, token, paths):
        base = {"status": "not_started", "detail": "尚未设计封面。", "image_url": None,
                "image_kind": None, "cover_set_id": None, "candidate_id": None, "candidate_count": 0}
        index = snapshot.document(_COVER_INDEX) or {}
        set_paths = [p for p in paths if p.startswith("outputs/covers/sets/") and p.endswith(".json")]
        # Read only the active set and the most recently committed set. Metadata
        # history is not loaded merely to find an artwork preview.
        latest_path = max(set_paths, key=lambda p: snapshot.stamp(p)[3] if snapshot.stamp(p) else 0, default=None)
        latest = snapshot.document(latest_path, maximum=16 * 1024 * 1024) if latest_path else None
        active_id = index.get("cover_set_id")
        active = None
        if active_id:
            if not re.fullmatch(r"cover-[0-9a-f]{32}", str(active_id)):
                raise ValueError("Invalid active cover reference")
            active_path = f"outputs/covers/sets/{active_id}.json"
            active = latest if active_path == latest_path else snapshot.document(active_path, maximum=16 * 1024 * 1024)
        for value in (latest, active):
            if value and (value.get("project_id") != project_id or not isinstance(value.get("candidates"), list)):
                raise ValueError("Cover set owner or candidates are invalid")
            if value and any(not isinstance(candidate, dict) for candidate in value["candidates"]):
                raise ValueError("Cover candidate record is invalid")
        selected = next((c for c in (active or {}).get("candidates", ()) if c.get("candidate_id") == active.get("selected_candidate_id")
                         and c.get("status") == "selected"), None) if active else None
        preview = selected or next((c for c in (latest or {}).get("candidates", ())
                                   if c.get("status") in {"ready", "selected"}), None)
        cover_set = active if selected else latest
        if latest:
            candidates = latest["candidates"]
            base.update(candidate_count=len(candidates), cover_set_id=latest.get("cover_set_id"),
                        status="awaiting_review", detail="候选已保存，可打开封面工作室审阅。")
            if latest.get("status") == "stale" or any(c.get("status") in {"failed", "pending"}
                or (c.get("quality_report") or {}).get("status") == "blocked" for c in candidates):
                base.update(status="needs_attention", detail="存在待修复或未完成的封面，请打开工作室查看。")
        if selected and base["status"] != "needs_attention":
            base.update(status="selected", detail="已选定作品封面。")
        elif selected:
            base["detail"] += "已保留原选定封面。"
        if preview:
            path, digest = preview.get("relative_path"), preview.get("sha256")
            if not isinstance(path, str) or not isinstance(digest, str) or not _SHA.fullmatch(digest):
                raise ValueError("Cover image provenance is incomplete")
            base.update(cover_set_id=cover_set.get("cover_set_id"), candidate_id=preview.get("candidate_id"),
                        image_kind="selected" if selected else "candidate")
            urls = self._thumbnails(snapshot, project_id, token, path, digest,
                                    selected_content_type=preview.get("content_type") if selected else None)
            base.update(thumbnail_urls=urls, thumbnail_url=urls["256"], image_url=urls["256"],
                        thumbnail_srcset=", ".join(f"{url} {width}w" for width, url in urls.items()))
        # Latest task/plan per domain is enough for a coarse next action. Saved
        # running never proves a live owner; the catalog overlays live jobs.
        events = []
        for directory in ("direction_tasks", "generation_tasks", "repair_tasks", "poster/tasks",
                          "directions", "poster/plans", "analyses"):
            choices = [p for p in paths if p.startswith(f"outputs/covers/{directory}/")]
            path = max(choices, key=lambda p: snapshot.stamp(p)[3] if snapshot.stamp(p) else 0, default=None)
            if path:
                event = snapshot.document(path, maximum=32 * 1024 * 1024)
                if event:
                    events.append((_timestamp(event.get("updated_at") or event.get("created_at")), directory, event))
        event = max(events, key=lambda row: row[0], default=None)
        if event and event[0] > _timestamp((latest or {}).get("updated_at") or (latest or {}).get("created_at")):
            _, directory, value = event
            status = value.get("status")
            if status in {"running", "queued", "planning", "generating", "failed", "interrupted", "stale", "rejected"}:
                base.update(status="needs_attention", detail="封面任务需要继续处理，请打开工作室查看已保存进度。")
            elif status in {"awaiting_approval", "approved", "draft"} or directory == "poster/plans":
                base.update(status="awaiting_review", detail="封面方案已保存，等待审核或生成。")
        return base

    def _thumbnails(self, snapshot, project_id, token, relative, source_digest, *, selected_content_type=None):
        derivative = _digest([THUMBNAIL_POLICY, source_digest])
        directory = self.cache_root / self.catalog.scope / project_id / derivative
        paths = {str(w): directory / f"{w}.webp" for w in THUMBNAIL_WIDTHS}
        validation = self.catalog.get_component(project_id, "cover-image")
        complete = all(p.is_file() and not p.is_symlink() for p in paths.values())
        sources = [relative]
        # CoverService._project_selected persists the selected bytes separately.
        # Historical candidates reused pending/cover-NN paths across sets; those
        # paths may now contain another candidate without changing the selection.
        # Only an active selected candidate can use this projection, and its
        # exact expected digest still governs both possible sources.
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(selected_content_type)
        if extension:
            sources.insert(0, f"outputs/deliverables/covers/selected-cover{extension}")
        raw = None
        found = False
        for source in dict.fromkeys(sources):
            stamp = snapshot.stamp(source)
            if stamp is None:
                continue
            found = True
            identity = _digest([source, source_digest, stamp])
            if complete and validation and validation.get("identity") == identity:
                break
            candidate_raw = snapshot.read(source, required=True)
            if sha256(candidate_raw).hexdigest() != source_digest:
                continue
            raw = candidate_raw
            self._cache(project_id, "cover-image", {"identity": identity}, token)
            break
        else:
            raise ValueError("Cover image does not match its saved digest" if found else "Cover image is missing")
        if not complete:
            rendered = self._render_thumbnails(raw)
            # Rendering never holds the deletion fence. A delete may finish
            # while the decoder works; only a still-current project epoch may
            # create cache files afterward. Purge uses the same shared lock.
            with self._thumbnail_lock(project_id):
                record = self.catalog.get_record(project_id)
                if not record or record.get("tombstone") or record["epoch"] != token["epoch"]:
                    raise SnapshotChanged("作品已删除或替换，缩略图不再保存。")
                self._cache_project_directory(project_id)
                directory.mkdir(parents=True, exist_ok=True)
                for width, path in paths.items():
                    if directory.is_symlink() or path.is_symlink():
                        raise ValueError("Thumbnail cache aliases are not writable")
                    temporary = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
                    try:
                        temporary.write_bytes(rendered[width])
                        os.replace(temporary, path)
                    finally:
                        temporary.unlink(missing_ok=True)
        return {width: f"/api/library/{quote(project_id, safe='')}/thumbnails/{derivative}/{width}.webp"
                for width in paths}

    @staticmethod
    def _render_thumbnails(raw):
        from PIL import Image, ImageOps
        rendered = {}
        with Image.open(BytesIO(raw)) as image:
            image.load()
            image = ImageOps.exif_transpose(image).convert("RGB")
            for width in THUMBNAIL_WIDTHS:
                copy = image.copy()
                copy.thumbnail((width, width * 3 // 2), Image.Resampling.LANCZOS)
                buffer = BytesIO()
                copy.save(buffer, format="WEBP", quality=82, method=4)
                rendered[str(width)] = buffer.getvalue()
        return rendered

    def _thumbnail_lock(self, project_id):
        if not valid_project_id(project_id):
            raise ValueError("Invalid project id")
        key = str(self.cache_root / self.catalog.scope / project_id)
        with _CACHE_GUARD:
            lock = _CACHE_LOCKS.get(key)
            if lock is None:
                lock = threading.RLock()
                _CACHE_LOCKS[key] = lock
            return lock

    def _cache_project_directory(self, project_id):
        if not valid_project_id(project_id):
            raise ValueError("Invalid project id")
        namespace = self.cache_root / self.catalog.scope
        if self.cache_root.is_symlink() or namespace.is_symlink():
            raise ValueError("Thumbnail cache namespace is unsafe")
        project_cache = namespace / project_id
        if project_cache.is_symlink():
            raise ValueError("Thumbnail project cache is unsafe")
        return project_cache

    def purge_project(self, project_id):
        """Idempotently remove derived images after the durable tombstone.

        No canonical path participates. The process-wide scoped lock also
        fences old indexer instances still decoding an image during shutdown.
        """
        with self._thumbnail_lock(project_id):
            record = self.catalog.get_record(project_id)
            if record and not record.get("tombstone"):
                raise ValueError("Persist the library tombstone before purging thumbnails")
            directory = self._cache_project_directory(project_id)
            if directory.exists():
                shutil.rmtree(directory)
            if record:
                self._purged_epochs.add((project_id, record["epoch"]))

    def _purge_discovered_tombstone(self, record):
        project_id = record.get("project_id") or record["id"]
        if (project_id, record["epoch"]) not in self._purged_epochs:
            try:
                self.purge_project(project_id)
            except (OSError, ValueError) as error:
                self.last_error = f"Thumbnail cleanup pending: {error}"
                _LOG.warning("Library thumbnail cleanup will retry for %s: %s", project_id, error)

    def thumbnail_path(self, project_id, digest, width):
        if not valid_project_id(project_id) or not _SHA.fullmatch(str(digest)) or int(width) not in THUMBNAIL_WIDTHS:
            raise FileNotFoundError("Thumbnail not found")
        record = self.catalog.get_record(project_id)
        if not record or record.get("tombstone"):
            raise FileNotFoundError("Thumbnail not found")
        cover = (record.get("summary") or {}).get("cover") or {}
        urls = list((cover.get("thumbnail_urls") or {}).values()) + [cover.get("thumbnail_url")]
        advertised = {f"/api/library/{quote(project_id, safe='')}/thumbnails/{digest}/{w}.webp"
                      for w in THUMBNAIL_WIDTHS}
        if not any(isinstance(url, str) and url in advertised for url in urls):
            # An orphaned derivative from a prior epoch/selection is not a
            # public resource of a newly-created book with the same slug.
            raise FileNotFoundError("Thumbnail not found")
        root = self.cache_root / self.catalog.scope
        path = root / project_id / digest / f"{int(width)}.webp"
        if any(parent.is_symlink() for parent in (root, path.parent.parent, path.parent, path)) or not path.is_file():
            raise FileNotFoundError("Thumbnail not found")
        return path

    def _delivery(self, snapshot, project_id, token, cover):
        status = snapshot.document(_DELIVERY_STATUS) or {}
        receipt = snapshot.document(_RECEIPT)
        archive = snapshot.stamp(_ARCHIVE)
        base = {"status": "missing", "retryable": True, "error": None,
                "updated_at": status.get("updated_at"), "observed_at": _now(),
                "validation_state": "not_verified"}
        if status.get("status") in {"pending", "failed"}:
            return {**base, "status": status["status"], "error": status.get("error") or None}
        if archive is None:
            return base
        if not receipt:
            return {**base, "status": "stale", "error": "交付包缺少校验记录，请打开作品重新核验。"}
        sources = receipt.get("sources")
        if not isinstance(sources, dict):
            raise ValueError("Delivery source receipt is invalid")
        stamps = [snapshot.stamp(_RECEIPT), archive]
        for path in sorted(sources):
            if not isinstance(path, str) or not path.startswith("outputs/"):
                raise ValueError("Delivery receipt contains an invalid source path")
            stamps.append((path, snapshot.stamp(path)))
        fingerprint = _digest([DELIVERY_POLICY, cover.get("cover_set_id"), cover.get("candidate_id"), stamps])
        cached = self.catalog.get_component(project_id, "delivery")
        if cached and cached.get("fingerprint") == fingerprint:
            return {**cached["value"], "observed_at": _now()}
        try:
            # Expensive, complete byte binding happens only in this background
            # domain on source changes. Download still performs the independent
            # current package/ZIP checks through its original authoritative API.
            from core.delivery_validation import current_package_binding_sha
            package_sha = current_package_binding_sha(snapshot.project)
            if status.get("archive_sha256") and status["archive_sha256"] != package_sha:
                raise ValueError("Delivery status package does not match")
            if status.get("candidate_id") and status.get("candidate_id") != cover.get("candidate_id"):
                raise ValueError("Delivery cover selection changed")
            base.update(status="ready", retryable=False, validation_state="verified_snapshot",
                        package_sha256=package_sha, validated_at=_now())
        except (OSError, ValueError) as error:
            base.update(status="stale", error=f"交付包需要重新核验：{error}")
        snapshot.verify()
        self._cache(project_id, "delivery", {"fingerprint": fingerprint, "value": base}, token)
        return base

    def build(self, project_id, token):
        project = self._project(project_id)
        snapshot = SourceSnapshot(project)
        paths = _metadata_paths(project)
        inventory = [(path, snapshot.stamp(path)) for path in paths]
        state = self._domain(snapshot, project_id, token, "state", [_STATE], lambda: self._state(snapshot))
        instance_id = _instance(snapshot)
        metadata = state.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("Story metadata must be an object")
        language = str(metadata.get("language") or "")
        genres = metadata.get("genres")
        genres = [str(v) for v in genres] if isinstance(genres, list) else []
        genre = str(metadata.get("genre") or (" / ".join(genres)))
        summary = {"id": project_id, "title": str(metadata.get("title") or project_id),
                   "genre": genre, "author": str(metadata.get("author") or ""), "genres": genres,
                   "language": language, "content_rating": metadata.get("content_rating", "general"),
                   "project_instance_id": instance_id or None,
                   "created_at": _timestamp(metadata.get("created")) or None,
                   "updated_at": datetime.fromtimestamp(snapshot.stamps[_STATE][3] / 1e9, timezone.utc).isoformat(),
                   # Query excerpt only. Complete story data stays untouched and
                   # is never replaced with this card's display excerpt.
                   "premise": str(metadata.get("premise") or "")[:600],
                   "target_word_count": _integer(metadata.get("target_word_count"), 80000),
                   "session_word_target": _integer(metadata.get("session_word_target"), 1000),
                   "classification": metadata.get("classification") or {},
                   "narrative_format": metadata.get("narrative_format") or {},
                   "index_policy": INDEX_POLICY, "index_diagnostics": [], "observed_at": _now()}
        previous = (self.catalog.get_record(project_id) or {}).get("summary") or {}
        diagnostics = summary["index_diagnostics"]
        try:
            chapter_paths = [_HEADS, _JOURNAL, *[p for p in paths if p.startswith("outputs/manuscript/")]]
            counts, statuses = self._domain(snapshot, project_id, token, "chapters", chapter_paths,
                lambda: self._chapters(snapshot, project_id, token, state, language),
                extra=[COUNT_POLICY, language, state.get("chapters")])
            summary.update(counts)
        except SnapshotChanged:
            raise
        except (OSError, ValueError, TypeError, KeyError) as error:
            statuses = []
            summary.update({key: previous.get(key, 0) for key in ("word_count", "drafted_count", "chapter_count", "final_count")})
            summary.update(count_unit=count_unit(language), count_policy=COUNT_POLICY)
            diagnostics.append({"domain": "chapters", "message": str(error)})
        try:
            task_paths = [p for p in paths if p.startswith("outputs/runs/")]
            task = self._domain(snapshot, project_id, token, "task", task_paths,
                                lambda: self._latest_task(snapshot, task_paths))
            summary.update(self._task(task, metadata, statuses))
            if summary["status"] == "draft" and summary.get("word_count", 0):
                summary.update(status="in_progress", saved_status="in_progress")
        except (OSError, ValueError, TypeError, KeyError) as error:
            summary.update(status="interrupted", task_can_resume=False, task_error="任务摘要无法读取，请打开作品检查。")
            diagnostics.append({"domain": "task", "message": str(error)})
        for domain, operation in (
            ("cover", lambda: self._domain(snapshot, project_id, token, "cover",
                [p for p in paths if p.startswith("outputs/covers/") and p != _DELIVERY_STATUS],
                lambda: self._cover(snapshot, project_id, token, paths), extra=COVER_SOURCE_POLICY)),
            ("delivery", lambda: self._domain(snapshot, project_id, token, "delivery",
                [_RECEIPT, _ARCHIVE, _DELIVERY_STATUS, _COVER_INDEX],
                lambda: self._delivery(snapshot, project_id, token, summary.get("cover") or {}),
                extra=[DELIVERY_POLICY, (summary.get("cover") or {}).get("cover_set_id"),
                       (summary.get("cover") or {}).get("candidate_id")])),
        ):
            try:
                summary[domain] = operation()
            except SnapshotChanged:
                raise
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
                diagnostics.append({"domain": domain, "message": str(error)})
                summary[domain] = {**previous.get(domain, {}), "status": "needs_attention" if domain == "cover" else "stale",
                                   "detail" if domain == "cover" else "error": "摘要暂不可用，请打开作品检查。"}
        try:
            from core.source_provenance import source_summary
            summary["source"] = self._domain(snapshot, project_id, token, "source", ["outputs/private/source-context.json"],
                lambda: source_summary(snapshot.document("outputs/private/source-context.json")))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            summary["source"] = previous.get("source")
            diagnostics.append({"domain": "source", "message": str(error)})
        snapshot.verify()
        if [(path, project_file_stamp(project, _parts(path))) for path in _metadata_paths(project)] != inventory:
            raise SnapshotChanged("作品资料在索引期间更新，下一轮重新同步。")
        self._cache(project_id, "dependencies", {"paths": sorted(snapshot.stamps)}, token)
        summary["freshness"] = "stale" if diagnostics else "fresh"
        # Bind the card to the exact sources it read. Resampling here could
        # pair an old card with a new file stamp and hide that edit forever.
        # A source commit after verify leaves this older fingerprint visible
        # to the next reconciliation pass, which then invalidates the card.
        return summary, _digest([INDEX_POLICY, COVER_SOURCE_POLICY, sorted(snapshot.stamps.items())])
