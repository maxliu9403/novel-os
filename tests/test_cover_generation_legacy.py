"""Legacy batch POST remains compatible while making its task recoverable."""
import threading
import time

from tests.test_cover_api import _client, _brief, _concepts


def test_legacy_generation_post_recovers_exact_active_batch_without_new_image_calls(tmp_path, monkeypatch):
    client, image_client = _client(tmp_path, monkeypatch)
    client.post('/api/projects', json={'title':'Book', 'id':'book'})
    # Resolve the created project using the same persisted list as the UI.
    projects = client.get('/api/projects').json()
    project_id = projects[0]['id']
    base = f'/api/projects/{project_id}/covers'
    entered, release = threading.Event(), threading.Event()
    generate = image_client.generate

    def blocked(prompt):
        entered.set()
        assert release.wait(5), 'test must release image double'
        return generate(prompt)

    monkeypatch.setattr(image_client, 'generate', blocked)
    body = {'brief':_brief(), 'concepts':_concepts()}
    try:
        started = client.post(base+'/generate', json=body)
        assert started.status_code == 202, started.text
        assert entered.wait(2)
        recovered = client.get(base+'/generation-tasks/latest')
        assert recovered.status_code == 200, recovered.text
        task = recovered.json()
        assert task and task['status'] == 'running'
        assert task['job_id'] == started.json()['job_id']
        assert task['task_id'] == started.json()['task_id']
        assert task['cover_set_id']
        assert task['progress']['phase'] == 'generating'
        duplicate = client.post(base+'/generate', json=body)
        assert duplicate.status_code == 202, duplicate.text
        assert duplicate.json()['task_id'] == task['task_id']
        assert image_client.calls == 0
    finally:
        release.set()
    for _ in range(200):
        restored = client.get(base+'/generation-tasks/latest').json()
        if restored['status'] not in {'queued','running'}:
            break
        time.sleep(.01)
    assert restored['status'] == 'completed', restored
    assert restored['progress']['completed'] == restored['progress']['ready'] == 4
    assert restored['progress']['failed'] == 0
    assert image_client.calls == 4
    assert len(client.get(base).json()) == 1
