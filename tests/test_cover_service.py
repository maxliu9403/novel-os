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
    assert len(list((tmp_path / "project" / "outputs/deliverables/covers/pending").rglob("*.jpg"))) == 2


def test_new_generation_keeps_each_batch_pending_images_separate(tmp_path) -> None:
    project = tmp_path / "project"
    first, _, _ = _service(
        tmp_path, [_jpeg(marker=bytes([index])) for index in range(1, 5)]
    )
    original = first.generate("project-one", project, _brief(), _concepts())

    failed = ImageClientError("provider unavailable", retryable=True)
    second, _, _ = _service(
        tmp_path, [_jpeg(marker=b"new"), failed, failed, failed]
    )
    partial = second.generate("project-one", project, _brief(), _concepts())

    pending = project / "outputs/deliverables/covers/pending"
    assert partial.status == "partial"
    assert [path.name for path in (pending / partial.cover_set_id).iterdir()] == ["cover-01.jpg"]
    assert len(list((pending / original.cover_set_id).glob("*.jpg"))) == 4


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
    assert len(list((tmp_path / "project/outputs/deliverables/covers/pending").rglob("*.jpg"))) == 4


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


@pytest.mark.parametrize(
    ("severity", "message", "expected_status", "expected_codes"),
    [
        (
            "info",
            "No title failure is visible; exact title reads naturally at thumbnail size.",
            "recommended_for_human_review",
            (),
        ),
        (
            "warning",
            "The title reading order is ambiguous at thumbnail size.",
            "blocked",
            ("title_failure",),
        ),
    ],
)
def test_title_finding_only_blocks_when_severity_requires_action(
    severity, message, expected_status, expected_codes,
) -> None:
    from core.cover_models_v2 import CoverQualityReport
    from tests.test_cover_director import adaptive_director_fixture

    brief = _brief_v2()
    direction = CoverArtDirector.from_fixture(adaptive_director_fixture()).plan(
        brief, count=4,
    )
    concept = scene_to_cover_concept(brief, direction.plans[0])
    scores = {name: 90 for name in CoverQualityReport._DIMENSIONS}
    evaluator = CoverVisualEvaluator(complete=lambda *_args: {
        "status": "human_review_required",
        **scores,
        "findings": [{
            "code": "title_failure",
            "severity": severity,
            "message": message,
            "evidence": "mobile thumbnail",
        }],
    })
    service = CoverService(
        visual_evaluator=evaluator,
        thumbnail_projector=lambda *_args, **_kwargs: b"thumbnail",
    )
    image = _jpeg(marker=b"title-severity")

    report = service._quality_report(
        brief,
        concept,
        image,
        digest(image),
        "image/jpeg",
    )

    assert report.status == expected_status
    assert report.blockers == expected_codes
    assert report.repair_codes == expected_codes
    assert report.findings[0].severity == severity


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
    result = service.generate('project-one', project, brief, concepts, extra_image_attempts=1)
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
    result = service.generate('project-one', tmp_path / 'project', brief, concepts, extra_image_attempts=2)
    original = result.candidates[0]
    assert original.prompt_revision == 1
    updated = service.retry_candidate('project-one', tmp_path / 'project', result.cover_set_id,
        original.candidate_id, expected_revision=result.revision, repair_codes=['core_conflict_missing'])
    retried = updated.candidates[0]
    assert [a['prompt_revision'] for a in retried.attempt_history] == [1, 2, 3]
    assert retried.prompt_revision == 3
    assert retried.attempt_history[:2] == original.attempt_history


def _actual_jpeg():
    from io import BytesIO
    from PIL import Image
    output = BytesIO()
    Image.new("RGB", (400, 600), (35, 45, 55)).save(output, format="JPEG")
    return output.getvalue()


def _bound_inputs():
    from core.cover_typography import prepare_title_layout, canonical_digest
    brief = _brief_v2()
    direction = CoverArtDirector.from_fixture(director_fixture()).plan(brief, count=4)
    concepts = [scene_to_cover_concept(brief, plan) for plan in direction.plans]
    layout = prepare_title_layout({"title": brief.title, "lines": [brief.title], "font_id": "sans-bold",
        "font_size_min": .02, "font_size_max": .07, "region": [.1, .02, .8, .18],
        "align": "left", "safe_zones": [[0, .5, 1, .5]]}, expected_title=brief.title)
    recipes = {}
    for concept in concepts:
        recipe = {"image_prompt": f"Approved artwork without letters: {concept.concept_id}",
            "compiler_version": "fixture-frozen.v1", "title_render_mode": "composed", "title_layout": layout}
        recipes[concept.concept_id] = {**recipe, "recipe_sha256": canonical_digest(recipe)}
    return brief, concepts, recipes


