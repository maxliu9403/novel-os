"""Real route storage -> real Workshop persistence, with isolated model doubles."""
from copy import deepcopy
import json
import shutil
from types import SimpleNamespace

import pytest
from test_workshop_api import client, finish, model_result  # noqa: F401
from test_source_library import bundle
from test_creative_types import default_refs

from api import workshop
from api.routes import get_service
from api.source_library import store_for
from api.source_routes import catalog_for
from core.source_story_design import SourceStoryDesign
from core.creative_types import digest
from core.source_route_context import (SourceEvidenceCoordinator, make_packet, save_packet,
                                       read_packet, VERSION, _units)


def route(slot, source_ref):
    return {"slot": slot, "title": "以公开证据争取独立", "reader_promise": "她能否亲手拿回选择权？",
            "continuity_basis": "承接第二章关系变化。", "source_refs": [source_ref],
            "causal_arc": {"opening": "她拒绝隐瞒账目。", "development": "合伙人施压，她联合证人。",
                           "turning_point": "她公开账目，也承认自己的失误。", "climax": "证人当面对质。",
                           "ending": "她取得独立经营权，双方重新协商关系。"},
            "character_relationship_arcs": ["从依赖对方转为平等协商。"],
            "emotional_design": [{"expectation": "希望她能被听见。", "scene": "公开会议。", "choice": "承认自己的失误。",
                                  "cost": "短期声誉受损。", "change": "证人因此愿意相信她。", "payoff": "她有权决定新合作。"}],
            "highlights": [{"scene": "她主动公开账目。", "story_value": "坦白换来可信证言。"}],
            "long_hooks_and_payoffs": [{"question": "谁能证实旧承诺？", "development": "证人反复犹豫。", "payoff": "她坦白后，证人公开支持。"}],
            "type_suggestion": {"audience": "female", "genre_id": "genre.romance", "tag_ids": [default_refs()[1].id], "reason": "关系变化推动人物选择。"},
            "proposed_assumptions": ["新证人属于续写设想。"], "tradeoffs": ["关系靠公开承担代价推进。"],
            "difference_from_existing": "通过公开担责破局。", "changes_from_parent": ""}


def setup_source(client, files=None):
    projects = client.app.dependency_overrides[get_service]()
    library = store_for(projects)
    book = library.import_files(bundle() if files is None else files)
    design = SourceStoryDesign(library, book['id'])
    request = {"operation_id": "initial-for-handoff", "requested_count": 2, "feedback": "  不要靠误会拖延。\n"}
    state = design.prepare(request, model={"model": "fake"}, catalog=catalog_for(projects))

    def complete(system, user):
        body = json.loads(user)
        ref = body['source_view'][0]['id']
        return json.dumps({"understanding": {"story_summary": "完整已知来源。", "narrative_endpoint": "第二章末关系改变。",
                           "opening_analysis": "首章通过具体选择建立期待。", "open_threads": ["旧承诺待回应。"],
                           "observations": [{"local_id": "o1", "kind": "open_thread", "statement": "旧承诺。", "source_refs": [ref], "supersedes": []}]},
                           "routes": [route(slot, ref) for slot in body['task']['allowed_output_slots']],
                           "recommended_slot": body['task']['allowed_output_slots'][0], "recommendation_reason": "人物主动承担代价。"}, ensure_ascii=False)

    result = design.run(state['run_id'], SimpleNamespace(complete=complete))
    assert result['status'] == 'ready', result
    saved = design.get_route_set(result['route_set_sha256'])
    body = {"operation_id": "handoff-operation-01", "draft_id": "new-workshop-draft-01",
            "source_id": book['id'], "route_set_sha256": saved['sha256'], "route_id": saved['routes'][0]['route_id'],
            "input": {"premise": "作者希望强调事业与关系。", "audience_channel": "female",
                      "creative_types": [r.model_dump() for r in default_refs()]},
            "author_note": "  保留她独立经营的方向。\n"}
    return projects, library, design, saved, body


