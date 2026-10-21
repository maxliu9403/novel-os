"""Local title recovery cannot change story approval or purchase new images."""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import json
import threading

from PIL import Image
import pytest

from api.media import digest
from core.cover_store import CoverConflict, CoverStore
from core.cover_typography import canonical_digest
from core.cover_typography_amendments import (
    load_amendment, read_amendment_preview, validate_candidate_amendment,
)
from tests.test_cover_campaign import campaign_fixture
from tests.test_cover_service import _actual_jpeg, _bound_inputs, _service, _independent_evaluator


@pytest.fixture
def case(tmp_path):
    brief, concepts, recipes = _bound_inputs()
    concepts = [replace(c, scene_plan={**c.scene_plan, "campaign": campaign_fixture(i)}) for i, c in enumerate(concepts)]
    service, client, _ = _service(tmp_path, [_actual_jpeg()] * 4)
    service.visual_evaluator = _independent_evaluator()
    project = tmp_path / "project"
    result = service.generate("project-one", project, brief, concepts, compiled_recipes=recipes,
        source_binding={"direction_id": "direction-" + "a" * 32, "direction_sha256": "b" * 64})
    assert all(c.status == "ready" for c in result.candidates)
    service.image_client = None
    return service, client, project, CoverStore(project), result


def proposal(result, mode="overlay"):
    layout = deepcopy(result.concepts[0].scene_plan["_compiled_recipe"]["title_layout"])
    layout.update(region=[.05, .025, .9, .17], font_size_min=.02, font_size_max=.065, color="#ffe9bd")
    value = {"mode": mode, "title_layout": layout}
    if mode == "contain_panel":
        value.update(artwork_region=[0, .25, 1, .75], background_color="#123456")
    return value


def propose(case, value=None):
    service, _, project, _, result = case
    return service.propose_typography_amendment("project-one", project, result.cover_set_id,
        result.candidates[0].candidate_id, expected_revision=result.revision, proposal=value or proposal(result))


def approve(case, record):
    service, _, project, _, result = case
    return service.approve_typography_amendment("project-one", project, result.cover_set_id,
        result.candidates[0].candidate_id, record["amendment_id"],
        expected_amendment_sha256=record["amendment_sha256"], confirm_layout=True)


def apply(case, record):
    service, _, project, _, result = case
    return service.apply_typography_amendment("project-one", project, result.cover_set_id,
        result.candidates[0].candidate_id, record["amendment_id"], expected_revision=result.revision,
        expected_amendment_sha256=record["amendment_sha256"], source_guard=lambda: None)


def test_preview_is_exact_immutable_and_does_not_mutate_candidate(case):
    _, client, _, store, before = case
    record = propose(case)
    preview = read_amendment_preview(store.root, record["amendment_id"],
        cover_set_id=before.cover_set_id, candidate_id=before.candidates[0].candidate_id)
    assert digest(preview) == record["preview_sha256"]
    assert store.load(before.cover_set_id).to_dict() == before.to_dict()
    assert record["image_calls"] == 0 and len(client.prompts) == 4
    assert load_amendment(store.root, record["amendment_id"])["font_sha256"]
    with pytest.raises(CoverConflict, match="another candidate"):
        read_amendment_preview(store.root, record["amendment_id"], cover_set_id=before.cover_set_id, candidate_id="other")


def test_exact_hash_explicit_approval_and_parent_are_required(case):
    service, _, project, store, before = case
    record = propose(case)
    with pytest.raises(CoverConflict, match="not been approved"):
        apply(case, record)
    with pytest.raises(CoverConflict, match="hash"):
        service.approve_typography_amendment("project-one", project, before.cover_set_id,
            before.candidates[0].candidate_id, record["amendment_id"],
            expected_amendment_sha256="0" * 64, confirm_layout=True)
    with pytest.raises(CoverConflict, match="Confirm"):
        service.approve_typography_amendment("project-one", project, before.cover_set_id,
            before.candidates[0].candidate_id, record["amendment_id"],
            expected_amendment_sha256=record["amendment_sha256"], confirm_layout="true")
    changed = replace(before.candidates[0], quality_report={**before.candidates[0].quality_report, "note": "new review"})
    store.save(replace(before, candidates=(changed, *before.candidates[1:])), expected_revision=before.revision)
    with pytest.raises(CoverConflict, match="parent candidate changed"):
        approve(case, record)


