from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
from types import SimpleNamespace
from zipfile import ZipFile

from PIL import Image, ImageChops
import pytest

from api.cover_service import CoverService
from api.media import LocalMediaStore
from api.poster_image_service import PosterImageService
from core.cover_models import CoverSet
from core.cover_poster_typography import compose_poster_cover, prepare_poster_layout
from core.cover_repair_lineage import validate_lineage
from core.cover_store import CoverConflict, CoverStore
from core.cover_typography import CoverTypographyError
from core.image_client import GeneratedImage, ImageClientError
from tests.test_cover_service import Registrar, _brief, _concepts
from tests.test_cover_typography_amendments import case as legacy_typography_case, propose, approve, apply


def art(color=(25, 35, 55)):
    stream = BytesIO()
    Image.new("RGB", (400, 600), color).save(stream, format="PNG")
    return stream.getvalue()


class Images:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.settings = SimpleNamespace(model="gpt-image-2", size="400x600")
        self.calls = []

    def _result(self, prompt):
        self.calls.append(prompt)
        result = self.outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return GeneratedImage(result, "image/png", 400, 600, f"request-{len(self.calls)}", self.settings.model,
                              "400x600")

    def generate(self, prompt):
        return self._result(prompt)

    def edit(self, prompt, image_path, *, expected_reference_sha256):
        assert sha256(image_path.read_bytes()).hexdigest() == expected_reference_sha256
        return replace(self._result(prompt), operation="edit", reference_image_sha256=expected_reference_sha256,
                       reference_transport="test-bytes")


def setup(tmp_path, outcomes=None, *, layout=None):
    project = tmp_path / "project"
    project.mkdir()
    images = Images(outcomes or [art((25 + i, 30, 50)) for i in range(4)])
    service = CoverService(image_client=images, media_store=LocalMediaStore(tmp_path / "media"), media_add=Registrar())
    service._quality_report = lambda *_a, **_k: pytest.fail("Poster images must not call the legacy per-image semantic gate")
    concepts = tuple(replace(concept, scene_plan={"pipeline_kind": "skeleton_poster", "_poster_recipe": {
        "schema_version": "poster-recipe.v1", "image_prompt": concept.generation_prompt, "title_render_mode": "composed",
        "title_layout": prepare_poster_layout(layout or {}, expected_title=_brief().title)}}) for concept in _concepts())
    current = CoverStore(project).create(CoverSet.new("project-one", _brief(), concepts, compiler_version="poster.v1"))
    return project, service, images, PosterImageService(service), current


def test_poster_provider_provenance_uses_actual_client_without_mutating_legacy_service(tmp_path):
    project, service, images, _, created = setup(tmp_path)
    assert service.provider == "openai_compatible"
    images.settings.provider = "codex"
    poster = PosterImageService(service)
    result = poster.generate("project-one", project, created.cover_set_id)
    assert service.provider == "openai_compatible"
    assert poster.service.provider == "codex"
    assert all(candidate.provider == "codex" for candidate in result.candidates)
    assert all(candidate.safe_request_parameters["observed_model"] == "" for candidate in result.candidates)


def test_image_provider_failures_do_not_save_credentials_in_candidate_or_attempt(tmp_path):
    failure = ImageClientError('{"api_key":"private custom platform key","access_token":"private-token"}')
    project, _, _, poster, created = setup(tmp_path, [failure] * 4)
    result = poster.generate("project-one", project, created.cover_set_id)
    assert result.status == "failed"
    saved = CoverStore(project)._set_path(result.cover_set_id).read_text()
    assert "private custom" not in saved and "private-token" not in saved
    assert all("redacted" in item.error for item in result.candidates)


def test_incremental_images_are_directly_selectable_while_other_slots_are_pending(tmp_path):
    project, service, images, poster, created = setup(tmp_path)
    observations = []
    def progress(event):
        if event["phase"] == "candidate_saved":
            current = CoverStore(project).load(created.cover_set_id)
            observations.append([item.status for item in current.candidates])
            if len(observations) == 1:
                assert current.candidates[1].status == "pending"
                service.select_candidate(project, current.cover_set_id, current.candidates[0].candidate_id,
                                         expected_revision=current.revision, expected_active_revision=0)
    result = poster.generate("project-one", project, created.cover_set_id, progress=progress)
    assert len(images.calls) == 4
    assert len(observations) == 4
    assert result.status == "selected" and result.selected_candidate_id == result.candidates[0].candidate_id
    assert result.candidates[0].status == "selected"
    assert all(c.status in {"ready", "selected"} for c in result.candidates)
    for candidate in result.candidates:
        assert candidate.safe_request_parameters["observed_model"] == ""
        assert candidate.safe_request_parameters["model_verified"] is False
        assert candidate.quality_report["advisory_status"] == "not_run"
        assert len(candidate.quality_report["assets"]["font_sha256"]) == 64
    assert CoverStore(project).active().cover_set_id == created.cover_set_id
    assert (project / "outputs/deliverables/covers/selected-cover.png").read_bytes() == (
        project / result.candidates[0].relative_path).read_bytes()
    with ZipFile(project / "outputs/deliverables/book-package.zip") as archive:
        manifest = json.loads(archive.read("package-manifest.json"))
        assert manifest["cover"]["selected_candidate_id"] == result.candidates[0].candidate_id
        assert sha256(archive.read("covers/selected-cover.png")).hexdigest() == result.candidates[0].sha256