def test_handoff_preserves_author_words_proposal_authority_and_single_first_turn(client, monkeypatch):
    projects, library, design, saved, body = setup_source(client)
    observed = []
    monkeypatch.setattr(workshop, '_complete', lambda s, skill: (observed.append(deepcopy(s['source_context'])) or model_result()))
    response = client.post('/api/workshop/source-route-handoffs', json=body)
    assert response.status_code == 202, response.text
    identifier = response.json()['session_id']
    final = finish(client, identifier)
    again = client.post('/api/workshop/source-route-handoffs', json=body)
    assert again.status_code == 202 and again.json()['session_id'] == identifier
    assert len(observed) == 1
    assert final['input']['premise'] == body['input']['premise']
    source = final['source_context']
    assert source['selected_proposal'] == saved['routes'][0]
    assert source['selection']['author_note'] == body['author_note']
    assert source['author_feedback'][0]['exact_text'] == '  不要靠误会拖延。\n'
    assert source['proposal_status'] == 'direction_selected_details_proposed'
    assert not source.get('analysis_sha256')
    packet = read_packet(workshop.WorkshopService(projects)._path(identifier), source)
    assert packet['content']['coverage']['all_supplied_text_sent']
    assert len([s for s in workshop.WorkshopService(projects).root.iterdir() if (s / 'session.json').exists()]) == 1


def test_context_survives_library_removal_and_rejects_tampered_private_evidence(client, monkeypatch):
    projects, library, design, saved, body = setup_source(client)
    monkeypatch.setattr(workshop, '_complete', lambda *_: model_result())
    response = client.post('/api/workshop/source-route-handoffs', json=body)
    assert response.status_code == 202, response.text
    session = finish(client, response.json()['session_id'])
    root = workshop.WorkshopService(projects)._path(session['id'])
    shutil.rmtree(library.path(body['source_id']))
    calls = []
    coordinator = SourceEvidenceCoordinator(root, session['source_context'])
    model = SimpleNamespace(complete=lambda system, user: calls.append(user) or '{"reply":"ready"}')
    assert coordinator.complete(model, 'system', 'task') == '{"reply":"ready"}'
    assert 'First scene.' in calls[0] and 'Second scene' in calls[0] and 'Announcement' in calls[0]
    assert coordinator.complete(model, 'system', 'task') == '{"reply":"ready"}'
    assert len(calls) == 1
    path = root / 'source-evidence' / (session['source_context']['evidence']['sha256'] + '.json')
    bad = json.loads(path.read_text()); bad['content']['story_summary'] = 'changed'
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match='校验'):
        SourceEvidenceCoordinator(root, session['source_context'])


def test_conflicting_handoff_cannot_replace_existing_source_or_create_another_session(client, monkeypatch):
    projects, library, design, saved, body = setup_source(client)
    monkeypatch.setattr(workshop, '_complete', lambda *_: model_result())
    response = client.post('/api/workshop/source-route-handoffs', json=body)
    finish(client, response.json()['session_id'])
    changed = {**body, 'author_note': '改成复合。'}
    assert client.post('/api/workshop/source-route-handoffs', json=changed).status_code == 409
    changed['operation_id'] = 'different-handoff-02'
    assert client.post('/api/workshop/source-route-handoffs', json=changed).status_code == 409
    assert len(workshop.WorkshopService(projects).list_sessions()['sessions']) == 1


def test_cover_and_rules_are_frozen_before_source_library_removal(client, monkeypatch, tmp_path):
    from test_poster_references import png
    from api.workshop_source_handoff import carry_route_cover
    from core.reference_assets import ReferenceAssets
    from core.source_route_context import execution_policy

    files = bundle()
    files['cover.png'] = png()
    projects, library, _, _, body = setup_source(client, files)
    monkeypatch.setattr(workshop, '_complete', lambda *_: model_result())
    response = client.post('/api/workshop/source-route-handoffs', json=body)
    assert response.status_code == 202, response.text
    session = finish(client, response.json()['session_id'])
    root = workshop.WorkshopService(projects)._path(session['id'])
    source = session['source_context']
    assert source['execution_policy'] == execution_policy()
    asset_id = source['cover_asset']['asset_id']
    assert ReferenceAssets(root / 'source-cover').read(asset_id) == files['cover.png']
    shutil.rmtree(library.path(body['source_id']))

    project = tmp_path / 'portable-book'
    carry_route_cover(root, project, source)
    assets = ReferenceAssets(project / 'outputs/private/references')
    assert assets.read(asset_id) == files['cover.png']
    assert assets.selection()['asset_id'] == asset_id


