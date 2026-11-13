"""Durable story lineage across interruptions, model cycles and old namespaces."""
import json
import shutil

import pytest

from outline_planning import OutlinePlanningError
from test_outline_planning import PlanningModel, make_planner


def book(directory, route, **options):
    model = PlanningModel(**options)
    model.model = route
    return make_planner(directory, model, count=12), model


@pytest.mark.parametrize("fault", ["identity", "lineage"])
def test_corrupt_handoff_never_forgets_predecessor_on_retry(tmp_path, fault):
    a, _ = book(tmp_path, "A", fail_batch=9)
    with pytest.raises(TimeoutError):
        a.run()
    path = a.cache / ("identity.json" if fault == "identity" else "model-continuation.json")
    path.write_text("{}")
    progress = a.directory / "progress.json"
    original = progress.read_bytes()
    for _ in range(2):
        model = PlanningModel()
        model.model = "B"
        with pytest.raises(OutlinePlanningError, match="continuation"):
            make_planner(tmp_path, model, count=12).run()
        assert not model.calls and progress.read_bytes() == original
        assert path.read_bytes() == b"{}"


def test_interrupted_handoff_keeps_ancestor_units_and_model_cycles_are_finite(tmp_path):
    a, _ = book(tmp_path, "A", fail_batch=9)
    with pytest.raises(TimeoutError):
        a.run()
    b, _ = book(tmp_path, "B")
    load = b._load

    def interrupt(name, *args, **kwargs):
        if name == "batch-001":
            raise TimeoutError("interrupted before adoption of earlier chapter batches")
        return load(name, *args, **kwargs)
    b._load = interrupt
    with pytest.raises(TimeoutError):
        b.run()

    # A single-link namespace created by the earlier implementation is also a
    # durable ancestor, including work it did not finish copying before exit.
    (b.cache / "model-continuation.json").write_text(json.dumps({"predecessor": a.identity_hash}))
    c, model = book(tmp_path, "C")
    c.run()
    assert [(stage, d.get("requested_chapters")) for stage, d in model.calls] == [
        ("batch", [9, 10, 11, 12]), ("review", None)]
    assert json.loads((c.cache / "batch-001.json").read_text())["inherited_from"]["identity_hash"] == a.identity_hash

    for route in ("A", "B", "C"):
        cycled, model = book(tmp_path, route)
        assert len(cycled.run()["chapters"]) == 12
        assert not any(stage in {"global", "batch"} for stage, _ in model.calls)
        ancestry = json.loads((cycled.cache / 'model-continuation.json').read_bytes())['predecessors']
        assert len(ancestry) == len(set(ancestry)) == 2
        assert cycled.identity_hash not in ancestry


def test_two_historical_valid_designs_never_silently_select_destination_or_overwrite(tmp_path):
    root = tmp_path / "run"
    a, _ = book(root, "A")
    a.run()
    elsewhere, _ = book(tmp_path / "legacy", "B", mutate_global=lambda g: g.update(logline="A different accepted design."))
    elsewhere.run()
    b, model = book(root, "B")
    shutil.copytree(elsewhere.cache, b.cache)
    originals = [(p, p.read_bytes()) for d in (a.cache, b.cache) for p in d.glob("*.json")]
    for _ in range(2):
        with pytest.raises(OutlinePlanningError, match="Divergent validated design"):
            b.run()
        assert not model.calls
        assert all(path.read_bytes() == raw for path, raw in originals)


def test_model_with_more_capacity_continues_partial_scoped_design(tmp_path):
    from test_planning_roadmap import STORIES, StoryModel, large_adopted_source
    story = STORIES[0]
    source = large_adopted_source(story)
    model = StoryModel(story, fail_at=9)
    model.planning_input_bytes = 100000
    a = make_planner(tmp_path, model, count=100, source=source)
    with pytest.raises(TimeoutError):
        a.run()
    assert json.loads((a.cache / "global-seed-source-v1.json").read_bytes())["payload"]["scope"] == "global_sections_and_chapter_titles"
    switched = StoryModel(story, fail_at=17)
    switched.model = "larger-context"
    b = make_planner(tmp_path, switched, count=100, source=source)
    with pytest.raises(TimeoutError):
        b.run()
    assert [(stage, ctx["requested_chapters"][0]) for stage, ctx in switched.calls] == [
        ("chapter_roadmap", 9), ("chapter_roadmap", 17)]
    for name in ("global-seed-source-v1", "global-seed-v1", "roadmap-v1-001-008"):
        assert json.loads((b.cache / f"{name}.json").read_bytes())["inherited_from"]["identity_hash"] == a.identity_hash


def test_bounded_chapter_design_pieces_keep_provenance_and_raw_replies_stay_private(tmp_path):
    a, _ = book(tmp_path, "A")
    a.run()
    names = ("batch-001-split", "batch-001-part-001-002", "batch-001-part-001-002-split")
    for name in (*names, "batch-001-part-001-002-response"):
        a._save(name, {"source": "subset"}, {"accepted": name})
    b, _ = book(tmp_path, "B")
    b.run()
    for name in names:
        assert b._load(name, {"source": "subset"}, lambda p: p) == {"accepted": name}
        assert json.loads((b.cache / f"{name}.json").read_bytes())['inherited_from']['identity_hash'] == a.identity_hash
    assert b._load("batch-001-part-001-002-response", {"source": "subset"}, lambda p: p) is None

    path = b.cache / "batch-001-part-001-002.json"
    value = json.loads(path.read_bytes())
    value["inherited_from"]["record_sha256"] = "0" * 64
    path.write_text(json.dumps(value))
    with pytest.raises(OutlinePlanningError, match="source checksum mismatch"):
        b._load("batch-001-part-001-002", {"source": "subset"}, lambda p: p)
