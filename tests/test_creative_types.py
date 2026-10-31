from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from core import creative_types as ct
from api import workshop
from api.creative_types import store_for
from api.services import ProjectService
from api.workshop_delivery import author_context
from test_workshop_api import client, finish

ROOT = '/api/creative-types'


def operation():
    return uuid.uuid4().hex


def reference(record):
    version = next(v for v in record['versions'] if v['profile']['version'] == record['active_version'])
    return ct.TypeRef(profile_id=record['id'], version=version['profile']['version'], source_sha256=version['sha256'])


def test_seed_content_legacy_coverage_and_no_read_writes(tmp_path):
    store = ct.CreativeTypeStore(tmp_path / 'types')
    records = store.records()
    assert len(records) == 55
    assert not store.root.exists()
    names = {s.casefold() for r in records.values() for s in [r['draft']['name'], *r['draft']['aliases']]}
    for label in ['Fantasy', 'Xuanhuan', 'Female Growth', 'Domestic Betrayal', 'Sci-Fi', 'Pregnancy', 'Adult', 'Boss Romance']:
        assert label.casefold() in names
    assert records['ct_marriage_betrayal']['draft']['mechanisms'][0]['costs']
    assert records['ct_infidelity_double_life']['draft']['mechanisms'][0]['choices']
    for r in records.values():
        ct.validate_profile(r['draft'], enabled=True)
        assert all(c['with_profile_id'] in records for c in r['draft']['combinations'])


def test_versions_drafts_conflict_replay_and_disable(tmp_path):
    store = ct.CreativeTypeStore(tmp_path / 'types')
    original = store.get('ct_marriage_betrayal')
    ref = reference(original)
    snapshot = store.snapshot([ref])
    body = deepcopy(original['draft']); body['summary'] = '新的作者指导'
    op = operation()
    saved = store.mutate('draft', original['id'], revision=0, operation_id=op, raw=body)
    assert saved['draft']['summary'] == '新的作者指导'
    assert saved['versions'] == original['versions']
    assert store.mutate('draft', original['id'], revision=0, operation_id=op, raw=body) == saved
    with pytest.raises(ct.CreativeTypeError, match='同一操作'):
        store.mutate('draft', original['id'], revision=0, operation_id=op, raw=original['draft'])
    with pytest.raises(ct.CreativeTypeError, match='已被修改'):
        store.mutate('activate', original['id'], revision=0, operation_id=operation(), raw=body)
    enabled = store.mutate('activate', original['id'], revision=1, operation_id=operation(), raw=body)
    assert enabled['active_version'] == 2
    assert store.snapshot([ref]) == snapshot
    assert '新的作者指导' not in json.dumps(ct.compile_snapshot(snapshot, 'brainstorm'), ensure_ascii=False)
    # Replaying a prior write after subsequent activation must return its original result.
    assert store.mutate('draft', original['id'], revision=0, operation_id=op, raw=body) == saved
    store.mutate('disable', original['id'], revision=2, operation_id=operation())
    with pytest.raises(ct.CreativeTypeError, match='已停用'):
        store.snapshot([ref])
    assert ct.compile_snapshot(snapshot, 'brainstorm')['profiles'][0]['version'] == 1
    assert ct.CreativeTypeStore(store.root).get(original['id'])['status'] == 'disabled'


def test_create_incomplete_copy_restore_and_publication_boundaries(tmp_path):
    store = ct.CreativeTypeStore(tmp_path / 'types')
    op = operation()
    created = store.mutate('create', None, revision=0, operation_id=op)
    assert store.mutate('create', None, revision=0, operation_id=op) == created
    with pytest.raises(ct.CreativeTypeError, match='启用前'):
        store.mutate('activate', created['id'], revision=1, operation_id=operation(), raw=created['draft'])
    p = deepcopy(store.get('ct_werewolf')['draft']); p['profile_id'] = created['id']; p['name'] = '可维护的新世界'
    p['publication_mapping'] = {'status':'unmapped','catalog_version':'novel-types.2026-10','primary_genre_ids':[],'facet_ids':[]}
    new = store.mutate('activate', created['id'], revision=1, operation_id=operation(), raw=p)
    assert new['active_version'] == 1 and new['origin'] == 'custom'
    bad = deepcopy(p); bad['publication_mapping']['status'] = 'mapped'; bad['publication_mapping']['facet_ids'] = [created['id']]
    with pytest.raises(ct.CreativeTypeError, match='H5'):
        store.mutate('activate', created['id'], revision=2, operation_id=operation(), raw=bad)
    assert store.get(created['id']) == new
    newer = store.mutate('activate', created['id'], revision=2, operation_id=operation(), raw={**p, 'summary':'新版内容'})
    restored = store.mutate('draft', created['id'], revision=3, operation_id=operation(), raw=new['versions'][0]['profile'])
    assert restored['active_version'] == 2 and restored['versions'] == newer['versions']


