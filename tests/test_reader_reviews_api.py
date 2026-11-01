"""Persistent API entry, idempotency, deletion fencing, safe read-only routes."""
import json
import time
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.reader_reviews import router
from api.routes import get_service
from api.services import ProjectService
from api.jobs import JobRunner
from api.project_operations import project_operations
from test_reader_experience import project, service, tree


def client_for(tmp_path, monkeypatch):
    root = project(tmp_path)
    review, fake = service(root)
    import api.reader_reviews as routes
    # Resolve the real project's path/tenant before providing the fake adapter.
    monkeypatch.setattr(routes, "runtime", lambda svc, pid: review if svc.project_path(pid) == root else None)
    jobs = JobRunner()
    monkeypatch.setattr(routes, "runner", jobs)
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[get_service] = lambda: ProjectService(tmp_path)
    return TestClient(app), review, fake, jobs


def test_full_api_duplicate_post_no_get_calls(tmp_path, monkeypatch):
    client, review, fake, jobs = client_for(tmp_path, monkeypatch)
    before = tree(review.project)
    assert client.get('/api/projects/book/reader-reviews').json() == {"reviews": []}
    assert tree(review.project) == before
    preview = client.post('/api/projects/book/reader-reviews/preview', json={})
    assert preview.status_code == 200
    assert not fake.requests and tree(review.project) == before
    body = {"options": {}, "idempotency_key": "key", "proposal_sha256": preview.json()["proposal_sha256"]}
    response = client.post('/api/projects/book/reader-reviews', json=body)
    assert response.status_code == 202, response.text
    rid = response.json()["id"]
    for _ in range(100):
        result = client.get(f'/api/projects/book/reader-reviews/{rid}').json()
        if result["status"] == "completed": break
        time.sleep(.01)
    assert result["status"] == "completed"
    calls = len(fake.requests)
    duplicate = client.post('/api/projects/book/reader-reviews', json=body)
    assert duplicate.status_code == 202 and duplicate.json()["id"] == rid
    assert len(fake.requests) == calls
    before = tree(review.project)
    assert client.get(f'/api/projects/book/reader-reviews/{rid}/source?chapter=1').status_code == 200
    assert client.get(f'/api/projects/book/reader-reviews/{rid}/source?chapter=99').status_code == 404
    assert client.get('/api/projects/missing/reader-reviews').status_code == 404
    assert tree(review.project) == before
    assert client.post(f'/api/projects/book/reader-reviews/{rid}/resume', json={}).status_code == 409


def test_job_registration_failure_releases_persistent_worker(tmp_path, monkeypatch):
    client, review, _, jobs = client_for(tmp_path, monkeypatch)
    from api.jobs import ProjectJobBlocked
    def blocked(*a, **kw): raise ProjectJobBlocked("deleting")
    monkeypatch.setattr(jobs, 'submit', blocked)
    preview = client.post('/api/projects/book/reader-reviews/preview', json={}).json()
    response = client.post('/api/projects/book/reader-reviews', json={"options": {}, "idempotency_key": "key", "proposal_sha256": preview["proposal_sha256"]})
    assert response.status_code == 409
    result = client.get('/api/projects/book/reader-reviews').json()["reviews"][0]
    assert result["status"] == 'interrupted' and not result["worker_active"]
