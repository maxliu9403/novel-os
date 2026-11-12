"""Exercise length editing through the real session/worker commit seam."""
from copy import deepcopy
import json

from api import workshop
from test_capacity_editing import expansion
from test_story_capacity import capacity_plan, QUOTE
from test_workshop_api import client, create, finish, model_result
from test_workshop_contract_repair import stub_model


def proposed(client, monkeypatch):
    result, route = model_result()
    result['capacity_plan'] = capacity_plan(2, 550)
    monkeypatch.setattr(workshop, '_complete', lambda *_: (deepcopy(result), route))
    value = create(client, premise=QUOTE, length_planning='story_first', mode='short_novel',
                   chapters=2, words_per_chapter=600, language='English')
    response = client.post(f'/api/workshop/sessions/{value["id"]}/turns',
                           json={'revision': 0, 'message': 'Design the whole story.'})
    assert response.status_code == 202
    return finish(client, value['id'])


def edit(value, **changes):
    return {'revision': value['revision'], 'message': '', 'intent': 'discuss',
            'operation_id': 'explicit-capacity-edit',
            'capacity_replan': {'proposal_sha256': value['capacity']['proposal_sha256'],
                               'option_id': 'keep_story', 'chapters': 2,
                               'total_words': 1160, 'words_per_chapter': 650, **changes}}


def test_local_length_edit_runs_no_model_and_preserves_history_and_story(client, monkeypatch):
    value = proposed(client, monkeypatch)
    before = deepcopy(value)
    calls = []
    def unexpected(*args):
        calls.append(args)
        raise AssertionError('A safe numeric allocation must not call a model')
    monkeypatch.setattr(workshop, '_complete', unexpected)
    body = edit(value, chapter_targets=[{'number': 1, 'target_words': 590}, {'number': 2, 'target_words': 570}])
    url = f'/api/workshop/sessions/{value["id"]}/turns'
    accepted = client.post(url, json=body)
    assert accepted.status_code == 202, accepted.text
    assert client.post(url, json=body).json()['job_id'] == accepted.json()['job_id']
    done = finish(client, value['id'])
    assert done['status'] == 'draft', done.get('last_error')
    assert not calls
    assert done['capacity']['status'] == 'proposed' and not done['capacity']['confirmed']
    assert done['capacity']['plan']['scenes'] == before['capacity']['plan']['scenes']
    assert done['capacity']['plan']['options'][0]['total_words'] == 1160
    assert done['creative_design'] == before['creative_design']
    assert not done['prompt_md'] and not done['ready_for_confirmation']
    assert done['input']['words_per_chapter'] == 650 and done['input']['target_total_words'] == 1160
    assert done['capacity_history'][-1]['plan'] == before['capacity']['plan']
    assert done['capacity_replan'] is None


def test_expanded_total_reestimates_complete_scenes_in_one_existing_worker(client, monkeypatch):
    real_complete = workshop._complete
    value = proposed(client, monkeypatch)
    monkeypatch.setattr(workshop, '_complete', real_complete)
    def response(_attempt, system, user):
        payload = json.loads(user)
        request = payload['chapter_repartition']
        assert request['plan'] == value['capacity']['plan']
        assert request['total_words'] == 4000 and request['chapter_words_limit'] == 1000
        assert payload['initial_brief']['target_total_words'] == 4000
        assert request['adjustment_rules'] and 'capacity_adjustment' in system
        return json.dumps(expansion())
    calls = stub_model(monkeypatch, response)
    body = edit(value, chapters=4, total_words=4000, words_per_chapter=1000)
    sent = client.post(f'/api/workshop/sessions/{value["id"]}/turns', json=body)
    assert sent.status_code == 202, sent.text
    done = finish(client, value['id'])
    assert done['status'] == 'draft', done.get('last_error')
    assert len(calls) == 1
    assert done['capacity']['plan']['options'][0]['total_words'] == 4000
    assert len(done['capacity']['plan']['options'][0]['chapters']) == 4
    assert done['capacity']['status'] == 'proposed' and not done['prompt_md']


def test_unfulfillable_total_remains_editable_without_silent_story_loss(client, monkeypatch):
    real_complete = workshop._complete
    value = proposed(client, monkeypatch)
    monkeypatch.setattr(workshop, '_complete', real_complete)
    calls = stub_model(monkeypatch, lambda *_: json.dumps({'status': 'needs_more_space',
        'reply': '两次重要抉择需要现场压力、选择和后果；400 词会丢失代价。', 'minimum_total_words': 1000}))
    body = edit(value, total_words=400, words_per_chapter=200)
    url = f'/api/workshop/sessions/{value["id"]}/turns'
    assert client.post(url, json=body).status_code == 202
    done = finish(client, value['id'])
    assert done['status'] == 'draft', done.get('last_error')
    assert len(calls) == 1 and done['capacity']['status'] == 'stale'
    assert done['capacity']['plan'] == value['capacity']['plan']
    assert done['capacity']['adjustment']['minimum_total_words'] == 1000
    assert not done['capacity']['confirmed'] and not done['prompt_md']
    retry = edit(done, total_words=1100, words_per_chapter=600)
    retry['operation_id'] = 'explicit-capacity-correction'
    assert client.post(url, json=retry).status_code == 202
    corrected = finish(client, value['id'])
    assert corrected['capacity']['status'] == 'proposed'
    assert corrected['capacity']['plan']['options'][0]['total_words'] == 1100
    assert len(calls) == 1