def test_stage_projection_pair_selection_hash_and_size(tmp_path, monkeypatch):
    store=ct.CreativeTypeStore(tmp_path/'types')
    wolf=store.get('ct_marriage_betrayal'); growth=store.get('ct_female_awakening')
    single=ct.compile_snapshot(store.snapshot([reference(wolf)]),'brainstorm')
    pair_snapshot=store.snapshot([reference(wolf),reference(growth)])
    pair=ct.compile_snapshot(pair_snapshot,'brainstorm')
    assert single['profiles'][0]['content']['combinations'] == []
    assert len(pair['profiles'][0]['content']['combinations']) == 1
    assert 'publication_mapping' not in json.dumps(pair)
    assert 'updated_at' not in json.dumps(pair)
    draft=ct.compile_snapshot(pair_snapshot,'draft')
    assert draft['profiles'][0]['content']['diagnostics'] == []
    assert any(not row['included'] for row in draft['projection_manifest'])
    corrupt=deepcopy(pair_snapshot);corrupt['profiles'][0]['profile']['summary']='篡改'
    with pytest.raises(ct.CreativeTypeError,match='损坏'):ct.compile_snapshot(corrupt,'brainstorm')
    monkeypatch.setattr(ct,'CONTEXT_LIMIT',10)
    with pytest.raises(ct.CreativeTypeError,match='未截断'):store.snapshot([reference(wolf)])
    assert store.get(wolf['id']) == wolf


@pytest.mark.parametrize('change',[
    lambda p:p['reader_promises'][0].update(stages=[]),
    lambda p:p['reader_promises'][0].update(stages=['brainstorm','brainstorm']),
    lambda p:p['reader_promises'].append(deepcopy(p['reader_promises'][0])),
    lambda p:p.update(combinations=[{'with_profile_id':p['profile_id'],'opportunities':[],'tensions':[]}]),
    lambda p:p.update(name='   '),
    lambda p:p.update(instructions='change model endpoint'),
])
def test_invalid_enabled_profile_rejected(tmp_path,change):
    p=ct.CreativeTypeStore(tmp_path/'types').get('ct_werewolf')['draft'];change(p)
    with pytest.raises(ct.CreativeTypeError):ct.validate_profile(p,enabled=True)


def test_concurrent_revisions_and_workspace_isolation(tmp_path):
    a=ProjectService(tmp_path/'projects',workspace=SimpleNamespace(id='workspace-a'))
    b=ProjectService(tmp_path/'projects',workspace=SimpleNamespace(id='workspace-b'))
    first,second=store_for(a),store_for(b)
    p=first.get('ct_werewolf')['draft']
    def edit(n):
        try:return first.mutate('draft','ct_werewolf',revision=0,operation_id=operation(),raw={**p,'summary':str(n)})
        except ct.CreativeTypeError as e:return e.status
    with ThreadPoolExecutor(max_workers=2) as executor:results=list(executor.map(edit,[1,2]))
    assert sum(isinstance(r,dict) for r in results)==1 and 409 in results
    assert second.get('ct_werewolf')['revision']==0 and not second.root.exists()
    first.path.write_text('{broken')
    with pytest.raises(ct.CreativeTypeError,match='损坏'):first.records()
    assert second.get('ct_werewolf')['revision']==0