def test_overlay_appends_local_attempt_preserves_recipe_art_and_image_budget(case):
    service, client, project, store, before = case
    record = propose(case)
    approve(case, record)
    result = apply(case, record)
    old, new = before.candidates[0], result.candidates[0]
    assert new.sha256 == record["preview_sha256"] != old.sha256
    assert new.attempt_history[:-1] == old.attempt_history
    assert new.attempt_history[-1]["operation"] == "typography_recomposition"
    assert new.prompt_revision == old.prompt_revision
    assert new.generation_prompt == old.generation_prompt
    assert result.concepts == before.concepts
    assert len(client.prompts) == 4 and new.quality_report["local_recomposition"]["image_provider_called"] is False
    assert new.quality_report["assets"]["artwork_sha256"] == old.quality_report["assets"]["artwork_sha256"]
    assert (project / old.relative_path).read_bytes() != (project / new.relative_path).read_bytes()
    assert "human_acceptance" not in new.quality_report
    assert validate_candidate_amendment(store.root, result, new, result.concepts[0])["amendment_id"] == record["amendment_id"]
    assert new.quality_report["assets"]["title_layout_sha256"] == record["title_layout_sha256"]
    assert new.quality_report["evaluation"]["evaluation_status"] == "complete"
    assert new.quality_report["evaluation"]["relative_path"]


def test_contain_panel_preserves_whole_art_and_evaluates_actual_geometry(case):
    service, _, _, store, before = case
    record = propose(case, proposal(before, "contain_panel"))
    approve(case, record)
    calls = []
    real = service.visual_evaluator.review
    def review(**kwargs):
        calls.append(kwargs)
        return real(**kwargs)
    service.visual_evaluator.review = review
    result = apply(case, record)
    assert len(calls) == 1
    actual = calls[0]
    assert digest(actual["artwork"]) == record["review_artwork_sha256"]
    assert digest(actual["image"]) == record["preview_sha256"]
    assert actual["scene"].campaign["title_layout"] == record["title_layout"]
    assert actual["scene"].campaign["layers"][0]["region"] != before.concepts[0].scene_plan["campaign"]["layers"][0]["region"]
    with Image.open(BytesIO(actual["artwork"])) as img:
        assert img.size == (400, 600)
        assert img.getpixel((0, 0)) == (18, 52, 86)
        assert img.getpixel((200, 599)) == (35, 45, 55)
    assert record["transform"]["source_crop"] == [0, 0, 1, 1]
    assert validate_candidate_amendment(store.root, result, result.candidates[0], result.concepts[0])


@pytest.mark.parametrize("change", [
    {"mode": "crop"}, {"source_crop": [0, .1, 1, .9]}, {"artwork_region": [0, .2, 1, .8]},
    {"background_color": "#000000"},
])
def test_overlay_rejects_unapproved_transform_fields(case, change):
    value = proposal(case[-1]); value.update(change)
    with pytest.raises(ValueError):
        propose(case, value)


def test_contain_title_cannot_obscure_retained_picture(case):
    value = proposal(case[-1], "contain_panel"); value["artwork_region"] = [0, 0, 1, 1]
    with pytest.raises(ValueError, match="outside"):
        propose(case, value)


@pytest.mark.parametrize("mode", ["null", "empty", "remove"])
def test_missing_both_markers_cannot_disguise_applied_amendment_as_legacy(case, mode):
    _, _, _, store, _ = case
    record = propose(case); approve(case, record)
    result = apply(case, record)
    candidate = result.candidates[0]
    report, history = deepcopy(candidate.quality_report), deepcopy(list(candidate.attempt_history))
    if mode == "remove":
        report.pop("typography_amendment"); history[-1].pop("typography_amendment")
        history[-1]["quality_report"].pop("typography_amendment")
        report["assets"].pop("typography_transform")
    else:
        report["typography_amendment"] = history[-1]["typography_amendment"] = None if mode == "null" else {}
    bad = replace(candidate, quality_report=report, attempt_history=tuple(history))
    with pytest.raises(CoverConflict, match="receipt"):
        validate_candidate_amendment(store.root, result, bad, result.concepts[0])


