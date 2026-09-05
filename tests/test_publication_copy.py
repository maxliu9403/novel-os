import json
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace

import pytest

from core.publication_copy import (
    PublicationCopy,
    canonical_json_bytes,
    count_publication_units,
    parse_publication_copy,
    validate_publication_candidate,
)


SHA = "a" * 64
HOOK = "Claire confronts Ethan before his lies cost their daughter."
BLURB = " ".join(f"detail{number}" for number in range(120)) + "."

SEMANTIC_CHECKS = {
    "whole_book_core_conflict": True,
    "hook_core_conflict": True,
    "blurb_core_conflict": True,
    "protagonist_stakes": True,
    "spoiler_free": True,
    "source_supported": True,
}
READER_PULL_CHECKS = {
    "first_glance_clarity": True,
    "concrete_emotional_stakes": True,
    "escalating_pressure": True,
    "protagonist_agency": True,
    "open_loop": True,
    "truthful_genre_promise": True,
}


def valid_candidate(**overrides):
    candidate = {
        "language": "en-US",
        "reader_heading": "Before the Story",
        "hook_lead": HOOK,
        "spoiler_free_blurb": BLURB,
        "checks": dict(SEMANTIC_CHECKS),
        "reader_pull": {
            "status": "pass",
            "checks": dict(READER_PULL_CHECKS),
        },
        "claim_evidence": [
            {
                "claim": "Claire keeps records.",
                "chapter": 2,
                "source_quote": "Claire saves every bank statement.",
            }
        ],
    }
    candidate.update(overrides)
    return candidate


def valid_copy_payload():
    return {
        "schema_version": 1,
        "title": "The Empty Chair Beside Her",
        "language": "en-US",
        "reader_heading": "Before the Story",
        "hook_lead": HOOK,
        "spoiler_free_blurb": BLURB,
        "whole_book_core_conflict": {
            "protagonist": "Claire Bennett",
            "goal": "Protect her daughter",
            "opposition": "Ethan's sustained deception",
            "stakes": "Her daughter's stable home",
            "escalation": "The lies spread into money and childcare",
            "unresolved_choice": "Whether Claire will leave the marriage",
            "evidence": {
                "opening": [{"chapter": 1, "source_quote": "The dinner charge was not hers."}],
                "middle": [{"chapter": 6, "source_quote": "The nursery account was empty."}],
                "late": [{"chapter": 10, "source_quote": "She opened a clean ledger."}],
            },
        },
        "source": {
            "run_id": "run-001",
            "book_check_stage_sha256": SHA,
            "source_set_sha256": "b" * 64,
            "chapters": [
                {"number": 1, "revision_id": "rev-001", "sha256": "c" * 64},
                {"number": 2, "revision_id": "rev-002", "sha256": "d" * 64},
                {"number": 6, "revision_id": "rev-006", "sha256": "6" * 64},
                {"number": 10, "revision_id": "rev-010", "sha256": "0" * 64},
            ],
        },
        "generation": {
            "conflict_provider": "provider",
            "conflict_model": "conflict-model",
            "conflict_prompt_version": "whole-book-conflict.v1",
            "conflict_response_sha256": "e" * 64,
            "writer_provider": "provider",
            "writer_model": "writer-model",
            "writer_prompt_version": "publication-copy-writer.v2",
            "writer_response_sha256": "f" * 64,
            "validator_provider": "provider",
            "validator_model": "validator-model",
            "validator_prompt_version": "publication-copy-validator.v2",
            "validator_response_sha256": "1" * 64,
            "generated_at": "2026-08-31T00:00:00Z",
        },
        "validation": {
            "policy_version": "publication-copy-policy.v3",
            "status": "pass",
            "length": {
                "unit": "words",
                "hook_lead": 9,
                "spoiler_free_blurb": 120,
            },
            "checks": {
                **SEMANTIC_CHECKS,
                "not_chapter_one_copy": True,
            },
            "reader_pull": {
                "status": "pass",
                "checks": dict(READER_PULL_CHECKS),
            },
            "claim_evidence": [
                {
                    "claim": "Claire keeps records.",
                    "chapter": 2,
                    "source_quote": "Claire saves every bank statement.",
                }
            ],
        },
    }


