"""Source-bound image editing never regenerates or replaces an existing cover."""
from dataclasses import replace
import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from api import cover_repair_tasks as tasks
from api.cover_service import CoverServiceError
from core.cover_refinement import reference_for_candidate
from core.cover_director import CoverArtDirector
from core.cover_store import CoverConflict
from core.cover_workflow import digest
from core.image_client import GeneratedImage, ImageClientError
from tests.test_cover_analysis_snapshot import source  # noqa: F401
from tests.test_cover_workflow import workflow, FullStoryFixture, approve as approve_direction  # noqa: F401
from tests.test_cover_repair_tasks import repair, finish  # noqa: F401
from tests.test_cover_service import _jpeg, _service, _actual_jpeg, _independent_evaluator
from tests.test_cover_workbench import DeferredRunner


PROMPT = "保留人物与主要构图，让光线更柔和，减少背景杂物。"


@pytest.fixture
def refinement(repair):
    flow, parent, director, project = repair
    candidates = []
    for index, candidate in enumerate(parent.candidates):
        data = _jpeg(marker=f"original-{index}".encode())
        path = project / "outputs" / "covers" / "originals" / f"{candidate.candidate_id}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        candidates.append(replace(candidate, status="ready", relative_path=str(path.relative_to(project)),
            media_id=f"original-{index}", sha256=hashlib.sha256(data).hexdigest(),
            width=2048, height=3072, content_type="image/jpeg", provider="fixture", model="fixture-image",
            quality_report={"status": "recommended_for_human_review", "blockers": [], "repair_codes": [],
                            "evidence": ["Original image is eligible for human selection."]}))
    parent = flow.store.save(replace(parent, status="ready", candidates=tuple(candidates)), expected_revision=parent.revision)
    return flow, parent, director, project


def _start(refinement, runner, prompt=PROMPT, **owner):
    flow, parent, director, _ = refinement
    return tasks.start(flow, runner, director, cover_set_id=parent.cover_set_id,
        candidate_id=parent.candidates[0].candidate_id, expected_revision=parent.revision,
        refinement_prompt=prompt, **(owner or {"project_id": "book"}))


def _proposal(refinement, runner):
    task = _start(refinement, runner)
    finish(runner)
    return tasks.get(refinement[0], task["task_id"], runner)


class EditImages:
    def __init__(self, *, outcome=None, operation="edit", reference_sha=None):
        self.calls = []
        self.generate_calls = []
        self.outcome = outcome or _jpeg(marker=b"edited-result")
        self.operation = operation
        self.reference_sha = reference_sha

    def generate(self, prompt):
        self.generate_calls.append(prompt)
        raise AssertionError("Refinement must never fall back to text-to-image generation")

    def edit(self, prompt, reference_path, *, expected_reference_sha256):
        data = Path(reference_path).read_bytes()
        self.calls.append({"prompt": prompt, "path": Path(reference_path), "data": data,
                           "sha256": expected_reference_sha256})
        assert hashlib.sha256(data).hexdigest() == expected_reference_sha256
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return GeneratedImage(data=self.outcome, content_type="image/jpeg", width=2048, height=3072,
            request_id="edited-once", model="gpt-image-2", operation=self.operation,
            reference_image_sha256=self.reference_sha or expected_reference_sha256,
            reference_transport="fixture-image-input")


def _editing_service(tmp_path, **options):
    service, _, _ = _service(tmp_path, [])
    images = EditImages(**options)
    service.image_client = images
    return service, images


def _approve(flow, runner, service, task):
    return tasks.approve(flow, runner, service, task["task_id"],
        expected_proposal_sha256=task["proposal_sha256"], confirm_image_calls=1, project_id="book")