def test_recompose_and_reevaluate_keep_amended_layout_without_new_image_attempt(case):
    service, client, project, _, _ = case
    record = propose(case); approve(case, record)
    result = apply(case, record)
    calls = []
    real = service.visual_evaluator.review
    def review(**kwargs):
        calls.append(kwargs["scene"].campaign["title_layout"])
        return real(**kwargs)
    service.visual_evaluator.review = review
    for method in (service.reevaluate_candidate, service.recompose_candidate):
        previous = result
        result = method("project-one", project, result.cover_set_id, result.candidates[0].candidate_id,
                        expected_revision=result.revision)
        assert result.candidates[0].sha256 == previous.candidates[0].sha256
        assert result.candidates[0].attempt_history == previous.candidates[0].attempt_history
    assert calls == [record["title_layout"]] * 2
    assert len(client.prompts) == 4


def test_tampered_preview_fails_before_approval_or_candidate_mutation(case):
    _, _, _, store, before = case
    record = propose(case)
    path = store.root / "typography-amendments" / record["amendment_id"] / "preview.png"
    path.write_bytes(b"changed")
    with pytest.raises(CoverConflict, match="preview bytes"):
        approve(case, record)
    assert store.load(before.cover_set_id) == before


def test_wrong_owner_or_symlink_root_never_writes_foreign_files(case, tmp_path):
    service, _, project, _, before = case
    with pytest.raises(CoverConflict, match="media owner"):
        service.propose_typography_amendment("wrong", project, before.cover_set_id,
            before.candidates[0].candidate_id, expected_revision=before.revision, proposal=proposal(before))
    foreign = tmp_path / "foreign"; foreign.mkdir()
    link = project / "foreign-covers"; link.symlink_to(foreign, target_is_directory=True)
    with pytest.raises(CoverConflict, match="symlink"):
        service.propose_typography_amendment("project-one", project, before.cover_set_id,
            before.candidates[0].candidate_id, expected_revision=before.revision, proposal=proposal(before), cover_root=link)
    assert list(foreign.iterdir()) == []


def test_concurrent_selection_wins_and_apply_never_overwrites_published_art(case):
    service, _, project, store, before = case
    record = propose(case); approve(case, record)
    entered, release = threading.Event(), threading.Event()
    real = service.visual_evaluator.review
    def review(**kwargs):
        entered.set(); assert release.wait(10)
        return real(**kwargs)
    service.visual_evaluator.review = review
    errors = []
    def worker():
        try: apply(case, record)
        except Exception as exc: errors.append(exc)
    thread = threading.Thread(target=worker); thread.start()
    assert entered.wait(10)
    selected = service.select_candidate(project, before.cover_set_id, before.candidates[0].candidate_id,
        expected_revision=before.revision, expected_active_revision=store.active().revision,
        quality_acceptance_reason="Fixture visual review")
    selected_bytes = (project / selected.candidates[0].relative_path).read_bytes()
    release.set(); thread.join(10)
    assert not thread.is_alive() and len(errors) == 1 and isinstance(errors[0], CoverConflict)
    final = store.load(before.cover_set_id)
    assert final == selected
    assert (project / final.candidates[0].relative_path).read_bytes() == selected_bytes
    assert not list((store.root / "typography-amendments" / record["amendment_id"]).glob("application-*.json"))


def test_original_portfolio_blocker_survives_typography_and_human_acceptance_does_not(case):
    service, client, project, store, before = case
    old = before.candidates[0]
    assert "duplicate_artwork" in old.quality_report["blockers"]  # Same source bytes in fixture.
    updated = replace(old, quality_report={**old.quality_report, "human_acceptance": {"reason": "old image only"}})
    before = store.save(replace(before, candidates=(updated, *before.candidates[1:])), expected_revision=before.revision)
    case = service, client, project, store, before
    record = propose(case); approve(case, record)
    result = apply(case, record)
    report = result.candidates[0].quality_report
    assert report["portfolio_comparison"] == old.quality_report["portfolio_comparison"]
    assert "duplicate_artwork" in report["blockers"] and report["status"] == "blocked"
    assert "human_acceptance" not in report


def test_source_guard_runs_again_before_final_publication(case):
    service, _, project, store, before = case
    record = propose(case); approve(case, record)
    checks = []
    def guard():
        checks.append("checked")
        if len(checks) == 2:
            raise CoverConflict("Source changed during visual review")
    with pytest.raises(CoverConflict, match="Source changed"):
        service.apply_typography_amendment("project-one", project, before.cover_set_id,
            before.candidates[0].candidate_id, record["amendment_id"], expected_revision=before.revision,
            expected_amendment_sha256=record["amendment_sha256"], source_guard=guard)
    assert len(checks) == 2 and store.load(before.cover_set_id) == before