def test_round_trip_is_frozen_and_emits_exact_schema_v1_fields():
    payload = valid_copy_payload()
    publication_copy = parse_publication_copy(json.dumps(payload))

    assert isinstance(publication_copy, PublicationCopy)
    assert publication_copy.to_dict() == payload
    assert set(publication_copy.to_dict()) == {
        "schema_version",
        "title",
        "language",
        "reader_heading",
        "hook_lead",
        "spoiler_free_blurb",
        "whole_book_core_conflict",
        "source",
        "generation",
        "validation",
    }
    with pytest.raises(FrozenInstanceError):
        publication_copy.title = "Changed"
    with pytest.raises(ValueError, match="generation must be a PublicationGeneration"):
        replace(publication_copy, generation={})


def test_round_trip_rejects_unknown_and_duplicate_keys():
    raw = '{"schema_version":1,"schema_version":1}'
    with pytest.raises(ValueError, match="duplicate JSON key"):
        parse_publication_copy(raw)

    payload = valid_copy_payload()
    payload["unexpected"] = True
    with pytest.raises(ValueError, match="unknown publication copy field"):
        parse_publication_copy(json.dumps(payload))

    payload = valid_copy_payload()
    payload["validation"]["length"]["unexpected"] = 1
    with pytest.raises(ValueError, match="unknown publication validation length field"):
        parse_publication_copy(json.dumps(payload))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value["source"].update(source_set_sha256="A" * 64), "lowercase 64 hex"),
        (lambda value: value.update(title=" Untrimmed"), "title must be trimmed"),
        (lambda value: value["validation"].update(status="fail"), "validation.status must be pass"),
        (
            lambda value: value["validation"]["reader_pull"].update(status="fail"),
            "validation.reader_pull.status must be pass",
        ),
        (
            lambda value: value["generation"].update(
                generated_at="2026-08-31 00:00:00+00:00"
            ),
            "RFC3339",
        ),
    ],
)
def test_round_trip_rejects_malformed_integrity_fields(mutate, message):
    payload = valid_copy_payload()
    mutate(payload)
    with pytest.raises(ValueError, match=message):
        parse_publication_copy(json.dumps(payload))


def test_round_trip_rejects_out_of_range_copy_even_when_declared_count_matches():
    payload = valid_copy_payload()
    payload["hook_lead"] = "one two three four five six seven."
    payload["validation"]["length"]["hook_lead"] = 7

    with pytest.raises(ValueError, match="hook_lead_length"):
        parse_publication_copy(json.dumps(payload))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda value: value["source"]["chapters"].pop(),
            "conflict evidence chapter 10 is absent from source.chapters",
        ),
        (
            lambda value: value["validation"]["claim_evidence"][0].update(chapter=3),
            "claim_evidence chapter 3 is absent from source.chapters",
        ),
    ],
)
def test_round_trip_rejects_evidence_for_an_undeclared_source_chapter(mutate, message):
    payload = valid_copy_payload()
    mutate(payload)

    with pytest.raises(ValueError, match=message):
        parse_publication_copy(json.dumps(payload))


def test_canonical_json_bytes_are_compact_sorted_utf8_and_finite():
    assert canonical_json_bytes({"z": 1, "a": "她"}) == b'{"a":"\xe5\xa5\xb9","z":1}'
    with pytest.raises(ValueError):
        canonical_json_bytes({"invalid": float("nan")})


def test_english_other_space_delimited_and_cjk_counters_use_contract_units():
    assert count_publication_units("Pregnant, thirty-six weeks", "en-US") == ("words", 3)
    assert count_publication_units("deja vu numero 2", "fr-FR") == ("words", 4)
    assert count_publication_units("她必须保护女儿，离开谎言。", "zh-CN") == (
        "content_characters",
        11,
    )
    assert count_publication_units("one two three four five six seven eight", "en-US") == (
        "words",
        8,
    )


