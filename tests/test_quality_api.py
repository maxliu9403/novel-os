"""Revision-bound quality reads and receipt-only API final mutations."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from api import db, richtext
from api import services as services_module
from api.main import create_app
from api.services import ProjectService
from artifacts import ArtifactStore, StaleArtifactHead
from promotion import PromotionCommitUncertain
from state_manager import ChapterState, StoryState


def _client(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    app = create_app(
        projects_root=projects,
        db_url=f"sqlite:///{tmp_path / 'quality.db'}",
    )
    return TestClient(app), projects


def _project(client: TestClient, projects, *, title: str = "Evidence Book"):
    created = client.post(
        "/api/projects",
        json={"title": title, "genre": "Drama", "author": "Ada"},
    ).json()
    project_id = created["id"]
    project = projects / project_id
    state = StoryState(str(project))
    state.chapters[1] = ChapterState(number=1, title="One", status="drafted")
    state.save_state()
    return project_id, project


def _seed_stage(project_id: str, project, stage: str, text: str) -> None:
    manuscript = project / "outputs" / "manuscript"
    manuscript.mkdir(parents=True, exist_ok=True)
    path = manuscript / f"chapter_001_{stage}.md"
    path.write_text(text, encoding="utf-8")
    db.upsert_artifact(
        project_id,
        1,
        stage,
        text,
        produced_by_agent="scribe" if stage == "draft" else "editor",
        produced_by_model="fixture:model",
    )


def _quality(client: TestClient, project_id: str) -> dict:
    response = client.get(f"/api/projects/{project_id}/chapters/1/quality")
    assert response.status_code == 200, response.text
    return response.json()


def test_quality_reads_project_authoritative_records_without_credentials(tmp_path):
    client, projects = _client(tmp_path)
    project_id, project = _project(client, projects)

    saved = client.put(
        f"/api/projects/{project_id}/chapters/1/final",
        json={"text": "A revision-bound final."},
    )
    assert saved.status_code == 200, saved.text

    quality = _quality(client, project_id)
    assert quality["final_revision_id"]
    assert quality["final_sha256"]
    assert len(quality["evaluation_reports"]) == 1
    report = quality["evaluation_reports"][0]
    assert report["artifact_revision_id"] == quality["final_revision_id"]
    assert report["artifact_sha256"] == quality["final_sha256"]
    assert report["rubric_version"]
    assert report["evaluator_provider"] == "deterministic"
    assert report["evaluator_model"]
    assert "api_key" not in json.dumps(report).lower()

    history = client.get(
        f"/api/projects/{project_id}/chapters/1/artifacts/revisions"
    )
    assert history.status_code == 200, history.text
    revisions = history.json()
    assert revisions[-1]["revision_id"] == quality["final_revision_id"]
    assert revisions[-1]["source"] == "author_edit"

    receipt = quality["promotion_receipts"][0]
    lookup = client.get(
        f"/api/projects/{project_id}/chapters/1/quality/receipts/{receipt['receipt_id']}"
    )
    assert lookup.status_code == 200, lookup.text
    assert lookup.json() == receipt

    assert db.artifact_revisions_list(project_id, 1)
    assert db.evaluation_reports_list(project_id, 1)
    assert db.promotion_receipts_list(project_id, 1)
    assert ArtifactStore(project).get_head(1, "final").revision_id == quality[
        "final_revision_id"
    ]


def test_text_save_replaces_stale_richtext_projection(tmp_path):
    client, projects = _client(tmp_path)
    project_id, _project_path = _project(client, projects)
    old_doc = richtext.from_markdown("Old rich text.\n")
    client.put(
        f"/api/projects/{project_id}/chapters/1/final/doc",
        json={"doc": old_doc},
    )

    saved = client.put(
        f"/api/projects/{project_id}/chapters/1/final",
        json={"text": "New plain text."},
    )

    assert saved.status_code == 200, saved.text
    final_doc = client.get(
        f"/api/projects/{project_id}/chapters/1/final/doc"
    ).json()
    assert final_doc["markdown"].strip() == "New plain text."
    assert final_doc["doc"] != old_doc


def test_author_edit_does_not_add_operational_story_event(tmp_path):
    client, projects = _client(tmp_path)
    project_id, project = _project(client, projects)

    saved = client.put(
        f"/api/projects/{project_id}/chapters/1/final",
        json={"text": "The story changes here."},
    )

    assert saved.status_code == 200, saved.text
    state = StoryState(str(project))
    chapter = state.get_chapter(1)
    assert not any("final updated" in event.lower() for event in chapter.plot_advances)


def test_text_final_save_updates_chapter_header_metadata_and_word_count(tmp_path):
    client, projects = _client(tmp_path)
    project_id, project = _project(client, projects)
    text = """<!--
