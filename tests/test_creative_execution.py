"""Source-bound execution tests use fake clients; no paid model calls."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import creative_execution as ce
from artifacts import ArtifactStore
from core.creative_types import CreativeTypeStore
from pipeline_models import RunManifest, RunSpec
from state_manager import StoryState
from test_creative_types import selected


SOURCE = "Rin refuses the pack's demand. Sanctuary costs a season of service, never consent to a mate."


@pytest.fixture
def setup(tmp_path):
    project = tmp_path / "book"
    inputs = project / "outputs" / "input"
    inputs.mkdir(parents=True)
    (inputs / "prompt.md").write_text(SOURCE)
    foundation = {"title": "Sanctuary", "setting": {"rules": [SOURCE]},
                  "characters": [{"id": "rin", "name": "Rin", "future_outcome": "new alliance"}],
                  "chapters": [{"number": n, "title": f"Choice {n}", "summary": f"PLANNED {n}"} for n in range(1, 6)]}
    (inputs / "foundation.json").write_text(json.dumps(foundation))
    library = CreativeTypeStore(tmp_path / "types")
    snapshot = library.snapshot(selected(library))
    handoff = ce.make_handoff(source=SOURCE, origin={"workshop_id": "fixture", "revision": 9}, type_snapshot=snapshot)
    runtime = ce.CreativeExecution.open(project=project, run_id="test", handoff=handoff)
    state = StoryState(str(project))
    for n in range(1, 6):
        state.create_chapter(n)
    return project, runtime, handoff, state, library


def promote(project, state, number, body):
    artifacts = ArtifactStore(project)
    revision = artifacts.put_text(chapter=number, kind="final", text=body, source="test")
    chapter = state.get_chapter(number)
    chapter.status = "complete"
    chapter.canonical_revision_id = revision.revision_id
    state.save_state()
    return revision


def test_frozen_types_and_prompts_do_not_follow_new_versions(setup, monkeypatch):
    project, runtime, handoff, state, library = setup
    before = runtime.chapter_context(1, "scribe", state)
    profile = library.get("tag.betrayal")["draft"]
    profile = profile.replace("## 核心承诺", "## 核心承诺\n\nSHOULD NOT APPEAR IN EXISTING BOOK")
    library.mutate("activate", "tag.betrayal", revision=0, operation_id="test-new-version", markdown=profile)
    monkeypatch.setattr(ce, "_system_prompts", lambda: pytest.fail("resume read live agent prompts"))
    reopened = ce.CreativeExecution.open(project=project, run_id="test", handoff=handoff,
                                        expected_sha256=runtime.lock_sha256, already_started=True)
    assert reopened.chapter_context(1, "scribe", state) == before
    assert "SHOULD NOT APPEAR" not in json.dumps(before)
    assert "Every scene needs at least three senses" not in reopened._lock["system_prompts"]["scribe"]
    assert "Every scene needs at least three senses" in (Path(__file__).parents[1] / "agents/scribe/prompt.md").read_text()


def test_restored_global_facts_reach_scribe_and_reviewer_contexts(setup):
    project, runtime, _, state, _ = setup
    path = project / 'outputs/input/foundation.json'
    foundation = json.loads(path.read_text())
    foundation['characters'][0]['background'] = 'Repairs tools for partial income.'
    foundation['reader_profile'] = 'US adult readers.'
    foundation['story_lead_plan'] = 'A separate introduction before chapter 1.'
    path.write_text(json.dumps(foundation))
    for agent in ('scribe', 'continuity'):
        book = runtime.chapter_context(1, agent, state)['planned_design']['book']
        assert book['characters'] == foundation['characters']
        assert book['reader_profile'] == foundation['reader_profile']
        assert book['story_lead_plan'] == foundation['story_lead_plan']


@pytest.mark.parametrize("change", ["source", "lock", "delete"])
def test_changed_source_or_lock_fails_before_model(setup, change):
    project, runtime, handoff, state, _ = setup
    if change == "source":
        (project / "outputs/input/prompt.md").write_text("different adopted story")
    elif change == "delete":
        (runtime.directory / "lock.json").unlink()
    else:
        value = json.loads((runtime.directory / "lock.json").read_text())
        value["rules"] = "new rules"
        (runtime.directory / "lock.json").write_text(json.dumps(value))
    with pytest.raises((ce.CreativeExecutionError, OSError)):
        ce.CreativeExecution.open(project=project, run_id="test", handoff=handoff,
                                 expected_sha256=runtime.lock_sha256, already_started=True)
    assert not (runtime.directory / "requests").exists()


def test_plans_do_not_become_events_and_prose_uses_promoted_revision_not_newer_head(setup):
    project, runtime, _, state, _ = setup
    for n in range(1, 4):
        promote(project, state, n, f"APPROVED PROSE {n}\n\nRin counts the cost, then refuses.")
    artifacts = ArtifactStore(project)
    newer = artifacts.put_text(chapter=3, kind="final", text="UNADOPTED NEW FINAL", source="test")
    artifacts.set_head(3, "final", newer.revision_id, expected_revision_id=None)
    promote(project, state, 5, "FUTURE PROSE MUST NOT LEAK")
    context = runtime.chapter_context(4, "scribe", state)
    assert context["planned_design"]["coverage"]["included_chapters"] == [3, 4, 5]
    assert context["planned_design"]["coverage"]["omitted_chapters"] == [1, 2]
    assert context["prose_coverage"]["complete_chapters"] == [2, 3]
    assert context["prose_coverage"]["not_supplied_in_full"] == [1]
    assert [p["text"] for p in context["approved_previous_prose"]] == [
        "APPROVED PROSE 2\n\nRin counts the cost, then refuses.",
        "APPROVED PROSE 3\n\nRin counts the cost, then refuses."]
    assert "FUTURE PROSE" not in json.dumps(context)
    assert "UNADOPTED" not in json.dumps(context)
    assert context["voice_reference"]["chapter"] == 1


def test_missing_approved_prose_never_falls_back_to_draft(setup):
    project, runtime, _, state, _ = setup
    manuscript = project / "outputs/manuscript"
    manuscript.mkdir()
    (manuscript / "chapter_001_final.md").write_text("unbound mutable final")
    with pytest.raises(ce.CreativeExecutionError, match="promoted final"):
        runtime.chapter_context(2, "scribe", state)


def test_voice_passages_are_verifiable_disclosed_samples():
    body = "\n\n".join(f"Paragraph {n}: " + "voice and motive. " * 40 for n in range(30))
    sample = ce.voice_passages(body)
    assert 0 < sample["supplied_chars"] < len(body)
    for passage in sample["passages"]:
        assert body[passage["start_char"]:passage["end_char"]] == passage["text"]
        assert passage["text"].rstrip().endswith("voice and motive.")


def test_receipts_bind_actual_request_and_only_one_model_call(setup):
    _, runtime, _, state, _ = setup
    calls = []
    def complete(system, user):
        calls.append((system, user))
        return "raw reply"
    user, receipt = runtime.prepare("scribe", "Write the chapter", 1, state)
    result = runtime.complete(SimpleNamespace(complete=complete), "scribe", user, receipt)
    assert result == "raw reply" and len(calls) == 1
    saved = json.loads(next((runtime.directory / "requests").glob("*.json")).read_text())
    assert saved["status"] == "response_received"
    assert saved["user_sha256"] == ce.text_hash(calls[0][1])
    assert saved["system_sha256"] == ce.text_hash(calls[0][0])
    assert saved["context"]["voice_reference"]["status"] == "not_available"
    with pytest.raises(ce.CreativeExecutionError, match="differs"):
        runtime.complete(SimpleNamespace(complete=complete), "scribe", user + "edited", receipt)
    assert len(calls) == 1


def test_completed_response_recovery_requires_exact_request_and_reply(setup):
    _, runtime, _, state, _ = setup
    user, receipt = runtime.prepare("style_curator", "Polish", 1, state)
    client = SimpleNamespace(complete=lambda **_: "complete reply")
    runtime.complete(client, "style_curator", user, receipt)
    assert runtime.recover_response("style_curator", user, receipt, "unbound raw file") is None
    changed_user, changed_receipt = runtime.prepare("style_curator", "Different instruction", 1, state)
    assert runtime.recover_response("style_curator", changed_user, changed_receipt, "complete reply") is None
    assert runtime.recover_response("style_curator", user, receipt, "complete reply") == "complete reply"
    records = [json.loads(p.read_text()) for p in (runtime.directory / "requests").glob("*.json")]
    assert sum(r["status"] == "response_received" for r in records) == 1
    reused = next(r for r in records if r["status"] == "response_reused")
    assert reused["response_sha256"] == ce.text_hash("complete reply")
    assert (runtime.directory / "requests" / (reused["original_request_id"] + ".json")).is_file()


def test_completed_response_recovery_rejects_corruption_and_changed_source(setup):
    project, runtime, _, state, _ = setup
    user, receipt = runtime.prepare("style_curator", "Polish", 1, state)
    runtime.complete(SimpleNamespace(complete=lambda **_: "complete reply"), "style_curator", user, receipt)
    path = next((runtime.directory / "requests").glob("*.response.txt"))
    path.write_text("corrupt reply")
    with pytest.raises(ce.CreativeExecutionError, match="integrity"):
        runtime.recover_response("style_curator", user, receipt, "complete reply")
    path.write_text("complete reply")
    (project / "outputs/input/foundation.json").write_text('{}')
    with pytest.raises(ce.CreativeExecutionError, match="context changed"):
        runtime.recover_response("style_curator", user, receipt, "complete reply")


def test_style_local_parse_failure_reuses_reply_without_a_second_model_call(setup, monkeypatch):
    import orchestrator as om
    from proposals import ProposalStore
    from state_parser import EndingProtocolError
    project, runtime, _, state, _ = setup
    state.save_state()
    engine = om.NovelOrchestrator(str(project), state_update_mode="proposal_only")
    engine.creative_execution = runtime
    engine.raise_llm_errors = True
    calls = []
    reply = """[STYLE_ANALYSIS]