def _independent_evaluator(report=None):
    from tests.test_cover_quality import _blind_observation, _scores
    response = report or _scores()
    if response.get("blockers") and "findings" not in response:
        response = {**response, "findings": [{"code": code, "severity": "blocker", "message": "Fixture observed failure", "evidence": "The central figure at the doorway"} for code in response["blockers"]]}
    return CoverVisualEvaluator(complete=lambda *_: _scores(), blind_complete=_blind_observation,
        verify_complete=lambda *_: response)


def test_bound_generation_uses_frozen_recipe_persists_artwork_final_and_independent_evaluation(tmp_path):
    import json
    brief, concepts, recipes = _bound_inputs()
    original = _actual_jpeg()
    service, client, _ = _service(tmp_path, [original] * 4)
    service.visual_evaluator = _independent_evaluator()
    project = tmp_path / "project"
    result = service.generate("project-one", project, brief, concepts, compiled_recipes=recipes,
        source_binding={"direction_id": "approved-direction", "analysis_id": "complete-analysis"})
    assert client.prompts == [recipes[item.concept_id]["image_prompt"] for item in concepts]
    assert len(client.prompts) == 4
    for candidate in result.candidates:
        report = candidate.quality_report
        assert report["evaluation"]["evaluation_status"] == "complete"
        assert report["evaluation"]["blindness_level"] == "artwork"
        assert report["assets"]["artwork_sha256"] == digest(original)
        assert (project / report["assets"]["artwork_relative_path"]).read_bytes() == original
        assert report["assets"]["final_cover_sha256"] == candidate.sha256 != digest(original)
        stored = json.loads((project / report["evaluation"]["relative_path"]).read_text())
        assert stored["quality_report"]["evaluation"]["blind_read"] == report["evaluation"]["blind_read"]
        assert candidate.attempt_history[0]["quality_report"]["assets"] == report["assets"]


def test_changed_recipe_is_rejected_before_an_image_request(tmp_path):
    brief, concepts, recipes = _bound_inputs()
    recipes[concepts[0].concept_id]["image_prompt"] = "unapproved changed prompt"
    service, client, _ = _service(tmp_path, [_actual_jpeg()] * 4)
    with pytest.raises(CoverServiceError, match="recipe hash"):
        service.generate("project-one", tmp_path / "project", brief, concepts, compiled_recipes=recipes)
    assert client.prompts == []


def test_new_story_design_failure_does_not_spend_extra_budget_and_requires_new_approval(tmp_path):
    from tests.test_cover_quality import _scores
    brief, concepts, recipes = _bound_inputs()
    service, client, _ = _service(tmp_path, [_actual_jpeg()] * 8)
    service.visual_evaluator = _independent_evaluator({**_scores(), "status": "blocked", "core_conflict_fidelity": 20,
        "blockers": ["core_conflict_missing"], "repair_codes": ["core_conflict_missing"]})
    result = service.generate("project-one", tmp_path / "project", brief, concepts, compiled_recipes=recipes, extra_image_attempts=1)
    assert len(client.prompts) == 4
    candidate = result.candidates[0]
    assert candidate.quality_report["repair_decision"]["action"] == "new_direction_approval"
    with pytest.raises(CoverServiceError, match="new_direction_approval"):
        service.retry_candidate("project-one", tmp_path / "project", result.cover_set_id, candidate.candidate_id,
            expected_revision=result.revision, repair_codes=["core_conflict_missing"])
    assert len(client.prompts) == 4


