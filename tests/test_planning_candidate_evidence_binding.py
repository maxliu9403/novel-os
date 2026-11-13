"""Exact JSON scalar evidence for a coordinated repair of the saved outline."""
from copy import deepcopy
import json

import pytest

from core import planning_evidence_plan as evidence
from test_outline_coordinated_repair import QUOTE, CoordinatedModel, original_checkpoints
from test_outline_planning import SOURCE, make_planner, review


CLIMAX = "/volume_contracts/0/climax_chapter"
DEADLINE = "/volume_contracts/0/obligations/2/due_chapter"
AUTHOR_QUOTE = "Mara refuses the sale publicly in chapter 8."
STORY = SOURCE + " " + AUTHOR_QUOTE
GLOBAL = {"scope": "global", "message": "Give the volume climax and its derived deadline the adopted chapter.",
          "finding_basis": "source_departure", "source_evidence": AUTHOR_QUOTE}
CHAPTER = {"scope": "chapter", "chapter": 3, "message": "Preserve the refusal during the chapter's opening.",
           "finding_basis": "source_departure", "source_evidence": QUOTE}


def issue(index, citations, *, chapter_writes=()):
    return {"issue_index": index, "classification": "contradiction",
            "reason": "The wrong reference needs correction." if index == 0 else "Restore the refusal.",
            "affected_chapters": [8, 11] if index == 0 else [3], "writable_chapters": list(chapter_writes),
            "dependency_chapters": [], "foundation_repairs": [],
            "source_quotes": [AUTHOR_QUOTE if index == 0 else QUOTE], "candidate_evidence": citations}


def cite(ref):
    candidate = {"chapter_map": [{"number": n} for n in range(1, 13)],
                 "stats": {"marker": 11, "open": True, "unset": None, "ratio": 0.5, "move/now~": 1},
                 "chapters": [{"number": 1}], "assumptions": ["Mara owes $11."]}
    item = issue(0, [ref], chapter_writes=[8])
    item["classification"] = "continuity_gap"
    return evidence.validate_plan(candidate, {"issues": [GLOBAL]}, STORY, {"issues": [item]})


@pytest.mark.parametrize(("field", "evidence_value", "canonical"), [
    ("/stats/marker", {"value": 11}, "11"),
    ("/stats/marker", {"quote": "11"}, "11"),
    ("/stats/marker", {"quote": '"marker":11'}, "11"),
    ("/stats/open", {"value": True}, "true"),
    ("/stats/unset", {"value": None}, "null"),
    ("/stats/ratio", {"value": 0.5}, "0.5"),
    ("/stats/move~1now~0", {"value": 1}, "1"),
])
def test_scalar_evidence_keeps_the_address_and_verifies_the_exact_json_value(field, evidence_value, canonical):
    ref = {"path": field, **evidence_value}
    original = deepcopy(ref)
    result = cite(ref)
    assert result["issues"][0]["candidate_evidence"] == [{"path": field, "quote": canonical}]
    assert ref == original


@pytest.mark.parametrize("ref", [
    {"path": "/stats/marker", "quote": "1"},
    {"path": "/stats/marker", "quote": '"unrelated":11'},
    {"path": "/stats/marker", "value": "11"},
    {"path": "/stats/move~1now~0", "value": True},
    {"path": "/stats/open", "value": 1},
    {"path": "/stats", "value": {"marker": 11}},
    {"path": "/stats/absent", "value": None},
    {"path": "/chapters/0/number", "value": 1},
    {"path": "/chapter_map/0/number", "quote": "1"},
    {"path": "/assumptions/0", "value": 11},
    {"path": "/stats/marker", "value": 11, "quote": "11"},
])
def test_citation_cannot_change_type_use_another_member_or_quote_an_omitted_chapter(ref):
    with pytest.raises(ValueError):
        cite(ref)


def test_only_the_candidate_actually_visible_to_the_model_can_be_cited():
    candidate = {"chapter_map": [{"number": n} for n in range(1, 13)], "hidden": {"number": 11}}
    proposal = {"issues": [issue(0, [{"path": "/hidden/number", "value": 11}], chapter_writes=[8])]}
    with pytest.raises(ValueError, match="candidate_evidence"):
        evidence.validate_plan(candidate, {"issues": [GLOBAL]}, STORY, proposal, visible_candidate={"chapters": []})


class VolumeRepair(CoordinatedModel):
    def __init__(self, *, global_change=None, corrected_plan=None):
        self.proposal = {"issues": [
            issue(0, [{"path": CLIMAX, "value": 11}, {"path": DEADLINE, "value": 11}]),
            issue(1, [], chapter_writes=[3]),
        ]}
        self.proposal["issues"][0]["writable_global_references"] = [CLIMAX]
        self.corrected_plan = corrected_plan
        self.global_change = global_change
        super().__init__(reviews=[review(GLOBAL, CHAPTER), review()], plan=self.proposal)

    def complete(self, system, user):
        if user.startswith("STAGE: coordinated_global_references"):
            ctx = json.loads(user.rsplit("\n\n", 1)[1])
            self.calls.append(("global_references", ctx))
            assert ctx["complete_adopted_source"] == STORY
            assert set(ctx["allowed_references"]) == {CLIMAX}
            response = {"edits": [{"path": CLIMAX, "before": 11, "after": 8, "source_quote": AUTHOR_QUOTE}],
                        "remaining_writable_chapters": [], "reason": "Only the global reference needs to move."}
            if self.global_change:
                self.global_change(response)
            return json.dumps(response)
        if self.corrected_plan and user.startswith("STAGE: format_correction"):
            self.calls.append(("format", json.loads(user.rsplit("\n\n", 1)[1])))
            return json.dumps(self.corrected_plan)
        return super().complete(system, user)