Payoff_Events: EP_F06 | status=private-revelation stage | evidence=Private admission, not a completed payoff
[/STYLE_ANALYSIS]
[REVISED_CHAPTER]
Rin refused the exchange. The question remained open.
[/REVISED_CHAPTER]
[STYLE_STATE_UPDATE]
Consistency_Score: 9/10
[/STYLE_STATE_UPDATE]"""
    def complete(**_):
        calls.append(True)
        return reply
    engine._llm = SimpleNamespace(provider="fake", model="fixture", complete=complete)
    revised = project / "outputs/manuscript/chapter_001_revised.md"
    revised.write_text("Rin refused the exchange. The question remained open.")
    before = (project / "outputs/state/story_state.json").read_bytes()
    with monkeypatch.context() as patch:
        def reject(*_):
            raise EndingProtocolError("ending_protocol: invalid analysis status")
        patch.setattr(om, "parse_agent_output", reject)
        with pytest.raises(EndingProtocolError):
            engine.curate_chapter(1)
    result = engine.curate_chapter(1, recover_saved_response=True)
    assert len(calls) == 1
    assert result.strip() == revised.read_text()
    proposal = ProposalStore(project).load(engine.last_canon_proposal_ids[0])
    assert "payoff_events" not in proposal.delta
    assert "private-revelation" in proposal.delta["style_analysis"]["payoff_events"]
    assert (project / "outputs/state/story_state.json").read_bytes() == before
    assert (project / "outputs/feedback/chapter_001_style_report.md.raw").read_text() == reply


def test_invalid_explicit_style_state_is_not_replayed_forever_on_retry(setup):
    from orchestrator import NovelOrchestrator
    from state_parser import EndingProtocolError
    project, runtime, _, state, _ = setup
    state.save_state()
    engine = NovelOrchestrator(str(project), state_update_mode="proposal_only")
    engine.creative_execution = runtime
    engine.raise_llm_errors = True
    bad = """[REVISED_CHAPTER]
