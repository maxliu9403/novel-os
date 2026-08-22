import json
from pathlib import Path

import pytest

from pipeline_models import (
    ManifestStore,
    RunManifest,
    RunSpec,
    StageResult,
)


def test_run_spec_and_manifest_round_trip(tmp_path: Path):
    spec = RunSpec(
        project_path=str(tmp_path / "project"),
        prompt_path="prompt.md",
        num_chapters=2,
        target_words=5000,
        approval_policy="auto",
    )
    manifest = RunManifest.new(spec, run_id="run-test")
    manifest.record(StageResult(
        phase="intake",
        status="done",
        artifact_paths=["prompt.md"],
        artifact_hashes={"prompt.md": "abc"},
    ))

    decoded = RunManifest.from_dict(manifest.to_dict())

    assert decoded.run_id == "run-test"
    assert decoded.spec.approval_policy == "auto"
    assert decoded.stages["intake"].status == "done"
    assert decoded.stages["intake"].artifact_paths == ["prompt.md"]
    assert decoded.stages["intake"].artifact_hashes == {"prompt.md": "abc"}


def test_stage_result_rejects_unknown_status():
    with pytest.raises(ValueError, match="Unknown stage status"):
        StageResult(phase="write", status="finished")


def test_manifest_store_writes_valid_json_atomically(tmp_path: Path):
    spec = RunSpec(project_path=str(tmp_path), prompt_path="prompt.md")
    manifest = RunManifest.new(spec, run_id="run-atomic")
    path = tmp_path / "run.json"

    ManifestStore(path).save(manifest)

    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == "run-atomic"
    assert not list(tmp_path.glob("run.json.tmp*"))
