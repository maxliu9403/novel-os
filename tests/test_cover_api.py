from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

import pytest
from fastapi.testclient import TestClient

from api.cover_service import CoverService
from api import db
from api.main import create_app
from api.media import LocalMediaStore
from api import routes
from api.routes import get_cover_art_director, get_cover_service
from core.cover_director import CoverArtDirector
from core.cover_models import CoverBrief, CoverConcept
from core.cover_models_v2 import ArtDirectionSet, CoverBriefV2, CoverQualityReport
from core.cover_prompt_compiler import scene_to_cover_concept
from core.image_client import GeneratedImage, ImageClientError
from tests.test_cover_director import adaptive_director_fixture, director_fixture
from tests.test_cover_models_v2 import two_character_fixture


def _jpeg(marker: bytes) -> bytes:
    return (
        b"\xff\xd8\xff\xc0\x00\x11\x08\x0c\x00\x08\x00"
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        + marker
        + b"\xff\xd9"
    )


class ImageClient:
    def __init__(self) -> None:
        self.calls = 0
        self.fail_calls: set[int] = set()

    def generate(self, _prompt: str) -> GeneratedImage:
        self.calls += 1
        if self.calls in self.fail_calls:
            raise ImageClientError("temporary image failure", retryable=True)
        return GeneratedImage(
            data=_jpeg(bytes([self.calls])), content_type="image/jpeg",
            width=2048, height=3072, request_id=f"request-{self.calls}",
            model="gpt-image-2",
        )


def _brief(title: str = "The Door Is Mine") -> dict:
    return {
        "schema_version": 1,
        "title": title,
        "language": "English",
        "genre": "domestic revenge",
        "target_audience": "women 30-50",
        "market_scope": "English serialized fiction",
        "core_task": "A caregiver claims a home.",
        "core_conflict": "Her family demands her labor.",
        "emotional_promise": "earned independence",
        "protagonist": {"role": "caregiver", "visual_identity": "woman with key", "agency_signal": "closes door"},
        "relationship_or_power_contrast": "one woman against two households",
        "decisive_story_node": "she signs a deed",
        "secondary_task": {"story_function": "protect child", "visual_signal": "backpack"},
        "world_signals": ["fictional commuter district"],
        "title_direction": {"hierarchy": "large", "preferred_zone": "top", "readability": "mobile_thumbnail"},
        "forbidden_elements": ["real cities"],
    }


def _concepts(title: str = "The Door Is Mine") -> list[dict]:
    return [{
        "concept_id": f"concept-{index}",
        "visual_strategy": f"strategy-{index}",
        "focal_scene": f"scene-{index}",
        "composition": "portrait",
        "palette": "red and charcoal",
        "secondary_signal": "house key",
        "title_treatment": "large title at top",
        "generation_prompt": f'Render exact title "{title}" once. Strategy {index}.',
    } for index in range(1, 5)]


def _client(tmp_path, monkeypatch, *, director=None) -> tuple[TestClient, ImageClient]:
    monkeypatch.setenv("NOVEL_OS_SETTINGS_PATH", str(tmp_path / "settings.json"))
    projects = tmp_path / "projects"
    media = tmp_path / "media"
    app = create_app(projects_root=projects, media_root=media, db_url=f"sqlite:///{tmp_path / 'db.sqlite'}")
    image_client = ImageClient()
    store = LocalMediaStore(media)

    def media_add(**fields):
        from api import db
        return db.media_add(**fields)

    app.dependency_overrides[get_cover_service] = lambda: CoverService(
        image_client=image_client,
        media_store=store,
        media_add=media_add,
    )
    if director is not None:
        app.dependency_overrides[get_cover_art_director] = lambda: director
    return TestClient(app), image_client


def _wait(client: TestClient, job_id: str) -> dict:
    for _ in range(100):
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.01)
    raise AssertionError("cover job did not finish")


def test_cover_art_director_uses_its_configured_reasoning_effort(monkeypatch) -> None:
    from core import llm_client, studio_settings

    captured = {}

    class FakeClient:
        model = "gpt-5.6-sol"
        provider = "codex"

        def __init__(self, **kwargs):
            captured.update(kwargs)

        def complete(self, _system: str, _user: str) -> str:
            return "{}"

    monkeypatch.setattr(llm_client, "LLMClient", FakeClient)
    monkeypatch.setattr(
        studio_settings,
        "resolve_cover_director_settings",
        lambda: studio_settings.CoverDirectorSettings(
            provider="codex",
            model="gpt-5.6-sol",
            base_url="",
            api_key="",
            timeout_seconds=600,
            reasoning_effort="high",
        ),
    )

    director = routes.get_cover_art_director()

    assert isinstance(director, CoverArtDirector)
    assert captured["timeout_seconds"] == 600
    assert captured["reasoning_effort"] == "high"


class CanonAwareFixtureDirector:
    def plan(self, brief, *, count: int, **_context) -> ArtDirectionSet:
        payload = director_fixture()
        character_id = brief.principal_characters[0].character_id
        node_id = brief.decisive_story_nodes[0].node_id
        for plan in payload["plans"]:
            plan["cast"] = [character_id]
            plan["focal_character_id"] = character_id
            plan["story_evidence_refs"] = [
                f"character:{character_id}", f"node:{node_id}",
            ]
            plan["gaze_graph"] = [f"{character_id} -> primary evidence"]
        payload["plans"] = payload["plans"][:count]
        return ArtDirectionSet.from_dict(payload, brief_sha256=brief.source_prompt_sha256)


def _write_pending_cover_sources(project_path) -> None:
    inputs = project_path / "outputs" / "input"
    state_dir = project_path / "outputs" / "state"
    inputs.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    inputs.joinpath("prompt.md").write_text(
        "# Legacy prompt\n```json\n"
        + json.dumps([
            {"id": "audience", "age_band": "women ages 30-50"},
            {
                "id": "claire_bennett",
                "name": "Claire Bennett",
                "public_identity": "Independent interior designer returning to practice.",
            },
        ])
        + "\n```\n",
        encoding="utf-8",
    )
    inputs.joinpath("brief.json").write_text(json.dumps({
        "title": "The Empty Chair Beside Her",
        "genre": "domestic drama",
        "audience": "women ages 30-50",
        "language": "American English",
    }), encoding="utf-8")
    inputs.joinpath("foundation.json").write_text(json.dumps({
        "title": "The Empty Chair Beside Her",
        "premise": "Claire leaves an unreliable marriage and rebuilds her work.",
        "characters": [{
            "id": "claire_bennett",
            "name": "Claire Bennett",
            "role": "protagonist",
            "age": None,
            "physical_description": "A practical woman rebuilding after a separation.",
            "external_goal": "Build a dependable home and independent practice.",
            "strength": "Documents the truth and sets boundaries.",
        }],
        "plot_threads": [{
            "id": "main",
            "name": "independence",
            "description": "Claire replaces promises with reliable care.",
            "type": "main",
        }],
        "setting": {
            "time_period": "August 2027 through June 2028",
            "primary_location": "Linden Falls, a fictional city centered on Willow Creek",
        },
    }), encoding="utf-8")
    state_dir.joinpath("story_state.json").write_text(json.dumps({
        "metadata": {
            "genre": "domestic drama",
            "language": "American English",
            "audience": "women ages 30-50",
        },
        "characters": {"claire_bennett": {
            "id": "claire_bennett",
            "full_name": "Claire Bennett",
            "role": "protagonist",
        }},
        "story_bible": {"setting": {
            "time_period": "August 2027 through June 2028",
            "primary_location": "Linden Falls, a fictional city centered on Willow Creek",
        }},
    }), encoding="utf-8")


