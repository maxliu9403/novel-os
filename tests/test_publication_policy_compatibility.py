"""Saved publication receipts keep the counting contract they passed."""

import json

import pytest

from core.publication_copy import (
    POLICY_VERSION,
    parse_publication_copy,
    validate_publication_candidate,
)
from tests.test_publication_copy import valid_candidate, valid_copy_payload


LEGACY_POLICIES = (
    "publication-copy-policy.v1",
    "publication-copy-policy.v2",
    "publication-copy-policy.v3",
)
DISPLAY_LANGUAGES = ("英文", "简体中文", "中文", "Chinese")
ENGLISH_HOOK = (
    "Claire confronts Ethan before his well-planned lies cost their daughter."
)
CHINESE_HOOK = "她带着女儿追查丈夫的谎言却发现家门已经换锁。"


def _legacy_payload(language, policy_version="publication-copy-policy.v3"):
    payload = valid_copy_payload()
    payload["language"] = language
    payload["validation"]["policy_version"] = policy_version
    if language == "英文":
        payload["hook_lead"] = ENGLISH_HOOK
        # The old Unicode-token policy split "well-planned" into two words.
        payload["validation"]["length"]["hook_lead"] = 11
    else:
        payload["hook_lead"] = (
            "她护住女儿，收起账本，推开门扉，离开婚房，"
            "面对威胁，寻找证据，守住工作，追问真相。"
        )
        payload["spoiler_free_blurb"] = "，".join(
            f"线索{number}" for number in range(120)
        ) + "。"
        # Historical display labels used one Unicode token per phrase.
        payload["validation"]["length"]["hook_lead"] = 8
    return payload


@pytest.mark.parametrize("policy_version", LEGACY_POLICIES)
@pytest.mark.parametrize("language", DISPLAY_LANGUAGES)
def test_saved_legacy_alias_receipt_round_trips_without_recounting(
    language, policy_version
):
    payload = _legacy_payload(language, policy_version)
    raw = json.dumps(payload, ensure_ascii=False)

    restored = parse_publication_copy(raw)

    assert restored.to_dict() == payload
    assert restored.validation.length_unit == "words"
    assert restored.language == language


@pytest.mark.parametrize("language", DISPLAY_LANGUAGES)
@pytest.mark.parametrize("field", ("hook_lead", "spoiler_free_blurb"))
def test_saved_legacy_alias_receipt_still_rejects_tampered_counts(language, field):
    payload = _legacy_payload(language)
    payload["validation"]["length"][field] += 1

    with pytest.raises(ValueError, match=rf"validation\.length\.{field} does not match"):
        parse_publication_copy(json.dumps(payload, ensure_ascii=False))


@pytest.mark.parametrize("language", DISPLAY_LANGUAGES)
def test_new_alias_validation_uses_v4_and_corrected_counts(language):
    candidate = valid_candidate(language=language, hook_lead=ENGLISH_HOOK)
    if language != "英文":
        candidate.update(
            hook_lead=CHINESE_HOOK,
            # Distinct characters isolate counting from the repetition gates.
            spoiler_free_blurb="".join(chr(0x4E00 + index) for index in range(240)) + "。",
        )

    validation = validate_publication_candidate(
        candidate,
        source_chapters={2: "Claire saves every bank statement."},
        chapter_one_prefix="A stranger waits outside the building.",
        ending_spoilers=(),
    )

    assert POLICY_VERSION == "publication-copy-policy.v4"
    assert validation.policy_version == POLICY_VERSION
    if language == "英文":
        assert (validation.length_unit, validation.hook_lead_length) == ("words", 10)
        assert validation.spoiler_free_blurb_length == 120
    else:
        assert (validation.length_unit, validation.hook_lead_length) == (
            "content_characters", 21,
        )
        assert validation.spoiler_free_blurb_length == 240

    payload = valid_copy_payload()
    payload.update({key: candidate[key] for key in (
        "language", "reader_heading", "hook_lead", "spoiler_free_blurb",
    )})
    payload["validation"] = validation.to_dict()
    assert parse_publication_copy(json.dumps(payload, ensure_ascii=False)).to_dict() == payload


@pytest.mark.parametrize("policy_version", ("publication-copy-policy.v0", "unknown"))
def test_saved_receipt_cannot_select_an_unknown_counting_policy(policy_version):
    payload = valid_copy_payload()
    payload["validation"]["policy_version"] = policy_version

    with pytest.raises(ValueError, match="policy"):
        parse_publication_copy(json.dumps(payload))
