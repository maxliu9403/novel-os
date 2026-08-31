from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from typing import Any


def span(text: str, quote: str) -> dict[str, Any]:
    start = text.index(quote)
    return {
        "status": "present",
        "quote": quote,
        "start": start,
        "end": start + len(quote),
    }


def guardian_reader_value_payload(**overrides: Any) -> dict[str, Any]:
    text = "Megan signed the withdrawal. The lender froze the draw."
    default_span = span(text, "Megan signed the withdrawal.")
    payoff_span = span(text, "The lender froze the draw.")
    payload: dict[str, Any] = {
        "agency": default_span,
        "resource_change": default_span,
        "local_payoff": payoff_span,
        "ending_hook": payoff_span,
        "reader_jobs": {
            "recognition": default_span,
            "anger": default_span,
        },
        "belonging_anchors": {},
        "free_trial_beats": [],
        "child_voice": {"status": "not_applicable", "quote": None, "start": None, "end": None},
        "institutional_plausibility": default_span,
        "findings": [],
    }
    for key, value in overrides.items():
        if key.endswith("_span"):
            target = {"payoff": "local_payoff", "hook": "ending_hook"}.get(
                key[:-5], key[:-5]
            )
            payload[target] = value
        else:
            payload[key] = value
    return payload


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


def verified_reader_value_update(**overrides: Any) -> dict[str, Any]:
    update = {
        "report_id": "commercial-chapter-report:" + "c" * 64,
        "candidate_sha256": "a" * 64,
        "reader_jobs": ["recognition", "anger"],
        "belonging_anchors": [],
        "resource_dimension": "name",
        "resource_change": "Megan withdraws permit approval",
        "satisfaction_type": "boundary",
        "hook_type": "consequence",
        "protagonist_caused_turn": True,
    }
    update.update(overrides)
    return update


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


