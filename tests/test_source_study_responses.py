"""Model response transport must not turn a completed reading into another call."""

import json
import threading

import pytest
from test_source_library import bundle, response
from test_workshop_api import client  # noqa: F401

from core import source_study as ss
from core.creative_types import digest
from core.reference_assets import atomic_json
from core.source_library import SourceLibrary


def study_for(tmp_path, *, large=False):
    library = SourceLibrary(tmp_path)
    book = library.import_files(bundle(large=large))
    study = ss.SourceStudy(library, book["id"])
    study.prepare({"model": "fixture"})
    return study


def no_model(*_):
    pytest.fail("A complete saved response must not trigger another model call")


def save_legacy_failed_response(study, raw):
    """Persist the exact pre-fix checkpoint shape, including request identity."""
    state = study.status()
    payload = {
        "phase": "reading",
        "scope": "provided_free_chapters_only",
        "segments": ss.batches(study.library, study.source_id)[0],
        "response_contract": ss.Analysis.model_json_schema(),
    }
    atomic_json(study.root / "0000.json", {
        "request_sha256": digest([state["binding"], ss.SYSTEM, json.dumps(payload, ensure_ascii=False)]),
        "raw": raw, "response_sha256": digest(raw), "created_at": ss.now(),
    })
    state.update(status="failed", current=1, error="来源阅读第 1 段返回格式无效", finished_at=ss.now())
    state.pop("progress", None)
    atomic_json(study.root / "state.json", state)


@pytest.mark.parametrize("wrap", [
    lambda value: value,
    lambda value: "```json\n" + value + "\n```",
    lambda value: "\n ```JSON\r\n" + value + "\r\n``` \n",
    lambda value: "```\n" + value + "\n```",
])
def test_complete_response_envelopes_preserve_raw_and_validate(tmp_path, wrap):
    study = study_for(tmp_path)
    raw = wrap(response())
    result = study.run(lambda *_: raw)
    assert result["analysis"] == json.loads(response())
    saved = json.loads((study.root / "0000.json").read_text())
    assert saved["raw"] == raw and saved["response_sha256"] == digest(raw)
    assert study.result() == result


def test_old_fenced_failure_recovers_without_resending(tmp_path):
    study = study_for(tmp_path)
    save_legacy_failed_response(study, "```json\n" + response() + "\n```")
    checkpoint = (study.root / "0000.json").read_bytes()
    study.prepare({"model": "fixture"})
    assert (study.root / "0000.json").is_file(), "Valid cached analysis was discarded"
    result = study.run(no_model)
    assert study.result() == result
    assert (study.root / "0000.json").read_bytes() == checkpoint
    assert not list(study.root.glob("failed-*.json"))
    assert study.status()["status"] == "complete"


@pytest.mark.parametrize("replacement", ["not JSON", "```json\n" + response() + "\n```"])
def test_corrupt_checkpoint_cannot_be_archived_as_a_retryable_format_error(tmp_path, replacement):
    study = study_for(tmp_path)
    save_legacy_failed_response(study, response())
    path = study.root / "0000.json"
    record = json.loads(path.read_text())
    record["raw"] = replacement  # Leave the original response hash intact.
    atomic_json(path, record)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="检查点校验失败"):
        study.prepare({"model": "fixture"})
    assert path.read_bytes() == before
    assert not list(study.root.glob("failed-*.json"))


def test_cached_wrapper_does_not_bypass_request_identity(tmp_path):
    study = study_for(tmp_path)
    save_legacy_failed_response(study, "```json\n" + response() + "\n```")
    path = study.root / "0000.json"
    record = json.loads(path.read_text())
    record["request_sha256"] = "wrong-request"
    atomic_json(path, record)
    study.prepare({"model": "fixture"})
    with pytest.raises(ValueError, match="检查点身份不匹配"):
        study.run(no_model)
    assert study.status()["status"] == "failed"


