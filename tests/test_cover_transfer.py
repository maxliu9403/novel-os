from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from api.media import LocalMediaStore
from core.cover_director import CoverArtDirector
from core.cover_models import CoverCandidate, CoverSet
from core.cover_prompt_compiler import scene_to_cover_concept
from core.cover_sources import CoverSourceContext, CoverSourceSelector, capture_source, canonical_hash
from core.cover_store import CoverStore, CoverConflict
from core.cover_transfer import transfer_workshop_covers, validate_import_selection
from tests.test_cover_director import _brief, director_fixture
from tests.test_cover_service import _jpeg


def setup_transfer(tmp_path, *, dependency=False, approved=True):
    session_id = "a" * 32
    project_id = "target-novel"
    session, project = tmp_path / "workshop" / session_id, tmp_path / "projects" / project_id
    session.mkdir(parents=True)
    (project / "outputs/input").mkdir(parents=True)
    text = "# Complete story\nA woman withdraws the shared key.\n"
    (session / "session.json").write_text(json.dumps({"id": session_id, "revision": 4, "prompt_md": text}))
    (project / "outputs/input/prompt.md").write_text(text)
    if dependency:
        (session / "characters.md").write_text("Mara and Oren share an apartment.")
        (project / "characters.md").write_text("Mara and Oren share an apartment.")
    resolver = (lambda selector, prompt: {"characters": "characters.md"}) if dependency else None
    source_store, target_store = CoverStore(session, cover_root=session / "covers"), CoverStore(project)
    source = CoverSourceContext(source_store.root, f"workspace:workshop:{session_id}",
        workshop_resolver=lambda identifier: session, dependency_resolver=resolver)
    target = CoverSourceContext(target_store.root, f"workspace:project:{project_id}",
        project_resolver=lambda identifier: project, dependency_resolver=resolver)
    snapshot = capture_source(source, CoverSourceSelector("workshop_current", session_id=session_id))
    brief = replace(_brief(), source_prompt_sha256=snapshot.source_content_sha256)
    direction = CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4)
    binding = {"owner_scope": source.owner_scope, "snapshot_id": snapshot.snapshot_id,
               "snapshot_sha256": snapshot.snapshot_sha256, "source_bundle_sha256": snapshot.source_bundle_sha256,
               "source_content_sha256": snapshot.source_content_sha256}
    direction = source_store.create_direction(replace(direction, schema_version=2,
        source_binding=binding, compiled_recipes={}), brief=brief.to_dict())
    if approved:
        direction = source_store.approve_direction(direction.direction_id,
            expected_brief_sha256=brief.source_prompt_sha256, approved_direction_sha256=direction.direction_sha256)
        source_store._write_json(source_store.root / "approvals" / f"{direction.direction_id}.json", {
            "source_confirmed": True, "revoked": False, "direction_sha256": direction.direction_sha256,
            "binding_sha256": canonical_hash(binding), "recipes_sha256": canonical_hash({}),
            "batch_reserved": True, "extra_image_attempts": 1})
    binding.update(direction_id=direction.direction_id, direction_sha256=direction.direction_sha256)
    concepts = [scene_to_cover_concept(brief, plan) for plan in direction.plans]
    concepts = [replace(concept, scene_plan={**concept.scene_plan, "_source_binding": deepcopy(binding),
                "_generation_authorization": {"extra_image_attempts": 1, "image_attempts_reserved": 1}}) for concept in concepts]
    namespace = "workshop-" + session_id
    cover_set = CoverSet.new(namespace, brief, concepts)
    media = LocalMediaStore(tmp_path / "media")
    candidates = []
    for index, candidate in enumerate(cover_set.candidates):
        data = _jpeg(marker=str(index).encode())
        digest = sha256(data).hexdigest()
        relative = f"covers/pending/cover-{index}.jpg"
        (session / relative).parent.mkdir(parents=True, exist_ok=True)
        (session / relative).write_bytes(data)
        media.put(namespace, digest, ".jpg", data)
        old = _jpeg(marker=f"old-{index}".encode())
        old_digest = sha256(old).hexdigest()
        media.put(namespace, old_digest, ".jpg", old)
        artwork = _jpeg(marker=f"artwork-{index}".encode())
        artwork_digest = sha256(artwork).hexdigest()
        media.put(namespace, artwork_digest, ".jpg", artwork)
        artwork_path = f"covers/artwork/{artwork_digest}.jpg"
        (session / artwork_path).parent.mkdir(parents=True, exist_ok=True)
        (session / artwork_path).write_bytes(artwork)
        candidates.append(replace(candidate, status="selected" if index == 0 else "ready", relative_path=relative,
            media_id=f"source-{index}", sha256=digest, width=2048, height=3072, content_type="image/jpeg",
            provider="fixture", model="fixture", quality_report={"artwork_sha256": artwork_digest,
                "artwork_media_id": f"artwork-{index}", "artwork_relative_path": artwork_path},
            attempt_history=({"attempt_id": f"old-{index}", "media_id": f"old-media-{index}",
                "image_sha256": old_digest, "relative_path": relative},)))
    cover_set = source_store.create(replace(cover_set, candidates=tuple(candidates), status="selected",
        selected_candidate_id=candidates[0].candidate_id))
    source_store.set_active(cover_set.cover_set_id, expected_revision=0)
    analysis = source_store.root / "analyses/analysis-history/attempts/segment.json"
    analysis.parent.mkdir(parents=True)
    analysis.write_text('{"coverage_status":"partial","exact_quote":"shared key"}')
    registrations = {}
    def media_add(**fields):
        key = (fields["project_id"], fields["sha"])
        registrations.setdefault(key, {"id": "media-" + str(len(registrations) + 1), **fields})
        return SimpleNamespace(**registrations[key])
    arguments = (source, target, source_store, target_store, media, media_add, project_id)
    return arguments, cover_set, snapshot, registrations


