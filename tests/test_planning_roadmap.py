"""Exercise the staged public planner and recoverable long-book global contract."""
import hashlib
import json
from dataclasses import dataclass

import pytest

from core.gemini_gateway import input_bytes
from core.planning_roadmap import MAX_RANGE
from narrative_format import infer_narrative_format
from novel_classification import infer_classification
from orchestrator import NovelOrchestrator
from outline_planning import CHAPTER_TEXT, OutlinePlanningError, content_hash
from test_outline_planning import PlanningModel, global_design, make_planner, review


@dataclass(frozen=True)
class Story:
    genre: str
    witnesses: tuple
    secret: str
    format: str
    volumes: int

    def evidence(self, number):
        witness = self.witnesses[(number - 1) % len(self.witnesses)]
        return f"{witness} records the cost of decision {number}."

    def source(self, count):
        chapters = "\n\n".join(
            f"### Chapter {n} — Testimony {n}\n\n{self.evidence(n)}" for n in range(1, count + 1)
        )
        return "\n\n".join(f"## {n}. Section\n\n" + (chapters if n == 12 else self.secret)
                            for n in range(1, 18))


STORIES = (
    Story("Mystery", ("Ana",), "The alibi is a recording. Reveal its date only in the finale.", "standalone_long", 1),
    Story("Fantasy", ("Kei",), "The west gate demands the crown, but no lives.", "multi_volume", 4),
    Story("Science Fiction", ("Ira", "Bo"), "The witnesses remember the signal in reverse order.", "multi_volume", 5),
)


class StoryModel:
    provider, model = "fake", "roadmap-fixture"

    def __init__(self, story, *, fail_at=None):
        self.story = story
        self.calls = []
        self.seed_conflict = None
        self.request_bytes = []
        self.fail_at = fail_at

    def complete(self, system, user):
        self.request_bytes.append(input_bytes(system, user))
        if getattr(self, "planning_input_bytes", None):
            assert self.request_bytes[-1] <= self.planning_input_bytes
        stage = user.splitlines()[0].removeprefix("STAGE: ")
        context = json.loads(user.rsplit("\n\n", 1)[1])
        self.calls.append((stage, context))
        story = self.story
        if stage == "global_design_seed":
            text = context.get("complete_adopted_source") or context["adopted_source_projection"]["text"]
            seed = global_design({**context, "complete_adopted_source": text})
            del seed["chapter_map"]
            seed.update(premise=story.secret, title=f"{story.genre} testimony", logline=story.secret,
                        causal_chain=[story.secret], plot_threads=[{"id": "case", "name": story.secret}],
                        source_anchors=[{"id": "rule", "quote": story.secret}],
                        relationships=[], characters=[{"id": name, "name": name, "role": "witness"}
                                                      for name in story.witnesses])
            seed["ending_contract"]["main_conflict"].update(thread_id="case", consequence=story.secret)
            seed["ending_contract"]["character_arcs"][0]["character_id"] = story.witnesses[0]
            return json.dumps(seed)
        if stage == "semantic_review":
            return json.dumps(review())
        numbers = context["requested_chapters"]
        if stage == "chapter_roadmap":
            if numbers[0] == self.fail_at:
                raise TimeoutError("simulated provider lost its connection")
            rows = [{"number": n, "purpose": story.evidence(n),
                     "incoming_consequence": story.evidence(n - 1) if n > 1 else story.secret,
                     "outgoing_commitment": story.evidence(n), "source_anchor_ids": [f"ev_{n}"],
                     "reserved_reveals": [story.secret] if n == 1 else [], "ending_obligations": []}
                    for n in numbers]
            if self.seed_conflict in numbers:
                n = self.seed_conflict
                return json.dumps({"seed_conflicts": [{"chapter": n, "message": "The seed gives this memory to the other witness.",
                                                        "source_quote": story.evidence(n)}]})
            return json.dumps({"chapter_map": rows, "source_anchors": [
                {"id": f"ev_{n}", "quote": story.evidence(n)} for n in numbers],
                **({"seed_conflicts": []} if "seed_conflicts" in user.split("\n\n{")[0] else {})})
        assert stage == "chapter_batch"
        chapters = []
        for n in numbers:
            witness = story.witnesses[(n - 1) % len(story.witnesses)]
            line = story.evidence(n)
            chapters.append({**dict.fromkeys(CHAPTER_TEXT, line), "number": n, "pov": witness,
                             "target_words": 1000, "continuity_obligations": [story.secret],
                             "setups": [], "payoffs": [], "reserved_later_payoffs": [],
                             "source_anchor_ids": [f"ev_{n}"],
                             "scenes": [{"pov": witness, "goal": line, "conflict": line, "change": line}]})
        return json.dumps({"chapters": chapters})


