"""Publication policy upgrades must not repurchase accepted copy on resume."""

import hashlib
import json

import pytest

from pipeline_models import RunSpec
from pipeline_runner import PipelineError, PipelineRunner
from tests.test_pipeline_runner import FakeOrchestrator, _factory


@pytest.fixture
def legacy_publication(tmp_path):
    FakeOrchestrator.calls = []
    FakeOrchestrator.publication_model_calls = []
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Historical Book\n\nA story.", encoding="utf-8")
    project = tmp_path / "project"
    runner = PipelineRunner(orchestrator_factory=_factory)
    manifest = runner.run(RunSpec(
        project_path=str(project), prompt_path=str(prompt), num_chapters=1,
        target_words=20, approval_policy="auto", output_formats=("markdown",),
    ))
    assert manifest.status == "completed", manifest.error
    relative = "outputs/publication/publication-copy.json"
    path = project / relative
    payload = json.loads(path.read_text())
    payload["validation"]["policy_version"] = "publication-copy-policy.v3"
    payload["generation"]["writer_prompt_version"] = "publication-copy-writer.v3"
    path.write_text(json.dumps(payload), encoding="utf-8")
    stage = manifest.get("publication.copy")
    stage.input_hashes["publication_copy_policy_sha256"] = hashlib.sha256(
        b"publication-copy-policy.v3"
    ).hexdigest()
    stage.artifact_hashes[relative] = runner._sha256(path)
    runner._write_stage_result(manifest, project, stage)
    runner._store(project, manifest.run_id).save(manifest)
    return runner, project, manifest, path


def test_compile_retry_preserves_accepted_legacy_policy_without_model_calls(legacy_publication):
    runner, project, manifest, path = legacy_publication
    saved = path.read_bytes()
    final = project / "outputs/manuscript/chapter_001_final.md"
    saved_final = final.read_bytes()
    calls = list(FakeOrchestrator.publication_model_calls)
    (project / "outputs/deliverables/book.md").unlink()

    resumed = runner.resume(manifest.run_id, project)

    assert resumed.status == "completed", resumed.error
    assert path.read_bytes() == saved
    assert final.read_bytes() == saved_final
    assert FakeOrchestrator.publication_model_calls == calls
    assert resumed.get("publication.copy").input_hashes == manifest.get("publication.copy").input_hashes
    assert (project / "outputs/deliverables/book.md").is_file()


def test_changed_publication_inputs_select_current_policy(legacy_publication):
    runner, project, manifest, _ = legacy_publication
    gate = runner._stage_result_path(project, manifest.run_id, "book-check")
    # A different persisted gate is a new source binding, even when the copy's
    # old stage was complete. It must not label a new v4 candidate as v3.
    gate.write_text(gate.read_text() + "\n")

    current = runner._stage_input_hashes(project, "publication.copy", None, manifest)

    assert current["publication_copy_policy_sha256"] == hashlib.sha256(
        b"publication-copy-policy.v4"
    ).hexdigest()


def test_copy_cannot_claim_a_different_policy_than_its_checkpoint(legacy_publication):
    runner, project, manifest, path = legacy_publication
    payload = json.loads(path.read_text())
    payload["validation"]["policy_version"] = "publication-copy-policy.v4"
    path.write_text(json.dumps(payload))

    with pytest.raises(PipelineError, match="policy differs from its checkpoint"):
        runner._load_publication_copy(manifest, project)


def test_unknown_saved_policy_blocks_without_rewriting_copy(legacy_publication):
    runner, project, manifest, path = legacy_publication
    saved = path.read_bytes()
    manifest.get("publication.copy").input_hashes["publication_copy_policy_sha256"] = "0" * 64

    with pytest.raises(PipelineError, match="unsupported policy binding"):
        runner._stage_input_hashes(project, "publication.copy", None, manifest)

    assert path.read_bytes() == saved


@pytest.mark.parametrize("run_status", ("paused", "failed"))
@pytest.mark.parametrize("damage", ("missing", "corrupt"))
def test_unfinished_run_does_not_repurchase_damaged_accepted_copy(
    legacy_publication, run_status, damage
):
    runner, project, manifest, path = legacy_publication
    calls = list(FakeOrchestrator.publication_model_calls)
    saved_inputs = dict(manifest.get("publication.copy").input_hashes)
    final = project / "outputs/manuscript/chapter_001_final.md"
    saved_final = final.read_bytes()
    if damage == "missing":
        path.unlink()
        damaged_bytes = None
    else:
        damaged_bytes = b'{"corrupted": true}\n'
        path.write_bytes(damaged_bytes)
    manifest.status = run_status
    manifest.current_phase = "compile"
    manifest.current_chapter = None
    manifest.error = "Interrupted before compilation completed"
    manifest.get("compile").status = "blocked" if run_status == "paused" else "failed"
    runner._store(project, manifest.run_id).save(manifest)

    resumed = runner.resume(manifest.run_id, project)

    assert resumed.status == "paused", resumed.error
    assert "Completed publication.copy authority is missing or divergent" in resumed.error
    assert FakeOrchestrator.publication_model_calls == calls
    assert resumed.get("publication.copy").status == "done"
    assert resumed.get("publication.copy").input_hashes == saved_inputs
    assert final.read_bytes() == saved_final
    if damaged_bytes is None:
        assert not path.exists()
    else:
        assert path.read_bytes() == damaged_bytes
