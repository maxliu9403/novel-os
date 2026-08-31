from __future__ import annotations

from copy import deepcopy
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
