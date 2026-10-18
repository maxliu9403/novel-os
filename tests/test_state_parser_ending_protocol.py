"""Ending declarations must survive parsing without being inferred from prose."""

import pytest

from state_manager import Character, StoryState
from state_parser import EndingProtocolError, ingest_agent_output, parse_continuity, parse_scribe


def test_misplaced_explicit_events_reach_the_durable_ending_fields(tmp_path):
    state = StoryState(str(tmp_path / "project"))
    state.add_character(Character(id="L01", full_name="Marian Reed", role="supporting"))
    output = """[CONTINUITY_STATE_UPDATE]
New_Facts_Established:
  - The original title remains in the owner's name.
  - Payoff_Events: payoff_008 | status=paid | evidence=The email is presented | chapter=13
  - Arc_State_Updates: L01 | stage=resolution | outcome=bounded_counsel_delivered | evidence=Counsel delivers the notices
  - Ending_Evidence: irreversible_change=The owner refuses the sale
[/CONTINUITY_STATE_UPDATE]"""

    ingest_agent_output(state, 13, "continuity_guardian", output)
    state.save_state()
    restored = StoryState(str(tmp_path / "project"))

    assert restored.chapters[13].payoff_events == [{
        "payoff_id": "payoff_008", "status": "paid", "chapter": 13,
        "evidence": "The email is presented",
    }]
    assert restored.characters["L01"].arc_stage == "resolution"
    assert restored.characters["L01"].outcome_state == "bounded_counsel_delivered"
    assert restored.chapters[13].ending_evidence == [
        "irreversible_change=The owner refuses the sale",
    ]
    assert parse_continuity(output)["new_facts_established"] == [
        "The original title remains in the owner's name.",
    ]


def test_repeated_field_prefix_is_not_part_of_character_identity():
    parsed = parse_scribe("""[SCRIBE_STATE_UPDATE]
Arc_State_Updates:
  - Arc_State_Updates: L01 | stage=resolution | evidence=Counsel delivers notices
[/SCRIBE_STATE_UPDATE]""")
    assert parsed["arc_state_updates"] == [
        "L01 | stage=resolution | evidence=Counsel delivers notices",
    ]


def test_empty_later_block_does_not_erase_events_from_report():
    parsed = parse_continuity("""[CONTINUITY_REPORT]
Payoff_Events:
  - payoff_004 | status=paid | evidence=The invoice is paid | chapter=8
Arc_State_Updates:
  - L01 | stage=resolution | evidence=Counsel delivers notices
Ending_Evidence:
  - irreversible_change=The choice persists
[/CONTINUITY_REPORT]
[CONTINUITY_STATE_UPDATE]
Payoff_Events: []
Arc_State_Updates: None
Ending_Evidence: []
[/CONTINUITY_STATE_UPDATE]""")
    assert len(parsed["payoff_events"]) == 1
    assert len(parsed["arc_state_updates"]) == 1
    assert len(parsed["ending_evidence"]) == 1


def test_multiple_declarations_and_blocks_preserve_distinct_evidence_in_order():
    paid = "payoff_004 | status=paid | evidence=The invoice is paid | chapter=8"
    recalled = "payoff_004 | status=recalled | evidence=The receipt is recalled | chapter=9"
    parsed = parse_continuity(f"""[CONTINUITY_REPORT]
Payoff_Events: {paid}
Payoff_Events: {recalled}
[/CONTINUITY_REPORT]
[CONTINUITY_STATE_UPDATE]
Payoff_Events:
  - {paid}
[/CONTINUITY_STATE_UPDATE]""")
    assert parsed["payoff_events"] == [paid, recalled]


def test_explicit_standalone_bullets_and_legacy_aliases_are_supported():
    parsed = parse_scribe("""[SCRIBE_STATE_UPDATE]
  - Payoff_Events: id=payoff_004 | status=paid | note=The invoice is paid
  - Arc_State_Updates: Marian Reed | arc_stage=resolution | arc_progress=100 | outcome_state=bounded_counsel_delivered | choice=Delivers notices
  - Ending_Evidence: emotional_payoff=She grieves without reversing her choice
Characters_Present: None
[/SCRIBE_STATE_UPDATE]""")
    assert len(parsed["payoff_events"]) == 1
    assert parsed["arc_state_updates"][0].startswith("Marian Reed |")
    assert len(parsed["ending_evidence"]) == 1
    assert parsed["characters_present"] == "None"


