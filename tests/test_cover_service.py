from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from api.cover_service import CoverService, CoverServiceError
from api.media import LocalMediaStore, digest
from core.cover_models import CoverBrief, CoverCandidate, CoverConcept, CoverSet
from core.cover_store import CoverConflict, CoverStore
from core.image_client import GeneratedImage, ImageClientError
from core.cover_director import CoverArtDirector
from core.cover_prompt_compiler import scene_to_cover_concept
from core.cover_quality import CoverVisualEvaluator
from tests.test_cover_director import _brief as _brief_v2, director_fixture


SHA = "a" * 64


def _jpeg(width: int = 2048, height: int = 3072, marker: bytes = b"") -> bytes:
    return (
        b"\xff\xd8\xff\xc0\x00\x11\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        + marker
        + b"\xff\xd9"
    )


def _webp(width: int = 2048, height: int = 3072, marker: bytes = b"") -> bytes:
    payload = (
        b"VP8X" + (10 + len(marker)).to_bytes(4, "little") + b"\x00\x00\x00\x00"
        + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
        + marker
    )
    return b"RIFF" + (len(payload) + 4).to_bytes(4, "little") + b"WEBP" + payload


def _brief() -> CoverBrief:
    return CoverBrief.from_dict({
        "schema_version": 1,
        "title": "Her Name on the Deed",
        "language": "English",
        "genre": "domestic revenge",
        "target_audience": "women 30-50 seeking boundary-setting stories",
        "market_scope": "English serialized fiction",
        "core_task": "A caregiver claims her independent home.",
        "core_conflict": "Two families demand her labor and income.",
        "emotional_promise": "anger and earned independence",
        "protagonist": {
            "role": "caregiver",
            "visual_identity": "composed woman in practical work clothes",
            "agency_signal": "holding her own house key",
        },
        "relationship_or_power_contrast": "one woman facing two demanding households",
        "decisive_story_node": "her name appears alone on the deed",
        "secondary_task": {"story_function": "protect child", "visual_signal": "small backpack"},
        "world_signals": ["fictional commuter district"],
        "title_direction": {"hierarchy": "large", "preferred_zone": "top", "readability": "mobile_thumbnail"},
        "forbidden_elements": ["real cities", "logos"],
    }, source_prompt_sha256=SHA)


def _concepts() -> list[CoverConcept]:
    return [CoverConcept(
        concept_id=f"concept-{index}",
        visual_strategy=f"strategy-{index}",
        focal_scene=f"scene-{index}",
        composition="portrait 2:3",
        palette="crimson and charcoal",
        secondary_signal="house key",
        title_treatment="large title at top",
        generation_prompt=f'Render exact title "Her Name on the Deed" once. Strategy {index}.',
    ) for index in range(1, 5)]


class FakeImageClient:
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> GeneratedImage:
        self.prompts.append(prompt)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return GeneratedImage(
            data=outcome,
            content_type="image/jpeg",
            width=2048,
            height=3072,
            request_id=f"request-{len(self.prompts)}",
            model="gpt-image-2",
        )


class WrongModelImageClient(FakeImageClient):
    def generate(self, prompt: str) -> GeneratedImage:
        return replace(super().generate(prompt), model="dall-e-3")


@dataclass
class MediaRow:
    id: str


class Registrar:
    def __init__(self) -> None:
        self.by_sha: dict[str, MediaRow] = {}
        self.calls: list[dict] = []

    def __call__(self, **fields) -> MediaRow:
        self.calls.append(fields)
        return self.by_sha.setdefault(fields["sha"], MediaRow(f"media-{len(self.by_sha) + 1}"))


def _service(tmp_path: Path, outcomes) -> tuple[CoverService, FakeImageClient, Registrar]:
    client = FakeImageClient(outcomes)
    registrar = Registrar()
    service = CoverService(
        image_client=client,
        media_store=LocalMediaStore(tmp_path / "media"),
        media_add=registrar,
    )
    return service, client, registrar


