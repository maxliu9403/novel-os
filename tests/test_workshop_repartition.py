from copy import deepcopy
import json

import pytest

from api import workshop
from test_story_capacity import capacity_plan, QUOTE
from test_workshop_api import client, create, finish, model_result
from test_workshop_contract_repair import stub_model
from test_capacity_partition import partition


def proposed(client, monkeypatch):
    result, route = model_result()
    result['capacity_plan'] = capacity_plan(2, 1400)
    monkeypatch.setattr(workshop, '_complete', lambda *_: (deepcopy(result), route))
    data = create(client, premise=QUOTE, length_planning='story_first', mode='short_novel',
                  chapters=2, words_per_chapter=800, language='English')
    assert client.post(f'/api/workshop/sessions/{data["id"]}/turns', json={'revision': 0, 'message': 'Plan the story'}).status_code == 202
    return finish(client, data['id'])


def request(data, *, chapters=4, op='repartition-1'):
    return {'revision': data['revision'], 'message': '', 'intent': 'discuss', 'operation_id': op,
            'capacity_replan': {'proposal_sha256': data['capacity']['proposal_sha256'], 'option_id': 'keep_story', 'chapters': chapters}}


def test_real_worker_repartitions_once_without_story_round_or_automatic_delivery(client, monkeypatch):
    real_complete = workshop._complete
    data = proposed(client, monkeypatch)
    original_design = deepcopy(data['creative_design'])
    monkeypatch.setattr(workshop, '_complete', real_complete)
    def response(attempt, system, user):
        context = json.loads(user)
        assert 'CHAPTER REPARTITION ONLY' in system
        assert context['chapter_repartition']['chapter_words_limit'] == 800
        assert context['initial_brief']['premise'] == QUOTE
        assert context['conversation'][:len(data['messages'])] == data['messages']
        return json.dumps(partition())
    calls = stub_model(monkeypatch, response)
    body = request(data)
    url = f'/api/workshop/sessions/{data["id"]}/turns'
    first = client.post(url, json=body)
    assert first.status_code == 202, first.text
    assert client.post(url, json=body).json()['job_id'] == first.json()['job_id']
    done = finish(client, data['id'])
    assert len(calls) == 1
    assert done['status'] == 'draft', done.get('last_error')
    assert done['capacity']['status'] == 'proposed'
    assert done['capacity']['plan']['options'][0]['total_words'] == 2800
    assert len(done['capacity']['plan']['options'][0]['chapters']) == 4
    assert done['creative_design'] == original_design  # No new map or premise approval.
    assert not done['prompt_md'] and not done['ready_for_confirmation']
    assert done['input'] == data['input']  # Only actual adoption changes chapter count.
    assert done['capacity_history'][0]['proposal_sha256'] == data['capacity']['proposal_sha256']
    assert done['capacity_replan'] is None
    assert len(done['capacity']['repartition']['scenes']) == 4
    # The same frozen allocation reaches delivery. A failed model leaves the
    # accepted allocation retryable instead of undoing the author's choice.
    def fail(session, _):
        assert len(session['capacity']['confirmed']['option']['chapters']) == 4
        assert session['input']['words_per_chapter'] == 800
        raise RuntimeError('Deliberate delivery interruption')
    monkeypatch.setattr(workshop, '_complete', fail)
    adoption = {'revision': done['revision'], 'intent': 'deliver', 'operation_id': 'adopt-new-count',
                'capacity_choice': {'proposal_sha256': done['capacity']['proposal_sha256'], 'option_id': 'repartitioned'}}
    assert client.post(url, json=adoption).status_code == 202
    accepted = finish(client, done['id'])
    assert accepted['input']['chapters'] == 4 and accepted['input']['words_per_chapter'] == 800
    assert accepted['capacity']['status'] == 'confirmed'


def test_invalid_repartition_is_not_retried_or_adopted_and_author_can_retry(client, monkeypatch):
    real_complete = workshop._complete
    data = proposed(client, monkeypatch)
    monkeypatch.setattr(workshop, '_complete', real_complete)
    calls = stub_model(monkeypatch, lambda *_: json.dumps({'reply': 'Bad', 'chapters': []}))
    body = request(data)
    url = f'/api/workshop/sessions/{data["id"]}/turns'
    assert client.post(url, json=body).status_code == 202
    failed = finish(client, data['id'])
    assert failed['status'] == 'error' and len(calls) == 1
    assert failed['capacity']['plan'] == data['capacity']['plan']
    assert failed['input'] == data['input']
    assert failed['capacity_replan']['request'] == body['capacity_replan']
    calls = stub_model(monkeypatch, lambda *_: json.dumps(partition()))
    retry = {**body, 'revision': failed['revision'], 'operation_id': 'explicit-retry'}
    response = client.post(url, json=retry)
    assert response.status_code == 202, response.text
    assert finish(client, data['id'])['capacity']['status'] == 'proposed'
    assert len(calls) == 1


def test_oversized_adoption_and_invalid_count_never_spend_a_model_call(client, monkeypatch):
    data = proposed(client, monkeypatch)
    monkeypatch.setattr(workshop, '_complete', lambda *_: pytest.fail('Rejected choices cannot call a model'))
    url = f'/api/workshop/sessions/{data["id"]}/turns'
    body = request(data)
    for invalid in [2, 0, 512, 4.5, True]:
        wrong = deepcopy(body); wrong['capacity_replan']['chapters'] = invalid
        assert client.post(url, json=wrong).status_code == 422
    wrong = deepcopy(body); wrong['capacity_replan']['proposal_sha256'] = '0' * 64
    assert client.post(url, json=wrong).status_code == 422
    assert client.post(url, json={**body, 'message': 'Change the ending too'}).status_code == 422
    adopt = {'revision': data['revision'], 'intent': 'deliver', 'operation_id': 'overlong-adopt',
             'capacity_choice': {'proposal_sha256': data['capacity']['proposal_sha256'], 'option_id': 'keep_story'}}
    response = client.post(url, json=adopt)
    assert response.status_code == 422 and '每章字数' in response.text
    current = client.get(f'/api/workshop/sessions/{data["id"]}').json()
    assert current['revision'] == data['revision'] and current['capacity'] == data['capacity']


def test_changing_basics_discards_failed_partition_scope_without_model_call(client, monkeypatch):
    data = proposed(client, monkeypatch)
    def fail(*_):
        raise RuntimeError('Transport failure')
    monkeypatch.setattr(workshop, '_complete', fail)
    root = f'/api/workshop/sessions/{data["id"]}'
    assert client.post(root + '/turns', json=request(data)).status_code == 202
    failed = finish(client, data['id'])
    assert failed['capacity_replan']
    monkeypatch.setattr(workshop, '_complete', lambda *_: pytest.fail('Editing only'))
    edited = client.post(root + '/input', json={'revision': failed['revision'], 'input': {**failed['input'], 'words_per_chapter': 1000}})
    assert edited.status_code == 200
    assert not edited.json().get('capacity_replan')


def test_bad_json_is_one_request_and_cannot_trigger_whole_story_format_repair(client, monkeypatch):
    real_complete = workshop._complete
    data = proposed(client, monkeypatch)
    monkeypatch.setattr(workshop, '_complete', real_complete)
    calls = stub_model(monkeypatch, lambda *_: '{bad JSON')
    assert client.post(f'/api/workshop/sessions/{data["id"]}/turns', json=request(data)).status_code == 202
    failed = finish(client, data['id'])
    assert failed['status'] == 'error' and len(calls) == 1
    assert failed['capacity']['plan'] == data['capacity']['plan']
