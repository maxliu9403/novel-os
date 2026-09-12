import pytest

from test_api import _client, _seed_project


@pytest.fixture
def client(tmp_path):
    _seed_project(tmp_path, "p", "Book", "Drama", chapters={"1": {
        "number": 1, "title": "One", "status": "drafted", "word_count": 10,
    }})
    return _client(tmp_path)


def test_method_policy_is_read_only_until_explicit_cas_save(client):
    response = client.get("/api/projects/p/method-policy")
    assert response.status_code == 200
    initial = response.json()
    assert initial["data"]["policy"]["mode"] == "advisory"
    saved = client.put("/api/projects/p/method-policy", json={
        "expected_revision": initial["sha256"], "policy": {"mode": "off"},
    })
    assert saved.status_code == 200
    assert saved.json()["data"]["policy"]["mode"] == "off"
    stale = client.put("/api/projects/p/method-policy", json={
        "expected_revision": initial["sha256"], "policy": {"mode": "advisory"},
    })
    assert stale.status_code == 409


def test_p1_api_rejects_active_generation_and_has_no_repair_route(client):
    result = client.put("/api/projects/p/method-policy", json={
        "expected_revision": "", "policy": {"mode": "enforced"},
    })
    assert result.status_code == 400
    assert client.post("/api/projects/p/chapters/1/method-repair", json={}).status_code == 404


def test_method_reports_do_not_invent_results_and_check_project_and_chapter(client):
    response = client.get("/api/projects/p/chapters/1/method-reviews")
    assert response.status_code == 200
    assert response.json()["reports"] == []
    assert client.get("/api/projects/missing/method-policy").status_code == 404
    assert client.get("/api/projects/p/chapters/2/method-reviews").status_code == 404


def test_review_job_keeps_writes_available_and_serves_verified_historical_source(tmp_path, monkeypatch):
    import json
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from artifacts import ArtifactStore
    from model_router import ModelRouter
    from project_identity import ensure_project_instance_id
    from narrative_methods import MethodPolicy
    from narrative_methods.runtime import MethodReviews
    from test_narrative_methods import FakeReviewer

    _seed_project(tmp_path, "p", "Book", "Drama", chapters={"1": {"number": 1, "title": "One", "status": "drafted"}})
    client = _client(tmp_path)
    project = tmp_path / 'p'
    ensure_project_instance_id(project)
    source = '🌿 Mara closed the door.'
    revision = ArtifactStore(project).put_text(chapter=1, kind='final', text=source, source='test')
    entered, release = threading.Event(), threading.Event()
    class Slow(FakeReviewer):
        def complete(self, system, user):
            entered.set()
            assert release.wait(5)
            return super().complete(system, user)
    reviewer = Slow()
    MethodReviews(project, client_factory=lambda: reviewer).prepare('run1', MethodPolicy(), language='English', free_trial_end=3)
    monkeypatch.setattr(ModelRouter, 'client_from_snapshot', staticmethod(lambda snapshot: reviewer))
    body = {'run_id': 'run1', 'revision_id': revision.revision_id}
    response = client.post('/api/projects/p/chapters/1/method-review', json=body)
    assert response.status_code == 202
    assert entered.wait(2)
    try:
        with ThreadPoolExecutor() as pool:
            # This route uses the same ordinary mutation guard as manuscript saves.
            saved = pool.submit(client.put, '/api/projects/p/method-policy', json={
                'expected_revision': '', 'policy': {'mode': 'off'},
            }).result(timeout=2)
            assert saved.status_code == 200
            deleted = pool.submit(client.delete, '/api/projects/p?confirm_title=Book').result(timeout=2)
            assert deleted.status_code == 409
    finally:
        release.set()
    for _ in range(100):
        job = client.get('/api/jobs/' + response.json()['job_id']).json()
        if job['status'] != 'running':
            break
        time.sleep(.01)
    assert job['status'] == 'done', job
    reports = client.get('/api/projects/p/chapters/1/method-reviews').json()['reports']
    assert len(reports) == 1 and reports[0]['status'] == 'valid'
    result = client.get('/api/projects/p/method-reviews/' + reports[0]['report_id'] + '/source')
    assert result.status_code == 200 and result.json()['text'] == source
    assert result.json()['sha256'] == revision.sha256
    assert reviewer.calls == 1
    assert not (project / 'outputs/manuscript/chapter_001_final.md').exists()
