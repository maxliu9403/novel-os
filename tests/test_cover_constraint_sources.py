from __future__ import annotations

import json

import pytest

from core.cover_constraint_sources import (
    assert_constraints_unchanged,
    constraint_source_evidence,
    extract_cover_constraints,
    resolve_constraint_sources,
)
from core.cover_models_v2 import CoverConstraints
from core.cover_sources import CoverSourceContext, CoverSourceSelector, capture_source
from tests.test_cover_models_v2 import two_character_fixture


def _prompt(*, refs=None, node_anchor=True, must_show=None) -> str:
    handoff = two_character_fixture()
    handoff["cover_constraints"] = {
        "constraint_version": "cover-constraints.v1",
        "must_preserve_story_facts": ["estranged spouses"],
        "must_show_if_supported": ["door_choice"] if must_show is None else must_show,
        "must_not_show": ["final reconciliation"],
        "spoiler_ceiling": "Do not reveal the final relationship outcome.",
        "user_visual_preferences": [],
        "preferred_story_node_ids": ["door_choice"],
        "preferred_character_ids": ["char_mara"],
        "source_refs": refs or ["section:6.relationship_map", "section:9.node:door_choice"],
    }
    anchor = "node_id: door_choice\n" if node_anchor else "door_choice is merely mentioned here\n"
    return (
        "# The Door Is Mine\n\n"
        "## 6. 人物关系及变化\nMara and Oren are estranged spouses.\n\n"
        "## 9. 全书／分卷／系列结构\n### Door decision\n" + anchor + "\n"
        "## 15. 封面交接与交付要求\n\nCOVER_HANDOFF_BEGIN\n```json\n"
        + json.dumps(handoff, ensure_ascii=False, indent=2)
        + "\n```\nCOVER_HANDOFF_END\n"
    )


@pytest.fixture
def snapshot(tmp_path):
    project = tmp_path / "project"
    (project / "outputs/input").mkdir(parents=True)
    prompt = _prompt()
    (project / "outputs/input/prompt.md").write_text(prompt, encoding="utf-8")
    context = CoverSourceContext(tmp_path / "covers", "owner:book", lambda _: project)
    return context, capture_source(context, CoverSourceSelector("project_input", project_id="book"))


def test_resolves_constraints_only_against_real_snapshot_sections(snapshot):
    _, frozen = snapshot

    constraints = extract_cover_constraints(frozen)
    sources = resolve_constraint_sources(frozen, constraints)

    assert constraints is not None
    assert [item.reference for item in sources] == [
        "section:6.relationship_map", "section:9.node:door_choice"
    ]
    assert all(item.source_sha256 == frozen.source_content_sha256 for item in sources)
    evidence = constraint_source_evidence(frozen, constraints)
    assert evidence[1]["section_number"] == 9


@pytest.mark.parametrize(
    "refs, node_anchor, message",
    [
        (["section:99.nonexistent"], True, "does not exist"),
        (["section:6.no_such_heading"], True, "does not match"),
        (["section:9.node:door_choice"], False, "node is not defined"),
        (["section:9.node:section:99"], True, "node is not defined"),
    ],
)
def test_invalid_refs_are_rejected_and_self_mentions_do_not_prove_themselves(
    tmp_path, refs, node_anchor, message
):
    project = tmp_path / "project"
    (project / "outputs/input").mkdir(parents=True)
    prompt = _prompt(refs=refs, node_anchor=node_anchor)
    (project / "outputs/input/prompt.md").write_text(prompt, encoding="utf-8")
    context = CoverSourceContext(tmp_path / "covers", "owner:book", lambda _: project)
    frozen = capture_source(context, CoverSourceSelector("project_input", project_id="book"))

    with pytest.raises(ValueError, match=message):
        extract_cover_constraints(frozen)


def test_model_cannot_add_or_delete_user_constraints(snapshot):
    _, frozen = snapshot
    source = extract_cover_constraints(frozen)
    assert source is not None
    assert_constraints_unchanged(frozen, source)

    mutated = CoverConstraints(
        **{**source.to_dict(), "must_not_show": ("final reconciliation", "new spoiler")}
    )
    with pytest.raises(ValueError, match="exactly preserve"):
        assert_constraints_unchanged(frozen, mutated)


