from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time

import pytest

from core import provider_settings, studio_settings


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_OS_SETTINGS_PATH", str(tmp_path / "studio_settings.json"))
    monkeypatch.delenv("NOVEL_OS_WORKSHOP_TIMEOUT_SECONDS", raising=False)
    for key in studio_settings._ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield tmp_path


def _connection(**overrides):
    payload = {
        "name": "Primary models",
        "provider": "openai_compatible",
        "auth_type": "api_key",
        "base_url": "https://models.example/v1/",
        "image_base_url": "",
        "capabilities": ["text_generation", "image_generation"],
        "secret_action": "replace",
        "api_key": "provider-secret",
    }
    payload.update(overrides)
    return provider_settings.save_connection(payload)


def test_connection_secret_is_stored_separately_and_redacted(isolated_settings) -> None:
    connection = _connection()

    settings_text = studio_settings.settings_path().read_text(encoding="utf-8")
    secrets_path = isolated_settings / "studio_settings.secrets.json"
    secrets_text = secrets_path.read_text(encoding="utf-8")

    assert connection["has_api_key"] is True
    assert "provider-secret" not in settings_text
    assert "provider-secret" not in repr(connection)
    assert "provider-secret" in secrets_text
    assert stat.S_IMODE(secrets_path.stat().st_mode) == 0o600


def test_saved_key_can_be_explicitly_cleared() -> None:
    connection = _connection()

    updated = provider_settings.save_connection({
        "name": connection["name"],
        "provider": connection["provider"],
        "auth_type": connection["auth_type"],
        "base_url": connection["base_url"],
        "capabilities": connection["capabilities"],
        "secret_action": "clear",
    }, connection["id"])

    assert updated["has_api_key"] is False
    assert updated["status"] == "invalid"
    assert "API key" in updated["error"]


def test_one_connection_drives_text_and_image_routes() -> None:
    connection = _connection()

    routes = provider_settings.save_text_routes([
        {
            "id": "default",
            "connection_id": connection["id"],
            "model": "story-model",
            "max_tokens": 12000,
            "inherits_default": False,
        }
    ])
    profile = provider_settings.save_image_profile("cover", {
        "connection_id": connection["id"],
        "model": "gpt-image-2",
        "size": "1024x1536",
        "quality": "high",
        "output_format": "jpeg",
        "count": 4,
        "timeout_seconds": 240,
    })

    writer = provider_settings.resolve_text_route("writer")
    image = provider_settings.resolve_image_profile("cover")

    assert next(item for item in routes if item["id"] == "writer")["effective_source"] == "default"
    assert writer["model"] == "story-model"
    assert writer["api_key"] == "provider-secret"
    assert image["connection_id"] == connection["id"]
    assert image["api_key"] == "provider-secret"
    assert profile["configured"] is True


def test_image_profile_allows_a_provider_model_id() -> None:
    connection = _connection()

    profile = provider_settings.save_image_profile("cover", {
        "connection_id": connection["id"],
        "model": "publisher/image-v3",
        "size": "2048x3072",
        "quality": "medium",
        "output_format": "png",
        "count": 3,
        "timeout_seconds": 180,
    })

    assert profile["model"] == "publisher/image-v3"


def test_codex_login_can_back_text_and_image_profiles(monkeypatch) -> None:
    monkeypatch.setattr(provider_settings.shutil, "which", lambda name: "/usr/local/bin/codex")
    connection = provider_settings.save_connection({
        "name": "Codex subscription",
        "provider": "codex",
        "auth_type": "codex_session",
        "capabilities": ["text_generation", "image_generation"],
    })
    provider_settings.save_text_routes([{
        "id": "default",
        "connection_id": connection["id"],
        "model": "",
        "inherits_default": False,
    }])
    provider_settings.save_image_profile("cover", {
        "connection_id": connection["id"],
        "model": "gpt-image-2",
        "size": "2048x3072",
        "quality": "high",
        "output_format": "jpeg",
        "count": 4,
        "timeout_seconds": 180,
    })

    text = provider_settings.resolve_text_route("writer")
    cover_director = provider_settings.resolve_text_route("cover_director")
    cover_director_status = next(
        route
        for route in provider_settings.text_routes_status()
        if route["id"] == "cover_director"
    )
    image = studio_settings.resolve_cover_settings()

    assert text["provider"] == "codex"
    assert text["api_key"] == ""
    assert cover_director["reasoning_effort"] == "medium"
    assert cover_director_status["effective_reasoning_effort"] == "medium"
    assert image.provider == "codex"
    assert image.api_key == ""
    assert studio_settings.cover_status()["configured"] is True


