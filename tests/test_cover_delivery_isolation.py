"""Delivery projections follow the human selection, not the newest generation."""

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import json
import hashlib
from pathlib import Path
import subprocess
import sys
import threading
import zipfile

import pytest

from core import delivery_package
from core.cover_store import CoverStore
from core.cover_director import CoverArtDirector
from core.cover_prompt_compiler import scene_to_cover_concept
from core.cover_quality import CoverVisualEvaluator
from tests.test_cover_director import _brief as _brief_v2, director_fixture
from tests.test_cover_service import _brief, _concepts, _jpeg, _service


def _assert_package(project, expected):
    root = project / "outputs/deliverables"
    manifest = json.loads((root / "package-manifest.json").read_text())
    metadata = json.loads((root / "covers/cover-set.json").read_text())
    imported = json.loads((root / "meta/h5-import.json").read_text())
    assert metadata == json.loads(json.dumps(expected.to_dict()))
    assert manifest["cover"]["cover_set_id"] == expected.cover_set_id
    assert manifest["cover"]["selected_candidate_id"] == expected.selected_candidate_id
    assert imported["cover"]["selected_candidate_id"] == expected.selected_candidate_id
    candidates = {item.candidate_id: item for item in expected.candidates}
    with zipfile.ZipFile(root / "book-package.zip") as archive:
        assert json.loads(archive.read("covers/cover-set.json")) == metadata
        assert json.loads(archive.read("package-manifest.json")) == manifest
        assert json.loads(archive.read("meta/h5-import.json")) == imported
        entries = [item for item in manifest["files"] if item["role"] == "cover_candidate"]
        assert len(entries) == len(expected.candidates)
        for entry in entries:
            candidate = candidates[entry["candidate_id"]]
            assert entry["sha256"] == candidate.sha256
            assert entry["path"] == str(candidate.relative_path).removeprefix("outputs/deliverables/")
            assert archive.read(entry["path"]) == (project / candidate.relative_path).read_bytes()
        if expected.selected_candidate_id:
            assert imported["cover"]["selection_action"] == "replace"
            assert imported["cover"]["selected"]["sha256"] == candidates[expected.selected_candidate_id].sha256


def test_new_unselected_batch_preserves_active_selection_and_delivery(tmp_path):
    project = tmp_path / "project"
    images = [_jpeg(marker=f"batch-image-{i}".encode()) for i in range(8)]
    service, _, _ = _service(tmp_path, images)
    first = service.generate("project-one", project, _brief(), _concepts())
    selected = service.select_candidate(project, first.cover_set_id, first.candidates[0].candidate_id,
        expected_revision=first.revision, expected_active_revision=0)
    previous_archive = (project / "outputs/deliverables/book-package.zip").read_bytes()

    second = service.generate("project-one", project, _brief(), _concepts())

    assert CoverStore(project).active().cover_set_id == first.cover_set_id
    assert CoverStore(project).active().revision == 1
    assert (project / "outputs/deliverables/covers/selected-cover.jpg").read_bytes() == images[0]
    assert set(item.relative_path for item in first.candidates).isdisjoint(
        item.relative_path for item in second.candidates)
    assert all((project / item.relative_path).read_bytes() == images[i + 4]
        for i, item in enumerate(second.candidates))
    assert (project / "outputs/deliverables/book-package.zip").read_bytes() == previous_archive
    _assert_package(project, selected)

    same = service.select_candidate(project, first.cover_set_id, selected.selected_candidate_id,
        expected_revision=selected.revision, expected_active_revision=1)
    _assert_package(project, same)
    chosen_second = service.select_candidate(project, second.cover_set_id, second.candidates[1].candidate_id,
        expected_revision=second.revision, expected_active_revision=1)
    _assert_package(project, chosen_second)
    reactivated = service.select_candidate(project, first.cover_set_id, selected.selected_candidate_id,
        expected_revision=selected.revision, expected_active_revision=2)
    _assert_package(project, reactivated)
    assert (project / "outputs/deliverables/covers/selected-cover.jpg").read_bytes() == images[0]