def test_generate_persists_four_independent_ready_candidates(tmp_path) -> None:
    images = [_jpeg(marker=bytes([index])) for index in range(1, 5)]
    service, client, registrar = _service(tmp_path, images)

    cover_set = service.generate("project-one", tmp_path / "project", _brief(), _concepts())

    assert cover_set.status == "ready"
    assert [candidate.status for candidate in cover_set.candidates] == ["ready"] * 4
    assert client.prompts == [concept.generation_prompt for concept in _concepts()]
    assert len(registrar.calls) == 4
    for index, candidate in enumerate(cover_set.candidates, start=1):
        assert candidate.media_id == f"media-{index}"
        assert candidate.relative_path.endswith(f"cover-{index:02d}.jpg")
        assert (tmp_path / "project" / candidate.relative_path).is_file()


def test_service_does_not_persist_an_image_from_a_different_model(tmp_path) -> None:
    registrar = Registrar()
    service = CoverService(
        image_client=WrongModelImageClient([_jpeg(marker=b"wrong-model")] * 4),
        media_store=LocalMediaStore(tmp_path / "media"),
        media_add=registrar,
    )

    cover_set = service.generate("project-one", tmp_path / "project", _brief(), _concepts())

    assert cover_set.candidates[0].status == "failed"
    assert "gpt-image-2" in cover_set.candidates[0].error
    assert not registrar.calls


def test_generate_supports_unicode_project_id(tmp_path) -> None:
    service, _, _ = _service(
        tmp_path, [_jpeg(marker=bytes([index])) for index in range(1, 5)]
    )

    cover_set = service.generate("中文小说", tmp_path / "中文小说", _brief(), _concepts())

    assert cover_set.status == "ready"
    assert len(list((tmp_path / "media" / "中文小说").glob("**/*.jpg"))) == 4


def test_generate_preserves_partial_success_and_candidate_errors(tmp_path) -> None:
    failed = ImageClientError("provider unavailable", retryable=True, status_code=503)
    service, _, _ = _service(
        tmp_path, [_jpeg(marker=b"1"), failed, _jpeg(marker=b"3"), failed]
    )

    cover_set = service.generate("project-one", tmp_path / "project", _brief(), _concepts())

    assert cover_set.status == "partial"
    assert [item.status for item in cover_set.candidates] == ["ready", "failed", "ready", "failed"]
    assert cover_set.candidates[1].error == "provider unavailable"
    assert len(list((tmp_path / "project" / "outputs/deliverables/covers/pending").glob("*"))) == 2


def test_new_generation_removes_pending_images_from_previous_cover_set(tmp_path) -> None:
    project = tmp_path / "project"
    first, _, _ = _service(
        tmp_path, [_jpeg(marker=bytes([index])) for index in range(1, 5)]
    )
    first.generate("project-one", project, _brief(), _concepts())

    failed = ImageClientError("provider unavailable", retryable=True)
    second, _, _ = _service(
        tmp_path, [_jpeg(marker=b"new"), failed, failed, failed]
    )
    partial = second.generate("project-one", project, _brief(), _concepts())

    pending = project / "outputs/deliverables/covers/pending"
    assert partial.status == "partial"
    assert [path.name for path in pending.iterdir()] == ["cover-01.jpg"]


def test_invalid_image_bytes_create_no_deliverable_projection(tmp_path) -> None:
    service, _, registrar = _service(
        tmp_path, [_jpeg(1024, 1024, bytes([index])) for index in range(1, 5)]
    )

    cover_set = service.generate("project-one", tmp_path / "project", _brief(), _concepts())

    assert cover_set.status == "failed"
    assert {candidate.status for candidate in cover_set.candidates} == {"failed"}
    assert registrar.calls == []
    pending = tmp_path / "project" / "outputs/deliverables/covers/pending"
    assert not pending.exists() or not list(pending.iterdir())