def test_unbulleted_ending_record_is_parsed_but_unstructured_declaration_is_rejected():
    parsed = parse_scribe("""[SCRIBE_STATE_UPDATE]
Payoff_Events:
payoff_004 | status=paid | evidence=The invoice is paid
[/SCRIBE_STATE_UPDATE]""")
    assert parsed["payoff_events"] == [
        "payoff_004 | status=paid | evidence=The invoice is paid",
    ]
    with pytest.raises(EndingProtocolError, match="missing entity or structured fields"):
        parse_scribe("""[SCRIBE_STATE_UPDATE]
Payoff_Events:
The invoice has been paid.
[/SCRIBE_STATE_UPDATE]""")


def test_prose_and_reference_notes_never_certify_payoff_or_arc(tmp_path):
    state = StoryState(str(tmp_path / "project"))
    state.add_character(Character(id="L01", full_name="Marian Reed", role="supporting"))
    output = """The report says payoff_004 is paid.
[CONTINUITY_STATE_UPDATE]
New_Facts_Established:
  - payoff_004 appears in a receipt and may be paid.
  - The phrase Payoff_Events: payoff_004 | status=paid is discussed, not declared.
Character_References:
  - L01 | note=Arc_State_Updates: L01 | stage=resolution | outcome=bounded_counsel_delivered | evidence=Delivers notices
[/CONTINUITY_STATE_UPDATE]"""
    ingest_agent_output(state, 15, "continuity_guardian", output)
    assert state.characters["L01"].arc_stage == "beginning"
    assert not state.characters["L01"].outcome_state
    assert not state.chapters[15].payoff_events
    assert not state.chapters[15].arc_state_updates


@pytest.mark.parametrize("declaration", [
    "payoff_007 | status=paid | evidence=The gift is canceled | chapter=12",
    "E01 | stage=climax | outcome=boundaries | evidence=Refuses the sale",
])
def test_unlabelled_structured_fact_requests_an_explicit_field(declaration):
    output = f"""[CONTINUITY_STATE_UPDATE]
New_Facts_Established:
  - {declaration}
[/CONTINUITY_STATE_UPDATE]"""
    with pytest.raises(EndingProtocolError, match="requires an explicit"):
        parse_continuity(output)


@pytest.mark.parametrize(("field", "declaration"), [
    ("Payoff_Events", "payoff_004 | status=paid"),
    ("Payoff_Events", "payoff_004 | status=complete | evidence=Paid invoice"),
    ("Payoff_Events", "payoff_004 | statuz=paid | evidence=Paid invoice"),
    ("Payoff_Events", "payoff_004 | status=paid | evidence=None"),
    ("Payoff_Events", "payoff_004 | status=paid | evidence=Paid invoice | chapter=later"),
    ("Payoff_Events", "payoff_004 | status=paid | status=blocked | evidence=Paid invoice"),
    ("Payoff_Events", "payoff_004 | id=payoff_005 | status=paid | evidence=Paid invoice"),
    ("Arc_State_Updates", "L01 | stage=finished | evidence=Delivers notices"),
    ("Arc_State_Updates", "L01 | stage=resolution | arc_stage=beginning | evidence=Delivers notices"),
    ("Arc_State_Updates", "L01 | progress=101 | evidence=Delivers notices"),
    ("Arc_State_Updates", "L01 | stage=resolution | outcome=bounded_counsel_delivered"),
    ("Arc_State_Updates", "L01 | evidence=Delivers notices"),
])
def test_invalid_explicit_declarations_fail_before_any_state_mutation(tmp_path, field, declaration):
    state = StoryState(str(tmp_path / "project"))
    state.add_character(Character(id="L01", full_name="Marian Reed", role="supporting"))
    output = f"""[SCRIBE_STATE_UPDATE]
Characters_Present:
  - L01
{field}:
  - {declaration}
[/SCRIBE_STATE_UPDATE]"""
    with pytest.raises(EndingProtocolError, match="ending_protocol"):
        ingest_agent_output(state, 15, "scribe", output)
    assert 15 not in state.chapters
    assert state.characters["L01"].arc_stage == "beginning"