def test_unconstrained_legacy_handoff_does_not_gain_model_constraints(tmp_path):
    project = tmp_path / "project"
    (project / "outputs/input").mkdir(parents=True)
    (project / "outputs/input/prompt.md").write_text(
        "COVER_HANDOFF_BEGIN\n```json\n" + json.dumps(two_character_fixture())
        + "\n```\nCOVER_HANDOFF_END\n", encoding="utf-8"
    )
    context = CoverSourceContext(tmp_path / "covers", "owner:legacy", lambda _: project)
    frozen = capture_source(context, CoverSourceSelector("project_input", project_id="book"))

    assert extract_cover_constraints(frozen) is None


def test_partial_handoff_markers_are_not_silently_treated_as_legacy(tmp_path):
    project = tmp_path / "project"
    (project / "outputs/input").mkdir(parents=True)
    (project / "outputs/input/prompt.md").write_text(
        "# story\n\nCOVER_HANDOFF_BEGIN\n", encoding="utf-8"
    )
    context = CoverSourceContext(tmp_path / "covers", "owner:partial", lambda _: project)
    frozen = capture_source(context, CoverSourceSelector("project_input", project_id="book"))

    with pytest.raises(ValueError, match="COVER_HANDOFF_END"):
        extract_cover_constraints(frozen)


def test_duplicate_constraint_source_refs_are_rejected(tmp_path):
    project = tmp_path / "project"
    (project / "outputs/input").mkdir(parents=True)
    prompt = _prompt(refs=["section:6.relationship_map", "section:6.relationship_map"])
    (project / "outputs/input/prompt.md").write_text(prompt, encoding="utf-8")
    context = CoverSourceContext(tmp_path / "covers", "owner:duplicate", lambda _: project)
    frozen = capture_source(context, CoverSourceSelector("project_input", project_id="book"))

    with pytest.raises(ValueError, match="must be unique"):
        extract_cover_constraints(frozen)


def test_full_analysis_preserves_frozen_constraints_into_derived_context(snapshot):
    """The synthesis may copy the contract, but cannot author or mutate it."""

    from core.cover_analysis import CoverStoryAnalyzer, derive_analysis_context
    from tests.test_cover_analysis_snapshot import ModelFixture

    context, frozen = snapshot
    source = extract_cover_constraints(frozen)
    assert source is not None
    model = ModelFixture()

    def copy_contract(request, result):
        if request["phase"] == "global":
            result["brief"]["cover_constraints"] = source.to_dict()
        return result

    model.mutate = copy_contract
    analysis = CoverStoryAnalyzer(
        complete=model,
        model="constraint-fixture",
        max_repair_attempts=0,
    ).analyze(context, frozen)

    brief, ledger = derive_analysis_context(context, analysis)
    assert brief.cover_constraints == source
    assert any(item.source_type == "constraint_source" for item in ledger.items)
    assert all("verified_constraint_source" in item.visual_tags
               for item in ledger.items if item.source_type == "constraint_source")


def test_full_analysis_restores_constraints_when_synthesis_drops_them(snapshot):
    from core.cover_analysis import CoverAnalysisError, CoverStoryAnalyzer
    from tests.test_cover_analysis_snapshot import ModelFixture

    context, frozen = snapshot
    model = ModelFixture()

    analysis = CoverStoryAnalyzer(
        complete=model,
        model="constraint-drop-fixture",
        max_repair_attempts=0,
    ).analyze(context, frozen)
    assert analysis.brief_payload["cover_constraints"] == extract_cover_constraints(frozen).to_dict()