def commercial_project_with_reports(
    tmp_path: Path,
    *,
    missing_chapter_3_payoff: bool = False,
    delivered_belonging: tuple[str, ...] = ("self", "work"),
) -> Path:
    """Create a twelve-chapter commercial project with real promotion receipts."""
    from artifacts import ArtifactStore
    from canon import CanonDeltaProposal
    from canon_ledger import canonical_canon_sha
    from commercial_quality import (
        free_trial_beats_for_chapter,
        review_commercial_chapter,
        write_commercial_report,
    )
    from commercial_story import CommercialStoryContract
    from contracts import ChapterContract
    from project_identity import ensure_project_instance_id
    from promotion import PromotionRequest, PromotionService
    from proposals import ProposalStore
    from quality import EvaluationRequest, QualityLab
    from state_manager import StoryState

    project = Path(tmp_path) / "commercial-project"
    project.mkdir(parents=True, exist_ok=True)
    payload = deepcopy(commercial_story_payload())
    payload["premise_engine"]["belonging_anchors"] = ["self", "work", "community"]
    story = CommercialStoryContract.from_dict(payload)
    input_dir = project / "outputs/input"
    input_dir.mkdir(parents=True, exist_ok=True)
    approved = {
        "commercial_story_contract": story.to_dict(),
        "commercial_story_contract_id": story.contract_id,
    }
    (input_dir / "brief.json").write_text(
        json.dumps(approved, sort_keys=True) + "\n", encoding="utf-8"
    )
    (input_dir / "foundation.json").write_text(
        json.dumps(approved, sort_keys=True) + "\n", encoding="utf-8"
    )

    state = StoryState(str(project))
    state.metadata["title"] = "Built From Her Records"
    state.story_bible["commercial_story_contract"] = story.to_dict()
    state.save_state()
    ensure_project_instance_id(project)

    artifacts = ArtifactStore(project)
    story_revision = artifacts.put_json(
        chapter=0,
        kind="story_contract",
        value={"commercial_story_contract_id": story.contract_id},
        source="intake",
    )
    artifacts.set_head(0, "story_contract", story_revision.revision_id, expected_revision_id=None)
    service = PromotionService(project)

    anchor_chapters = {
        anchor: 8 + index
        for index, anchor in enumerate(delivered_belonging)
    }
    for number in range(1, 13):
        chapter_anchors = tuple(
            anchor for anchor, chapter in anchor_chapters.items() if chapter == number
        )
        reader_jobs = (
            ("belonging", "agency") if chapter_anchors
            else ("recognition", "anger") if number == 1
            else ("anger", "agency")
        )
        contract_payload = chapter_contract_v2_payload(
            chapter=number,
            reader_jobs=reader_jobs,
            belonging_anchors=chapter_anchors,
            resource_dimension=("name", "labor", "memory", "system", "future")[
                (number - 1) % 5
            ],
            satisfaction_type=(
                "boundary", "evidence", "competence", "identity", "relationship", "consequence"
            )[(number - 1) % 6],
            hook_type=(
                "decision", "consequence", "evidence", "relationship_shift", "deadline", "arrival"
            )[(number - 1) % 6],
            humiliation_scene=number in {1, 2, 5},
            seeded_resource_ids=(f"resource_{number:02d}",),
            used_resource_ids=(f"resource_{number:02d}",),
        )
        contract = ChapterContract.from_dict(contract_payload)
        contract_revision = artifacts.put_json(
            chapter=number,
            kind="chapter_contract",
            value=contract.to_dict(),
            source="architect",
            story_contract_revision_id=story_revision.revision_id,
        )
        artifacts.set_head(
            number,
            "chapter_contract",
            contract_revision.revision_id,
            expected_revision_id=None,
        )

        text = (
            f"Chapter {number}. Megan verifies the record, acts on it, and changes "
            "the balance before accepting the visible cost."
        )
        candidate = artifacts.put_text(
            chapter=number,
            kind="final",
            text=text,
            source="style_curator",
            story_contract_revision_id=story_revision.revision_id,
            chapter_contract_revision_id=contract_revision.revision_id,
        )
        evidence_span = span(text, text)
        expected_beats = free_trial_beats_for_chapter(story, number)
        guardian_payload = {
            "agency": evidence_span,
            "resource_change": evidence_span,
            "local_payoff": evidence_span,
            "ending_hook": evidence_span,
            "reader_jobs": {job: evidence_span for job in contract.reader_jobs},
            "belonging_anchors": {
                anchor: evidence_span for anchor in contract.belonging_anchors
            },
            "free_trial_beats": {
                beat: evidence_span for beat in expected_beats
            } if expected_beats else [],
            "child_voice": {
                "status": "not_applicable", "quote": None, "start": None, "end": None
            },
            "institutional_plausibility": evidence_span,
            "findings": [],
        }
        report = review_commercial_chapter(
            text,
            contract,
            guardian_payload,
            (),
            story_contract_revision_id=story_revision.revision_id,
            chapter_contract_revision_id=contract_revision.revision_id,
            expected_free_trial_beats=expected_beats,
        )
        if missing_chapter_3_payoff and number == 3:
            report = replace(
                report,
                free_trial_beats=tuple(
                    beat for beat in report.free_trial_beats if beat != "local_payoff"
                ),
            )
        write_commercial_report(project, report)

        proposal = ProposalStore(project).save(
            CanonDeltaProposal(
                chapter=number,
                agent_name="style_curator",
                source_artifact_sha=candidate.sha256,
                delta={"key_events": [f"Megan changes the conflict in chapter {number}."]},
            )
        )
        evaluation = QualityLab.evaluate_deterministic(
            EvaluationRequest.for_text(
                number,
                text,
                artifact_revision_id=candidate.revision_id,
                story_contract_id=story_revision.revision_id,
                chapter_contract_id=contract_revision.revision_id,
            ),
            candidate_text=text,
            continuity_findings=(),
        )
        receipt = service.promote(
            PromotionRequest(
                project_id=project.name,
                project_instance_id=ensure_project_instance_id(project),
                chapter=number,
                candidate_revision_id=candidate.revision_id,
                candidate_sha256=candidate.sha256,
                evaluation_report=evaluation,
                canon_proposal_id=proposal.proposal_id,
                expected_final_revision_id=None,
                expected_final_sha256=None,
                base_canon_sha=canonical_canon_sha(StoryState(str(project))),
                story_contract_revision_id=story_revision.revision_id,
                chapter_contract_revision_id=contract_revision.revision_id,
                idempotency_key=f"fixture-commercial-chapter-{number}",
                actor="test",
                reason="commercial fixture",
                decision_metadata={
                    "commercial_report_id": report.report_id,
                    "commercial_report_artifact_sha256": report.artifact_sha256,
                },
            )
        )
        assert receipt.new_artifact_revision_id == candidate.revision_id
    return project