def test_two_local_applications_have_one_cas_winner(case):
    service, _, _, store, before = case
    record = propose(case); approve(case, record)
    barrier = threading.Barrier(2)
    real = service.visual_evaluator.review
    def review(**kwargs):
        barrier.wait(timeout=10)
        return real(**kwargs)
    service.visual_evaluator.review = review
    results, errors = [], []
    def worker():
        try: results.append(apply(case, record))
        except Exception as exc: errors.append(exc)
    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(10)
    assert not any(thread.is_alive() for thread in threads)
    assert len(results) == len(errors) == 1 and isinstance(errors[0], CoverConflict)
    final = store.load(before.cover_set_id)
    assert len(final.candidates[0].attempt_history) == len(before.candidates[0].attempt_history) + 1
    assert validate_candidate_amendment(store.root, final, final.candidates[0], final.concepts[0])


def test_amendment_cannot_become_extra_image_authorization(case):
    service, client, project, _, _ = case
    record = propose(case); approve(case, record)
    result = apply(case, record)
    service.image_client = client
    with pytest.raises(CoverConflict, match="authorize image"):
        service.retry_candidate("project-one", project, result.cover_set_id,
            result.candidates[0].candidate_id, expected_revision=result.revision, repair_codes=["generic_ai_face"])
    assert len(client.prompts) == 4


def test_selected_candidate_cannot_be_replaced_by_new_proposal(case):
    service, _, project, store, before = case
    selected = service.select_candidate(project, before.cover_set_id, before.candidates[0].candidate_id,
        expected_revision=before.revision, expected_active_revision=store.active().revision,
        quality_acceptance_reason="Fixture visual review")
    with pytest.raises(CoverConflict, match="unselected"):
        service.propose_typography_amendment("project-one", project, selected.cover_set_id,
            selected.candidates[0].candidate_id, expected_revision=selected.revision, proposal=proposal(selected))


def test_failed_projection_can_retry_without_overwriting_immutable_application(case, monkeypatch):
    service, _, _, store, before = case
    record = propose(case); approve(case, record)
    real = service._atomic_write
    def fail(path, data):
        if path.name == record["amendment_id"] + ".png":
            raise OSError("Fixture interrupted projection")
        return real(path, data)
    monkeypatch.setattr(service, "_atomic_write", fail)
    with pytest.raises(OSError, match="interrupted"):
        apply(case, record)
    assert store.load(before.cover_set_id) == before
    folder = store.root / "typography-amendments" / record["amendment_id"]
    orphan = list(folder.glob("application-*.json"))
    assert len(orphan) == 1
    orphan_bytes = orphan[0].read_bytes()
    monkeypatch.setattr(service, "_atomic_write", real)
    result = apply(case, record)
    assert len(list(folder.glob("application-*.json"))) == 2
    assert orphan[0].read_bytes() == orphan_bytes
    assert validate_candidate_amendment(store.root, result, result.candidates[0], result.concepts[0])


def test_lexical_parent_traversal_cannot_create_foreign_lock(case, tmp_path):
    service, _, project, _, before = case
    foreign = tmp_path / "foreign"
    with pytest.raises(CoverConflict, match="owner"):
        service.propose_typography_amendment("project-one", project, before.cover_set_id,
            before.candidates[0].candidate_id, expected_revision=before.revision, proposal=proposal(before),
            cover_root=project / ".." / "foreign")
    assert not foreign.exists()


@pytest.mark.parametrize("import_binding", [None, {"snapshot_id": "immutable-import"}])
def test_imported_history_cannot_create_orphan_amendment(case, import_binding):
    service, client, project, store, before = case
    concept = replace(before.concepts[0], scene_plan={**before.concepts[0].scene_plan, "_cover_import": import_binding})
    before = store.save(replace(before, concepts=(concept, *before.concepts[1:])), expected_revision=before.revision)
    with pytest.raises(CoverConflict, match="Imported"):
        propose((service, client, project, store, before))
    assert not (store.root / "typography-amendments").exists()