@pytest.mark.parametrize("story", STORIES, ids=["single-volume-mystery", "four-volume-fantasy", "dual-pov-scifi"])
def test_hundred_chapters_publish_through_orchestrator_entry(tmp_path, story):
    orch = NovelOrchestrator(tmp_path, state_update_mode="proposal_only")
    model = StoryModel(story)
    orch._llm = model
    source = story.source(100)
    classification = infer_classification(genre=story.genre, premise=story.secret)
    fmt = infer_narrative_format(classification, chapters=100, target_words=100000,
                                 mode=story.format, volume_count=story.volumes, explicit_length=True)
    inputs = orch.outputs_dir / "input"
    inputs.mkdir(exist_ok=True)
    (inputs / "prompt.md").write_text(source)
    (inputs / "brief.json").write_text(json.dumps({"classification": classification.to_dict(),
                                                    "narrative_format": fmt.to_dict()}))
    orch.plan_outline(100, 100000)
    foundation = json.loads((inputs / "foundation.json").read_text())
    assert foundation["premise"] == story.secret
    assert [c["name"] for c in foundation["characters"]] == list(story.witnesses)
    assert len(foundation["volume_contracts"]) == story.volumes
    assert len(foundation["chapter_map"]) == len(foundation["chapters"]) == 100
    assert [c["source_anchor_ids"] for c in foundation["chapters"]] == [[f"ev_{n}"] for n in range(1, 101)]
    assert dict((anchor["id"], anchor["quote"]) for anchor in foundation["source_anchors"]) == {
        "rule": story.secret, **{f"ev_{n}": story.evidence(n) for n in range(1, 101)}}
    assert model.calls[0][0] == "global_design_seed"
    assert model.calls[0][1]["complete_adopted_source"] == source
    ranges = [c for stage, c in model.calls if stage == "chapter_roadmap"]
    assert [n for context in ranges for n in context["requested_chapters"]] == list(range(1, 101))
    for context in ranges:
        requested = context["requested_chapters"]
        first = requested[0]
        assert len(requested) <= MAX_RANGE
        assert context["frozen_global_design"]["premise"] == story.secret
        assert "chapter_map" not in context["frozen_global_design"]
        if first > 1:
            assert context["previous_roadmap_entry"]["outgoing_commitment"] == story.evidence(first - 1)
            assert context["earlier_obligations"][0]["reserved_reveals"] == [story.secret]
        projection = context["adopted_source_projection"]
        assert projection["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
        for n in requested:
            assert story.evidence(n) in projection["text"]
    published = orch.outputs_dir / "standalone/planning/progress.json"
    assert json.loads(published.read_text())["status"] == "complete"


def test_interrupted_roadmap_resumes_partial_sixty_chapter_book(tmp_path):
    story = STORIES[0]
    model = StoryModel(story, fail_at=9)
    planner = make_planner(tmp_path, model, count=60, source=story.source(60))
    with pytest.raises(TimeoutError):
        planner.run()
    assert not (planner.cache / "global.json").exists()
    originals = {p: p.read_bytes() for p in planner.cache.glob("*.json") if p.name != "identity.json"}
    assert "global-seed-v1.json" in [p.name for p in originals]
    assert "roadmap-v1-001-008.json" in [p.name for p in originals]
    continued = StoryModel(story)
    result = make_planner(tmp_path, continued, count=60, source=planner.source).run()
    assert len(result["chapter_map"]) == 60
    assert continued.calls[0][0] == "chapter_roadmap"
    assert continued.calls[0][1]["requested_chapters"][0] == 9
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    completed = StoryModel(story)
    assert make_planner(tmp_path, completed, count=60, source=planner.source).run() == result
    assert completed.calls == []


def test_historical_completed_global_response_promotes_without_seed_or_roadmap(tmp_path):
    client = PlanningModel()
    planner = make_planner(tmp_path, client, count=60)
    context = {**planner._context(), "volume_contract_template": planner.volume_template}
    old_response = json.dumps(global_design(context))
    prompt = planner.global_prompt + "\n\n" + json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    planner._save("global-response", {"prompt_hash": content_hash(prompt)}, {"raw": old_response})
    record = (planner.cache / "global-response.json").read_bytes()
    result = planner.run()
    assert len(result["chapter_map"]) == 60 and client.calls[0][0] == "batch"
    assert not any(stage in {"global", "seed", "roadmap"} for stage, _ in client.calls)
    assert (planner.cache / "global-response.json").read_bytes() == record


def test_oversized_source_fails_before_seed_dispatch_and_retains_original(tmp_path):
    model = StoryModel(STORIES[0])
    model.planning_input_bytes = 8192
    source = "A distant fact with an exception. " * 5000
    planner = make_planner(tmp_path, model, count=100, source=source)
    with pytest.raises(OutlinePlanningError, match="capacity"):
        planner.run()
    assert model.calls == [] and planner.source == source
    assert not (planner.cache / "global.json").exists()


def large_adopted_source(story):
    source = story.source(100)
    for n in range(1, 101):
        evidence = story.evidence(n)
        source = source.replace(evidence, evidence + " " + (f"Chapter {n} retains this exact condition. " * 54))
    return source + "\nOutside the numbered map, this qualifier applies to the ending."


def test_scoped_seed_covers_full_source_across_small_connection_and_review(tmp_path):
    story = STORIES[2]
    source = large_adopted_source(story)
    model = StoryModel(story)
    model.planning_input_bytes = 100000
    assert len(source.encode()) > 2 * model.planning_input_bytes
    planner = make_planner(tmp_path, model, count=100, source=source)
    result = planner.run()
    scope = result["roadmap_source_coverage"]
    assert scope["covered_chapters"] == 100
    assert scope["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    seed_view = model.calls[0][1]["adopted_source_projection"]
    assert seed_view["mode"] == "global_sections_and_chapter_titles"
    assert seed_view["chapter_bodies_deferred"] == list(range(1, 101))
    assert all(f"### Chapter {n} — Testimony {n}\n" in seed_view["text"] for n in range(1, 101))
    assert "Outside the numbered map" in seed_view["text"] and "retains this exact condition" not in seed_view["text"]
    for stage in ("chapter_roadmap", "chapter_batch", "semantic_review"):
        seen = []
        for label, context in model.calls:
            if label != stage:
                continue
            requested = (context["review_window"]["requested_chapters"] if stage == "semantic_review"
                         else context["requested_chapters"])
            seen.extend(requested)
            text = context["adopted_source_projection"]["text"]
            assert "Outside the numbered map" in text
            for n in requested:
                assert story.evidence(n) + " " + (f"Chapter {n} retains this exact condition. " * 54) in text
        assert seen == list(range(1, 101))
    resumed = StoryModel(story)
    resumed.planning_input_bytes = 500000
    assert make_planner(tmp_path, resumed, count=100, source=source).run() == result
    assert resumed.calls == []


def test_material_conflict_in_deferred_source_blocks_publication_and_survives_resume(tmp_path):
    story = STORIES[1]
    source = large_adopted_source(story)
    model = StoryModel(story)
    model.planning_input_bytes = 100000
    model.seed_conflict = 9
    planner = make_planner(tmp_path, model, count=100, source=source)
    with pytest.raises(ValueError, match="conflicts.*chapter 9"):
        planner.run()
    assert not (planner.cache / "global.json").exists()
    resumed = StoryModel(story)
    with pytest.raises(ValueError, match="conflicts.*chapter 9"):
        make_planner(tmp_path, resumed, count=100, source=source).run()
    assert resumed.calls == []


def test_ambiguous_large_skeleton_preserves_full_original_and_stops_before_dispatch(tmp_path):
    story = STORIES[1]
    source = large_adopted_source(story).replace("### Chapter 8 —", "### Ambiguous header —")
    model = StoryModel(story)
    model.planning_input_bytes = 100000
    planner = make_planner(tmp_path, model, count=100, source=source)
    with pytest.raises(OutlinePlanningError, match="capacity"):
        planner.run()
    assert model.calls == [] and planner.source == source


def test_oversized_range_bisects_before_dispatch_and_keeps_partition_after_cap_grows(tmp_path):
    story = STORIES[2]
    source = large_adopted_source(story)
    for n in range(1, 11):
        source = source.replace(story.evidence(n), story.evidence(n) + (" exact detail" * 1100))
    interrupted = StoryModel(story, fail_at=5)
    interrupted.planning_input_bytes = 100000
    first = make_planner(tmp_path, interrupted, count=100, source=source)
    with pytest.raises(TimeoutError):
        first.run()
    assert (first.cache / "roadmap-v1-001-008-split.json").exists()
    attempted = [c["requested_chapters"] for s, c in interrupted.calls if s == "chapter_roadmap"]
    assert attempted[0] == [1, 2, 3, 4]
    assert attempted[1][0] == 5 and len(attempted[1]) <= 4
    continued = StoryModel(story)
    continued.planning_input_bytes = 500000
    result = make_planner(tmp_path, continued, count=100, source=source).run()
    assert continued.calls[0][1]["requested_chapters"] == attempted[1]
    assert result["roadmap_source_coverage"]["covered_chapters"] == 100


def test_short_book_with_large_source_uses_same_capacity_policy(tmp_path):
    story = STORIES[0]
    source = story.source(32)
    for n in range(1, 33):
        source = source.replace(story.evidence(n), story.evidence(n) + (" exact detail" * 330))
    client = StoryModel(story)
    client.planning_input_bytes = 76000
    planner = make_planner(tmp_path, client, count=32, source=source)
    result = planner.run()
    assert result["roadmap_source_coverage"]["covered_chapters"] == 32
    assert client.calls[0][0] == "global_design_seed"


class DamagedRoadmap(StoryModel):
    def __init__(self, story, broken, **kwargs):
        super().__init__(story, **kwargs)
        self.broken = broken

    def complete(self, system, user):
        context = json.loads(user.rsplit("\n\n", 1)[1])
        if user.startswith("STAGE: format_correction"):
            self.calls.append(("correction", context))
            return context["completed_response"]
        if user.startswith("STAGE: chapter_roadmap"):
            numbers = context["requested_chapters"]
            raw = self.broken(numbers)
            if raw is not None:
                self.calls.append(("broken", numbers))
                return raw
        return super().complete(system, user)


def test_twice_truncated_range_splits_and_never_overwrites_replies_on_resume(tmp_path):
    story = STORIES[1]
    def truncate_large_range(numbers):
        return '{"chapter_map": [' if numbers == list(range(1, 9)) else None

    broken = DamagedRoadmap(story, truncate_large_range, fail_at=5)
    initial = make_planner(tmp_path, broken, count=60, source=story.source(60))
    with pytest.raises(TimeoutError):
        initial.run()
    assert [s for s, _ in broken.calls] == ["global_design_seed", "broken", "correction", "chapter_roadmap", "chapter_roadmap"]
    name = "roadmap-v1-001-008"
    originals = {p: p.read_bytes() for p in initial.cache.glob(f"{name}*.json")}
    assert {p.name for p in originals} == {f"{name}-response.json", f"{name}-format-correction-response.json", f"{name}-split.json"}
    continued = StoryModel(story)
    result = make_planner(tmp_path, continued, count=60, source=initial.source).run()
    assert len(result["chapter_map"]) == 60
    assert continued.calls[0][1]["requested_chapters"] == [5, 6, 7, 8]
    assert all(p.read_bytes() == data for p, data in originals.items())


@pytest.mark.parametrize("raw,single", [('{"chapter_map":[', True), ('{"chapter_map":[],"source_anchors":[]}', False)])
def test_only_repeatedly_unparseable_and_divisible_outputs_are_split(tmp_path, raw, single):
    story = STORIES[2]
    broken = DamagedRoadmap(story, lambda ns: raw)
    planner = make_planner(tmp_path, broken, count=60, source=story.source(60))
    with pytest.raises(OutlinePlanningError, match="remains invalid"):
        planner.run()
    requests = [numbers for stage, numbers in broken.calls if stage == "broken"]
    assert requests[-1] == ([1] if single else list(range(1, 9)))
    assert not (planner.cache / "roadmap-v1-001-001-split.json").exists()
    assert not (planner.cache / "global.json").exists()


def test_malformed_historical_global_replies_transition_to_bounded_generation(tmp_path):
    story = STORIES[2]
    model = DamagedRoadmap(story, lambda ns: None, fail_at=9)
    source = story.source(60)
    planner = make_planner(tmp_path, model, count=60, source=source)
    context = {**planner._context(), "volume_contract_template": planner.volume_template}
    prompt = planner.global_prompt + "\n\n" + json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    planner._save("global-response", {"prompt_hash": content_hash(prompt)}, {"raw": '{"chapter_map":['})
    with pytest.raises(TimeoutError):
        planner.run()
    assert [stage for stage, _ in model.calls[:2]] == ["correction", "global_design_seed"]
    assert (planner.cache / "global-format-correction-response.json").is_file()
    original = (planner.cache / "global-response.json").read_bytes()
    resumed = StoryModel(story)
    assert len(make_planner(tmp_path, resumed, count=60, source=source).run()["chapter_map"]) == 60
    assert resumed.calls[0][0] == "chapter_roadmap"
    assert resumed.calls[0][1]["requested_chapters"][0] == 9
    assert (planner.cache / "global-response.json").read_bytes() == original


def test_full_source_seed_but_larger_batches_and_review_use_exact_local_source(tmp_path):
    story = STORIES[1]
    source = story.source(100)
    def chapter_text(n):
        return story.evidence(n) + " " + (f"Keep the exact original qualifier for {n}. " * 15)
    for n in range(1, 101):
        source = source.replace(story.evidence(n), chapter_text(n))
    source += "\nA global qualifier after the chapter map belongs to the entire book."

    class InterruptedReview(StoryModel):
        def complete(self, system, user):
            if user.startswith("STAGE: semantic_review") and any(s == "semantic_review" for s, _ in self.calls):
                raise TimeoutError("interrupted after the first verified scoped window")
            return super().complete(system, user)

    interrupted = InterruptedReview(story)
    interrupted.planning_input_bytes = 90000
    planner = make_planner(tmp_path, interrupted, count=100, source=source)
    with pytest.raises(TimeoutError, match="first verified scoped window"):
        planner.run()
    assert interrupted.calls[0][0] == "global_design_seed"
    assert interrupted.calls[0][1]["complete_adopted_source"] == source
    assert not planner.provisional_seed
    manifest_path, = planner.cache.glob("review-initial-source-v1-*-ranges.json")
    preflight = json.loads(manifest_path.read_bytes())["payload"]["requested_ranges"]
    assert len(preflight) > 1
    saved, = [p for p in planner.cache.glob("review-initial-source-v1-*-window-*.json") if "-response" not in p.name]
    first_receipt = saved.read_bytes()
    # Legacy ordinals belong to a different source exposure. Leave those byte
    # addresses intact even when equivalent chapter numbers are checked.
    legacy_path = planner.cache / "review-initial-window-001.json"
    legacy_path.write_text('{"historical":"complete-source receipt"}\n')
    continued = StoryModel(story)
    continued.planning_input_bytes = 1000000
    result = make_planner(tmp_path, continued, count=100, source=source).run()
    assert len(result["chapters"]) == 100 and "roadmap_source_coverage" not in result
    assert [ctx["review_window"]["requested_chapters"] for stage, ctx in continued.calls] == preflight[1:]
    assert saved.read_bytes() == first_receipt and legacy_path.read_text() == '{"historical":"complete-source receipt"}\n'
    for wanted_stage in ("chapter_batch", "semantic_review"):
        covered = []
        for stage, context in interrupted.calls + continued.calls:
            if stage != wanted_stage:
                continue
            numbers = (context["requested_chapters"] if stage == "chapter_batch"
                       else context["review_window"]["requested_chapters"])
            projection = context["adopted_source_projection"]
            assert all(chapter_text(n) in projection["text"] for n in numbers)
            assert "A global qualifier after the chapter map" in projection["text"]
            if stage == "semantic_review":
                assert projection["included_chapters"] == context["review_window"]["included_chapters"]
                assert len(context["candidate"]["chapter_map"]) == 100
            covered.extend(numbers)
        assert covered == list(range(1, 101))