def test_provider_portrait_resolution_is_persisted_without_upscaling(tmp_path) -> None:
    service, _, registrar = _service(
        tmp_path, [_jpeg(1024, 1536, bytes([index])) for index in range(1, 5)]
    )

    cover_set = service.generate("project-one", tmp_path / "project", _brief(), _concepts())

    assert cover_set.status == "ready"
    assert [(item.width, item.height) for item in cover_set.candidates] == [(1024, 1536)] * 4
    assert [call["width"] for call in registrar.calls] == [1024] * 4
    assert [item.safe_request_parameters["size"] for item in cover_set.candidates] == [
        "2048x3072"
    ] * 4


def test_identical_image_bytes_reuse_content_addressed_media(tmp_path) -> None:
    image = _jpeg(marker=b"same")
    service, _, _ = _service(tmp_path, [image, image, image, image])

    cover_set = service.generate("project-one", tmp_path / "project", _brief(), _concepts())

    assert {candidate.media_id for candidate in cover_set.candidates} == {"media-1"}
    assert len(list((tmp_path / "media/project-one").glob("**/*.jpg"))) == 1
    assert len(list((tmp_path / "project/outputs/deliverables/covers/pending").glob("*.jpg"))) == 4


def test_retry_calls_only_failed_candidate_and_reaches_ready(tmp_path) -> None:
    failed = ImageClientError("busy", retryable=True)
    service, client, _ = _service(
        tmp_path,
        [
            _jpeg(marker=b"1"), failed, _jpeg(marker=b"3"), failed,
            _jpeg(marker=b"2"),
        ],
    )
    project = tmp_path / "project"
    partial = service.generate("project-one", project, _brief(), _concepts())
    prompts_before = list(client.prompts)

    retried = service.retry_candidate(
        "project-one", project, partial.cover_set_id, partial.candidates[1].candidate_id,
        expected_revision=partial.revision,
    )

    assert retried.status == "ready"
    assert len(client.prompts) == 5
    assert client.prompts[:4] == prompts_before
    assert client.prompts[-1] != _concepts()[1].generation_prompt
    assert "Do not infer or invent ethnicity" in client.prompts[-1]


def test_quality_repair_appends_attempt_and_preserves_prior_provenance(tmp_path) -> None:
    brief = _brief_v2()
    direction = CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4)
    concepts = [scene_to_cover_concept(brief, scene) for scene in direction.plans]
    images = [_jpeg(marker=bytes([index])) for index in range(1, 6)]
    client = FakeImageClient(images)
    registrar = Registrar()
    evaluator = CoverVisualEvaluator(
        provider="fixture",
        model="visual-v1",
        complete=lambda *_args: {
            "status": "blocked",
            "blockers": ["generic_ai_face"],
            "repair_codes": ["generic_ai_face"],
            "evidence": ["face region"],
            "findings": [],
        },
    )
    projected: list[bytes] = []

    def thumbnail_projector(_image: bytes, *, width: int, height: int) -> bytes:
        assert (width, height) == (120, 180)
        thumbnail = b"projected-thumbnail"
        projected.append(thumbnail)
        return thumbnail

    service = CoverService(
        image_client=client,
        media_store=LocalMediaStore(tmp_path / "media"),
        media_add=registrar,
        visual_evaluator=evaluator,
        thumbnail_projector=thumbnail_projector,
    )
    project = tmp_path / "project"
    generated = service.generate("project-one", project, brief, concepts, compiler_version="cover-compiler.v2")
    candidate = generated.candidates[0]
    first_attempt = candidate.attempt_history[0]

    repaired = service.retry_candidate(
        "project-one",
        project,
        generated.cover_set_id,
        candidate.candidate_id,
        expected_revision=generated.revision,
        repair_codes=["generic_ai_face"],
    )

    updated = repaired.candidates[0]
    assert len(client.prompts) == 5
    assert len(updated.attempt_history) == 2
    assert updated.attempt_history[0] == first_attempt
    assert updated.prompt_revision == 2
    assert updated.sha256 != candidate.sha256
    assert "Repair focus: generic ai face" in updated.generation_prompt
    assert projected == [b"projected-thumbnail"] * 5