def test_refinement_plans_without_a_failed_review_and_binds_request_reference_and_proposal(refinement):
    flow, parent, _, project = refinement
    runner = DeferredRunner()
    task = _start(refinement, runner, f"  {PROMPT}  ")
    assert task["kind"] == "refinement" and task["status"] == "planning" and task["image_calls"] == 1
    assert task["refinement_prompt"] == PROMPT
    assert "repair_decision" not in parent.candidates[0].quality_report
    reference = task["reference_image"]
    assert reference["kind"] == "cover" and reference["sha256"] == parent.candidates[0].sha256
    assert (project / reference["relative_path"]).read_bytes() == _jpeg(marker=b"original-0")
    assert _start(refinement, runner)["task_id"] == task["task_id"]
    finish(runner)
    proposed = tasks.get(flow, task["task_id"], runner)
    assert proposed["status"] == "awaiting_approval"
    assert proposed["proposal"]["refinement_prompt"] == PROMPT
    assert proposed["proposal"]["reference_image"] == reference
    assert proposed["proposal_sha256"] == digest(proposed["proposal"])
    binding = flow.store.load_direction(proposed["direction_id"]).source_binding["single_candidate_repair"]
    assert binding["refinement_prompt_sha256"] == digest(PROMPT)
    assert binding["reference_image_sha256"] == reference["sha256"]
    assert flow.store.load(parent.cover_set_id) == parent


@pytest.mark.parametrize("status", ["ready", "selected", "rejected"])
def test_refinement_edits_exact_original_once_and_preserves_batch_siblings_and_selection(refinement, tmp_path, status):
    flow, parent, director, project = refinement
    if status != "ready":
        parent = flow.store.save(replace(parent, candidates=(replace(parent.candidates[0], status=status), *parent.candidates[1:]),
            status="selected" if status == "selected" else "ready",
            selected_candidate_id=parent.candidates[0].candidate_id if status == "selected" else ""), expected_revision=parent.revision)
        refinement = (flow, parent, director, project)
    if status == "selected":
        flow.store.set_active(parent.cover_set_id, expected_revision=flow.store.active().revision)
    active = flow.store.active()
    parent_bytes = flow.store._set_path(parent.cover_set_id).read_bytes()
    files = {candidate.relative_path: (project / candidate.relative_path).read_bytes() for candidate in parent.candidates}
    runner = DeferredRunner()
    task = _proposal(refinement, runner)
    service, images = _editing_service(tmp_path)
    approved = _approve(flow, runner, service, task)
    assert approved["status"] == "generating" and not images.calls
    assert _approve(flow, runner, service, task)["task_id"] == task["task_id"]
    assert len(runner.jobs) == 2
    finish(runner)
    done = tasks.get(flow, task["task_id"], runner)
    assert done["status"] == "completed", done
    assert len(images.calls) == 1 and not images.generate_calls
    assert images.calls[0]["data"] == files[parent.candidates[0].relative_path]
    assert images.calls[0]["sha256"] == parent.candidates[0].sha256
    assert images.calls[0]["prompt"].startswith("Edit the supplied reference image")
    child = flow.store.load(done["cover_set_id"])
    assert child.cover_set_id != parent.cover_set_id and not child.selected_candidate_id
    assert child.candidates[1:] == parent.candidates[1:]
    assert child.concepts[1:] == parent.concepts[1:]
    target = child.candidates[0]
    assert target.candidate_id != parent.candidates[0].candidate_id and target.sha256 != parent.candidates[0].sha256
    assert target.quality_report["image_refinement"]["prompt"] == PROMPT
    assert target.quality_report["image_refinement"]["reference_image"]["sha256"] == parent.candidates[0].sha256
    assert target.quality_report["image_refinement"]["reference_transport"] == "fixture-image-input"
    assert target.safe_request_parameters["operation"] == "edit"
    assert target.safe_request_parameters["reference_image_sha256"] == parent.candidates[0].sha256
    assert target.safe_request_parameters["edit_prompt_sha256"] == hashlib.sha256(images.calls[0]["prompt"].encode()).hexdigest()
    assert flow.store.active() == active and flow.store._set_path(parent.cover_set_id).read_bytes() == parent_bytes
    assert {path: (project / path).read_bytes() for path in files} == files
    _approve(flow, runner, service, task)
    assert len(runner.jobs) == 2 and len(images.calls) == 1


@pytest.mark.parametrize("count", [0, 2, True])
def test_refinement_requires_exactly_one_explicit_image_call(refinement, count):
    flow, _, _, _ = refinement
    runner = DeferredRunner()
    task = _proposal(refinement, runner)
    with pytest.raises(CoverConflict, match="准确"):
        tasks.approve(flow, runner, None, task["task_id"], expected_proposal_sha256=task["proposal_sha256"],
                      confirm_image_calls=count, project_id="book")
    assert len(runner.jobs) == 1