@pytest.mark.parametrize("budget", [0, 1])
def test_new_render_retry_enforces_cumulative_budget_and_keeps_exact_recipe(tmp_path, budget):
    from tests.test_cover_quality import _scores
    brief, concepts, recipes = _bound_inputs()
    service, client, _ = _service(tmp_path, [_actual_jpeg()] * 8)
    service.visual_evaluator = _independent_evaluator({**_scores(), "status": "blocked", "blockers": ["generic_ai_face"], "repair_codes": ["generic_ai_face"]})
    project = tmp_path / "project"
    result = service.generate("project-one", project, brief, concepts, compiled_recipes=recipes, extra_image_attempts=budget)
    candidate = result.candidates[0]
    if budget:
        result = service.retry_candidate("project-one", project, result.cover_set_id, candidate.candidate_id,
            expected_revision=result.revision, repair_codes=["generic_ai_face"])
        assert client.prompts[-1] == recipes[candidate.concept_id]["image_prompt"]
        assert len(result.candidates[0].attempt_history) == 2
    with pytest.raises(CoverServiceError, match="budget is exhausted"):
        service.retry_candidate("project-one", project, result.cover_set_id, candidate.candidate_id,
            expected_revision=result.revision, repair_codes=["generic_ai_face"])
    assert len(client.prompts) == 4 + budget


def test_workshop_cover_root_does_not_create_project_delivery_structure(tmp_path):
    brief, concepts, recipes = _bound_inputs()
    service, _, _ = _service(tmp_path, [_actual_jpeg()] * 4)
    service.visual_evaluator = _independent_evaluator()
    session = tmp_path / "session"
    result = service.generate("workshop-session", session, brief, concepts, compiled_recipes=recipes, cover_root=session / "covers")
    assert (session / "covers/sets" / f"{result.cover_set_id}.json").is_file()
    assert not (session / "outputs").exists()
    assert all((session / item.relative_path).is_file() for item in result.candidates)


def test_reevaluation_preserves_image_and_attempt_history_without_an_image_call(tmp_path):
    brief, concepts, recipes = _bound_inputs()
    service, client, _ = _service(tmp_path, [_actual_jpeg()] * 4)
    # No evaluator initially: this is an evaluation failure, not a redraw request.
    project = tmp_path / "project"
    initial = service.generate("project-one", project, brief, concepts, compiled_recipes=recipes)
    candidate = initial.candidates[0]
    assert candidate.quality_report["repair_decision"]["kind"] == "evaluation"
    service.visual_evaluator = _independent_evaluator()
    reviewed = service.reevaluate_candidate("project-one", project, initial.cover_set_id, candidate.candidate_id,
        expected_revision=initial.revision)
    updated = reviewed.candidates[0]
    assert len(client.prompts) == 4
    assert updated.sha256 == candidate.sha256
    assert updated.attempt_history == candidate.attempt_history
    assert updated.quality_report["evaluation"]["evaluation_status"] == "complete"
    assert updated.quality_report["evaluation"]["previous_evaluation_id"] == candidate.quality_report["evaluation"]["evaluation_id"]


@pytest.mark.parametrize("review_status", ["blocked", "human_review_required", "recommended_for_human_review", None])
@pytest.mark.parametrize("owner", ["project", "workshop"])
@pytest.mark.parametrize("candidate_status", ["ready", "rejected"])
def test_completed_cover_can_be_selected_without_a_reason_regardless_of_review(tmp_path, review_status, owner, candidate_status):
    brief, concepts, recipes = _bound_inputs()
    service, client, _ = _service(tmp_path, [_actual_jpeg()] * 4)
    project = tmp_path / "project"
    kwargs = {"cover_root": project / "discussion" / "covers"} if owner == "workshop" else {}
    result = service.generate("project-one", project, brief, concepts, compiled_recipes=recipes, **kwargs)
    report = {"status": review_status, "blockers": ["core_conflict_missing"],
              "evaluation": {"evaluation_status": "incomplete"}} if review_status else {}
    assert result.candidates[0].status == "ready", result.candidates[0].error
    target = replace(result.candidates[0], status=candidate_status, quality_report=report)
    siblings = tuple(replace(c, status="failed") for c in result.candidates[1:])
    store = CoverStore(project, **kwargs)
    result = store.save(replace(result, candidates=(target, *siblings)), expected_revision=result.revision)
    selected = service.select_candidate(project, result.cover_set_id, target.candidate_id,
        expected_revision=result.revision, expected_active_revision=0, **kwargs)
    assert selected.selected_candidate_id == target.candidate_id
    assert selected.candidates[0].status == "selected"
    assert selected.candidates[0].quality_report == report  # Selection does not claim that model review passed.
    assert selected.candidates[1:] == siblings
    assert store.active().cover_set_id == result.cover_set_id
    assert len(client.prompts) == 4  # Selection never generates or evaluates another image.


