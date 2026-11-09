import json

import pytest

from core.planning_errors import OutlinePlanningError, PlanningOutputIncomplete
from test_outline_planning import PlanningModel, make_planner


class TruncatedBatchModel(PlanningModel):
    def __init__(self, *, syntax=True, fail_after_left=False):
        super().__init__()
        self.syntax = syntax
        self.fail_after_left = fail_after_left
        self.attempts = []

    def complete(self, system, user):
        context = json.loads(user.rsplit("\n\n", 1)[1])
        if user.startswith("STAGE: chapter_batch"):
            numbers = context["requested_chapters"]
            self.attempts.append(numbers)
            if len(numbers) > 2:
                return '{"chapters":[' if self.syntax else '{"chapters":[]}'
            if self.fail_after_left and numbers[0] == 3:
                raise TimeoutError("after left half")
        return super().complete(system, user)


def test_detail_batch_resumes_smaller_units_after_repeated_incomplete_json(tmp_path):
    first = TruncatedBatchModel(fail_after_left=True)
    old = make_planner(tmp_path, first, count=4)
    with pytest.raises(TimeoutError):
        old.run()
    assert first.attempts == [[1, 2, 3, 4], [1, 2], [3, 4]]
    assert (old.cache / "batch-001-response.json").is_file()
    assert (old.cache / "batch-001-format-correction-response.json").is_file()
    before = (old.cache / "batch-001-part-001-002.json").read_bytes()
    second = TruncatedBatchModel()
    new = make_planner(tmp_path, second, count=4)
    result = new.run()
    assert second.attempts == [[3, 4]]
    assert [c["number"] for c in result["chapters"]] == [1, 2, 3, 4]
    assert (new.cache / "batch-001-part-001-002.json").read_bytes() == before
    history = next(ctx for stage, ctx in second.calls if stage == "batch")
    assert [c["number"] for c in history["previous_completed_chapters"]] == [1, 2]


def test_detail_batch_with_parseable_but_invalid_story_contract_stays_blocked(tmp_path):
    model = TruncatedBatchModel(syntax=False)
    planner = make_planner(tmp_path, model, count=4)
    with pytest.raises(OutlinePlanningError, match="remains invalid") as caught:
        planner.run()
    assert not isinstance(caught.value, PlanningOutputIncomplete)
    assert model.attempts == [[1, 2, 3, 4]]
    assert not (planner.cache / "batch-001-split.json").exists()