def test_transfer_is_complete_idempotent_nonactive_and_has_no_new_budget(tmp_path):
    args, original, snapshot, registrations = setup_transfer(tmp_path)
    source, target, source_store, target_store, media, _, project_id = args
    original_bytes = (source_store.sets_dir / f"{original.cover_set_id}.json").read_bytes()
    manifest = transfer_workshop_covers(*args)
    assert transfer_workshop_covers(*args) == manifest
    assert len(registrations) == 12  # current, previous attempt and artwork for each candidate
    assert manifest["new_image_budget"] == 0
    assert not target_store.active().cover_set_id
    assert not target_store.directions_dir.exists()
    imported = target_store.list()[0]
    assert imported.cover_set_id != original.cover_set_id
    assert imported.selected_candidate_id == ""
    assert imported.status == "ready"
    assert all(candidate.status == "ready" for candidate in imported.candidates)
    assert all(item.scene_plan["_generation_authorization"] == {
        "extra_image_attempts": 0, "image_attempts_reserved": 1} for item in imported.concepts)
    assert all(item.scene_plan["_original_generation_authorization"]["extra_image_attempts"] == 1 for item in imported.concepts)
    folder = target_store.root / "imports" / manifest["transfer_key"] / "provenance"
    assert (folder / "sources" / snapshot.snapshot_id / "documents/primary.md").read_bytes() == snapshot.primary_bytes
    assert (folder / "analyses/analysis-history/attempts/segment.json").is_file()
    assert (source_store.sets_dir / f"{original.cover_set_id}.json").read_bytes() == original_bytes
    for candidate in imported.candidates:
        assert sha256((target_store.project_path / candidate.relative_path).read_bytes()).hexdigest() == candidate.sha256
        assert media.read(project_id, candidate.sha256, ".jpg") is not None
        assert candidate.attempt_history[0]["media_id"].startswith("media-")
        assert candidate.quality_report["artwork_media_id"].startswith("media-")
    validate_import_selection(target, target_store, imported, imported.candidates[0])


