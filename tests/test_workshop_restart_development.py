"""A fresh workshop context cannot reuse the previous operation or its proof."""
from copy import deepcopy
import json

from api import workshop, workshop_development, workshop_story
from test_story_capacity import capacity_plan, QUOTE
from test_workshop_api import client, create, edit_session_file, finish, model_result  # noqa: F401
from test_workshop_story import story_notes, action


def _turn(client, data, **body):
    response = client.post(f'/api/workshop/sessions/{data["id"]}/turns',
                           json={'revision': data['revision'], **body})
    assert response.status_code == 202, response.text
    return finish(client, data['id'])


def _change_basics(client, data):
    response = client.post(f'/api/workshop/sessions/{data["id"]}/input', json={
        'revision': data['revision'], 'input': {**data['input'], 'premise': QUOTE,
            'length_planning': 'story_first', 'language': 'English',
            'mode': 'short_novel', 'words_per_chapter': 800},
    })
    assert response.status_code == 200, response.text
    return response.json()


def _restart(client, data):
    response = client.post(f'/api/workshop/sessions/{data["id"]}/restart',
                           json={'revision': data['revision']})
    assert response.status_code == 202, response.text
    result = finish(client, data['id'])
    assert result['status'] == 'draft', result.get('last_error')
    return result


def _converged_result():
    result, route = model_result()
    result['capacity_plan'] = capacity_plan(2, 550)
    result['design_notes'] = {
        'proposals': [], 'recommendation_reason': 'The central choice is designed.',
        'conflicts': [],
        'guidance': {'groups': [], 'next_action': 'generate', 'reason': 'The ending is designed.', 'missing': []},
        'story': {'synopsis': QUOTE, 'focus': 'ending', 'changes': [],
                  'nodes': [{'area': area, 'status': 'proposed', 'summary': QUOTE, 'evidence': []}
                            for area in workshop_story.AREAS]},
    }
    return result, route


def test_restart_after_explanation_reviews_and_saves_new_design_and_capacity(client, monkeypatch):
    result, route = model_result()
    result['design_notes'] = story_notes()
    monkeypatch.setattr(workshop, '_complete', lambda *_: (deepcopy(result), route))
    original = create(client, genres=['Werewolf'], premise='A physician offers shelter to an exile.')
    pending = _turn(client, original, message='Design the whole story.')
    monkeypatch.setattr(workshop, '_complete', lambda *_: model_result())
    explained = _turn(client, pending, **action(pending, mode='question', message='Explain the cost of the alliance.'))
    assert explained['turn_scope'] == 'explanation'
    changed = _change_basics(client, explained)
    generated, reviews = [], []

    def generate(snapshot, _skill):
        generated.append(deepcopy(snapshot['development']['active']['baseline']))
        assert snapshot['turn_scope'] == 'development'
        return _converged_result()

    class Judge:
        def complete(self, system, user):
            reviews.append(json.loads(user))
            return json.dumps({'status': 'pass', 'issues': [], 'evidence_requests': []})

    monkeypatch.setattr(workshop, '_complete', generate)
    monkeypatch.setattr(workshop_development, '_client', lambda *_: Judge())
    restarted = _restart(client, changed)
    assert len(generated) == 1 and reviews
    assert len(restarted['creative_design']['rounds']) == 1
    assert restarted['creative_design']['story_state']['map']['synopsis'] == QUOTE
    assert restarted['capacity']['status'] == 'proposed'
    assert restarted['development']['last_assessment']['status'] == 'passed'


def test_restart_archives_old_verification_and_excludes_it_from_new_baseline(client, monkeypatch, tmp_path):
    monkeypatch.setattr(workshop, '_complete', lambda *_: model_result())
    original = create(client, premise='The original story has a different ending.')
    accepted = _turn(client, original, message='Develop the original ending.')
    old_development = deepcopy(accepted['development'])
    old_development['delivery_verification'] = {'status': 'passed', 'source_sha256': 'a' * 64,
                                               'review': {'candidate_sha256': 'b' * 64}}
    edit_session_file(tmp_path, accepted['id'], development=old_development)
    root = tmp_path / 'workshop_v2' / accepted['id']
    retained = root / 'delivery-checkpoints' / 'historical-artifact.json'
    retained.parent.mkdir(parents=True, exist_ok=True)
    retained.write_text('{"historical":"complete stage"}\n')
    artifact_bytes = retained.read_bytes()
    changed = _change_basics(client, accepted)
    snapshots = []

    def generate(snapshot, _skill):
        snapshots.append(deepcopy(snapshot['development']))
        return model_result()

    monkeypatch.setattr(workshop, '_complete', generate)
    restarted = _restart(client, changed)
    fresh = snapshots[0]
    assert fresh['candidate_history'] == []
    assert all(key not in fresh for key in ('accepted', 'last_assessment', 'delivery_verification', 'notice'))
    assert fresh['active']['baseline']['last_accepted_candidate'] is None
    assert fresh['active']['baseline']['candidate_history_status'] == []
    assert fresh['active']['baseline']['dependencies']['initial_brief']['premise'] == QUOTE
    assert fresh['initialized_revision'] == changed['revision'] + 1
    assert 'delivery_verification' not in restarted['development']
    assert len(restarted['development']['candidate_history']) == 1
    archived = client.get(f'/api/workshop/sessions/{accepted["id"]}/history/{changed["revision"]}')
    assert archived.status_code == 200, archived.text
    assert archived.json()['development'] == old_development
    assert retained.read_bytes() == artifact_bytes


def test_restart_ignores_retained_numeric_operation_and_old_request_identity(client, monkeypatch, tmp_path):
    original = create(client, premise='Previous premise.')
    changed = _change_basics(client, original)
    # Legacy/persisted restart records may retain a numeric operation even though
    # the current input-edit endpoint now invalidates that operation itself.
    old_edit = {'local_result': {'old': 'numeric allocation'}, 'request': {'chapters': 3}}
    old_request = {'request_hash': 'old-request', 'job_id': None}
    changed = edit_session_file(tmp_path, changed['id'], capacity_replan=old_edit,
        active_turn_request='old-capacity-edit', turn_requests={'old-capacity-edit': old_request})
    snapshots = []

    def generate(snapshot, _skill):
        snapshots.append(snapshot)
        assert not snapshot.get('capacity_replan')
        assert snapshot.get('active_turn_request') is None
        return model_result()

    monkeypatch.setattr(workshop, '_complete', generate)
    restarted = _restart(client, changed)
    assert len(snapshots) == 1
    assert restarted['turn_scope'] == 'development'
    assert restarted['turn_requests']['old-capacity-edit'] == old_request
    assert restarted['development']['last_assessment']['status'] == 'passed'