@pytest.mark.parametrize("case", ["supported", "absent_cast", "different_character", "unknown_evidence"])
def test_requested_character_resolves_through_frozen_analysis_evidence(tmp_path, case):
    from dataclasses import replace
    from core.cover_analysis import CoverStoryAnalyzer, derive_analysis_context
    from core.cover_director import CoverArtDirector
    from core.cover_validator import validate_direction
    from tests.test_cover_workflow import FullStoryFixture

    project = tmp_path / "project"
    (project / "outputs/input").mkdir(parents=True)
    (project / "outputs/input/prompt.md").write_text(_prompt(must_show=["char_mara"]), encoding="utf-8")
    context = CoverSourceContext(tmp_path / "covers", "owner:character-proof", lambda _: project)
    frozen = capture_source(context, CoverSourceSelector("project_input", project_id="book"))
    director = FullStoryFixture()
    analysis = CoverStoryAnalyzer(
        complete=director._complete, model=director.model, max_repair_attempts=0,
    ).analyze(context, frozen)
    brief, ledger = derive_analysis_context(context, analysis)
    facts = analysis.to_dict()
    direction = director.plan(brief, evidence_ledger=ledger, story_analysis=facts)
    if case == "absent_cast":
        direction = replace(direction, plans=tuple(
            replace(plan, cast=("char_oren",)) for plan in direction.plans
        ))
    elif case == "different_character":
        facts["characters"] = [item for item in facts["characters"] if item["character_id"] != "char_mara"]
    elif case == "unknown_evidence":
        for item in facts["characters"]:
            item["evidence_refs"] = ["evidence:not-in-the-frozen-ledger"]
        direction = replace(direction, plans=tuple(
            replace(plan, story_evidence_refs=("evidence:not-in-the-frozen-ledger",))
            for plan in direction.plans
        ))

    findings = validate_direction(
        brief, direction, story_analysis=facts,
        verified_constraint_source_refs=CoverArtDirector._verified_constraint_source_refs(ledger),
    )

    assert ("requested_story_evidence_missing" in {item.code for item in findings}) is (case != "supported")


def _remove_persisted_constraint_fields(context, analysis, *, remove_brief=False):
    from core.cover_analysis import canonical_hash

    path = context.cover_root / "analyses" / analysis.analysis_id / "analysis.json"
    payload = json.loads(path.read_text())
    payload.pop("source_constraints_payload", None)
    payload.pop("source_constraint_sources", None)
    if remove_brief:
        payload["brief_payload"].pop("cover_constraints", None)
    payload.pop("analysis_sha256")
    payload["analysis_sha256"] = canonical_hash(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_cached_legacy_analysis_migrates_constraints_without_reextracting(snapshot):
    from core.cover_analysis import CoverStoryAnalyzer
    from tests.test_cover_analysis_snapshot import ModelFixture

    context, frozen = snapshot
    model = ModelFixture()
    engine = CoverStoryAnalyzer(complete=model, model="legacy-cache", max_repair_attempts=0)
    original = engine.analyze(context, frozen)
    _remove_persisted_constraint_fields(context, original, remove_brief=True)
    model.calls.clear()

    repaired = engine.analyze(context, frozen)

    assert repaired.analysis_id == original.analysis_id
    assert repaired.source_constraints_payload == extract_cover_constraints(frozen).to_dict()
    assert repaired.brief_payload["cover_constraints"] == repaired.source_constraints_payload
    assert {call["phase"] for call in model.calls} == {"global", "verify"}
    assert repaired.segment_results == original.segment_results


@pytest.mark.parametrize("with_constraints", [True, False])
def test_legacy_completed_analysis_cannot_be_rebound_to_another_model(tmp_path, with_constraints):
    from core.cover_analysis import CoverAnalysisError, CoverStoryAnalyzer
    from tests.test_cover_analysis_snapshot import ModelFixture

    project = tmp_path / "project"
    (project / "outputs/input").mkdir(parents=True)
    prompt = _prompt() if with_constraints else (
        "# The Door Is Mine\n\nCOVER_HANDOFF_BEGIN\n```json\n"
        + json.dumps(two_character_fixture()) + "\n```\nCOVER_HANDOFF_END\n"
    )
    (project / "outputs/input/prompt.md").write_text(prompt, encoding="utf-8")
    context = CoverSourceContext(tmp_path / "covers", "owner:legacy-config", lambda _: project)
    frozen = capture_source(context, CoverSourceSelector("project_input", project_id="book"))
    model = ModelFixture()
    original = CoverStoryAnalyzer(
        complete=model, model="original-model", max_repair_attempts=0,
    ).analyze(context, frozen)
    _remove_persisted_constraint_fields(context, original)
    model.calls.clear()

    with pytest.raises(CoverAnalysisError) as error:
        CoverStoryAnalyzer(complete=model, model="different-model", max_repair_attempts=0).analyze(
            context, frozen, analysis_id=original.analysis_id,
        )

    assert error.value.code == "analysis_conflict"
    assert model.calls == []