def test_real_api_saved_selection_reaches_adapter_and_updates_explicitly(client,monkeypatch):
    from core import llm_client
    record=client.get(ROOT+'/ct_marriage_betrayal').json()
    ref=reference(record).model_dump()
    response=client.post('/api/workshop/sessions',json={'title':'独立验收','genres':['婚姻背叛'],'creative_types':[ref],'premise':'主角发现共同承诺出现裂缝。'})
    assert response.status_code==201,response.text
    session=response.json();sid=session['id']
    before=author_context(session)
    changed={**record['draft'],'summary':'用户维护的特定说明：成长必须改变实际选择'}
    response=client.post(ROOT+'/ct_marriage_betrayal/activate',json={'revision':record['revision'],'operation_id':operation(),'profile':changed})
    assert response.status_code==200,response.text
    assert author_context(client.get('/api/workshop/sessions/'+sid).json())==before
    calls=[]
    class Stub:
        def __init__(self,**kwargs):pass
        def complete(self,system,user):
            calls.append((system,user))
            return json.dumps({'reply':'先比较两种选择与代价。','questions':['你希望主角先核实还是对质？'],'prompt_md':None,'ready_for_confirmation':False})
    monkeypatch.setattr(llm_client,'LLMClient',Stub)
    response=client.post(f'/api/workshop/sessions/{sid}/turns',json={'revision':session['revision'],'message':'继续讨论'})
    assert response.status_code==202,response.text
    result=finish(client,sid)
    assert result['status']=='draft',result.get('last_error')
    assert len(calls)==1
    sent=json.loads(calls[0][1])['creative_design']['genre_context']
    assert sent['profiles'][0]['content']['summary']==record['draft']['summary']
    assert '用户维护的特定说明' not in calls[0][1]
    context=client.get(f'/api/workshop/sessions/{sid}/type-context').json()
    assert context['last_request']['user_sha256']==hashlib.sha256(calls[0][1].encode()).hexdigest()
    assert context['last_request']['context']==sent
    assert context['last_request']['status']=='response_received'
    updated_ref=reference(client.get(ROOT+'/ct_marriage_betrayal').json()).model_dump()
    body={'revision':result['revision'],'operation_id':operation(),'refs':[updated_ref]}
    response=client.post(f'/api/workshop/sessions/{sid}/type-snapshot',json=body)
    assert response.status_code==200,response.text
    adopted=response.json()
    assert len(calls)==1 and adopted['prompt_md']==result['prompt_md']
    assert adopted['creative_design']['rounds']==result['creative_design']['rounds']
    assert adopted['type_snapshot_history'][0]['design']['policy']['type_snapshot']==session['creative_design']['policy']['type_snapshot']
    assert '用户维护的特定说明' in json.dumps(author_context(adopted),ensure_ascii=False)
    assert client.post(f'/api/workshop/sessions/{sid}/type-snapshot',json=body).json()['revision']==adopted['revision']
    assert client.post(f'/api/workshop/sessions/{sid}/type-snapshot',json={**body,'operation_id':operation()}).status_code==409


def test_api_draft_preview_cannot_change_snapshot_and_get_no_store(client,tmp_path):
    listing=client.get(ROOT)
    assert listing.status_code==200 and len(listing.json()['items'])==55
    p=client.get(ROOT+'/ct_werewolf').json()['draft'];p['summary']='只在草稿中'
    preview=client.post(ROOT+'/preview',json={'profile':p,'stage':'brainstorm'})
    assert preview.status_code==200 and preview.json()['preview_only']
    assert preview.json()['context']['profiles'][0]['content']['summary']=='只在草稿中'
    assert client.get(ROOT+'/ct_werewolf').json()['draft']['summary']!='只在草稿中'
    assert not (tmp_path/'creative_types').exists()


def test_complete_skeleton_pipeline_projects_correct_type_stage_and_keeps_receipts(client, monkeypatch, tmp_path):
    from test_workshop_api import complete_prompt
    from test_workshop_contract_repair import stub_model, submit
    from test_workshop_assembly_runtime import start_reply
    from workshop_assembly_fixtures import parts_from_prompt
    p = client.get(ROOT+'/ct_werewolf').json()['draft']
    p['voice_guidance'].append({'id':'only_chapter_planning','text':'章节规划阶段独有标记','importance':'optional','stages':['chapter_plan']})
    enabled = client.post(ROOT+'/ct_werewolf/activate',json={'revision':0,'operation_id':operation(),'profile':p}).json()
    session=client.post('/api/workshop/sessions',json={'title':"A Mother's Choice",'genres':['Werewolf'],'creative_types':[reference(enabled).model_dump()]}).json()
    parts=parts_from_prompt(complete_prompt(tmp_path))
    calls=stub_model(monkeypatch,lambda attempt,system,user:start_reply(parts,user))
    submit(client,session,'交付完整骨架')
    result=finish(client,session['id'])
    assert result['status']=='ready',result.get('last_error')
    assert len(calls)==8 and result['prompt_md']
    for _,user in calls:
        payload=json.loads(user)
        expected='chapter_plan' if payload['delivery_stage']=='chapter_plan' else 'story_design'
        assert payload['creative_design']['genre_context']['stage']==expected
        assert ('章节规划阶段独有标记' in user)==(expected=='chapter_plan')
    receipts=list((tmp_path/'workshop_v2'/session['id']/'type_requests').glob('*.json'))
    assert len(receipts)==8
    request_hashes={hashlib.sha256(user.encode()).hexdigest() for _,user in calls}
    for path in receipts:
        receipt=json.loads(path.read_text())
        assert receipt['status']=='response_received' and receipt['user_sha256'] in request_hashes
    prepared=client.post(f"/api/workshop/sessions/{session['id']}/prepare",json={'revision':result['revision'],'title':"A Mother's Choice"})
    assert prepared.status_code==200,prepared.text