def test_legacy_shared_pending_paths_restore_each_selected_batch_from_media(tmp_path):
    project = tmp_path / "project"
    images = [_jpeg(marker=f"legacy-image-{i}".encode()) for i in range(8)]
    service, _, _ = _service(tmp_path, images)
    store = CoverStore(project)
    old_sets = []
    for set_number in range(2):
        generated = service.generate("project-one", project, _brief(), _concepts())
        legacy = replace(generated, cover_set_id=f"cover-{str(set_number + 1) * 32}",
            candidates=tuple(replace(item, relative_path=f"outputs/deliverables/covers/pending/cover-{i:02d}.jpg")
                for i, item in enumerate(generated.candidates, 1)))
        old_sets.append(store.create(legacy))
    first, second = old_sets
    # Historical shared files may be absent or hold an unrelated batch's bytes.
    for item in first.candidates[1:]:
        (project / item.relative_path).write_bytes(images[7])

    selected_first = service.select_candidate(project, first.cover_set_id, first.candidates[0].candidate_id,
        expected_revision=first.revision, expected_active_revision=0)
    _assert_package(project, selected_first)
    selected_second = service.select_candidate(project, second.cover_set_id, second.candidates[1].candidate_id,
        expected_revision=second.revision, expected_active_revision=1)
    _assert_package(project, selected_second)
    same_first = service.select_candidate(project, first.cover_set_id, selected_first.selected_candidate_id,
        expected_revision=selected_first.revision, expected_active_revision=2)
    _assert_package(project, same_first)
    assert all((project / item.relative_path).read_bytes() == images[i]
        for i, item in enumerate(same_first.candidates))


def test_generation_cannot_follow_pending_symlink_outside_project(tmp_path):
    project = tmp_path / "project"
    pending = project / "outputs/deliverables/covers/pending"
    pending.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    pending.symlink_to(outside, target_is_directory=True)
    service, _, _ = _service(tmp_path, [_jpeg(marker=str(i).encode()) for i in range(4)])

    generated = service.generate("project-one", project, _brief(), _concepts())

    assert generated.status == "failed"
    assert all("within the project" in item.error for item in generated.candidates)
    assert not list(outside.iterdir())


