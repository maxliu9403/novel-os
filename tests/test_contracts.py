"""Tests for immutable, versioned story quality contracts."""

import json
from dataclasses import FrozenInstanceError

import pytest

from contracts import AuthorIntent, ChapterContract, StoryContract
from state_manager import ChapterState


def _chapter_contract(**overrides):
    values = {
        "chapter": 4,
        "goal": "Recover the ledger",
        "obstacle": "The archive is flooding",
        "active_choice": "Mara returns for the missing page",
        "cost": "She loses her escape route",
        "irreversible_change": "The forgery becomes public",
        "local_payoff": "The workers see who altered the vote",
        "ending_pressure": "Security seals the building",
    }
    values.update(overrides)
    return ChapterContract(**values)


INVALID_SCHEMA_VERSIONS = [
    pytest.param("1", id="string"),
    pytest.param(True, id="bool"),
    pytest.param(0, id="zero"),
    pytest.param(2, id="future"),
    pytest.param(object(), id="object"),
]


def _construct_with_schema_version(contract_type, schema_version):
    if contract_type is AuthorIntent:
        return AuthorIntent("Premise", "Adults", schema_version=schema_version)
    if contract_type is StoryContract:
        return StoryContract(
            "Title",
            "Thriller",
            AuthorIntent("Premise", "Adults"),
            schema_version=schema_version,
        )
    return _chapter_contract(schema_version=schema_version)


def test_author_intent_round_trips_and_serializes_schema_version():
    intent = AuthorIntent(
        premise="  A cooperative exposes a rigged acquisition.  ",
        target_audience="  adult thriller readers ",
        content_boundaries=[" no graphic torture ", " no sexual violence "],
    )

    payload = json.loads(json.dumps(intent.to_dict()))
    restored = AuthorIntent.from_dict(payload)

    assert restored == intent
    assert restored.premise == "A cooperative exposes a rigged acquisition."
    assert restored.content_boundaries == (
        "no graphic torture",
        "no sexual violence",
    )
    assert payload["schema_version"] == 1


def test_story_contract_round_trips_nested_intent_and_is_immutable():
    story = StoryContract(
        title="  The Last Ballot ",
        genre=" thriller ",
        intent=AuthorIntent(" Workers uncover fraud ", " Adult readers "),
        themes=[" solidarity ", " institutional memory "],
        non_negotiables=[" Mara makes the final choice "],
    )

    restored = StoryContract.from_dict(json.loads(json.dumps(story.to_dict())))

    assert restored == story
    assert restored.title == "The Last Ballot"
    assert restored.intent.premise == "Workers uncover fraud"
    assert restored.themes == ("solidarity", "institutional memory")
    assert restored.schema_version == 1
    with pytest.raises(FrozenInstanceError):
        story.title = "A Different Title"


def test_chapter_contract_round_trips_sequences_from_json_lists():
    contract = _chapter_contract(
        preserve_facts=["  Mara cannot swim  ", "The ledger is watermarked"],
        allowed_knowledge=[" Mara knows the west stairwell "],
        world_event_ids=[" flood-warning ", " archive-lockdown "],
    )

    restored = ChapterContract.from_dict(
        json.loads(json.dumps(contract.to_dict()))
    )

    assert restored == contract
    assert restored.preserve_facts == (
        "Mara cannot swim",
        "The ledger is watermarked",
    )
    assert restored.allowed_knowledge == ("Mara knows the west stairwell",)
    assert restored.world_event_ids == ("flood-warning", "archive-lockdown")
    assert restored.schema_version == 1


def test_contract_ids_are_stable_for_semantically_equal_values():
    first = StoryContract(
        title="The Last Ballot",
        genre="Thriller",
        intent=AuthorIntent("Workers uncover fraud", "Adults"),
        themes=["Solidarity"],
    )
    same = StoryContract(
        title=" The Last Ballot ",
        genre=" Thriller ",
        intent=AuthorIntent(" Workers uncover fraud ", " Adults "),
        themes=(" Solidarity ",),
    )

    assert first.contract_id == same.contract_id
    assert first.contract_id.startswith("contract:")
    assert len(first.contract_id) == len("contract:") + 64


def test_contract_ids_change_when_contract_content_changes():
    first = _chapter_contract()
    changed = _chapter_contract(cost="Mara sacrifices the original ledger")

    assert first.contract_id != changed.contract_id


def test_blank_active_choice_is_rejected_with_field_name():
    with pytest.raises(ValueError, match="active_choice"):
        _chapter_contract(active_choice="  ")


@pytest.mark.parametrize("chapter", [0, -1])
def test_non_positive_chapter_is_rejected(chapter):
    with pytest.raises(ValueError, match="chapter"):
        _chapter_contract(chapter=chapter)


def test_blank_sequence_items_are_filtered():
    story = StoryContract(
        title="The Last Ballot",
        genre="Thriller",
        intent=AuthorIntent("Workers uncover fraud", "Adults"),
        themes=["solidarity", "  ", " institutional memory "],
    )

    assert story.themes == ("solidarity", "institutional memory")


@pytest.mark.parametrize(
    "values",
    [
        pytest.param({"solidarity", "memory"}, id="set"),
        pytest.param(frozenset({"solidarity", "memory"}), id="frozenset"),
        pytest.param({"solidarity": True}, id="dict"),
        pytest.param((value for value in ["solidarity"]), id="generator"),
    ],
)
def test_sequence_fields_reject_non_sequence_iterables(values):
    with pytest.raises(ValueError, match="themes"):
        StoryContract(
            title="The Last Ballot",
            genre="Thriller",
            intent=AuthorIntent("Workers uncover fraud", "Adults"),
            themes=values,
        )


@pytest.mark.parametrize(
    "contract_type",
    [AuthorIntent, StoryContract, ChapterContract],
)
@pytest.mark.parametrize("schema_version", INVALID_SCHEMA_VERSIONS)
def test_direct_construction_rejects_unsupported_schema_versions(
    contract_type, schema_version
):
    with pytest.raises(ValueError, match="schema_version"):
        _construct_with_schema_version(contract_type, schema_version)


@pytest.mark.parametrize(
    "contract_type",
    [AuthorIntent, StoryContract, ChapterContract],
)
@pytest.mark.parametrize("schema_version", INVALID_SCHEMA_VERSIONS)
def test_from_dict_rejects_schema_version_before_parsing_other_fields(
    contract_type, schema_version
):
    with pytest.raises(ValueError, match="schema_version"):
        contract_type.from_dict({"schema_version": schema_version})


def test_legacy_chapter_state_loads_with_empty_contract_references():
    legacy = ChapterState(number=3, title="The Vote").to_dict()
    legacy.pop("contract_id", None)
    legacy.pop("canonical_revision_id", None)
    legacy.pop("last_evaluation_id", None)

    restored = ChapterState.from_dict(legacy)

    assert restored.number == 3
    assert restored.title == "The Vote"
    assert restored.contract_id == ""
    assert restored.canonical_revision_id == ""
    assert restored.last_evaluation_id == ""
    assert restored.to_dict()["contract_id"] == ""
