"""Only a verified completed generation is offered for review-only recovery."""
from copy import deepcopy
import json

import pytest

from api import workshop, workshop_development
from test_workshop_api import client, create, finish, model_result  # noqa: F401


def _failed_review(client, monkeypatch):
    generated = []
    monkeypatch.setattr(workshop, '_complete', lambda *_: (generated.append(True) or model_result()))
    def unavailable(*_):
        raise TimeoutError('Review connection unavailable')
    monkeypatch.setattr(workshop_development, '_client', unavailable)
    value = create(client, premise='The protagonist chooses independence.')
    response = client.post(f'/api/workshop/sessions/{value["id"]}/turns',
                           json={'revision': value['revision'], 'message': 'Develop the ending.'})
    assert response.status_code == 202, response.text
    value = finish(client, value['id'])
    assert value['status'] == 'error'
    return value, generated


def test_saved_generation_recovery_is_read_only_and_resume_does_not_generate_again(client, monkeypatch, tmp_path):
    value, generated = _failed_review(client, monkeypatch)
    assert value['recovery']['can_resume'] is True
    assert value['recovery']['kind'] == 'saved_generation'
    assert not value.get('delivery_progress')
    session_file = tmp_path / 'workshop_v2' / value['id'] / 'session.json'
    before = session_file.read_bytes()
    assert client.get(f'/api/workshop/sessions/{value["id"]}').json()['recovery'] == value['recovery']
    assert session_file.read_bytes() == before
    class Judge:
        def complete(self, system, user):
            return json.dumps({'status': 'pass', 'issues': [], 'evidence_requests': []})
    monkeypatch.setattr(workshop_development, '_client', lambda *_: Judge())
    response = client.post(f'/api/workshop/sessions/{value["id"]}/resume', json={'revision': value['revision']})
    assert response.status_code == 202, response.text
    done = finish(client, value['id'])
    assert done['status'] == 'draft', done.get('last_error')
    assert generated == [True]
    assert 'recovery' not in done


@pytest.mark.parametrize('invalid', ['missing_generation', 'tampered_generation', 'changed_run', 'restart_pending', 'running'])
def test_unknown_or_unbound_generation_never_advertises_saved_review_recovery(client, monkeypatch, tmp_path, invalid):
    value, _ = _failed_review(client, monkeypatch)
    root = tmp_path / 'workshop_v2' / value['id']
    generation = next((root / 'development-runs').glob('*/generation.json'))
    view = deepcopy(value)
    if invalid == 'missing_generation':
        generation.unlink()
        journal = generation.parent / 'generation-requests' / ('a' * 64 + '.json')
        journal.parent.mkdir(exist_ok=True)
        journal.write_text(json.dumps({'state': 'pending_unknown', 'request_id': 'unknown-request'}))
    elif invalid == 'tampered_generation':
        saved = json.loads(generation.read_text())
        saved['result']['reply'] += ' tampered'
        generation.write_text(json.dumps(saved))
    elif invalid == 'changed_run':
        view['delivery_run_id'] = 'f' * 32
    elif invalid == 'restart_pending':
        view['restart_required'] = True
    else:
        view['status'] = 'running'
    assert workshop_development.recovery_view(view, root) is None