def read_payload(cache, name):
    return json.loads((cache / (name + ".json")).read_bytes())["payload"]


def archived_attempt(root, mutate_latest=None):
    """Journal the old plan and its correction with the original stage's wire contract."""
    from core.planning_repair_context import build_plan_context
    from outline_planning import OutlinePlanningError

    first = VolumeRepair()
    del first.proposal["issues"][0]["writable_global_references"]
    first.proposal["issues"][0]["candidate_evidence"] = [
        {"path": CLIMAX, "quote": "11"}, {"path": DEADLINE, "quote": "11"}]
    latest = deepcopy(first.proposal)
    latest["issues"][0].update(writable_chapters=[8], candidate_evidence=[
        {"path": CLIMAX, "quote": '"climax_chapter":11'}, {"path": DEADLINE, "quote": '"due_chapter":11'}])
    # A change with an observable effect makes using the original reply instead of the latest detectable.
    latest["issues"][1]["dependency_chapters"] = [6]
    if mutate_latest:
        mutate_latest(latest)
    first.corrected_plan = latest
    planner = make_planner(root, first, count=12, source=STORY)

    def old_round(candidate, findings, **kwargs):
        ctx = build_plan_context(candidate, findings, STORY, planner.brief, planner.locked,
                                 {"target_words": planner.target_words})

        def former_validator(value):
            errors = [] if value["issues"][0]["writable_chapters"] else [
                "issues[0].writable_chapters must identify the actual material correction"]
            errors.extend(f"issues[0].candidate_evidence[{i}] must quote the declared visible candidate field exactly"
                          for i in (0, 1))
            raise ValueError("\n".join(errors))

        try:
            planner._call(evidence.VERSION + "-plan", evidence.PLAN_INSTRUCTION, ctx, former_validator,
                          strict_dependencies=True, recover_archived=True)
        except OutlinePlanningError as exc:
            planner._needs_attention(str(exc))
            raise

    planner._coordinate = old_round
    with pytest.raises(OutlinePlanningError, match="must quote the declared visible candidate field exactly"):
        planner.run()
    snapshots = {planner.cache / name: content for name, content in original_checkpoints(planner).items()}
    for path in planner.cache.rglob(evidence.VERSION + "-plan*"):
        if path.is_file():
            snapshots[path] = path.read_bytes()
    assert [s for s, _ in first.calls][-3:] == ["review", "coordinated_plan", "format"]
    return snapshots


