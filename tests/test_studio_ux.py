"""Tests for Studio LLM settings + richer project summaries."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from api.main import create_app


def _client(tmp_path, monkeypatch):
    for key in (
        "NOVEL_OS_COVER_API_KEY",
        "NOVEL_OS_API_KEY",
        "OPENAI_API_KEY",
        "NOVEL_OS_COVER_BASE_URL",
        "NOVEL_OS_BASE_URL",
        "NOVEL_OS_COVER_MODEL",
        "NOVEL_OS_COVER_FORMAT",
        "NOVEL_OS_COVER_DIRECTOR_PROVIDER",
        "NOVEL_OS_COVER_DIRECTOR_MODEL",
        "NOVEL_OS_COVER_DIRECTOR_BASE_URL",
        "NOVEL_OS_COVER_DIRECTOR_API_KEY",
        "NOVEL_OS_COVER_DIRECTOR_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = tmp_path / "studio_settings.json"
    monkeypatch.setenv("NOVEL_OS_SETTINGS_PATH", str(settings))
    projects = tmp_path / "projects"
    projects.mkdir()
    app = create_app(projects_root=projects, db_url=f"sqlite:///{tmp_path / 't.db'}")
    return TestClient(app), settings


def test_llm_status_endpoint(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    body = client.get("/api/studio/llm").json()
    assert "configured" in body
    assert "presets" in body
    assert {p["id"] for p in body["presets"]} >= {"quality", "fast", "local", "mature"}


def test_llm_put_preset_persists(tmp_path, monkeypatch):
    client, settings = _client(tmp_path, monkeypatch)
    resp = client.put("/api/studio/llm", json={"preset": "local", "onboarding_completed": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["preset"] == "local"
    assert body["onboarding_completed"] is True
    saved = json.loads(settings.read_text(encoding="utf-8"))
    assert saved["preset"] == "local"
    assert saved["NOVEL_OS_LLM_PROVIDER"] == "ollama"
    assert saved["onboarding_completed"] is True


def test_cover_status_and_put_keep_key_write_only(tmp_path, monkeypatch):
    client, settings = _client(tmp_path, monkeypatch)

    initial = client.get("/api/studio/cover")
    assert initial.status_code == 200
    assert initial.json()["model"] == "gpt-image-2"
    assert initial.json()["size"] == "2048x3072"
    assert initial.json()["has_api_key"] is False

    saved_response = client.put("/api/studio/cover", json={
        "base_url": "https://sub2api.example/v1/",
        "api_key": "cover-secret",
        "count": 5,
        "timeout_seconds": 240,
    })
    assert saved_response.status_code == 200
    body = saved_response.json()
    assert body["configured"] is True
    assert body["has_api_key"] is True
    assert body["base_url"] == "https://sub2api.example/v1"
    assert body["count"] == 5
    assert "api_key" not in body
    assert "cover-secret" not in saved_response.text

    persisted = json.loads(settings.read_text(encoding="utf-8"))
    assert persisted["NOVEL_OS_COVER_API_KEY"] == "cover-secret"
    assert persisted["NOVEL_OS_COVER_BASE_URL"] == "https://sub2api.example/v1/"


def test_cover_put_persists_independent_director_settings_without_echoing_key(
    tmp_path, monkeypatch,
):
    client, settings = _client(tmp_path, monkeypatch)

    response = client.put("/api/studio/cover", json={
        "director_provider": "openai_compatible",
        "director_model": "director-model",
        "director_base_url": "https://director.example/v1/",
        "director_api_key": "director-secret",
        "director_timeout_seconds": 240,
    })

    assert response.status_code == 200
    body = response.json()
    assert body["director_model"] == "director-model"
    assert body["director_has_api_key"] is True
    assert body["director_inherits_writing"] is False
    assert "director_api_key" not in body
    assert "director-secret" not in response.text
    persisted = json.loads(settings.read_text(encoding="utf-8"))
    assert persisted["NOVEL_OS_COVER_DIRECTOR_API_KEY"] == "director-secret"


def test_cover_put_accepts_provider_native_portrait_size(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    response = client.put("/api/studio/cover", json={"size": "1024x1536"})

    assert response.status_code == 200
    assert response.json()["size"] == "1024x1536"


def test_cover_put_rejects_non_portrait_ratio(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    response = client.put("/api/studio/cover", json={"size": "1024x1024"})

    assert response.status_code == 400
    assert "2:3" in response.json()["detail"]


def test_cover_put_accepts_configured_image_model(tmp_path, monkeypatch):
    client, settings = _client(tmp_path, monkeypatch)

    response = client.put("/api/studio/cover", json={"model": "publisher/image-v3"})

    assert response.status_code == 200
    assert response.json()["model"] == "publisher/image-v3"
    assert json.loads(settings.read_text(encoding="utf-8"))["NOVEL_OS_COVER_MODEL"] == "publisher/image-v3"


def test_legacy_webp_settings_keep_independent_endpoint_during_save(
    tmp_path, monkeypatch,
):
    client, settings = _client(tmp_path, monkeypatch)
    settings.write_text(json.dumps({
        "NOVEL_OS_COVER_BASE_URL": "https://images.example/v1",
        "NOVEL_OS_COVER_API_KEY": "cover-secret",
        "NOVEL_OS_COVER_FORMAT": "webp",
    }), encoding="utf-8")

    initial = client.get("/api/studio/cover").json()
    response = client.put("/api/studio/cover", json={
        "base_url": initial["base_url"],
        "model": initial["model"],
        "size": initial["size"],
        "quality": initial["quality"],
        "output_format": initial["output_format"],
        "count": initial["count"],
        "timeout_seconds": initial["timeout_seconds"],
    })

    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert response.json()["output_format"] == "jpeg"
    persisted = json.loads(settings.read_text(encoding="utf-8"))
    assert persisted["NOVEL_OS_COVER_BASE_URL"] == "https://images.example/v1"
    assert persisted["NOVEL_OS_COVER_API_KEY"] == "cover-secret"
    assert persisted["NOVEL_OS_COVER_FORMAT"] == "jpeg"


def test_project_summary_includes_words_and_rating(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    created = client.post("/api/projects", json={
        "title": "The Last Signal", "genre": "Romance", "author": "Ada",
    }).json()
    assert created["author"] == "Ada"
    assert created["content_rating"] == "general"
    assert "word_count" in created

    items = client.get("/api/projects").json()
    assert len(items) == 1
    assert items[0]["title"] == "The Last Signal"

    detail = client.patch(f"/api/projects/{created['id']}", json={"content_rating": "mature"}).json()
    assert detail["content_rating"] == "mature"

    again = client.get("/api/projects").json()[0]
    assert again["content_rating"] == "mature"