def test_codex_default_route_clears_stale_http_environment(monkeypatch) -> None:
    monkeypatch.setattr(provider_settings.shutil, "which", lambda name: "/usr/local/bin/codex")
    monkeypatch.setenv("NOVEL_OS_MODEL", "stale-model")
    monkeypatch.setenv("NOVEL_OS_BASE_URL", "https://stale.example/v1")
    monkeypatch.setenv("NOVEL_OS_API_KEY", "stale-secret")
    connection = provider_settings.save_connection({
        "name": "Codex subscription",
        "provider": "codex",
        "capabilities": ["text_generation", "image_generation"],
    })

    provider_settings.save_text_routes([{
        "id": "default",
        "connection_id": connection["id"],
        "model": "",
        "inherits_default": False,
    }])

    assert os.environ["NOVEL_OS_LLM_PROVIDER"] == "codex"
    assert "NOVEL_OS_MODEL" not in os.environ
    assert "NOVEL_OS_BASE_URL" not in os.environ
    assert "NOVEL_OS_API_KEY" not in os.environ


def test_provider_rejects_capabilities_outside_its_template() -> None:
    with pytest.raises(provider_settings.ProviderSettingsError, match="capabilities"):
        _connection(
            provider="anthropic",
            capabilities=["text_generation", "image_generation"],
        )


def test_provider_rejects_authorization_type_override() -> None:
    with pytest.raises(provider_settings.ProviderSettingsError, match="authorization type"):
        _connection(
            provider="openai",
            auth_type="none",
            capabilities=["text_generation"],
        )


def test_non_key_provider_rejects_a_secret(monkeypatch) -> None:
    monkeypatch.setattr(provider_settings.shutil, "which", lambda name: "/usr/local/bin/codex")

    with pytest.raises(provider_settings.ProviderSettingsError, match="does not accept"):
        provider_settings.save_connection({
            "name": "Codex subscription",
            "provider": "codex",
            "capabilities": ["text_generation"],
            "secret_action": "replace",
            "api_key": "must-not-be-stored",
        })


def test_text_route_rejects_connection_without_required_key() -> None:
    connection = provider_settings.save_connection({
        "name": "Incomplete gateway",
        "provider": "openai_compatible",
        "base_url": "https://models.example/v1",
        "capabilities": ["text_generation"],
    })
    provider_settings.save_text_routes([{
        "id": "default",
        "connection_id": connection["id"],
        "model": "story-model",
        "inherits_default": False,
    }])

    with pytest.raises(provider_settings.ProviderSettingsError, match="API key"):
        provider_settings.resolve_text_route("writer")


def test_image_profile_rejects_zero_count() -> None:
    connection = _connection()

    with pytest.raises(ValueError, match="between 3 and 5"):
        provider_settings.save_image_profile("cover", {
            "connection_id": connection["id"],
            "model": "publisher/image-v3",
            "size": "2048x3072",
            "quality": "medium",
            "output_format": "png",
            "count": 0,
            "timeout_seconds": 180,
        })


def test_connection_in_use_cannot_be_deleted() -> None:
    connection = _connection()
    provider_settings.save_text_routes([
        {
            "id": "default",
            "connection_id": connection["id"],
            "model": "story-model",
            "inherits_default": False,
        }
    ])

    with pytest.raises(provider_settings.ProviderSettingsError, match="still used"):
        provider_settings.delete_connection(connection["id"])