@pytest.mark.parametrize(
    ("language", "hook", "blurb", "unit", "hook_length", "blurb_length"),
    [
        ("en-US", " ".join(f"h{n}" for n in range(8)) + ".", " ".join(f"b{n}" for n in range(120)) + ".", "words", 8, 120),
        ("en-US", " ".join(f"h{n}" for n in range(20)) + ".", " ".join(f"b{n}" for n in range(180)) + ".", "words", 20, 180),
        ("zh-CN", "甲" * 16 + "。", "乙" * 240 + "。", "content_characters", 16, 240),
        ("zh-CN", "甲" * 36 + "。", "乙" * 360 + "。", "content_characters", 36, 360),
    ],
)
def test_quality_gate_accepts_inclusive_language_boundaries(
    language, hook, blurb, unit, hook_length, blurb_length
):
    validation = validate_publication_candidate(
        valid_candidate(language=language, hook_lead=hook, spoiler_free_blurb=blurb),
        source_chapters={2: "Claire saves every bank statement."},
        chapter_one_prefix="Unrelated opening language with no shared sequence.",
        ending_spoilers=[],
    )

    assert validation.length_unit == unit
    assert validation.hook_lead_length == hook_length
    assert validation.spoiler_free_blurb_length == blurb_length
    assert validation.status == "pass"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"hook_lead": "One two three four five six seven."}, "hook_lead_length"),
        ({"hook_lead": "One two three four five six seven eight. Another sentence."}, "single_sentence_hook"),
        ({"hook_lead": "One two three four five six seven eight. Another fragment"}, "single_sentence_hook"),
        ({"hook_lead": "One two three four five six seven eight.First.Following"}, "single_sentence_hook"),
        ({"reader_heading": "# Before the Story"}, "plain_text_reader_heading"),
        ({"spoiler_free_blurb": "<b>" + BLURB}, "forbidden_markup"),
        ({"spoiler_free_blurb": "*detail0* " + " ".join(f"detail{n}" for n in range(1, 120)) + "."}, "forbidden_markup"),
        ({"spoiler_free_blurb": "https://example.com " + BLURB}, "forbidden_url"),
        ({"spoiler_free_blurb": "Buy now " + BLURB}, "promotional_cta"),
        ({"spoiler_free_blurb": "Read more " + BLURB}, "promotional_cta"),
        ({"hook_lead": "A story about love and betrayal unfolds tonight."}, "generic_reader_hook"),
    ],
)
def test_quality_gate_rejects_invalid_shape_and_promotional_copy(overrides, message):
    with pytest.raises(ValueError, match=message):
        validate_publication_candidate(
            valid_candidate(**overrides),
            source_chapters={2: "Claire saves every bank statement."},
            chapter_one_prefix="Unrelated opening language with no shared sequence.",
            ending_spoilers=[],
        )


def test_quality_gate_rejects_cjk_sentence_boundary_without_whitespace():
    with pytest.raises(ValueError, match="single_sentence_hook"):
        validate_publication_candidate(
            valid_candidate(
                language="zh-CN",
                hook_lead="她必须保护女儿离开谎言寻找真正真相。下一句。",
                spoiler_free_blurb="雨" * 240 + "。",
            ),
            source_chapters={2: "Claire saves every bank statement."},
            chapter_one_prefix="完全不同的开篇内容。",
            ending_spoilers=[],
        )


def test_quality_gate_accepts_one_sentence_with_terminal_closing_quote():
    validation = validate_publication_candidate(
        valid_candidate(
            hook_lead='"Claire confronts Ethan before his lies cost their daughter."'
        ),
        source_chapters={2: "Claire saves every bank statement."},
        chapter_one_prefix="Unrelated opening.",
        ending_spoilers=[],
    )

    assert validation.status == "pass"


def test_quality_gate_accepts_cjk_sentence_with_terminal_closing_quote():
    validation = validate_publication_candidate(
        valid_candidate(
            language="ja-JP",
            hook_lead="「她必须保护女儿离开谎言寻找真正真相。」",
            spoiler_free_blurb="雨" * 240 + "。",
        ),
        source_chapters={2: "Claire saves every bank statement."},
        chapter_one_prefix="完全不同的开篇内容。",
        ending_spoilers=[],
    )

    assert validation.status == "pass"


