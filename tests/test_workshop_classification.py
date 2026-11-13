from copy import deepcopy
import json
import pytest
from core.publication_taxonomy import bind_classification
from api.workshop_delivery import author_context
from core.creative_types import CreativeTypeStore
from core.novel_classification import NovelClassification, parse_classification_block
from core.publication_taxonomy import classification
from test_creative_types import selected
from test_workshop_api import client, ready
from test_workshop_handoff import make_prompt, handoff, REPO


def selection(client):
    items=client.get('/api/creative-types').json()['items']
    return [{'id':r['id'],'version':r['active_version'],'source_sha256':r['source_sha256']}
            for r in items if r['id'] in ('genre.romance','tag.betrayal')]


def test_selection_defaults_omitted_channel_but_requires_valid_channel_genre_and_tags(client,tmp_path):
    refs=selection(client)
    for invalid in (None,[],refs[:1],refs+refs[:1]):
        response=client.post('/api/workshop/sessions',json={'creation_flow':'genre_tags.v2','audience_channel':'female','creative_types':invalid})
        assert response.status_code==422,response.text
    assert client.post('/api/workshop/sessions',json={'creative_types':refs,'audience_channel':None}).status_code == 422
    omitted=client.post('/api/workshop/sessions',json={'creative_types':refs})
    assert omitted.status_code == 201,omitted.text
    assert omitted.json()['input']['audience_channel'] == 'female'
    data=client.post('/api/workshop/sessions',json={'creation_flow':'genre_tags.v2','audience_channel':'female','creative_types':refs}).json()
    assert data['input']['publication_taxonomy']['genre']['id']=='genre.romance'
    assert 'source_reference' not in author_context(data)
    assert 'author-selected' in author_context(data)['publication_selection_policy']


def test_model_cannot_override_author_taxonomy_and_private_knowledge_stays_private(tmp_path):
    store=CreativeTypeStore(tmp_path/'types');snapshot=store.snapshot(selected(store))
    raw=make_prompt(tmp_path).read_text();brief={'publication_taxonomy':snapshot['publication_taxonomy']}
    bound=bind_classification(raw,brief);value=parse_classification_block(bound)
    assert value.primary_genre_id=='genre.romance'
    assert value.story_type_ids==('tag.betrayal',)
    assert value.to_dict()['schema_version']=='novel-classification.v2'
    assert value.to_dict()==NovelClassification.from_dict(value.to_dict()).to_dict()
    assert bind_classification(bound,brief)==bound
    assert 'sections' not in json.dumps(value.to_dict()) and 'markdown' not in json.dumps(value.to_dict())
    path=tmp_path/'bound.md';path.write_text(bound)
    assert handoff.validate(path,REPO)['chapters']==21
    for invalid in (raw+raw,raw.replace('[NOVEL_CLASSIFICATION_JSON]','[OTHER]')):
        with pytest.raises(ValueError,match='唯一'):bind_classification(invalid,brief)


def test_public_revision_separate_from_writing_revision(tmp_path):
    store=CreativeTypeStore(tmp_path/'types');before=store.snapshot(selected(store))
    r=store.get('tag.betrayal')
    md=r['draft'].replace('## 故事机制','## 故事机制\n\nOnly private writing rules change.')
    r=store.mutate('activate',r['id'],revision=0,operation_id='private01',markdown=md)
    after=store.snapshot(selected(store))
    assert before['publication_taxonomy']==after['publication_taxonomy']
    assert before['snapshot_sha256']!=after['snapshot_sha256']
    import yaml
    _,head,body=md.split('---',2);header=yaml.safe_load(head)
    header['publication']['definition']='New public description.'
    md='---\n'+yaml.safe_dump(header,allow_unicode=True,sort_keys=False)+'---'+body
    store.mutate('activate',r['id'],revision=r['revision'],operation_id='public01',markdown=md)
    assert store.snapshot(selected(store))['publication_taxonomy']['tags'][0]['revision']==2


def test_real_workshop_prepare_keeps_exact_taxonomy(client,monkeypatch,tmp_path):
    data=ready(client,monkeypatch,tmp_path,creation_flow='genre_tags.v1',creative_types=selection(client))
    assert data['status']=='ready',data.get('last_error')
    assert parse_classification_block(data['prompt_md']).primary_genre_id=='genre.romance'
    response=client.post(f"/api/workshop/sessions/{data['id']}/prepare",json={'revision':data['revision'],'title':'Chosen Love'})
    assert response.status_code==200,response.text
    assert parse_classification_block(response.json()['prompt_md']).story_type_ids==('tag.betrayal',)


@pytest.mark.parametrize('change',[lambda v:v.update(primary_genre_id='genre.horror'),lambda v:v['taxonomy']['tags'].append(v['taxonomy']['tags'][0]),lambda v:v['taxonomy']['genre'].update(revision=0),lambda v:v['taxonomy'].update(namespace='../bad')])
def test_invalid_public_projection_is_rejected(tmp_path,change):
    store=CreativeTypeStore(tmp_path);value=classification(store.snapshot(selected(store))['publication_taxonomy']).to_dict()
    change(value)
    with pytest.raises(ValueError):NovelClassification.from_dict(value)