def test_model_router_uses_v2_role_override(monkeypatch) -> None:
    from core import model_router

    captured = {}

    class RecordingClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(model_router, "LLMClient", RecordingClient)
    connection = _connection()
    provider_settings.save_text_routes([
        {
            "id": "default",
            "connection_id": connection["id"],
            "model": "default-story",
            "inherits_default": False,
        },
        {
            "id": "editor",
            "connection_id": connection["id"],
            "model": "editor-specialist",
            "max_tokens": 4096,
            "reasoning_effort": "high",
            "inherits_default": False,
        },
    ])

    model_router.ModelRouter().client_for("editor")

    assert captured == {
        "provider": "openai_compatible",
        "model": "editor-specialist",
        "base_url": "https://models.example/v1",
        "api_key": "provider-secret",
        "max_tokens": 4096,
        "reasoning_effort": "high",
    }


def test_inherited_cover_route_can_override_only_reasoning_effort(monkeypatch) -> None:
    monkeypatch.setattr(provider_settings.shutil, "which", lambda _name: "/usr/local/bin/codex")
    connection = provider_settings.save_connection({
        "name": "Codex subscription",
        "provider": "codex",
        "auth_type": "codex_session",
        "capabilities": ["text_generation", "image_generation"],
    })
    provider_settings.save_text_routes([
        {
            "id": "default",
            "connection_id": connection["id"],
            "model": "gpt-5.6-sol",
            "reasoning_effort": "ultra",
            "inherits_default": False,
        },
        {
            "id": "cover_director",
            "reasoning_effort": "medium",
            "inherits_default": True,
        },
    ])

    route = provider_settings.resolve_text_route("cover_director")
    status = next(
        item
        for item in provider_settings.text_routes_status()
        if item["id"] == "cover_director"
    )

    assert route["connection_id"] == connection["id"]
    assert route["model"] == "gpt-5.6-sol"
    assert route["reasoning_effort"] == "medium"
    assert status["reasoning_effort"] == "medium"
    assert status["effective_reasoning_effort"] == "medium"


def test_legacy_settings_project_into_shared_connection() -> None:
    studio_settings.save_settings({
        "NOVEL_OS_LLM_PROVIDER": "openai_compatible",
        "NOVEL_OS_MODEL": "legacy-story",
        "NOVEL_OS_BASE_URL": "https://legacy.example/v1",
        "NOVEL_OS_API_KEY": "legacy-secret",
        "NOVEL_OS_COVER_MODEL": "gpt-image-2",
    })

    configuration = provider_settings.configuration_status()

    assert configuration["source"] == "legacy"
    assert len(configuration["connections"]) == 1
    assert configuration["image_profiles"]["cover"]["connection_id"] == "legacy-writing"
    assert "legacy-secret" not in json.dumps(configuration)


def test_first_v2_mutation_moves_legacy_secret_out_of_metadata() -> None:
    studio_settings.save_settings({
        "NOVEL_OS_LLM_PROVIDER": "openai_compatible",
        "NOVEL_OS_MODEL": "legacy-story",
        "NOVEL_OS_BASE_URL": "https://legacy.example/v1",
        "NOVEL_OS_API_KEY": "legacy-secret",
    })

    provider_settings.save_connection({
        "name": "Migrated gateway",
        "provider": "openai_compatible",
        "auth_type": "api_key",
        "base_url": "https://legacy.example/v1",
        "capabilities": ["text_generation", "image_generation"],
        "secret_action": "keep",
    }, "legacy-writing")

    metadata = studio_settings.settings_path().read_text(encoding="utf-8")
    secrets = studio_settings.settings_path().with_name(
        "studio_settings.secrets.json"
    ).read_text(encoding="utf-8")
    assert "legacy-secret" not in metadata
    assert "legacy-secret" in secrets
    assert provider_settings.resolve_text_route("default")["api_key"] == "legacy-secret"