def test_changed_font_bytes_invalidate_approved_preview(case, tmp_path, monkeypatch):
    from core import cover_typography
    record = propose(case); approve(case, record)
    font = tmp_path / "font.ttf"
    font.write_bytes(cover_typography._font_path("sans-bold").read_bytes() + b"changed")
    monkeypatch.setitem(cover_typography._FONTS, "sans-bold", (str(font),))
    with pytest.raises(ValueError, match="font bytes changed"):
        apply(case, record)


def test_tampered_receipt_is_checked_even_for_already_selected_candidate(case):
    service, _, project, store, _ = case
    record = propose(case); approve(case, record)
    result = apply(case, record)
    selected = service.select_candidate(project, result.cover_set_id, result.candidates[0].candidate_id,
        expected_revision=result.revision, expected_active_revision=store.active().revision,
        quality_acceptance_reason="Fixture review of new layout")
    path = store.root / "typography-amendments" / record["amendment_id"] / "approval.json"
    receipt = json.loads(path.read_text()); receipt["preview_sha256"] = "0" * 64
    path.write_text(json.dumps(receipt))
    with pytest.raises(CoverConflict, match="approval receipt"):
        service.select_candidate(project, selected.cover_set_id, selected.candidates[0].candidate_id,
            expected_revision=selected.revision, expected_active_revision=store.active().revision)
    with pytest.raises(CoverConflict, match="approval receipt"):
        service.reevaluate_candidate("project-one", project, selected.cover_set_id,
            selected.candidates[0].candidate_id, expected_revision=selected.revision)


@pytest.mark.parametrize("mode", [None, "overlay", "contain_panel"])
def test_reevaluation_checkpoint_binds_amendment_and_actual_art_geometry(case, mode):
    service, client, project, _, result = case
    record = None
    if mode is not None:
        record = propose(case, proposal(result, mode))
        approve(case, record)
        result = apply(case, record)
    candidate = result.candidates[0]
    original_attempts = deepcopy(candidate.attempt_history)
    original_recipe = deepcopy(result.concepts[0].scene_plan["_compiled_recipe"])
    prior_checkpoint = candidate.quality_report["evaluation"]["blind_checkpoint"]
    prior_path = project / prior_checkpoint["relative_path"]
    prior_bytes = prior_path.read_bytes()
    prior_paths = set((project / "outputs/covers/evaluations").glob("*/blind-read.json"))
    before_b = []
    real_verify = service.visual_evaluator._verify_complete
    def verify(*args):
        new_paths = set((project / "outputs/covers/evaluations").glob("*/blind-read.json")) - prior_paths
        before_b.extend((path, path.read_bytes()) for path in new_paths)
        return real_verify(*args)
    service.visual_evaluator._verify_complete = verify
    updated = service.reevaluate_candidate("project-one", project, result.cover_set_id,
        candidate.candidate_id, expected_revision=result.revision)
    current = updated.candidates[0]
    checkpoint = current.quality_report["evaluation"]["blind_checkpoint"]
    path = project / checkpoint["relative_path"]
    data = path.read_bytes()
    persisted = json.loads(data)
    assert len(before_b) == 1 and before_b[0] == (path, data)
    assert digest(data) == checkpoint["sha256"]
    assert persisted["image_sha256"] == candidate.sha256 == current.sha256
    assert persisted["recipe_sha256"] == original_recipe["recipe_sha256"]
    assert persisted["source_binding"] == result.concepts[0].scene_plan["_source_binding"]
    expected_art = (record["review_artwork_sha256"] if record is not None
                    else candidate.quality_report["assets"]["artwork_sha256"])
    assert persisted["artwork_sha256"] == expected_art
    assert persisted["blind_evaluation"]["blind_image_sha256"] == expected_art
    if record is not None:
        assert persisted["blind_evaluation"]["typography_amendment"] == candidate.quality_report["typography_amendment"]
        assert persisted["blind_evaluation"]["typography_transform"] == record["transform"]
    else:
        assert "typography_amendment" not in persisted["blind_evaluation"]
        assert "typography_transform" not in persisted["blind_evaluation"]
    assert path != prior_path and prior_path.read_bytes() == prior_bytes
    assert current.attempt_history == original_attempts
    assert updated.concepts[0].scene_plan["_compiled_recipe"] == original_recipe
    assert len(client.prompts) == 4
