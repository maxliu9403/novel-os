"""Observe a blocked source request without sending or repeating model work."""

import threading
from concurrent.futures import ThreadPoolExecutor

from test_source_library import bundle, response
from test_workshop_api import client  # noqa: F401

from core.source_library import SourceLibrary
from core.source_study import SourceStudy


def test_waiting_request_has_durable_start_and_coverage_before_model_returns(tmp_path):
    library = SourceLibrary(tmp_path)
    book = library.import_files(bundle())
    study = SourceStudy(library, book["id"])
    study.prepare({"model": "fixture", "reasoning_effort": "max"})
    entered, release = threading.Event(), threading.Event()

    def complete(*_):
        entered.set()
        assert release.wait(3)
        return response()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(study.run, complete)
        try:
            assert entered.wait(2)
            waiting = SourceStudy(library, book["id"]).status()
            assert waiting.get("started_at"), "No start time while source model waits"
            assert waiting.get("progress", {}).get("request_started_at")
            assert waiting["progress"]["source_files"] == 4
            assert waiting["progress"]["input_bytes"] > 0
            assert waiting["progress"]["received_chars"] == 0
            assert waiting["completed"] == 0
        finally:
            release.set()
            future.result(timeout=3)
    assert study.status()["finished_at"]


def test_observations_keep_heartbeat_transport_and_content_distinct(tmp_path):
    from core.source_study_progress import StudyProgress, read_progress

    progress = StudyProgress(tmp_path, "attempt", heartbeat_seconds=0.02)
    with progress:
        progress.request(
            index=0, payload={"segments": [{"path": "synopsis.md"}]}, input_bytes=100
        )
        progress.observe({"event": "request.start", "request_id": "request-1"})
        first = read_progress(tmp_path, "attempt")["heartbeat_at"]
        threading.Event().wait(0.06)
        observed = read_progress(tmp_path, "attempt")
        assert observed["heartbeat_at"] != first
        assert observed["received_chars"] == 0 and observed["last_content_at"] is None
        progress.observe(
            {"event": "stdin_sent", "request_id": "request-1", "prompt": "PRIVATE"}
        )
        progress.observe(
            {
                "event": "content.progress",
                "request_id": "stale-request",
                "model_output_chars": 999,
            }
        )
        assert read_progress(tmp_path, "attempt")["received_chars"] == 0
        progress.observe(
            {
                "event": "content.progress",
                "request_id": "request-1",
                "model_output_chars": 42,
                "text": "PRIVATE",
            }
        )
        progress.update()  # flush the throttled observation
        observed = read_progress(tmp_path, "attempt")
        assert observed["received_chars"] == 42 and observed["last_content_at"]
        assert "PRIVATE" not in (tmp_path / "progress.json").read_text()
        progress.observe(
            {
                "event": "request.completed",
                "request_id": "request-1",
                "output_chars": 100,
            }
        )
        before = read_progress(tmp_path, "attempt")["received_chars"]
        progress.observe(
            {
                "event": "content.progress",
                "request_id": "request-1",
                "model_output_chars": 999,
            }
        )
        assert read_progress(tmp_path, "attempt")["received_chars"] == before
        progress.request(index=1, payload={"readings": []}, input_bytes=200)
        assert read_progress(tmp_path, "attempt")["received_chars"] == 0
        assert not read_progress(tmp_path, "old-attempt")
    assert read_progress(tmp_path, "attempt")["phase"] == "finished"


def test_source_api_exposes_worker_liveness_and_persists_diagnostics(
    client,  # noqa: F811 -- shared pytest fixture
    monkeypatch,
):
    from api import source_library
    from api.jobs import JobRunner
    from core import llm_client, provider_settings

    entered, release = threading.Event(), threading.Event()
    model_calls = []
    route = dict(
        provider="fixture",
        model="fake",
        base_url="",
        api_key="",
        max_tokens=8192,
        timeout_seconds=900,
        reasoning_effort="max",
    )
    monkeypatch.setattr(provider_settings, "resolve_text_route", lambda _: route)
    monkeypatch.setattr(source_library, "runner", JobRunner())

    class Model:
        def __init__(self, **kwargs):
            self.observe = kwargs["diagnostic_callback"]

        def complete(self, *_):
            model_calls.append(1)
            self.observe({"event": "request.start", "request_id": "api-test"})
            self.observe({"event": "stdin_sent", "request_id": "api-test"})
            entered.set()
            assert release.wait(3)
            return response()

    monkeypatch.setattr(llm_client, "LLMClient", Model)
    book = client.post(
        "/api/source-books",
        files=[
            ("files", (p, b, "application/octet-stream")) for p, b in bundle().items()
        ],
    ).json()
    url = "/api/source-books/" + book["id"]
    try:
        assert client.post(url + "/analyze").status_code == 202
        assert entered.wait(2)
        running = client.get(url).json()["study"]
        assert running["worker_active"] and running["checked_at"]
        assert running["progress"]["input_sent_at"]
        assert running["progress"]["received_chars"] == 0
        assert client.post(url + "/analyze").status_code == 202
        assert len(model_calls) == 1
        paused = client.post(url + "/cancel").json()
        assert paused["cancel_requested"] and paused["worker_active"]
    finally:
        release.set()
    for _ in range(100):
        if not client.get(url).json()["study"]["worker_active"]:
            break
        threading.Event().wait(0.01)
    assert not client.get(url).json()["study"]["worker_active"]
