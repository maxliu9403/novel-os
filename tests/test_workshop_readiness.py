"""The next action communicates story readiness without pretending adoption."""
from api.workshop_next_step import project


def session(status='proposed'):
    return {
        'revision': 4, 'status': 'draft', 'input': {'premise': 'A clear story'},
        'messages': [{'role': 'assistant', 'content': 'The ending is designed.'}],
        'creative_design': {'policy': {'capacity_version': 'story-capacity.v1'},
                            'story_state': {'pending': None}, 'rounds': []},
        'capacity': {'status': status, 'plan': {'options': [{'id': 'book'}]},
                     'confirmed': {'option': {'id': 'book'}} if status == 'confirmed' else None},
    }


def test_ready_story_announces_length_choice_then_skeleton_without_adopting():
    data = session()
    result = project(data)
    assert result['readiness']['state'] == 'ready_for_capacity'
    assert '骨架' in result['readiness']['title']
    assert not result['can_deliver']
    assert data['capacity']['confirmed'] is None
    data['capacity']['status'] = 'confirmed'
    data['capacity']['confirmed'] = {'option': {'id': 'book'}}
    assert project(data)['readiness']['state'] == 'ready_for_skeleton'


def test_waiting_or_unresolved_story_never_announces_ready():
    data = session()
    data['status'] = 'running'
    assert project(data)['readiness']['state'] == 'blocked'
    data['status'] = 'draft'
    data['creative_design']['story_state']['pending'] = {'id': 'unresolved-ending'}
    assert project(data)['readiness']['state'] == 'blocked'


def test_missing_questions_alone_do_not_prove_story_readiness():
    data = session('unavailable')
    assert project(data)['readiness']['state'] == 'exploring'
    data['creative_design']['rounds'] = [{'guidance': {'next_action': 'generate', 'reviewed_revision': 4}}]
    assert project(data)['readiness']['state'] == 'ready_for_capacity'
    data['revision'] = 5
    assert project(data)['readiness']['state'] == 'exploring'


def test_unpromoted_development_cannot_offer_formal_delivery():
    data = session('confirmed')
    data['development'] = {'schema': 'creative-development.v1', 'status': 'needs_revision'}
    result = project(data)
    assert not result['can_deliver']
    assert result['readiness']['state'] == 'blocked'