@pytest.mark.parametrize("field", ["proposal", "prompt", "reference"])
def test_tampered_refinement_cannot_be_approved(refinement, field):
    flow, _, _, _ = refinement
    runner = DeferredRunner()
    task = _proposal(refinement, runner)
    stored = tasks._read(flow, task["task_id"])
    if field == "proposal":
        stored["proposal"]["plan"]["frozen_action"] += " Unapproved action."
    elif field == "prompt":
        stored["refinement_prompt"] = "A different edit request"
    else:
        stored["reference_image"] = {**stored["reference_image"], "sha256": "f" * 64}
    tasks._write(flow, stored)
    with pytest.raises(CoverConflict, match="完整性|不一致"):
        _approve(flow, runner, None, task)
    assert len(runner.jobs) == 1


@pytest.mark.parametrize("when", ["before_approval", "after_approval"])
def test_changed_reference_file_blocks_spending_at_approval_and_worker_boundary(refinement, tmp_path, when):
    flow, parent, _, project = refinement
    runner = DeferredRunner()
    task = _proposal(refinement, runner)
    service, images = _editing_service(tmp_path)
    if when == "after_approval":
        _approve(flow, runner, service, task)
    (project / task["reference_image"]["relative_path"]).write_bytes(_jpeg(marker=b"changed-original"))
    with pytest.raises(CoverConflict, match="原图已变化|不一致"):
        _approve(flow, runner, service, task) if when == "before_approval" else finish(runner)
    assert not images.calls and not images.generate_calls
    assert flow.store.load(parent.cover_set_id) == parent


def test_changed_story_after_approval_blocks_editing(refinement, tmp_path):
    flow, _, _, project = refinement
    runner = DeferredRunner()
    task = _proposal(refinement, runner)
    service, images = _editing_service(tmp_path)
    _approve(flow, runner, service, task)
    (project / "outputs/input/prompt.md").write_text("Changed facts and characters")
    with pytest.raises(CoverConflict, match="骨架已变化"):
        finish(runner)
    assert not images.calls and not images.generate_calls


def test_unsupported_editor_does_not_fall_back_to_generation(refinement, tmp_path):
    flow, parent, _, _ = refinement
    runner = DeferredRunner()
    task = _proposal(refinement, runner)
    service, images, _ = _service(tmp_path, [_jpeg(marker=b"must-not-generate")])
    _approve(flow, runner, service, task)
    with pytest.raises(CoverServiceError, match="支持原图编辑"):
        finish(runner)
    assert not images.prompts and flow.store.load(parent.cover_set_id) == parent
    assert tasks.get(flow, task["task_id"], runner)["status"] == "failed"


@pytest.mark.parametrize("failure", ["provider", "wrong_operation", "wrong_reference", "unchanged_image"])
def test_invalid_or_failed_edit_never_retries_or_generates(refinement, tmp_path, failure):
    flow, parent, _, project = refinement
    options = {"provider": {"outcome": ImageClientError("edit unsupported", retryable=True)},
        "wrong_operation": {"operation": "generate"}, "wrong_reference": {"reference_sha": "e" * 64},
        "unchanged_image": {"outcome": (project / parent.candidates[0].relative_path).read_bytes()}}[failure]
    service, images = _editing_service(tmp_path, **options)
    runner = DeferredRunner()
    task = _proposal(refinement, runner)
    _approve(flow, runner, service, task)
    finish(runner)
    done = tasks.get(flow, task["task_id"], runner)
    assert done["status"] == "failed", done
    assert len(images.calls) == 1 and not images.generate_calls
    assert flow.store.load(parent.cover_set_id) == parent
    _approve(flow, runner, service, task)
    assert len(runner.jobs) == 2 and len(images.calls) == 1


