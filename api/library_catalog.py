"""Rebuildable, scoped read model for the library.

Only the indexer reads story files. Queries here read SQLite summaries and one
detached JobRunner snapshot; a missing index never falls back to story loading.
The catalog is deliberately not an authority for download, resume or promotion.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterator
import uuid

from .tenancy import valid_project_id


SCHEMA_VERSION = 1
CHANGE_RETENTION = 5000
STATUSES = {"all", "writing", "completed", "attention", "draft"}
COVERS = {"all", "not_started", "processing", "awaiting_review", "needs_attention", "selected"}
SORTS = {"recent", "title", "updated"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _encode(value: dict) -> str:
    return base64.urlsafe_b64encode(_json(value).encode()).decode().rstrip("=")


class CatalogCursorError(ValueError):
    """A cursor cannot describe this query anymore; retain cards and requery."""

    reset_required = True


def _decode(value: str) -> dict:
    try:
        if not isinstance(value, str) or len(value) > 8192:
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        if not isinstance(data, dict) or data.get("v") != SCHEMA_VERSION:
            raise ValueError
        return data
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise CatalogCursorError("作品库分页版本已变化，请刷新当前结果。") from exc


def _placeholder(project_id: str) -> dict:
    return {
        "id": project_id, "title": project_id, "genre": "", "author": "",
        "chapter_count": 0, "drafted_count": 0, "word_count": 0,
        "status": "draft", "task_can_resume": False,
        "cover": {"status": "not_started", "image_url": None},
        "delivery": {"status": "missing", "retryable": False},
    }


def _stale_summary(row: sqlite3.Row, domains: tuple | list = ("all",)) -> str:
    """Expose source invalidation immediately, without claiming a failed gate."""
    summary = json.loads(row["summary_json"])
    if set(domains).intersection({"all", "metadata", "chapters", "artifacts", "cover", "delivery", "canon", "state"}):
        delivery = summary.get("delivery") or {}
        if delivery.get("status") == "ready":
            summary["delivery"] = {**delivery, "status": "stale", "error": "作品资料已更新，交付摘要正在同步。"}
    return _json(summary)


class LibraryCatalog:
    """Small interface around durable summaries, rebuild intents and cursors.

    `root` is the already-authorized workspace directory, not a client path.
    The supplied database can be the application's SQLite file; all tables are
    additive and namespaced. No canonical project file is opened or created.
    """

    def __init__(self, database: Path, root: Path, workspace_id: str = "default"):
        self.database = Path(database)
        self.root = Path(root).resolve()
        self.workspace_id = workspace_id
        self.scope = _digest([str(self.root), workspace_id])
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connection(initialize=True) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS library_scopes (
                    scope TEXT PRIMARY KEY, root TEXT NOT NULL, workspace TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    member_revision INTEGER NOT NULL DEFAULT 0,
                    min_revision INTEGER NOT NULL DEFAULT 0,
                    observed_at TEXT NOT NULL DEFAULT '',
                    scan_completed_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS library_projects (
                    scope TEXT NOT NULL, project_id TEXT NOT NULL,
                    epoch INTEGER NOT NULL DEFAULT 1,
                    generation INTEGER NOT NULL DEFAULT 1,
                    instance_id TEXT NOT NULL DEFAULT '',
                    legacy_identity TEXT NOT NULL,
                    tombstone INTEGER NOT NULL DEFAULT 0,
                    fingerprint TEXT NOT NULL DEFAULT '',
                    summary_json TEXT NOT NULL,
                    title TEXT NOT NULL, genre TEXT NOT NULL DEFAULT '',
                    search_text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
                    cover_status TEXT NOT NULL DEFAULT 'not_started',
                    task_present INTEGER NOT NULL DEFAULT 0,
                    sort_time TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL,
                    observed_at TEXT NOT NULL DEFAULT '',
                    freshness TEXT NOT NULL DEFAULT 'rebuilding',
                    error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (scope, project_id)
                );
                CREATE INDEX IF NOT EXISTS library_recent ON library_projects
                    (scope,tombstone,task_present DESC,sort_time DESC,project_id);
                CREATE INDEX IF NOT EXISTS library_status ON library_projects
                    (scope,tombstone,status,cover_status);
                CREATE INDEX IF NOT EXISTS library_title ON library_projects
                    (scope,tombstone,title,project_id);
                CREATE TABLE IF NOT EXISTS library_work (
                    scope TEXT NOT NULL, project_id TEXT NOT NULL,
                    epoch INTEGER NOT NULL, generation INTEGER NOT NULL,
                    domains_json TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0,
                    queued_at TEXT NOT NULL,
                    PRIMARY KEY (scope, project_id)
                );
                CREATE INDEX IF NOT EXISTS library_work_due ON library_work
                    (scope,next_attempt,queued_at);
                CREATE TABLE IF NOT EXISTS library_changes (
                    scope TEXT NOT NULL, revision INTEGER NOT NULL,
                    project_id TEXT NOT NULL, epoch INTEGER NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    membership_changed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (scope, revision)
                );
                CREATE TABLE IF NOT EXISTS library_components (
                    scope TEXT NOT NULL, project_id TEXT NOT NULL,
                    epoch INTEGER NOT NULL, component_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (scope,project_id,component_key)
                );
            """)
            connection.execute(
                "INSERT OR IGNORE INTO library_scopes(scope,root,workspace) VALUES(?,?,?)",
                (self.scope, str(self.root), self.workspace_id),
            )

    @contextmanager
    def _connection(self, *, write: bool = False, initialize: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.database), timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            if not initialize:
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _scope_row(self, connection: sqlite3.Connection) -> sqlite3.Row:
        return connection.execute("SELECT * FROM library_scopes WHERE scope=?", (self.scope,)).fetchone()

    def _row(self, connection: sqlite3.Connection, project_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM library_projects WHERE scope=? AND project_id=?", (self.scope, project_id),
        ).fetchone()

    @staticmethod
    def _record(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        result["id"] = result["project_id"]
        result["summary"] = json.loads(result.pop("summary_json"))
        result["tombstone"] = bool(result["tombstone"])
        return result

    def get_record(self, project_id: str) -> dict | None:
        with self._connection() as connection:
            return self._record(self._row(connection, project_id))

    def list_records(self) -> list[dict]:
        with self._connection() as connection:
            return [self._record(row) for row in connection.execute(
                "SELECT * FROM library_projects WHERE scope=? ORDER BY project_id", (self.scope,),
            )]

    def _bump(self, connection: sqlite3.Connection, project_id: str, epoch: int,
              *, deleted: bool = False, membership: bool = False) -> int:
        connection.execute(
            "UPDATE library_scopes SET revision=revision+1, member_revision=member_revision+?, observed_at=? WHERE scope=?",
            (int(membership), _now(), self.scope),
        )
        revision = self._scope_row(connection)["revision"]
        connection.execute(
            "INSERT INTO library_changes VALUES(?,?,?,?,?,?)",
            (self.scope, revision, project_id, epoch, int(deleted), int(membership)),
        )
        floor = max(0, revision - CHANGE_RETENTION)
        connection.execute("DELETE FROM library_changes WHERE scope=? AND revision<=?", (self.scope, floor))
        connection.execute("UPDATE library_scopes SET min_revision=? WHERE scope=?", (floor, self.scope))
        return revision

    def _queue(self, connection: sqlite3.Connection, project_id: str, epoch: int,
               generation: int, domains: tuple | list) -> None:
        existing = connection.execute(
            "SELECT domains_json,queued_at FROM library_work WHERE scope=? AND project_id=?",
            (self.scope, project_id),
        ).fetchone()
        domains = set(domains or ("all",))
        if existing:
            domains.update(json.loads(existing["domains_json"]))
        # Keep the original queue time: a noisy project cannot keep resetting
        # its own age, and old projects retain a fair turn after new events.
        connection.execute("""
            INSERT INTO library_work VALUES(?,?,?,?,?,0,0,?)
            ON CONFLICT(scope,project_id) DO UPDATE SET epoch=excluded.epoch,
            generation=excluded.generation,domains_json=excluded.domains_json,
            attempts=0,next_attempt=0
        """, (self.scope, project_id, epoch, generation,
              _json(sorted(domains)), existing["queued_at"] if existing else _now()))

    def discover(self, project_id: str, instance_id: str = "", fingerprint: str = "",
                 *, allow_recreate: bool = False) -> dict | None:
        """Register a read-only discovery. An old scanner cannot revive deletion.

        Re-creation is an explicit operation requiring a different source
        identity/fingerprint and creates a new epoch. Ordinary reconciliation
        must leave `allow_recreate=False`.
        """
        if not valid_project_id(project_id):
            raise ValueError("Invalid project id")
        with self._connection(write=True) as connection:
            row = self._row(connection, project_id)
            if row is not None:
                changed_identity = bool(instance_id and row["instance_id"] and instance_id != row["instance_id"])
                if row["tombstone"]:
                    if not allow_recreate or not (changed_identity or fingerprint and fingerprint != row["fingerprint"]):
                        return None
                    epoch, generation = row["epoch"] + 1, row["generation"] + 1
                    summary = _placeholder(project_id)
                    revision = self._bump(connection, project_id, epoch, membership=True)
                    connection.execute("""UPDATE library_projects SET epoch=?,generation=?,instance_id=?,
                        legacy_identity=?,tombstone=0,fingerprint=?,summary_json=?,title=?,genre='',search_text=?,
                        status='draft',cover_status='not_started',task_present=0,sort_time='',updated_at='',
                        revision=?,observed_at='',freshness='rebuilding',error=''
                        WHERE scope=? AND project_id=?""",
                        (epoch, generation, instance_id, uuid.uuid4().hex, fingerprint, _json(summary),
                         project_id, project_id.casefold(), revision, self.scope, project_id))
                    connection.execute("DELETE FROM library_components WHERE scope=? AND project_id=?", (self.scope, project_id))
                elif not changed_identity and row["fingerprint"] == fingerprint and (
                    not instance_id or row["instance_id"] == instance_id
                ):
                    return None
                else:
                    epoch = row["epoch"] + int(changed_identity)
                    generation = row["generation"] + 1
                    revision = self._bump(connection, project_id, epoch, membership=changed_identity)
                    connection.execute("""UPDATE library_projects SET epoch=?,generation=?,instance_id=?,
                        fingerprint=?,revision=?,summary_json=?,freshness=CASE WHEN observed_at='' THEN 'rebuilding' ELSE 'stale' END,
                        error='' WHERE scope=? AND project_id=?""",
                        (epoch, generation, instance_id or row["instance_id"], fingerprint, revision,
                         _stale_summary(row), self.scope, project_id))
                    if changed_identity:
                        connection.execute("DELETE FROM library_components WHERE scope=? AND project_id=?", (self.scope, project_id))
                        connection.execute("""UPDATE library_projects SET summary_json=?,title=?,genre='',search_text=?,
                            status='draft',cover_status='not_started',task_present=0,sort_time='',updated_at='',
                            observed_at='',freshness='rebuilding',legacy_identity=? WHERE scope=? AND project_id=?""",
                            (_json(_placeholder(project_id)), project_id, project_id.casefold(), uuid.uuid4().hex,
                             self.scope, project_id))
            else:
                epoch, generation = 1, 1
                revision = self._bump(connection, project_id, epoch, membership=True)
                connection.execute("""INSERT INTO library_projects
                    (scope,project_id,instance_id,legacy_identity,fingerprint,summary_json,title,search_text,revision)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (self.scope, project_id, instance_id, uuid.uuid4().hex, fingerprint,
                     _json(_placeholder(project_id)), project_id, project_id.casefold(), revision))
            self._queue(connection, project_id, epoch, generation, ("all",))
            return self._record(self._row(connection, project_id))

    def invalidate(self, project_id: str, domains: tuple | list = ("all",)) -> dict | None:
        """Persist an intent/dirty marker; safe to call before and after writes."""
        if not valid_project_id(project_id):
            raise ValueError("Invalid project id")
        with self._connection(write=True) as connection:
            row = self._row(connection, project_id)
            if row is None:
                revision = self._bump(connection, project_id, 1, membership=True)
                connection.execute("""INSERT INTO library_projects
                    (scope,project_id,legacy_identity,summary_json,title,search_text,revision)
                    VALUES(?,?,?,?,?,?,?)""",
                    (self.scope, project_id, uuid.uuid4().hex, _json(_placeholder(project_id)),
                     project_id, project_id.casefold(), revision))
                self._queue(connection, project_id, 1, 1, domains)
                return self._record(self._row(connection, project_id))
            if row["tombstone"]:
                return None
            generation = row["generation"] + 1
            revision = self._bump(connection, project_id, row["epoch"])
            connection.execute("""UPDATE library_projects SET generation=?,revision=?,summary_json=?,
                freshness=CASE WHEN observed_at='' THEN 'rebuilding' ELSE 'stale' END,error=''
                WHERE scope=? AND project_id=?""", (generation, revision, _stale_summary(row, domains), self.scope, project_id))
            self._queue(connection, project_id, row["epoch"], generation, domains)
            return self._record(self._row(connection, project_id))

    def tombstone(self, project_id: str) -> bool:
        if not valid_project_id(project_id):
            raise ValueError("Invalid project id")
        with self._connection(write=True) as connection:
            row = self._row(connection, project_id)
            if row is not None and row["tombstone"]:
                # Also scrub tombstones written by an older catalog version.
                # The deletion fence is durable identity, not an archive of
                # the permanently deleted book's private display payload.
                self._scrub_tombstone(connection, project_id)
                return False
            epoch = row["epoch"] + 1 if row else 1
            revision = self._bump(connection, project_id, epoch, deleted=True, membership=True)
            if row:
                connection.execute("""UPDATE library_projects SET tombstone=1,epoch=?,generation=generation+1,
                    revision=? WHERE scope=? AND project_id=?""", (epoch, revision, self.scope, project_id))
            else:
                connection.execute("""INSERT INTO library_projects
                    (scope,project_id,epoch,legacy_identity,tombstone,summary_json,title,search_text,revision)
                    VALUES(?,?,?,?,1,?,?,?,?)""", (self.scope, project_id, epoch, uuid.uuid4().hex,
                    "{}", "", "", revision))
            self._scrub_tombstone(connection, project_id)
            return True

    def _scrub_tombstone(self, connection: sqlite3.Connection, project_id: str) -> None:
        # Retain only the identity/version/fingerprint needed to reject stale
        # writers and distinguish an explicitly recreated directory. SQLite
        # free-page/WAL erasure and historical backups are separate policies;
        # this removes content from all logical catalog records immediately.
        connection.execute("""UPDATE library_projects SET summary_json='{}',title='',genre='',search_text='',
            status='deleted',cover_status='not_started',task_present=0,sort_time='',updated_at='',
            observed_at='',freshness='unavailable',error='' WHERE scope=? AND project_id=? AND tombstone=1""",
            (self.scope, project_id))
        connection.execute("DELETE FROM library_work WHERE scope=? AND project_id=?", (self.scope, project_id))
        connection.execute("DELETE FROM library_components WHERE scope=? AND project_id=?", (self.scope, project_id))

    def pending(self, limit: int = 20) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute("""SELECT w.*,p.fingerprint FROM library_work w
                JOIN library_projects p ON p.scope=w.scope AND p.project_id=w.project_id
                WHERE w.scope=? AND p.tombstone=0 AND w.next_attempt<=?
                ORDER BY w.queued_at,w.project_id LIMIT ?""", (self.scope, time.time(), max(1, min(limit, 100))))
            return [{**dict(row), "domains": json.loads(row["domains_json"])} for row in rows]

    @staticmethod
    def _columns(summary: dict) -> tuple:
        title = str(summary.get("title") or summary["id"])
        genre = str(summary.get("genre") or "")
        search_text = " ".join((title, genre, str(summary.get("author") or ""), str(summary["id"]))).casefold()
        task_start = str(summary.get("task_started_at") or "")
        return (title, genre, search_text, str(summary.get("status") or "draft"),
                str((summary.get("cover") or {}).get("status") or "not_started"),
                int(bool(task_start)), task_start or str(summary.get("created_at") or ""),
                str(summary.get("updated_at") or ""))

    def publish(self, project_id: str, summary: dict, expected_epoch: int,
                expected_generation: int, fingerprint: str = "", error: str = "") -> bool:
        """Publish only the still-current snapshot. A late build never wins."""
        summary = {**_placeholder(project_id), **summary, "id": project_id}
        columns = self._columns(summary)
        with self._connection(write=True) as connection:
            row = self._row(connection, project_id)
            if row is None or row["tombstone"] or (row["epoch"], row["generation"]) != (expected_epoch, expected_generation):
                return False
            work = connection.execute("SELECT epoch,generation FROM library_work WHERE scope=? AND project_id=?",
                                      (self.scope, project_id)).fetchone()
            if work is None or tuple(work) != (expected_epoch, expected_generation):
                return False
            old_columns = tuple(row[name] for name in (
                "title", "genre", "search_text", "status", "cover_status", "task_present", "sort_time", "updated_at",
            ))
            revision = self._bump(connection, project_id, expected_epoch, membership=columns[:-1] != old_columns[:-1])
            connection.execute("""UPDATE library_projects SET summary_json=?,title=?,genre=?,search_text=?,
                status=?,cover_status=?,task_present=?,sort_time=?,updated_at=?,fingerprint=?,revision=?,
                observed_at=?,freshness=?,error=? WHERE scope=? AND project_id=?""",
                (_json(summary), *columns, fingerprint or row["fingerprint"], revision, _now(),
                 "stale" if error else "fresh", error[:2000], self.scope, project_id))
            if error:
                connection.execute("UPDATE library_work SET attempts=attempts+1,next_attempt=? WHERE scope=? AND project_id=?",
                                   (time.time() + 30, self.scope, project_id))
            else:
                connection.execute("DELETE FROM library_work WHERE scope=? AND project_id=? AND epoch=? AND generation=?",
                                   (self.scope, project_id, expected_epoch, expected_generation))
            return True

    def fail(self, token: dict, error: str) -> bool:
        with self._connection(write=True) as connection:
            row = self._row(connection, token["project_id"])
            if row is None or row["tombstone"] or (row["epoch"], row["generation"]) != (token["epoch"], token["generation"]):
                return False
            work = connection.execute("SELECT epoch,generation FROM library_work WHERE scope=? AND project_id=?",
                                      (self.scope, row["project_id"])).fetchone()
            if work is None or tuple(work) != (token["epoch"], token["generation"]):
                return False
            freshness = "stale" if row["observed_at"] else "unavailable"
            error = str(error)[:2000]
            revision = row["revision"]
            if row["error"] != error or row["freshness"] != freshness:
                revision = self._bump(connection, row["project_id"], row["epoch"])
            connection.execute("UPDATE library_projects SET revision=?,freshness=?,error=? WHERE scope=? AND project_id=?",
                               (revision, freshness, error, self.scope, row["project_id"]))
            attempts = int(token.get("attempts", 0)) + 1
            connection.execute("UPDATE library_work SET attempts=?,next_attempt=? WHERE scope=? AND project_id=?",
                               (attempts, time.time() + min(300, 2 ** min(attempts, 8)), self.scope, row["project_id"]))
            return True

    def get_component(self, project_id: str, key: str) -> dict | None:
        with self._connection() as connection:
            row = connection.execute("""SELECT c.payload_json FROM library_components c JOIN library_projects p
                ON c.scope=p.scope AND c.project_id=p.project_id AND c.epoch=p.epoch
                WHERE c.scope=? AND c.project_id=? AND c.component_key=? AND p.tombstone=0""",
                (self.scope, project_id, key)).fetchone()
            return json.loads(row[0]) if row else None

    def set_component(self, project_id: str, key: str, value: dict, expected_epoch: int) -> bool:
        with self._connection(write=True) as connection:
            row = self._row(connection, project_id)
            if row is None or row["tombstone"] or row["epoch"] != expected_epoch:
                return False
            connection.execute("""INSERT INTO library_components VALUES(?,?,?,?,?)
                ON CONFLICT(scope,project_id,component_key) DO UPDATE SET epoch=excluded.epoch,
                payload_json=excluded.payload_json""", (self.scope, project_id, expected_epoch, key, _json(value)))
            return True

    def mark_scan_complete(self) -> None:
        with self._connection(write=True) as connection:
            connection.execute("UPDATE library_scopes SET scan_completed_at=? WHERE scope=?", (_now(), self.scope))

    def _scoped_jobs(self, live: dict) -> list[dict]:
        jobs = []
        for raw in live.get("jobs", []):
            meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
            job = {**meta, **raw}
            path = str(job.get("project_path") or "")
            # Paths were resolved by the execution boundary. Avoid realpath()
            # and all filesystem access in the HTTP query path.
            if path and Path(os.path.normpath(path)).parent == self.root:
                project_id = Path(path).name
                if valid_project_id(project_id):
                    jobs.append({**job, "project_id": project_id})
        return jobs

    def _view(self, connection: sqlite3.Connection, live: dict | None) -> tuple[dict, str]:
        live = dict(live or {})
        live.setdefault("boot_epoch", "offline")
        live.setdefault("revision", 0)
        connection.execute("""CREATE TEMP TABLE library_live (
            project_id TEXT PRIMARY KEY, novel_json TEXT, cover_json TEXT,
            novel_started TEXT, running_novel INTEGER NOT NULL, running_cover INTEGER NOT NULL
        )""")
        active: dict[str, dict] = {}
        for job in sorted(self._scoped_jobs(live), key=lambda j: (str(j.get("started_at") or ""), str(j.get("job_id") or ""))):
            if job.get("status") != "running":
                continue
            kind = str(job.get("kind") or "")
            project = active.setdefault(job["project_id"], {})
            if kind not in {"novel.run", "novel.resume"} and not kind.startswith("cover."):
                continue
            project["novel" if kind in {"novel.run", "novel.resume"} else "cover"] = job
        for project_id, jobs in active.items():
            novel, cover = jobs.get("novel"), jobs.get("cover")
            connection.execute("INSERT INTO library_live VALUES(?,?,?,?,?,?)", (
                project_id, _json(novel) if novel else None, _json(cover) if cover else None,
                str(novel.get("started_at") or "") if novel else None, int(bool(novel)), int(bool(cover)),
            ))
        # Snapshot revision controls ETag and delta progress. Membership only
        # includes fields that change filter/order, so heartbeats do not expire
        # pagination cursors or reset scroll.
        membership = _digest(sorted((project_id, bool(value.get("novel")), bool(value.get("cover")),
                                    str((value.get("novel") or {}).get("started_at") or ""))
                                   for project_id, value in active.items()
                                   if value.get("novel") or value.get("cover")))[:24]
        connection.execute("""CREATE TEMP VIEW library_view AS SELECT p.*,
            CASE WHEN l.running_novel=1 THEN 'running'
                 WHEN p.status IN ('running','queued') THEN 'interrupted' ELSE p.status END AS effective_status,
            CASE WHEN l.running_cover=1 THEN 'processing' ELSE p.cover_status END AS effective_cover,
            CASE WHEN l.running_novel=1 THEN 1 ELSE p.task_present END AS effective_task,
            CASE WHEN p.task_present=1 THEN p.sort_time
                 WHEN l.running_novel=1 THEN l.novel_started ELSE p.sort_time END AS effective_time,
            l.novel_json,l.cover_json,l.project_id IS NOT NULL AS live_busy
            FROM library_projects p LEFT JOIN library_live l USING(project_id)
        """)
        return live, membership

    def _versions(self, scope: sqlite3.Row, live: dict, membership: str) -> tuple[str, str]:
        collection = f'{scope["member_revision"]}:{live["boot_epoch"]}:{membership}'
        changes = _encode({"v": SCHEMA_VERSION, "scope": self.scope, "catalog": scope["revision"],
                           "boot": live["boot_epoch"], "job": live["revision"]})
        return collection, changes

    @staticmethod
    def _card(row: sqlite3.Row, live: dict) -> dict:
        summary = json.loads(row["summary_json"])
        summary.update(library_revision=row["revision"], epoch=row["epoch"],
                       observed_at=row["observed_at"] or None, freshness=row["freshness"],
                       index_error=row["error"] or None, live_revision=live["revision"],
                       live_boot_epoch=live["boot_epoch"], status=row["effective_status"],
                       library_schema_version=SCHEMA_VERSION)
        summary.setdefault("count_unit", "words")
        if row["live_busy"]:
            summary["task_can_resume"] = False
        if row["novel_json"]:
            job = json.loads(row["novel_json"])
            summary["saved_task_error"] = summary.get("task_error")
            summary.update(task_can_resume=False, task_error=None, task_resume_job_id=job.get("job_id"),
                           active_operation={"job_id": job.get("job_id"), "kind": job.get("kind"),
                                             "cancel_requested": bool(job.get("cancel_requested"))})
            if job.get("run_id"):
                summary["task_id"] = job["run_id"]
            if not summary.get("task_started_at"):
                summary["task_started_at"] = job.get("started_at")
            if job.get("kind") == "novel.resume":
                summary["task_phase"] = "正在恢复核验"
        elif summary["status"] == "interrupted":
            summary["task_can_resume"] = False
        if row["cover_json"]:
            summary["cover"] = {**(summary.get("cover") or {}), "status": "processing"}
        return summary

    def _health(self, connection: sqlite3.Connection, scope: sqlite3.Row) -> str:
        counts = dict(connection.execute("SELECT freshness,count(*) FROM library_projects WHERE scope=? AND tombstone=0 GROUP BY freshness", (self.scope,)).fetchall())
        if counts.get("unavailable"):
            return "degraded"
        if counts.get("stale") or counts.get("rebuilding") or not scope["scan_completed_at"] and not counts:
            return "rebuilding"
        return "ready"

    @staticmethod
    def _filters(q: str, status: str, cover: str) -> tuple[str, list]:
        clauses, params = ["tombstone=0", "scope=?"], []
        if q.strip():
            clauses.append("instr(search_text,?)>0")
            params.append(q.strip().casefold())
        if status == "writing":
            clauses.append("effective_status IN ('running','pending','queued','in_progress')")
        elif status == "attention":
            clauses.append("effective_status IN ('failed','paused','interrupted','cancelled')")
        elif status != "all":
            clauses.append("effective_status=?")
            params.append(status)
        if cover != "all":
            clauses.append("effective_cover=?")
            params.append(cover)
        return " AND ".join(clauses), params

    def query(self, q: str = "", status: str = "all", cover: str = "all", sort: str = "recent",
              cursor: str | None = None, limit: int = 20, live_snapshot: dict | None = None) -> dict:
        if status not in STATUSES or cover not in COVERS or sort not in SORTS:
            raise ValueError("Unsupported library filter or sort")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
            raise ValueError("Library page size must be between 1 and 50")
        if len(q) > 500:
            raise ValueError("Library search is too long")
        query_key = _digest([q.strip().casefold(), status, cover, sort, limit])
        with self._connection() as connection:
            scope = self._scope_row(connection)
            live, membership = self._view(connection, live_snapshot)
            collection, changes = self._versions(scope, live, membership)
            if sort == "updated":
                collection += f':{scope["revision"]}'
            where, params = self._filters(q, status, cover)
            params.insert(0, self.scope)
            total = connection.execute("SELECT count(*) FROM library_projects WHERE scope=? AND tombstone=0", (self.scope,)).fetchone()[0]
            filtered = connection.execute(f"SELECT count(*) FROM library_view WHERE {where}", params).fetchone()[0]
            after = ""
            if cursor:
                position = _decode(cursor)
                if position.get("scope") != self.scope or position.get("query") != query_key or position.get("collection") != collection:
                    raise CatalogCursorError("作品库筛选或排序已变化，请重新加载分页。")
                key = position.get("key")
                if sort == "recent" and isinstance(key, list) and len(key) == 3 and type(key[0]) is int and all(isinstance(value, str) for value in key[1:]):
                    after = " AND (effective_task<? OR (effective_task=? AND effective_time<?) OR (effective_task=? AND effective_time=? AND project_id>?))"
                    params.extend((key[0], key[0], key[1], key[0], key[1], key[2]))
                elif sort in {"title", "updated"} and isinstance(key, list) and len(key) == 2 and all(isinstance(value, str) for value in key):
                    field, comparison = ("title", ">") if sort == "title" else ("updated_at", "<")
                    after = f" AND ({field}{comparison}? OR ({field}=? AND project_id>?))"
                    params.extend((key[0], key[0], key[1]))
                else:
                    raise CatalogCursorError("Invalid library cursor")
            order = {"recent": "effective_task DESC,effective_time DESC,project_id ASC", "title": "title ASC,project_id ASC", "updated": "updated_at DESC,project_id ASC"}[sort]
            rows = connection.execute(f"SELECT * FROM library_view WHERE {where}{after} ORDER BY {order} LIMIT ?", (*params, limit + 1)).fetchall()
            next_cursor = None
            if len(rows) > limit:
                row = rows[limit - 1]
                key = [row["effective_task"], row["effective_time"], row["project_id"]] if sort == "recent" else [row["title" if sort == "title" else "updated_at"], row["project_id"]]
                next_cursor = _encode({"v": SCHEMA_VERSION, "scope": self.scope, "query": query_key, "collection": collection, "key": key})
            return {
                "items": [self._card(row, live) for row in rows[:limit]], "next_cursor": next_cursor,
                "total": total, "filtered_total": filtered, "collection_revision": collection,
                "changes_cursor": changes, "observed_at": scope["observed_at"] or None,
                "catalog_status": self._health(connection, scope), "scope": self.scope,
                "etag": '"' + _digest([self.scope, scope["revision"], bool(scope["scan_completed_at"]),
                                      live["boot_epoch"], live["revision"], query_key, cursor]) + '"',
            }

    def changes(self, after: str | None, limit: int = 100, live_snapshot: dict | None = None) -> dict:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise ValueError("Library change limit must be between 1 and 200")
        with self._connection() as connection:
            scope = self._scope_row(connection)
            live, membership = self._view(connection, live_snapshot)
            collection, current_cursor = self._versions(scope, live, membership)
            result = {"scope": self.scope, "items": [], "deleted": [], "reset_required": False,
                      "membership_changed": False, "has_more": False, "changes_cursor": current_cursor,
                      "collection_revision": collection, "observed_at": scope["observed_at"] or None,
                      "catalog_status": self._health(connection, scope)}
            try:
                position = _decode(after) if after else {}
                revision, job_revision = position.get("catalog"), position.get("job")
                if (position.get("scope") != self.scope or position.get("boot") != live["boot_epoch"]
                        or type(revision) is not int or not scope["min_revision"] <= revision <= scope["revision"]
                        or type(job_revision) is not int or not max(0, int(live.get("oldest_revision", 0))) <= job_revision <= live["revision"]):
                    raise CatalogCursorError("Library changes must be resynchronized")
            except CatalogCursorError:
                result["reset_required"] = True
                return result
            events = connection.execute("SELECT * FROM library_changes WHERE scope=? AND revision>? ORDER BY revision LIMIT ?",
                                        (self.scope, revision, limit + 1)).fetchall()
            more_catalog = len(events) > limit
            events = events[:limit]
            ids = {event["project_id"] for event in events}
            result["membership_changed"] = any(event["membership_changed"] for event in events)
            consumed = events[-1]["revision"] if more_catalog else scope["revision"]
            job_events = [event for event in live.get("changes", []) if event.get("revision", 0) > job_revision]
            job_events.sort(key=lambda event: event.get("revision", 0))
            remaining = limit - len(events)
            selected = job_events[:remaining] if not more_catalog else []
            for event in selected:
                job = event.get("job") or {}
                scoped = self._scoped_jobs({"jobs": [job]})
                if not scoped:
                    continue
                ids.add(scoped[0]["project_id"])
                if event.get("membership_changed", True):
                    result["membership_changed"] = True
            more_jobs = more_catalog or len(job_events) > len(selected)
            consumed_job = selected[-1]["revision"] if selected else job_revision
            if not more_jobs:
                consumed_job = live["revision"]
            # A changed job counter without a replayable log cannot be safely
            # acknowledged. A restart or log gap must trigger reconciliation.
            if live["revision"] > job_revision and not job_events and not more_catalog:
                result["reset_required"] = True
                return result
            if ids:
                rows = connection.execute("SELECT * FROM library_view WHERE scope=? AND project_id IN (" + ",".join("?" for _ in ids) + ")",
                                          (self.scope, *sorted(ids))).fetchall()
                for row in rows:
                    if row["tombstone"]:
                        result["deleted"].append({"id": row["project_id"], "epoch": row["epoch"], "revision": row["revision"]})
                    else:
                        result["items"].append(self._card(row, live))
            result["has_more"] = more_catalog or more_jobs
            result["changes_cursor"] = _encode({"v": SCHEMA_VERSION, "scope": self.scope, "catalog": consumed,
                                                "boot": live["boot_epoch"], "job": consumed_job})
            return result

    def navigation(self, q: str = "", limit: int = 20, live_snapshot: dict | None = None) -> dict:
        """Search all titles, including pages the browser has never loaded."""
        if not 1 <= limit <= 50 or len(q) > 500:
            raise ValueError("Invalid navigation query")
        with self._connection() as connection:
            scope = self._scope_row(connection)
            live, membership = self._view(connection, live_snapshot)
            collection, _ = self._versions(scope, live, membership)
            # Select only navigation columns: no summary JSON is decoded.
            rows = connection.execute("""SELECT project_id,title,genre FROM library_view
                WHERE scope=? AND tombstone=0 AND instr(search_text,?)>0
                ORDER BY effective_task DESC,effective_time DESC,project_id LIMIT ?""",
                (self.scope, q.strip().casefold(), limit))
            return {"items": [{"id": row["project_id"], "title": row["title"], "genre": row["genre"]} for row in rows],
                    "scope": self.scope, "collection_revision": collection}