def test_visual_evaluator_receives_thumbnail_projection_not_original_bytes(tmp_path) -> None:
    brief = _brief_v2()
    direction = CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4)
    concepts = [scene_to_cover_concept(brief, scene) for scene in direction.plans]
    images = [_jpeg(marker=bytes([index])) for index in range(1, 5)]
    received: list[tuple[bytes, bytes]] = []
    evaluator = CoverVisualEvaluator(
        provider="fixture",
        model="visual-v1",
        complete=lambda image, thumbnail, _brief, _scene: (
            received.append((image, thumbnail))
            or {
                "status": "human_review_required",
                "blockers": [],
                "repair_codes": [],
                "evidence": ["fixture"],
                "findings": [],
            }
        ),
    )
    service = CoverService(
        image_client=FakeImageClient(images),
        media_store=LocalMediaStore(tmp_path / "media"),
        media_add=Registrar(),
        visual_evaluator=evaluator,
        thumbnail_projector=lambda _image, **_size: b"120x180-thumbnail",
    )

    service.generate("project-one", tmp_path / "project", brief, concepts)

    assert len(received) == 4
    assert all(thumbnail == b"120x180-thumbnail" for _, thumbnail in received)
    assert all(image != thumbnail for image, thumbnail in received)


def test_quality_repair_rejects_unreported_code_before_image_call(tmp_path) -> None:
    brief = _brief_v2()
    direction = CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4)
    concepts = [scene_to_cover_concept(brief, scene) for scene in direction.plans]
    service, client, _ = _service(
        tmp_path, [_jpeg(marker=bytes([index])) for index in range(1, 5)]
    )
    generated = service.generate("project-one", tmp_path / "project", brief, concepts)

    with pytest.raises(CoverServiceError, match="reported"):
        service.retry_candidate(
            "project-one",
            tmp_path / "project",
            generated.cover_set_id,
            generated.candidates[0].candidate_id,
            expected_revision=generated.revision,
            repair_codes=["generic_ai_face"],
        )

    assert len(client.prompts) == 4


def test_reject_and_select_are_explicit_and_idempotent(tmp_path) -> None:
    service, _, _ = _service(
        tmp_path, [_jpeg(marker=bytes([i])) for i in range(1, 5)]
    )
    project = tmp_path / "project"
    ready = service.generate("project-one", project, _brief(), _concepts())
    rejected = service.reject_candidate(
        project, ready.cover_set_id, ready.candidates[3].candidate_id,
        expected_revision=ready.revision,
    )
    selected = service.select_candidate(
        project, rejected.cover_set_id, rejected.candidates[0].candidate_id,
        expected_revision=rejected.revision, expected_active_revision=0,
    )
    same = service.select_candidate(
        project, selected.cover_set_id, selected.candidates[0].candidate_id,
        expected_revision=selected.revision, expected_active_revision=1,
    )

    assert rejected.selected_candidate_id == ""
    assert rejected.candidates[3].status == "rejected"
    assert selected.status == "selected"
    assert selected.selected_candidate_id == selected.candidates[0].candidate_id
    assert same.selected_candidate_id == selected.selected_candidate_id
    assert same.revision == selected.revision


def test_selecting_historical_set_restores_its_content_addressed_image(tmp_path) -> None:
    project = tmp_path / "project"
    first_images = [_jpeg(marker=f"first-{index}".encode()) for index in range(1, 5)]
    second_images = [_jpeg(marker=f"second-{index}".encode()) for index in range(1, 5)]
    service, _, _ = _service(tmp_path, [*first_images, *second_images])
    first = service.generate("project-one", project, _brief(), _concepts())
    service.generate("project-one", project, _brief(), _concepts())

    service.select_candidate(
        project, first.cover_set_id, first.candidates[0].candidate_id,
        expected_revision=first.revision, expected_active_revision=0,
    )

    assert (
        project / "outputs/deliverables/covers/selected-cover.jpg"
    ).read_bytes() == first_images[0]