def test_generate_lists_and_downloads_project_cover_package(tmp_path, monkeypatch) -> None:
    client, image_client = _client(tmp_path, monkeypatch)
    project = client.post("/api/projects", json={"title": "API Cover", "genre": "Drama"}).json()

    response = client.post(f"/api/projects/{project['id']}/covers/generate", json={
        "brief": _brief(), "concepts": _concepts(),
    })
    assert response.status_code == 202
    job = _wait(client, response.json()["job_id"])
    assert job["status"] == "done"
    assert job["meta"]["cover_set_id"].startswith("cover-")
    assert image_client.calls == 4

    sets = client.get(f"/api/projects/{project['id']}/covers").json()
    assert len(sets) == 1
    assert sets[0]["status"] == "ready"
    assert len(sets[0]["candidates"]) == 4
    assert all(candidate["url"].startswith(f"/api/projects/{project['id']}/media/") for candidate in sets[0]["candidates"])
    assert "api_key" not in repr(sets)

    package = client.get(f"/api/projects/{project['id']}/deliverables/package")
    assert package.status_code == 200
    assert package.content.startswith(b"PK")
    assert "book-package.zip" in package.headers["content-disposition"]


def test_cover_routes_enforce_project_isolation_and_revision_conflicts(tmp_path, monkeypatch) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    first = client.post("/api/projects", json={"title": "First", "genre": "Drama"}).json()
    second = client.post("/api/projects", json={"title": "Second", "genre": "Drama"}).json()
    job_id = client.post(f"/api/projects/{first['id']}/covers/generate", json={
        "brief": _brief(), "concepts": _concepts(),
    }).json()["job_id"]
    _wait(client, job_id)
    cover_set = client.get(f"/api/projects/{first['id']}/covers").json()[0]
    candidate = cover_set["candidates"][0]
    assert cover_set["active_revision"] == 0

    assert client.get(
        f"/api/projects/{second['id']}/covers/{cover_set['cover_set_id']}"
    ).status_code == 404
    conflict = client.post(
        f"/api/projects/{first['id']}/covers/{cover_set['cover_set_id']}/candidates/{candidate['candidate_id']}/select",
        json={"expected_revision": 1, "expected_active_revision": 0},
    )
    assert conflict.status_code == 409

    selected = client.post(
        f"/api/projects/{first['id']}/covers/{cover_set['cover_set_id']}/candidates/{candidate['candidate_id']}/select",
        json={"expected_revision": cover_set["revision"], "expected_active_revision": 0},
    )
    assert selected.status_code == 200
    assert selected.json()["selected_candidate_id"] == candidate["candidate_id"]
    assert selected.json()["active_revision"] == 1
    assert client.get(f"/api/projects/{first['id']}/covers").json()[0]["active_revision"] == 1


def test_generate_validates_concept_count_before_starting_job(tmp_path, monkeypatch) -> None:
    client, image_client = _client(tmp_path, monkeypatch)
    project = client.post("/api/projects", json={"title": "Invalid", "genre": "Drama"}).json()

    response = client.post(f"/api/projects/{project['id']}/covers/generate", json={
        "brief": _brief(), "concepts": _concepts()[:2],
    })

    assert response.status_code == 400
    assert "between 3 and 5" in response.json()["detail"]
    assert image_client.calls == 0


def test_cover_reads_return_not_found_for_missing_project(tmp_path, monkeypatch) -> None:
    client, _ = _client(tmp_path, monkeypatch)

    listing = client.get("/api/projects/missing/covers")
    package = client.get("/api/projects/missing/deliverables/package")

    assert listing.status_code == 404
    assert listing.json()["detail"] == "Project 'missing' not found"
    assert package.status_code == 404


def test_generate_can_derive_brief_and_concepts_from_persisted_prompt(
    tmp_path, monkeypatch,
) -> None:
    client, image_client = _client(tmp_path, monkeypatch)
    project = client.post(
        "/api/projects", json={"title": "Prompt Cover", "genre": "Drama"}
    ).json()
    prompt = tmp_path / "projects" / project["id"] / "outputs" / "input" / "prompt.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(
        "COVER_HANDOFF_BEGIN\n```json\n"
        + json.dumps(_brief(), ensure_ascii=False)
        + "\n```\nCOVER_HANDOFF_END\n",
        encoding="utf-8",
    )

    response = client.post(
        f"/api/projects/{project['id']}/covers/generate", json={"count": 4}
    )

    assert response.status_code == 202
    job = _wait(client, response.json()["job_id"])
    assert job["status"] == "done"
    assert image_client.calls == 4
    cover_set = client.get(f"/api/projects/{project['id']}/covers").json()[0]
    assert cover_set["brief"]["title"] == "The Door Is Mine"
    assert len({item["visual_strategy"] for item in cover_set["concepts"]}) == 4


def test_generate_legacy_project_derives_brief_from_durable_story_artifacts(
    tmp_path, monkeypatch,
) -> None:
    client, image_client = _client(tmp_path, monkeypatch)
    project = client.post(
        "/api/projects", json={"title": "The Care Ledger", "genre": "Domestic drama"}
    ).json()
    inputs = tmp_path / "projects" / project["id"] / "outputs" / "input"
    inputs.mkdir(parents=True, exist_ok=True)
    inputs.joinpath("prompt.md").write_text(
        "# Legacy approved prompt\n\nA caregiver uncovers a hidden rehabilitation plan.",
        encoding="utf-8",
    )
    inputs.joinpath("brief.json").write_text(json.dumps({
        "title": "The Care Ledger",
        "author": "",
        "genre": "domestic revenge",
        "audience": "women rebuilding after unequal caregiving",
        "language": "English",
    }), encoding="utf-8")
    inputs.joinpath("foundation.json").write_text(json.dumps({
        "title": "The Care Ledger",
        "premise": "Her husband demands that she abandon her mother and carry both households alone.",
        "characters": [
            {
                "id": "char_001",
                "name": "Mara Vale",
                "role": "protagonist",
                "physical_description": "a composed caregiver carrying a document wallet",
                "external_goal": "protect her mother, child, income, and right to choose",
                "strength": "reconstruct the truth from schedules, receipts, and original messages",
            },
            {
                "id": "char_002",
                "name": "Evan Vale",
                "role": "antagonist",
                "external_goal": "preserve his public image by transferring care work to his wife",
            },
        ],
        "plot_threads": [
            {
                "name": "care work and self-determination",
                "description": "Mara replaces coerced care with written boundaries and independent housing.",
                "type": "main",
                "priority": 5,
            },
            {
                "name": "the hidden referral",
                "description": "A concealed referral proves that professional rehabilitation was available.",
                "type": "mystery",
                "priority": 5,
            },
        ],
        "setting": {
            "time_period": "present day",
            "primary_location": "a fictional commuter district with hospitals and a modest apartment",
        },
        "ending_contract": {
            "main_conflict": {
                "protagonist_choice": "Mara refuses to trade silence for the marriage.",
            },
            "plot_payoffs": [{
                "required_payoff": "The complete care ledger exposes the missing shifts and payments.",
            }],
            "emotional_contract": {
                "reader_emotion": "anger resolved through evidence, boundaries, and earned independence",
            },
        },
    }), encoding="utf-8")

    response = client.post(
        f"/api/projects/{project['id']}/covers/generate", json={"count": 4}
    )

    assert response.status_code == 202
    job = _wait(client, response.json()["job_id"])
    assert job["status"] == "done"
    cover_set = client.get(f"/api/projects/{project['id']}/covers").json()[0]
    assert image_client.calls == 4
    assert cover_set["brief"]["title"] == "The Care Ledger"
    assert cover_set["brief"]["target_audience"] == (
        "women rebuilding after unequal caregiving"
    )
    assert cover_set["brief"]["protagonist"]["visual_identity"] == (
        "a composed caregiver carrying a document wallet"
    )
    assert cover_set["brief"]["decisive_story_node"] == (
        "A concealed referral proves that professional rehabilitation was available."
    )
    assert cover_set["brief"]["secondary_task"]["visual_signal"] == (
        "The complete care ledger exposes the missing shifts and payments."
    )