CHAPTER: 1 - Changed Title
POV: Mara Vale
LOCATION: North Pier
TIME: Midnight
-->

One two three.
"""

    response = client.put(
        f"/api/projects/{project_id}/chapters/1/final",
        json={"text": text},
    )

    assert response.status_code == 200, response.text
    chapter = StoryState(str(project)).get_chapter(1)
    assert chapter.title == "Changed Title"
    assert chapter.pov_character == "Mara Vale"
    assert chapter.location == "North Pier"
    assert chapter.time == "Midnight"
    assert chapter.word_count == 3


def test_prosemirror_final_save_updates_chapter_word_count(tmp_path):
    client, projects = _client(tmp_path)
    project_id, project = _project(client, projects)
    doc = richtext.from_markdown("Four words live right here.\n")

    response = client.put(
        f"/api/projects/{project_id}/chapters/1/final/doc",
        json={"doc": doc},
    )

    assert response.status_code == 200, response.text
    assert StoryState(str(project)).get_chapter(1).word_count == 5


def test_stage_acceptance_promotes_through_receipt_authority(tmp_path):
    client, projects = _client(tmp_path)
    project_id, project = _project(client, projects)
    _seed_stage(project_id, project, "draft", "Accepted draft.\n")

    response = client.post(
        f"/api/projects/{project_id}/chapters/1/stages/draft/review",
        json={"decision": "accept"},
    )

    assert response.status_code == 200, response.text
    quality = _quality(client, project_id)
    receipt = quality["promotion_receipts"][-1]
    assert receipt["reason"] == "stage_acceptance"
    assert receipt["decision_metadata"]["source_stage"] == "draft"
    assert ArtifactStore(project).read_text(receipt["new_artifact_revision_id"]) == (
        "Accepted draft.\n"
    )


def test_stage_acceptance_updates_chapter_header_metadata_and_word_count(tmp_path):
    client, projects = _client(tmp_path)
    project_id, project = _project(client, projects)
    _seed_stage(
        project_id,
        project,
        "revised",
        """<!--
CHAPTER: 1 - Accepted Title
POV: Ilse Venn
LOCATION: Archive Hall
TIME: Dawn
-->

