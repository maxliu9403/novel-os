from __future__ import annotations

from copy import deepcopy
import json
from typing import Any


def commercial_story_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "reader_contract": {
            "audience_age_band": "35-60",
            "life_contexts": ["midlife work", "family responsibility"],
            "emotional_jobs": {
                "recognition": 5,
                "anger": 4,
                "pity": 2,
                "regret": 3,
                "belonging": 4,
                "agency": 5,
                "hope": 3,
            },
        },
        "premise_engine": {
            "protagonist_life_stage": "career_midpoint",
            "protagonist_desire_beyond_escape": "Own the work carrying her name",
            "invisible_labor": "professional_ghostwork",
            "sacred_asset": "professional_identity",
            "boundary_transfer": "name",
            "beneficiary_role": "colleague",
            "proof_type": "technical_metadata",
            "deadline_type": "public_event",
            "agency_source": "process_knowledge",
            "agency_seeded_in_chapter": 1,
            "action_cost": "career_access",
            "belonging_anchors": ["self", "child", "work"],
            "relationship_shape": "spouse_triangle",
        },
        "conflict_ladder": [
            {
                "level": level,
                "resource_dimension": dimension,
                "protagonist_action": action,
                "observable_consequence": consequence,
            }
            for level, dimension, action, consequence in (
                (1, "name", "She checks the omitted credit", "The launch copy names someone else"),
                (2, "labor", "She asks who approved it", "Her husband calls the omission harmless"),
                (3, "memory", "She audits version history", "A repeated transfer becomes visible"),
                (4, "system", "She withdraws approval", "The release loses its compliance signoff"),
                (5, "future", "She files her authorship record", "Her marriage and job access fracture"),
            )
        ],
        "free_trial_arc": {
            "chapter_count": 3,
            "recognition_event": "Her name is removed at launch",
            "pattern_proof": "Version history shows years of erased work",
            "first_boundary_test": "She refuses to approve the release",
            "local_payoff": "The launch pauses for her missing signoff",
            "irreversible_choice": "She files the original records",
            "visible_cost": "She loses access to the company lab",
            "next_concrete_expectation": "She must prove ownership before the board vote",
            "action_sequence": ["verify", "test_boundary", "withdraw", "accept_cost"],
        },
        "quality_budgets": {
            "consecutive_humiliation_scenes_max": 2,
            "identical_hook_type_max": 1,
            "unseeded_rescue_max": 0,
            "child_voice_age_check": True,
            "institutional_plausibility_check": True,
            "protagonist_causes_major_turn": True,
        },
    }


def commercial_story_fixture():
    from commercial_story import CommercialStoryContract

    return CommercialStoryContract.from_dict(commercial_story_payload())


def commercial_story_fixture_variant():
    from commercial_story import CommercialStoryContract

    payload = deepcopy(commercial_story_payload())
    premise = payload["premise_engine"]
    premise.update(
        {
            "protagonist_life_stage": "empty_nest",
            "invisible_labor": "community_service",
            "sacred_asset": "community_role",
            "boundary_transfer": "money",
            "beneficiary_role": "community_insider",
            "proof_type": "financial_record",
            "deadline_type": "financial_close",
            "agency_source": "community_role",
            "action_cost": "community_belonging",
            "belonging_anchors": ["self", "friend", "community"],
            "relationship_shape": "community_betrayal",
        }
    )
    payload["free_trial_arc"]["action_sequence"] = [
        "document",
        "protect",
        "disclose",
        "counter_move",
    ]
    return CommercialStoryContract.from_dict(payload)


def high_overlap_candidate():
    """A candidate just over the configured structural block threshold."""
    return commercial_story_fixture()


