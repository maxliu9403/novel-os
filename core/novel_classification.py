"""Canonical, versioned novel classification for planning and publication.

The legacy ``genre`` field is reader-facing prose.  It remains useful for old
projects and bylines, but it is a poor query contract: it mixes shelves,
tropes, settings, and emotional promises.  This module owns stable identifiers
for those separate axes and provides a deterministic migration path for legacy
metadata.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "novel-classification.v1"
CATALOG_VERSION = "novel-types.2026-10"
LEGACY_CATALOG_VERSION = "novel-types.2026-09"
SUPPORTED_CATALOG_VERSIONS = frozenset({LEGACY_CATALOG_VERSION, CATALOG_VERSION})

SOURCES = frozenset({"engine_inferred", "author_confirmed", "legacy_migration"})
AUDIENCE_CHANNELS = frozenset({"female", "male", "general"})
AGE_BANDS = frozenset({"unknown", "teen", "adult", "midlife"})
LENGTH_FORMS = frozenset({"unknown", "short", "long"})
CHAPTER_BANDS = frozenset({
    "unknown",
    "chapters_0_50",
    "chapters_51_100",
    "chapters_101_150",
    "chapters_151_200",
    "chapters_201_plus",
})
_CLASSIFICATION_BLOCK = re.compile(
    r"\[NOVEL_CLASSIFICATION_JSON\]\s*(.*?)\s*\[/NOVEL_CLASSIFICATION_JSON\]",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    axis: str
    zh: str
    en: str
    priority_tier: str
    aliases: tuple[str, ...] = ()
    design_requirement: str = ""
    legacy_labels: tuple[str, ...] = ()


def _entry(
    id: str,
    axis: str,
    zh: str,
    en: str,
    tier: str,
    *aliases: str,
    design: str = "",
    legacy: Sequence[str] = (),
) -> CatalogEntry:
    return CatalogEntry(
        id=id,
        axis=axis,
        zh=zh,
        en=en,
        priority_tier=tier,
        aliases=tuple(dict.fromkeys((id, zh, en, *aliases))),
        design_requirement=design,
        legacy_labels=tuple(legacy),
    )


PRIMARY_GENRES = (
    _entry("romance", "primary_genre", "浪漫", "Romance", "core", "言情"),
    _entry(
        "womens_fiction", "primary_genre", "女性小说", "Women's Fiction", "core",
        "women fiction", "female fiction", "女频", "adult contemporary women's fiction",
    ),
    _entry(
        "family_drama", "primary_genre", "家庭", "Family Drama", "core",
        "family fiction", "家庭情感", "domestic drama",
    ),
    _entry("mystery", "primary_genre", "悬疑", "Mystery", "growth", "悬疑小说"),
    _entry(
        "thriller", "primary_genre", "惊悚", "Thriller", "growth",
        "惊悚片", "惊悚小说", legacy=("惊悚片",),
    ),
    _entry("fantasy", "primary_genre", "奇幻", "Fantasy", "growth", "幻想"),
    _entry(
        "xuanhuan", "primary_genre", "玄幻", "Xuanhuan", "growth", "玄幻小说",
        "cultivation fantasy", "eastern fantasy",
        design="Make cultivation rules, advancement costs, and rival responses drive earned growth and escalating stakes.",
    ),
    _entry(
        "horror", "primary_genre", "恐怖", "Horror", "growth", "恐怖小说",
        design="Establish an escalating source of dread whose rules constrain choices and make survival costly.",
    ),
    _entry(
        "supernatural", "primary_genre", "超自然", "Supernatural", "growth",
        "paranormal", "超自然小说",
    ),
    _entry(
        "contemporary_realism", "primary_genre", "情感现实主义",
        "Contemporary Realism", "core", "contemporary fiction", "realistic fiction",
        "现实情感", "当代现实",
    ),
    _entry(
        "young_adult", "primary_genre", "青春", "Young Adult", "experimental",
        "ya", "青春小说", legacy=("青春激情",),
    ),
    _entry(
        "general_fiction", "primary_genre", "综合小说", "General Fiction",
        "compatibility",
    ),
)

STORY_TYPES = (
    _entry(
        "sweet_romance", "story_type", "甜蜜浪漫", "Sweet Romance", "growth",
        "sweet love", "甜宠",
        design="Build earned tenderness, relational safety, and visible reciprocal care.",
    ),
    _entry(
        "ceo_romance", "story_type", "CEO浪漫", "CEO Romance", "growth",
        "billionaire romance", "boss romance", "霸总", "总裁", "CEO", "霸道总裁",
        design="Make the power imbalance produce consequential choices rather than decorative wealth.",
    ),
    _entry(
        "dark_romance", "story_type", "黑暗浪漫", "Dark Romance", "growth",
        "dark romantic",
        design="Sustain danger, moral pressure, and costly attraction with a coherent emotional contract.",
    ),
    _entry(
        "revenge", "story_type", "复仇", "Revenge", "core", "revenge story",
        design="Establish the injury, an active counter-plan, escalating countermoves, and a visible consequence.",
    ),
    _entry(
        "second_chance", "story_type", "第二次机会", "Second Chance", "core",
        "second-chance", "重新开始", "破镜重圆",
        design="Make renewed trust depend on demonstrated change and a fresh high-cost choice.",
    ),
    _entry(
        "mafia", "story_type", "黑手党", "Mafia", "growth", "mob romance",
        design="Use the criminal power structure as active pressure on loyalty, intimacy, and survival.",
    ),
    _entry(
        "werewolf", "story_type", "狼人", "Werewolf", "growth", "wolf shifter", "shifter",
        design="Turn pack rules, identity, and bond expectations into causal story constraints.",
    ),
    _entry(
        "royal_intrigue", "story_type", "宫廷阴谋", "Royal Intrigue", "experimental",
        "court intrigue", "宫斗",
        design="Tie every alliance and betrayal to succession, legitimacy, or access to power.",
    ),
    _entry(
        "domestic_betrayal", "story_type", "家庭背叛", "Domestic Betrayal", "core",
        "betrayal", "betrayed", "cheating", "affair", "infidelity", "出轨", "背叛",
        design="Reveal a concrete intimate betrayal, its family cost, and the protagonist's decisive response.",
    ),
    _entry(
        "marriage_crisis", "story_type", "婚姻危机", "Marriage Crisis", "core",
        "marital crisis", "divorce", "离婚", "婚变",
        design="Center incompatible marital choices and force a durable change in the relationship structure.",
    ),
    _entry(
        "ethical_dilemma", "story_type", "伦理", "Ethical Drama", "core",
        "ethics", "ethical dilemma", "family ethics", "伦理小说",
        design="Give competing obligations credible emotional weight; trace injury, interpretation, conflicting feelings, and a consequential moral choice.",
    ),
    _entry(
        "female_growth", "story_type", "女性成长", "Female Growth", "core",
        "women's growth", "woman's growth", "female coming of age", "女性/男性成长",
        design="Ground her changing self-belief in specific wounds, mixed emotions, and increasingly independent choices with lasting consequences.",
    ),
    _entry(
        "male_growth", "story_type", "男性成长", "Male Growth", "core",
        "men's growth", "man's growth", "male coming of age", "女性/男性成长",
        design="Ground his changing self-belief in specific wounds, mixed emotions, and increasingly responsible choices with lasting consequences.",
    ),
    _entry(
        "celebrity", "story_type", "名人明星", "Celebrity", "growth",
        "celebrities", "celebrity romance", "famous star", "娱乐圈", "明星",
        design="Make public image, private needs, and career obligations collide in costly personal decisions.",
    ),
    _entry(
        "abuse_survival", "story_type", "受到虐待", "Abuse Survival", "core",
        "abuse survivor", "abused", "domestic abuse", "受虐", "遭受虐待", "家暴",
        design="Show the survivor's lived emotional impact, constraints, and choices toward agency; make recovery earned and never confuse abuse with proof of love.",
    ),
    _entry(
        "queen_empress", "story_type", "皇后女王", "Queen / Empress", "growth",
        "queen", "empress", "皇后", "女王",
        design="Give the queen or empress concrete authority, obligations, and personal loyalties that shape consequential decisions.",
    ),
    _entry(
        "love_at_first_sight", "story_type", "一见钟情", "Love at First Sight", "growth",
        "instant attraction",
        design="Turn immediate attraction into tested trust through later choices, incompatible needs, and earned emotional intimacy.",
    ),
    _entry(
        "office_romance", "story_type", "办公室恋情", "Office Romance", "growth",
        "workplace romance", "办公室恋爱",
        design="Make workplace responsibilities, professional boundaries, and attraction create credible relational costs and choices.",
    ),
    _entry(
        "workplace_comedy", "story_type", "职场闹剧", "Workplace Comedy", "growth",
        "office comedy", "workplace farce", "职场喜剧",
        design="Build escalating comic consequences from clashing workplace goals while preserving believable motives and emotional stakes.",
    ),
    _entry(
        "same_sex_romance", "story_type", "同性恋", "Same-Sex Romance", "growth",
        "gay romance", "lesbian romance", "queer romance", "同性恋情", "同性之爱",
        design="Build a specific same-sex relationship with distinct personalities, reciprocal attraction, emotional choices, and earned intimacy.",
    ),
    _entry(
        "single_parent", "story_type", "单身父母亲", "Single Parent", "growth",
        "single mother", "single father", "single parents", "单亲", "单身母亲", "单身父亲",
        design="Make caregiving, limited resources, and personal desires produce difficult choices for a parent with an independent emotional life.",
    ),
    _entry(
        "billionaire", "story_type", "富豪", "Billionaire", "growth",
        "millionaire", "wealthy heir", "富翁", "豪门",
        design="Make wealth change access, dependency, and family or romantic obligations instead of serving only as luxury scenery.",
    ),
    _entry(
        "pregnancy", "story_type", "怀孕", "Pregnancy", "growth",
        "pregnant", "expecting a baby", "孕期",
        design="Give pregnancy concrete emotional and practical consequences, preserve the pregnant character's agency, and let choices reshape relationships.",
    ),
    _entry(
        "apocalypse", "story_type", "末日降临", "Apocalypse", "growth",
        "apocalyptic", "doomsday", "end of the world", "末日", "末世",
        design="Stage the unfolding collapse through causal losses, survival choices, changing alliances, and escalating consequences.",
    ),
)

TONES = (
    _entry("angst", "tone", "虐心", "Angst", "core", "焦虑", "情绪虐心", legacy=("焦虑",)),
    _entry("sweet", "tone", "甜蜜", "Sweet", "growth", "暖甜"),
    _entry("dark", "tone", "黑暗", "Dark", "growth", "暗黑"),
    _entry("suspenseful", "tone", "紧张悬疑", "Suspenseful", "growth", "紧张", "suspense"),
    _entry("passionate", "tone", "激情", "Passionate", "experimental", "青春激情"),
    _entry(
        "emotional_realism", "tone", "情感现实主义", "Emotional Realism", "core",
        "realistic emotion", "现实情感",
    ),
)

SETTINGS = (
    _entry(
        "contemporary_urban", "setting", "现代城市", "Contemporary Urban", "core",
        "modern city", "contemporary urban", "urban", "现代都市", "都市",
    ),
    _entry("social_life", "setting", "社交生活", "Social Life", "experimental", "social world"),
    _entry(
        "family_domestic", "setting", "家庭生活", "Family / Domestic", "core",
        "domestic", "household", "家庭日常",
    ),
    _entry("royal_court", "setting", "宫廷", "Royal Court", "experimental", "royal court", "宫廷"),
    _entry("western", "setting", "西部", "Western", "experimental", "american west"),
    _entry(
        "post_apocalyptic", "setting", "后末日", "Post-Apocalyptic", "experimental",
        "post apocalyptic", "post-apocalypse", "末日后",
    ),
)

_AXES = {
    "primary_genres": PRIMARY_GENRES,
    "story_types": STORY_TYPES,
    "tones": TONES,
    "settings": SETTINGS,
}
_BY_AXIS = {
    "primary_genre": {item.id: item for item in PRIMARY_GENRES},
    "story_type": {item.id: item for item in STORY_TYPES},
    "tone": {item.id: item for item in TONES},
    "setting": {item.id: item for item in SETTINGS},
}
_ALL = {item.id: item for values in _AXES.values() for item in values}
_OCTOBER_IDS = frozenset({
    "xuanhuan", "horror", "ethical_dilemma", "female_growth", "male_growth",
    "celebrity", "abuse_survival", "queen_empress", "love_at_first_sight",
    "office_romance", "workplace_comedy", "same_sex_romance", "single_parent",
    "billionaire", "pregnancy", "apocalypse",
})
_TIER_ORDER = {"core": 0, "growth": 1, "experimental": 2, "compatibility": 3}


def _normalized_text(*values: Any) -> str:
    parts: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            parts.extend(str(item) for item in value)
        else:
            parts.append(str(value))
    text = " ".join(parts).casefold().replace("’", "'")
    return re.sub(r"[^\w\u3400-\u9fff']+", " ", text).strip()


def _alias_position(text: str, alias: str) -> int:
    needle = _normalized_text(alias)
    if not needle:
        return -1
    if re.search(r"[\u3400-\u9fff]", needle):
        return text.find(needle)
    match = re.search(rf"(?<![\w']){re.escape(needle)}(?![\w'])", text)
    return match.start() if match else -1


def _matches(text: str, entries: Iterable[CatalogEntry]) -> list[str]:
    found: list[tuple[int, int, str]] = []
    for order, item in enumerate(entries):
        positions = [
            position
            for alias in (*item.aliases, *item.legacy_labels)
            if (position := _alias_position(text, alias)) >= 0
        ]
        if positions:
            found.append((min(positions), order, item.id))
    return [item_id for _position, _order, item_id in sorted(found)]


def _unique(values: Iterable[str], maximum: int) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))[:maximum]


def _validated_ids(
    values: Any,
    *,
    axis: str,
    field_name: str,
    maximum: int,
) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{field_name} must be a list")
    normalized = tuple(str(value).strip() for value in values)
    if any(not value for value in normalized):
        raise ValueError(f"{field_name} must contain nonblank ids")
    if len(normalized) > maximum or len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must contain at most {maximum} unique ids")
    unknown = [value for value in normalized if value not in _BY_AXIS[axis]]
    if unknown:
        raise ValueError(f"{field_name} contains unknown ids: {', '.join(unknown)}")
    return normalized


def _chapter_shape(chapters: int | None) -> tuple[str, str]:
    if chapters is None or chapters < 1:
        return "unknown", "unknown"
    if chapters <= 50:
        return "short", "chapters_0_50"
    if chapters <= 100:
        return "long", "chapters_51_100"
    if chapters <= 150:
        return "long", "chapters_101_150"
    if chapters <= 200:
        return "long", "chapters_151_200"
    return "long", "chapters_201_plus"


@dataclass(frozen=True)
class NovelClassification:
    primary_genre_id: str
    secondary_genre_ids: tuple[str, ...] = ()
    story_type_ids: tuple[str, ...] = ()
    tone_ids: tuple[str, ...] = ()
    setting_ids: tuple[str, ...] = ()
    audience_channel: str = "general"
    audience_age_band: str = "unknown"
    length_form: str = "unknown"
    chapter_band: str = "unknown"
    source: str = "engine_inferred"
    confidence: float = 0.5
    catalog_version: str = CATALOG_VERSION

    def __post_init__(self) -> None:
        if self.catalog_version not in SUPPORTED_CATALOG_VERSIONS:
            raise ValueError("classification catalog_version is unsupported")
        if self.primary_genre_id not in _BY_AXIS["primary_genre"]:
            raise ValueError("primary_genre_id is unknown")
        for field_name, axis, maximum in (
            ("secondary_genre_ids", "primary_genre", 2),
            ("story_type_ids", "story_type", 3),
            ("tone_ids", "tone", 3),
            ("setting_ids", "setting", 2),
        ):
            normalized = _validated_ids(
                getattr(self, field_name),
                axis=axis,
                field_name=field_name,
                maximum=maximum,
            )
            object.__setattr__(self, field_name, normalized)
        if self.catalog_version == LEGACY_CATALOG_VERSION and _OCTOBER_IDS.intersection(self.filter_type_ids):
            raise ValueError("classification ids are not available in this catalog_version")
        if self.primary_genre_id in self.secondary_genre_ids:
            raise ValueError("primary_genre_id cannot also be secondary")
        if {"sweet_romance", "dark_romance"} <= set(self.story_type_ids):
            raise ValueError("sweet_romance and dark_romance are mutually exclusive")
        if self.audience_channel not in AUDIENCE_CHANNELS:
            raise ValueError("audience.channel is unknown")
        if self.audience_age_band not in AGE_BANDS:
            raise ValueError("audience.age_band is unknown")
        if self.length_form not in LENGTH_FORMS:
            raise ValueError("length.form is unknown")
        if self.chapter_band not in CHAPTER_BANDS:
            raise ValueError("length.chapter_band is unknown")
        if self.source not in SOURCES:
            raise ValueError("classification source is unknown")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise ValueError("classification confidence must be numeric")
        if not 0 <= float(self.confidence) <= 1:
            raise ValueError("classification confidence must be between 0 and 1")
        object.__setattr__(self, "confidence", round(float(self.confidence), 3))

    @property
    def filter_type_ids(self) -> tuple[str, ...]:
        return _unique((
            self.primary_genre_id,
            *self.secondary_genre_ids,
            *self.story_type_ids,
            *self.tone_ids,
            *self.setting_ids,
        ), 32)

    @property
    def commercial_tier(self) -> str:
        entries = [
            _ALL[item_id]
            for item_id in (
                self.primary_genre_id,
                *self.secondary_genre_ids,
                *self.story_type_ids,
            )
        ]
        return min(entries, key=lambda item: _TIER_ORDER[item.priority_tier]).priority_tier

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "catalog_version": self.catalog_version,
            "primary_genre_id": self.primary_genre_id,
            "secondary_genre_ids": list(self.secondary_genre_ids),
            "story_type_ids": list(self.story_type_ids),
            "tone_ids": list(self.tone_ids),
            "setting_ids": list(self.setting_ids),
            "audience": {
                "channel": self.audience_channel,
                "age_band": self.audience_age_band,
            },
            "length": {
                "form": self.length_form,
                "chapter_band": self.chapter_band,
            },
        }

    @property
    def classification_id(self) -> str:
        raw = json.dumps(
            self._identity_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "classification:" + hashlib.sha256(raw).hexdigest()

    def _labels(self, locale: str) -> dict[str, Any]:
        attribute = "zh" if locale == "zh-CN" else "en"
        label = lambda item_id: getattr(_ALL[item_id], attribute)
        return {
            "primary_genre": label(self.primary_genre_id),
            "secondary_genres": [label(value) for value in self.secondary_genre_ids],
            "story_types": [label(value) for value in self.story_type_ids],
            "tones": [label(value) for value in self.tone_ids],
            "settings": [label(value) for value in self.setting_ids],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "catalog_version": self.catalog_version,
            "classification_id": self.classification_id,
            **self._identity_payload(),
            "commercial_tier": self.commercial_tier,
            "source": self.source,
            "confidence": self.confidence,
            "filter_type_ids": list(self.filter_type_ids),
            "display_labels": {
                "zh-CN": self._labels("zh-CN"),
                "en-US": self._labels("en-US"),
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NovelClassification":
        if not isinstance(data, Mapping):
            raise ValueError("classification must be an object")
        if data.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
            raise ValueError("classification schema_version is unsupported")
        if data.get("catalog_version", CATALOG_VERSION) not in SUPPORTED_CATALOG_VERSIONS:
            raise ValueError("classification catalog_version is unsupported")
        audience = data.get("audience") or {}
        length = data.get("length") or {}
        if not isinstance(audience, Mapping) or not isinstance(length, Mapping):
            raise ValueError("classification audience and length must be objects")
        value = cls(
            catalog_version=str(data.get("catalog_version", CATALOG_VERSION)),
            primary_genre_id=str(data.get("primary_genre_id") or "").strip(),
            secondary_genre_ids=_validated_ids(
                data.get("secondary_genre_ids"), axis="primary_genre",
                field_name="secondary_genre_ids", maximum=2,
            ),
            story_type_ids=_validated_ids(
                data.get("story_type_ids"), axis="story_type",
                field_name="story_type_ids", maximum=3,
            ),
            tone_ids=_validated_ids(
                data.get("tone_ids"), axis="tone", field_name="tone_ids", maximum=3,
            ),
            setting_ids=_validated_ids(
                data.get("setting_ids"), axis="setting", field_name="setting_ids", maximum=2,
            ),
            audience_channel=str(audience.get("channel") or "general"),
            audience_age_band=str(audience.get("age_band") or "unknown"),
            length_form=str(length.get("form") or "unknown"),
            chapter_band=str(length.get("chapter_band") or "unknown"),
            source=str(data.get("source") or "author_confirmed"),
            confidence=float(data.get("confidence", 1.0)),
        )
        supplied_id = data.get("classification_id")
        if supplied_id is not None and supplied_id != value.classification_id:
            raise ValueError("classification_id does not match classification fields")
        supplied_filter = data.get("filter_type_ids")
        if supplied_filter is not None and list(supplied_filter) != list(value.filter_type_ids):
            raise ValueError("filter_type_ids do not match classification fields")
        supplied_tier = data.get("commercial_tier")
        if supplied_tier is not None and supplied_tier != value.commercial_tier:
            raise ValueError("commercial_tier does not match classification fields")
        supplied_labels = data.get("display_labels")
        canonical_labels = value.to_dict()["display_labels"]
        if supplied_labels is not None and supplied_labels != canonical_labels:
            raise ValueError("display_labels do not match classification fields")
        return value

    def with_source(self, source: str, confidence: float | None = None) -> "NovelClassification":
        return replace(
            self,
            source=source,
            confidence=self.confidence if confidence is None else confidence,
        )


def infer_classification(
    *,
    genre: str = "",
    genres: Sequence[str] = (),
    premise: str = "",
    audience: str = "",
    tone: str = "",
    raw_prompt: str = "",
    chapters: int | None = None,
    source: str = "engine_inferred",
) -> NovelClassification:
    """Select canonical ids from approved catalog values only."""

    genre_text = _normalized_text(genre, genres)
    # The complete prompt often names rejected options in "forbidden" or
    # comparison sections. Prefer affirmative metadata; use raw prose only as
    # a last-resort signal when the brief supplied no descriptive fields.
    fallback_prompt = raw_prompt if not any((genre, genres, premise, tone)) else ""
    full_text = _normalized_text(genre, genres, premise, tone, fallback_prompt)
    audience_text = _normalized_text(audience)

    primary_hits = _matches(genre_text, PRIMARY_GENRES)
    female = any(token in audience_text for token in ("female", "women", "woman", "女频", "女性"))
    male = any(token in audience_text for token in ("male", "men", "man", "男频", "男性"))
    audience_channel = "female" if female else "male" if male else "general"

    if "womens_fiction" in primary_hits:
        primary = "womens_fiction"
    elif primary_hits:
        primary = primary_hits[0]
    elif female and _matches(full_text, STORY_TYPES):
        primary = "womens_fiction"
    else:
        primary = "general_fiction"

    story_types = _matches(full_text, STORY_TYPES)
    tones = _matches(_normalized_text(tone, genre, genres, premise, raw_prompt), TONES)
    settings = _matches(full_text, SETTINGS)

    implied_secondary: list[str] = []
    if primary == "womens_fiction" and any(
        value in story_types for value in ("domestic_betrayal", "marriage_crisis", "revenge")
    ):
        implied_secondary.append("family_drama")
    if any(value in story_types for value in ("sweet_romance", "ceo_romance", "dark_romance")):
        implied_secondary.append("romance")
    if "werewolf" in story_types:
        implied_secondary.append("supernatural")
    if "royal_intrigue" in story_types:
        implied_secondary.append("fantasy")
    secondary = _unique(
        (value for value in (*primary_hits, *implied_secondary) if value != primary), 2
    )

    if primary in {"mystery", "thriller"} and "suspenseful" not in tones:
        tones.append("suspenseful")
    if "sweet_romance" in story_types and "sweet" not in tones:
        tones.append("sweet")
    if "dark_romance" in story_types and "dark" not in tones:
        tones.append("dark")
    if primary == "contemporary_realism" and "emotional_realism" not in tones:
        tones.append("emotional_realism")
    tones_tuple = _unique(tones, 3)

    if any(value in story_types for value in ("domestic_betrayal", "marriage_crisis")):
        settings.append("family_domestic")
    settings_tuple = _unique(settings, 2)

    age_band = "unknown"
    if any(token in audience_text for token in ("teen", "young adult", "adolescent", "青少年", "青春")):
        age_band = "teen"
    elif any(token in audience_text for token in ("midlife", "middle aged", "中年", "45 60", "50岁", "50 岁")) or re.search(
        r"\b(?:4[5-9]|5\d|60)\b", audience_text
    ):
        age_band = "midlife"
    elif any(token in audience_text for token in ("adult", "成年")):
        age_band = "adult"

    length_form, chapter_band = _chapter_shape(chapters)
    explicit_primary = primary != "general_fiction"
    confidence = 0.9 if explicit_primary and story_types else 0.78 if explicit_primary else 0.45
    return NovelClassification(
        primary_genre_id=primary,
        secondary_genre_ids=secondary,
        story_type_ids=_unique(story_types, 3),
        tone_ids=tones_tuple,
        setting_ids=settings_tuple,
        audience_channel=audience_channel,
        audience_age_band=age_band,
        length_form=length_form,
        chapter_band=chapter_band,
        source=source,
        confidence=confidence,
    )


def parse_classification_block(prompt: str) -> NovelClassification | None:
    matches = _CLASSIFICATION_BLOCK.findall(str(prompt or ""))
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("prompt must contain exactly one NOVEL_CLASSIFICATION_JSON block")
    body = matches[0].strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", body, re.IGNORECASE | re.DOTALL)
    if fenced:
        body = fenced.group(1)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("NOVEL_CLASSIFICATION_JSON block contains invalid JSON") from exc
    return NovelClassification.from_dict(payload).with_source("author_confirmed", 1.0)


def classification_from_metadata(
    metadata: Mapping[str, Any],
    *,
    raw_prompt: str = "",
    source: str = "legacy_migration",
) -> NovelClassification:
    existing = metadata.get("classification")
    if isinstance(existing, Mapping):
        return NovelClassification.from_dict(existing)
    chapters = metadata.get("target_chapters")
    try:
        chapter_count = int(chapters) if chapters is not None else None
    except (TypeError, ValueError):
        chapter_count = None
    return infer_classification(
        genre=str(metadata.get("genre") or ""),
        genres=tuple(metadata.get("genres") or ()),
        premise=str(metadata.get("premise") or ""),
        audience=str(metadata.get("audience") or ""),
        tone=str(metadata.get("tone") or ""),
        raw_prompt=raw_prompt,
        chapters=chapter_count,
        source=source,
    )


def design_requirements(
    classification: NovelClassification, *, genre: str = "",
) -> tuple[str, ...]:
    # Canonical publication tags stay bounded, but every author-selected genre
    # must reach planning even when a hybrid includes more than three tropes.
    ids = dict.fromkeys((
        classification.primary_genre_id,
        *classification.secondary_genre_ids,
        *classification.story_type_ids,
        *_matches(_normalized_text(genre), (*PRIMARY_GENRES, *STORY_TYPES)),
    ))
    return tuple(
        _ALL[item_id].design_requirement
        for item_id in ids
        if _ALL[item_id].design_requirement
    )


def catalog_payload() -> dict[str, Any]:
    def public(item: CatalogEntry) -> dict[str, Any]:
        payload = {
            "id": item.id,
            "labels": {"zh-CN": item.zh, "en-US": item.en},
            "priority_tier": item.priority_tier,
        }
        if item.design_requirement:
            payload["design_requirement"] = item.design_requirement
        if item.legacy_labels:
            payload["legacy_labels"] = list(item.legacy_labels)
        return payload

    return {
        "schema_version": SCHEMA_VERSION,
        "catalog_version": CATALOG_VERSION,
        "supported_catalog_versions": sorted(SUPPORTED_CATALOG_VERSIONS),
        "axes": {
            axis: [public(item) for item in entries]
            for axis, entries in _AXES.items()
        },
        "audience_channels": sorted(AUDIENCE_CHANNELS),
        "age_bands": sorted(AGE_BANDS),
        "length_forms": sorted(LENGTH_FORMS),
        "chapter_bands": sorted(CHAPTER_BANDS),
        "combination_rules": {
            "primary_genre_count": 1,
            "secondary_genre_max": 2,
            "story_type_max": 3,
            "tone_max": 3,
            "setting_max": 2,
            "mutually_exclusive_story_types": [["sweet_romance", "dark_romance"]],
        },
    }


def canonical_json_bytes(classification: NovelClassification | Mapping[str, Any]) -> bytes:
    value = (
        classification
        if isinstance(classification, NovelClassification)
        else NovelClassification.from_dict(classification)
    )
    return json.dumps(
        value.to_dict(),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"


def markdown_front_matter(classification: NovelClassification | Mapping[str, Any]) -> str:
    value = (
        classification
        if isinstance(classification, NovelClassification)
        else NovelClassification.from_dict(classification)
    ).to_dict()
    lines = [
        "---",
        f"novel_os_schema: {json.dumps(value['schema_version'])}",
        f"novel_os_catalog: {json.dumps(value['catalog_version'])}",
        f"classification_id: {json.dumps(value['classification_id'])}",
        f"primary_genre_id: {json.dumps(value['primary_genre_id'])}",
        "secondary_genre_ids: " + json.dumps(value["secondary_genre_ids"], ensure_ascii=False),
        "story_type_ids: " + json.dumps(value["story_type_ids"], ensure_ascii=False),
        "tone_ids: " + json.dumps(value["tone_ids"], ensure_ascii=False),
        "setting_ids: " + json.dumps(value["setting_ids"], ensure_ascii=False),
        "filter_type_ids: " + json.dumps(value["filter_type_ids"], ensure_ascii=False),
        f"audience_channel: {json.dumps(value['audience']['channel'])}",
        f"audience_age_band: {json.dumps(value['audience']['age_band'])}",
        f"length_form: {json.dumps(value['length']['form'])}",
        f"chapter_band: {json.dumps(value['length']['chapter_band'])}",
        "---",
    ]
    return "\n".join(lines) + "\n"


__all__ = [
    "CATALOG_VERSION",
    "SCHEMA_VERSION",
    "NovelClassification",
    "canonical_json_bytes",
    "catalog_payload",
    "classification_from_metadata",
    "design_requirements",
    "infer_classification",
    "markdown_front_matter",
    "parse_classification_block",
]