@pytest.mark.parametrize("publisher", ["service_rebuild", "direct_package"])
def test_selection_waits_for_older_delivery_transaction(tmp_path, monkeypatch, publisher):
    project = tmp_path / "project"
    images = [_jpeg(marker=f"concurrent-{i}".encode()) for i in range(8)]
    service, _, _ = _service(tmp_path, images)
    first = service.generate("project-one", project, _brief(), _concepts())
    second = service.generate("project-one", project, _brief(), _concepts())
    selected_first = service.select_candidate(project, first.cover_set_id, first.candidates[0].candidate_id,
        expected_revision=first.revision, expected_active_revision=0)
    publisher_paused = threading.Event()
    release_publisher = threading.Event()
    selection_started = threading.Event()
    publisher_thread = None

    def pause_publisher():
        if threading.get_ident() == publisher_thread:
            publisher_paused.set()
            assert release_publisher.wait(5), "test did not release the publication transaction"

    if publisher == "service_rebuild":
        original_project = service._project_selected

        def project_selected(*args):
            pause_publisher()
            return original_project(*args)

        monkeypatch.setattr(service, "_project_selected", project_selected)
    else:
        original_archive = delivery_package._write_archive

        def write_archive(*args):
            pause_publisher()
            return original_archive(*args)

        monkeypatch.setattr(delivery_package, "_write_archive", write_archive)

    def publish_old_selection():
        nonlocal publisher_thread
        publisher_thread = threading.get_ident()
        if publisher == "service_rebuild":
            service._rebuild_delivery(project, selected_first)
        else:
            delivery_package.build_delivery_package(project, cover_set=selected_first)

    def select_second():
        selection_started.set()
        return service.select_candidate(project, second.cover_set_id, second.candidates[1].candidate_id,
            expected_revision=second.revision, expected_active_revision=1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        publishing = executor.submit(publish_old_selection)
        try:
            assert publisher_paused.wait(5)
            selecting = executor.submit(select_second)
            assert selection_started.wait(5)
            with pytest.raises(FutureTimeout):
                selecting.result(timeout=0.2)
            assert CoverStore(project).active().cover_set_id == first.cover_set_id
        finally:
            release_publisher.set()
        publishing.result(timeout=5)
        selected_second = selecting.result(timeout=5)

    assert CoverStore(project).active().cover_set_id == second.cover_set_id
    assert (project / "outputs/deliverables/covers/selected-cover.jpg").read_bytes() == images[5]
    _assert_package(project, selected_second)


def test_delivery_lock_coordinates_a_separate_process_and_releases_after_error(tmp_path):
    script = """
import sys
sys.path.insert(0, sys.argv[2])
from core.delivery_package import delivery_lock
print('ready', flush=True)
with delivery_lock(sys.argv[1]):
    print('acquired', flush=True)
"""
    child = None
    try:
        with pytest.raises(RuntimeError, match="test failure"):
            with delivery_package.delivery_lock(tmp_path / "project"):
                child = subprocess.Popen([sys.executable, "-c", script, str(tmp_path / "project"),
                    str(Path(delivery_package.__file__).parent)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                assert child.stdout.readline().strip() == "ready"
                with pytest.raises(subprocess.TimeoutExpired):
                    child.communicate(timeout=0.2)
                raise RuntimeError("test failure")
        output, errors = child.communicate(timeout=5)
        assert child.returncode == 0, errors
        assert output.strip() == "acquired"
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate()


def test_candidate_retry_cannot_change_bytes_during_archive_publication(tmp_path, monkeypatch):
    project = tmp_path / "project"
    images = [_jpeg(marker=f"retry-{i}".encode()) for i in range(5)]
    service, _, _ = _service(tmp_path, images)
    service.visual_evaluator = CoverVisualEvaluator(provider="fixture", model="fixture",
        complete=lambda *_args: {"status": "blocked", "blockers": ["generic_ai_face"],
            "repair_codes": ["generic_ai_face"], "evidence": ["face region"], "findings": []})
    service.thumbnail_projector = lambda *_args, **_kwargs: b"thumbnail"
    brief = _brief_v2()
    direction = CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4)
    concepts = [scene_to_cover_concept(brief, scene) for scene in direction.plans]
    generated = service.generate("project-one", project, brief, concepts)
    selected = service.select_candidate(project, generated.cover_set_id, generated.candidates[0].candidate_id,
        expected_revision=generated.revision, expected_active_revision=0)
    archive_paused, release_archive = threading.Event(), threading.Event()
    retry_generated, retry_ready, release_retry = threading.Event(), threading.Event(), threading.Event()
    original_archive = delivery_package._write_archive
    original_persist, original_rebuild = service._persist_generated, service._rebuild_delivery
    publisher_thread = None

    def write_archive(*args):
        if threading.get_ident() == publisher_thread:
            archive_paused.set()
            assert release_archive.wait(5)
        return original_archive(*args)

    def persist_generated(*args, **kwargs):
        retry_generated.set()  # Provider completed while the publisher owns the lock.
        return original_persist(*args, **kwargs)

    def rebuild_delivery(*args):
        retry_ready.set()
        assert release_retry.wait(5)
        return original_rebuild(*args)

    monkeypatch.setattr(delivery_package, "_write_archive", write_archive)
    monkeypatch.setattr(service, "_persist_generated", persist_generated)
    monkeypatch.setattr(service, "_rebuild_delivery", rebuild_delivery)

    def publish():
        nonlocal publisher_thread
        publisher_thread = threading.get_ident()
        return delivery_package.build_delivery_package(project)

    with ThreadPoolExecutor(max_workers=2) as executor:
        publishing = executor.submit(publish)
        try:
            assert archive_paused.wait(5)
            retrying = executor.submit(service.retry_candidate, "project-one", project,
                selected.cover_set_id, selected.candidates[1].candidate_id,
                expected_revision=selected.revision, repair_codes=["generic_ai_face"])
            assert retry_generated.wait(5)
            with pytest.raises(FutureTimeout):
                retrying.result(timeout=0.2)
            assert (project / selected.candidates[1].relative_path).read_bytes() == images[1]
            release_archive.set()
            previous = publishing.result(timeout=5)
            assert retry_ready.wait(5)
            with zipfile.ZipFile(previous.archive_path) as archive:
                manifest = json.loads(archive.read("package-manifest.json"))
                assert all(hashlib.sha256(archive.read(item["path"])).hexdigest() == item["sha256"]
                    for item in manifest["files"])
        finally:
            release_archive.set()
            release_retry.set()
        retried = retrying.result(timeout=5)
    _assert_package(project, retried)