def test_legacy_cover_director_override_becomes_an_explicit_route() -> None:
    studio_settings.save_settings({
        "NOVEL_OS_LLM_PROVIDER": "openai_compatible",
        "NOVEL_OS_MODEL": "story-model",
        "NOVEL_OS_BASE_URL": "https://writing.example/v1",
        "NOVEL_OS_API_KEY": "writing-secret",
        "NOVEL_OS_COVER_DIRECTOR_MODEL": "director-model",
    })

    configuration = provider_settings.configuration_status()
    director = next(
        route for route in configuration["text_routes"]
        if route["id"] == "cover_director"
    )

    assert director["inherits_default"] is False
    assert director["effective_model"] == "director-model"
    assert director["effective_connection_id"] == "legacy-writing"


def test_legacy_image_endpoint_can_reuse_writing_secret() -> None:
    studio_settings.save_settings({
        "NOVEL_OS_LLM_PROVIDER": "openai_compatible",
        "NOVEL_OS_MODEL": "story-model",
        "NOVEL_OS_BASE_URL": "https://writing.example/v1",
        "NOVEL_OS_API_KEY": "shared-secret",
        "NOVEL_OS_COVER_BASE_URL": "https://images.example/v1",
    })

    image = provider_settings.resolve_image_profile("cover")

    assert image["base_url"] == "https://images.example/v1"
    assert image["api_key"] == "shared-secret"


def test_v2_api_configures_shared_text_and_image_connection(tmp_path) -> None:
    from fastapi.testclient import TestClient
    from api.main import create_app

    client = TestClient(create_app(
        projects_root=tmp_path / "projects",
        db_url=f"sqlite:///{tmp_path / 'api.db'}",
        media_root=tmp_path / "media",
    ))
    created = client.post("/api/studio/providers", json={
        "name": "Shared gateway",
        "provider": "openai_compatible",
        "auth_type": "api_key",
        "base_url": "https://gateway.example/v1",
        "capabilities": ["text_generation", "image_generation"],
        "secret_action": "replace",
        "api_key": "api-route-secret",
    })

    assert created.status_code == 201
    connection_id = created.json()["id"]
    assert created.json()["has_api_key"] is True
    assert "api-route-secret" not in created.text

    routes = client.put("/api/studio/model-routes", json={"routes": [{
        "id": "default",
        "connection_id": connection_id,
        "model": "story-model",
        "max_tokens": 8192,
        "inherits_default": False,
    }]})
    image = client.put("/api/studio/image-profiles/cover", json={
        "connection_id": connection_id,
        "model": "gpt-image-2",
        "size": "2048x3072",
        "quality": "high",
        "output_format": "jpeg",
        "count": 4,
        "timeout_seconds": 180,
    })
    status = client.get("/api/studio/models")

    assert routes.status_code == 200
    assert image.status_code == 200
    assert status.status_code == 200
    assert status.json()["image_profiles"]["cover"]["configured"] is True
    assert "api-route-secret" not in status.text


def _wait_for_job(client, job_id: str) -> dict:
    for _ in range(100):
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["status"] != "running":
            return payload
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_text_route_response_test_returns_model_reply(monkeypatch, tmp_path) -> None:
    from fastapi.testclient import TestClient
    from api.main import create_app
    from core import llm_client

    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def complete(self, system: str, prompt: str) -> str:
            captured["prompt"] = prompt
            return "The model connection is working."

    monkeypatch.setattr(llm_client, "LLMClient", FakeClient)
    connection = _connection()
    provider_settings.save_text_routes([{
        "id": "default",
        "connection_id": connection["id"],
        "model": "story-model",
        "inherits_default": False,
    }])
    client = TestClient(create_app(
        projects_root=tmp_path / "projects",
        db_url=f"sqlite:///{tmp_path / 'text-test.db'}",
        media_root=tmp_path / "media",
    ))

    started = client.post(
        "/api/studio/model-routes/default/test",
        json={"prompt": "Confirm the connection."},
    )
    completed = _wait_for_job(client, started.json()["job_id"])

    assert started.status_code == 202
    assert completed["status"] == "done"
    assert completed["meta"]["reply"] == "The model connection is working."
    assert captured["provider"] == "openai_compatible"
    assert captured["reasoning_effort"] == "low"


