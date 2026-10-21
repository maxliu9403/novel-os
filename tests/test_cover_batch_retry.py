"""Explicit new batches reuse approved recipes without replenishing prior calls."""
from __future__ import annotations

import pytest

from core.image_client import ImageClientError
from core.cover_store import CoverConflict
from tests.test_cover_analysis_snapshot import source  # noqa: F401
from tests.test_cover_workflow import workflow, approve  # noqa: F401
from tests.test_cover_service import _service, _jpeg


@pytest.mark.parametrize("first_ready", [0, 2, 4])
def test_explicit_batch_after_terminal_generation_keeps_history(workflow, tmp_path, first_ready):
    flow, analysis, director, project = workflow
    direction = approve(flow, flow.plan(analysis.analysis_id, director))
    outcomes = [_jpeg(marker=bytes([index])) for index in range(first_ready)]
    outcomes += [ImageClientError("fixture provider unavailable")] * (8 - first_ready)
    service, client, _ = _service(tmp_path, outcomes)
    brief, concepts, arguments = flow.generation_arguments(direction.direction_id, direction.direction_sha256)
    first = service.generate("book", project, brief, concepts, **arguments)
    first_path = flow.store.sets_dir / f"{first.cover_set_id}.json"
    first_bytes = first_path.read_bytes()
    assert [item.status for item in first.candidates] == ["ready"] * first_ready + ["failed"] * (4 - first_ready)
    assert len(client.prompts) == 4

    # A new deliberate generation action has a fresh durable ID; the legacy
    # consumed approval is retained rather than reset or silently replenished.
    batch_id = "generation-task-" + "b" * 32
    approval_before = flow.approval(direction.direction_id)
    brief, concepts, arguments = flow.generation_arguments(
        direction.direction_id, direction.direction_sha256, generation_batch_id=batch_id)
    second = service.generate("book", project, brief, concepts, **arguments)

    assert second.cover_set_id != first.cover_set_id
    assert len(client.prompts) == 8
    assert first_path.read_bytes() == first_bytes
    assert all(item.scene_plan["_generation_authorization"]["image_attempts_reserved"] == 1
               for item in second.concepts)

    after = flow.approval(direction.direction_id)
    assert {key: after[key] for key in approval_before} == approval_before
    assert after["batch_reservations"][batch_id]["initial_image_calls"] == 4
    assert all(item.scene_plan["_generation_batch_id"] == batch_id for item in second.concepts)
    with pytest.raises(CoverConflict, match="额度"):
        flow.generation_arguments(direction.direction_id, direction.direction_sha256, generation_batch_id=batch_id)
    assert len(client.prompts) == 8
    # New batch approval cannot authorize another call on either old candidate.
    for cover in (first, second):
        failed = next((candidate for candidate in cover.candidates if candidate.status == "failed"), None)
        if failed is None:
            continue
        with pytest.raises(ValueError, match="budget is exhausted"):
            service.retry_candidate("book", project, cover.cover_set_id, failed.candidate_id,
                                    expected_revision=cover.revision)
    assert len(client.prompts) == 8


@pytest.mark.parametrize("prefix", ["generation-task", "cli-batch"])
def test_batch_preflight_is_read_only_and_each_id_can_only_be_reserved_once(workflow, prefix):
    flow, analysis, director, _ = workflow
    direction = approve(flow, flow.plan(analysis.analysis_id, director), extra_image_attempts=1)
    batch_id = prefix + "-" + "a" * 32
    path = flow._approval_path(direction.direction_id)
    before = path.read_bytes()
    for _ in range(2):
        _, concepts, options = flow.generation_arguments(direction.direction_id, direction.direction_sha256,
            generation_batch_id=batch_id, reserve=False)
        assert options["extra_image_attempts"] == 1
        assert len(concepts) == 4
    assert path.read_bytes() == before
    flow.generation_arguments(direction.direction_id, direction.direction_sha256, generation_batch_id=batch_id)
    reserved = path.read_bytes()
    for reserve in (False, True):
        with pytest.raises(CoverConflict, match="额度"):
            flow.generation_arguments(direction.direction_id, direction.direction_sha256,
                generation_batch_id=batch_id, reserve=reserve)
    assert path.read_bytes() == reserved
    second_id = prefix + "-" + "b" * 32
    flow.generation_arguments(direction.direction_id, direction.direction_sha256, generation_batch_id=second_id)
    records = flow.approval(direction.direction_id)["batch_reservations"]
    assert set(records) == {batch_id, second_id}
    assert all(record["extra_image_attempts"] == 1 for record in records.values())