def test_retry_endpoint_replaces_only_the_failed_candidate(tmp_path, monkeypatch) -> None:
    client, image_client = _client(tmp_path, monkeypatch)
    image_client.fail_calls = {2}
    project = client.post("/api/projects", json={"title": "Retry", "genre": "Drama"}).json()
    generate_job = client.post(f"/api/projects/{project['id']}/covers/generate", json={
        "brief": _brief(), "concepts": _concepts(),
    }).json()["job_id"]
    _wait(client, generate_job)
    partial = client.get(f"/api/projects/{project['id']}/covers").json()[0]
    failed = next(item for item in partial["candidates"] if item["status"] == "failed")

    response = client.post(
        f"/api/projects/{project['id']}/covers/{partial['cover_set_id']}/candidates/{failed['candidate_id']}/retry",
        json={"expected_revision": partial["revision"]},
    )
    assert response.status_code == 202
    job = _wait(client, response.json()["job_id"])

    assert job["status"] == "done"
    assert job["meta"]["cover_set_id"] == partial["cover_set_id"]
    assert image_client.calls == 5
    ready = client.get(
        f"/api/projects/{project['id']}/covers/{partial['cover_set_id']}"
    ).json()
    assert ready["status"] == "ready"
    assert {item["status"] for item in ready["candidates"]} == {"ready"}


def test_generation_automatically_repairs_a_render_that_drops_the_causal_relationship(
    tmp_path, monkeypatch,
) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    project = client.post(
        "/api/projects", json={"title": "Conflict Audit", "genre": "Drama"}
    ).json()
    brief = CoverBriefV2.from_dict(two_character_fixture(), source_prompt_sha256="a" * 64)
    direction = CoverArtDirector.from_fixture(adaptive_director_fixture()).plan(brief, count=4)
    concepts = [
        scene_to_cover_concept(
            brief,
            plan,
            visual_identity=direction.visual_identity,
            evidence_ledger=direction.evidence_ledger,
            conflict_contract=direction.core_conflict_visual_contract,
        )
        for plan in direction.plans
    ]

    class Evaluator:
        available = True

        def __init__(self) -> None:
            self.calls = 0

        def evaluate(self, **_kwargs):
            self.calls += 1
            common = dict(
                canon_fidelity=92,
                required_cast_coverage=92,
                age_and_environment_fidelity=92,
                medium_fidelity=92,
                anatomy_and_physics=92,
                core_conflict_fidelity=92,
                causal_relationship_clarity=92,
                protagonist_agency=92,
                cinematic_storytelling=92, genre_emotion=92, thumbnail_clarity=92,
                hook_promise_alignment=92, title_legibility_advisory=92,
            )
            if self.calls == 1:
                return CoverQualityReport(
                    status="human_review_required",
                    blockers=(),
                    repair_codes=(),
                    evidence=("The planned spouse is absent",),
                    **{**common, "causal_relationship_clarity": 20},
                )
            return CoverQualityReport(status="human_review_required", **common)

    image_client = ImageClient()
    evaluator = Evaluator()
    service = CoverService(
        image_client=image_client,
        media_store=LocalMediaStore(tmp_path / "media"),
        media_add=db.media_add,
        visual_evaluator=evaluator,
        thumbnail_projector=lambda _image, **_size: b"thumbnail",
    )

    cover_set = service.generate(
        project["id"], tmp_path / "projects" / project["id"], brief, concepts,
    )

    assert image_client.calls == 5
    assert evaluator.calls == 5
    repaired = cover_set.candidates[0]
    assert repaired.prompt_revision == 2
    assert len(repaired.attempt_history) == 2
    assert repaired.attempt_history[1]["repair_codes"] == ["causal_relationship_missing"]
    assert "Repair focus: causal relationship missing." in repaired.generation_prompt


def test_selecting_existing_cover_does_not_require_image_api_key(tmp_path, monkeypatch) -> None:
    for key in (
        "NOVEL_OS_COVER_API_KEY", "NOVEL_OS_API_KEY", "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NOVEL_OS_SETTINGS_PATH", str(tmp_path / "settings.json"))
    projects = tmp_path / "projects"
    media = tmp_path / "media"
    app = create_app(
        projects_root=projects,
        media_root=media,
        db_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
    )
    client = TestClient(app)
    project = client.post(
        "/api/projects", json={"title": "Existing Cover", "genre": "Drama"}
    ).json()
    image_client = ImageClient()
    covers = CoverService(
        image_client=image_client,
        media_store=LocalMediaStore(media),
        media_add=db.media_add,
    )
    cover_set = covers.generate(
        project["id"], projects / project["id"],
        CoverBrief.from_dict(_brief(), source_prompt_sha256="a" * 64),
        [CoverConcept.from_dict(item) for item in _concepts()],
    )

    response = client.post(
        f"/api/projects/{project['id']}/covers/{cover_set.cover_set_id}"
        f"/candidates/{cover_set.candidates[0].candidate_id}/select",
        json={"expected_revision": cover_set.revision, "expected_active_revision": 0},
    )

    assert response.status_code == 200
    assert response.json()["selected_candidate_id"] == cover_set.candidates[0].candidate_id


def test_cover_mutation_service_is_available_without_provider_configuration(
    tmp_path, monkeypatch,
) -> None:
    for key in (
        "NOVEL_OS_COVER_API_KEY", "NOVEL_OS_API_KEY", "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NOVEL_OS_SETTINGS_PATH", str(tmp_path / "settings.json"))

    service = routes.get_cover_mutation_service(LocalMediaStore(tmp_path / "media"))

    assert service.image_client is None


def test_direction_api_persists_and_approves_exact_direction_hash(tmp_path, monkeypatch) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    project = client.post("/api/projects", json={"title": "Direction API", "genre": "Drama"}).json()

    created = client.post(
        f"/api/projects/{project['id']}/covers/directions",
        json={"brief": two_character_fixture(), "direction": director_fixture()},
    )

    assert created.status_code == 201
    pending = created.json()
    assert pending["status"] == "awaiting_approval"
    approved = client.post(
        f"/api/projects/{project['id']}/covers/directions/{pending['direction_id']}/approve",
        json={
            "expected_brief_sha256": pending["brief_sha256"],
            "approved_direction_sha256": pending["direction_sha256"],
        },
    )

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"