def test_image_profile_test_returns_immediately_and_deduplicates(
    monkeypatch,
    tmp_path,
) -> None:
    from fastapi.testclient import TestClient
    from api.main import create_app
    from core import image_client

    release = threading.Event()
    calls = []

    class FakeImageClient:
        def generate(self, prompt: str):
            calls.append(prompt)
            assert release.wait(timeout=2)
            return image_client.GeneratedImage(
                data=b"image-bytes",
                content_type="image/png",
                width=1024,
                height=1536,
                request_id="image-test-request",
                model="gpt-image-2",
            )

    monkeypatch.setattr(
        image_client,
        "build_image_generation_client",
        lambda settings: FakeImageClient(),
    )
    connection = _connection()
    provider_settings.save_image_profile("cover", {
        "connection_id": connection["id"],
        "model": "gpt-image-2",
        "size": "1024x1536",
        "quality": "high",
        "output_format": "png",
        "count": 4,
        "timeout_seconds": 180,
    })
    client = TestClient(create_app(
        projects_root=tmp_path / "projects",
        db_url=f"sqlite:///{tmp_path / 'image-test.db'}",
        media_root=tmp_path / "media",
    ))

    first = client.post("/api/studio/image-profiles/cover/test")
    second = client.post("/api/studio/image-profiles/cover/test")

    assert first.status_code == 202
    assert first.json()["status"] == "running"
    assert second.json()["job_id"] == first.json()["job_id"]
    release.set()
    completed = _wait_for_job(client, first.json()["job_id"])
    assert completed["status"] == "done"
    assert completed["meta"]["data_url"].startswith("data:image/png;base64,")
    assert len(calls) == 1


def test_workshop_timeout_defaults_to_fifteen_minutes_and_preserves_legacy_env(monkeypatch):
    status = {row['id']: row for row in provider_settings.text_routes_status()}
    assert status['workshop']['timeout_seconds'] is None
    assert status['workshop']['effective_timeout_seconds'] == 900
    assert all(row['effective_timeout_seconds'] is None for name, row in status.items() if name != 'workshop')
    monkeypatch.setenv('NOVEL_OS_WORKSHOP_TIMEOUT_SECONDS', '600')
    assert provider_settings.workshop_timeout_seconds() == 600
    monkeypatch.setenv('NOVEL_OS_WORKSHOP_TIMEOUT_SECONDS', '30')
    assert provider_settings.workshop_timeout_seconds() == 30
    for invalid in ('nan', 'inf', 'invalid'):
        monkeypatch.setenv('NOVEL_OS_WORKSHOP_TIMEOUT_SECONDS', invalid)
        assert provider_settings.workshop_timeout_seconds() == 900


def test_saved_workshop_timeout_wins_and_survives_model_inheritance_edits(monkeypatch):
    connection = _connection()
    provider_settings.save_text_routes([{'id': 'default', 'connection_id': connection['id'],
        'model': 'writer-model', 'inherits_default': False},
        {'id': 'workshop', 'inherits_default': True, 'timeout_seconds': 1200}])
    monkeypatch.setenv('NOVEL_OS_WORKSHOP_TIMEOUT_SECONDS', '300')
    assert provider_settings.resolve_text_route('workshop')['timeout_seconds'] == 1200
    assert 'timeout_seconds' not in provider_settings.resolve_text_route('writer')
    for payload in ({'id': 'workshop', 'inherits_default': True},
                    {'id': 'workshop', 'inherits_default': True, 'timeout_seconds': None},
                    {'id': 'workshop', 'inherits_default': False, 'connection_id': connection['id'], 'model': 'brainstorm-model'}):
        rows = provider_settings.save_text_routes([payload])
        workshop_route = next(row for row in rows if row['id'] == 'workshop')
        assert workshop_route['timeout_seconds'] == 1200
        assert workshop_route['effective_timeout_seconds'] == 1200
    saved = json.loads(studio_settings.settings_path().read_text())
    assert saved['model_configuration']['text_routes']['workshop']['timeout_seconds'] == 1200
    assert provider_settings.resolve_text_route('workshop')['model'] == 'brainstorm-model'