def evidence_fixture(tmp_path, text):
    packet = make_packet({'segments': [{'id': 's1', 'text': text}], 'ledger': [{'id': 'early', 'statement': '开头承诺必须保留'}]},
                         source_id='source-' + 'a' * 64, source_sha256='b' * 64, understanding_sha256='c' * 64)
    context = {'schema': VERSION, 'source_id': packet['source_id'], 'source_sha256': packet['source_sha256'],
               'understanding_sha256': packet['understanding_sha256'], 'route_set_sha256': 'd' * 64,
               'mode': 'source_development', 'selected_proposal': {'sha256': digest({})},
               'evidence': save_packet(tmp_path, packet)}
    context['context_sha256'] = digest(context)
    return context, packet


def test_small_capacity_reconstructs_all_evidence_and_closes_latest_candidate(tmp_path):
    context, packet = evidence_fixture(tmp_path, '最早的承诺🙂\n' + '中段资料。' * 700 + '\n最后一幕。')
    calls = []

    def complete(system, user):
        body = json.loads(user)
        calls.append(body)
        return json.dumps({'candidate': '完整故事方案，不省略结局。', 'evidence_requests': []}, ensure_ascii=False)

    coordinator = SourceEvidenceCoordinator(tmp_path, context)
    result = coordinator.complete(SimpleNamespace(complete=complete), 'system', 'author original text', limit=2800)
    assert result == '完整故事方案，不省略结局。'
    # At least one full stable traversal after the initial candidate is created.
    assert len(calls) > 2
    spans = [u for c in calls for u in c['evidence_units'] if u['path'] == ['content', 'segments', 0, 'text']]
    unique = {(u.get('start', 0), u.get('end', len(u['value']))): u['value'] for u in spans}
    covered = set()
    for (start, end), value in unique.items():
        assert packet['content']['segments'][0]['text'][start:end] == value
        covered.update(range(start, end))
    assert len(covered) == len(packet['content']['segments'][0]['text'])
    assert any(u['value'] == '开头承诺必须保留' for c in calls for u in c['evidence_units'])
    count = len(calls)
    assert coordinator.complete(SimpleNamespace(complete=complete), 'system', 'author original text', limit=2800) == result
    assert len(calls) == count


def test_unknown_transport_is_preserved_without_blind_resend(tmp_path):
    context, _ = evidence_fixture(tmp_path, '完整来源。')
    calls = []
    def fail(*args):
        calls.append(args)
        raise TimeoutError('upstream unknown')
    coordinator = SourceEvidenceCoordinator(tmp_path, context)
    with pytest.raises(TimeoutError):
        coordinator.complete(SimpleNamespace(complete=fail), 'system', 'task')
    with pytest.raises(ValueError, match='尚未确认'):
        coordinator.complete(SimpleNamespace(complete=fail), 'system', 'task')
    assert len(calls) == 1


@pytest.mark.parametrize('status', [401, 502])
def test_confirmed_http_failure_recovers_without_manual_reconciliation(tmp_path, status):
    context, _ = evidence_fixture(tmp_path, '原始来源与未决承诺。')
    calls = []

    def complete(*args):
        calls.append(args)
        if len(calls) == 1:
            error = RuntimeError('upstream response')
            error.status_code = status
            raise error
        return '完整候选'

    model = SimpleNamespace(complete=complete)
    coordinator = SourceEvidenceCoordinator(tmp_path, context)
    if status == 401:
        with pytest.raises(RuntimeError):
            coordinator.complete(model, 'system', 'task')
        # After connection settings are corrected, ordinary resume can send.
        coordinator = SourceEvidenceCoordinator(tmp_path, context)
    assert coordinator.complete(model, 'system', 'task') == '完整候选'
    assert len(calls) == 2
    records = [json.loads(p.read_text()) for p in (tmp_path / 'source-evidence/requests').glob('*/*.json')
               if p.name != 'coverage.json']
    assert len(records) == 1
    assert records[0]['attempts'][0]['failure']['http_status'] == status
    assert records[0]['attempts'][1]['state'] == 'received'


