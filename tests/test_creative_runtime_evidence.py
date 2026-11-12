"""Frozen history stays recoverable without becoming repeated active authority."""
import json
from types import SimpleNamespace

import pytest

from core import creative_execution as ce
from core.creative_baseline import build
from core.creative_development import DevelopmentReview
from test_creative_development import Model
from test_development_handoff import SOURCE, kwargs, project, restore_projection


def runtime(tmp_path, history=None):
    history = history if history is not None else 'UNADOPTED OPTION: ' + 'old alternative; ' * 14000
    baseline = build({'id': 'handoff-story', 'revision': 3,
        'input': {'premise': 'Keep her freedom and the real cost.'},
        'messages': [{'role': 'user', 'content': 'The ending must preserve her independence.'},
                     {'role': 'assistant', 'content': history}],
        'creative_design': {'rounds': [], 'decisions': []}})
    receipt = DevelopmentReview(tmp_path / 'review', baseline).assess(Model(), {
        'story_design': {'ending': SOURCE}, 'chapter_plan': [{'number': 1, 'scene': SOURCE}]})['receipt']
    handoff = ce.make_handoff(**kwargs(), development_basis={
        'status': 'passed', 'source_sha256': ce.text_hash(SOURCE), 'baseline': baseline, 'review': receipt})
    result = ce.CreativeExecution.open(project=project(tmp_path, handoff), run_id='compact', handoff=handoff)
    return result, handoff, history


def test_unadopted_large_history_is_not_sent_as_current_authority(tmp_path):
    run, handoff, history = runtime(tmp_path)
    user, receipt = run.prepare('outline_planner', SOURCE, None, None)
    assert receipt['request_bytes'] < 40000
    assert history not in user
    assert 'The ending must preserve her independence.' in user
    assert 'Keep her freedom and the real cost.' in user
    assert history in (run.directory / 'lock.json').read_text()
    assert run.read_development_evidence([])['value'] == handoff['development_basis']
    context = run.design_context('draft')
    view = restore_projection(context['development_basis']['data'], context)
    assert view['schema'] == 'creative-runtime-basis.v1'
    assert view['basis_sha256'] == handoff['development_basis']['sha256']
    assert view['historical_evidence']['authority'].startswith('Historical')


def test_exact_history_request_is_durable_and_resume_does_not_repurchase(tmp_path):
    run, handoff, _ = runtime(tmp_path)
    user, receipt = run.prepare('outline_planner', SOURCE, None, None)
    calls = []
    request = {'path': ['baseline', 'dependencies', 'conversation', 1, 'content'], 'start_char': 0, 'end_char': 80}
    def complete(system, user):
        calls.append(user)
        if len(calls) == 1:
            return json.dumps({'creative_evidence_requests': [request]})
        assert 'UNADOPTED OPTION' in user and 'complete_value_supplied":false' in user
        return '{"foundation":"complete"}'
    client = SimpleNamespace(complete=complete, planning_input_bytes=128000)
    assert run.complete(client, 'outline_planner', user, receipt) == '{"foundation":"complete"}'
    assert len(calls) == 2
    reopened = ce.CreativeExecution.open(project=run.project, run_id='compact', handoff=handoff,
        expected_sha256=run.lock_sha256, already_started=True)
    assert reopened.complete(client, 'outline_planner', user, receipt) == '{"foundation":"complete"}'
    assert len(calls) == 2


def test_evidence_paths_are_bound_json_addresses_and_slices_declare_scope(tmp_path):
    run, _, history = runtime(tmp_path)
    item = run.read_development_evidence(['baseline', 'dependencies', 'conversation', 1, 'content'],
                                         start_char=10, end_char=50)
    assert item['value'] == history[10:50]
    assert item['source_value_sha256'] == ce.digest(history)
    assert item['total_chars'] == len(history) and item['complete_value_supplied'] is False
    assert item['value_sha256'] == ce.digest(history[10:50])
    for path in [['../../outside'], ['baseline', 'dependencies', 'conversation', -1],
                 ['baseline', 'dependencies', 'conversation', True],
                 ['baseline', 'dependencies', 'conversation', 1, 'content', 0]]:
        with pytest.raises(ValueError):
            run.read_development_evidence(path)
    with pytest.raises(ValueError):
        run.read_development_evidence(['baseline'], start_char=0, end_char=10)
    with pytest.raises(ValueError):
        run.read_development_evidence(['baseline', 'dependencies', 'conversation', 1, 'content'], start_char=1)