def test_refresh_restores_prompt_and_reference_without_model_calls_or_record_changes(refinement):
    flow, _, director, _ = refinement
    runner = DeferredRunner()
    task = _proposal(refinement, runner)
    def forbidden(*args, **kwargs):
        pytest.fail("Reading a saved refinement must not replan")
    director.plan = forbidden
    path = tasks._path(flow, task["task_id"])
    saved = path.read_bytes()
    restarted = DeferredRunner()
    for restored in (tasks.get(flow, task["task_id"], restarted), tasks.latest(flow, restarted)):
        assert restored["status"] == "awaiting_approval" and restored["refinement_prompt"] == PROMPT
        assert restored["reference_image"] == task["reference_image"]
    assert not restarted.jobs and len(runner.jobs) == 1 and path.read_bytes() == saved


def test_composed_reference_uses_retained_artwork_not_the_title_composite(refinement):
    _, parent, _, project = refinement
    buffer = BytesIO()
    Image.new("RGB", (120, 180), "navy").save(buffer, format="PNG")
    raw = buffer.getvalue()
    path = project / "outputs/covers/originals/artwork.png"
    path.write_bytes(raw)
    candidate = replace(parent.candidates[0], quality_report={"assets": {"artwork_sha256": hashlib.sha256(raw).hexdigest(),
        "artwork_relative_path": str(path.relative_to(project)), "artwork_content_type": "image/png"}})
    concept = replace(parent.concepts[0], scene_plan={"_compiled_recipe": {"title_render_mode": "composed"}})
    reference = reference_for_candidate(project, candidate, concept)
    assert reference["kind"] == "artwork" and reference["sha256"] != candidate.sha256
    assert reference["relative_path"] == str(path.relative_to(project))
    assert [reference["width"], reference["height"]] == [120, 180]
    with pytest.raises(CoverConflict, match="缺少可验证的原画"):
        reference_for_candidate(project, replace(candidate, quality_report={}), concept)


def test_composed_refinement_edits_raw_artwork_then_composes_and_reviews_fresh_result(repair, tmp_path):
    flow, previous, repair_director, project = repair
    previous_direction = flow.store.load_direction(previous.concepts[0].scene_plan["_source_binding"]["direction_id"])

    class ComposedDirector:
        def plan(self, brief, *, count, evidence_ledger, story_analysis):
            value = FullStoryFixture().plan(brief, count=count, evidence_ledger=evidence_ledger,
                                           story_analysis=story_analysis).to_dict()
            for plan in value["plans"]:
                plan["campaign"]["title_render_mode"] = "composed"
                plan["campaign"]["title_layout"].update(title=brief.title, lines=[brief.title])
            return CoverArtDirector.from_fixture(value).plan(brief, count=count,
                evidence_ledger=evidence_ledger, story_analysis=story_analysis)

    direction = approve_direction(flow, flow.plan(previous_direction.source_binding["analysis_id"], ComposedDirector()))
    brief, concepts, options = flow.generation_arguments(direction.direction_id, direction.direction_sha256, reserve=False)
    raw_original = _actual_jpeg()
    service, _, _ = _service(tmp_path, [raw_original] * 4)
    service.visual_evaluator = _independent_evaluator()
    parent = service.generate("book", project, brief, concepts, **options)
    assert all(candidate.status == "ready" for candidate in parent.candidates)
    original = parent.candidates[0]
    original_final = (project / original.relative_path).read_bytes()
    original_art_sha = hashlib.sha256(raw_original).hexdigest()
    assert original.sha256 != original_art_sha
    parent_bytes = flow.store._set_path(parent.cover_set_id).read_bytes()
    reviewed = []
    review = service.visual_evaluator.review
    def fresh_review(**kwargs):
        reviewed.append(kwargs)
        return review(**kwargs)
    service.visual_evaluator.review = fresh_review
    edited = BytesIO()
    Image.new("RGB", (400, 600), (85, 95, 110)).save(edited, format="JPEG")
    edited_raw = edited.getvalue()
    images = EditImages(outcome=edited_raw)
    service.image_client = images
    runner = DeferredRunner()
    task = _proposal((flow, parent, repair_director, project), runner)
    assert task["reference_image"]["kind"] == "artwork"
    assert task["reference_image"]["sha256"] == original_art_sha
    _approve(flow, runner, service, task)
    finish(runner)
    done = tasks.get(flow, task["task_id"], runner)
    assert done["status"] == "completed", done
    child = flow.store.load(done["cover_set_id"])
    target = child.candidates[0]
    assert len(images.calls) == 1 and images.calls[0]["data"] == raw_original
    assert images.calls[0]["data"] != original_final and not images.generate_calls
    assert len(reviewed) == 1
    assert reviewed[0]["artwork"] == edited_raw
    assert reviewed[0]["image"] == (project / target.relative_path).read_bytes()
    assert reviewed[0]["image"] != edited_raw
    assert target.quality_report["evaluation"]["evaluation_status"] == "complete"
    assets = target.quality_report["assets"]
    assert assets["artwork_sha256"] == hashlib.sha256(edited_raw).hexdigest()
    assert assets["final_cover_sha256"] == target.sha256 != assets["artwork_sha256"]
    assert assets["artwork_generation"]["operation"] == "edit"
    assert assets["artwork_generation"]["reference_image_sha256"] == original_art_sha
    assert target.quality_report["image_refinement"]["reference_image"] == task["reference_image"]
    binding = target.quality_report["source_binding"]["single_candidate_repair"]
    assert binding["reference_image_sha256"] == original_art_sha and binding["refinement_prompt_sha256"] == digest(PROMPT)
    assert child.candidates[1:] == parent.candidates[1:] and child.concepts[1:] == parent.concepts[1:]
    assert flow.store._set_path(parent.cover_set_id).read_bytes() == parent_bytes
    assert (project / original.relative_path).read_bytes() == original_final
    assert (project / original.quality_report["assets"]["artwork_relative_path"]).read_bytes() == raw_original