def test_failed_slots_retry_in_new_version_without_repeating_completed_images(tmp_path):
    failure = ImageClientError("busy", retryable=True)
    project, service, images, poster, created = setup(tmp_path, [art(), failure, art((50, 20, 50)), failure, art((60, 20, 60)), art((70, 20, 70))])
    first = poster.generate("project-one", project, created.cover_set_id)
    original = CoverStore(project)._set_path(first.cover_set_id).read_bytes()
    result = poster.retry("project-one", project, first.cover_set_id)
    assert len(images.calls) == 6
    assert result.cover_set_id != first.cover_set_id
    assert result.status == "ready"
    assert CoverStore(project)._set_path(first.cover_set_id).read_bytes() == original
    assert result.candidates[0] == first.candidates[0]
    assert result.candidates[2] == first.candidates[2]
    assert len(result.candidates[1].attempt_history) == 2
    validate_lineage(CoverStore(project), result)
    selected = service.select_candidate(project, result.cover_set_id, result.candidates[1].candidate_id,
                                       expected_revision=result.revision, expected_active_revision=0)
    assert selected.candidates[1].status == "selected"


def test_artwork_checkpoint_retries_local_composition_without_a_second_image_call(tmp_path, monkeypatch):
    import api.poster_image_service as module
    project, _, images, poster, created = setup(tmp_path)
    real = module.compose_poster_cover
    def composition_failure(data, *args, **kwargs):
        if Image.open(BytesIO(data)).getpixel((0, 0)) == (0, 0, 0):
            return real(data, *args, **kwargs)
        raise CoverTypographyError("temporary font issue")
    monkeypatch.setattr(module, "compose_poster_cover", composition_failure)
    failed = poster.generate("project-one", project, created.cover_set_id)
    assert len(images.calls) == 4 and failed.status == "failed"
    assert all(c.quality_report["assets"]["artwork_sha256"] for c in failed.candidates)
    # A second failed compose still has the same checkpoint chain, never a new image.
    second = poster.retry("project-one", project, failed.cover_set_id, [failed.candidates[0].candidate_id])
    assert second.candidates[0].status == "failed" and len(images.calls) == 4
    monkeypatch.setattr(module, "compose_poster_cover", real)
    third = poster.retry("project-one", project, second.cover_set_id, [second.candidates[0].candidate_id])
    assert third.candidates[0].status == "ready" and len(images.calls) == 4


def test_inflight_reservation_cannot_be_replayed_implicitly(tmp_path):
    project, _, images, poster, created = setup(tmp_path)
    def failed_checkpoint(event):
        if event["phase"] == "generating":
            raise RuntimeError("task checkpoint unavailable")
    with pytest.raises(RuntimeError):
        poster.generate("project-one", project, created.cover_set_id, progress=failed_checkpoint)
    assert not images.calls
    with pytest.raises(CoverConflict, match="结果尚不确定"):
        poster.generate("project-one", project, created.cover_set_id)
    assert not images.calls


def test_unfittable_title_stops_before_any_paid_image_request(tmp_path):
    project, _, images, poster, created = setup(tmp_path, layout={"region": [.05, .05, .01, .01]})
    with pytest.raises(CoverTypographyError, match="无法放入"):
        poster.generate("project-one", project, created.cover_set_id)
    assert not images.calls


def test_lost_progress_after_paid_image_stops_batch_and_recovers_saved_art(tmp_path):
    project, _, images, poster, created = setup(tmp_path)
    def failed_checkpoint(event):
        if event["phase"] == "composing":
            raise ValueError("task metadata write failed")
    with pytest.raises(RuntimeError, match="further image requests stopped"):
        poster.generate("project-one", project, created.cover_set_id, progress=failed_checkpoint)
    assert len(images.calls) == 1
    interrupted = CoverStore(project).load(created.cover_set_id)
    assert interrupted.candidates[0].quality_report["assets"]["artwork_sha256"]
    recovered = poster.retry("project-one", project, interrupted.cover_set_id)
    assert recovered.status == "ready" and len(images.calls) == 4


