from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from api.cover_service import CoverService
from api import db
from api.main import create_app
from api.media import LocalMediaStore
from api import routes
from api.routes import get_cover_service
from core.cover_models import CoverBrief, CoverConcept
from core.image_client import GeneratedImage, ImageClientError


def _webp(marker: bytes) -> bytes:
    payload = (
        b"VP8X" + (10 + len(marker)).to_bytes(4, "little") + b"\x00\x00\x00\x00"
        + (2047).to_bytes(3, "little") + (3071).to_bytes(3, "little") + marker
    )
    return b"RIFF" + (len(payload) + 4).to_bytes(4, "little") + b"WEBP" + payload


class ImageClient:
    def __init__(self) -> None:
        self.calls = 0
        self.fail_calls: set[int] = set()

    def generate(self, _prompt: str) -> GeneratedImage:
        self.calls += 1
        if self.calls in self.fail_calls:
            raise ImageClientError("temporary image failure", retryable=True)
        return GeneratedImage(
            data=_webp(bytes([self.calls])), content_type="image/webp",
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


def _client(tmp_path, monkeypatch) -> tuple[TestClient, ImageClient]:
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
    return TestClient(app), image_client


def _wait(client: TestClient, job_id: str) -> dict:
    for _ in range(100):
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.01)
    raise AssertionError("cover job did not finish")


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
