"""Exact Workshop operation binding and explicit one-attempt recovery."""
from copy import deepcopy
import hashlib
import json

import pytest

from api import workshop, workshop_development, workshop_request_recovery
from core.creative_types import digest
from core.source_request_session import RequestOutcomeUnknown
from test_workshop_api import client, create, finish, model_result  # noqa: F401


def tree(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def unknown(client, monkeypatch, tmp_path):
    calls = []
    response, route = model_result(questions=['Which ending should survive?'])

    class Model:
        def complete(self, system, user):
            calls.append((system, user))
            if len(calls) == 1:
                raise TimeoutError('Unobserved remote completion')
            return json.dumps(response)

    def complete(snapshot, _):
        raw = workshop_development.journal_complete(Model(), 'immutable rules', 'complete author input', snapshot)
        return json.loads(raw), route

    monkeypatch.setattr(workshop, '_complete', complete)
    value = create(client, premise='The heroine retains her independence.')
    url = f'/api/workshop/sessions/{value["id"]}'
    submitted = client.post(url + '/turns', json={'revision': value['revision'], 'message': 'Develop the ending.'})
    assert submitted.status_code == 202, submitted.text
    value = finish(client, value['id'])
    assert value['status'] == 'error', value
    assert value['request_recovery']['can_retry_unknown']
    return value, tmp_path / 'workshop_v2' / value['id'], calls, url


def retry_body(value, operation_id='retry-original-request'):
    recovery = value['request_recovery']
    return {'revision': value['revision'], 'request_recovery': {
        'action': 'retry_unknown', 'operation_id': operation_id,
        'request_sha256': recovery['request_sha256'], 'receipt_sha256': recovery['receipt_sha256']}}


def test_unknown_get_is_read_only_plain_resume_blocks_and_retry_is_exactly_once(client, monkeypatch, tmp_path):
    value, root, calls, url = unknown(client, monkeypatch, tmp_path)
    before = tree(root)
    assert client.get(url).json()['request_recovery'] == value['request_recovery']
    blocked = client.post(url + '/resume', json={'revision': value['revision']})
    assert blocked.status_code == 409
    assert tree(root) == before and len(calls) == 1

    body = retry_body(value)
    submitted = client.post(url + '/resume', json=body)
    assert submitted.status_code == 202, submitted.text
    done = finish(client, value['id'])
    assert done['status'] == 'draft', done.get('last_error')
    assert len(calls) == 2
    repeated = client.post(url + '/resume', json=body)
    assert repeated.status_code == 202
    assert repeated.json()['job_id'] == submitted.json()['job_id']
    assert len(calls) == 2 and 'request_recovery' not in done
    changed = deepcopy(body)
    changed['request_recovery']['request_sha256'] = 'f' * 64
    assert client.post(url + '/resume', json=changed).status_code == 409
    assert len(calls) == 2

    receipt_path = next((root / 'development-runs').glob('*/generation-requests/*.json'))
    receipt = json.loads(receipt_path.read_text())
    assert receipt['retry_authorizations'][0]['status'] == 'consumed'
    archived = receipt_path.parent / 'execution' / body['request_recovery']['request_sha256'] / 'history'
    assert (archived / (body['request_recovery']['receipt_sha256'] + '.json')).is_file()


@pytest.mark.parametrize('field', ['request_sha256', 'receipt_sha256'])
def test_stale_or_wrong_retry_is_rejected_before_mutation(client, monkeypatch, tmp_path, field):
    value, root, calls, url = unknown(client, monkeypatch, tmp_path)
    body = retry_body(value)
    body['request_recovery'][field] = 'f' * 64
    before = tree(root)
    rejected = client.post(url + '/resume', json=body)
    assert rejected.status_code == 409
    assert tree(root) == before and len(calls) == 1


def test_author_input_change_invalidates_recovery_binding(client, monkeypatch, tmp_path):
    value, root, calls, url = unknown(client, monkeypatch, tmp_path)
    session = json.loads((root / 'session.json').read_text())
    original = workshop_request_recovery.authority(session)
    paused = deepcopy(session)
    paused.update(status='paused', revision=session['revision'] + 1, job_id='another-worker')
    assert workshop_request_recovery.authority(paused) == original
    session['input']['constraints'] = 'A new ending supersedes the old request.'
    (root / 'session.json').write_text(json.dumps(session))
    before = tree(root)
    assert 'request_recovery' not in client.get(url).json()
    rejected = client.post(url + '/resume', json=retry_body(value))
    assert rejected.status_code == 409
    assert tree(root) == before and len(calls) == 1


def test_legacy_unknown_explicit_probe_binds_without_buying_a_request(client, monkeypatch, tmp_path):
    value, root, calls, url = unknown(client, monkeypatch, tmp_path)
    workshop_request_recovery.pointer(root).unlink()
    session = json.loads((root / 'session.json').read_text())
    session['logs'][-1]['error_type'] = RequestOutcomeUnknown.__name__
    (root / 'session.json').write_text(json.dumps(session))
    receipt_path = next((root / 'development-runs').glob('*/generation-requests/*.json'))
    original_receipt = receipt_path.read_bytes()
    before = tree(root)
    assert 'request_recovery' not in client.get(url).json()
    assert tree(root) == before
    submitted = client.post(url + '/resume', json={'revision': value['revision']})
    assert submitted.status_code == 202, submitted.text
    probed = finish(client, value['id'])
    assert probed['status'] == 'error'
    assert probed['request_recovery']['can_retry_unknown']
    assert receipt_path.read_bytes() == original_receipt and len(calls) == 1
    retried = client.post(url + '/resume', json=retry_body(probed))
    assert retried.status_code == 202, retried.text
    assert finish(client, value['id'])['status'] == 'draft'
    assert len(calls) == 2


def test_legacy_replay_drift_cannot_become_permission_for_a_new_request(client, monkeypatch, tmp_path):
    value, root, calls, url = unknown(client, monkeypatch, tmp_path)
    workshop_request_recovery.pointer(root).unlink()
    session = json.loads((root / 'session.json').read_text())
    session['logs'][-1]['error_type'] = RequestOutcomeUnknown.__name__
    (root / 'session.json').write_text(json.dumps(session))
    receipt_path = next((root / 'development-runs').glob('*/generation-requests/*.json'))
    original_receipt = receipt_path.read_bytes()
    saved_text = json.dumps(model_result()[0])
    saved_identity = digest({'system': 'earlier completed rules', 'user': 'complete author input'})
    (receipt_path.parent / (saved_identity + '.json')).write_text(json.dumps({
        'system': 'earlier completed rules', 'user': 'complete author input', 'state': 'received',
        'response': saved_text, 'response_sha256': hashlib.sha256(saved_text.encode()).hexdigest()}))

    class UnexpectedModel:
        def complete(self, *_):
            calls.append(('unexpected', 'new request'))
            return json.dumps(model_result()[0])

    def changed_prompt(snapshot, _):
        workshop_development.journal_complete(UnexpectedModel(), 'earlier completed rules',
                                             'complete author input', snapshot)
        raw = workshop_development.journal_complete(UnexpectedModel(), 'changed runtime rules',
                                                   'complete author input', snapshot)
        return json.loads(raw), model_result()[1]
    monkeypatch.setattr(workshop, '_complete', changed_prompt)
    for _ in range(2):
        submitted = client.post(url + '/resume', json={'revision': value['revision']})
        assert submitted.status_code == 202, submitted.text
        value = finish(client, value['id'])
        assert value['status'] == 'error'
        assert not value.get('request_recovery', {}).get('can_resume')
        assert receipt_path.read_bytes() == original_receipt and len(calls) == 1


def test_corrupt_bound_pointer_is_blocked_without_page_mutation(client, monkeypatch, tmp_path):
    value, root, calls, url = unknown(client, monkeypatch, tmp_path)
    workshop_request_recovery.pointer(root).write_text('{broken')
    before = tree(root)
    recovery = client.get(url).json()['request_recovery']
    assert not recovery['can_resume'] and not recovery['can_retry_unknown']
    assert client.post(url + '/resume', json={'revision': value['revision']}).status_code == 409
    assert tree(root) == before and len(calls) == 1


def test_late_saved_complete_response_resumes_without_model_reissue(client, monkeypatch, tmp_path):
    value, root, calls, url = unknown(client, monkeypatch, tmp_path)
    receipt_path = next((root / 'development-runs').glob('*/generation-requests/*.json'))
    receipt = json.loads(receipt_path.read_text())
    response = json.dumps(model_result(questions=['Which ending should survive?'])[0])
    receipt.update(state='received', response=response,
                   response_sha256=hashlib.sha256(response.encode()).hexdigest())
    receipt_path.write_text(json.dumps(receipt))
    before = tree(root)
    current = client.get(url).json()
    assert current['request_recovery']['can_resume']
    assert not current['request_recovery']['can_retry_unknown']
    assert tree(root) == before
    submitted = client.post(url + '/resume', json={'revision': value['revision']})
    assert submitted.status_code == 202, submitted.text
    done = finish(client, value['id'])
    assert done['status'] == 'draft', done.get('last_error')
    assert len(calls) == 1


def test_invalid_author_binding_settles_worker_without_generation(client, monkeypatch):
    value = create(client)
    calls = []
    monkeypatch.setattr(workshop, '_complete', lambda *_: (calls.append(True) or model_result()))
    def invalid_authority(_):
        raise ValueError('Corrupt author baseline')
    monkeypatch.setattr(workshop_request_recovery, 'authority', invalid_authority)
    url = f'/api/workshop/sessions/{value["id"]}'
    submitted = client.post(url + '/turns', json={'revision': value['revision'], 'message': 'Develop the ending.'})
    assert submitted.status_code == 202, submitted.text
    value = finish(client, value['id'])
    assert value['status'] == 'error' and not calls
    assert value['logs'][-1]['error_type'] == 'RequestRecoveryError'