def test_requested_large_history_pages_completely_on_same_small_connection_and_resumes(tmp_path):
    run, _, history = runtime(tmp_path)
    user, receipt = run.prepare('outline_planner', SOURCE, None, None)
    calls = []
    fragments = {}
    def complete(system, user):
        calls.append(user)
        if len(calls) == 1:
            return json.dumps({'creative_evidence_requests': [
                {'path': ['baseline', 'dependencies', 'conversation', 1, 'content']}]})
        payload = json.loads(user)
        for unit in payload['evidence_units']:
            if unit['path'] == ['content', 'historical_evidence', 0, 'value']:
                fragments[(unit.get('start', 0), unit.get('end', len(history)))] = unit['value']
        return json.dumps({'candidate': '{"foundation":"complete"}', 'evidence_requests': []})
    client = SimpleNamespace(complete=complete, planning_input_bytes=128000)
    assert run.complete(client, 'outline_planner', user, receipt) == '{"foundation":"complete"}'
    covered_until = 0
    for (start, end), value in sorted(fragments.items()):
        assert value == history[start:end]
        assert start <= covered_until
        covered_until = max(covered_until, end)
    assert covered_until == len(history)
    assert 2 < len(calls) < 15
    assert all(len(call.encode()) < 128000 for call in calls)
    count = len(calls)
    assert run.complete(client, 'outline_planner', user, receipt) == '{"foundation":"complete"}'
    assert len(calls) == count


def test_normal_planning_json_is_not_mistaken_for_evidence_envelope(tmp_path):
    run, _, _ = runtime(tmp_path, history='A small unadopted option.')
    user, receipt = run.prepare('outline_planner', SOURCE, None, None)
    response = json.dumps({'title': 'A book', 'notes': {'creative_evidence_requests': []}})
    client = SimpleNamespace(complete=lambda system, user: response, planning_input_bytes=128000)
    assert run.complete(client, 'outline_planner', user, receipt) == response


def test_unknown_history_transport_never_repurchases_on_plain_resume(tmp_path):
    from core.source_request_session import RequestOutcomeUnknown
    run, _, _ = runtime(tmp_path, history='A retained option.')
    user, receipt = run.prepare('outline_planner', SOURCE, None, None)
    calls = []
    def unavailable(system, user):
        calls.append(user)
        raise TimeoutError('Upstream result is unknown')
    client = SimpleNamespace(complete=unavailable, planning_input_bytes=128000)
    with pytest.raises(TimeoutError):
        run.complete(client, 'outline_planner', user, receipt)
    with pytest.raises(RequestOutcomeUnknown):
        run.complete(client, 'outline_planner', user, receipt)
    assert len(calls) == 1


def test_source_evidence_remains_complete_during_optional_history_retrieval(tmp_path):
    from test_source_route_handoff import evidence_fixture
    from test_development_handoff import verification
    from core.source_route_context import save_packet
    source, packet = evidence_fixture(tmp_path / 'evidence', 'Every source detail must remain available.')
    handoff = ce.make_handoff(**kwargs(), source_context=source,
                             development_basis=verification(tmp_path, source, packet))
    root = project(tmp_path, handoff)
    save_packet(root / 'outputs/private', packet)
    run = ce.CreativeExecution.open(project=root, run_id='source-history', handoff=handoff)
    user, receipt = run.prepare('outline_planner', SOURCE, None, None)
    calls = []
    def respond(system, user):
        calls.append(user)
        assert 'Every source detail must remain available.' in user
        if len(calls) == 1:
            return json.dumps({'creative_evidence_requests': [{'path': ['baseline', 'route_snapshot']}]})
        assert 'creative-runtime-evidence-selection.v1' in user
        return '{"result":"complete"}'
    model = SimpleNamespace(complete=respond, planning_input_bytes=128000)
    assert run.complete(model, 'outline_planner', user, receipt) == '{"result":"complete"}'
    assert len(calls) == 2
    assert run.complete(model, 'outline_planner', user, receipt) == '{"result":"complete"}'
    assert len(calls) == 2


def test_multiple_explicit_slices_do_not_accumulate_into_an_oversize_base_prompt(tmp_path):
    run, _, history = runtime(tmp_path)
    user, receipt = run.prepare('outline_planner', SOURCE, None, None)
    path = ['baseline', 'dependencies', 'conversation', 1, 'content']
    midpoint = len(history) // 2
    first = json.dumps({'creative_evidence_requests': [{'path': path, 'start_char': 0, 'end_char': midpoint}]})
    second = json.dumps({'creative_evidence_requests': [{'path': path, 'start_char': midpoint, 'end_char': len(history)}]})
    final = '{"foundation":"both ranges integrated"}'
    calls, seen = [], set()
    def respond(system, user):
        calls.append(user)
        if len(calls) == 1:
            return first
        payload = json.loads(user)
        for unit in payload['evidence_units']:
            address = unit['path']
            if address[:2] == ['content', 'historical_evidence'] and len(address) >= 4 and address[3] == 'value':
                seen.add(address[2])
        completed = 1 in seen or payload['candidate'] == final
        return json.dumps({'candidate': final if completed else second, 'evidence_requests': []})
    model = SimpleNamespace(complete=respond, planning_input_bytes=128000)
    assert run.complete(model, 'outline_planner', user, receipt) == final
    assert seen == {0, 1}
    assert all(len(call.encode()) < 128000 for call in calls)
    count = len(calls)
    assert run.complete(model, 'outline_planner', user, receipt) == final
    assert len(calls) == count