def test_quality_gate_rejects_spoilers_and_repeated_hook_blurb_windows():
    with pytest.raises(ValueError, match="ending_spoiler"):
        validate_publication_candidate(
            valid_candidate(spoiler_free_blurb=BLURB + " Ethan wins custody."),
            source_chapters={2: "Claire saves every bank statement."},
            chapter_one_prefix="Unrelated opening.",
            ending_spoilers=["Ethan wins custody"],
        )

    repeated = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
    with pytest.raises(ValueError, match="hook_blurb_repetition"):
        validate_publication_candidate(
            valid_candidate(
                hook_lead=repeated + ".",
                spoiler_free_blurb=repeated + " " + BLURB,
            ),
            source_chapters={2: "Claire saves every bank statement."},
            chapter_one_prefix="Unrelated opening.",
            ending_spoilers=[],
        )


@pytest.mark.parametrize("language", ["zh-CN", "ja-JP", "ko-KR"])
def test_quality_gate_rejects_cjk_hook_blurb_content_character_repetition(language):
    repeated = "甲乙丙丁戊己庚辛壬癸"
    with pytest.raises(ValueError, match="hook_blurb_repetition"):
        validate_publication_candidate(
            valid_candidate(
                language=language,
                hook_lead="甲，乙 丙、丁戊己庚辛壬癸她必须选择回家。",
                spoiler_free_blurb=repeated + "雨" * 230 + "。",
            ),
            source_chapters={2: "Claire saves every bank statement."},
            chapter_one_prefix="完全不同的开篇内容。",
            ending_spoilers=[],
        )


@pytest.mark.parametrize("language", ["zh-CN", "ja-JP", "ko-KR"])
def test_quality_gate_rejects_cjk_copy_of_first_chapter(language):
    copied = "甲乙丙丁戊己庚辛"
    with pytest.raises(ValueError, match="not_chapter_one_copy"):
        validate_publication_candidate(
            valid_candidate(
                language=language,
                hook_lead="甲，乙 丙、丁戊己庚辛她必须选择离开谎言。",
                spoiler_free_blurb="雨" * 240 + "。",
            ),
            source_chapters={2: "Claire saves every bank statement."},
            chapter_one_prefix=copied + "之后她打开了门。",
            ending_spoilers=[],
        )

def test_quality_gate_rejects_copy_of_first_chapter_and_wrong_claim_quote():
    copied = "Pregnant and betrayed Claire must choose to protect her daughter"
    with pytest.raises(ValueError, match="not_chapter_one_copy"):
        validate_publication_candidate(
            valid_candidate(hook_lead=copied + "."),
            source_chapters={
                1: copied + ". Then the door opened.",
                2: "Claire saves every bank statement.",
            },
            chapter_one_prefix=copied + ". Then the door opened.",
            ending_spoilers=[],
        )

    candidate = deepcopy(valid_candidate())
    candidate["claim_evidence"] = [
        {"claim": "The bank closes her account.", "chapter": 2, "source_quote": "not in chapter"}
    ]
    with pytest.raises(ValueError, match="source_quote"):
        validate_publication_candidate(
            candidate,
            source_chapters={1: "Opening.", 2: "Chapter two."},
            chapter_one_prefix="Opening.",
            ending_spoilers=[],
        )


def test_quality_gate_rejects_failed_semantic_and_reader_pull_checks():
    candidate = valid_candidate()
    candidate["checks"]["source_supported"] = False
    with pytest.raises(ValueError, match="source_supported"):
        validate_publication_candidate(
            candidate,
            source_chapters={2: "Claire saves every bank statement."},
            chapter_one_prefix="Unrelated opening.",
            ending_spoilers=[],
        )

    candidate = valid_candidate()
    candidate["reader_pull"]["checks"]["open_loop"] = False
    with pytest.raises(ValueError, match="open_loop"):
        validate_publication_candidate(
            candidate,
            source_chapters={2: "Claire saves every bank statement."},
            chapter_one_prefix="Unrelated opening.",
            ending_spoilers=[],
        )