def test_selecting_legacy_webp_candidate_preserves_historical_projection(tmp_path) -> None:
    project = tmp_path / "project"
    service, _, _ = _service(tmp_path, [])
    image = _webp(marker=b"legacy")
    image_sha = digest(image)
    relative_path = "outputs/deliverables/covers/pending/cover-01.webp"
    assert service.media_store is not None
    service.media_store.put("project-one", image_sha, ".webp", image)
    legacy = CoverSet.new("project-one", _brief(), _concepts())
    ready = CoverCandidate(
        candidate_id=legacy.candidates[0].candidate_id,
        concept_id=legacy.candidates[0].concept_id,
        status="ready",
        relative_path=relative_path,
        media_id="media-legacy",
        sha256=image_sha,
        width=2048,
        height=3072,
        content_type="image/webp",
        provider="openai_compatible",
        model="gpt-image-2",
        generation_prompt=legacy.concepts[0].generation_prompt,
    )
    legacy = replace(
        legacy,
        candidates=(ready, *legacy.candidates[1:]),
        status="partial",
    )
    stored = CoverStore(project).create(legacy)

    service.select_candidate(
        project,
        stored.cover_set_id,
        ready.candidate_id,
        expected_revision=stored.revision,
        expected_active_revision=0,
    )

    assert (
        project / "outputs/deliverables/covers/selected-cover.webp"
    ).read_bytes() == image


def test_active_revision_conflict_rolls_back_selected_candidate_state(tmp_path) -> None:
    project = tmp_path / "project"
    service, _, _ = _service(
        tmp_path, [_jpeg(marker=bytes([index])) for index in range(1, 5)]
    )
    ready = service.generate("project-one", project, _brief(), _concepts())

    with pytest.raises(CoverConflict, match="active cover revision changed"):
        service.select_candidate(
            project, ready.cover_set_id, ready.candidates[0].candidate_id,
            expected_revision=ready.revision, expected_active_revision=1,
        )

    persisted = CoverStore(project).load(ready.cover_set_id)
    assert persisted.selected_candidate_id == ""
    assert {candidate.status for candidate in persisted.candidates} == {"ready"}


def test_selected_historical_set_can_be_reactivated(tmp_path) -> None:
    project = tmp_path / "project"
    images = [_jpeg(marker=bytes([index])) for index in range(1, 9)]
    service, _, _ = _service(tmp_path, images)
    first = service.generate("project-one", project, _brief(), _concepts())
    second = service.generate("project-one", project, _brief(), _concepts())
    selected_first = service.select_candidate(
        project, first.cover_set_id, first.candidates[0].candidate_id,
        expected_revision=first.revision, expected_active_revision=0,
    )
    service.select_candidate(
        project, second.cover_set_id, second.candidates[0].candidate_id,
        expected_revision=second.revision, expected_active_revision=1,
    )

    same = service.select_candidate(
        project, selected_first.cover_set_id, selected_first.candidates[0].candidate_id,
        expected_revision=selected_first.revision, expected_active_revision=2,
    )

    assert same.revision == selected_first.revision
    assert CoverStore(project).active().cover_set_id == first.cover_set_id
    assert CoverStore(project).active().revision == 3


def test_stale_set_selection_requires_explicit_confirmation(tmp_path) -> None:
    service, _, _ = _service(
        tmp_path, [_jpeg(marker=bytes([i])) for i in range(1, 5)]
    )
    project = tmp_path / "project"
    ready = service.generate("project-one", project, _brief(), _concepts())
    stale = service.mark_stale(project, ready.cover_set_id, "b" * 64, "")

    with pytest.raises(CoverServiceError, match="stale"):
        service.select_candidate(
            project, stale.cover_set_id, stale.candidates[0].candidate_id,
            expected_revision=stale.revision, expected_active_revision=0,
        )