def test_direction_api_ignores_client_supplied_approved_status(tmp_path, monkeypatch) -> None:
    client, _ = _client(tmp_path, monkeypatch)
    project = client.post(
        "/api/projects", json={"title": "Approval Boundary", "genre": "Drama"}
    ).json()
    payload = director_fixture()
    payload["status"] = "approved"

    response = client.post(
        f"/api/projects/{project['id']}/covers/directions",
        json={"brief": two_character_fixture(), "direction": payload},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "awaiting_approval"


def test_new_direction_supersedes_older_approval_before_image_call(
    tmp_path, monkeypatch,
) -> None:
    client, image_client = _client(tmp_path, monkeypatch)
    project = client.post(
        "/api/projects", json={"title": "Latest Direction Gate", "genre": "Drama"}
    ).json()
    first = client.post(
        f"/api/projects/{project['id']}/covers/directions",
        json={"brief": two_character_fixture(), "direction": director_fixture()},
    ).json()
    approved = client.post(
        f"/api/projects/{project['id']}/covers/directions/{first['direction_id']}/approve",
        json={
            "expected_brief_sha256": first["brief_sha256"],
            "approved_direction_sha256": first["direction_sha256"],
        },
    ).json()
    second = client.post(
        f"/api/projects/{project['id']}/covers/directions",
        json={"brief": two_character_fixture(), "direction": director_fixture()},
    )
    assert second.status_code == 201

    response = client.post(
        f"/api/projects/{project['id']}/covers/generate",
        json={
            "brief": approved["brief"],
            "source_prompt_sha256": approved["brief_sha256"],
            "direction_id": approved["direction_id"],
            "approved_direction_sha256": approved["direction_sha256"],
        },
    )

    assert response.status_code == 409
    assert "latest" in response.json()["detail"]
    assert image_client.calls == 0


def test_direction_api_plans_from_persisted_v2_story_facts(tmp_path, monkeypatch) -> None:
    client, image_client = _client(
        tmp_path,
        monkeypatch,
        director=CoverArtDirector.from_fixture(director_fixture()),
    )
    project = client.post(
        "/api/projects", json={"title": "Direction from Story", "genre": "Drama"}
    ).json()
    prompt = tmp_path / "projects" / project["id"] / "outputs" / "input" / "prompt.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(
        "COVER_HANDOFF_BEGIN\n```json\n"
        + json.dumps(two_character_fixture(), ensure_ascii=False)
        + "\n```\nCOVER_HANDOFF_END\n",
        encoding="utf-8",
    )

    response = client.post(
        f"/api/projects/{project['id']}/covers/directions",
        json={"count": 4},
    )

    assert response.status_code == 201
    created = response.json()
    assert created["status"] == "awaiting_approval"
    assert created["brief"]["schema_version"] == 2
    assert created["brief"]["principal_characters"][0]["age"] == 34
    assert len(created["plans"]) == 4
    assert image_client.calls == 0


def test_adaptive_direction_binds_preface_and_final_prose_then_generates_image2_prompts(
    tmp_path, monkeypatch,
) -> None:
    photographic_fixture = adaptive_director_fixture()
    photographic_fixture["profile_version"] = "cover-profiles.v8"
    client, image_client = _client(
        tmp_path,
        monkeypatch,
        director=CoverArtDirector.from_fixture(photographic_fixture),
    )
    project = client.post(
        "/api/projects", json={"title": "Evidence Cover", "genre": "Drama"}
    ).json()
    project_path = tmp_path / "projects" / project["id"]
    prompt = project_path / "outputs" / "input" / "prompt.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(
        "COVER_HANDOFF_BEGIN\n```json\n"
        + json.dumps(two_character_fixture(), ensure_ascii=False)
        + "\n```\nCOVER_HANDOFF_END\n",
        encoding="utf-8",
    )
    publication = project_path / "outputs" / "publication" / "publication-copy.json"
    publication.parent.mkdir(parents=True, exist_ok=True)
    publication.write_text(json.dumps({
        "reader_heading": "The Key He Still Expected",
        "hook_lead": "Can one removed key change who belongs behind the family door?",
        "spoiler_free_blurb": "A shared doorway becomes an irreversible family boundary.",
        "whole_book_core_conflict": "Access, care, and belonging stop meaning the same thing.",
    }), encoding="utf-8")
    manuscript = project_path / "outputs" / "manuscript"
    manuscript.mkdir(parents=True, exist_ok=True)
    manuscript.joinpath("chapter_001_final.md").write_text(
        "# Chapter 1\n\nMara closes the door while the brass key warms in her palm.",
        encoding="utf-8",
    )

    created_response = client.post(
        f"/api/projects/{project['id']}/covers/directions", json={"count": 4},
    )

    assert created_response.status_code == 201
    created = created_response.json()
    assert created["profile_version"] == "cover-profiles.v8"
    assert created["visual_identity"]["design_thesis"].startswith("Turn the shared doorway")
    assert "publication_intro" in {
        item["source_type"] for item in created["evidence_ledger"]["items"]
    }
    design_dir = project_path / "outputs" / "covers" / "design"
    assert design_dir.joinpath("visual-evidence-ledger.json").is_file()
    assert design_dir.joinpath("book-visual-identity.json").is_file()
    assert design_dir.joinpath("core-conflict-visual-contract.json").is_file()

    approved = client.post(
        f"/api/projects/{project['id']}/covers/directions/{created['direction_id']}/approve",
        json={
            "expected_brief_sha256": created["brief_sha256"],
            "approved_direction_sha256": created["direction_sha256"],
        },
    ).json()
    response = client.post(
        f"/api/projects/{project['id']}/covers/generate",
        json={
            "direction_id": approved["direction_id"],
            "approved_direction_sha256": approved["direction_sha256"],
        },
    )

    assert response.status_code == 202
    assert _wait(client, response.json()["job_id"])["status"] == "done"
    assert image_client.calls == 4
    cover_set = client.get(f"/api/projects/{project['id']}/covers").json()[0]
    assert "lead book-cover designer" in cover_set["concepts"][0]["generation_prompt"]
    assert cover_set["compiler_version"] == "cover-compiler.v14"


def test_v9_relationship_cast_survives_approval_storage_and_generation(tmp_path, monkeypatch):
    from tests.test_cover_story_policy import story_fixture

    brief, fixture = story_fixture()
    client, image_client = _client(tmp_path, monkeypatch, director=CoverArtDirector.from_fixture(fixture))
    project = client.post('/api/projects', json={'title': brief.title, 'genre': 'Drama'}).json()
    prompt = tmp_path / 'projects' / project['id'] / 'outputs' / 'input' / 'prompt.md'
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text('COVER_HANDOFF_BEGIN\n```json\n' + json.dumps(brief.to_dict()) + '\n```\nCOVER_HANDOFF_END\n')
    url = f"/api/projects/{project['id']}/covers"
    response = client.post(url + '/directions', json={'count': 4})
    assert response.status_code == 201, response.text
    created = response.json()
    assert created['profile_version'] == 'cover-profiles.v9'
    assert all(plan['story_policy_version'] == 'cover-story.v1' for plan in created['plans'])
    approved = client.post(url + '/directions/' + created['direction_id'] + '/approve', json={
        'expected_brief_sha256': created['brief_sha256'],
        'approved_direction_sha256': created['direction_sha256'],
    })
    assert approved.status_code == 200, approved.text
    result = client.post(url + '/generate', json={
        'direction_id': created['direction_id'],
        'approved_direction_sha256': approved.json()['direction_sha256'],
    })
    assert result.status_code == 202, result.text
    assert _wait(client, result.json()['job_id'])['status'] == 'done'
    cover_set = client.get(url).json()[0]
    assert image_client.calls == 4
    plans = [item['scene_plan'] for item in cover_set['concepts']]
    assert [plan['cast'] for plan in plans] == [plan['cast'] for plan in fixture['plans']]
    assert plans[2]['focal_character_id'] == 'char_child'
    assert all('THUMBNAIL STORY READ' in item['generation_prompt'] for item in cover_set['concepts'])