def test_pending_feedback_enters_old_direction_without_becoming_source_fact(client, monkeypatch):
    projects, library, design, saved, body = setup_source(client)
    pending = design.prepare({'operation_id': 'pending-extra-direction', 'operation': 'expand_routes',
        'base_route_set_sha256': saved['sha256'], 'feedback': '保留不复合，加强事业线，再给两个选项。',
        'author_context': {'constraints': '不要靠误会。'}}, model={}, catalog=catalog_for(projects))
    body['pending_feedback_run_id'] = pending['run_id']
    monkeypatch.setattr(workshop, '_complete', lambda *_: model_result())
    response = client.post('/api/workshop/source-route-handoffs', json=body)
    assert response.status_code == 202, response.text
    data = finish(client, response.json()['session_id'])
    source = data['source_context']
    assert source['author_feedback'][-1]['exact_text'] == pending['request']['feedback']
    assert source['selected_proposal'] == saved['routes'][0]
    assert source['pending_feedback_run_id'] == pending['run_id']


def test_v5_execution_inherits_private_evidence_and_later_author_amendments(client, monkeypatch, tmp_path):
    from api.workshop_design import execution_handoff
    from core.creative_execution import CreativeExecution, validate_handoff
    projects, library, design, saved, body = setup_source(client)
    monkeypatch.setattr(workshop, '_complete', lambda *_: model_result())
    response = client.post('/api/workshop/source-route-handoffs', json=body)
    assert response.status_code == 202, response.text
    session = finish(client, response.json()['session_id'])
    session['prompt_md'] = '# Adopted complete skeleton\n她以公开担责争取独立，保留平等关系。'
    session['messages'].append({'role': 'user', 'content': '结局保留独立经营，关系可以继续讨论。'})
    handoff = execution_handoff(session)
    assert handoff['schema'] == 'creative-handoff.v5'
    assert handoff['author_requirements']['entries'][-1]['text'] == session['messages'][-1]['content']
    project = tmp_path / 'new-book'
    inputs = project / 'outputs/input'; inputs.mkdir(parents=True)
    (inputs / 'prompt.md').write_text(session['prompt_md'])
    packet = read_packet(workshop.WorkshopService(projects)._path(session['id']), session['source_context'])
    save_packet(project / 'outputs/private', packet)
    shutil.rmtree(library.path(body['source_id']))
    runtime = CreativeExecution.open(project=project, run_id='test-new-route', handoff=handoff)
    user, receipt = runtime.prepare('outline_planner', session['prompt_md'] + '\nConvert the complete adopted story.', None, None)
    requests = []
    model = SimpleNamespace(complete=lambda system, user: requests.append(user) or '{"result":"complete"}')
    assert runtime.complete(model, 'outline_planner', user, receipt) == '{"result":"complete"}'
    assert 'First scene.' in requests[0] and 'Announcement' in requests[0]
    assert '不要靠误会拖延' in requests[0] and '平等关系' in requests[0]
    broken = deepcopy(handoff)
    broken['source_context']['selected_proposal']['causal_arc']['ending'] = '改写为未选结局。'
    with pytest.raises(ValueError):
        validate_handoff(broken)


def test_route_action_can_settle_only_direction(client, monkeypatch):
    from api.workshop_story_result import validate_candidate
    projects, library, design, saved, body = setup_source(client)
    monkeypatch.setattr(workshop, '_complete', lambda *_: model_result())
    response = client.post('/api/workshop/source-route-handoffs', json=body)
    data = finish(client, response.json()['session_id'])
    candidate = {'synopsis': '正在完善所选方向。', 'focus': 'direction', 'changes': [],
                 'nodes': [{'area': area, 'summary': '模型提出的具体设计。', 'status': 'settled',
                            'evidence': [{'author_action_ref': body['operation_id']}]}
                           for area in ('direction', 'opening', 'middle', 'climax', 'ending')]}
    record = {'source_revision': 0, 'message_index': len(data['messages'])}
    result = validate_candidate(candidate, record, data)
    assert result['map']['nodes'][0]['status'] == 'settled'
    assert all(n['status'] == 'proposed' for n in result['map']['nodes'][1:])