@pytest.mark.parametrize("owner", ["project", "workshop"])
def test_refinement_http_routes_bind_prompt_and_allow_get_only_recovery(refinement, tmp_path, monkeypatch, owner):
    from api import cover_workbench
    from tests.test_cover_api import _client
    flow, parent, director, _ = refinement
    if owner == "workshop":
        parent = flow.store.save(replace(parent, project_id="workshop-discussion"), expected_revision=parent.revision)
    client, images = _client(tmp_path, monkeypatch, director=director)
    monkeypatch.setattr(cover_workbench, f"{owner}_workflow", lambda *args: flow)
    runner = DeferredRunner()
    monkeypatch.setattr(cover_workbench, "runner", runner)
    base = "/api/projects/book/covers" if owner == "project" else "/api/workshop/sessions/discussion/covers"
    url = base + f"/{parent.cover_set_id}/candidates/{parent.candidates[0].candidate_id}/repair-tasks"
    response = client.post(url, json={"expected_revision": parent.revision, "refinement_prompt": f"  {PROMPT}  "})
    assert response.status_code == 202, response.text
    task = response.json()
    assert task["kind"] == "refinement" and task["refinement_prompt"] == PROMPT and task["image_calls"] == 1
    assert task["reference_image"]["sha256"] == parent.candidates[0].sha256
    assert client.get(base + "/repair-tasks/latest").json()["task_id"] == task["task_id"]
    assert client.get(base + "/repair-tasks/" + task["task_id"]).json()["refinement_prompt"] == PROMPT
    assert len(runner.jobs) == 1 and images.calls == 0


@pytest.mark.parametrize("owner", ["project", "workshop"])
@pytest.mark.parametrize("prompt", ["", "   \n", "x" * 2001, 42])
def test_refinement_http_rejects_blank_invalid_or_excessive_prompts_before_dispatch(refinement, tmp_path, monkeypatch, owner, prompt):
    from api import cover_workbench
    from tests.test_cover_api import _client
    flow, parent, director, _ = refinement
    client, images = _client(tmp_path, monkeypatch, director=director)
    monkeypatch.setattr(cover_workbench, f"{owner}_workflow", lambda *args: flow)
    runner = DeferredRunner()
    monkeypatch.setattr(cover_workbench, "runner", runner)
    base = "/api/projects/book/covers" if owner == "project" else "/api/workshop/sessions/discussion/covers"
    response = client.post(base + f"/{parent.cover_set_id}/candidates/{parent.candidates[0].candidate_id}/repair-tasks",
                           json={"expected_revision": parent.revision, "refinement_prompt": prompt})
    assert response.status_code in {400, 422}, response.text
    assert not runner.jobs and images.calls == 0
