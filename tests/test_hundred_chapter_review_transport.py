"""Exercise review windowing with the Gemini transport's actual byte accounting."""
from copy import deepcopy
import json

from core.gemini_gateway import INPUT_LIMIT_BYTES, input_bytes
from core.planning_context import expand_review_context
from test_outline_planning import PlanningModel, make_planner


class MeteredPlannerModel(PlanningModel):
    provider = "gemini_openai"

    def __init__(self):
        super().__init__()
        self.request_sizes = []

    def text_input_limit_bytes(self):
        return INPUT_LIMIT_BYTES

    def complete(self, system, user):
        size = input_bytes(system, user)
        assert size <= INPUT_LIMIT_BYTES
        self.request_sizes.append(size)
        response = super().complete(system, user)
        if not user.startswith("STAGE: chapter_batch"):
            return response
        batch = json.loads(response)
        for chapter in batch["chapters"]:
            chapter["scenes"][0]["source_notes"] = (
                "Independent evidence preserved exactly. " * 100
                + f"原文细节 {chapter['number']}"
            )
        return json.dumps(batch, ensure_ascii=False)


def assert_complete_window(context, candidate, source):
    expanded = expand_review_context(context, complete_source=source)
    assert expanded["complete_adopted_source"] == source
    numbers = context["review_window"]["included_chapters"]
    assert expanded["candidate"] == {
        **candidate,
        "chapters": [candidate["chapters"][n - 1] for n in numbers],
    }


def test_hundred_chapter_planning_and_local_recheck_fit_inline_byte_budget(tmp_path):
    client = MeteredPlannerModel()
    planner = make_planner(tmp_path, client, count=100)
    result = planner.run()
    assert len(result["chapters"]) == 100
    # Compare with the pre-publication candidate actually supplied to review.
    def saved(name):
        return json.loads((planner.cache / f"{name}.json").read_text())["payload"]

    candidate = {
        **saved("global"),
        "chapters": [chapter for i in range(1, len(planner.ranges) + 1)
                     for chapter in saved(f"batch-{i:03d}")["chapters"]],
    }
    initial = [ctx for stage, ctx in client.calls if stage == "review"]
    windows = [ctx["review_window"] for ctx in initial]
    assert len(windows) > 1
    assert [n for w in windows for n in w["requested_chapters"]] == list(range(1, 101))
    assert all(any({n, n + 1} <= set(w["included_chapters"]) for w in windows)
               for n in range(1, 100))
    for ctx in initial:
        assert_complete_window(ctx, candidate, planner.source)

    changed = deepcopy(candidate)
    changed["chapters"][6]["incoming_consequence"] += " She must honour the recorded debt."
    calls_before = len(client.calls)
    options = {"reuse_from": "review-initial", "baseline_candidate": candidate}
    review = planner._semantic_review(changed, "review-repaired", **options)
    assert review["status"] == "passed"
    fresh = [ctx for stage, ctx in client.calls[calls_before:] if stage == "review"]
    affected = [w for w in windows if 7 in w["included_chapters"]]
    assert 0 < len(affected) < len(windows)
    assert [ctx["review_window"] for ctx in fresh] == affected
    assert len(review["review_scope"]["reused_passed_windows"]) == len(windows) - len(affected)
    for ctx in fresh:
        assert_complete_window(ctx, changed, planner.source)
    requests_before_resume = len(client.request_sizes)
    assert planner._semantic_review(changed, "review-repaired", **options) == review
    assert len(client.request_sizes) == requests_before_resume
