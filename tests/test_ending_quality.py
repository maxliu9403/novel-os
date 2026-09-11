import json
from pathlib import Path

from ending_quality import evaluate_ending, ensure_quality_ledgers, load_ending_contract
from orchestrator import NovelOrchestrator
from pipeline_models import RunManifest, RunSpec
from pipeline_runner import PipelineRunner
from state_manager import Character, StoryState, initialize_project
from state_parser import ingest_agent_output


def _project(tmp_path: Path, *, chapters: int = 4, contract: dict | None = None) -> Path:
    project = tmp_path / "book"
    initialize_project(str(project), "Test Book", "romance")
    state = StoryState(str(project))
    state.set_metadata("target_chapters", chapters)
    state.set_metadata("target_word_count", chapters * 10)
    protagonist = state.create_chapter(1)
    protagonist.characters_present = ["Mara"]
    state.characters["char_001"] = Character(
        id="char_001", full_name="Mara", role="protagonist", arc_stage="resolution", arc_progress=100
    )
    state.plot_threads["plot_001"] = __import__("state_manager").PlotThread(
        id="plot_001", name="Main", description="Main conflict", thread_type="main", status="resolved"
    )
    for number in range(1, chapters + 1):
        chapter = state.get_chapter(number) or state.create_chapter(number)
        chapter.status = "complete"
        chapter.plot_advances = [f"Change {number}"]
        chapter.character_development = {"char_001": "resolution"}
        chapter.foreshadowing_planted = ["minor clue"] if number == 1 else []
        chapter.foreshadowing_resolved = ["minor clue"] if number == chapters else []
        chapter.foreshadowing_resolved_ids = ["ch1:fs1"] if number == chapters else []
        chapter.ending_evidence = ["irreversible_change: Mara chooses herself"] if number == chapters else []
        (project / "outputs/manuscript").mkdir(parents=True, exist_ok=True)
        (project / "outputs/manuscript" / f"chapter_{number:03d}_final.md").write_text(
            f"Chapter {number}\nMara changes the situation.", encoding="utf-8"
        )
    state.save_state()
    if contract is not None:
        path = project / "outputs/input/ending_contract.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
    return project


def _contract(*, main_status: str = "resolved", arc_state: str = "resolution") -> dict:
    return {
        "schema_version": 1,
        "enforce": True,
        "finale_window": {"start_chapter": 3, "end_chapter": 4},
        "main_conflict": {
            "thread_id": "plot_001",
            "required_status": main_status,
            "protagonist_choice": "Mara chooses herself",
            "consequence": "The old arrangement ends",
        },
        "character_arcs": [
            {
                "character_id": "char_001",
                "required_end_state": arc_state,
                "required_choice": "Mara chooses herself",
            }
        ],
        "plot_payoffs": [
            {
                "id": "payoff_001",
                "setup_ids": ["ch1:fs1"],
                "required_payoff": "minor clue",
                "deadline": 4,
                "allow_intentional_open": False,
            }
        ],
        "antagonist_outcome": {"required": False},
        "emotional_contract": {"reader_emotion": "catharsis", "afterglow_state": "new equilibrium"},
    }


def test_ending_contract_is_loaded_and_ledgers_are_created(tmp_path: Path):
    project = _project(tmp_path, contract=_contract())

    loaded = load_ending_contract(project)
    assert loaded["enforce"] is True
    paths = ensure_quality_ledgers(project, loaded)

    assert paths["ending_contract"].is_file()
    assert paths["payoff_ledger"].is_file()
    ledger = json.loads(paths["payoff_ledger"].read_text(encoding="utf-8"))
    assert ledger["items"][0]["id"] == "payoff_001"


def test_unresolved_main_conflict_blocks_ending(tmp_path: Path):
    contract = _contract(main_status="resolved")
    project = _project(tmp_path, contract=contract)
    state = StoryState(str(project))
    state.plot_threads["plot_001"].status = "active"
    state.save_state()

    report = evaluate_ending(project, as_of_chapter=4)

    assert report.status == "fail"
    assert any(item["category"] == "main_conflict_unresolved" for item in report.critical)