Accepted prose now.
""",
    )

    response = client.post(
        f"/api/projects/{project_id}/chapters/1/stages/revised/review",
        json={"decision": "accept"},
    )

    assert response.status_code == 200, response.text
    chapter = StoryState(str(project)).get_chapter(1)
    assert chapter.title == "Accepted Title"
    assert chapter.pov_character == "Ilse Venn"
    assert chapter.location == "Archive Hall"
    assert chapter.time == "Dawn"
    assert chapter.word_count == 3


def test_approve_phase_job_uses_stage_acceptance_authority(tmp_path, monkeypatch):
    client, projects = _client(tmp_path)
    project_id, project = _project(client, projects)
    _seed_stage(project_id, project, "revised", "Approved revision.\n")

    def legacy_orchestrator_forbidden(_project_dir):
        raise AssertionError("approve phase must not call the legacy orchestrator")

    monkeypatch.setattr(
        services_module,
        "build_orchestrator",
        legacy_orchestrator_forbidden,
    )
    job = ProjectService(projects).make_phase_job(
        project_id,
        "approve",
        {"number": 1},
    )

    job()

    quality = _quality(client, project_id)
    receipt = quality["promotion_receipts"][-1]
    assert receipt["reason"] == "stage_acceptance"
    assert receipt["decision_metadata"]["source_stage"] == "revised"
    assert ArtifactStore(project).read_text(receipt["new_artifact_revision_id"]) == (
        "Approved revision.\n"
    )


def test_final_doc_legacy_conversion_is_read_only_until_receipted_save(tmp_path):
    client, projects = _client(tmp_path)
    project_id, project = _project(client, projects)
    manuscript = project / "outputs/manuscript"
    manuscript.mkdir(parents=True, exist_ok=True)
    final_path = manuscript / "chapter_001_final.md"
    original = "# One\n\nLegacy **markdown** final.\n"
    final_path.write_text(original, encoding="utf-8")

    converted = client.get(
        f"/api/projects/{project_id}/chapters/1/final/doc"
    )

    assert converted.status_code == 200, converted.text
    assert final_path.read_text(encoding="utf-8") == original
    assert ArtifactStore(project).get_head(1, "final") is None
    assert _quality(client, project_id)["promotion_receipts"] == []

    saved = client.put(
        f"/api/projects/{project_id}/chapters/1/final/doc",
        json={"doc": converted.json()["doc"]},
    )

    assert saved.status_code == 200, saved.text
    quality = _quality(client, project_id)
    assert quality["promotion_receipts"][-1]["reason"] == "legacy_migration"
    assert db.get_artifact_doc(project_id, 1, "final") is not None


def test_snapshot_restore_creates_a_new_receipted_final(tmp_path):
    client, projects = _client(tmp_path)
    project_id, _project_path = _project(client, projects)
    client.put(
        f"/api/projects/{project_id}/chapters/1/final",
        json={"text": "Version one."},
    )
    snapshot = client.post(
        f"/api/projects/{project_id}/chapters/1/snapshots",
        json={"label": "v1"},
    ).json()
    client.put(
        f"/api/projects/{project_id}/chapters/1/final",
        json={"text": "Version two."},
    )

    restored = client.post(
        f"/api/projects/{project_id}/chapters/1/snapshots/{snapshot['id']}/restore"
    )

    assert restored.status_code == 200, restored.text
    assert restored.json()["final"].strip() == "Version one."
    receipt = _quality(client, project_id)["promotion_receipts"][-1]
    assert receipt["reason"] == "snapshot_restore"
    assert receipt["decision_metadata"]["snapshot_id"] == snapshot["id"]


def test_consequence_accept_promotes_text_and_canon_delta_together(tmp_path):
    client, projects = _client(tmp_path)
    project_id, project = _project(client, projects)
    client.put(
        f"/api/projects/{project_id}/chapters/1/final",
        json={"text": "Ilse held the ledger."},
    )
    preview_id = "quality-preview"
    ProjectService._consequence_previews[preview_id] = {
        "preview_id": preview_id,
        "project_id": project_id,
        "chapter": 1,
        "state_delta": {"key_events": ["Ilse drops the ledger"]},
        "changelog": ["[scribe] event: Ilse drops the ledger"],
    }
    doc = richtext.from_markdown("Ilse dropped the ledger.\n")

    accepted = client.post(
        f"/api/projects/{project_id}/chapters/1/consequence/accept",
        json={
            "preview_id": preview_id,
            "rewritten": "Ilse dropped the ledger.",
            "doc": doc,
            "state_delta": {"key_events": ["Ilse drops the ledger"]},
        },
    )

    assert accepted.status_code == 200, accepted.text
    receipt = _quality(client, project_id)["promotion_receipts"][-1]
    assert receipt["reason"] == "consequence_accept"
    assert receipt["canon_proposal_id"]
    state = StoryState(str(project))
    assert "Ilse drops the ledger" in state.get_chapter(1).plot_advances
    assert state.get_chapter(1).canonical_revision_id == receipt[
        "new_artifact_revision_id"
    ]


def test_force_override_records_scope_actor_and_integrity_bases(tmp_path):
    client, projects = _client(tmp_path)
    project_id, _project_path = _project(client, projects)
    _seed_stage(project_id, _project_path, "draft", "Unreviewed draft.\n")

    forced = client.post(
        f"/api/projects/{project_id}/chapters/1/final/promote?force=true"
    )

    assert forced.status_code == 200, forced.text
    receipt = _quality(client, project_id)["promotion_receipts"][-1]
    metadata = receipt["decision_metadata"]
    assert receipt["reason"] == "force_override"
    assert receipt["actor"] == "author"
    assert metadata["scope"] == "chapter.final"
    assert metadata["actor"] == "author"
    assert metadata["candidate_sha256"] == receipt["new_artifact_sha256"]
    assert metadata["base_canon_sha"] == receipt["old_canon_sha"]


def test_ingest_uses_canonical_head_instead_of_legacy_final_file(tmp_path):
    client, projects = _client(tmp_path)
    project_id, project = _project(client, projects)
    client.put(
        f"/api/projects/{project_id}/chapters/1/final",
        json={"text": "Canonical final."},
    )
    head = ArtifactStore(project).get_head(1, "final")
    final_path = project / "outputs/manuscript/chapter_001_final.md"
    final_path.write_text("Forged legacy projection.", encoding="utf-8")
    db._clear_all()

    db.ingest_project(projects, project_id)

    assert ArtifactStore(project).get_head(1, "final") == head
    assert db.get_artifact_text(project_id, 1, "final").strip() == "Canonical final."


def test_final_consumers_read_canonical_head_after_projection_is_tampered(tmp_path):
    client, projects = _client(tmp_path)
    project_id, project = _project(client, projects)
    canonical = "Canonical final survives."
    saved = client.put(
        f"/api/projects/{project_id}/chapters/1/final",
        json={"text": canonical},
    )
    assert saved.status_code == 200, saved.text
    final_path = project / "outputs/manuscript/chapter_001_final.md"
    final_path.write_text("Forged mutable projection.", encoding="utf-8")

    stages = client.get(f"/api/projects/{project_id}/chapters/1/stages")
    final_doc = client.get(f"/api/projects/{project_id}/chapters/1/final/doc")
    snapshot = client.post(
        f"/api/projects/{project_id}/chapters/1/snapshots",
        json={"label": "canonical"},
    )
    exported = client.get(f"/api/projects/{project_id}/export")
    compiled = client.get(f"/api/projects/{project_id}/compile?format=html")

    assert stages.status_code == 200, stages.text
    assert stages.json()["final"].strip() == canonical
    assert final_doc.status_code == 200, final_doc.text
    assert final_doc.json()["markdown"].strip() == canonical
    assert snapshot.status_code == 201, snapshot.text
    snap_text = client.get(
        f"/api/projects/{project_id}/chapters/1/snapshots/{snapshot.json()['id']}"
    )
    assert snap_text.status_code == 200, snap_text.text
    assert snap_text.json()["text"].strip() == canonical
    assert canonical in exported.text
    assert "Forged mutable projection." not in exported.text
    assert canonical in compiled.text
    assert "Forged mutable projection." not in compiled.text


def test_projection_failure_after_promotion_does_not_turn_commit_into_500(
    tmp_path,
    monkeypatch,
):
    client, projects = _client(tmp_path)
    project_id, project = _project(client, projects)
    real_atomic_write = services_module._atomic_write

    def fail_legacy_final(path, text):
        if path.name == "chapter_001_final.md":
            raise OSError("simulated legacy projection failure")
        real_atomic_write(path, text)

    monkeypatch.setattr(services_module, "_atomic_write", fail_legacy_final)

    response = client.put(
        f"/api/projects/{project_id}/chapters/1/final",
        json={"text": "Committed canonical final."},
    )

    assert response.status_code == 200, response.text
    assert response.json()["final"].strip() == "Committed canonical final."
    quality = _quality(client, project_id)
    assert len(quality["promotion_receipts"]) == 1
    head = ArtifactStore(project).get_head(1, "final")
    assert head is not None
    assert ArtifactStore(project).read_text(head.revision_id).strip() == (
        "Committed canonical final."
    )


def test_quality_read_returns_canonical_evidence_when_sqlite_backfill_fails(
    tmp_path,
    monkeypatch,
):
    client, projects = _client(tmp_path)
    project_id, _project_path = _project(client, projects)
    saved = client.put(
        f"/api/projects/{project_id}/chapters/1/final",
        json={"text": "Canonical evidence remains readable."},
    )
    assert saved.status_code == 200, saved.text

    def fail_backfill(_project_id, _payload):
        raise OSError("simulated SQLite backfill failure")

    monkeypatch.setattr(db, "project_artifact_revision", fail_backfill)

    response = client.get(f"/api/projects/{project_id}/chapters/1/quality")

    assert response.status_code == 200, response.text
    assert response.json()["final_revision_id"]
    assert len(response.json()["promotion_receipts"]) == 1


def test_stale_final_promotion_returns_conflict(tmp_path, monkeypatch):
    client, projects = _client(tmp_path)
    project_id, _project_path = _project(client, projects)

    def stale(_service, _request):
        raise StaleArtifactHead("simulated concurrent final update")

    monkeypatch.setattr(services_module.PromotionService, "promote", stale)
    http = TestClient(client.app, raise_server_exceptions=False)

    response = http.put(
        f"/api/projects/{project_id}/chapters/1/final",
        json={"text": "Competing edit."},
    )

    assert response.status_code == 409, response.text
    assert "conflict" in response.json()["detail"].lower()


def test_post_commit_uncertain_is_reconciled_before_returning(tmp_path, monkeypatch):
    client, projects = _client(tmp_path)
    project_id, _project_path = _project(client, projects)
    real_promote = services_module.PromotionService.promote

    def commit_then_report_uncertain(service, request):
        real_promote(service, request)
        raise PromotionCommitUncertain("receipt_commit", request.request_id)

    monkeypatch.setattr(
        services_module.PromotionService,
        "promote",
        commit_then_report_uncertain,
    )

    response = client.put(
        f"/api/projects/{project_id}/chapters/1/final",
        json={"text": "Committed before uncertain response."},
    )

    assert response.status_code == 200, response.text
    quality = _quality(client, project_id)
    assert len(quality["promotion_receipts"]) == 1
    assert response.json()["final"].strip() == "Committed before uncertain response."


def test_quality_receipt_validation_is_linear(tmp_path, monkeypatch):
    client, projects = _client(tmp_path)
    project_id, _project_path = _project(client, projects)
    for version in range(4):
        response = client.put(
            f"/api/projects/{project_id}/chapters/1/final",
            json={"text": f"Final version {version}."},
        )
        assert response.status_code == 200, response.text

    calls = 0
    real_load = services_module.PromotionService._load_receipt_unlocked

    def counted_load(service, key):
        nonlocal calls
        calls += 1
        return real_load(service, key)

    monkeypatch.setattr(
        services_module.PromotionService,
        "_load_receipt_unlocked",
        counted_load,
    )

    quality = _quality(client, project_id)

    receipt_count = len(quality["promotion_receipts"])
    assert receipt_count == 4
    assert calls <= receipt_count * 2


def test_corrupt_receipt_returns_integrity_conflict(tmp_path):
    client, projects = _client(tmp_path)
    project_id, project = _project(client, projects)
    saved = client.put(
        f"/api/projects/{project_id}/chapters/1/final",
        json={"text": "Receipt-bound final."},
    )
    assert saved.status_code == 200, saved.text
    receipt = _quality(client, project_id)["promotion_receipts"][0]
    receipt_path = (
        project
        / "outputs/state/promotion_receipts"
        / f"{receipt['idempotency_key']}.json"
    )
    receipt_path.write_text("{broken", encoding="utf-8")
    http = TestClient(client.app, raise_server_exceptions=False)

    response = http.get(f"/api/projects/{project_id}/chapters/1/quality")

    assert response.status_code == 409, response.text
    assert "integrity" in response.json()["detail"].lower()


@pytest.mark.parametrize("corrupt_target", ["blob", "heads"])
def test_corrupt_canonical_final_returns_integrity_conflict(tmp_path, corrupt_target):
    client, projects = _client(tmp_path)
    project_id, project = _project(client, projects)
    saved = client.put(
        f"/api/projects/{project_id}/chapters/1/final",
        json={"text": "Integrity-bound final."},
    )
    assert saved.status_code == 200, saved.text
    artifacts = ArtifactStore(project)
    head = artifacts.get_head(1, "final")
    assert head is not None
    if corrupt_target == "blob":
        revision = artifacts.get_revision(head.revision_id)
        (artifacts.blob_root / revision.sha256).write_text("corrupt", encoding="utf-8")
    else:
        artifacts.heads_path.write_text("{broken", encoding="utf-8")
    http = TestClient(client.app, raise_server_exceptions=False)

    response = http.get(f"/api/projects/{project_id}/chapters/1/final/doc")

    assert response.status_code == 409, response.text
    assert "integrity" in response.json()["detail"].lower()