@pytest.mark.parametrize("raw", [
    "Here is the analysis:\n```json\n" + response() + "\n```",
    "```json\n" + response() + "\n```\nDone.",
    "```json\n" + response() + "\n```\n```json\n{}\n```",
    "```json\n" + response(),
    "```python\n" + response() + "\n```",
    response() + "\n{}",
    response()[:-1],
    response().replace('"story_summary":', '"story_summary":"conflict", "story_summary":', 1),
    response().replace('"visual_notes": []', '"visual_notes": [NaN]'),
    response().replace('"visual_notes": []', '"visual_notes": "wrong type"'),
    response().replace('"visual_notes": []', '"unexpected": []'),
    "[]",
])
def test_malformed_or_ambiguous_outputs_remain_failed(tmp_path, raw):
    study = study_for(tmp_path)
    calls = []
    def complete(*_):
        calls.append(1)
        return raw
    with pytest.raises(ValueError, match="格式无效"):
        study.run(complete)
    assert len(calls) == 1
    assert study.status()["status"] == "failed"
    assert study.status()["completed"] == 0
    assert not (study.root / "result.json").exists()
    assert json.loads((study.root / "0000.json").read_text())["raw"] == raw


def test_fenced_readings_and_synthesis_use_same_boundary(tmp_path):
    study = study_for(tmp_path, large=True)
    phases = []
    def complete(system, user):
        phases.append(json.loads(user)["phase"])
        return "```json\n" + response() + "\n```"
    result = study.run(complete)
    assert "reading" in phases and "synthesis" in phases
    assert len(phases) == study.status()["total"]
    assert study.result() == result


def test_resume_api_reuses_saved_fenced_analysis(
    client,  # noqa: F811 -- shared pytest fixture
    monkeypatch,
    tmp_path,
):
    from api import source_library
    from api.jobs import JobRunner
    from core import llm_client, provider_settings

    library = SourceLibrary(tmp_path / "references")
    book = library.import_files(bundle())
    client.app.dependency_overrides[source_library.get_store] = lambda: library
    route = {
        "provider": "fixture", "model": "fake", "base_url": "", "api_key": "",
        "max_tokens": 8192, "timeout_seconds": 900, "reasoning_effort": "",
        "connection_id": None,
    }
    monkeypatch.setattr(provider_settings, "resolve_text_route", lambda _: route)
    monkeypatch.setattr(source_library, "runner", JobRunner())
    calls = []

    class Model:
        def __init__(self, **_):
            pass

        def complete(self, *_):
            calls.append(1)
            raise AssertionError("Recovery must reuse the saved response")

    monkeypatch.setattr(llm_client, "LLMClient", Model)
    study = ss.SourceStudy(library, book["id"])
    binding = {k: v for k, v in route.items() if k not in {"base_url", "api_key"}}
    binding["endpoint_sha256"] = digest("")
    study.prepare(binding)
    save_legacy_failed_response(study, "```json\n" + response() + "\n```")
    original = (study.root / "0000.json").read_bytes()
    url = "/api/source-books/" + book["id"]
    started = client.post(url + "/analyze")
    assert started.status_code == 202, started.text
    for _ in range(100):
        detail = client.get(url).json()
        if not detail["study"]["worker_active"]:
            break
        threading.Event().wait(0.01)
    assert detail["study"]["status"] == "complete", detail
    assert detail["result"]["analysis"] == json.loads(response())
    assert calls == []
    assert (study.root / "0000.json").read_bytes() == original


def test_envelope_removal_does_not_rewrite_json_string_values(tmp_path):
    study = study_for(tmp_path)
    expected = json.loads(response())
    expected["story_summary"] = '原文有 ```json、反斜杠 \\、引号 " 和换行\n，不能替换。'
    raw = "```json\n" + json.dumps(expected, ensure_ascii=False) + "\n```"
    result = study.run(lambda *_: raw)
    assert result["analysis"] == expected