Rin refuses.
[/REVISED_CHAPTER]
[STYLE_STATE_UPDATE]
Payoff_Events: EP_F06 | status=private-revelation stage | evidence=The admission occurs
[/STYLE_STATE_UPDATE]"""
    good = """[REVISED_CHAPTER]
Rin refuses.
[/REVISED_CHAPTER]
[STYLE_STATE_UPDATE]
Consistency_Score: 9/10
[/STYLE_STATE_UPDATE]"""
    replies = [bad, good]
    engine._llm = SimpleNamespace(provider="fake", model="fixture", complete=lambda **_: replies.pop(0))
    (project / "outputs/manuscript/chapter_001_revised.md").write_text("Rin refuses.")
    with pytest.raises(EndingProtocolError):
        engine.curate_chapter(1)
    assert engine.curate_chapter(1, recover_saved_response=True).strip() == "Rin refuses."
    assert replies == []
    records = [json.loads(p.read_text()) for p in (runtime.directory / "requests").glob("*.json")]
    assert len(records) == 2 and all(r['status'] == 'response_received' for r in records)


def test_budget_excess_stops_without_silent_cutting(setup):
    _, runtime, _, state, _ = setup
    with pytest.raises(ce.CreativeExecutionError, match="nothing was truncated"):
        runtime.prepare("scribe", "x" * ce.MAX_REQUEST_BYTES, 1, state)
    assert not (runtime.directory / "requests").exists()


def test_changed_context_retains_complete_reply_but_does_not_accept_it(setup):
    project, runtime, _, state, _ = setup
    user, receipt = runtime.prepare("scribe", "Write", 1, state)
    def complete(**_):
        (project / "outputs/input/foundation.json").write_text('{"chapters": []}')
        return "COMPLETE MODEL REPLY FOR DIAGNOSIS"
    with pytest.raises(ce.CreativeExecutionError, match="context changed"):
        runtime.complete(SimpleNamespace(complete=complete), "scribe", user, receipt)
    assert next((runtime.directory / "requests").glob("*.response.txt")).read_text() == "COMPLETE MODEL REPLY FOR DIAGNOSIS"
    assert not (project / "outputs/manuscript/chapter_001_final.md").exists()


def test_failed_call_has_honest_receipt_without_extra_model_attempts(setup):
    _, runtime, _, state, _ = setup
    calls = []
    def complete(**_):
        calls.append(True)
        raise TimeoutError("fixture timeout")
    user, receipt = runtime.prepare("scribe", "Write", 1, state)
    with pytest.raises(TimeoutError):
        runtime.complete(SimpleNamespace(complete=complete), "scribe", user, receipt)
    saved = json.loads(next((runtime.directory / "requests").glob("*.json")).read_text())
    assert calls == [True] and saved["status"] == "failed"
    assert saved["error_type"] == "TimeoutError"
    assert "response_sha256" not in saved


def test_mismatched_input_stops_before_author_model(tmp_path, fake_author_model):
    from pipeline_runner import PipelineRunner
    source = tmp_path / "source.md"
    source.write_text("CHANGED")
    spec = RunSpec(project_path=str(tmp_path / "project"), prompt_path=str(source),
                   creative_handoff=ce.make_handoff(source=SOURCE, origin={}), quality_policy="evidence_v1")
    result = PipelineRunner().run(spec)
    assert result.status == "failed" and "before author generation" in result.error
    assert fake_author_model == []


def test_legacy_manifest_has_no_implicit_opt_in_and_new_handoff_roundtrips(setup):
    project, runtime, handoff, _, _ = setup
    legacy = RunManifest.from_dict({"run_id": "legacy", "spec": {"project_path": str(project)}})
    assert legacy.spec.creative_handoff is None
    assert not legacy.creative_snapshot_started and legacy.creative_lock_sha256 == ""
    current = RunManifest.new(RunSpec(project_path=str(project), creative_handoff=handoff, quality_policy="evidence_v1"))
    current.creative_snapshot_started = True
    current.creative_lock_sha256 = runtime.lock_sha256
    assert RunManifest.from_dict(current.to_dict()) == current
    bad = deepcopy(handoff); bad["source_sha256"] = "a" * 64
    with pytest.raises(ce.CreativeExecutionError, match="hash mismatch"):
        RunSpec(project_path=str(project), creative_handoff=bad)
    with pytest.raises(ValueError, match="requires evidence_v1"):
        RunSpec(project_path=str(project), creative_handoff=handoff)


def test_checkpoint_dependencies_ignore_current_and_future_changes(setup):
    project, _, _, state, _ = setup
    promote(project, state, 1, "Past")
    before = ce.prior_context_hash(project, 2)
    promote(project, state, 2, "Current output")
    promote(project, state, 4, "Future output")
    assert ce.prior_context_hash(project, 2) == before
    promote(project, state, 1, "Newly adopted past")
    assert ce.prior_context_hash(project, 2) != before


def test_staged_planning_uses_frozen_context_and_reuses_saved_calls(setup, tmp_path):
    from test_outline_planning import PlanningModel, make_planner
    _, runtime, _, _, _ = setup

    class Model(PlanningModel):
        def complete(self, system, user):
            original, extra = user.split("\n\n[CREATIVE_EXECUTION_CONTEXT]\n", 1)
            context = json.loads(extra.split("\n[/CREATIVE_EXECUTION_CONTEXT]", 1)[0])
            expected = "chapter_plan" if original.startswith("STAGE: chapter_batch") else "review" if original.startswith("STAGE: semantic_review") else "story_design"
            assert context["genre_context"]["stage"] == expected
            assert context["lock_sha256"] == runtime.lock_sha256
            assert "CREATIVE EXECUTION POLICY" in system
            return super().complete(system, original)

    model = Model()
    # The existing fixture cites its own source phrase; use a fixture-valid
    # source while binding that exact complete input into this run.
    from test_outline_planning import SOURCE as planner_source
    (runtime.project / "outputs/input/prompt.md").write_text(planner_source)
    handoff = ce.make_handoff(source=planner_source, origin={"workshop_id": "planner"},
                             type_snapshot=runtime._lock["handoff"]["type_snapshot"])
    frozen = ce.CreativeExecution.open(project=runtime.project, run_id="planner", handoff=handoff)
    runtime = frozen
    planner = make_planner(tmp_path, model, count=4, source=planner_source, creative_execution=frozen)
    result = planner.run()
    assert len(result["chapters"]) == 4
    assert [stage for stage, _ in model.calls] == ["global", "batch", "review"]
    again = make_planner(tmp_path, model, count=4, source=planner_source, creative_execution=frozen)
    assert again.run() == result
    assert len(model.calls) == 3
    assert len(list((frozen.directory / "requests").glob("*.json"))) == 3


@pytest.mark.parametrize("expanded", [False, True])
def test_actual_scribe_editor_style_calls_share_context(setup, expanded):
    from orchestrator import NovelOrchestrator
    project, runtime, _, state, _ = setup
    state.get_chapter(1).pov_character = "Rin"
    state.save_state()
    engine = NovelOrchestrator(str(project))
    engine.creative_execution = runtime
    calls = []

    def complete(system, user):
        calls.append((system, user))
        assert "[CREATIVE_EXECUTION_CONTEXT]" in user
        assert '"stage":"draft"' in user or '"stage":"review"' in user
        assert "Every scene needs at least three senses" not in system
        if "THE SCRIBE" in system:
            return "Rin refused the exchange.\n[SCRIBE_STATE_UPDATE]\nKey_Events: Rin refuses\n[/SCRIBE_STATE_UPDATE]"
        return "[REVISED_CHAPTER]\nRin refused the exchange.\n[/REVISED_CHAPTER]"

    engine._llm = SimpleNamespace(provider="fake", model="fixture", complete=complete,
                                  run_agent=lambda *_: pytest.fail("bypassed frozen request"))
    if expanded:
        (project / "outputs/chapter_001_outline.md").write_text("Author-expanded beat: count the cost.")
    engine.write_chapter(1)
    engine.edit_chapter(1)
    engine.state.get_chapter(1).status = "validated"
    engine.curate_chapter(1)
    assert len(calls) == 3
    assert len(list((runtime.directory / "requests").glob("*.json"))) == 3
    if expanded:
        assert "Author-expanded beat" in calls[0][1]
    else:
        assert "Include at least 3 sensory" not in calls[0][1]
    assert "Ensure the protagonist causes the chapter's major turn" not in calls[1][1]


def test_run_resume_keeps_lock_and_does_not_replay_completed_work(tmp_path):
    from pipeline_runner import PipelineRunner
    from test_pipeline_integration import PipelineLLM
    from orchestrator import NovelOrchestrator

    seen = []
    class Model(PipelineLLM):
        def complete(self, *, system, user):
            if "[CREATIVE_EXECUTION_CONTEXT]" not in user:
                return super().complete(system=system, user=user)
            original, extra = user.split("\n\n[CREATIVE_EXECUTION_CONTEXT]\n", 1)
            context = json.loads(extra.split("\n[/CREATIVE_EXECUTION_CONTEXT]", 1)[0])
            agent = next(name for name in ce.AGENTS if name != "outline_planner"
                         and f"THE {name.upper().replace('_', ' ')}" in system.splitlines()[0])
            if agent == "scribe":
                seen.append((context["chapter"], context["lock_sha256"]))
            return self.run_agent(agent, original)

    client = Model()
    def factory(project_path):
        engine = NovelOrchestrator(project_path)
        engine._llm = client
        return engine

    prompt = tmp_path / "input.md"
    prompt.write_text("# Test Book\n\nA story.")
    handoff = ce.make_handoff(source=prompt.read_text(), origin={"workshop_id": "fixture"})
    project = tmp_path / "project"
    spec = RunSpec(project_path=str(project), prompt_path=str(prompt), num_chapters=2,
                   target_words=40, approval_policy="review_required", creative_handoff=handoff,
                   method_policy={"mode": "off"}, quality_policy="evidence_v1", max_retries=0, outline_planning_mode="legacy")
    runner = PipelineRunner(orchestrator_factory=factory)
    paused = runner.run(spec)
    assert paused.status == "paused", paused.error
    assert seen == [(1, paused.creative_lock_sha256)]
    completed = runner.resume(paused.run_id, project, approval_policy="auto")
    assert completed.status == "completed", completed.error
    assert seen == [(1, completed.creative_lock_sha256), (2, completed.creative_lock_sha256)]
    assert completed.creative_snapshot_started
    assert completed.creative_lock_sha256 == paused.creative_lock_sha256
    # Real promotion records and final revisions can supply the next chapter;
    # planning fields never stand in for the prior prose.
    runtime = ce.CreativeExecution.open(project=project, run_id=completed.run_id, handoff=handoff,
                                       expected_sha256=completed.creative_lock_sha256, already_started=True)
    context = runtime.chapter_context(2, "scribe", StoryState(str(project)))
    assert context["prose_coverage"]["complete_chapters"] == [1]
    assert context["committed_updates"]
    assert all(item["chapter"] == 1 for item in context["committed_updates"])


def test_handoff_keeps_only_current_explicit_adoptions_and_author_revision():
    from api.workshop_design import execution_handoff, new_design
    session = {"id": "workshop", "revision": 11, "prompt_md": SOURCE,
               "creative_design": new_design({"genres": ["Werewolf"]})}
    design = session["creative_design"]
    design["rounds"] = [{"proposals": [{"id": "a", "story": "old proposal"}, {"id": "b", "story": "rejected"}]}]
    design["decisions"] = [{"proposal_id": "a", "status": "accepted"},
                           {"proposal_id": "a", "status": "revised", "note": "No mate coercion"},
                           {"proposal_id": "b", "status": "rejected"}]
    handoff = execution_handoff(session)
    assert len(handoff["adopted_proposals"]) == 1
    assert handoff["adopted_proposals"][0]["author_decision"]["note"] == "No mate coercion"
    assert handoff["source_sha256"] == ce.text_hash(SOURCE)
    assert "rejected" not in json.dumps(handoff)


def test_cli_uses_same_frozen_guidance_and_rejects_unbound_dynamic_taxonomy(tmp_path):
    from core.knowledge_intake import load_handoff, validate_source_selection
    from core.creative_types import CreativeTypeStore
    from test_creative_types import selected
    from core.publication_taxonomy import bind_classification
    from orchestrator import main
    store = CreativeTypeStore(tmp_path/'library')
    snapshot = store.snapshot(selected(store))
    source = bind_classification('# A new choice\nGenre: Romance\nChapters: 2\n\nA shared promise changes their lives.', {'publication_taxonomy':snapshot['publication_taxonomy']})
    prompt = tmp_path/'prompt.md';prompt.write_text(source)
    knowledge = tmp_path/'knowledge.json';knowledge.write_text(json.dumps(snapshot))
    with pytest.raises(ValueError, match='缺少冻结'):validate_source_selection(source, None)
    handoff = load_handoff(str(prompt), str(knowledge))
    assert handoff['type_snapshot'] == snapshot
    project = tmp_path/'cli-book'
    assert main(['run','--project',str(project),'--prompt',str(prompt),'--knowledge',str(knowledge),'--quality-policy','evidence_v1','--chapters','2','--dry-run']) == 0
    assert json.loads((project/'outputs/input/knowledge.json').read_text()) == snapshot
