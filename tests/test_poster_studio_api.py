"""User-visible poster flows through HTTP, with real local image composition."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
import threading
import time
from types import SimpleNamespace
from zipfile import ZipFile

from PIL import Image
import pytest

from api import db, poster_studio, poster_tasks, routes
from api.cover_service import CoverService
from api.media import LocalMediaStore
from api.poster_image_service import PosterImageService
from core.cover_store import CoverStore
from core.image_client import GeneratedImage, ImageClientError
from tests.test_cover_api import _client
from tests.test_cover_poster_planner import response as planning_response
from tests.test_cover_epub import book_fixture, embedded_cover
from compile_epub import render_epub
from styles import StyleSheet
from core.h5_import import epub_index
from core.delivery_package import build_delivery_package


class Planner:
    model = "text-fixture"
    provider_name = "codex"

    def __init__(self):
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = False

    def complete(self, system, user):
        self.calls.append((system, json.loads(user)))
        self.entered.set()
        if self.block:
            assert self.release.wait(5), "Test did not release the planning fixture"
        return json.dumps(planning_response())


class Images:
    settings = SimpleNamespace(model="gpt-image-2", size="400x600")

    def __init__(self):
        self.calls = []
        self.failed_calls = set()
        self.block_call = None
        self.entered = threading.Event()
        self.release = threading.Event()

    def _result(self, prompt, operation):
        self.calls.append((operation, prompt))
        number = len(self.calls)
        if number == self.block_call:
            self.entered.set()
            assert self.release.wait(8), "Test did not release the image fixture"
        if number in self.failed_calls:
            raise ImageClientError("fixture upstream failure", retryable=True)
        output = BytesIO()
        Image.new("RGB", (400, 600), (20 + number, 35 + number, 70 + number)).save(output, format="PNG")
        return GeneratedImage(output.getvalue(), "image/png", 400, 600,
                              f"request-{number}", "gpt-image-2", "400x600")

    def generate(self, prompt):
        return self._result(prompt, "generate")

    def edit(self, prompt, reference, *, expected_reference_sha256):
        assert sha256(reference.read_bytes()).hexdigest() == expected_reference_sha256
        return replace(self._result(prompt, "edit"), operation="edit",
                       reference_image_sha256=expected_reference_sha256, reference_transport="fixture-bytes")


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_OS_POSTER_STUDIO", "1")
    monkeypatch.delenv("NOVEL_OS_POSTER_CONTEXT_WINDOW_TOKENS", raising=False)
    client, _ = _client(tmp_path, monkeypatch)
    planner, images = Planner(), Images()
    service = CoverService(image_client=images, media_store=LocalMediaStore(tmp_path / "media"), media_add=db.media_add)
    service._quality_report = lambda *_a, **_kw: pytest.fail("New poster flow called the old quality gate")
    monkeypatch.setattr(poster_studio, "get_poster_planner", lambda: planner)
    client.app.dependency_overrides[poster_studio.get_poster_service_factory] = lambda: lambda: PosterImageService(service)
    client.app.dependency_overrides[routes.get_cover_mutation_service] = lambda: service
    created = client.post("/api/projects", json={"title": "The Choice Was Hers", "genre": "Domestic drama"})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    project = tmp_path / "projects" / project_id
    source_path = project / "outputs/input/prompt.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source = ("# Complete skeleton\r\nMara, 58, is Leo's mother.\r\n"
              "## Core conflict\r\nShe can love her adult son without surrendering her own life.\r\n"
              "## Ending\r\nMara chooses her own flat. Family affection remains unresolved.\r\n"
              "```COVER_SOURCE_DEPENDENCIES\r\n{\"cast\":\"cast.md\"}\r\n```\r\n").encode()
    source_path.write_bytes(source)
    (project / "cast.md").write_text("Leo is an adult; preserve mother and son identities.\n")
    yield SimpleNamespace(client=client, project=project, project_id=project_id,
                          base=f"/api/projects/{project_id}/poster", planner=planner,
                          images=images, source=source, service=service)
    planner.release.set()
    images.release.set()


def overview(studio):
    value = studio.client.get(studio.base)
    assert value.status_code == 200, value.text
    return value.json()


def wait_task(studio, task_id):
    for _ in range(250):
        task = next(item for item in overview(studio)["tasks"] if item["task_id"] == task_id)
        if task["status"] not in {"queued", "running"}:
            return task
        time.sleep(.02)
    raise AssertionError("Poster fixture task did not finish")


def plan(studio, request_id="plan-request-01"):
    result = studio.client.post(studio.base + "/plans", json={"request_id": request_id, "preferences": {"audience": "Mature women"}})
    assert result.status_code == 202, result.text
    task = wait_task(studio, result.json()["task_id"])
    assert task["status"] == "complete", task
    return next(item for item in overview(studio)["plans"] if item["plan_id"] == task["result"]["plan_id"])


def generate(studio, saved_plan=None, request_id="batch-request-01"):
    saved_plan = saved_plan or plan(studio)
    body = {"request_id": request_id, "plan_id": saved_plan["plan_id"], "plan_hash": saved_plan["plan_hash"]}
    result = studio.client.post(studio.base + "/batches", json=body)
    assert result.status_code == 202, result.text
    task = wait_task(studio, result.json()["task_id"])
    return task, next(item for item in overview(studio)["sets"] if item["cover_set_id"] == task["result"]["cover_set_id"])


def select(studio, cover_set, candidate):
    return studio.client.post(studio.base + f"/sets/{cover_set['cover_set_id']}/candidates/{candidate['candidate_id']}/select", json={
        "expected_revision": cover_set["revision"], "expected_active_revision": overview(studio)["active_revision"],
        "expected_sha256": candidate["sha256"]})


def test_full_source_one_call_private_checkpoint_and_immutable_plan_edit(studio):
    initial = overview(studio)
    assert initial["source"]["preview"].encode() == studio.source
    assert not studio.planner.calls and not studio.images.calls
    saved = plan(studio)
    assert len(studio.planner.calls) == 1
    documents = studio.planner.calls[0][1]["source"]["documents"]
    assert next(item["text"].encode() for item in documents if item["document_id"] == "primary") == studio.source
    assert len(documents) == 2
    assert saved["metadata"]["title"] == "The Choice Was Hers"
    task = overview(studio)["tasks"][0]
    assert "response_checkpoint" not in task and "raw" not in task
    checkpoints = list((studio.project / "outputs/covers/poster/tasks").glob("*/response-1.json"))
    assert len(checkpoints) == 1 and json.loads(checkpoints[0].read_text())["raw"] == json.dumps(planning_response())
    path = studio.project / "outputs/covers/poster/plans" / f"{saved['plan_id']}.json"
    before = path.read_bytes()
    directions = deepcopy(saved["directions"])
    directions[1]["composition"] = "A unified triangular composition with the mother as the central focal point."
    edited = studio.client.patch(studio.base + f"/plans/{saved['plan_id']}", json={"expected_hash": saved["plan_hash"], "directions": directions})
    assert edited.status_code == 200, edited.text
    assert edited.json()["plan_id"] != saved["plan_id"]
    assert path.read_bytes() == before
    assert len(studio.planner.calls) == 1
    assert edited.json()["directions"][1]["image_prompt"] != saved["directions"][1]["image_prompt"]
    from core.cover_poster_planner import poster_storage_objects
    _, concepts = poster_storage_objects(edited.json())
    assert directions[1]["composition"] in concepts[1].generation_prompt
    assert saved["directions"][1]["image_prompt"] not in concepts[1].generation_prompt
    stale = studio.client.patch(studio.base + f"/plans/{saved['plan_id']}", json={"expected_hash": saved["plan_hash"], "directions": saved["directions"]})
    assert stale.status_code == 409, stale.text


def test_generate_select_and_zip_references_exact_completed_cover(studio):
    task, cover_set = generate(studio)
    assert task["status"] == "complete"
    assert len(studio.images.calls) == 4
    assert all(candidate["status"] == "ready" for candidate in cover_set["candidates"])
    candidate = cover_set["candidates"][1]
    assert candidate["poster_metadata"]["observed_model"] == ""
    chosen = select(studio, cover_set, candidate)
    assert chosen.status_code == 200, chosen.text
    assert overview(studio)["selected_candidate_id"] == candidate["candidate_id"]
    package = studio.client.get(f"/api/projects/{studio.project_id}/deliverables/package")
    assert package.status_code == 200 and package.content.startswith(b"PK")
    with ZipFile(BytesIO(package.content)) as archive:
        assert archive.testzip() is None
        selected = [name for name in archive.namelist() if name.endswith("selected-cover.png")]
        assert len(selected) == 1
        assert sha256(archive.read(selected[0])).hexdigest() == candidate["sha256"]
        manifests = [name for name in archive.namelist() if name.endswith("manifest.json")]
        manifest = json.loads(archive.read(manifests[0]))
        assert manifest["cover"]["selected_candidate_id"] == candidate["candidate_id"]
    assert len(studio.planner.calls) == 1 and len(studio.images.calls) == 4


def test_selection_and_local_typography_update_epub_and_every_delivery_hash(studio):
    book, classification, serialization = book_fixture("The Choice Was Hers")
    original = render_epub(book, StyleSheet())
    original_index = epub_index(original, serialization, classification)
    deliverables = studio.project / "outputs/deliverables"
    deliverables.mkdir(parents=True)
    epub_path = deliverables / "book.epub"
    epub_path.write_bytes(original)
    publication = studio.project / "outputs/publication"
    publication.mkdir()
    classification_bytes = json.dumps(classification, ensure_ascii=False).encode()
    (publication / "novel-classification.json").write_bytes(classification_bytes)
    (publication / "novel-serialization.json").write_text(json.dumps(serialization))
    _, first = generate(studio)
    candidate = first["candidates"][0]
    assert select(studio, first, candidate).status_code == 200

    def verify_package(expected_candidate):
        package = studio.client.get(f"/api/projects/{studio.project_id}/deliverables/package")
        assert package.status_code == 200, package.text
        with ZipFile(BytesIO(package.content)) as archive:
            epub = archive.read("book.epub")
            assert epub == epub_path.read_bytes()
            image, _ = embedded_cover(epub)
            assert sha256(image).hexdigest() == expected_candidate["sha256"]
            manifest = json.loads(archive.read("package-manifest.json"))
            indexed = {entry["path"]: entry for entry in manifest["files"]}
            assert indexed["book.epub"]["sha256"] == sha256(epub).hexdigest()
            sidecar = json.loads(archive.read("meta/h5-import.json"))
            assert sidecar["status"] == "ready"
            assert sidecar["versions"]["epub_sha256"] == sha256(epub).hexdigest()
            assert sidecar["versions"]["selected_cover_sha256"] == expected_candidate["sha256"]
            assert sidecar["versions"]["content_sha256"] == original_index["content_sha256"]
            packed_classification = archive.read("meta/novel-classification.json")
            assert json.loads(packed_classification) == classification
            assert sidecar["versions"]["classification_sha256"] == sha256(packed_classification).hexdigest()
            assert (publication / "novel-classification.json").read_bytes() == classification_bytes
            assert epub_index(epub, serialization, classification)["content_sha256"] == original_index["content_sha256"]
        return epub

    first_epub = verify_package(candidate)
    url = studio.base + f"/sets/{first['cover_set_id']}/candidates/{candidate['candidate_id']}"
    saved = studio.client.post(url + "/typography", json={"request_id": "epub-typography-01",
        "layout": {"position": "bottom", "font_id": "serif-bold", "color": "#FFE8B0"}})
    assert saved.status_code == 202, saved.text
    done = wait_task(studio, saved.json()["task_id"])
    assert done["status"] == "complete", done
    child = next(row for row in overview(studio)["sets"] if row["cover_set_id"] == done["result"]["cover_set_id"])
    changed = child["candidates"][0]
    assert changed["sha256"] != candidate["sha256"]
    assert epub_path.read_bytes() == first_epub  # Saving a draft never changes the selected cover.
    assert select(studio, child, changed).status_code == 200
    second_epub = verify_package(changed)
    assert second_epub != first_epub
    epub_path.write_bytes(original)  # A later manuscript compile must keep the current selection.
    build_delivery_package(studio.project)
    assert verify_package(changed) == second_epub
    assert len(studio.images.calls) == 4 and len(studio.planner.calls) == 1


def test_failed_epub_rebuild_preserves_selection_and_old_zip_but_blocks_stale_download(studio):
    from core.cover_delivery import delivery_status
    book = book_fixture("The Choice Was Hers")[0]
    original = render_epub(book, StyleSheet())
    deliverables = studio.project / "outputs/deliverables"
    deliverables.mkdir(parents=True)
    epub_path = deliverables / "book.epub"
    epub_path.write_bytes(original)
    _, first = generate(studio)
    initial, chosen = first["candidates"][:2]
    assert select(studio, first, initial).status_code == 200
    archive_path = deliverables / "book-package.zip"
    previous_archive = archive_path.read_bytes()
    current = overview(studio)["sets"][0]
    epub_path.write_bytes(b"broken existing EPUB")
    selected = select(studio, current, chosen)
    assert selected.status_code == 200, selected.text
    assert overview(studio)["selected_candidate_id"] == chosen["candidate_id"]
    status = delivery_status(studio.project)
    assert status["status"] == "failed" and status["retryable"]
    assert status["candidate_id"] == chosen["candidate_id"]
    assert "EPUB" in status["error"]
    assert archive_path.read_bytes() == previous_archive
    assert epub_path.read_bytes() == b"broken existing EPUB"
    url = f"/api/projects/{studio.project_id}/deliverables/package"
    blocked = studio.client.get(url)
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "delivery_not_current"
    retry = studio.client.post(url + "/rebuild")
    assert retry.status_code == 409 and delivery_status(studio.project)["status"] == "failed"
    assert archive_path.read_bytes() == previous_archive
    epub_path.write_bytes(original)
    retry = studio.client.post(url + "/rebuild")
    assert retry.status_code == 200, retry.text
    assert retry.json()["delivery"]["status"] == "ready"
    package = studio.client.get(url)
    assert package.status_code == 200
    with ZipFile(BytesIO(package.content)) as archive:
        assert sha256(embedded_cover(archive.read("book.epub"))[0]).hexdigest() == chosen["sha256"]
    assert len(studio.images.calls) == 4 and len(studio.planner.calls) == 1


def test_crash_between_selection_commit_and_delivery_marker_cannot_serve_previous_zip(studio):
    from core.cover_delivery import delivery_status
    _, first = generate(studio)
    assert select(studio, first, first["candidates"][0]).status_code == 200
    store = CoverStore(studio.project)
    current = store.load(first["cover_set_id"])
    chosen = current.candidates[1]
    store.save(replace(current, selected_candidate_id=chosen.candidate_id,
        candidates=tuple(replace(c, status="selected" if c.candidate_id == chosen.candidate_id else "ready")
                         for c in current.candidates)), expected_revision=current.revision)
    archive_path = studio.project / "outputs/deliverables/book-package.zip"
    prior = archive_path.read_bytes()
    url = f"/api/projects/{studio.project_id}/deliverables/package"
    assert delivery_status(studio.project)["status"] == "stale"
    assert studio.client.get(url).status_code == 409
    assert archive_path.read_bytes() == prior
    assert studio.client.post(url + "/rebuild").status_code == 200
    assert studio.client.get(url).status_code == 200
    assert len(studio.images.calls) == 4


def test_missing_completed_image_cannot_be_committed_as_a_selection(studio):
    _, first = generate(studio)
    candidate = first["candidates"][0]
    studio.service.media_store.delete(studio.project_id, candidate["sha256"], ".png")
    (studio.project / candidate["relative_path"]).unlink()
    rejected = select(studio, first, candidate)
    assert rejected.status_code == 400, rejected.text
    assert overview(studio)["selected_candidate_id"] == ""
    assert len(studio.images.calls) == 4


def test_plan_active_alias_and_replayed_key_do_not_repeat_model(studio):
    studio.planner.block = True
    body = {"request_id": "plan-original-01", "preferences": {"audience": "Mature women"}}
    first = studio.client.post(studio.base + "/plans", json=body)
    assert first.status_code == 202 and studio.planner.entered.wait(2)
    alias_body = {**body, "request_id": "plan-alias-02"}
    alias = studio.client.post(studio.base + "/plans", json=alias_body)
    assert alias.status_code == 202 and alias.json()["task_id"] == first.json()["task_id"]
    changed = studio.client.post(studio.base + "/plans", json={**body, "preferences": {"audience": "Other readers"}})
    assert changed.status_code == 409
    assert overview(studio)["tasks"][0]["status"] == "running"
    assert len(studio.planner.calls) == 1
    studio.planner.release.set()
    assert wait_task(studio, first.json()["task_id"])["status"] == "complete"
    for replay in (body, alias_body):
        repeated = studio.client.post(studio.base + "/plans", json=replay)
        assert repeated.status_code == 202 and repeated.json()["task_id"] == first.json()["task_id"]
    assert len(studio.planner.calls) == 1


def test_finished_batch_replay_and_changed_hash_are_safe(studio):
    saved = plan(studio)
    task, _ = generate(studio, saved)
    body = {"request_id": "batch-request-01", "plan_id": saved["plan_id"], "plan_hash": saved["plan_hash"]}
    replay = studio.client.post(studio.base + "/batches", json=body)
    assert replay.status_code == 202 and replay.json()["task_id"] == task["task_id"]
    changed = studio.client.post(studio.base + "/batches", json={**body, "plan_hash": "0" * 64})
    assert changed.status_code == 409
    assert len(studio.images.calls) == 4 and len(overview(studio)["sets"]) == 1


def test_all_failed_batch_can_regenerate_and_failed_slots_can_retry(studio):
    studio.images.failed_calls = {1, 2, 3, 4}
    saved = plan(studio)
    first_task, first = generate(studio, saved)
    assert first_task["status"] == "failed"
    assert all(item["status"] == "failed" for item in first["candidates"])
    store = CoverStore(studio.project)
    before = store._set_path(first["cover_set_id"]).read_bytes()
    retry = studio.client.post(studio.base + f"/sets/{first['cover_set_id']}/retry", json={"request_id": "retry-all-failed-01"})
    assert retry.status_code == 202, retry.text
    retried_task = wait_task(studio, retry.json()["task_id"])
    assert retried_task["status"] == "complete", retried_task
    assert len(studio.images.calls) == 8
    assert store._set_path(first["cover_set_id"]).read_bytes() == before
    new_task, new_batch = generate(studio, saved, request_id="new-explicit-batch-02")
    assert new_task["status"] == "complete" and new_batch["cover_set_id"] != first["cover_set_id"]
    assert len(studio.images.calls) == 12


def test_partial_failure_retries_only_requested_slot_and_keeps_siblings(studio):
    studio.images.failed_calls = {2}
    task, first = generate(studio)
    assert task["status"] == "complete" and task["result"]["ready"] == 3
    target = first["candidates"][1]
    retry = studio.client.post(studio.base + f"/sets/{first['cover_set_id']}/retry", json={
        "request_id": "retry-one-slot-01", "candidate_ids": [target["candidate_id"]]})
    result = wait_task(studio, retry.json()["task_id"])
    assert result["status"] == "complete", result
    child = next(row for row in overview(studio)["sets"] if row["cover_set_id"] == result["result"]["cover_set_id"])
    assert len(studio.images.calls) == 5
    for index in (0, 2, 3):
        assert child["candidates"][index]["sha256"] == first["candidates"][index]["sha256"]
        assert child["candidates"][index]["candidate_id"] == first["candidates"][index]["candidate_id"]


def test_refinement_failure_preserves_prompt_parent_and_selection_then_retries_one_edit(studio):
    _, first = generate(studio)
    candidate = first["candidates"][1]
    assert select(studio, first, candidate).status_code == 200
    parent_bytes = CoverStore(studio.project)._set_path(first["cover_set_id"]).read_bytes()
    studio.images.failed_calls = {5}
    prompt = "Warm the mother's face while keeping her mature age and family relationships."
    url = studio.base + f"/sets/{first['cover_set_id']}/candidates/{candidate['candidate_id']}/refine"
    started = studio.client.post(url, json={"request_id": "refine-request-01", "prompt": prompt, "keep_composition": True})
    assert started.status_code == 202, started.text
    failed = wait_task(studio, started.json()["task_id"])
    assert failed["status"] == "failed", failed
    assert failed["input"]["prompt"] == prompt
    refreshed = overview(studio)
    assert refreshed["selected_candidate_id"] == candidate["candidate_id"]
    child = next(row for row in refreshed["sets"] if row["cover_set_id"] == failed["result"]["cover_set_id"])
    assert child["candidates"][1]["status"] == "failed"
    assert CoverStore(studio.project)._set_path(first["cover_set_id"]).read_bytes() == parent_bytes
    retry = studio.client.post(studio.base + f"/sets/{child['cover_set_id']}/retry", json={
        "request_id": "refine-retry-01", "candidate_ids": [child["candidates"][1]["candidate_id"]]})
    done = wait_task(studio, retry.json()["task_id"])
    assert done["status"] == "complete", done
    assert [operation for operation, _ in studio.images.calls] == ["generate"] * 4 + ["edit", "edit"]
    assert len(studio.planner.calls) == 1
    assert overview(studio)["selected_candidate_id"] == candidate["candidate_id"]


def test_typography_preview_and_save_are_local_new_versions(studio):
    _, first = generate(studio)
    candidate = first["candidates"][0]
    assert select(studio, first, candidate).status_code == 200
    layout = {"position": "top", "color": "#FFE8B0", "font_id": "serif-bold"}
    url = studio.base + f"/sets/{first['cover_set_id']}/candidates/{candidate['candidate_id']}"
    preview = studio.client.post(url + "/typography-preview", json={"layout": layout})
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"
    assert len(overview(studio)["sets"]) == 1
    saved = studio.client.post(url + "/typography", json={"request_id": "typography-request-01", "layout": layout})
    assert saved.status_code == 202, saved.text
    done = wait_task(studio, saved.json()["task_id"])
    assert done["status"] == "complete", done
    child = next(row for row in overview(studio)["sets"] if row["cover_set_id"] == done["result"]["cover_set_id"])
    changed = child["candidates"][0]
    assert changed["sha256"] == sha256(preview.content).hexdigest()
    assert changed["quality_report"]["assets"]["artwork_sha256"] == candidate["quality_report"]["assets"]["artwork_sha256"]
    assert changed["candidate_id"] != candidate["candidate_id"]
    assert overview(studio)["selected_candidate_id"] == candidate["candidate_id"]
    assert len(studio.images.calls) == 4 and len(studio.planner.calls) == 1


def test_source_change_blocks_generation_without_changing_prior_plan(studio):
    saved = plan(studio)
    plan_path = studio.project / "outputs/covers/poster/plans" / f"{saved['plan_id']}.json"
    before = plan_path.read_bytes()
    (studio.project / "outputs/input/prompt.md").write_bytes(studio.source + b"\nChanged ending.\n")
    assert overview(studio)["plans"][0]["stale"]
    started = studio.client.post(studio.base + "/batches", json={"request_id": "changed-source-batch", "plan_id": saved["plan_id"], "plan_hash": saved["plan_hash"]})
    assert started.status_code == 409, started.text
    assert not studio.images.calls and plan_path.read_bytes() == before
    assert not overview(studio)["sets"]


def test_prior_completed_cover_stays_selectable_after_source_changes(studio):
    _, cover_set = generate(studio)
    candidate = cover_set["candidates"][2]
    (studio.project / "outputs/input/prompt.md").write_bytes(studio.source + b"\nA revised ending.\n")
    changed = overview(studio)
    assert changed["plans"][0]["stale"] is True
    chosen = select(studio, cover_set, candidate)
    assert chosen.status_code == 200, chosen.text
    assert overview(studio)["selected_candidate_id"] == candidate["candidate_id"]
    assert len(studio.images.calls) == 4


def test_wrong_candidate_hash_or_old_selection_revision_cannot_change_selection(studio):
    _, cover_set = generate(studio)
    first, second = cover_set["candidates"][:2]
    first_url = studio.base + f"/sets/{cover_set['cover_set_id']}/candidates/{first['candidate_id']}/select"
    rejected = studio.client.post(first_url, json={"expected_revision": cover_set["revision"],
        "expected_active_revision": 0, "expected_sha256": "0" * 64})
    assert rejected.status_code == 409
    assert overview(studio)["selected_candidate_id"] == ""
    assert select(studio, cover_set, first).status_code == 200
    second_url = studio.base + f"/sets/{cover_set['cover_set_id']}/candidates/{second['candidate_id']}/select"
    stale = studio.client.post(second_url, json={"expected_revision": cover_set["revision"],
        "expected_active_revision": 0, "expected_sha256": second["sha256"]})
    assert stale.status_code == 409
    assert overview(studio)["selected_candidate_id"] == first["candidate_id"]


def test_corrupted_reference_cannot_trigger_paid_refinement(studio):
    _, cover_set = generate(studio)
    candidate = cover_set["candidates"][0]
    assert select(studio, cover_set, candidate).status_code == 200
    assets = candidate["quality_report"]["assets"]
    (studio.project / assets["artwork_relative_path"]).write_bytes(b"incorrect reference bytes")
    start = studio.client.post(studio.base + f"/sets/{cover_set['cover_set_id']}/candidates/{candidate['candidate_id']}/refine", json={
        "request_id": "bad-reference-refine", "prompt": "Warmer light"})
    assert start.status_code == 202
    failed = wait_task(studio, start.json()["task_id"])
    assert failed["status"] == "failed"
    assert len(studio.images.calls) == 4
    assert len(overview(studio)["sets"]) == 1
    assert overview(studio)["selected_candidate_id"] == candidate["candidate_id"]


def test_poster_endpoints_do_not_resolve_another_projects_plan_or_candidate(studio):
    saved = plan(studio)
    _, cover_set = generate(studio, saved)
    other = studio.client.post("/api/projects", json={"title": "Other Book", "genre": "Fantasy"}).json()["id"]
    other_base = f"/api/projects/{other}/poster"
    copied_plan = studio.client.post(other_base + "/batches", json={"request_id": "cross-owner-batch", "plan_id": saved["plan_id"], "plan_hash": saved["plan_hash"]})
    assert copied_plan.status_code == 404, copied_plan.text
    candidate = cover_set["candidates"][0]
    copied_candidate = studio.client.post(other_base + f"/sets/{cover_set['cover_set_id']}/candidates/{candidate['candidate_id']}/select", json={
        "expected_revision": cover_set["revision"], "expected_sha256": candidate["sha256"]})
    assert copied_candidate.status_code == 404
    assert len(studio.images.calls) == 4 and len(studio.planner.calls) == 1


def test_disabled_flag_preserves_read_selection_download_and_blocks_new_calls(studio, monkeypatch):
    _, first = generate(studio)
    monkeypatch.setenv("NOVEL_OS_POSTER_STUDIO", "0")
    assert overview(studio)["enabled"] is False
    assert select(studio, first, first["candidates"][0]).status_code == 200
    assert studio.client.get(f"/api/projects/{studio.project_id}/deliverables/package").status_code == 200
    denied = studio.client.post(studio.base + "/plans", json={"request_id": "disabled-plan-02"})
    assert denied.status_code == 409
    assert len(studio.planner.calls) == 1 and len(studio.images.calls) == 4


def test_select_first_completed_image_while_other_images_generate(studio):
    saved = plan(studio)
    studio.images.block_call = 2
    started = studio.client.post(studio.base + "/batches", json={"request_id": "concurrent-batch-01", "plan_id": saved["plan_id"], "plan_hash": saved["plan_hash"]})
    assert started.status_code == 202 and studio.images.entered.wait(3)
    current = overview(studio)["sets"][0]
    assert current["candidates"][0]["status"] == "ready"
    assert current["candidates"][1]["status"] == "pending"
    selected = select(studio, current, current["candidates"][0])
    assert selected.status_code == 200, selected.text
    studio.images.release.set()
    done = wait_task(studio, started.json()["task_id"])
    assert done["status"] == "complete"
    final = overview(studio)
    assert final["selected_candidate_id"] == current["candidates"][0]["candidate_id"]
    assert final["sets"][0]["selected_candidate_id"] == current["candidates"][0]["candidate_id"]


def test_lost_queued_worker_is_shown_interrupted_without_automatic_restart(studio, monkeypatch):
    class DeferredRunner:
        def __init__(self):
            self.jobs = {}

        def submit(self, kind, fn, meta=None, **kwargs):
            job_id = "deferred-job"
            self.jobs[job_id] = {"status": "running", "kind": kind, "fn": fn, **meta}
            return job_id

        def get(self, job_id):
            return self.jobs.get(job_id)

        def running_for_project(self, *_args, **_kwargs):
            return list(self.jobs.values())

    monkeypatch.setattr(poster_studio, "runner", DeferredRunner())
    body = {"request_id": "queued-plan-01"}
    started = studio.client.post(studio.base + "/plans", json=body)
    assert started.status_code == 202 and started.json()["status"] == "queued"
    monkeypatch.setattr(poster_tasks, "_PROCESS", "new-process-after-restart")
    interrupted = overview(studio)["tasks"][0]
    assert interrupted["status"] == "interrupted"
    replay = studio.client.post(studio.base + "/plans", json=body)
    assert replay.status_code == 202 and replay.json()["task_id"] == started.json()["task_id"]
    assert replay.json()["status"] == "interrupted"
    assert not studio.planner.calls and not studio.images.calls
