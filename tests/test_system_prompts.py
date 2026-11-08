from concurrent.futures import ThreadPoolExecutor

import pytest

from core.system_prompts import SystemPromptError, SystemPromptStore
from api import workshop_titles
from test_workshop_api import client, ready  # noqa: F401
from test_workshop_titles import suggest, CANDIDATES, ROUTE


def test_versions_idempotency_conflict_and_workspace_isolation(tmp_path):
    store = SystemPromptStore(tmp_path / "one")
    initial = store.get()
    assert initial["current"]["version"] == 1
    assert not store.path.exists()  # Opening the editor does not create a mutation.
    original = store.snapshot()
    saved = store.save("# Name\nPrioritize the impossible choice.", 0, "first-request")
    assert saved["current"]["version"] == 2 and saved["revision"] == 1
    assert store.save(saved["current"]["markdown"], 0, "first-request") == saved
    assert store.snapshot()["sha256"] != original["sha256"]
    assert SystemPromptStore(tmp_path / "two").snapshot() == original
    with pytest.raises(SystemPromptError) as exc:
        store.save("stale change", 0, "second-request")
    assert exc.value.status == 409 and store.get() == saved
    restored = store.save(original["markdown"], 1, "restore-request")
    assert restored["current"]["version"] == 3
    assert restored["versions"][1]["markdown"] == saved["current"]["markdown"]


def test_concurrent_saves_do_not_lose_versions(tmp_path):
    def save(n):
        try:
            return SystemPromptStore(tmp_path).save(f"prompt {n}", 0, f"operation-{n}")["revision"]
        except SystemPromptError as exc:
            return exc.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [1, 2]))
    assert sorted(results) == [1, 409]
    assert len(SystemPromptStore(tmp_path).get()["versions"]) == 2


def test_used_default_remains_frozen_after_resource_upgrade(tmp_path, monkeypatch):
    import core.system_prompts as prompts
    store = SystemPromptStore(tmp_path / "workspace")
    original = store.snapshot()
    replacement = tmp_path / "new-default.md"
    replacement.write_text("Upgraded default")
    monkeypatch.setattr(prompts, "DEFAULT_PATH", replacement)
    assert store.snapshot() == original
    assert store.get()["default_markdown"] == "Upgraded default"
    assert store.get()["current"]["markdown"] == original["markdown"]


def test_api_validation_recovery_and_no_automatic_model_call(client, monkeypatch):
    monkeypatch.setattr(workshop_titles, "complete_titles", lambda *_: pytest.fail("editing must not call model"))
    url = "/api/system-prompts/title-design"
    initial = client.get(url).json()
    assert client.put(url, json={"markdown": " ", "revision": 0, "operation_id": "empty-001"}).status_code == 422
    assert client.put(url, json={"markdown": "中文" * 2200, "revision": 0, "operation_id": "large-001"}).status_code == 422
    assert client.put(url, json={"markdown": "新要求", "revision": 0, "operation_id": "save-0001"}).status_code == 200
    assert client.put(url, json={"markdown": "覆盖", "revision": 0, "operation_id": "save-0002"}).status_code == 409
    assert client.get(url).json()["versions"][0]["markdown"] == initial["current"]["markdown"]


def test_queued_request_uses_frozen_prompt_and_does_not_inject_genre_documents(client, monkeypatch, tmp_path):
    data = ready(client, monkeypatch, tmp_path)
    url = "/api/system-prompts/title-design"
    client.put(url, json={"markdown": "书名：突出人物选择。", "revision": 0, "operation_id": "before-call"})
    calls = []
    def complete(snapshot, rules):
        # Editing the shared prompt after the request was queued cannot affect this call.
        client.put(url, json={"markdown": "书名：突出危险反差。", "revision": 1, "operation_id": "during-call"})
        calls.append((snapshot, rules))
        return CANDIDATES, ROUTE
    monkeypatch.setattr(workshop_titles, "complete_titles", complete)
    result = suggest(client, data, "Keep it in English")
    assert calls[0][1] == "书名：突出人物选择。"
    saved = result["naming"]["requests"][-1]
    assert saved["system_prompt"]["version"] == 2
    assert saved["system_prompt"]["markdown"] == calls[0][1]
    assert "creative_types" not in saved["title_context"]["context"]
    assert saved["title_context"]["source_sha256"] == result["naming"]["source_sha256"]
    assert client.get(url).json()["current"]["version"] == 3
