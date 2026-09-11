"""Create project with multi-genre + optional premise."""

from fastapi.testclient import TestClient

from api.main import create_app
from core.narrative_format import infer_narrative_format
from core.novel_classification import NovelClassification


def _client(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    return TestClient(create_app(
        projects_root=projects,
        db_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}",
    ))


def test_create_with_genres_and_premise(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/projects", json={
        "title": "Saltlight",
        "author": "Ada",
        "genres": ["Romance", "Fantasy"],
        "premise": "A cartographer maps a city that redraws itself every dawn.",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["genre"] == "Romance · Fantasy"
    assert body["genres"] == ["Romance", "Fantasy"]
    assert body["classification"]["primary_genre_id"] == "romance"
    assert body["classification"]["secondary_genre_ids"] == ["fantasy"]
    assert "cartographer" in body["premise"]

    detail = client.get(f"/api/projects/{body['id']}").json()
    assert detail["genres"] == ["Romance", "Fantasy"]
    assert detail["classification"]["filter_type_ids"][:2] == [
        "romance", "fantasy",
    ]
    assert "cartographer" in detail["premise"]

    patched = client.patch(f"/api/projects/{body['id']}", json={
        "genres": ["Romance", "Fantasy", "Mystery"],
        "premise": "Updated brief.",
    }).json()
    assert patched["genre"] == "Romance · Fantasy · Mystery"
    assert patched["premise"] == "Updated brief."


def test_create_legacy_genre_string_still_works(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/projects", json={
        "title": "Old Path", "genre": "Thriller", "author": "Bea",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["genre"] == "Thriller"
    assert "Thriller" in body["genres"]


def test_classification_catalog_and_explicit_project_contract(tmp_path):
    client = _client(tmp_path)
    catalog = client.get("/api/novel-classification/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["catalog_version"] == "novel-types.2026-09"
    format_catalog = client.get("/api/narrative-format/catalog")
    assert format_catalog.status_code == 200
    assert format_catalog.json()["decision_policy"]["engine_suitability_is_advisory"] is True

    classification_payload = {
        "primary_genre_id": "womens_fiction",
        "secondary_genre_ids": ["family_drama"],
        "story_type_ids": ["second_chance", "revenge"],
        "tone_ids": ["angst"],
        "setting_ids": ["contemporary_urban"],
        "audience": {"channel": "female", "age_band": "midlife"},
        "length": {"form": "long", "chapter_band": "chapters_51_100"},
    }
    narrative_format = infer_narrative_format(
        NovelClassification.from_dict(classification_payload),
        chapters=80,
        target_words=72000,
        explicit_length=True,
    ).with_confirmation("author_selected")

    response = client.post("/api/projects", json={
        "title": "Her Second Door",
        "genre": "Women's Fiction",
        "classification": classification_payload,
        "narrative_format": narrative_format.to_dict(),
    })
    assert response.status_code == 201, response.text
    value = response.json()["classification"]
    assert value["source"] == "author_confirmed"
    assert value["confidence"] == 1.0
    assert value["filter_type_ids"][:4] == [
        "womens_fiction", "family_drama", "second_chance", "revenge",
    ]
    assert response.json()["narrative_format"]["mode"] == "multi_volume"
    assert response.json()["narrative_format"]["volume_count"] == 4
    assert response.json()["narrative_format"]["confirmation_status"] == "confirmed"

    invalid = client.patch(
        f"/api/projects/{response.json()['id']}",
        json={"classification": {
            "primary_genre_id": "womens_fiction",
            "story_type_ids": ["model_invented_type"],
        }},
    )
    assert invalid.status_code == 400