def high_overlap_reference():
    """Nine matching dimensions plus a three-of-four ordered action overlap."""
    from commercial_story import CommercialStoryContract

    payload = deepcopy(commercial_story_payload())
    premise = payload["premise_engine"]
    premise["beneficiary_role"] = "employer"
    premise["belonging_anchors"] = ["self", "child", "friend"]
    premise["protagonist_desire_beyond_escape"] = (
        "Keep control of the work that secures her family's future"
    )
    payload["free_trial_arc"].update(
        {
            "recognition_event": "A promised credit disappears before release",
            "pattern_proof": "An ordinary audit exposes repeated transfers",
            "first_boundary_test": "She withholds a required approval",
            "local_payoff": "The release pauses under ordinary process rules",
            "irreversible_choice": "She submits a formal ownership record",
            "visible_cost": "Her employer removes her system access",
            "next_concrete_expectation": "She must establish ownership at review",
            "action_sequence": [
                "verify",
                "test_boundary",
                "withdraw",
                "counter_move",
            ],
        }
    )
    return CommercialStoryContract.from_dict(payload)


def legacy_chapter_contract_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "chapter": 1,
        "goal": "Megan verifies who removed her credit.",
        "obstacle": "The release record is controlled by her colleague.",
        "active_choice": "She checks the approval history herself.",
        "cost": "Her access is questioned.",
        "irreversible_change": "She preserves the first authorship record.",
        "local_payoff": "The missing approval becomes visible.",
        "ending_pressure": "The launch proceeds in the morning.",
        "preserve_facts": ["Megan built the original control system"],
        "allowed_knowledge": ["Megan knows her own approval process"],
        "world_event_ids": [],
    }


def chapter_contract_v2_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        **legacy_chapter_contract_payload(),
        "schema_version": 2,
        "reader_jobs": ["recognition", "anger"],
        "belonging_anchors": [],
        "resource_dimension": "name",
        "resource_change": "Megan withholds the approval tied to her work.",
        "satisfaction_type": "boundary",
        "hook_type": "consequence",
        "humiliation_scene": False,
        "protagonist_causes_turn": True,
        "seeded_resource_ids": ["license_record"],
        "used_resource_ids": ["license_record"],
    }
    payload.update(overrides)
    return payload


def chapter_contract_v2(**overrides: Any):
    from contracts import ChapterContract

    return ChapterContract.from_dict(chapter_contract_v2_payload(**overrides))


def architect_foundation_text(
    commercial_story_contract: dict[str, Any], chapter_count: int = 3
) -> str:
    from commercial_story import CommercialStoryContract

    contract = CommercialStoryContract.from_dict(commercial_story_contract)
    foundation = {
        "title": "Built From Her Records",
        "premise": "Megan must reclaim authorship before a public launch.",
        "themes": ["agency", "belonging"],
        "setting": {
            "time_period": "present",
            "primary_location": "a fictional industrial city",
            "world_rules": ["Professional records have ordinary legal limits"],
        },
        "characters": [
            {
                "id": "char_001",
                "name": "Megan Hale",
                "role": "protagonist",
                "external_goal": "Restore her authorship",
            }
        ],
        "plot_threads": [
            {
                "id": "plot_001",
                "name": "Authorship",
                "description": "Megan proves who built the system.",
                "type": "main",
                "priority": 5,
            }
        ],
        "style": {
            "tone": "intimate",
            "pov": "third_limited",
            "tense": "past",
            "prose_style": "balanced",
        },
        "commercial_story_contract": contract.to_dict(),
        "commercial_story_contract_id": contract.contract_id,
        "chapters": [
            {
                "number": number,
                "title": f"Chapter {number}",
                "pov": "Megan Hale",
                "summary": f"Megan changes the authorship conflict in chapter {number}.",
                "target_words": 1200,
            }
            for number in range(1, chapter_count + 1)
        ],
    }
    return (
        "[STORY_FOUNDATION_JSON]\n"
        + json.dumps(foundation, ensure_ascii=False, indent=2)
        + "\n[/STORY_FOUNDATION_JSON]\n\n# Blueprint\n"
    )