@pytest.mark.parametrize("remove_markers", [False, True])
def test_amended_workshop_history_is_rejected_before_any_import_or_media_registration(tmp_path, remove_markers):
    from tests.test_cover_campaign import campaign_fixture
    from tests.test_cover_service import _actual_jpeg, _bound_inputs, _independent_evaluator, _service
    from core.cover_typography_amendments import validate_candidate_amendment

    args, original, _, registrations = setup_transfer(tmp_path)
    source_store, target_store = args[2:4]
    brief, concepts, recipes = _bound_inputs()
    concepts = [replace(c, scene_plan={**c.scene_plan, "campaign": campaign_fixture(i)})
                for i, c in enumerate(concepts)]
    service, image_client, _ = _service(tmp_path, [_actual_jpeg()] * 4)
    service.visual_evaluator = _independent_evaluator()
    binding = original.concepts[0].scene_plan["_source_binding"]
    generated = service.generate(original.project_id, source_store.project_path, brief, concepts,
        compiled_recipes=recipes, source_binding=binding, cover_root=source_store.root)
    candidate = generated.candidates[0]
    layout = deepcopy(generated.concepts[0].scene_plan["_compiled_recipe"]["title_layout"])
    layout["region"] = [.05, .025, .9, .17]
    record = service.propose_typography_amendment(original.project_id, source_store.project_path,
        generated.cover_set_id, candidate.candidate_id, expected_revision=generated.revision,
        proposal={"mode": "overlay", "title_layout": layout}, cover_root=source_store.root)
    service.approve_typography_amendment(original.project_id, source_store.project_path,
        generated.cover_set_id, candidate.candidate_id, record["amendment_id"],
        expected_amendment_sha256=record["amendment_sha256"], confirm_layout=True, cover_root=source_store.root)
    amended = service.apply_typography_amendment(original.project_id, source_store.project_path,
        generated.cover_set_id, candidate.candidate_id, record["amendment_id"], expected_revision=generated.revision,
        expected_amendment_sha256=record["amendment_sha256"], source_guard=lambda: None, cover_root=source_store.root)
    assert validate_candidate_amendment(source_store.root, amended, amended.candidates[0], amended.concepts[0])
    if remove_markers:
        changed = deepcopy(amended.candidates[0].to_dict())
        changed["quality_report"].pop("typography_amendment")
        changed["attempt_history"][-1].pop("typography_amendment")
        # Simulate corrupted persisted input; the ordinary store already rejects
        # rewriting an attempt, and transfer must independently fail closed.
        amended = replace(amended, candidates=(CoverCandidate.from_dict(changed), *amended.candidates[1:]))
        (source_store.sets_dir / f"{amended.cover_set_id}.json").write_text(json.dumps(amended.to_dict()))
    before = (source_store.sets_dir / f"{amended.cover_set_id}.json").read_bytes()
    with pytest.raises(CoverConflict, match="Typography"):
        transfer_workshop_covers(*args)
    assert registrations == {} and target_store.list() == []
    assert not (target_store.root / "imports").exists()
    assert not (target_store.root / "typography-amendments").exists()
    assert (source_store.sets_dir / f"{amended.cover_set_id}.json").read_bytes() == before
    assert len(image_client.prompts) == 4  # Transfer cannot initiate corrective drawing.


def test_dependency_changes_make_import_historical_even_with_identical_primary(tmp_path):
    args, _, _, _ = setup_transfer(tmp_path, dependency=True)
    (args[3].project_path / "characters.md").write_text("Their relationship has changed.")
    manifest = transfer_workshop_covers(*args)
    assert manifest["imported_sets"][0]["source_match"] == "historical"
    imported = args[3].list()[0]
    assert imported.status == "stale"
    assert imported.concepts[0].scene_plan["_cover_import"]["source_match"] == "mismatched"
    with pytest.raises(CoverConflict, match="current skeleton and dependencies"):
        validate_import_selection(args[1], args[3], imported, imported.candidates[0])