def test_refinement_uses_original_art_bytes_without_a_planner_or_selection_change(tmp_path):
    project, service, images, poster, created = setup(tmp_path, [art((20 + i, 30, 50)) for i in range(5)])
    parent = poster.generate("project-one", project, created.cover_set_id)
    parent = service.select_candidate(project, parent.cover_set_id, parent.candidates[0].candidate_id,
                                      expected_revision=parent.revision, expected_active_revision=0)
    snapshot = CoverStore(project)._set_path(parent.cover_set_id).read_bytes()
    result = poster.refine("project-one", project, parent.cover_set_id, parent.candidates[0].candidate_id,
                           "Light the woman's face warmly; keep her mature age.", keep_composition=True)
    assert len(images.calls) == 5
    target = result.candidates[0]
    assert target.status == "ready" and target.safe_request_parameters["operation"] == "edit"
    assert target.safe_request_parameters["reference_image_sha256"] == parent.candidates[0].quality_report["assets"]["artwork_sha256"]
    assert result.candidates[1:] == parent.candidates[1:]
    assert CoverStore(project).active().cover_set_id == parent.cover_set_id
    assert CoverStore(project)._set_path(parent.cover_set_id).read_bytes() == snapshot
    validate_lineage(CoverStore(project), result)
    service.select_candidate(project, result.cover_set_id, target.candidate_id,
                             expected_revision=result.revision, expected_active_revision=1)


def test_typography_preview_is_read_only_and_save_creates_exact_art_new_version(tmp_path):
    project, service, images, poster, created = setup(tmp_path)
    parent = poster.generate("project-one", project, created.cover_set_id)
    first = parent.candidates[0]
    parent = service.select_candidate(project, parent.cover_set_id, first.candidate_id,
                                      expected_revision=parent.revision, expected_active_revision=0)
    before = {str(p): p.read_bytes() for p in (project / "outputs/covers").rglob("*.json")}
    layout = {"position": "top", "font_id": "serif-bold", "color": "#FFFFCC", "backdrop": {"opacity": .4, "color": "#101020"}}
    preview = poster.preview_typography("project-one", project, parent.cover_set_id, first.candidate_id, layout)
    assert before == {str(p): p.read_bytes() for p in (project / "outputs/covers").rglob("*.json")}
    images.settings.model = "some-new-configured-route"
    result = poster.typography("project-one", project, parent.cover_set_id, first.candidate_id, layout)
    target = result.candidates[0]
    assert len(images.calls) == 4
    assert target.model == first.model
    assert target.sha256 == sha256(preview).hexdigest()
    assert target.quality_report["assets"]["artwork_sha256"] == first.quality_report["assets"]["artwork_sha256"]
    assert CoverStore(project).active().cover_set_id == parent.cover_set_id
    validate_lineage(CoverStore(project), result)
    service.select_candidate(project, result.cover_set_id, target.candidate_id,
                             expected_revision=result.revision, expected_active_revision=1)


@pytest.mark.parametrize("layout", [
    {"lines": ["Deed Her Name"]}, {"font_id": "../../file"}, {"font_sha256": "0" * 64},
    {"line_spacing": float("nan")}, {"font_size_max": .01}, {"region": [.8, .8, .5, .5]},
    {"backdrop": {"color": "bad", "opacity": .4}}, {"lines": ["Her Name on the Deed"], "line_styles": [{"scale": 20}]},
])
def test_invalid_title_edits_fail_before_any_external_operation(layout):
    with pytest.raises(CoverTypographyError):
        prepare_poster_layout(layout, expected_title="Her Name on the Deed")


def test_per_line_type_hierarchy_is_deterministic_and_preserves_art_outside_title():
    title = "Her Name on the Deed"
    layout = prepare_poster_layout({"lines": ["Her Name", "on the Deed"], "position": "top", "font_id": "sans-bold",
        "line_styles": [{"font_id": "serif-bold", "scale": 1.3, "color": "#FFDD88"}, {"scale": .8}],
        "line_spacing": .4}, expected_title=title)
    original = art()
    first = compose_poster_cover(original, layout, expected_title=title)
    assert first == compose_poster_cover(original, layout, expected_title=title)
    assert len(layout["fonts"]) == 2
    bounds = ImageChops.difference(Image.open(BytesIO(original)), Image.open(BytesIO(first.data))).getbbox()
    assert bounds[1] >= 30 and bounds[3] <= 198
    assert sha256(original).hexdigest() == first.artwork_sha256


