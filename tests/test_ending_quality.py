import json
from pathlib import Path

from ending_quality import evaluate_ending, ensure_quality_ledgers, load_ending_contract
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