def test_selection_rechecks_current_head_and_import_integrity(tmp_path):
    args, original, _, _ = setup_transfer(tmp_path)
    manifest = transfer_workshop_covers(*args)
    imported = args[3].list()[0]
    primary = args[3].project_path / "outputs/input/prompt.md"
    before = primary.read_bytes()
    primary.write_text("New story")
    with pytest.raises(CoverConflict, match="current skeleton"):
        validate_import_selection(args[1], args[3], imported, imported.candidates[0])
    primary.write_bytes(before)
    folder = args[3].root / "imports" / manifest["transfer_key"] / "provenance"
    (folder / "sets" / f"{original.cover_set_id}.json").write_text("{}")
    with pytest.raises(CoverConflict, match="provenance changed"):
        validate_import_selection(args[1], args[3], imported, imported.candidates[0])


def test_completed_rejected_import_can_be_selected_again_without_quality_override(tmp_path):
    args, _, _, _ = setup_transfer(tmp_path)
    transfer_workshop_covers(*args)
    store = args[3]
    imported = store.list()[0]
    candidate = replace(imported.candidates[0], status="rejected", quality_report={"status": "blocked"})
    imported = store.save(replace(imported, candidates=(candidate, *imported.candidates[1:])),
        expected_revision=imported.revision)
    validate_import_selection(args[1], store, imported, candidate)


def test_transfer_without_approval_remains_viewable_but_not_selectable(tmp_path):
    args, _, _, _ = setup_transfer(tmp_path, approved=False)
    transfer_workshop_covers(*args)
    imported = args[3].list()[0]
    assert not imported.concepts[0].scene_plan["_cover_import"]["source_approval_verified"]
    with pytest.raises(CoverConflict, match="source approval"):
        validate_import_selection(args[1], args[3], imported, imported.candidates[0])


def test_cross_workspace_and_symlink_source_are_rejected_before_registration(tmp_path):
    args, _, _, registrations = setup_transfer(tmp_path)
    altered = list(args)
    altered[1] = replace(args[1], owner_scope="other:project:target-novel")
    with pytest.raises(CoverConflict, match="same workspace"):
        transfer_workshop_covers(*altered)
    assert registrations == {}
    (args[2].root / "escaped").symlink_to(tmp_path / "projects", target_is_directory=True)
    with pytest.raises(CoverConflict, match="symlink"):
        transfer_workshop_covers(*args)
    assert registrations == {}


def test_candidate_image_digest_mismatch_does_not_publish_sets(tmp_path):
    args, original, _, registrations = setup_transfer(tmp_path)
    candidate = original.candidates[0]
    args[4].delete(original.project_id, candidate.sha256, ".jpg")
    (args[2].project_path / candidate.relative_path).write_bytes(_jpeg(marker=b"wrong"))
    with pytest.raises(CoverConflict, match="digest"):
        transfer_workshop_covers(*args)
    assert args[3].list() == []


def test_superseded_artwork_only_in_historical_report_is_registered(tmp_path):
    args, original, _, registrations = setup_transfer(tmp_path)
    data = _jpeg(marker=b"superseded-artwork")
    digest = sha256(data).hexdigest()
    args[4].put(original.project_id, digest, ".jpg", data)
    report = args[2].root / "evaluations/evaluation-old/report.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({"assets": {"artwork_sha256": digest, "artwork_media_id": "old-artwork-only", "artwork_relative_path": "covers/artwork/old.jpg"}}))
    manifest = transfer_workshop_covers(*args)
    assert digest in manifest["assets"]
    assert manifest["id_map"]["media"]["old-artwork-only"].startswith("media-")
    assert len(registrations) == 13


def test_select_cannot_substitute_another_candidate_or_media_record(tmp_path):
    args, _, _, _ = setup_transfer(tmp_path)
    transfer_workshop_covers(*args)
    imported = args[3].list()[0]
    forged = replace(imported.candidates[0], media_id="some-other-media")
    with pytest.raises(CoverConflict, match="immutable transfer record"):
        validate_import_selection(args[1], args[3], imported, forged)