@pytest.mark.parametrize('route,value', [('workshop', 59), ('workshop', 1801), ('workshop', 600.5), ('writer', 900)])
def test_workshop_timeout_rejects_invalid_or_other_route_values(route, value):
    with pytest.raises(provider_settings.ProviderSettingsError):
        provider_settings.save_text_routes([{'id': route, 'inherits_default': True, 'timeout_seconds': value}])


def test_workshop_timeout_round_trips_through_existing_model_settings_api(tmp_path):
    from fastapi.testclient import TestClient
    from api.main import create_app
    connection = _connection()
    provider_settings.save_text_routes([{'id': 'default', 'connection_id': connection['id'],
        'model': 'writer-model', 'inherits_default': False}])
    client = TestClient(create_app(projects_root=tmp_path / 'projects', db_url=f'sqlite:///{tmp_path / "api.db"}'))
    route = next(row for row in client.get('/api/studio/models').json()['text_routes'] if row['id'] == 'workshop')
    assert route['effective_timeout_seconds'] == 900
    response = client.put('/api/studio/model-routes', json={'routes': [
        {'id': 'workshop', 'inherits_default': True, 'timeout_seconds': 1800}]})
    assert response.status_code == 200, response.text
    route = next(row for row in response.json() if row['id'] == 'workshop')
    assert route['timeout_seconds'] == route['effective_timeout_seconds'] == 1800
    response = client.put('/api/studio/model-routes', json={'routes': [
        {'id': 'workshop', 'inherits_default': True}]})
    assert next(row for row in response.json() if row['id'] == 'workshop')['timeout_seconds'] == 1800
    for value in (0, 59, 1801):
        assert client.put('/api/studio/model-routes', json={'routes': [
            {'id': 'workshop', 'inherits_default': True, 'timeout_seconds': value}]}).status_code == 422


def _codex_catalog_stub(tmp_path, body: str):
    command = tmp_path / 'codex-catalog-stub'
    command.write_text(f'#!{sys.executable}\nimport json, sys, time\n' + body)
    command.chmod(0o700)
    return str(command)


def test_codex_models_reads_real_rpc_shape_and_pagination(tmp_path):
    command = _codex_catalog_stub(tmp_path, '''
assert sys.argv[1:] == ['app-server', '--listen', 'stdio://']
initialized = False
for line in sys.stdin:
    request = json.loads(line)
    method = request['method']
    if method == 'initialize':
        assert request['params']['clientInfo']['name'] == 'novel-os-model-discovery'
        result = {'userAgent': 'codex/test'}
    elif method == 'initialized':
        initialized = True
        continue
    else:
        assert initialized and method == 'model/list'
        assert request['params']['includeHidden'] is False
        if 'cursor' not in request['params']:
            result = {'data': [{'id': 'alias', 'model': 'catalog-model-z'},
                               {'id': 'hidden', 'model': 'secret-model', 'hidden': True}],
                      'nextCursor': 'page-two'}
        else:
            assert request['params']['cursor'] == 'page-two'
            result = {'data': [{'id': 'catalog-model-a'}, {'model': 'catalog-model-z'}], 'nextCursor': None}
    print(json.dumps({'method': 'notification', 'params': {}}), flush=True)
    print(json.dumps({'id': -1, 'result': {'ignored': True}}), flush=True)
    print(json.dumps({'id': request['id'], 'result': result}), flush=True)
''')
    assert provider_settings._codex_models(command, 10) == ['catalog-model-a', 'catalog-model-z']


