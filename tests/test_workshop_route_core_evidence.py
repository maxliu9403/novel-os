"""A new route choice can settle its core, without adopting optional assumptions."""
from copy import deepcopy

import pytest

from api import workshop
from api.workshop_guidance import source_core_adopted
from api.workshop_request import discussion_system
from api.workshop_story_result import validate_candidate
from core.source_route_context import validate_context
from test_source_route_handoff import setup_source
from test_workshop_api import client, finish, model_result  # noqa: F401


def _selected(client, monkeypatch, *, adopt_core):
    _, _, _, _, body = setup_source(client)
    if adopt_core:
        body['adoption_scope'] = 'core_and_payoffs'
    monkeypatch.setattr(workshop, '_complete', lambda *_: model_result())
    response = client.post('/api/workshop/source-route-handoffs', json=body)
    assert response.status_code == 202, response.text
    return finish(client, response.json()['session_id'])


def _candidate(session):
    source = session['source_context']
    action = source['selection']['action_id']
    stages = {'opening': 'opening', 'middle': 'development', 'climax': 'climax', 'ending': 'ending'}
    return {'synopsis': 'Deepen the selected story without replacing its core.', 'focus': 'ending', 'changes': [],
            'nodes': [{'area': 'direction', 'summary': source['selected_proposal']['title'], 'status': 'settled',
                       'evidence': [{'author_action_ref': action}]}] + [
                {'area': area, 'summary': source['selected_proposal']['causal_arc'][field], 'status': 'settled',
                 'evidence': [{'author_action_ref': action, 'route_path': ['causal_arc', field],
                               'quote': source['selected_proposal']['causal_arc'][field]}]}
                for area, field in stages.items()]}


def _validate(candidate, session):
    return validate_candidate(candidate, {'source_revision': session['revision'],
                                         'message_index': len(session['messages'])}, session)


@pytest.mark.parametrize('adopt_core', [False, True])
def test_route_core_scope_is_explicit_and_frozen_source_is_unchanged(client, monkeypatch, adopt_core):
    session = _selected(client, monkeypatch, adopt_core=adopt_core)
    frozen = deepcopy(session['source_context'])
    result = _validate(_candidate(session), session)
    assert result['map']['nodes'][0]['status'] == 'settled'
    assert {node['status'] for node in result['map']['nodes'][1:]} == ({'settled'} if adopt_core else {'proposed'})
    system = discussion_system(session, 'Workshop task.')
    assert ('CURRENT AUTHOR SELECTION SCOPE: core_and_payoffs' in system) == adopt_core
    assert ('For the direction node only' in system) != adopt_core
    if adopt_core:
        assert 'proposed_assumptions remain proposals' in system
        assert 'Latest author corrections retain priority' in system
    assert source_core_adopted(session) is adopt_core
    assert session['source_context'] == frozen
    assert validate_context(session['source_context']) == frozen


def test_selected_core_requires_field_evidence_and_never_adopts_optional_assumptions(client, monkeypatch):
    session = _selected(client, monkeypatch, adopt_core=True)
    source = session['source_context']
    candidate = _candidate(session)
    action = source['selection']['action_id']
    candidate['nodes'][1]['evidence'] = [{'author_action_ref': action}]
    candidate['nodes'][2]['evidence'] = [{'author_action_ref': action,
        'route_path': ['proposed_assumptions', 0], 'quote': source['selected_proposal']['proposed_assumptions'][0]}]
    candidate['nodes'][3]['evidence'] = [{'author_action_ref': action,
        'route_path': ['type_suggestion', 'reason'], 'quote': source['selected_proposal']['type_suggestion']['reason']}]
    candidate['nodes'][4]['evidence'] = [{'author_action_ref': action,
        'route_path': ['tradeoffs', 0], 'quote': source['selected_proposal']['tradeoffs'][0]}]
    result = _validate(candidate, session)
    assert result['map']['nodes'][0]['status'] == 'settled'
    assert all(node['status'] == 'proposed' for node in result['map']['nodes'][1:])
    assert any(issue['code'] == 'core_evidence_required' for issue in result['validation']['issues'])


@pytest.mark.parametrize('changed', [
    {'author_action_ref': 'unrecorded-selection'},
    {'route_path': ['causal_arc']},
    {'route_path': ['emotional_design', -1, 'payoff']},
    {'route_path': ['causal_arc', 'missing']},
    {'quote': 'This ending was never part of the selected route.'},
    {'quote': None},
])
def test_adopted_route_evidence_requires_current_selection_and_exact_scalar_quote(client, monkeypatch, changed):
    session = _selected(client, monkeypatch, adopt_core=True)
    candidate = _candidate(session)
    candidate['nodes'][-1]['evidence'][0].update(changed)
    result = _validate(candidate, session)
    assert result['map']['nodes'][-1]['status'] == 'proposed'
    assert result['map']['nodes'][-1]['evidence'] == []


def test_old_or_replaced_selection_scope_cannot_settle_new_route_core(client, monkeypatch):
    session = _selected(client, monkeypatch, adopt_core=True)
    session['source_route_selection']['action_id'] = 'an-earlier-route-selection'
    assert not source_core_adopted(session)
    result = _validate(_candidate(session), session)
    assert all(node['status'] == 'proposed' for node in result['map']['nodes'][1:])


def test_emotional_payoff_can_cite_specific_adopted_route_field(client, monkeypatch):
    session = _selected(client, monkeypatch, adopt_core=True)
    candidate = _candidate(session)
    candidate['nodes'][-1]['evidence'] = [{
        'author_action_ref': session['source_context']['selection']['action_id'],
        'route_path': ['emotional_design', 0, 'payoff'],
        'quote': session['source_context']['selected_proposal']['emotional_design'][0]['payoff'],
    }]
    result = _validate(candidate, session)
    assert result['map']['nodes'][-1]['status'] == 'settled'
    assert result['map']['nodes'][-1]['evidence'] == candidate['nodes'][-1]['evidence']