def test_adaptive_direction_becomes_stale_when_publication_intro_changes(
    tmp_path, monkeypatch,
) -> None:
    client, image_client = _client(
        tmp_path,
        monkeypatch,
        director=CoverArtDirector.from_fixture(adaptive_director_fixture()),
    )
    project = client.post(
        "/api/projects", json={"title": "Stale Evidence", "genre": "Drama"}
    ).json()
    project_path = tmp_path / "projects" / project["id"]
    prompt = project_path / "outputs" / "input" / "prompt.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(
        "COVER_HANDOFF_BEGIN\n```json\n"
        + json.dumps(two_character_fixture(), ensure_ascii=False)
        + "\n```\nCOVER_HANDOFF_END\n",
        encoding="utf-8",
    )
    publication = project_path / "outputs" / "publication" / "publication-copy.json"
    publication.parent.mkdir(parents=True, exist_ok=True)
    publication.write_text(json.dumps({
        "hook_lead": "The original spoiler-safe hook uses the shared key.",
    }), encoding="utf-8")
    created = client.post(
        f"/api/projects/{project['id']}/covers/directions", json={"count": 4},
    ).json()
    approved = client.post(
        f"/api/projects/{project['id']}/covers/directions/{created['direction_id']}/approve",
        json={
            "expected_brief_sha256": created["brief_sha256"],
            "approved_direction_sha256": created["direction_sha256"],
        },
    ).json()
    publication.write_text(json.dumps({
        "hook_lead": "The revised hook now makes the empty chair the central reader promise.",
    }), encoding="utf-8")

    response = client.post(
        f"/api/projects/{project['id']}/covers/generate",
        json={
            "direction_id": approved["direction_id"],
            "approved_direction_sha256": approved["direction_sha256"],
        },
    )

    assert response.status_code == 409
    assert "preface" in response.json()["detail"]
    assert image_client.calls == 0
    assert client.get(f"/api/projects/{project['id']}/covers/directions").json()[0]["status"] == "stale"


@pytest.mark.parametrize(
    ("prompt_text", "expected_identity", "expected_source_ref"),
    [
        pytest.param(
            "# Legacy prompt\n\n"
            "```yaml\n"
            "financial_baseline:\n"
            "  claire:\n"
            "    occupation: accounts-payable clerk\n"
            "```\n",
            "accounts-payable clerk",
            "outputs/input/prompt.md:structured_character_facts",
            id="structured-occupation",
        ),
        pytest.param(
            "# Story\n\n## Character Core\n\n"
            "### Claire Bennett\n\n"
            "Claire was an excellent university graduate and teacher who stepped behind "
            "her husband's career. She wants to recover her independence.\n",
            "Claire was an excellent university graduate and teacher who stepped behind "
            "her husband's career.",
            "outputs/input/prompt.md:7",
            id="markdown-biography",
        ),
    ],
)
def test_direction_api_plans_from_confirmed_legacy_story_facts(
    tmp_path,
    monkeypatch,
    prompt_text,
    expected_identity,
    expected_source_ref,
) -> None:
    client, image_client = _client(
        tmp_path,
        monkeypatch,
        director=CanonAwareFixtureDirector(),
    )
    project = client.post(
        "/api/projects", json={"title": "Legacy Direction", "genre": "Domestic drama"}
    ).json()
    project_path = tmp_path / "projects" / project["id"]
    inputs = project_path / "outputs" / "input"
    state_dir = project_path / "outputs" / "state"
    inputs.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    inputs.joinpath("prompt.md").write_text(prompt_text, encoding="utf-8")
    inputs.joinpath("brief.json").write_text(json.dumps({
        "title": "The Empty Chair Beside Her",
        "genre": "domestic drama",
        "audience": "women ages 30-50",
        "language": "American English",
    }), encoding="utf-8")
    inputs.joinpath("foundation.json").write_text(json.dumps({
        "title": "The Empty Chair Beside Her",
        "premise": "Claire leaves an unrepentant husband and builds a stable home.",
        "characters": [{
            "id": "claire_bennett",
            "name": "Claire Bennett",
            "role": "protagonist",
            "age": 30,
            "physical_description": "A practical woman in late pregnancy.",
            "external_goal": "Build a dependable home for her daughter.",
            "strength": "Documents the truth and sets boundaries.",
        }],
        "plot_threads": [{
            "id": "main",
            "name": "independence",
            "description": "Claire replaces family appearance with reliable care.",
            "type": "main",
        }],
        "setting": {
            "time_period": "August 2027 through June 2028",
            "primary_location": "Linden Falls, a fictional city centered on Willow Creek",
        },
    }), encoding="utf-8")
    state_dir.joinpath("story_state.json").write_text(json.dumps({
        "metadata": {
            "genre": "domestic drama",
            "language": "American English",
            "audience": "women ages 30-50",
        },
        "characters": {"claire_bennett": {
            "id": "claire_bennett",
            "full_name": "Claire Bennett",
            "role": "protagonist",
            "age": 30,
            "arc_stage": "resolution",
            "current_location": "Two-bedroom apartment near Little Harbor",
            "emotional_state": "hurt but self-directed",
        }},
        "story_bible": {"setting": {
            "time_period": "August 2027 through June 2028",
            "primary_location": "Linden Falls, a fictional city centered on Willow Creek",
        }},
    }), encoding="utf-8")

    source_paths = (
        inputs / "prompt.md",
        inputs / "brief.json",
        inputs / "foundation.json",
        state_dir / "story_state.json",
    )
    source_hashes = {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths
    }

    facts_response = client.get(
        f"/api/projects/{project['id']}/covers/story-facts"
    )
    assert facts_response.status_code == 200
    facts = facts_response.json()
    assert facts["pending_fields"] == []
    facts_character = facts["brief"]["principal_characters"][0]
    assert facts_character["occupation_and_status"] == expected_identity
    assert expected_source_ref in facts_character["source_refs"]

    response = client.post(
        f"/api/projects/{project['id']}/covers/directions",
        json={"count": 4},
    )

    assert response.status_code == 201
    direction_character = response.json()["brief"]["principal_characters"][0]
    assert direction_character["age"] == 30
    assert direction_character["occupation_and_status"] == facts_character[
        "occupation_and_status"
    ]
    assert expected_source_ref in direction_character["source_refs"]
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths
    } == source_hashes
    assert image_client.calls == 0