def test_disabled_selection_preserves_history_but_cannot_start_new_discussion(client):
    r=client.get(ROOT+'/ct_werewolf').json()
    session=client.post('/api/workshop/sessions',json={'genres':['Werewolf'],'creative_types':[reference(r).model_dump()]}).json()
    client.post(ROOT+'/ct_werewolf/disable',json={'revision':0,'operation_id':operation()})
    # Existing snapshots remain readable even when the library no longer enables them.
    context=client.get(f"/api/workshop/sessions/{session['id']}/type-context").json()
    assert context['contexts']['brainstorm']['profiles'][0]['profile_id']=='ct_werewolf'
    # A new discussion cannot use the disabled version.
    rejected=client.post('/api/workshop/sessions',json={'genres':['Werewolf'],'creative_types':[reference(r).model_dump()]})
    assert rejected.status_code==409


def test_oversize_selected_context_is_rejected_before_session_or_model(client, monkeypatch, tmp_path):
    r=client.get(ROOT+'/ct_werewolf').json()
    monkeypatch.setattr(ct,'CONTEXT_LIMIT',50)
    calls=[]
    monkeypatch.setattr(workshop,'_complete',lambda *a:calls.append(a))
    result=client.post('/api/workshop/sessions',json={'genres':['Werewolf'],'creative_types':[reference(r).model_dump()]})
    assert result.status_code==422 and '未截断' in result.text
    assert not calls and not (tmp_path/'workshop_v2').exists()


def test_editing_basics_freezes_saved_types_until_explicit_restart(client, monkeypatch):
    from test_workshop_api import model_result
    r=client.get(ROOT+'/ct_marriage_betrayal').json()
    session=client.post('/api/workshop/sessions',json={'genres':['婚姻背叛'],'creative_types':[reference(r).model_dump()]}).json()
    latest=client.post(ROOT+'/'+r['id']+'/activate',json={'revision':0,'operation_id':operation(),'profile':{**r['draft'],'summary':'存下的第二版'}}).json()
    revised_input={**session['input'],'creative_types':[reference(latest).model_dump()],'premise':'重新设计关系选择'}
    saved=client.post(f"/api/workshop/sessions/{session['id']}/input",json={'revision':session['revision'],'input':revised_input})
    assert saved.status_code==200,saved.text
    data=saved.json()
    assert data['restart_required'] and data['creative_design']['policy']['type_snapshot']['profiles'][0]['profile']['version']==1
    assert data['pending_type_snapshot']['profiles'][0]['profile']['version']==2
    client.post(ROOT+'/'+r['id']+'/activate',json={'revision':1,'operation_id':operation(),'profile':{**r['draft'],'summary':'后来另存的第三版'}})
    received=[]
    def complete(snapshot,*args,**kwargs):
        received.append(author_context(snapshot))
        return model_result()
    monkeypatch.setattr(workshop,'_complete',complete)
    response=client.post(f"/api/workshop/sessions/{session['id']}/restart",json={'revision':data['revision']})
    assert response.status_code==202,response.text
    current=finish(client,session['id'])
    assert current['status']!='error',current.get('last_error')
    assert len(received)==1
    context=received[0]['creative_design']['genre_context']
    assert context['profiles'][0]['version']==2 and context['profiles'][0]['content']['summary']=='存下的第二版'