@pytest.mark.parametrize("history", [False, True], ids=["new-plan", "saved-correction"])
def test_mixed_repair_changes_only_the_volume_reference_and_the_separately_writable_chapter(tmp_path, history):
    snapshots = archived_attempt(tmp_path) if history else {}
    model = VolumeRepair()
    if history:
        model.reviews = [review()]  # Initial blocked review already saved.
    planner = make_planner(tmp_path, model, count=12, source=STORY)
    result = planner.run()
    stages = [stage for stage, ctx in model.calls]
    assert stages.count("global_references") == 1 and stages.count("coordinated_group") == 1
    assert stages.count("coordinated_plan") == (0 if history else 1)
    assert stages.count("format") == 0 and stages[-1] == "review"
    writer = next(ctx for s, ctx in model.calls if s == "coordinated_group")
    assert writer["requested_chapters"] == [3]
    if history:
        assert 6 in writer["repair_scope"]["readonly_chapters"]
    volume = result["volume_contracts"][0]
    original = read_payload(planner.cache, "global")["volume_contracts"][0]
    expected = deepcopy(original)
    expected["climax_chapter"] = expected["obligations"][2]["due_chapter"] = 8
    assert original["climax_chapter"] == 11 and volume == expected
    checked = model.calls[-1][1]
    assert checked["candidate"]["volume_contracts"][0] == expected
    for number in range(1, 13):
        batch = read_payload(planner.cache, "batch-" + str((number - 1) // 4 + 1).zfill(3))
        before = batch["chapters"][(number - 1) % 4]
        after = result["chapters"][number - 1]
        if number == 3:
            assert after["incoming_consequence"] != before["incoming_consequence"]
        else:
            assert all(after[key] == value for key, value in before.items())
    replay = VolumeRepair()
    assert make_planner(tmp_path, replay, count=12, source=STORY).run() == result
    assert not replay.calls
    assert all(path.read_bytes() == content for path, content in snapshots.items())


def test_invalid_newer_correction_cannot_be_replaced_by_now_parseable_original(tmp_path):
    from outline_planning import OutlinePlanningError

    def fabricated(latest):
        latest["issues"][0]["candidate_evidence"][0]["quote"] = '"climax_chapter":12'
    snapshots = archived_attempt(tmp_path, fabricated)
    model = VolumeRepair()
    with pytest.raises(OutlinePlanningError):
        make_planner(tmp_path, model, count=12, source=STORY).run()
    assert not model.calls and all(path.read_bytes() == content for path, content in snapshots.items())


@pytest.mark.parametrize("violation", ["derived", "other-chapter", "false-source", "wrong-before"])
def test_cited_number_cannot_grant_unrelated_writes_or_replace_author_evidence(tmp_path, violation):
    from outline_planning import OutlinePlanningError

    def widen(response):
        if violation == "derived":
            response["edits"][0]["path"] = DEADLINE
        elif violation == "other-chapter":
            response["remaining_writable_chapters"] = [11]
        elif violation == "false-source":
            response["edits"][0]["source_quote"] = "Never stated in the adopted skeleton."
        else:
            response["edits"][0]["before"] = 8
    model = VolumeRepair(global_change=widen)
    planner = make_planner(tmp_path, model, count=12, source=STORY)
    with pytest.raises(OutlinePlanningError):
        planner.run()
    assert any(s == "global_references" for s, _ in model.calls)
    assert not (planner.cache / "validated.json").exists()
    assert read_payload(planner.cache, "global")["volume_contracts"][0]["climax_chapter"] == 11
    for stage, ctx in model.calls:
        if stage == "coordinated_group":
            assert ctx["requested_chapters"] == [3]


@pytest.mark.parametrize("permission", [[], [DEADLINE], ["/volume_contracts/0/chapter_end"], [CLIMAX, CLIMAX]])
def test_explicit_global_write_scope_cannot_be_inferred_from_citations(tmp_path, permission):
    from outline_planning import OutlinePlanningError

    model = VolumeRepair()
    model.proposal["issues"][0]["writable_global_references"] = permission
    planner = make_planner(tmp_path, model, count=12, source=STORY)
    with pytest.raises(OutlinePlanningError):
        planner.run()
    assert "global_references" not in [stage for stage, _ in model.calls]
    assert not (planner.cache / "validated.json").exists()


def test_global_only_classification_replays_its_semantic_verdict_without_a_chapter_patch(tmp_path):
    model = VolumeRepair()
    model.reviews = [review({**GLOBAL, "finding_basis": "candidate_assumption"}), review()]
    model.proposal["issues"].pop()
    planner = make_planner(tmp_path, model, count=12, source=STORY)
    candidate = planner.run()
    assert not any(stage == "coordinated_group" for stage, _ in model.calls)
    verification = model.calls[-1][1]["coordinated_verification"]
    assert [row["original_issue"]["message"] for row in verification["issues"]] == [GLOBAL["message"]]
    assert verification["issues"][0]["requested_chapters"] == []
    before = original_checkpoints(planner)
    for verified in planner.cache.glob("review-" + evidence.VERSION + "*.json"):
        before[verified.name] = verified.read_bytes()
    resumed = VolumeRepair()
    assert make_planner(tmp_path, resumed, count=12, source=STORY).run() == candidate
    assert not resumed.calls
    assert all((planner.cache / name).read_bytes() == content for name, content in before.items())


def test_new_wire_omitting_global_write_scope_can_only_repair_the_named_chapter(tmp_path):
    model = VolumeRepair()
    first = model.proposal["issues"][0]
    del first["writable_global_references"]
    first["writable_chapters"] = [8]
    first["source_quotes"].append(QUOTE)
    planner = make_planner(tmp_path, model, count=12, source=STORY)
    result = planner.run()
    assert result["volume_contracts"][0]["climax_chapter"] == 11
    assert not any(stage == "global_references" for stage, _ in model.calls)


@pytest.mark.parametrize("damage", ["missing-decision", "altered-decision"])
def test_a_saved_pass_cannot_publish_global_changes_without_the_bound_reference_decision(tmp_path, damage):
    from outline_planning import OutlinePlanningError, content_hash

    model = VolumeRepair()
    planner = make_planner(tmp_path, model, count=12, source=STORY)
    planner.run()
    path = planner.cache / (evidence.VERSION + "-global-references-001.json")
    if damage == "missing-decision":
        path.unlink()
    else:
        # Both proposed chapter numbers are plausible. Only the original
        # recorded decision belongs to the already computed semantic verdict.
        record = json.loads(path.read_bytes())
        record["payload"]["edits"][0]["after"] = 11
        record["payload_hash"] = content_hash(record["payload"])
        path.write_text(json.dumps(record))
    saved_review = planner.cache / ("review-" + evidence.VERSION + ".json")
    previous = saved_review.read_bytes()
    resumed = VolumeRepair()
    with pytest.raises(OutlinePlanningError):
        make_planner(tmp_path, resumed, count=12, source=STORY).run()
    assert not resumed.calls and saved_review.read_bytes() == previous