def test_cover_story_facts_api_persists_only_missing_fields_with_revision_check(
    tmp_path, monkeypatch,
) -> None:
    client, _image_client = _client(tmp_path, monkeypatch)
    project = client.post(
        "/api/projects", json={"title": "Pending Cover Facts", "genre": "Domestic drama"}
    ).json()
    project_path = tmp_path / "projects" / project["id"]
    _write_pending_cover_sources(project_path)

    before_files = {
        path.relative_to(project_path).as_posix()
        for path in project_path.rglob("*")
        if path.is_file()
    }
    pending = client.get(
        f"/api/projects/{project['id']}/covers/story-facts"
    ).json()

    assert pending["pending_fields"] == [{
        "field": "principal_characters[0].age",
        "character_id": "claire_bennett",
        "character_name": "Claire Bennett",
        "label": "Claire Bennett age range",
        "proposed_value": "derive from approved story facts",
    }]
    assert pending["brief"]["principal_characters"][0][
        "occupation_and_status"
    ] == "Independent interior designer returning to practice."
    empty = client.put(
        f"/api/projects/{project['id']}/covers/story-facts",
        json={
            "expected_revision_sha256": pending["revision_sha256"],
            "characters": [{
                "character_id": "claire_bennett",
                "age_band": "",
            }],
        },
    )
    assert empty.status_code == 400

    saved_response = client.put(
        f"/api/projects/{project['id']}/covers/story-facts",
        json={
            "expected_revision_sha256": pending["revision_sha256"],
            "characters": [{
                "character_id": "claire_bennett",
                "age_band": "early thirties",
            }],
        },
    )

    assert saved_response.status_code == 200
    saved = saved_response.json()
    assert saved["pending_fields"] == []
    assert saved["revision_sha256"] != pending["revision_sha256"]
    assert saved["brief"]["source_prompt_sha256"] != pending["brief"][
        "source_prompt_sha256"
    ]
    assert saved["brief"]["principal_characters"][0]["age_band"] == "early thirties"
    after_files = {
        path.relative_to(project_path).as_posix()
        for path in project_path.rglob("*")
        if path.is_file()
    }
    assert after_files - before_files == {
        "outputs/covers/.story-facts.lock",
        "outputs/covers/story-facts.json",
    }
    sidecar = json.loads(
        (project_path / "outputs/covers/story-facts.json").read_text(encoding="utf-8")
    )
    assert sidecar["provenance"] == "explicit_user_confirmation"
    assert sidecar["source_prompt_sha256"] == pending["brief"]["source_prompt_sha256"]
    assert len(sidecar["source_revision_sha256"]) == 64

    stale = client.put(
        f"/api/projects/{project['id']}/covers/story-facts",
        json={
            "expected_revision_sha256": pending["revision_sha256"],
            "characters": [{
                "character_id": "claire_bennett",
                "age_band": "mid thirties",
            }],
        },
    )
    assert stale.status_code == 409
    unknown = client.put(
        f"/api/projects/{project['id']}/covers/story-facts",
        json={
            "expected_revision_sha256": saved["revision_sha256"],
            "characters": [{
                "character_id": "unknown_character",
                "age_band": "mid thirties",
            }],
        },
    )
    assert unknown.status_code == 400

    already_confirmed = client.put(
        f"/api/projects/{project['id']}/covers/story-facts",
        json={
            "expected_revision_sha256": saved["revision_sha256"],
            "characters": [{
                "character_id": "claire_bennett",
                "age_band": "mid thirties",
            }],
        },
    )
    assert already_confirmed.status_code == 400


def test_cover_story_facts_merge_separate_confirmations(tmp_path, monkeypatch) -> None:
    client, _image_client = _client(tmp_path, monkeypatch)
    project = client.post(
        "/api/projects", json={"title": "Partial Cover Facts", "genre": "Domestic drama"}
    ).json()
    project_path = tmp_path / "projects" / project["id"]
    _write_pending_cover_sources(project_path)
    foundation_path = project_path / "outputs/input/foundation.json"
    foundation = json.loads(foundation_path.read_text(encoding="utf-8"))
    foundation["setting"] = {"time_period": "August 2027 through June 2028"}
    foundation_path.write_text(json.dumps(foundation), encoding="utf-8")
    initial_response = client.get(
        f"/api/projects/{project['id']}/covers/story-facts"
    )
    assert initial_response.status_code == 200, initial_response.text
    initial = initial_response.json()
    assert {item["field"] for item in initial["pending_fields"]} == {
        "principal_characters[0].age",
        "lived_environment.primary_spaces",
    }
    age_saved = client.put(
        f"/api/projects/{project['id']}/covers/story-facts",
        json={
            "expected_revision_sha256": initial["revision_sha256"],
            "characters": [{
                "character_id": "claire_bennett",
                "age_band": "early thirties",
            }],
        },
    ).json()

    completed_response = client.put(
        f"/api/projects/{project['id']}/covers/story-facts",
        json={
            "expected_revision_sha256": age_saved["revision_sha256"],
            "primary_spaces": ["Claire's rented cottage", "Willow Creek studio"],
        },
    )

    assert completed_response.status_code == 200
    completed = completed_response.json()
    assert completed["pending_fields"] == []
    assert completed["brief"]["principal_characters"][0]["age_band"] == "early thirties"
    assert completed["brief"]["lived_environment"]["primary_spaces"] == [
        "Claire's rented cottage",
        "Willow Creek studio",
    ]


