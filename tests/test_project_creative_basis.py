"""Creative basis is a frozen-input projection, never a new literary assessment."""
from copy import deepcopy
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.project_creative_basis import CreativeBasisError, creative_basis, creative_basis_markdown
from core.creative_execution import CreativeExecution, digest, make_handoff
from core.creative_types import CreativeTypeStore
from test_creative_types import selected


@pytest.fixture
def frozen_book(tmp_path):
    project = tmp_path / "book"
    (project / "outputs/input").mkdir(parents=True)
    (project / "outputs/state").mkdir()
    (project / "outputs/state/story_state.json").write_text('{"characters": {}}')
    source = "# Final adopted story\nShe leaves the company and chooses a new life."
    (project / "outputs/input/prompt.md").write_text(source)
    library = CreativeTypeStore(tmp_path / "types")
    snapshot = library.snapshot(selected(library), audience_channel="female")
    handoff = make_handoff(source=source, origin={"workshop_id": "old-discussion", "revision": 9},
        type_snapshot=snapshot, adopted_proposals=[{
            "proposal": {"id": "r2:p1", "title": "Stay at the company", "story": "An earlier selected direction.",
                         "choice": "Stay", "cost": "Lose independence", "payoff": "An alliance",
                         "tag_contributions": [{"tag_id": "tag.betrayal", "tag_name": "UNTRUSTED NAME", "contribution": "The broken promise drives her choice."}]},
            "author_decision": {"status": "revised", "note": "Keep the alliance but change the workplace."}}])
    runtime = CreativeExecution.open(project=project, run_id="run1", handoff=handoff)
    manifest = {"run_id": "run1", "status": "completed", "created_at": "2026-11-08T00:00:00Z",
                "spec": {"creative_handoff": handoff}, "creative_snapshot_started": True,
                "creative_lock_sha256": runtime.lock_sha256}
    (project / "outputs/runs/run1/run.json").write_text(json.dumps(manifest))
    return project, manifest, library


def fingerprint(project):
    return {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in project.rglob("*") if p.is_file()}


def test_frozen_report_keeps_historical_design_distinct_from_final_story_and_reads_no_live_library(frozen_book):
    project, manifest, library = frozen_book
    before = fingerprint(project)
    record = library.get("tag.betrayal")
    library.mutate("activate", record["id"], revision=record["revision"], operation_id="new-library-version",
                   markdown=record["draft"] + "\nNEW LIVE WORDING\n")
    report = creative_basis(project, project.name, manifest)
    assert report["recorded"] and report["binding"] == "execution_lock"
    assert report["audience_channel"] == "female"
    assert [row["version"] for row in report["documents"]] == [1, 1]
    assert report["historical_designs"][0]["summary"] == "An earlier selected direction."
    assert report["historical_designs"][0]["tag_contributions"][0]["tag_name"] != "UNTRUSTED NAME"
    assert "最终采用骨架优先" in report["authority_note"]
    assert "正文体现待确认" in report["evidence_note"]
    assert report["prose_evidence"] == []
    markdown = creative_basis_markdown(report)
    assert "历史已选设计" in markdown and "The broken promise" in markdown and "NEW LIVE WORDING" not in markdown
    assert "Keep the alliance" in markdown and "An earlier selected direction." in markdown
    assert fingerprint(project) == before


@pytest.mark.parametrize("tamper", ["source", "lock", "handoff", "snapshot", "missing_lock", "source_symlink"])
def test_tampered_sources_and_bindings_are_rejected(frozen_book, tmp_path, tamper):
    project, manifest, _ = frozen_book
    if tamper == "source":
        (project / "outputs/input/prompt.md").write_text("changed")
    elif tamper == "lock":
        path = project / "outputs/runs/run1/creative-design/lock.json"
        lock = json.loads(path.read_text()); lock["rules"] = "changed"
        path.write_text(json.dumps(lock))
    elif tamper == "handoff":
        manifest["spec"]["creative_handoff"]["adopted_proposals"][0]["proposal"]["story"] = "changed"
    elif tamper == "snapshot":
        # Even a rehashed outer handoff cannot certify an altered inner document.
        handoff = manifest["spec"]["creative_handoff"]
        handoff["type_snapshot"]["documents"][0]["markdown"] += "changed"
        handoff["handoff_sha256"] = digest({k: v for k, v in handoff.items() if k != "handoff_sha256"})
    elif tamper == "missing_lock":
        (project / "outputs/runs/run1/creative-design/lock.json").unlink()
    else:
        source = project / "outputs/input/prompt.md"
        external = tmp_path / "external.md"; external.write_bytes(source.read_bytes())
        source.unlink(); source.symlink_to(external)
    with pytest.raises(CreativeBasisError, match="完整性"):
        creative_basis(project, project.name, manifest)


def test_no_fabricated_channel_for_legacy_and_no_fallback_to_older_handoff(frozen_book):
    project, manifest, _ = frozen_book
    legacy = {"run_id": "newer-run", "spec": {}}
    report = creative_basis(project, project.name, legacy)
    assert not report["recorded"] and report["audience_channel"] is None
    assert "未记录" in creative_basis_markdown(report)
    assert not creative_basis(project, project.name, None)["recorded"]
    prepared = deepcopy(manifest)
    prepared.update(run_id="prepared", creative_snapshot_started=False, creative_lock_sha256="")
    assert creative_basis(project, project.name, prepared)["binding"] == "frozen_handoff"


def test_read_api_and_separate_markdown_download_use_same_verified_record(frozen_book, tmp_path):
    project, _, _ = frozen_book
    client = TestClient(create_app(projects_root=tmp_path, db_url=f"sqlite:///{tmp_path / 'api.db'}"))
    response = client.get(f"/api/projects/{project.name}/creative-basis")
    assert response.status_code == 200, response.text
    download = client.get(f"/api/projects/{project.name}/creative-basis/markdown")
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    assert download.headers["cache-control"] == "no-store"
    assert response.json()["source_sha256"] in download.text
    (project / "outputs/input/prompt.md").write_text("changed")
    assert client.get(f"/api/projects/{project.name}/creative-basis").status_code == 409
    assert client.get(f"/api/projects/{project.name}/creative-basis/markdown").status_code == 409
    assert client.get("/api/projects/missing/creative-basis").status_code == 404


def test_read_api_never_skips_a_corrupt_run_and_displays_an_older_design(frozen_book, tmp_path):
    project, _, _ = frozen_book
    path = project / "outputs/runs/newer-run/run.json"
    path.parent.mkdir()
    path.write_text('{"run_id": "newer-run", "spec":')
    client = TestClient(create_app(projects_root=tmp_path, db_url=f"sqlite:///{tmp_path / 'api.db'}"))
    response = client.get(f"/api/projects/{project.name}/creative-basis")
    assert response.status_code == 409
    assert "未回退" in response.json()["detail"]
    assert client.get(f"/api/projects/{project.name}/creative-basis/markdown").status_code == 409