def test_optional_legacy_quality_reason_is_preserved_without_being_required(tmp_path):
    brief, concepts, recipes = _bound_inputs()
    service, client, _ = _service(tmp_path, [_actual_jpeg()] * 4)
    project = tmp_path / "project"
    result = service.generate("project-one", project, brief, concepts, compiled_recipes=recipes)
    candidate = result.candidates[0]
    selected = service.select_candidate(project, result.cover_set_id, candidate.candidate_id,
        expected_revision=result.revision, expected_active_revision=0,
        quality_acceptance_reason="I inspected the source and accept the current composition.")
    assert selected.candidates[0].quality_report["human_acceptance"]["reason"].startswith("I inspected")
    assert len(client.prompts) == 4


def test_failed_provider_attempt_consumes_bound_budget(tmp_path):
    brief, concepts, recipes = _bound_inputs()
    service, client, _ = _service(tmp_path, [ImageClientError("provider failed"), *[_actual_jpeg()] * 3])
    project = tmp_path / "project"
    result = service.generate("project-one", project, brief, concepts, compiled_recipes=recipes)
    failed = result.candidates[0]
    assert failed.status == "failed"
    assert result.concepts[0].scene_plan["_generation_authorization"]["image_attempts_reserved"] == 1
    with pytest.raises(CoverServiceError, match="budget is exhausted"):
        service.retry_candidate("project-one", project, result.cover_set_id, failed.candidate_id,
            expected_revision=result.revision)
    assert len(client.prompts) == 4


def test_actual_artwork_duplicates_block_recommendation_without_extra_provider_calls(tmp_path):
    brief, concepts, recipes = _bound_inputs()
    service, client, _ = _service(tmp_path, [_actual_jpeg()] * 4)
    service.visual_evaluator = _independent_evaluator()
    project = tmp_path / "project"
    result = service.generate("project-one", project, brief, concepts, compiled_recipes=recipes)
    candidate = result.candidates[0]
    comparison = candidate.quality_report["portfolio_comparison"]
    assert comparison["status"] == "complete"
    assert comparison["basis"] == "artwork"
    assert len(comparison["matches"]) == 3
    assert all(item["kind"] == "exact" for item in comparison["matches"])
    assert "duplicate_artwork" in candidate.quality_report["blockers"]
    assert candidate.quality_report["status"] == "blocked"
    assert len(client.prompts) == 4
    # Better evaluator scores cannot erase the local duplicate finding.
    reviewed = service.reevaluate_candidate("project-one", project, result.cover_set_id,
        candidate.candidate_id, expected_revision=result.revision)
    assert "duplicate_artwork" in reviewed.candidates[0].quality_report["blockers"]
    assert len(client.prompts) == 4


def test_actual_image_comparison_unavailable_is_not_reported_as_pass(tmp_path, monkeypatch):
    brief, concepts, recipes = _bound_inputs()
    service, client, _ = _service(tmp_path, [_actual_jpeg()] * 4)
    service.visual_evaluator = _independent_evaluator()
    def fail(_images):
        raise ValueError("Cannot decode one image")
    monkeypatch.setattr("api.cover_service.compare_cover_images", fail)
    result = service.generate("project-one", tmp_path / "project", brief, concepts, compiled_recipes=recipes)
    report = result.candidates[0].quality_report
    assert report["portfolio_comparison"]["status"] == "unavailable"
    assert report["status"] == "human_review_required"
    assert len(client.prompts) == 4