@pytest.mark.parametrize('response,match', [
    ("{'error': {'message': 'private-auth-config-value'}}", 'rejected'),
    ("{'result': {'data': [], 'nextCursor': 'repeated'}}", 'cursor'),
    ("{'result': {'data': 'wrong'}}", 'invalid model catalog'),
])
def test_codex_models_rejects_errors_bad_catalogs_and_cursor_loops(tmp_path, response, match):
    command = _codex_catalog_stub(tmp_path, f'''
for line in sys.stdin:
    request = json.loads(line)
    if request['method'] == 'initialized':
        continue
    payload = {{'result': {{}}}} if request['method'] == 'initialize' else {response}
    print(json.dumps({{'id': request['id'], **payload}}), flush=True)
''')
    with pytest.raises(provider_settings.ProviderModelDiscoveryError, match=match) as error:
        provider_settings._codex_models(command, 10)
    assert 'private-auth-config-value' not in str(error.value)


def test_codex_model_timeout_reaps_child_process(tmp_path, monkeypatch):
    command = _codex_catalog_stub(tmp_path, 'time.sleep(30)\n')
    actual_popen = subprocess.Popen
    processes = []

    def record_process(*args, **kwargs):
        process = actual_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(provider_settings.subprocess, 'Popen', record_process)
    with pytest.raises(provider_settings.ProviderModelDiscoveryTimeout):
        provider_settings._codex_models(command, 0.1)
    assert len(processes) == 1
    assert processes[0].poll() is not None
    assert processes[0].stdin.closed and processes[0].stdout.closed


