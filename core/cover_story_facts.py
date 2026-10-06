"""Explicit, source-bound confirmations for missing cover-only story facts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Mapping, Sequence

try:
    import fcntl
except ImportError as exc:  # pragma: no cover - Novel OS targets POSIX
    raise RuntimeError("cover story-facts locks require POSIX fcntl") from exc

try:
    from .cover_models_v2 import CoverBriefV2
    from .cover_store import CoverConflict, CoverStore
except ImportError:  # pragma: no cover - legacy top-level core imports
    from cover_models_v2 import CoverBriefV2
    from cover_store import CoverConflict, CoverStore


RELATIVE_PATH = Path("outputs/covers/story-facts.json")
SCHEMA_VERSION = "cover-story-facts.v1"
PROVENANCE = "explicit_user_confirmation"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CHARACTER_FIELD = re.compile(
    r"^principal_characters\[(?P<index>\d+)\]\.(?P<name>age|occupation_and_status)$"
)
_SOURCE_PATHS = (
    "outputs/input/prompt.md",
    "outputs/input/foundation.json",
    "outputs/input/brief.json",
    "outputs/input/ending_contract.json",
    "outputs/state/story_state.json",
)


@dataclass(frozen=True)
class CoverSourceSnapshot:
    prompt_text: str
    prompt_sha256: str
    foundation_sha256: str
    source_revision_sha256: str
    file_sha256: tuple[tuple[str, str], ...]


class StoryFactsLock:
    """Serialize story-fact compare-and-swap updates across processes."""

    def __init__(self, project: Path) -> None:
        self.project = Path(os.path.abspath(os.fspath(project)))
        self.directory = self.project / "outputs/covers"
        self.path = self.directory / ".story-facts.lock"
        self._descriptor: int | None = None

    @staticmethod
    def _ensure_directory(path: Path, *, create: bool = False) -> None:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            if not create:
                raise ValueError(f"Cover story-facts lock parent is missing: {path}")
            try:
                path.mkdir()
            except FileExistsError:
                pass
            mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise ValueError(
                f"Cover story-facts lock parent must be a directory, not a symlink: {path}"
            )

    def __enter__(self) -> "StoryFactsLock":
        if self._descriptor is not None:
            raise RuntimeError("Cover story-facts lock is already held")
        self._ensure_directory(self.project)
        self._ensure_directory(self.project / "outputs")
        self._ensure_directory(self.directory, create=True)
        try:
            mode = self.path.lstat().st_mode
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise ValueError("Cover story-facts lock must be a regular file")
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.path, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("Cover story-facts lock must be a regular file")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except BaseException:
            os.close(descriptor)
            raise
        self._descriptor = descriptor
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is None:
            return False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
        return False


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_cover_source_snapshot(project: Path) -> CoverSourceSnapshot:
    """Read every durable input consumed by the legacy cover resolver."""
    project = Path(project)
    digests: list[tuple[str, str]] = []
    prompt_bytes: bytes | None = None
    foundation_sha256 = ""
    for relative in _SOURCE_PATHS:
        path = project / relative
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            body = None
        digest = hashlib.sha256(body).hexdigest() if body is not None else ""
        digests.append((relative, digest))
        if relative == "outputs/input/prompt.md":
            prompt_bytes = body
        elif relative == "outputs/input/foundation.json":
            foundation_sha256 = digest
    if prompt_bytes is None:
        raise FileNotFoundError(project / "outputs/input/prompt.md")
    prompt_text = prompt_bytes.decode("utf-8")
    prompt_sha256 = hashlib.sha256(prompt_bytes).hexdigest()
    return CoverSourceSnapshot(
        prompt_text=prompt_text,
        prompt_sha256=prompt_sha256,
        foundation_sha256=foundation_sha256,
        source_revision_sha256=_canonical_sha256(digests),
        file_sha256=tuple(digests),
    )


def brief_revision_sha256(brief: CoverBriefV2) -> str:
    return _canonical_sha256(brief.to_dict())


def pending_fields(brief: CoverBriefV2) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for assumption in brief.pending_critical_assumptions():
        item = {
            "field": assumption.field,
            "label": _field_label(brief, assumption.field),
            "proposed_value": assumption.proposed_value,
        }
        match = _CHARACTER_FIELD.fullmatch(assumption.field)
        if match:
            index = int(match.group("index"))
            if index < len(brief.principal_characters):
                character = brief.principal_characters[index]
                item["character_id"] = character.character_id
                item["character_name"] = character.name
        result.append(item)
    return result


def response_payload(brief: CoverBriefV2) -> dict[str, Any]:
    return {
        "brief": brief.to_dict(),
        "pending_fields": pending_fields(brief),
        "revision_sha256": brief_revision_sha256(brief),
    }


def apply_persisted_story_facts(
    project: Path,
    brief: CoverBriefV2,
    *,
    snapshot: CoverSourceSnapshot,
) -> CoverBriefV2:
    facts = _load(project / RELATIVE_PATH)
    if facts is None:
        return brief
    if (
        facts["source_prompt_sha256"] != snapshot.prompt_sha256
        or facts["foundation_sha256"] != snapshot.foundation_sha256
        or facts["source_revision_sha256"] != snapshot.source_revision_sha256
    ):
        return brief
    return _apply_facts(brief, facts)


def confirm_missing_story_facts(
    project: Path,
    *,
    expected_revision_sha256: str,
    characters: Sequence[Mapping[str, Any]],
    primary_spaces: Sequence[str] | None,
) -> CoverBriefV2:
    _sha(expected_revision_sha256, "expected_revision_sha256")
    with StoryFactsLock(project):
        return _confirm_missing_story_facts_locked(
            project,
            expected_revision_sha256=expected_revision_sha256,
            characters=characters,
            primary_spaces=primary_spaces,
        )


def _confirm_missing_story_facts_locked(
    project: Path,
    *,
    expected_revision_sha256: str,
    characters: Sequence[Mapping[str, Any]],
    primary_spaces: Sequence[str] | None,
) -> CoverBriefV2:
    try:
        from .cover_handoff import _resolve_cover_brief_v2_snapshot
    except ImportError:  # pragma: no cover - legacy top-level core imports
        from cover_handoff import _resolve_cover_brief_v2_snapshot

    brief, snapshot = _resolve_cover_brief_v2_snapshot(project)
    existing = _load(project / RELATIVE_PATH)
    if existing is not None and (
        existing["source_prompt_sha256"] != snapshot.prompt_sha256
        or existing["foundation_sha256"] != snapshot.foundation_sha256
        or existing["source_revision_sha256"] != snapshot.source_revision_sha256
    ):
        existing = None
    current = apply_persisted_story_facts(
        project,
        brief,
        snapshot=snapshot,
    )
    current_revision = brief_revision_sha256(current)
    if expected_revision_sha256 != current_revision:
        raise CoverConflict(
            "Cover story facts changed: expected "
            f"{expected_revision_sha256}, found {current_revision}"
        )

    pending = {item.field for item in current.pending_critical_assumptions()}
    by_id = {
        character.character_id: (index, character)
        for index, character in enumerate(brief.principal_characters)
    }
    confirmed_characters: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in characters:
        character_id = _required_text(raw.get("character_id"), "character_id")
        if character_id not in by_id:
            raise ValueError(f"Unknown cover character_id: {character_id}")
        if character_id in seen:
            raise ValueError(f"Duplicate cover character_id: {character_id}")
        seen.add(character_id)
        index, _character = by_id[character_id]
        allowed = {"character_id", "age_band", "occupation_and_status"}
        if not set(raw).issubset(allowed):
            raise ValueError("Cover character confirmation has unknown fields")
        confirmation = {"character_id": character_id}
        supplied = False
        if "age_band" in raw and raw.get("age_band") is not None:
            field = f"principal_characters[{index}].age"
            if field not in pending:
                raise ValueError(f"Cover fact is not missing: {field}")
            confirmation["age_band"] = _required_text(raw.get("age_band"), field)
            supplied = True
        if (
            "occupation_and_status" in raw
            and raw.get("occupation_and_status") is not None
        ):
            field = f"principal_characters[{index}].occupation_and_status"
            if field not in pending:
                raise ValueError(f"Cover fact is not missing: {field}")
            confirmation["occupation_and_status"] = _required_text(
                raw.get("occupation_and_status"), field
            )
            supplied = True
        if not supplied:
            raise ValueError(
                f"Cover character {character_id} has no confirmed missing fields"
            )
        confirmed_characters.append(confirmation)

    confirmed_spaces: list[str] = []
    if primary_spaces is not None:
        field = "lived_environment.primary_spaces"
        if field not in pending:
            raise ValueError(f"Cover fact is not missing: {field}")
        confirmed_spaces = _nonempty_texts(primary_spaces, field)

    if not confirmed_characters and primary_spaces is None:
        raise ValueError("At least one missing cover story fact must be confirmed")

    merged_characters = {
        item["character_id"]: dict(item)
        for item in (existing or {}).get("characters", [])
    }
    for item in confirmed_characters:
        merged_characters.setdefault(item["character_id"], {}).update(item)
    facts = {
        "schema_version": SCHEMA_VERSION,
        "source_prompt_sha256": snapshot.prompt_sha256,
        "foundation_sha256": snapshot.foundation_sha256,
        "source_revision_sha256": snapshot.source_revision_sha256,
        "provenance": PROVENANCE,
        "characters": list(merged_characters.values()),
        "primary_spaces": (
            confirmed_spaces
            if primary_spaces is not None
            else list((existing or {}).get("primary_spaces", []))
        ),
    }
    if read_cover_source_snapshot(project) != snapshot:
        raise CoverConflict("Cover story sources changed while facts were being confirmed")
    CoverStore._write_json(project / RELATIVE_PATH, facts)
    return _apply_facts(brief, facts)


def _apply_facts(brief: CoverBriefV2, facts: Mapping[str, Any]) -> CoverBriefV2:
    payload = brief.to_dict()
    pending = {item.field for item in brief.pending_critical_assumptions()}
    by_id = {
        character.character_id: index
        for index, character in enumerate(brief.principal_characters)
    }
    applied: dict[str, Any] = {"characters": [], "primary_spaces": []}
    for confirmation in facts["characters"]:
        character_id = confirmation["character_id"]
        index = by_id.get(character_id)
        if index is None:
            raise ValueError(f"Unknown cover character_id: {character_id}")
        character = dict(payload["principal_characters"][index])
        applied_character = {"character_id": character_id}
        if (
            confirmation.get("age_band")
            and f"principal_characters[{index}].age" in pending
        ):
            character["age_band"] = confirmation["age_band"]
            _approve(
                payload,
                f"principal_characters[{index}].age",
                confirmation["age_band"],
            )
            applied_character["age_band"] = confirmation["age_band"]
        if (
            confirmation.get("occupation_and_status")
            and f"principal_characters[{index}].occupation_and_status" in pending
        ):
            character["occupation_and_status"] = confirmation[
                "occupation_and_status"
            ]
            _approve(
                payload,
                f"principal_characters[{index}].occupation_and_status",
                confirmation["occupation_and_status"],
            )
            applied_character["occupation_and_status"] = confirmation[
                "occupation_and_status"
            ]
        if len(applied_character) > 1:
            refs = list(character.get("source_refs") or [])
            ref = "outputs/covers/story-facts.json:explicit_user_confirmation"
            if ref not in refs:
                refs.append(ref)
            character["source_refs"] = refs
            applied["characters"].append(applied_character)
        payload["principal_characters"][index] = character

    spaces = list(facts["primary_spaces"])
    if spaces and "lived_environment.primary_spaces" in pending:
        environment = dict(payload["lived_environment"])
        environment["primary_spaces"] = spaces
        payload["lived_environment"] = environment
        _approve(payload, "lived_environment.primary_spaces", ", ".join(spaces))
        applied["primary_spaces"] = spaces

    effective_sha256 = _canonical_sha256({
        "source_prompt_sha256": facts["source_prompt_sha256"],
        "foundation_sha256": facts["foundation_sha256"],
        "source_revision_sha256": facts["source_revision_sha256"],
        "explicit_user_confirmations": applied,
    })
    return CoverBriefV2.from_dict(
        payload,
        source_prompt_sha256=effective_sha256,
        foundation_sha256=brief.foundation_sha256,
        allow_pending_required_facts=True,
    )


def _load(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    if path.is_symlink():
        raise ValueError("Cover story facts file must not be a symlink")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Cover story facts are not valid JSON: {exc.msg}") from exc
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "source_prompt_sha256",
        "foundation_sha256",
        "source_revision_sha256",
        "provenance",
        "characters",
        "primary_spaces",
    }:
        raise ValueError("Cover story facts have invalid fields")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Cover story facts schema version is invalid")
    if raw["provenance"] != PROVENANCE:
        raise ValueError("Cover story facts provenance is invalid")
    _sha(raw["source_prompt_sha256"], "source_prompt_sha256")
    _sha(raw["foundation_sha256"], "foundation_sha256", optional=True)
    _sha(raw["source_revision_sha256"], "source_revision_sha256")
    if not isinstance(raw["characters"], list):
        raise ValueError("Cover story facts characters must be a list")
    characters: list[dict[str, str]] = []
    character_ids: set[str] = set()
    for item in raw["characters"]:
        if not isinstance(item, dict) or not set(item).issubset(
            {"character_id", "age_band", "occupation_and_status"}
        ):
            raise ValueError("Cover story facts character is invalid")
        character = {
            "character_id": _required_text(item.get("character_id"), "character_id")
        }
        if character["character_id"] in character_ids:
            raise ValueError("Cover story facts contain duplicate character_id")
        character_ids.add(character["character_id"])
        for name in ("age_band", "occupation_and_status"):
            if name in item:
                character[name] = _required_text(item.get(name), name)
        if len(character) == 1:
            raise ValueError("Cover story facts character has no confirmed fields")
        characters.append(character)
    raw["characters"] = characters
    raw["primary_spaces"] = _nonempty_texts(
        raw["primary_spaces"], "primary_spaces", allow_empty=True
    )
    return raw


def _approve(payload: dict[str, Any], field: str, value: str) -> None:
    for assumption in payload.get("visual_assumptions") or []:
        if assumption.get("field") == field:
            assumption.update({
                "proposed_value": value,
                "reason": "explicit user confirmation for cover visualization",
                "status": "approved",
            })
            return


def _field_label(brief: CoverBriefV2, field: str) -> str:
    match = _CHARACTER_FIELD.fullmatch(field)
    if match:
        index = int(match.group("index"))
        if index < len(brief.principal_characters):
            character = brief.principal_characters[index]
            suffix = (
                "age range"
                if match.group("name") == "age"
                else "occupation and status"
            )
            return f"{character.name} {suffix}"
    if field == "lived_environment.primary_spaces":
        return "Primary story spaces"
    return field


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonblank string")
    return value.strip()


def _nonempty_texts(
    values: Any,
    field: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"{field} must be a list")
    result = [_required_text(value, field) for value in values]
    if not result and not allow_empty:
        raise ValueError(f"{field} must be a nonempty list")
    if len(set(result)) != len(result):
        raise ValueError(f"{field} must not contain duplicates")
    return result


def _sha(value: Any, field: str, *, optional: bool = False) -> str:
    text = str(value or "").strip()
    if optional and not text:
        return ""
    if not _SHA256.fullmatch(text):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return text


__all__ = [
    "CoverSourceSnapshot",
    "RELATIVE_PATH",
    "StoryFactsLock",
    "apply_persisted_story_facts",
    "brief_revision_sha256",
    "confirm_missing_story_facts",
    "pending_fields",
    "read_cover_source_snapshot",
    "response_payload",
]