def test_new_batch_preserves_approval_and_current_source_checks(workflow):
    flow, analysis, director, project = workflow
    direction = flow.plan(analysis.analysis_id, director)
    batch_id = "generation-task-" + "a" * 32
    with pytest.raises(CoverConflict):
        flow.generation_arguments(direction.direction_id, direction.direction_sha256, generation_batch_id=batch_id)
    approve(flow, direction)
    with pytest.raises(CoverConflict, match="hash mismatch"):
        flow.generation_arguments(direction.direction_id, "f" * 64, generation_batch_id=batch_id)
    assert not flow.approval(direction.direction_id).get("batch_reservations")
    (project / "outputs/input/prompt.md").write_text("Another novel")
    with pytest.raises(CoverConflict, match="骨架已变化"):
        flow.generation_arguments(direction.direction_id, direction.direction_sha256, generation_batch_id=batch_id)
    assert not flow.approval(direction.direction_id).get("batch_reservations")


@pytest.mark.parametrize("batch_id", [None, 0, 1, False, {}, "../../other", "generation-task-too-short"])
def test_invalid_batch_identity_cannot_reserve_or_write(workflow, batch_id):
    flow, analysis, director, _ = workflow
    direction = approve(flow, flow.plan(analysis.analysis_id, director))
    before = flow._approval_path(direction.direction_id).read_bytes()
    with pytest.raises(ValueError, match="batch id"):
        flow.generation_arguments(direction.direction_id, direction.direction_sha256, generation_batch_id=batch_id)
    assert flow._approval_path(direction.direction_id).read_bytes() == before


def test_single_candidate_approval_cannot_acquire_new_batch_allowance(workflow):
    flow, analysis, director, _ = workflow
    direction = approve(flow, flow.plan(analysis.analysis_id, director))
    before = flow._approval_path(direction.direction_id).read_bytes()
    with pytest.raises(CoverConflict, match="单张修复批准"):
        flow.generation_arguments(direction.direction_id, direction.direction_sha256,
            single_candidate=direction.plans[0].concept_id, generation_batch_id="generation-task-" + "b" * 32)
    assert flow._approval_path(direction.direction_id).read_bytes() == before


def test_new_batch_candidate_must_keep_its_recorded_allowance(workflow, tmp_path):
    from dataclasses import replace

    flow, analysis, director, project = workflow
    direction = approve(flow, flow.plan(analysis.analysis_id, director))
    service, _, _ = _service(tmp_path, [ImageClientError("fixture unavailable")] * 4)
    batch_id = "generation-task-" + "a" * 32
    brief, concepts, arguments = flow.generation_arguments(direction.direction_id, direction.direction_sha256,
        generation_batch_id=batch_id)
    generated = service.generate("book", project, brief, concepts, **arguments)
    candidate = generated.candidates[0]
    for for_generation in (False, True):
        flow.validate_candidate(generated.cover_set_id, candidate.candidate_id, for_generation=for_generation)
    concept = generated.concepts[0]
    changed = replace(concept, scene_plan={**concept.scene_plan,
        "_generation_batch_id": "generation-task-" + "b" * 32})
    flow.store.save(replace(generated, concepts=(changed, *generated.concepts[1:])),
                    expected_revision=generated.revision)
    with pytest.raises(CoverConflict, match="generation batch"):
        flow.validate_candidate(generated.cover_set_id, candidate.candidate_id)