def test_minor_unresolved_payoff_is_warning_not_blocker(tmp_path: Path):
    contract = _contract()
    contract["plot_payoffs"][0]["allow_intentional_open"] = True
    project = _project(tmp_path, contract=contract)
    state = StoryState(str(project))
    state.chapters[4].foreshadowing_resolved = []
    state.chapters[4].foreshadowing_resolved_ids = []
    state.save_state()

    report = evaluate_ending(project, as_of_chapter=4)

    assert report.status == "pass"
    assert any(item["category"] == "payoff_unresolved" for item in report.warnings)


def test_closed_ending_passes_and_writes_report(tmp_path: Path):
    project = _project(tmp_path, contract=_contract())

    report = evaluate_ending(project, as_of_chapter=4)

    assert report.status == "pass"
    assert report.report_path.is_file()
    persisted = json.loads(report.report_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "pass"
    assert persisted["finale_window"] == {"start_chapter": 3, "end_chapter": 4}


def test_agent_state_blocks_feed_payoff_arc_and_ending_ledgers(tmp_path: Path):
    project = _project(tmp_path, contract=_contract())
    state = StoryState(str(project))
    state.chapters[4].foreshadowing_resolved_ids = []
    state.chapters[4].foreshadowing_resolved = []
    state.chapters[4].ending_evidence = []
    ingest_agent_output(
        state,
        4,
        "scribe",
        """[SCRIBE_STATE_UPDATE]
Payoff_Events:
  - payoff_001 | status=paid | evidence=The clue changes the final choice | chapter=4
Arc_State_Updates:
  - char_001 | stage=resolution | progress=100 | evidence=Mara chooses herself
Ending_Evidence:
  - irreversible_change=Mara leaves the old arrangement
  - emotional_payoff=She accepts her new life
[/SCRIBE_STATE_UPDATE]""",
    )
    state.save_state()

    assert state.chapters[4].payoff_events[0]["status"] == "paid"
    assert state.chapters[4].arc_state_updates[0]["progress"] == 100
    assert len(state.chapters[4].ending_evidence) == 2


def test_semantic_character_outcome_is_distinct_from_arc_stage(tmp_path: Path):
    contract = _contract(arc_state="accountability")
    project = _project(tmp_path, contract=contract)
    state = StoryState(str(project))
    character = state.characters["char_001"]
    character.arc_stage = "middle"
    character.arc_progress = 55
    character.outcome_state = "accountability"
    character.outcome_evidence = (
        "Mara hands over the original records and accepts the cost."
    )
    state.save_state()

    report = evaluate_ending(project, as_of_chapter=4)

    assert report.status == "pass"


def test_explicit_arc_stage_and_semantic_outcome_are_both_enforced(tmp_path: Path):
    contract = _contract()
    arc_contract = contract["character_arcs"][0]
    arc_contract.pop("required_end_state")
    arc_contract["required_arc_stage"] = "resolution"
    arc_contract["required_outcome"] = "independence"
    project = _project(tmp_path, contract=contract)
    state = StoryState(str(project))
    character = state.characters["char_001"]
    character.outcome_state = "independence"
    character.outcome_evidence = "Mara signs a lease in her own name."
    state.save_state()

    passing = evaluate_ending(project, as_of_chapter=4)
    assert passing.status == "pass"

    state = StoryState(str(project))
    state.characters["char_001"].outcome_state = "dependence"
    state.save_state()

    wrong_outcome = evaluate_ending(project, as_of_chapter=4)
    assert wrong_outcome.status == "fail"
    assert any(
        item["category"] == "character_outcome_unclosed"
        for item in wrong_outcome.critical
    )

    state = StoryState(str(project))
    state.characters["char_001"].outcome_state = "independence"
    state.characters["char_001"].arc_stage = "climax"
    state.characters["char_001"].arc_progress = 80
    state.save_state()

    failing = evaluate_ending(project, as_of_chapter=4)
    assert failing.status == "fail"
    assert any(
        item["category"] == "character_arc_unclosed"
        for item in failing.critical
    )


def _outcome_contract(mode: str = "exact") -> dict:
    contract = _contract()
    arc_contract = contract["character_arcs"][0]
    arc_contract.pop("required_end_state")
    arc_contract["required_arc_stage"] = "resolution"
    arc_contract["required_outcome"] = "independence."
    arc_contract["outcome_match_mode"] = mode
    return contract


def test_normalized_outcome_mode_ignores_formatting_only_differences(tmp_path: Path):
    project = _project(tmp_path, contract=_outcome_contract("normalized"))
    state = StoryState(str(project))
    state.characters["char_001"].outcome_state = "  INDEPENDENCE  "
    state.characters["char_001"].outcome_evidence = "Mara signs a lease."
    state.save_state()

    report = evaluate_ending(project, as_of_chapter=4)

    assert report.status == "pass"


def test_contains_outcome_mode_allows_an_explicitly_richer_state(tmp_path: Path):
    project = _project(tmp_path, contract=_outcome_contract("contains"))
    state = StoryState(str(project))
    state.characters["char_001"].outcome_state = (
        "independence with a stable home and income"
    )
    state.characters["char_001"].outcome_evidence = "Mara signs a lease."
    state.save_state()

    report = evaluate_ending(project, as_of_chapter=4)

    assert report.status == "pass"


def test_contains_outcome_mode_tolerates_connective_words_inside_a_clause(
    tmp_path: Path,
):
    contract = _outcome_contract("contains")
    contract["character_arcs"][0]["required_outcome"] = (
        "拥有独立住所、稳定收入、明确育儿和照护边界。"
    )
    project = _project(tmp_path, contract=contract)
    state = StoryState(str(project))
    state.characters["char_001"].outcome_state = (
        "拥有独立住所、稳定收入以及明确的育儿和照护边界，不等待认可而继续生活"
    )
    state.characters["char_001"].outcome_evidence = "Mara signs a lease."
    state.save_state()

    report = evaluate_ending(project, as_of_chapter=4)

    assert report.status == "pass"


def test_auto_outcome_mode_handles_natural_language_without_contract_changes(
    tmp_path: Path,
):
    contract = _outcome_contract("auto")
    contract["character_arcs"][0].pop("outcome_match_mode")
    contract["character_arcs"][0]["required_outcome"] = (
        "拥有独立住所、稳定收入、明确育儿和照护边界。"
    )
    project = _project(tmp_path, contract=contract)
    state = StoryState(str(project))
    state.characters["char_001"].outcome_state = (
        "拥有独立住所、稳定收入以及明确的育儿和照护边界，不等待认可而继续生活"
    )
    state.characters["char_001"].outcome_evidence = "Mara signs a lease."
    state.save_state()

    report = evaluate_ending(project, as_of_chapter=4)

    assert report.status == "pass"


def test_auto_outcome_mode_keeps_short_ascii_ids_strict(tmp_path: Path):
    contract = _outcome_contract("auto")
    contract["character_arcs"][0].pop("outcome_match_mode")
    contract["character_arcs"][0]["required_outcome"] = "independence"
    project = _project(tmp_path, contract=contract)
    state = StoryState(str(project))
    state.characters["char_001"].outcome_state = "independence with extra detail"
    state.characters["char_001"].outcome_evidence = "Mara signs a lease."
    state.save_state()

    report = evaluate_ending(project, as_of_chapter=4)

    assert report.status == "fail"


def test_contains_mode_does_not_confuse_similar_ascii_outcome_ids(tmp_path: Path):
    project = _project(tmp_path, contract=_outcome_contract("contains"))
    state = StoryState(str(project))
    state.characters["char_001"].outcome_state = "dependence"
    state.characters["char_001"].outcome_evidence = "Mara remains dependent."
    state.save_state()

    report = evaluate_ending(project, as_of_chapter=4)

    assert report.status == "fail"
    assert any(
        item["category"] == "character_outcome_unclosed"
        for item in report.critical
    )


def test_contains_mode_supports_multiword_outcome_phrases(tmp_path: Path):
    contract = _outcome_contract("contains")
    contract["character_arcs"][0]["required_outcome"] = "independent life"
    project = _project(tmp_path, contract=contract)
    state = StoryState(str(project))
    state.characters["char_001"].outcome_state = (
        "chooses an independent life with a stable home"
    )
    state.characters["char_001"].outcome_evidence = "Mara signs a lease."
    state.save_state()

    report = evaluate_ending(project, as_of_chapter=4)

    assert report.status == "pass"


def test_outcome_aliases_allow_explicit_paraphrases(tmp_path: Path):
    contract = _outcome_contract("exact")
    contract["character_arcs"][0]["required_outcome_aliases"] = [
        "chooses an independent life"
    ]
    project = _project(tmp_path, contract=contract)
    state = StoryState(str(project))
    state.characters["char_001"].outcome_state = "chooses an independent life"
    state.characters["char_001"].outcome_evidence = "Mara signs a lease."
    state.save_state()

    report = evaluate_ending(project, as_of_chapter=4)

    assert report.status == "pass"


def test_agent_arc_update_persists_semantic_outcome_and_evidence(tmp_path: Path):
    project = _project(tmp_path, contract=_contract(arc_state="accountability"))
    state = StoryState(str(project))

    ingest_agent_output(
        state,
        4,
        "scribe",
        """[SCRIBE_STATE_UPDATE]
Arc_State_Updates:
  - char_001 | stage=resolution | progress=100 | outcome=accountability | evidence=Mara hands over the original records
[/SCRIBE_STATE_UPDATE]""",
    )
    state.save_state()

    reloaded = StoryState(str(project))
    character = reloaded.characters["char_001"]
    assert character.arc_stage == "resolution"
    assert character.outcome_state == "accountability"
    assert character.outcome_evidence == "Mara hands over the original records"
    assert reloaded.chapters[4].arc_state_updates[-1]["outcome"] == "accountability"


def test_finale_prompts_share_semantic_ending_contract(tmp_path: Path):
    contract = _contract()
    arc_contract = contract["character_arcs"][0]
    arc_contract.pop("required_end_state")
    arc_contract["required_arc_stage"] = "resolution"
    arc_contract["required_outcome"] = "independence"
    project = _project(tmp_path, contract=contract)
    orchestrator = NovelOrchestrator(str(project))
    chapter = orchestrator.state.get_chapter(4)

    prompts = {
        "architect": orchestrator._generate_chapter_outline_prompt(chapter),
        "scribe": orchestrator._generate_chapter_prompt(chapter),
        "editor": orchestrator._generate_edit_prompt(chapter, "Draft", "developmental"),
        "guardian": orchestrator._generate_validation_prompt(4, "Draft"),
    }

    assert all('"required_outcome": "independence"' in value for value in prompts.values())
    assert "outcome=<canonical outcome value from ending_contract>" in prompts[
        "scribe"
    ]
    assert "outcome=<canonical outcome value from ending_contract>" in prompts[
        "guardian"
    ]


def test_pipeline_ending_stages_gate_before_compile(tmp_path: Path):
    project = _project(tmp_path, contract=_contract())
    spec = RunSpec(project_path=str(project), num_chapters=4, target_words=40, approval_policy="auto")
    manifest = RunManifest.new(spec, run_id="ending-gate")
    runner = PipelineRunner()
    store = runner._store(project, manifest.run_id)

    assert runner._ending_contract_enforced(project) is True
    runner._stage(
        manifest,
        project,
        store,
        "ending.preflight",
        None,
        lambda: ensure_quality_ledgers(project),
        lambda _value: runner._require_files(project, ["outputs/input/ending_contract.json"]),
        ["outputs/input/ending_contract.json", "outputs/state/payoff_ledger.json"],
    )
    runner._stage(
        manifest,
        project,
        store,
        "ending.review",
        None,
        lambda: evaluate_ending(project, as_of_chapter=4),
        runner._validate_ending_result,
        ["outputs/input/ending_contract.json", "outputs/state/payoff_ledger.json", "outputs/feedback/book_completion_report.json"],
    )

    assert manifest.get("ending.review").status == "done"


def _project_with_payoff_events(tmp_path: Path, events: dict[int, list[dict]]) -> Path:
    """Exercise persisted canon without a foreshadowing-id fallback."""
    count = max(4, *events)
    contract = _contract()
    contract["finale_window"] = {"start_chapter": max(1, count - 4), "end_chapter": count}
    project = _project(tmp_path, chapters=count, contract=contract)
    state = StoryState(str(project))
    for chapter in state.chapters.values():
        chapter.foreshadowing_resolved = []
        chapter.foreshadowing_resolved_ids = []
        chapter.payoff_events = events.get(chapter.number, [])
    state.save_state()
    # Promotion snapshots can serialize chapter keys lexicographically: 1, 10, 2.
    path = project / "outputs/state/story_state.json"
    path.write_text(json.dumps(json.loads(path.read_text()), sort_keys=True))
    return project


def _payoff_event(status: str, evidence: str) -> dict:
    return {"payoff_id": "payoff_001", "status": status, "evidence": evidence}


def test_pipeline_ending_preserves_paid_evidence_after_later_recall(tmp_path: Path):
    project = _project_with_payoff_events(tmp_path, {
        2: [_payoff_event("paid", "Both parents confirm the truth; Mara changes her plan.")],
        4: [_payoff_event("recalled", "Mara sees the parents keep the arrangement.")],
    })
    state_before = (project / "outputs/state/story_state.json").read_bytes()
    manifest = RunManifest.new(
        RunSpec(project_path=str(project), num_chapters=4, target_words=40, approval_policy="auto"),
        run_id="paid-then-recalled",
    )
    runner = PipelineRunner()
    runner._stage(
        manifest, project, runner._store(project, manifest.run_id),
        "ending.review", None,
        lambda: evaluate_ending(project, as_of_chapter=4),
        runner._validate_ending_result,
        ["outputs/state/payoff_ledger.json", "outputs/feedback/book_completion_report.json"],
    )

    assert manifest.get("ending.review").status == "done"
    ledger = json.loads((project / "outputs/state/payoff_ledger.json").read_text())
    assert ledger["items"][0]["status"] == "paid"
    assert ledger["items"][0]["payoff_evidence"] == [{
        **_payoff_event("paid", "Both parents confirm the truth; Mara changes her plan."),
        "chapter": 2,
    }]
    assert (project / "outputs/state/story_state.json").read_bytes() == state_before


def test_paid_event_survives_lexicographic_chapter_order_and_same_chapter_recall(tmp_path: Path):
    project = _project_with_payoff_events(tmp_path, {
        2: [_payoff_event("recalled", "The question remains active.")],
        10: [
            _payoff_event("paid", "Mara acts after the direct confirmation."),
            _payoff_event("recalled", "She remembers that choice afterward."),
        ],
    })

    assert evaluate_ending(project, as_of_chapter=10).status == "pass"
    ledger = json.loads((project / "outputs/state/payoff_ledger.json").read_text())
    assert ledger["items"][0]["payoff_evidence"][0]["chapter"] == 10
    assert ledger["items"][0]["payoff_evidence"][0]["status"] == "paid"


def test_recalled_events_without_paid_evidence_still_block(tmp_path: Path):
    project = _project_with_payoff_events(tmp_path, {
        2: [_payoff_event("recalled", "Mara asks the question.")],
        4: [_payoff_event("recalled", "The question is mentioned again.")],
    })

    report = evaluate_ending(project, as_of_chapter=4)

    assert report.status == "fail"
    assert any(item["category"] == "core_payoff_unresolved" for item in report.critical)


def test_nonpaid_event_selection_uses_numeric_chapter_order(tmp_path: Path):
    project = _project_with_payoff_events(tmp_path, {
        2: [_payoff_event("intentional_open", "Mara considers leaving the question open.")],
        10: [_payoff_event("recalled", "The still-unanswered question is active again.")],
    })

    report = evaluate_ending(project, as_of_chapter=10)

    assert report.status == "fail"
    assert any(item["category"] == "core_payoff_unresolved" for item in report.critical)


def test_multiple_paid_events_retain_first_completion_source(tmp_path: Path):
    project = _project_with_payoff_events(tmp_path, {
        2: [_payoff_event("paid", "The first direct confirmation changes Mara's choice.")],
        3: [_payoff_event("paid", "The new arrangement remains in force.")],
        4: [_payoff_event("recalled", "Mara recalls the confirmed truth.")],
    })

    assert evaluate_ending(project, as_of_chapter=4).status == "pass"
    ledger = json.loads((project / "outputs/state/payoff_ledger.json").read_text())
    assert ledger["items"][0]["payoff_evidence"][0]["chapter"] == 2