def test_cjk_line_wrap_and_typographic_apostrophe_keep_exact_title():
    cjk = prepare_poster_layout({"lines": ["被争夺的", "时间"]}, expected_title="被争夺的时间")
    assert cjk["font_id"] == "cjk-sans"
    assert compose_poster_cover(art(), cjk, expected_title="被争夺的时间").data
    english = prepare_poster_layout({}, expected_title="My Husband’s Secret")
    assert english["font_id"] == "sans-bold"


def test_legacy_v2_native_cover_uses_full_reference_and_explains_typography_limit(tmp_path):
    from tests.test_cover_director import _brief as v2_brief
    project, _, images, poster, created = setup(tmp_path, [art((20 + i, 30, 50)) for i in range(5)])
    parent = poster.generate("project-one", project, created.cover_set_id)
    store = CoverStore(project)
    legacy = replace(parent, cover_set_id="cover-" + "f" * 32, revision=0,
        brief=replace(v2_brief(), title=parent.brief.title), brief_schema_version=2,
        concepts=tuple(replace(c, scene_plan={"_compiled_recipe": {"title_render_mode": "native"}}) for c in parent.concepts),
        candidates=tuple(replace(c, quality_report={}) for c in parent.candidates))
    legacy = store.create(legacy)
    result = poster.refine("project-one", project, legacy.cover_set_id, legacy.candidates[0].candidate_id,
                          "Warmer lighting; retain ages.")
    assert result.candidates[0].status == "ready"
    assert len(images.calls) == 5
    assert result.candidates[0].safe_request_parameters["reference_image_sha256"] == legacy.candidates[0].sha256
    assert "principal_characters" in images.calls[-1]
    with pytest.raises(CoverConflict, match="文字已嵌入"):
        poster.preview_typography("project-one", project, legacy.cover_set_id, legacy.candidates[0].candidate_id, {})


def test_retained_legacy_typography_receipt_resolves_frozen_owner_across_versions(legacy_typography_case):
    service, _, project, store, _ = legacy_typography_case
    record = propose(legacy_typography_case)
    approve(legacy_typography_case, record)
    parent = apply(legacy_typography_case, record)
    poster = PosterImageService(service)
    child = poster.typography("project-one", project, parent.cover_set_id, parent.candidates[1].candidate_id,
                              {"position": "bottom"})
    grandchild = poster.typography("project-one", project, child.cover_set_id, child.candidates[2].candidate_id,
                                   {"position": "bottom"})
    selected = service.select_candidate(project, grandchild.cover_set_id, grandchild.candidates[0].candidate_id,
                                         expected_revision=grandchild.revision, expected_active_revision=0)
    assert selected.candidates[0].sha256 == record["preview_sha256"]
    assert store.load(parent.cover_set_id).selected_candidate_id == ""
    # Editing the amended target itself makes a new independent recipe, not a
    # forged continuation of the old per-candidate amendment receipt.
    typed = poster.typography("project-one", project, parent.cover_set_id, parent.candidates[0].candidate_id,
                              {"position": "bottom", "color": "#FFDD88"})
    service.select_candidate(project, typed.cover_set_id, typed.candidates[0].candidate_id,
                             expected_revision=typed.revision, expected_active_revision=1)


def test_legacy_composed_v11_refines_saved_art_and_keeps_its_pinned_typography(legacy_typography_case):
    service, _, project, _, parent = legacy_typography_case
    client = Images([art((80, 35, 65))])
    service.image_client = client
    poster = PosterImageService(service)
    original = parent.candidates[0]
    child = poster.refine("project-one", project, parent.cover_set_id, original.candidate_id,
                          "Use warmer cinematic light, preserving the characters.")
    assert len(client.calls) == 1
    result = child.candidates[0]
    assert result.status == "ready"
    assert result.safe_request_parameters["reference_image_sha256"] == original.quality_report["assets"]["artwork_sha256"]
    assert result.quality_report["assets"]["font_sha256"] == original.quality_report["assets"]["font_sha256"]
    service.select_candidate(project, child.cover_set_id, result.candidate_id,
                             expected_revision=child.revision, expected_active_revision=0)


def test_retry_legacy_saved_raw_art_does_not_require_new_image_client(tmp_path):
    project, service, images, poster, created = setup(tmp_path)
    rendered = poster.generate("project-one", project, created.cover_set_id)
    legacy = replace(rendered, cover_set_id="cover-" + "e" * 32, revision=0,
        candidates=tuple(replace(c, status="failed", sha256="", relative_path="", media_id="", error="Old composition failed")
                         for c in rendered.candidates))
    legacy = CoverStore(project).create(legacy)
    service.image_client = None
    result = poster.retry("project-one", project, legacy.cover_set_id)
    assert result.status == "ready"
    assert len(images.calls) == 4
    assert all(c.model == "gpt-image-2" for c in result.candidates)
