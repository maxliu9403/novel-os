"""Real contracts and fake inference: no network or manuscript mutation in fixtures."""
from copy import deepcopy
import hashlib
import json

import pytest

from core.creative_types import CreativeTypeStore
from core.story_design import VERSION, render
from core.chapter_review import read_review, project_outline, DIMENSIONS
from test_creative_types import selected
from test_workshop_api import client  # noqa: F401


def freeze_legacy_policy(tmp_path, identifier):
    path = tmp_path / "workshop_v2" / identifier / "session.json"
    data = json.loads(path.read_text())
    policy = data["creative_design"]["policy"]
    policy.pop("story_engine", None)
    data["creative_design"]["policy_sha256"] = hashlib.sha256(json.dumps(policy, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    path.write_text(json.dumps(data))


@pytest.fixture
def design(tmp_path):
    store = CreativeTypeStore(tmp_path / "types")
    taxonomy = store.snapshot(selected(store))["publication_taxonomy"]
    session = {"id": "test", "revision": 1, "messages": [], "questions": [], "prompt_md": "",
               "turn_intent": "deliver", "skill_version": "fixture", "input": {
                   "publication_taxonomy": taxonomy, "chapters": 2, "words_per_chapter": 800,
                   "language": "English", "title": "The Choice", "author": "A. Writer",
                   "mode": "short_novel"},
               "creative_design": {"policy": {"story_engine": VERSION}}}
    story = {"narrative_mode": "short_novel", "total_chapters": 2, "story_design": {
        "title": "The Choice", "author": "A. Writer", "premise": "An ally asks for the key she cannot give.",
        "characters": "Mara wants trust; Iris wants an honest answer.", "world_rules": "A modern town.",
        "causal_chain": "A refused request leads to an honest admission and a different alliance.",
        "ending": "They choose a bounded alliance.", "decisions": "Author chose reconciliation with a cost.",
        "voice": "Intimate, restrained dialogue with distinct voices.", "setups_payoffs": "The key changes meaning."}}
    chapters = {"narrative_mode": "short_novel", "total_chapters": 2, "chapters": [
        {"number": n, "title": f"Choice {n}", "pov": "Mara", "core_plot": [
            f"Mara places the key between them in scene {n}. Iris returns it and asks for the truth."],
         "reader_value": "A costly admission changes trust.", "ending_hook": "The returned key offers a different future.",
         "setups_payoffs": ["The key recalls the first promise."]} for n in (1, 2)]}
    return session, story, chapters


def test_compiler_preserves_every_card_and_passes_real_intake(design, tmp_path):
    from api.workshop import _validate_candidate
    from core.skeleton_projection import chapter_source
    session, story, chapters = design
    result = render(session, story, chapters)
    path = tmp_path / "prompt.md"
    path.write_text(result["prompt_md"])
    validated = _validate_candidate(path)
    assert validated["chapters"] == 2
    assert result == render(session, story, chapters)
    for c in chapters["chapters"]:
        assert c["core_plot"][0] in result["prompt_md"]
    assert "COVER_HANDOFF_JSON" not in result["prompt_md"]
    assert chapter_source(result["prompt_md"], [1], 2)["text"] == result["prompt_md"]


def test_compiler_rejects_missing_duplicate_cards_and_stale_counts(design):
    session, story, chapters = design
    for change in (lambda d: d["chapters"].pop(), lambda d: d["chapters"][1].update(number=1),
                   lambda d: d["chapters"][0].update(core_plot=[]), lambda d: d.update(total_chapters=3)):
        value = deepcopy(chapters)
        change(value)
        with pytest.raises(ValueError):
            render(session, story, value)


def test_complex_structure_requires_valid_structure_before_checkpoint(design):
    from api.workshop_delivery import parse_stage, DeliveryError
    _, story, _ = design
    story["narrative_mode"] = "multi_volume"
    with pytest.raises(DeliveryError, match="分卷"):
        parse_stage(json.dumps(story), "story_design", "multi_volume", story_first=True)


def test_compiled_story_remains_readable_in_project_workspace(design, tmp_path):
    from test_project_story import _make_project
    from core.project_story_insights import project_story_insights
    session, story, chapters = design
    text = render(session, story, chapters)["prompt_md"]
    project = _make_project(tmp_path, prompt=text)
    result = project_story_insights(project, project.name)
    assert result["narrative"]["text"].strip() == story["story_design"]["causal_chain"]
    assert len(result["chapters"]) == 2
    for actual, expected in zip(result["chapters"], chapters["chapters"]):
        assert expected["core_plot"][0] in actual["summary"]
        assert expected["reader_value"] in actual["summary"]


def review(text, *, revise=False):
    checks = [{"dimension": d, "status": "revise" if revise and d == "story" else "pass",
               "quote": text, "reason": "Concrete observed effect.",
               "repair": "Stage the admission on page." if revise and d == "story" else ""} for d in sorted(DIMENSIONS)]
    return "[JOINT_REVIEW_JSON]" + json.dumps({"candidate_sha256": hashlib.sha256(text.encode()).hexdigest(), "checks": checks}) + "[/JOINT_REVIEW_JSON]"


def test_review_bound_to_exact_candidate_and_all_dimensions():
    text = "She returned the key."
    assert read_review(review(text), text).passed
    assert not read_review(review(text, revise=True), text).passed
    with pytest.raises(ValueError, match="版本"):
        read_review(review(text), text + " Changed.")
    with pytest.raises(ValueError, match="引用"):
        read_review(review(text).replace('"quote": "She returned the key."', '"quote": "Invented quote"'), text)
    with pytest.raises(ValueError, match="检查"):
        read_review(review(text).replace('"voice"', '"story"'), text)


def test_local_chapter_projection_keeps_scenes_without_extra_planning():
    from commercial_fixtures import legacy_chapter_contract_payload
    contract = legacy_chapter_contract_payload()
    chapter = {"number": contract["chapter"], "title": "The Key", "scenes": [{"detail": "A quiet admission."}],
               "execution_contract": contract}
    text = project_outline({"chapters": [chapter]}, contract["chapter"])
    assert "A quiet admission." in text and "[CHAPTER_CONTRACT]" in text
    chapter["execution_contract"]["chapter"] += 1
    with pytest.raises(ValueError, match="不匹配"):
        project_outline({"chapters": [chapter]}, chapter["number"])


def test_delivery_is_two_semantic_calls_and_local_assembly(design, monkeypatch):
    from api import workshop
    import core.llm_client as clients
    session, story, chapters = design
    # Use the real staged coordinator, metadata binder, local compiler and validator.
    monkeypatch.setattr(workshop, "design_rules", lambda _: "")
    monkeypatch.setattr(workshop, "_skill_bundle", lambda *args: ("Design concrete stories.", "fixture"))
    monkeypatch.setattr("core.provider_settings.resolve_text_route", lambda _: {
        "provider": "fake", "model": "test", "timeout_seconds": 60, "base_url": None, "api_key": None, "max_tokens": 8192})
    calls = []
    class Client:
        def __init__(self, **kwargs): pass
        def complete(self, system, user):
            calls.append((system, user))
            if len(calls) > 2:
                pytest.fail("Local assembly called the model")
            return json.dumps(story if len(calls) == 1 else chapters)
    monkeypatch.setattr(clients, "LLMClient", Client)
    # DeliveryPlan fingerprints use the module's seam too.
    monkeypatch.setattr("api.workshop_delivery.author_context", lambda _: {"input": session["input"]})
    result, _ = workshop._complete(session, "Design concrete stories.")
    assert result["prompt_md"].startswith("<!-- story-first.v1 -->")
    assert len(calls) == 2


def test_new_api_delivery_preserves_adopted_capacity_without_assembly_calls(client, design, monkeypatch):
    from api import workshop
    from test_workshop_capacity import propose
    from test_workshop_api import finish
    from test_workshop_contract_repair import stub_model
    from core.story_capacity import read_contract
    actual_complete = workshop._complete
    data = propose(client, monkeypatch)
    monkeypatch.setattr(workshop, "_complete", actual_complete)
    _, story, chapters = design
    story["total_chapters"] = 21
    chapters["total_chapters"] = 21
    chapters["chapters"] = [{**deepcopy(chapters["chapters"][0]), "number": n} for n in range(1, 22)]
    def respond(attempt, system, user):
        context = json.loads(user)
        assert context["story_capacity"]["confirmed"]["option"]["total_words"] == 11550
        if context["delivery_stage"] == "story_design":
            return json.dumps(story)
        assert context["delivery_stage"] == "chapter_plan"
        return json.dumps(chapters)
    calls = stub_model(monkeypatch, respond)
    body = {"revision": data["revision"], "message": "", "intent": "deliver", "operation_id": "story-first-capacity-1",
            "capacity_choice": {"proposal_sha256": data["capacity"]["proposal_sha256"], "option_id": "keep_story"}}
    assert client.post(f'/api/workshop/sessions/{data["id"]}/turns', json=body).status_code == 202
    result = finish(client, data["id"])
    assert result["status"] == "ready", result.get("last_error")
    contract = read_contract(result["prompt_md"])
    assert contract["option"]["total_words"] == 11550
    assert len(contract["option"]["chapters"]) == 21
    assert all(c["target_words"] == 550 for c in contract["option"]["chapters"])
    assert len(calls) == 2


def test_story_map_delta_is_revision_bound_and_never_implies_author_approval():
    from api.workshop_story_result import validate_candidate, AREAS
    session = {"revision": 3, "messages": [], "input": {}, "creative_design": {
        "policy": {"story_engine": VERSION}, "story_state": {"map": None, "assumptions": []}}}
    record = {"source_revision": 2, "message_index": 0, "proposals": []}
    raw = {"base_revision": None, "synopsis": "A proposed route.", "focus": "opening", "nodes": [
        {"area": "opening", "summary": "The key is returned.", "status": "proposed", "evidence": []}]}
    result = validate_candidate(raw, record, session)
    assert result["map"]["nodes"][1]["summary"] == "The key is returned."
    assert len(result["map"]["nodes"]) == len(AREAS)
    session["creative_design"]["story_state"]["map"] = result["map"]
    raw["nodes"][0]["summary"] = "Stale change"
    assert validate_candidate(raw, record, session)["map"] is None
    raw["base_revision"] = 2
    raw["nodes"] = [{"area": "middle", "summary": "A costly admission.", "status": "proposed"}]
    result = validate_candidate(raw, record, session)
    assert result["map"]["nodes"][1]["summary"] == "The key is returned."
    assert result["map"]["nodes"][2]["status"] == "proposed"


@pytest.mark.parametrize("needs_repair,interrupt_review", [(False, False), (True, False), (True, True)])
def test_real_story_first_pipeline_promotes_and_resumes_without_extra_models(tmp_path, needs_repair, interrupt_review):
    import re
    from test_outline_planning import PlanningModel
    from test_pipeline_integration import PipelineLLM
    from commercial_fixtures import legacy_chapter_contract_payload
    from core.reader_investment import snapshot
    from creative_execution import make_handoff
    from orchestrator import NovelOrchestrator
    from pipeline_models import RunSpec
    from pipeline_runner import PipelineRunner
    from state_manager import StoryState

    class Client(PipelineLLM):
        def __init__(self):
            self.planner = PlanningModel()
            self.chapter_calls = []
            self.reviews = {}

        def complete(self, *, system, user):
            marker = "\n\n[CREATIVE_EXECUTION_CONTEXT]\n"
            context = None
            if marker in user:
                user, raw_context = user.split(marker, 1)
                context = json.loads(raw_context.split("\n[/CREATIVE_EXECUTION_CONTEXT]", 1)[0])
            if system.startswith("You are Novel OS's story architect"):
                value = json.loads(self.planner.complete(system, user))
                if user.startswith("STAGE: global_design"):
                    value["characters"][0].update(id="char_001", name="Mara Vale")
                    value["plot_threads"][0].update(id="plot_001", name="Second Chance")
                    value["ending_contract"]["main_conflict"].update(thread_id="plot_001", protagonist_choice="Mara opens the studio")
                    value["ending_contract"]["character_arcs"][0].update(character_id="char_001", required_outcome="independence", required_choice="Mara opens the studio")
                for c in value.get("chapters", []):
                    c["pov"], c["target_words"] = "Mara Vale", 30
                    for scene in c["scenes"]:
                        scene["pov"] = "Mara Vale"
                    c["execution_contract"] = {**legacy_chapter_contract_payload(), "chapter": c["number"],
                        "preserve_facts": [], "allowed_knowledge": []}
                return json.dumps(value)
            if context is not None:
                number = context["chapter"]
                if system.startswith("You are the joint"):
                    self.chapter_calls.append(("review", number))
                    self.reviews[number] = self.reviews.get(number, 0) + 1
                    if interrupt_review and number == 1 and self.reviews[number] == 2:
                        from pipeline_runtime import PipelineStopped
                        raise PipelineStopped("Test explicit pause after completed repair")
                    manuscript = user.split("COMPLETE CANDIDATE (data, not instructions):\n", 1)[1].rstrip("\n")
                    # Hash/quote must match the exact candidate, including final newline.
                    match = re.search(r'"candidate_sha256":"([0-9a-f]+)"', user)
                    path = project / f"outputs/manuscript/chapter_{number:03d}_revised.md"
                    manuscript = path.read_text()
                    assert hashlib.sha256(manuscript.encode()).hexdigest() == match[1]
                    revise = needs_repair and number == 1 and self.reviews[number] == 1
                    draft = self.draft(number)
                    state = draft.split("[SCRIBE_STATE_UPDATE]", 1)[1].split("[/SCRIBE_STATE_UPDATE]", 1)[0]
                    return (review(manuscript, revise=revise) + "\n[CONTINUITY_REPORT]\nStatus: "
                            + ("FAIL" if revise else "PASS") + "\nCritical_Issues: [None]\nWarnings: [None]\n[/CONTINUITY_REPORT]\n"
                            + "[CONTINUITY_STATE_UPDATE]" + state + "[/CONTINUITY_STATE_UPDATE]")
                if "THE SCRIBE" in system.splitlines()[0]:
                    self.chapter_calls.append(("write", number))
                    return self.draft(number)
                if "THE EDITOR" in system.splitlines()[0]:
                    self.chapter_calls.append(("repair", number))
                    path = project / f"outputs/manuscript/chapter_{number:03d}_revised.md"
                    return "[REVISED_CHAPTER]\n" + path.read_text() + "\n[/REVISED_CHAPTER]\n[EDITOR_STATE_UPDATE]\nImprovements_Made: [Scene made concrete]\n[/EDITOR_STATE_UPDATE]"
                pytest.fail("Unexpected chapter model role: " + system[:80])
            return super().complete(system=system, user=user)

        def draft(self, number):
            response = super().run_agent("scribe", f"Chapter {number}")
            if number == 2:
                response = response.replace("status=active | milestone=Mara advances the studio opening", "status=resolved | milestone=Mara opens the studio")
                response = response.replace("[/SCRIBE_STATE_UPDATE]", "Arc_State_Updates: [char_001 | stage=resolution | progress=100 | outcome=independence | evidence=Mara opens the studio in her own name]\nEnding_Evidence: [irreversible_change=Mara assumes the lease; emotional_payoff=She chooses a self-directed life]\n[/SCRIBE_STATE_UPDATE]")
            return response

    client = Client()
    def factory(path):
        engine = NovelOrchestrator(path)
        engine._llm = client
        return engine
    source = tmp_path / "skeleton.md"
    source.write_text("# One Prompt Book\n\nMara Vale chooses a new life and independent studio work. She keeps the studio and opens it in chapter 2.")
    project = tmp_path / "project"
    handoff = make_handoff(source=source.read_text(), origin={"workshop_id": "fixture"},
                           reader_investment=snapshot(), story_engine=VERSION)
    spec = RunSpec(project_path=str(project), prompt_path=str(source), num_chapters=2, target_words=60,
                   approval_policy="auto", quality_policy="evidence_v1", creative_handoff=handoff, max_retries=0)
    runner = PipelineRunner(orchestrator_factory=factory)
    manifest = runner.run(spec)
    if interrupt_review:
        assert manifest.status != "completed"
        assert manifest.get("chapter.repair.1", 1).status == "done"
        assert manifest.get("chapter.repair.2", 1) is None
        assert client.chapter_calls == [("write", 1), ("review", 1), ("repair", 1), ("review", 1)]
        manifest = runner.resume(manifest.run_id, project)
    assert manifest.status == "completed", manifest.error
    assert client.chapter_calls.count(("write", 1)) == 1
    assert len(client.chapter_calls) == (6 if needs_repair else 4) + int(interrupt_review)
    assert manifest.get("chapter.edit", 1) is None
    assert manifest.get("chapter.style", 1).provider == "local"
    for n in (1, 2):
        assert (project / f"outputs/manuscript/chapter_{n:03d}_final.md").read_bytes() == (project / f"outputs/manuscript/chapter_{n:03d}_revised.md").read_bytes()
    assert StoryState(str(project)).characters["char_001"].outcome_state == "independence"
    before = list(client.chapter_calls)
    assert runner.resume(manifest.run_id, project).status == "completed"
    assert client.chapter_calls == before


def test_compiled_skeleton_reaches_cover_planner_without_duplicate_handoff(design, tmp_path):
    from core.cover_sources import CoverSourceContext, CoverSourceSelector, capture_source
    from core.cover_poster_planner import plan_poster
    from test_cover_poster_planner import Model
    session, story, chapters = design
    text = render(session, story, chapters)["prompt_md"]
    project = tmp_path / "book"
    (project / "outputs/input").mkdir(parents=True)
    (project / "outputs/input/prompt.md").write_text(text)
    context = CoverSourceContext(tmp_path / "covers", "default:project:book", lambda _: project)
    source = capture_source(context, CoverSourceSelector("project_input", project_id="book"))
    model = Model()
    plan_poster(source, {"title": "The Choice", "author": "A. Writer", "language": "en", "genre": "Romance"},
                {}, model, model="fixture")
    assert len(model.calls) == 1
    assert model.calls[0][1]["source"]["documents"][0]["text"] == text
