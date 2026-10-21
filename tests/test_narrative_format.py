import pytest

from core.narrative_format import (
    NarrativeFormat,
    assess_long_form_suitability,
    bind_foundation_serialization,
    chapter_binding,
    infer_narrative_format,
    parse_narrative_format_block,
    serialization_payload,
    validate_serialization,
    volume_contract_template,
)
from core.novel_classification import infer_classification


def _classification(story: str = "werewolf"):
    return infer_classification(
        genre="Women's Fiction / Supernatural",
        premise=story,
        audience="women 45-60",
    )


def _filled_volumes(contract: NarrativeFormat):
    values = volume_contract_template(contract)
    for item in values:
        number = item["volume_number"]
        item.update({
            "title": f"Movement {number}",
            "central_conflict": f"Distinct pressure {number}",
            "volume_promise": f"Distinct reader promise {number}",
            "protagonist_shift": f"Durable protagonist shift {number}",
            "payoff": f"Observable payoff {number}",
        })
        if number < len(values):
            item["carryover_hook"] = f"Consequence opening movement {number + 1}"
    return values


def test_explicit_eighty_chapters_produces_four_confirmed_volume_spans():
    contract = infer_narrative_format(
        _classification(),
        chapters=80,
        target_words=72000,
        explicit_length=True,
    )

    assert contract.mode == "multi_volume"
    assert contract.confirmation_status == "confirmed"
    assert contract.selection_source == "prompt_explicit"
    assert [(v.chapter_start, v.chapter_end) for v in contract.volumes] == [
        (1, 20), (21, 40), (41, 60), (61, 80),
    ]
    assert chapter_binding(contract, 23)["volume_id"] == "volume_02"
    assert chapter_binding(contract, 23)["chapter_in_volume"] == 3


def test_absent_length_is_a_recommendation_pending_customer_confirmation():
    contract = infer_narrative_format(
        _classification("a family secret across multiple generations"),
        chapters=32,
        target_words=80000,
        premise="a family secret across multiple generations",
        explicit_length=False,
    )

    assert contract.selection_source == "engine_recommended"
    assert contract.confirmation_status == "pending_confirmation"
    assert contract.long_form_suitability.recommendation in {
        "flexible", "long_preferred",
    }


def test_long_form_score_is_advisory_and_records_extension_risk():
    domestic = infer_classification(
        genre="Women's Fiction",
        premise="One night reveals a marriage betrayal.",
        audience="women 45-60",
    )
    assessment = assess_long_form_suitability(
        domestic,
        chapters=80,
        premise="One night reveals a marriage betrayal.",
    )

    assert 0 <= assessment.score <= 100
    assert assessment.requested_fit in {
        "strong_fit", "supported_with_expansion", "high_extension_risk",
    }


def test_locked_format_block_requires_confirmation_and_preserves_author_choice():
    original = infer_narrative_format(
        _classification(),
        chapters=80,
        target_words=72000,
        explicit_length=True,
    ).with_confirmation("author_selected")
    prompt = (
        "[NARRATIVE_FORMAT_JSON]\n"
        + __import__("json").dumps(original.to_dict())
        + "\n[/NARRATIVE_FORMAT_JSON]"
    )

    parsed = parse_narrative_format_block(prompt)

    assert parsed == original
    assert parsed.selection_source == "author_selected"


def test_customer_can_choose_a_multi_volume_series_installment():
    contract = infer_narrative_format(
        _classification(),
        chapters=80,
        target_words=72000,
        explicit_length=True,
        source="author_selected",
        mode="series_installment",
        volume_count=4,
        series_id="mercer-family",
        series_title="The Mercer Family",
        series_book_number=2,
        planned_books=4,
    )

    assert contract.mode == "series_installment"
    assert len(contract.volumes) == 4
    assert chapter_binding(contract, 21)["series_id"] == "mercer-family"
    assert chapter_binding(contract, 21)["series_book_number"] == 2


def test_foundation_binding_attaches_every_chapter_to_a_volume():
    contract = infer_narrative_format(
        _classification(), chapters=80, target_words=72000, explicit_length=True
    )
    foundation = {
        "volume_contracts": _filled_volumes(contract),
        "chapters": [
            {"number": number, "title": f"Chapter {number}"}
            for number in range(1, 81)
        ],
    }

    bound = bind_foundation_serialization(foundation, contract)

    assert bound["chapters"][22]["volume_id"] == "volume_02"
    assert bound["chapters"][22]["chapter_in_volume"] == 3
    assert bound["chapters"][79]["volume_number"] == 4
    assert bound["volume_contracts"][0]["obligations"][0] == {
        "event_id": "volume_01_promise",
        "kind": "promise",
        "due_chapter": 1,
    }


def test_repeated_volume_engines_are_rejected():
    contract = infer_narrative_format(
        _classification(), chapters=80, target_words=72000, explicit_length=True
    )
    volumes = _filled_volumes(contract)
    volumes[1]["central_conflict"] = volumes[0]["central_conflict"]

    with pytest.raises(ValueError, match="repeat the same central_conflict"):
        bind_foundation_serialization(
            {"volume_contracts": volumes, "chapters": [{"number": i} for i in range(1, 81)]},
            contract,
        )


def test_serialization_id_binds_format_and_volume_contracts():
    contract = infer_narrative_format(
        _classification(), chapters=80, target_words=72000, explicit_length=True
    )
    payload = serialization_payload(contract, _filled_volumes(contract))

    assert validate_serialization(payload) == payload
    payload["volumes"][0]["title"] = "Changed"
    with pytest.raises(ValueError, match="serialization_id"):
        validate_serialization(payload)
