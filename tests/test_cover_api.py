from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from api.cover_service import CoverService
from api import db
from api.main import create_app
from api.media import LocalMediaStore
from api import routes
from api.routes import get_cover_art_director, get_cover_service
from core.cover_director import CoverArtDirector
from core.cover_models import CoverBrief, CoverConcept
from core.cover_models_v2 import ArtDirectionSet
from core.image_client import GeneratedImage, ImageClientError
from tests.test_cover_director import director_fixture
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


class CanonAwareFixtureDirector:
    def plan(self, brief, *, count: int) -> ArtDirectionSet:
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


def test_direction_api_plans_from_confirmed_legacy_story_facts(tmp_path, monkeypatch) -> None:
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
    inputs.joinpath("prompt.md").write_text(
        "# Legacy prompt\n\n"
        "```yaml\n"
        "financial_baseline:\n"
        "  claire:\n"
        "    occupation: accounts-payable clerk\n"
        "```\n",
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

    response = client.post(
        f"/api/projects/{project['id']}/covers/directions",
        json={"count": 4},
    )

    assert response.status_code == 201
    assert response.json()["brief"]["principal_characters"][0]["age"] == 30
    assert response.json()["brief"]["principal_characters"][0][
        "occupation_and_status"
    ] == "accounts-payable clerk"
    assert image_client.calls == 0


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
    assert cover_set["compiler_version"] == "cover-compiler.v7"
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
