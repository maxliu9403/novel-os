from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from api.cover_service import CoverService, CoverServiceError
from api.media import LocalMediaStore
from core.cover_models import CoverBrief, CoverConcept
from core.image_client import GeneratedImage, ImageClientError


SHA = "a" * 64


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
            content_type="image/webp",
            width=2048,
            height=3072,
            request_id=f"request-{len(self.prompts)}",
            model="gpt-image-2",
        )


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
    images = [_webp(marker=bytes([index])) for index in range(1, 5)]
    service, client, registrar = _service(tmp_path, images)

    cover_set = service.generate("project-one", tmp_path / "project", _brief(), _concepts())

    assert cover_set.status == "ready"
    assert [candidate.status for candidate in cover_set.candidates] == ["ready"] * 4
    assert client.prompts == [concept.generation_prompt for concept in _concepts()]
    assert len(registrar.calls) == 4
    for index, candidate in enumerate(cover_set.candidates, start=1):
        assert candidate.media_id == f"media-{index}"
        assert candidate.relative_path.endswith(f"cover-{index:02d}.webp")
        assert (tmp_path / "project" / candidate.relative_path).is_file()


def test_generate_supports_unicode_project_id(tmp_path) -> None:
    service, _, _ = _service(
        tmp_path, [_webp(marker=bytes([index])) for index in range(1, 5)]
    )

    cover_set = service.generate("中文小说", tmp_path / "中文小说", _brief(), _concepts())

    assert cover_set.status == "ready"
    assert len(list((tmp_path / "media" / "中文小说").glob("**/*.webp"))) == 4


def test_generate_preserves_partial_success_and_candidate_errors(tmp_path) -> None:
    failed = ImageClientError("provider unavailable", retryable=True, status_code=503)
    service, _, _ = _service(
        tmp_path, [_webp(marker=b"1"), failed, _webp(marker=b"3"), failed]
    )

    cover_set = service.generate("project-one", tmp_path / "project", _brief(), _concepts())

    assert cover_set.status == "partial"
    assert [item.status for item in cover_set.candidates] == ["ready", "failed", "ready", "failed"]
    assert cover_set.candidates[1].error == "provider unavailable"
    assert len(list((tmp_path / "project" / "outputs/deliverables/covers/pending").glob("*"))) == 2


def test_new_generation_removes_pending_images_from_previous_cover_set(tmp_path) -> None:
    project = tmp_path / "project"
    first, _, _ = _service(
        tmp_path, [_webp(marker=bytes([index])) for index in range(1, 5)]
    )
    first.generate("project-one", project, _brief(), _concepts())

    failed = ImageClientError("provider unavailable", retryable=True)
    second, _, _ = _service(
        tmp_path, [_webp(marker=b"new"), failed, failed, failed]
    )
    partial = second.generate("project-one", project, _brief(), _concepts())

    pending = project / "outputs/deliverables/covers/pending"
    assert partial.status == "partial"
    assert [path.name for path in pending.iterdir()] == ["cover-01.webp"]


def test_invalid_image_bytes_create_no_deliverable_projection(tmp_path) -> None:
    service, _, registrar = _service(
        tmp_path, [_webp(1024, 1536, bytes([index])) for index in range(1, 5)]
    )

    cover_set = service.generate("project-one", tmp_path / "project", _brief(), _concepts())

    assert cover_set.status == "failed"
    assert {candidate.status for candidate in cover_set.candidates} == {"failed"}
    assert registrar.calls == []
    pending = tmp_path / "project" / "outputs/deliverables/covers/pending"
    assert not pending.exists() or not list(pending.iterdir())


def test_identical_image_bytes_reuse_content_addressed_media(tmp_path) -> None:
    image = _webp(marker=b"same")
    service, _, _ = _service(tmp_path, [image, image, image, image])

    cover_set = service.generate("project-one", tmp_path / "project", _brief(), _concepts())

    assert {candidate.media_id for candidate in cover_set.candidates} == {"media-1"}
    assert len(list((tmp_path / "media/project-one").glob("**/*.webp"))) == 1
    assert len(list((tmp_path / "project/outputs/deliverables/covers/pending").glob("*.webp"))) == 4


def test_retry_calls_only_failed_candidate_and_reaches_ready(tmp_path) -> None:
    failed = ImageClientError("busy", retryable=True)
    service, client, _ = _service(
        tmp_path,
        [
            _webp(marker=b"1"), failed, _webp(marker=b"3"), failed,
            _webp(marker=b"2"),
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
    assert client.prompts[-1] == _concepts()[1].generation_prompt


def test_reject_and_select_are_explicit_and_idempotent(tmp_path) -> None:
    service, _, _ = _service(
        tmp_path, [_webp(marker=bytes([i])) for i in range(1, 5)]
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


def test_stale_set_selection_requires_explicit_confirmation(tmp_path) -> None:
    service, _, _ = _service(
        tmp_path, [_webp(marker=bytes([i])) for i in range(1, 5)]
    )
    project = tmp_path / "project"
    ready = service.generate("project-one", project, _brief(), _concepts())
    stale = service.mark_stale(project, ready.cover_set_id, "b" * 64, "")

    with pytest.raises(CoverServiceError, match="stale"):
        service.select_candidate(
            project, stale.cover_set_id, stale.candidates[0].candidate_id,
            expected_revision=stale.revision, expected_active_revision=0,
        )