def test_codex_cleanup_kills_stubborn_helper_after_parent_exits(tmp_path):
    helper_pid_file = tmp_path / 'helper.pid'
    helper_source = (
        'import os, signal, time; from pathlib import Path; '
        'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
        f'Path({str(helper_pid_file)!r}).write_text(str(os.getpid())); '
        'time.sleep(30)'
    )
    command = _codex_catalog_stub(tmp_path, f'''
import subprocess
from pathlib import Path
subprocess.Popen([sys.executable, '-c', {helper_source!r}])
while not Path({str(helper_pid_file)!r}).exists():
    time.sleep(0.01)
for line in sys.stdin:
    request = json.loads(line)
    if request['method'] == 'initialized':
        continue
    result = {{}} if request['method'] == 'initialize' else {{'data': [], 'nextCursor': None}}
    print(json.dumps({{'id': request['id'], 'result': result}}), flush=True)
''')
    helper_pid = None
    try:
        assert provider_settings._codex_models(command, 10) == []
        helper_pid = int(helper_pid_file.read_text())
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            # A just-killed orphan may await init's reap; a zombie no longer runs.
            status = subprocess.run(['ps', '-o', 'stat=', '-p', str(helper_pid)],
                capture_output=True, text=True, timeout=1).stdout.strip()
            if not status or status.startswith('Z'):
                return
            time.sleep(0.02)
        raise AssertionError('The helper survived parent exit and process-group cleanup')
    finally:
        if helper_pid is None and helper_pid_file.exists():
            helper_pid = int(helper_pid_file.read_text())
        if helper_pid is not None:
            try:
                os.kill(helper_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_text_catalog_ignores_codex_image_feature_gate(monkeypatch):
    monkeypatch.setattr(provider_settings.shutil, 'which', lambda _: '/fake/codex')
    monkeypatch.setattr(provider_settings, '_codex_models', lambda command, timeout: ['available-text-model'])

    def no_feature_probe(*args, **kwargs):
        raise AssertionError('Text model discovery must not test image generation')

    monkeypatch.setattr(provider_settings.subprocess, 'run', no_feature_probe)
    assert provider_settings._discover_models({'provider': 'codex', 'capabilities': [
        'text_generation', 'image_generation']}) == ['available-text-model']


def test_http_catalog_uses_connection_auth_and_deduplicates(monkeypatch):
    from contextlib import closing
    from io import BytesIO
    seen = {}

    def response(request, timeout):
        seen.update(url=request.full_url, authorization=request.get_header('Authorization'), timeout=timeout)
        return closing(BytesIO(json.dumps({'data': [{'id': 'gateway-b'}, {'id': 'gateway-a'},
            {'id': 'gateway-b'}]}).encode()))

    monkeypatch.setattr(provider_settings.urllib.request, 'urlopen', response)
    assert provider_settings._discover_models({'provider': 'openai_compatible',
        'base_url': 'https://models.example/v1'}, api_key='snapshot-key') == ['gateway-a', 'gateway-b']
    assert seen == {'url': 'https://models.example/v1/models', 'authorization': 'Bearer snapshot-key', 'timeout': 12}


def test_refresh_does_not_lock_network_or_overwrite_concurrent_route_saves(monkeypatch):
    connection = _connection()
    provider_settings.save_text_routes([{'id': 'default', 'connection_id': connection['id'],
        'model': 'writer-model', 'inherits_default': False}])
    started = threading.Event()
    release = threading.Event()
    results = []

    def discover(_connection, **kwargs):
        assert kwargs['api_key'] == 'provider-secret'
        started.set()
        assert release.wait(2)
        return ['new-catalog-model']

    monkeypatch.setattr(provider_settings, '_discover_models', discover)
    thread = threading.Thread(target=lambda: results.append(provider_settings.refresh_connection_models(connection['id'])))
    thread.start()
    try:
        assert started.wait(1)
        before_save = time.monotonic()
        provider_settings.save_text_routes([{'id': 'workshop', 'inherits_default': True, 'timeout_seconds': 1200}])
        assert time.monotonic() - before_save < 1
    finally:
        release.set()
        thread.join(2)
    assert results == [['new-catalog-model']]
    configuration = provider_settings.load_configuration()
    assert configuration['text_routes']['workshop']['timeout_seconds'] == 1200
    assert provider_settings._connection(configuration, connection['id'])['discovered_models'] == ['new-catalog-model']


def test_refresh_rejects_catalog_if_connection_changed(monkeypatch):
    connection = _connection()

    def discover(_connection, **kwargs):
        provider_settings.save_connection({**connection, 'base_url': 'https://new.example/v1',
            'secret_action': 'keep'}, connection['id'])
        return ['stale-model']

    monkeypatch.setattr(provider_settings, '_discover_models', discover)
    with pytest.raises(provider_settings.ProviderModelDiscoveryConflict, match='changed'):
        provider_settings.refresh_connection_models(connection['id'])
    current = provider_settings._connection(provider_settings.load_configuration(), connection['id'])
    assert current['base_url'] == 'https://new.example/v1'
    assert current['discovered_models'] == []


def test_model_catalog_api_refreshes_and_keeps_cache_on_errors(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import create_app
    connection = _connection()
    client = TestClient(create_app(projects_root=tmp_path / 'projects', db_url=f'sqlite:///{tmp_path / "models.db"}'))
    endpoint = f'/api/studio/providers/{connection["id"]}/models'
    calls = []

    def discover(*args, **kwargs):
        calls.append(True)
        return ['from-provider']

    monkeypatch.setattr(provider_settings, '_discover_models', discover)
    assert client.get(endpoint).json() == []
    assert calls == []
    response = client.get(endpoint, params={'refresh': True})
    assert response.status_code == 200 and response.json() == ['from-provider']
    assert client.get(endpoint).json() == ['from-provider']
    assert calls == [True]
    for error, expected_status in [
        (provider_settings.ProviderModelDiscoveryTimeout('Catalog timed out'), 504),
        (provider_settings.ProviderModelDiscoveryError('Catalog failed'), 502),
        (provider_settings.ProviderModelDiscoveryConflict('Connection changed'), 409),
        (ValueError('provider-secret must remain private'), 502),
    ]:
        def fail(*args, **kwargs):
            raise error
        monkeypatch.setattr(provider_settings, '_discover_models', fail)
        response = client.get(endpoint, params={'refresh': True})
        assert response.status_code == expected_status
        assert 'provider-secret' not in response.text
        assert client.get(endpoint).json() == ['from-provider']
    assert client.get('/api/studio/providers/missing/models?refresh=true').status_code == 404