def test_cover_story_facts_same_revision_concurrent_put_has_one_winner(
    tmp_path, monkeypatch,
) -> None:
    from core.cover_store import CoverStore

    client, _image_client = _client(tmp_path, monkeypatch)
    project = client.post(
        "/api/projects", json={"title": "Concurrent Cover Facts", "genre": "Drama"}
    ).json()
    project_path = tmp_path / "projects" / project["id"]
    _write_pending_cover_sources(project_path)
    foundation_path = project_path / "outputs/input/foundation.json"
    foundation = json.loads(foundation_path.read_text(encoding="utf-8"))
    foundation["setting"] = {"time_period": "August 2027 through June 2028"}
    foundation_path.write_text(json.dumps(foundation), encoding="utf-8")
    initial = client.get(
        f"/api/projects/{project['id']}/covers/story-facts"
    ).json()

    first_write_entered = threading.Event()
    release_first_write = threading.Event()
    original_write = CoverStore._write_json

    def delayed_first_write(path, payload):
        if path.name == "story-facts.json" and not first_write_entered.is_set():
            first_write_entered.set()
            assert release_first_write.wait(timeout=3)
        return original_write(path, payload)

    monkeypatch.setattr(CoverStore, "_write_json", staticmethod(delayed_first_write))
    endpoint = f"/api/projects/{project['id']}/covers/story-facts"
    age_request = {
        "expected_revision_sha256": initial["revision_sha256"],
        "characters": [{
            "character_id": "claire_bennett",
            "age_band": "early thirties",
        }],
    }
    spaces_request = {
        "expected_revision_sha256": initial["revision_sha256"],
        "primary_spaces": ["Claire's rented cottage"],
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(client.put, endpoint, json=age_request)
        assert first_write_entered.wait(timeout=3)
        second = executor.submit(client.put, endpoint, json=spaces_request)
        with pytest.raises(FuturesTimeoutError):
            second.result(timeout=0.2)
        release_first_write.set()
        responses = [first.result(timeout=3), second.result(timeout=3)]

    assert sorted(response.status_code for response in responses) == [200, 409]
    saved = json.loads(
        (project_path / "outputs/covers/story-facts.json").read_text(encoding="utf-8")
    )
    assert saved["characters"] == [{
        "character_id": "claire_bennett",
        "age_band": "early thirties",
    }]
    assert saved["primary_spaces"] == []


@pytest.mark.parametrize(
    "relative_source",
    ["outputs/input/prompt.md", "outputs/input/foundation.json"],
)
def test_cover_story_facts_require_reconfirmation_when_story_source_changes(
    tmp_path, monkeypatch, relative_source,
) -> None:
    client, _image_client = _client(tmp_path, monkeypatch)
    project = client.post(
        "/api/projects", json={"title": "Changing Cover Facts", "genre": "Domestic drama"}
    ).json()
    project_path = tmp_path / "projects" / project["id"]
    _write_pending_cover_sources(project_path)
    initial = client.get(
        f"/api/projects/{project['id']}/covers/story-facts"
    ).json()
    saved = client.put(
        f"/api/projects/{project['id']}/covers/story-facts",
        json={
            "expected_revision_sha256": initial["revision_sha256"],
            "characters": [{
                "character_id": "claire_bennett",
                "age_band": "early thirties",
            }],
        },
    ).json()
    source = project_path / relative_source
    source.write_text(
        source.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    current = client.get(
        f"/api/projects/{project['id']}/covers/story-facts"
    ).json()

    assert current["revision_sha256"] != saved["revision_sha256"]
    assert current["brief"]["principal_characters"][0]["age_band"] == ""
    assert [item["field"] for item in current["pending_fields"]] == [
        "principal_characters[0].age"
    ]
    stale = client.put(
        f"/api/projects/{project['id']}/covers/story-facts",
        json={
            "expected_revision_sha256": saved["revision_sha256"],
            "characters": [{
                "character_id": "claire_bennett",
                "age_band": "early thirties",
            }],
        },
    )
    assert stale.status_code == 409


def test_cover_story_facts_retries_when_source_changes_during_resolution(
    tmp_path, monkeypatch,
) -> None:
    from core import cover_handoff

    client, _image_client = _client(tmp_path, monkeypatch)
    project = client.post(
        "/api/projects", json={"title": "Moving Cover Source", "genre": "Drama"}
    ).json()
    project_path = tmp_path / "projects" / project["id"]
    _write_pending_cover_sources(project_path)
    original_resolve = cover_handoff._resolve_cover_brief_v2_source
    calls = 0

    def resolve_then_change(project_root, prompt_text):
        nonlocal calls
        result = original_resolve(project_root, prompt_text)
        calls += 1
        if calls == 1:
            foundation_path = project_path / "outputs/input/foundation.json"
            foundation = json.loads(foundation_path.read_text(encoding="utf-8"))
            foundation["characters"][0]["name"] = "Claire Morgan"
            foundation_path.write_text(json.dumps(foundation), encoding="utf-8")
        return result

    monkeypatch.setattr(
        cover_handoff, "_resolve_cover_brief_v2_source", resolve_then_change,
    )

    response = client.get(f"/api/projects/{project['id']}/covers/story-facts")

    assert response.status_code == 200
    assert calls == 2
    assert response.json()["brief"]["principal_characters"][0]["name"] == "Claire Morgan"


def test_cover_story_facts_rejects_source_change_before_commit(
    tmp_path, monkeypatch,
) -> None:
    from core import cover_story_facts

    client, _image_client = _client(tmp_path, monkeypatch)
    project = client.post(
        "/api/projects", json={"title": "Commit Race", "genre": "Drama"}
    ).json()
    project_path = tmp_path / "projects" / project["id"]
    _write_pending_cover_sources(project_path)
    initial = client.get(
        f"/api/projects/{project['id']}/covers/story-facts"
    ).json()
    original_load = cover_story_facts._load
    changed = False

    def load_then_change(path):
        nonlocal changed
        result = original_load(path)
        if not changed:
            changed = True
            state_path = project_path / "outputs/state/story_state.json"
            state_path.write_text(
                state_path.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
        return result

    monkeypatch.setattr(cover_story_facts, "_load", load_then_change)
    response = client.put(
        f"/api/projects/{project['id']}/covers/story-facts",
        json={
            "expected_revision_sha256": initial["revision_sha256"],
            "characters": [{
                "character_id": "claire_bennett",
                "age_band": "early thirties",
            }],
        },
    )

    assert response.status_code == 409
    assert not (project_path / "outputs/covers/story-facts.json").exists()


def test_cover_story_facts_state_character_change_invalidates_confirmation(
    tmp_path, monkeypatch,
) -> None:
    client, _image_client = _client(tmp_path, monkeypatch)
    project = client.post(
        "/api/projects", json={"title": "Changed Protagonist", "genre": "Drama"}
    ).json()
    project_path = tmp_path / "projects" / project["id"]
    _write_pending_cover_sources(project_path)
    foundation_path = project_path / "outputs/input/foundation.json"
    foundation = json.loads(foundation_path.read_text(encoding="utf-8"))
    foundation["characters"] = []
    foundation_path.write_text(json.dumps(foundation), encoding="utf-8")
    state_path = project_path / "outputs/state/story_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["characters"]["claire_bennett"].update({
        "role": "protagonist",
        "physical_description": "A practical woman rebuilding after separation.",
        "external_goal": "Rebuild her independent practice.",
        "strength": "Documents the truth and sets boundaries.",
    })
    state_path.write_text(json.dumps(state), encoding="utf-8")
    initial_response = client.get(
        f"/api/projects/{project['id']}/covers/story-facts"
    )
    assert initial_response.status_code == 200, initial_response.text
    initial = initial_response.json()
    saved = client.put(
        f"/api/projects/{project['id']}/covers/story-facts",
        json={
            "expected_revision_sha256": initial["revision_sha256"],
            "characters": [{
                "character_id": "claire_bennett",
                "age_band": "early thirties",
            }],
        },
    )
    assert saved.status_code == 200

    state["characters"] = {"ivy_morgan": {
        "id": "ivy_morgan",
        "full_name": "Ivy Morgan",
        "role": "protagonist",
        "physical_description": "A composed woman in practical work clothes.",
        "external_goal": "Build a dependable home.",
        "strength": "Keeps careful records.",
    }}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    current_response = client.get(
        f"/api/projects/{project['id']}/covers/story-facts"
    )

    assert current_response.status_code == 200
    current = current_response.json()
    assert current["brief"]["principal_characters"][0]["character_id"] == "ivy_morgan"
    assert current["brief"]["principal_characters"][0]["age_band"] == ""
    assert "principal_characters[0].age" in {
        item["field"] for item in current["pending_fields"]
    }


def test_confirmed_story_facts_flow_through_direction_approval_and_generation(
    tmp_path, monkeypatch,
) -> None:
    client, image_client = _client(
        tmp_path, monkeypatch, director=CanonAwareFixtureDirector(),
    )
    project = client.post(
        "/api/projects", json={"title": "Confirmed Cover Facts", "genre": "Domestic drama"}
    ).json()
    project_path = tmp_path / "projects" / project["id"]
    _write_pending_cover_sources(project_path)
    pending = client.get(
        f"/api/projects/{project['id']}/covers/story-facts"
    ).json()
    saved = client.put(
        f"/api/projects/{project['id']}/covers/story-facts",
        json={
            "expected_revision_sha256": pending["revision_sha256"],
            "characters": [{
                "character_id": "claire_bennett",
                "age_band": "early thirties",
            }],
        },
    ).json()
    assert saved["pending_fields"] == []

    created_response = client.post(
        f"/api/projects/{project['id']}/covers/directions", json={"count": 4},
    )
    assert created_response.status_code == 201
    created = created_response.json()
    assert created["brief_sha256"] == saved["brief"]["source_prompt_sha256"]
    approved_response = client.post(
        f"/api/projects/{project['id']}/covers/directions/{created['direction_id']}/approve",
        json={
            "expected_brief_sha256": created["brief_sha256"],
            "approved_direction_sha256": created["direction_sha256"],
        },
    )
    assert approved_response.status_code == 200
    approved = approved_response.json()
    generated = client.post(
        f"/api/projects/{project['id']}/covers/generate",
        json={
            "direction_id": approved["direction_id"],
            "approved_direction_sha256": approved["direction_sha256"],
        },
    )

    assert generated.status_code == 202
    assert _wait(client, generated.json()["job_id"])["status"] == "done"
    assert image_client.calls == 4


def test_v2_generation_requires_an_approved_direction_before_image_call(tmp_path, monkeypatch) -> None:
    client, image_client = _client(tmp_path, monkeypatch)
    project = client.post("/api/projects", json={"title": "Direction Gate", "genre": "Drama"}).json()
    created = client.post(
        f"/api/projects/{project['id']}/covers/directions",
        json={"brief": two_character_fixture(), "direction": director_fixture()},
    ).json()

    response = client.post(
        f"/api/projects/{project['id']}/covers/generate",
        json={"brief": two_character_fixture(), "direction_id": created["direction_id"]},
    )

    assert response.status_code == 409
    assert "approved" in response.json()["detail"]
    assert image_client.calls == 0


def test_changed_v2_brief_marks_approved_direction_stale_before_image_call(
    tmp_path, monkeypatch,
) -> None:
    client, image_client = _client(tmp_path, monkeypatch)
    project = client.post("/api/projects", json={"title": "Direction Drift", "genre": "Drama"}).json()
    created = client.post(
        f"/api/projects/{project['id']}/covers/directions",
        json={"brief": two_character_fixture(), "direction": director_fixture()},
    ).json()
    approved = client.post(
        f"/api/projects/{project['id']}/covers/directions/{created['direction_id']}/approve",
        json={
            "expected_brief_sha256": created["brief_sha256"],
            "approved_direction_sha256": created["direction_sha256"],
        },
    ).json()

    response = client.post(
        f"/api/projects/{project['id']}/covers/generate",
        json={
            "brief": approved["brief"],
            "source_prompt_sha256": "c" * 64,
            "direction_id": approved["direction_id"],
            "approved_direction_sha256": approved["direction_sha256"],
        },
    )

    assert response.status_code == 409
    assert "stale" in response.json()["detail"]
    directions = client.get(f"/api/projects/{project['id']}/covers/directions").json()
    assert directions[0]["status"] == "stale"
    assert image_client.calls == 0


def test_current_project_prompt_overrides_old_client_brief_for_stale_detection(
    tmp_path, monkeypatch,
) -> None:
    client, image_client = _client(tmp_path, monkeypatch)
    project = client.post("/api/projects", json={"title": "Current Prompt Gate", "genre": "Drama"}).json()
    created = client.post(
        f"/api/projects/{project['id']}/covers/directions",
        json={"brief": two_character_fixture(), "direction": director_fixture()},
    ).json()
    approved = client.post(
        f"/api/projects/{project['id']}/covers/directions/{created['direction_id']}/approve",
        json={
            "expected_brief_sha256": created["brief_sha256"],
            "approved_direction_sha256": created["direction_sha256"],
        },
    ).json()
    changed = two_character_fixture()
    changed["core_conflict"] = "The current story now turns on a different family rupture."
    prompt = tmp_path / "projects" / project["id"] / "outputs" / "input" / "prompt.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(
        "COVER_HANDOFF_BEGIN\n```json\n"
        + json.dumps(changed, ensure_ascii=False)
        + "\n```\nCOVER_HANDOFF_END\n",
        encoding="utf-8",
    )

    response = client.post(
        f"/api/projects/{project['id']}/covers/generate",
        json={
            "brief": approved["brief"],
            "source_prompt_sha256": approved["brief_sha256"],
            "direction_id": approved["direction_id"],
            "approved_direction_sha256": approved["direction_sha256"],
        },
    )

    assert response.status_code == 409
    assert "stale" in response.json()["detail"]
    assert image_client.calls == 0


