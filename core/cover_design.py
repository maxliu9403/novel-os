"""Evidence-bound design context for book-specific cover art direction.

The public interface of this module is intentionally small:

``collect_visual_evidence(project, brief)`` turns the durable novel artifacts
into a compact, source-bound ledger.  The art director and image model can use
that ledger without every caller learning where publication copy, prompts,
story state, and final chapters live.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for type checkers
    from .cover_models_v2 import CoverBriefV2


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SPOILER_LEVELS = {"safe", "tease", "late_spoiler"}
_VISUAL_TERMS = (
    "door", "window", "table", "chair", "letter", "photo", "photograph", "phone",
    "ring", "dress", "coat", "key", "bag", "suitcase", "plate", "glass", "light",
    "shadow", "rain", "snow", "kitchen", "hall", "room", "garden", "street",
    "车", "门", "窗", "桌", "椅", "信", "照片", "手机", "戒指", "衣", "钥匙",
    "行李", "餐", "灯", "影", "雨", "雪", "厨房", "走廊", "房间", "花园",
)
_ACTION_TERMS = (
    "leave", "walk", "turn", "choose", "refuse", "hold", "drop", "open", "close",
    "discover", "watch", "reach", "tear", "sign", "sit", "stand", "wait", "return",
    "离开", "转身", "选择", "拒绝", "握", "放下", "打开", "关上", "发现", "看着",
    "伸手", "撕", "签", "坐", "站", "等待", "回来",
)


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _texts(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(text for item in value if (text := _text(item)))


def _identity_texts(value: Any) -> tuple[str, ...]:
    """Normalize one designer paragraph or an explicit list into one shape."""
    if isinstance(value, str):
        compact = _text(value)
        return (compact,) if compact else ()
    return _texts(value)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_digest(value: Any) -> str:
    return _digest(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


@dataclass(frozen=True)
class VisualEvidence:
    evidence_id: str
    source_type: str
    source_ref: str
    summary: str
    story_function: str
    spoiler_level: str
    visual_tags: tuple[str, ...] = ()
    source_sha256: str = ""

    def __post_init__(self) -> None:
        if not self.evidence_id or not self.source_type or not self.source_ref or not self.summary:
            raise ValueError("visual evidence requires id, source type, source ref, and summary")
        if self.spoiler_level not in _SPOILER_LEVELS:
            raise ValueError(f"unknown visual evidence spoiler level '{self.spoiler_level}'")
        if self.source_sha256 and not _SHA256.fullmatch(self.source_sha256):
            raise ValueError("visual evidence source_sha256 must be a SHA-256 digest")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VisualEvidence":
        return cls(
            evidence_id=_text(data.get("evidence_id")),
            source_type=_text(data.get("source_type")),
            source_ref=_text(data.get("source_ref")),
            summary=_text(data.get("summary")),
            story_function=_text(data.get("story_function")) or "story-specific visual evidence",
            spoiler_level=_text(data.get("spoiler_level")) or "safe",
            visual_tags=_texts(data.get("visual_tags")),
            source_sha256=_text(data.get("source_sha256")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "summary": self.summary,
            "story_function": self.story_function,
            "spoiler_level": self.spoiler_level,
            "visual_tags": list(self.visual_tags),
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True)
class VisualEvidenceLedger:
    schema_version: int
    source_bundle_sha256: str
    source_files: Mapping[str, str]
    items: tuple[VisualEvidence, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("visual evidence ledger schema_version must be 1")
        if not _SHA256.fullmatch(self.source_bundle_sha256):
            raise ValueError("visual evidence source bundle must be a SHA-256 digest")
        if not self.items:
            raise ValueError("visual evidence ledger requires at least one item")
        ids = [item.evidence_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("visual evidence ids must be unique")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VisualEvidenceLedger":
        raw_items = data.get("items") or ()
        if not isinstance(raw_items, (list, tuple)):
            raise ValueError("visual evidence ledger items must be a list")
        raw_files = data.get("source_files") or {}
        if not isinstance(raw_files, Mapping):
            raise ValueError("visual evidence source_files must be an object")
        return cls(
            schema_version=int(data.get("schema_version") or 1),
            source_bundle_sha256=_text(data.get("source_bundle_sha256")),
            source_files={_text(key): _text(value) for key, value in raw_files.items()},
            items=tuple(VisualEvidence.from_dict(item) for item in raw_items),
        )

    @property
    def allowed_refs(self) -> tuple[str, ...]:
        return tuple(f"evidence:{item.evidence_id}" for item in self.items)

    def by_id(self, evidence_id: str) -> VisualEvidence | None:
        return next((item for item in self.items if item.evidence_id == evidence_id), None)

    def prompt_payload(self, *, max_items: int = 24) -> dict[str, Any]:
        source_order = (
            "core_conflict", "publication_intro", "source_prompt", "final_chapter",
            "story_node", "story_signal", "character", "foundation", "emotional_promise",
        )
        groups = {
            source_type: sorted(
                (item for item in self.items if item.source_type == source_type),
                key=lambda item: item.spoiler_level == "late_spoiler",
            )
            for source_type in source_order
        }
        ranked: list[VisualEvidence] = []
        depth = 0
        while len(ranked) < max_items:
            added = False
            for source_type in source_order:
                group = groups[source_type]
                if depth < len(group):
                    ranked.append(group[depth])
                    added = True
                    if len(ranked) >= max_items:
                        break
            if not added:
                break
            depth += 1
        if len(ranked) < max_items:
            ranked.extend(item for item in self.items if item not in ranked)
        return {
            "schema_version": self.schema_version,
            "source_bundle_sha256": self.source_bundle_sha256,
            "items": [
                {
                    **item.to_dict(),
                    "summary": item.summary[:420],
                }
                for item in ranked[:max_items]
            ],
            "usage_rule": (
                "Use safe and tease evidence for visible story promises. Late-spoiler evidence may verify "
                "identity, setting, and recurring motifs but must not reveal the resolution."
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_bundle_sha256": self.source_bundle_sha256,
            "source_files": dict(self.source_files),
            "items": [item.to_dict() for item in self.items],
        }


@dataclass(frozen=True)
class BookVisualIdentity:
    """One book-level design language shared by a portfolio, not a layout template."""

    design_thesis: str
    dominant_emotional_contradiction: str
    story_signatures: tuple[str, ...]
    visual_grammar: tuple[str, ...]
    material_language: tuple[str, ...]
    palette_logic: str
    lighting_logic: str
    spatial_logic: str
    typography_voice: str
    cast_policy: str
    cliche_blacklist: tuple[str, ...]
    uniqueness_anchors: tuple[str, ...]
    spoiler_boundary: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BookVisualIdentity":
        return cls(
            design_thesis=_text(data.get("design_thesis")),
            dominant_emotional_contradiction=_text(data.get("dominant_emotional_contradiction")),
            story_signatures=_identity_texts(data.get("story_signatures")),
            visual_grammar=_identity_texts(data.get("visual_grammar")),
            material_language=_identity_texts(data.get("material_language")),
            palette_logic=_text(data.get("palette_logic")),
            lighting_logic=_text(data.get("lighting_logic")),
            spatial_logic=_text(data.get("spatial_logic")),
            typography_voice=_text(data.get("typography_voice")),
            cast_policy=_text(data.get("cast_policy")),
            cliche_blacklist=_identity_texts(data.get("cliche_blacklist")),
            uniqueness_anchors=_identity_texts(data.get("uniqueness_anchors")),
            spoiler_boundary=_identity_texts(data.get("spoiler_boundary")),
        )

    @classmethod
    def from_brief(cls, brief: "CoverBriefV2") -> "BookVisualIdentity":
        signatures = tuple(dict.fromkeys(
            [item.description for item in brief.decisive_story_nodes[:2]]
            + [item.description for item in brief.secondary_signals[:2]]
            + [brief.core_task, brief.core_conflict]
        ))
        return cls(
            design_thesis=(
                f"Express {brief.core_conflict} through a story-specific visual decision rather than a generic genre pose."
            ),
            dominant_emotional_contradiction=brief.emotional_promise,
            story_signatures=signatures or (brief.core_task,),
            visual_grammar=("evidence-bound editorial narrative", "mobile-first hierarchy"),
            material_language=tuple(brief.lived_environment.economic_signals[:3]) or ("lived-in surfaces",),
            palette_logic=brief.genre_emotion_profile.emotional_temperature,
            lighting_logic="Motivated light must reveal the decisive emotional change.",
            spatial_logic="Choose the spatial relationship that best explains this book; character scale is not fixed.",
            typography_voice="Title lettering grows from the title meaning and the book's emotional contradiction.",
            cast_policy=(
                f"Keep {brief.reader_anchor_character.name} visibly present as the clear human and emotional "
                "anchor in every direction. Object, environment, absence, or typography may lead the design "
                "language while remaining connected to that protagonist's specific story action."
            ),
            cliche_blacklist=(
                tuple(brief.genre_emotion_profile.prohibited_shortcuts)
                or ("generic genre pose", "interchangeable stock symbolism")
            ),
            uniqueness_anchors=signatures[:3] or (brief.core_conflict,),
            spoiler_boundary=tuple(brief.forbidden_elements) or ("unsupported resolution spoilers",),
        )

    @property
    def complete(self) -> bool:
        return all((
            self.design_thesis,
            self.dominant_emotional_contradiction,
            len(self.story_signatures) >= 2,
            self.visual_grammar,
            self.material_language,
            self.palette_logic,
            self.lighting_logic,
            self.spatial_logic,
            self.typography_voice,
            self.cast_policy,
            self.cliche_blacklist,
            len(self.uniqueness_anchors) >= 2,
            self.spoiler_boundary,
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "design_thesis": self.design_thesis,
            "dominant_emotional_contradiction": self.dominant_emotional_contradiction,
            "story_signatures": list(self.story_signatures),
            "visual_grammar": list(self.visual_grammar),
            "material_language": list(self.material_language),
            "palette_logic": self.palette_logic,
            "lighting_logic": self.lighting_logic,
            "spatial_logic": self.spatial_logic,
            "typography_voice": self.typography_voice,
            "cast_policy": self.cast_policy,
            "cliche_blacklist": list(self.cliche_blacklist),
            "uniqueness_anchors": list(self.uniqueness_anchors),
            "spoiler_boundary": list(self.spoiler_boundary),
        }


class _LedgerBuilder:
    def __init__(self) -> None:
        self.items: list[VisualEvidence] = []
        self._summaries: set[str] = set()

    def add(
        self,
        *,
        source_type: str,
        source_ref: str,
        summary: Any,
        story_function: str,
        spoiler_level: str = "safe",
        visual_tags: Sequence[str] = (),
        source_sha256: str = "",
    ) -> None:
        compact = _text(summary)[:700]
        normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", compact.casefold()).strip()
        if len(compact) < 12 or normalized in self._summaries:
            return
        identity = f"{source_type}\0{source_ref}\0{compact}".encode("utf-8")
        self.items.append(VisualEvidence(
            evidence_id=f"ev-{_digest(identity)[:12]}",
            source_type=source_type,
            source_ref=source_ref,
            summary=compact,
            story_function=story_function,
            spoiler_level=spoiler_level,
            visual_tags=tuple(dict.fromkeys(_text(item) for item in visual_tags if _text(item))),
            source_sha256=source_sha256,
        ))
        self._summaries.add(normalized)


def _paragraphs(text: str) -> list[str]:
    cleaned = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    return [
        _text(value)
        for value in re.split(r"\n\s*\n|(?<=[.!?。！？])\s+(?=[A-Z\u4e00-\u9fff])", cleaned)
        if 35 <= len(_text(value)) <= 1600
    ]


def _visual_score(paragraph: str, names: Sequence[str]) -> float:
    folded = paragraph.casefold()
    visual = sum(term in folded for term in _VISUAL_TERMS)
    action = sum(term in folded for term in _ACTION_TERMS)
    named = sum(bool(name and name.casefold() in folded) for name in names)
    dialogue = 1 if any(mark in paragraph for mark in ('"', '“', '”')) else 0
    length_fit = 1 if 80 <= len(paragraph) <= 650 else 0
    return visual * 2.2 + action * 1.8 + named * 1.5 + dialogue * 0.4 + length_fit


def _best_paragraphs(text: str, *, names: Sequence[str], limit: int) -> list[tuple[int, str]]:
    values = _paragraphs(text)
    ranked = sorted(
        enumerate(values, start=1),
        key=lambda pair: (_visual_score(pair[1], names), -pair[0]),
        reverse=True,
    )
    return [pair for pair in ranked[:limit] if _visual_score(pair[1], names) > 0]


def _best_prompt_passages(
    text: str,
    *,
    names: Sequence[str],
    limit: int,
) -> list[tuple[int, str]]:
    """Choose visual passages across prompt sections instead of one dense cluster."""
    sections = re.split(r"(?=^#{1,6}\s+)", text, flags=re.MULTILINE)
    selected: list[tuple[int, str]] = []
    all_candidates: list[tuple[int, str]] = []
    offset = 0
    for section in sections:
        candidates = _best_paragraphs(section, names=names, limit=3)
        shifted = [(offset + index, paragraph) for index, paragraph in candidates]
        all_candidates.extend(shifted)
        if shifted:
            selected.append(shifted[0])
        offset += max(1, len(_paragraphs(section)))
    selected = sorted(
        selected,
        key=lambda pair: _visual_score(pair[1], names),
        reverse=True,
    )[:limit]
    seen = {paragraph for _, paragraph in selected}
    for pair in sorted(
        all_candidates,
        key=lambda candidate: _visual_score(candidate[1], names),
        reverse=True,
    ):
        if len(selected) >= limit:
            break
        if pair[1] not in seen:
            selected.append(pair)
            seen.add(pair[1])
    return selected


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _source_files(project: Path) -> list[Path]:
    outputs = project / "outputs"
    publication = next((path for path in (
        outputs / "publication" / "publication-copy.json",
        outputs / "deliverables" / "meta" / "publication-copy.json",
    ) if path.is_file() and not path.is_symlink()), None)
    fixed = [
        outputs / "input" / "prompt.md",
        outputs / "input" / "foundation.json",
        outputs / "input" / "ending_contract.json",
        outputs / "state" / "story_state.json",
        *([publication] if publication is not None else []),
    ]
    chapters = _final_chapter_paths(outputs)
    return [path for path in (*fixed, *chapters) if path.is_file() and not path.is_symlink()]


def _final_chapter_paths(outputs: Path) -> list[Path]:
    """Return promoted final chapters, excluding intermediate candidate finals."""
    manuscript = outputs / "manuscript"
    return sorted(
        path for path in manuscript.glob("chapter_*_final.md")
        if (
            re.fullmatch(r"chapter_\d+_final\.md", path.name)
            and path.is_file()
            and not path.is_symlink()
        )
    )


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_visual_evidence(
    project: str | Path,
    brief: "CoverBriefV2",
    *,
    prompt_text: str | None = None,
) -> VisualEvidenceLedger:
    """Collect compact visual evidence from every durable cover-relevant source.

    All source files participate in ``source_bundle_sha256`` even when only a
    representative passage reaches the model.  A changed chapter, preface, or
    prompt therefore makes an approved direction stale without sending the
    complete manuscript through the image-generation seam.
    """
    project_path = Path(project).resolve()
    files = _source_files(project_path)
    file_hashes = {path.relative_to(project_path).as_posix(): _file_digest(path) for path in files}
    bundle_payload = {
        "brief": brief.to_dict(),
        "files": file_hashes,
    }
    ledger = _LedgerBuilder()
    brief_sha = _json_digest(brief.to_dict())
    ledger.add(
        source_type="core_conflict",
        source_ref="cover_brief:core_conflict",
        summary=brief.core_conflict,
        story_function="central conflict and reader promise",
        visual_tags=("conflict", "relationship or power geometry"),
        source_sha256=brief_sha,
    )
    ledger.add(
        source_type="emotional_promise",
        source_ref="cover_brief:emotional_promise",
        summary=brief.emotional_promise,
        story_function="dominant emotional contradiction",
        visual_tags=("emotion", "tone"),
        source_sha256=brief_sha,
    )
    for character in brief.principal_characters:
        ledger.add(
            source_type="character",
            source_ref=f"cover_brief:character:{character.character_id}",
            summary=(
                f"{character.name}: {character.narrative_role}; {character.occupation_and_status}; "
                f"{character.daily_wardrobe}; {character.current_emotional_state}; {character.agency_signal}."
            ),
            story_function="character identity, behavior, and story agency",
            visual_tags=("character", character.narrative_role, character.lived_environment),
            source_sha256=brief_sha,
        )
    for node in brief.decisive_story_nodes:
        ledger.add(
            source_type="story_node",
            source_ref=f"cover_brief:node:{node.node_id}",
            summary=node.description,
            story_function="decisive visual story beat",
            visual_tags=("action", "decision"),
            source_sha256=brief_sha,
        )
    for signal in brief.secondary_signals:
        ledger.add(
            source_type="story_signal",
            source_ref=f"cover_brief:signal:{signal.signal_id}",
            summary=f"{signal.description}. Story function: {signal.story_function}.",
            story_function="story-bearing object or recurring signal",
            visual_tags=("object", "motif"),
            source_sha256=brief_sha,
        )

    outputs = project_path / "outputs"
    publication_path = next((path for path in (
        outputs / "publication" / "publication-copy.json",
        outputs / "deliverables" / "meta" / "publication-copy.json",
    ) if path.is_file()), None)
    if publication_path is not None:
        publication = _read_json(publication_path)
        publication_sha = file_hashes.get(publication_path.relative_to(project_path).as_posix(), "")
        functions = {
            "reader_heading": "reader-facing thematic frame",
            "hook_lead": "spoiler-safe opening hook",
            "spoiler_free_blurb": "commercial conflict promise",
            "whole_book_core_conflict": "whole-book conflict synthesis",
        }
        for field, function in functions.items():
            ledger.add(
                source_type="publication_intro",
                source_ref=f"{publication_path.relative_to(project_path).as_posix()}:{field}",
                summary=publication.get(field),
                story_function=function,
                visual_tags=("reader hook", "commercial promise"),
                source_sha256=publication_sha,
            )

    foundation_path = outputs / "input" / "foundation.json"
    foundation = _read_json(foundation_path) if foundation_path.is_file() else {}
    foundation_sha = file_hashes.get("outputs/input/foundation.json", "")
    ledger.add(
        source_type="foundation",
        source_ref="outputs/input/foundation.json:premise",
        summary=foundation.get("premise"),
        story_function="approved premise",
        visual_tags=("premise",),
        source_sha256=foundation_sha,
    )
    for index, thread in enumerate(foundation.get("plot_threads") or []):
        if isinstance(thread, Mapping):
            ledger.add(
                source_type="foundation",
                source_ref=f"outputs/input/foundation.json:plot_threads[{index}]",
                summary=thread.get("description") or thread.get("name"),
                story_function="approved plot thread",
                visual_tags=("plot thread", _text(thread.get("type"))),
                source_sha256=foundation_sha,
            )

    prompt_path = outputs / "input" / "prompt.md"
    if prompt_text is None and prompt_path.is_file():
        try:
            prompt_text = prompt_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            prompt_text = ""
    prompt_text = prompt_text or ""
    names = [character.name for character in brief.principal_characters]
    prompt_sha = file_hashes.get("outputs/input/prompt.md", _digest(prompt_text.encode("utf-8")))
    for paragraph_index, paragraph in _best_prompt_passages(
        prompt_text, names=names, limit=8,
    ):
        ledger.add(
            source_type="source_prompt",
            source_ref=f"outputs/input/prompt.md:paragraph:{paragraph_index}",
            summary=paragraph,
            story_function="approved story design or recurring visual detail",
            spoiler_level="tease",
            visual_tags=("source prompt", "visual detail"),
            source_sha256=prompt_sha,
        )

    chapters = _final_chapter_paths(outputs)
    if chapters:
        indexes = sorted({0, len(chapters) // 2, len(chapters) - 1})
        for order, chapter_index in enumerate(indexes):
            chapter = chapters[chapter_index]
            try:
                text = chapter.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            level = "safe" if chapter_index == 0 else "tease" if chapter_index < len(chapters) - 1 else "late_spoiler"
            chapter_sha = file_hashes.get(chapter.relative_to(project_path).as_posix(), "")
            for paragraph_index, paragraph in _best_paragraphs(text, names=names, limit=2):
                ledger.add(
                    source_type="final_chapter",
                    source_ref=f"{chapter.relative_to(project_path).as_posix()}:paragraph:{paragraph_index}",
                    summary=paragraph,
                    story_function=(
                        "opening visual truth" if order == 0 else
                        "mid-book escalation" if chapter_index < len(chapters) - 1 else
                        "late-book identity and motif verification"
                    ),
                    spoiler_level=level,
                    visual_tags=("final prose", "concrete scene"),
                    source_sha256=chapter_sha,
                )

    if not ledger.items:  # defensive fallback; brief normally guarantees entries
        ledger.add(
            source_type="core_task",
            source_ref="cover_brief:core_task",
            summary=brief.core_task,
            story_function="protagonist task",
            source_sha256=brief_sha,
        )
    return VisualEvidenceLedger(
        schema_version=1,
        source_bundle_sha256=_json_digest(bundle_payload),
        source_files=file_hashes,
        items=tuple(ledger.items[:40]),
    )


def evidence_ledger_from_brief(brief: "CoverBriefV2") -> VisualEvidenceLedger:
    """Build the deterministic in-memory adapter used by fixtures and clients."""
    builder = _LedgerBuilder()
    source_sha = _json_digest(brief.to_dict())
    builder.add(
        source_type="core_conflict",
        source_ref="cover_brief:core_conflict",
        summary=brief.core_conflict,
        story_function="central conflict",
        visual_tags=("conflict",),
        source_sha256=source_sha,
    )
    for node in brief.decisive_story_nodes:
        builder.add(
            source_type="story_node",
            source_ref=f"cover_brief:node:{node.node_id}",
            summary=node.description,
            story_function="decisive visual story beat",
            visual_tags=("action",),
            source_sha256=source_sha,
        )
    for signal in brief.secondary_signals:
        builder.add(
            source_type="story_signal",
            source_ref=f"cover_brief:signal:{signal.signal_id}",
            summary=signal.description,
            story_function=signal.story_function,
            visual_tags=("object",),
            source_sha256=source_sha,
        )
    return VisualEvidenceLedger(1, source_sha, {"cover_brief": source_sha}, tuple(builder.items))


__all__ = [
    "BookVisualIdentity",
    "VisualEvidence",
    "VisualEvidenceLedger",
    "collect_visual_evidence",
    "evidence_ledger_from_brief",
]