@pytest.mark.parametrize('regression', ['title', 'lighting', 'unknown', 'failure', 'improved'])
def test_automatic_repair_keeps_original_unless_story_and_art_both_pass(tmp_path, regression):
    from core.cover_models_v2 import CoverQualityReport
    from tests.test_cover_director import adaptive_director_fixture

    brief = _brief_v2()
    direction = CoverArtDirector.from_fixture(adaptive_director_fixture()).plan(brief, count=4)
    concepts = [scene_to_cover_concept(brief, plan) for plan in direction.plans]
    original = _jpeg(marker=b'original')
    replacement = _jpeg(marker=b'replacement')
    outcomes = [original, ImageClientError('timeout') if regression == 'failure' else replacement,
                *[_jpeg(marker=bytes([i])) for i in range(3)]]
    calls = []

    def evaluate(image, *_args):
        scores = {name: 90 for name in CoverQualityReport._DIMENSIONS}
        report = dict(status='human_review_required', **scores)
        calls.append(image)
        if image == original:
            report.update(status='blocked', blockers=['core_conflict_missing'],
                          repair_codes=['core_conflict_missing'], core_conflict_fidelity=30)
        elif image == replacement:
            if regression == 'title':
                # Some evaluators put the visible failure only in findings.
                report['findings'] = [dict(code='title_reading_order_ambiguous', severity='warning',
                    message='The second clause reads before the first', evidence='left/right columns')]
            elif regression == 'lighting':
                report['cinematic_storytelling'] = 40
            elif regression == 'unknown':
                report['thumbnail_clarity'] = None
        return report

    client = FakeImageClient(outcomes)
    media = LocalMediaStore(tmp_path / 'media')
    service = CoverService(image_client=client, media_store=media, media_add=Registrar(),
        visual_evaluator=CoverVisualEvaluator(complete=evaluate),
        thumbnail_projector=lambda *_args, **_kwargs: b'thumbnail')
    project = tmp_path / 'project'
    result = service.generate('project-one', project, brief, concepts)
    candidate = result.candidates[0]
    assert len(client.prompts) == 5  # one bounded correction, never an unbounded loop
    assert len(candidate.attempt_history) == 2
    assert candidate.attempt_history[0]['image_sha256'] == digest(original)
    promote = regression == 'improved'
    assert candidate.sha256 == digest(replacement if promote else original)
    assert (project / candidate.relative_path).read_bytes() == (replacement if promote else original)
    assert candidate.prompt_revision == (2 if promote else 1)
    assert candidate.generation_prompt == result.concepts[0].generation_prompt
    assert CoverStore(project).load(result.cover_set_id).candidates[0] == candidate
    assert not result.selected_candidate_id
    if regression != 'failure':
        assert candidate.attempt_history[1]['image_sha256'] == digest(replacement)
        assert media.read('project-one', digest(replacement), '.jpg') == replacement
    if not promote:
        assert 'repair_not_promoted' in {f['code'] for f in candidate.quality_report['findings']}


def test_manual_retry_after_unpromoted_repair_uses_a_new_prompt_revision(tmp_path):
    from tests.test_cover_director import adaptive_director_fixture
    brief = _brief_v2()
    direction = CoverArtDirector.from_fixture(adaptive_director_fixture()).plan(brief, count=4)
    concepts = [scene_to_cover_concept(brief, scene) for scene in direction.plans]
    client = FakeImageClient([_jpeg(marker=bytes([i])) for i in range(10)])
    evaluator = CoverVisualEvaluator(complete=lambda *_args: dict(
        status='blocked', blockers=['core_conflict_missing'], repair_codes=['core_conflict_missing']))
    service = CoverService(image_client=client, media_store=LocalMediaStore(tmp_path / 'media'),
        media_add=Registrar(), visual_evaluator=evaluator,
        thumbnail_projector=lambda *_args, **_kwargs: b'thumbnail')
    result = service.generate('project-one', tmp_path / 'project', brief, concepts)
    original = result.candidates[0]
    assert original.prompt_revision == 1
    updated = service.retry_candidate('project-one', tmp_path / 'project', result.cover_set_id,
        original.candidate_id, expected_revision=result.revision, repair_codes=['core_conflict_missing'])
    retried = updated.candidates[0]
    assert [a['prompt_revision'] for a in retried.attempt_history] == [1, 2, 3]
    assert retried.prompt_revision == 3
    assert retried.attempt_history[:2] == original.attempt_history