def test_approved_v2_direction_generates_four_independent_image2_candidates(
    tmp_path, monkeypatch,
) -> None:
    client, image_client = _client(tmp_path, monkeypatch)
    project = client.post("/api/projects", json={"title": "Direction Generate", "genre": "Drama"}).json()
    created = client.post(
        f"/api/projects/{project['id']}/covers/directions",
        json={"brief": two_character_fixture(), "direction": director_fixture()},
    ).json()
    approved = client.post(
        f"/api/projects/{project['id']}/covers/directions/{created['direction_id']}/approve",
        json={
            "expected_brief_sha256": created["brief_sha256"],
            "approved_direction_sha256": created["direction_sha256"],
        },
    ).json()

    response = client.post(
        f"/api/projects/{project['id']}/covers/generate",
        json={
            "brief": approved["brief"],
            "source_prompt_sha256": approved["brief_sha256"],
            "direction_id": approved["direction_id"],
            "approved_direction_sha256": approved["direction_sha256"],
        },
    )

    assert response.status_code == 202
    assert _wait(client, response.json()["job_id"])["status"] == "done"
    assert image_client.calls == 4
    cover_set = client.get(f"/api/projects/{project['id']}/covers").json()[0]
    assert cover_set["brief_schema_version"] == 2
    assert cover_set["compiler_version"] == "cover-compiler.v14"
    assert cover_set["brief"]["principal_characters"][0]["age"] == 34
    assert [item["model"] for item in cover_set["candidates"]] == ["gpt-image-2"] * 4
    assert all(item["safe_request_parameters"]["n"] == 1 for item in cover_set["candidates"])
    assert all(len(item["attempt_history"]) == 1 for item in cover_set["candidates"])


def test_quality_route_is_advisory_and_invalid_repair_code_stops_before_image_call(
    tmp_path, monkeypatch,
) -> None:
    client, image_client = _client(tmp_path, monkeypatch)
    project = client.post("/api/projects", json={"title": "Quality API", "genre": "Drama"}).json()
    job_id = client.post(
        f"/api/projects/{project['id']}/covers/generate",
        json={"brief": _brief(), "concepts": _concepts()},
    ).json()["job_id"]
    _wait(client, job_id)
    cover_set = client.get(f"/api/projects/{project['id']}/covers").json()[0]
    candidate = cover_set["candidates"][0]

    quality = client.get(
        f"/api/projects/{project['id']}/covers/{cover_set['cover_set_id']}/quality"
    )
    assert quality.status_code == 200
    assert quality.json()["reports"][0]["status"] == "human_review_required"
    assert quality.json()["reports"][0]["attempt_count"] == 1

    retry = client.post(
        f"/api/projects/{project['id']}/covers/{cover_set['cover_set_id']}"
        f"/candidates/{candidate['candidate_id']}/retry",
        json={"expected_revision": cover_set["revision"], "repair_codes": ["generic_ai_face"]},
    )
    assert retry.status_code == 400
    assert "reported" in retry.json()["detail"]
    assert image_client.calls == 4