def test_local_title_recomposition_recovers_saved_artwork_without_image_client(tmp_path, monkeypatch):
    from core.cover_typography import CoverTypographyError
    from api import cover_service as module
    brief, concepts, recipes = _bound_inputs()
    original = _actual_jpeg()
    service, client, _ = _service(tmp_path, [original] * 4)
    service.visual_evaluator = _independent_evaluator()
    compose = module.compose_cover
    calls = 0
    def temporarily_unavailable(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CoverTypographyError("temporary local compositor failure")
        return compose(*args, **kwargs)
    monkeypatch.setattr(module, "compose_cover", temporarily_unavailable)
    project = tmp_path / "project"
    initial = service.generate("project-one", project, brief, concepts, compiled_recipes=recipes)
    candidate = initial.candidates[0]
    assert candidate.status == "failed"
    assert candidate.quality_report["repair_decision"]["kind"] == "typography"
    artwork_sha = candidate.quality_report["assets"]["artwork_sha256"]
    service.image_client = None
    result = service.reevaluate_candidate("project-one", project, initial.cover_set_id, candidate.candidate_id,
        expected_revision=initial.revision)
    updated = result.candidates[0]
    assert updated.status == "ready"
    assert updated.quality_report["assets"]["artwork_sha256"] == artwork_sha == digest(original)
    assert updated.quality_report["local_recomposition"]["image_provider_called"] is False
    assert updated.attempt_history[:-1] == candidate.attempt_history
    assert updated.attempt_history[-1]["operation"] == "typography_recomposition"
    assert result.concepts[0].scene_plan["_generation_authorization"]["image_attempts_reserved"] == 1
    assert len(client.prompts) == 4
    # Reapplying the identical approved layout has identical final bytes and does
    # not manufacture another image-generation attempt.
    again = service.recompose_candidate("project-one", project, result.cover_set_id, candidate.candidate_id,
        expected_revision=result.revision)
    assert again.candidates[0].sha256 == updated.sha256
    assert again.candidates[0].attempt_history == updated.attempt_history


def test_impossible_approved_title_layout_requires_new_direction_without_redraw(tmp_path, monkeypatch):
    from core.cover_typography import CoverTypographyError
    brief, concepts, recipes = _bound_inputs()
    service, client, _ = _service(tmp_path, [_actual_jpeg()] * 4)
    def impossible(*_args, **_kwargs):
        raise CoverTypographyError("Approved title cannot fit the approved bounds")
    monkeypatch.setattr("api.cover_service.compose_cover", impossible)
    project = tmp_path / "project"
    initial = service.generate("project-one", project, brief, concepts, compiled_recipes=recipes)
    candidate = initial.candidates[0]
    service.image_client = None
    with pytest.raises(CoverServiceError, match="approve a new direction"):
        service.reevaluate_candidate("project-one", project, initial.cover_set_id, candidate.candidate_id,
            expected_revision=initial.revision)
    assert len(client.prompts) == 4
    saved = CoverStore(project).load(initial.cover_set_id).candidates[0]
    assert saved.attempt_history == candidate.attempt_history
    assert saved.quality_report["assets"]["artwork_sha256"] == candidate.quality_report["assets"]["artwork_sha256"]


@pytest.mark.parametrize('model', ['gpt-image-2.5-sunburst', 'gpt-image-2.5-flare', 'publisher/image-v3'])
def test_service_persists_configured_model_and_explicit_verification_provenance(tmp_path, model):
    from types import SimpleNamespace
    class ModelClient(FakeImageClient):
        settings = SimpleNamespace(model=model)
        def generate(self, prompt):
            return replace(super().generate(prompt), model=model, observed_model=model, model_verified=True)
    registrar = Registrar()
    service = CoverService(image_client=ModelClient([_jpeg(marker=bytes([i])) for i in range(4)]),
        media_store=LocalMediaStore(tmp_path / 'media'), media_add=registrar)
    result = service.generate('project-one', tmp_path / 'project', _brief(), _concepts())
    assert all(c.status == 'ready' and c.model == model for c in result.candidates)
    assert all(c.safe_request_parameters['model_verified'] is True and
               c.safe_request_parameters['observed_model'] == model for c in result.candidates)


def test_service_rejects_mismatching_observed_model_even_when_labeled_with_request(tmp_path):
    from types import SimpleNamespace
    class Mislabelled(FakeImageClient):
        settings = SimpleNamespace(model='gpt-image-2.5-sunburst')
        def generate(self, prompt):
            return replace(super().generate(prompt), model=self.settings.model,
                           observed_model='gpt-image-2', model_verified=False)
    registrar = Registrar()
    service = CoverService(image_client=Mislabelled([_jpeg()] * 4),
        media_store=LocalMediaStore(tmp_path / 'media'), media_add=registrar)
    result = service.generate('project-one', tmp_path / 'project', _brief(), _concepts())
    assert all(c.status == 'failed' and 'model verification' in c.error for c in result.candidates)
    assert not registrar.calls
