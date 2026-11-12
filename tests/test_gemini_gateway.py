"""Exercise the actual OpenAI SDK wire and the gateway's documented limitations."""
import json

import httpx
import pytest

from core import gemini_gateway as gateway, model_context_probe as probe, provider_settings, studio_settings
from core.llm_client import LLMClient, LLMError
from core.usage_tracking import normalize_usage


@pytest.fixture
def wire(monkeypatch):
    from openai import OpenAI
    sent, responses, clients = [], [], []
    def handler(request):
        sent.append(request)
        if request.url.path == '/v1/context-capabilities' and not responses:
            return httpx.Response(404, json={'error': 'unsupported'})
        assert responses, 'Unexpected retry'
        return responses.pop(0)
    def builder(self, base_url, api_key):
        client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0,
                        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        clients.append(client)
        return client
    monkeypatch.setattr(LLMClient, '_build_openai_compatible', builder)
    yield sent, responses
    for client in clients: client.close()


def chunk(content='', finish=None, *, reasoning=None, usage=None):
    return {'id':'test', 'object':'chat.completion.chunk', 'created':1, 'model':'gemini-4-argon-thinking',
            'choices':[{'index':0, 'delta':{'content':content, 'reasoning_content':reasoning}, 'finish_reason':finish}],
            'usage':usage}


def stream(*chunks):
    return httpx.Response(200, headers={'content-type':'text/event-stream'},
                         text=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n')


def client(**kwargs):
    return LLMClient(provider='gemini_openai', model='gemini-4-argon-thinking',
                     api_key='secret-test', base_url='https://gateway.test', **kwargs)


def test_wire_preserves_full_long_input_omits_ignored_controls_and_records_echo(wire):
    sent, responses = wire
    responses.append(stream(chunk(reasoning='hidden'), chunk('finished', 'stop', usage={'prompt_tokens':100, 'completion_tokens':2, 'total_tokens':102, 'reasoning_tokens':4})))
    llm=client()
    text='前文与人物关系。'*3000
    assert llm.complete('SYSTEM EXACT', text)=='finished'
    body=json.loads(sent[0].content)
    assert sent[0].url.path=='/v1/chat/completions'
    assert sent[0].headers['authorization']=='Bearer secret-test'
    assert body['messages']==[{'role':'system','content':'SYSTEM EXACT'},{'role':'user','content':text}]
    assert body['stream_options']=={'include_usage':True}
    assert not set(body)&{'max_tokens','reasoning_effort','temperature','response_format'}
    assert llm.last_response_metadata['actual_model_verified'] is False
    assert llm.last_response_metadata['reported_model']=='gemini-4-argon-thinking'
    assert llm.last_response_metadata['usage_source']=='gateway_estimate'
    assert llm._active_stream is None


@pytest.mark.parametrize('finish', ['length','content_filter',None])
def test_incomplete_stream_is_never_accepted(wire, finish):
    _, responses=wire
    responses.append(stream(chunk('unfinished', finish)))
    with pytest.raises(LLMError, match='未完整完成'): client().complete('s','u')


@pytest.mark.parametrize('finish', ['length', 'content_filter', None, 'stop'])
def test_attempt_preserves_all_received_gateway_prose(wire, tmp_path, finish):
    from core.llm_attempt import completion_attempt, recover_completion
    _, responses = wire
    responses.append(stream(chunk(reasoning='PRIVATE_REASONING'), chunk('first '), chunk('last', finish)))
    directory = tmp_path / 'attempt'
    with completion_attempt(directory, 'r1'):
        if finish == 'stop':
            assert client().complete('s', 'u') == 'first last'
        else:
            with pytest.raises(LLMError) as captured:
                client().complete('s', 'u')
            if finish == 'length':
                from core.source_request_recovery import classify_failure
                outcome = classify_failure(captured.value)
                assert outcome['outcome'] == 'failed' and outcome['finish_reason'] == 'length'
    assert (directory / 'partial-response.txt').read_text() == 'first last'
    assert recover_completion(directory, 'r1') == ('first last' if finish == 'stop' else None)


def test_attempt_preserves_last_chunk_beyond_gateway_output_bound(wire, tmp_path, monkeypatch):
    from core.llm_attempt import completion_attempt, recover_completion
    monkeypatch.setattr(gateway, 'OUTPUT_LIMIT_BYTES', 5)
    _, responses = wire
    responses.append(stream(chunk('first'), chunk('entire last chunk', 'stop')))
    with completion_attempt(tmp_path / 'attempt', 'r1'):
        with pytest.raises(LLMError, match='安全上限'):
            client().complete('s', 'u')
    assert (tmp_path / 'attempt' / 'partial-response.txt').read_text() == 'firstentire last chunk'
    assert recover_completion(tmp_path / 'attempt', 'r1') is None


def test_over_budget_is_rejected_without_request_or_truncation(wire):
    sent, _=wire
    from core.llm_client import LLMInputLimitError
    from api.workshop import _failure
    from api.workshop_diagnostics import diagnostic_error_category
    with pytest.raises(LLMInputLimitError, match='请求未发送，内容未截断') as error:
        client().complete('s','中'*(gateway.INPUT_LIMIT_BYTES//3))
    assert len(sent) == 1 and sent[0].method == 'GET'
    message = _failure(error.value, timeout_seconds=900, has_prompt=True)
    assert '128,000' in message and '检查头脑风暴模型配置' not in message
    assert '完整骨架已保留' in message
    assert diagnostic_error_category(error.value) == 'input_limit'


def test_inline_boundary_counts_envelope_and_keeps_full_text(wire):
    from core.llm_client import LLMInputLimitError
    sent, responses = wire
    # Mixed UTF-8 text exercises bytes, not Python character counts.
    budget = gateway.INPUT_LIMIT_BYTES - gateway.ENVELOPE_RESERVE_BYTES - len('system'.encode())
    text = '中' * (budget // 3) + 'x' * (budget % 3)
    responses.append(stream(chunk('ok', 'stop')))
    assert client().complete('system', text) == 'ok'
    assert json.loads(sent[0].content)['messages'][1]['content'] == text
    with pytest.raises(LLMInputLimitError):
        client().complete('system', text + 'x')
    assert len(sent) == 2 and sent[-1].method == 'GET'


def test_first_prose_signal_is_not_throttled_by_reasoning(wire):
    _, responses = wire
    events = []
    responses.append(stream(chunk(reasoning='private'), chunk('first'), chunk('last', 'stop')))
    assert client(diagnostic_callback=events.append).complete('s', 'u') == 'firstlast'
    progress = [event for event in events if event['event'] == 'content.progress']
    assert progress[0]['model_output_chars'] == 5


def test_error_after_content_does_not_return_partial_or_retry(wire):
    sent,responses=wire
    responses.append(stream(chunk('partial'),{'error':{'message':'private upstream log','code':'model_downgraded'}}))
    with pytest.raises(LLMError) as err: client().complete('s','u')
    assert 'private' not in str(err.value)
    assert len(sent)==1


def test_images_share_same_strict_transport(wire):
    sent,responses=wire
    responses.append(stream(chunk('cover', 'stop')))
    assert client().complete_with_images('s','image',[b'\x89PNG\r\n\x1a\nfixture'])=='cover'
    body=json.loads(sent[0].content)
    assert body['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    assert 'max_tokens' not in body


def test_model_id_controls_thinking_not_ignored_parameter():
    with pytest.raises(ValueError, match='完整模型 ID'): client(reasoning_effort='high')
    for media in ['gemini-image','gemini-video','gemini-canvas','gemini-music']:
        assert not gateway.is_text_model(media)
    assert gateway.is_text_model('gemini-4-argon-thinking-medium')
    for url in ['https://secret:key@host/v1','https://host/v1?key=x','https://host/v1/chat/completions','https://host/v1beta']:
        with pytest.raises(ValueError): gateway.normalize_base_url(url)
    assert gateway.normalize_base_url('https://host/')=='https://host/v1'


@pytest.mark.parametrize('size', probe.SIZES)
def test_probe_size_random_markers_and_honest_partial_failure(size):
    system,user,expected=probe.build_probe(size)
    assert len((system+user).encode())==size
    assert gateway.input_bytes(system,user)<gateway.INPUT_LIMIT_BYTES
    assert all(user.count(v)==1 for v in expected.values())
    assert probe.assess(json.dumps(expected),expected)['passed']
    missing={**expected,'ending':'missed'}
    assert probe.assess(json.dumps(missing),expected)['matched']==4
    assert not probe.assess('This is a great story.',expected)['passed']
    assert probe.build_probe(size)[2]!=expected


def test_gateway_usage_keeps_separately_counted_reasoning():
    value=normalize_usage({'prompt_tokens':100,'completion_tokens':10,'reasoning_tokens':30,'total_tokens':110},'gemini_openai')
    assert value['output_tokens']==40
    assert value['reasoning_tokens']==30
    assert value['total_tokens']==140


@pytest.fixture
def settings(tmp_path,monkeypatch):
    for key in studio_settings._ENV_KEYS: monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('NOVEL_OS_SETTINGS_PATH',str(tmp_path/'settings.json'))
    return provider_settings.save_connection({'name':'Gemini gateway','provider':'gemini_openai',
        'base_url':'http://gateway.test','secret_action':'replace','api_key':'fake-key'})


def test_catalog_filters_media_and_preserves_thinking_ids(settings, monkeypatch):
    calls=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,*args):return json.dumps({'data':[{'id':name,'description':'Gateway text model'} for name in ['gemini-video','gemini-4-argon-thinking-medium','gemini-4-argon']]}).encode()
    def open_url(request,timeout):
        calls.append(request)
        return Response()
    monkeypatch.setattr('urllib.request.urlopen',open_url)
    models=provider_settings.refresh_connection_models(settings['id'])
    assert models==['gemini-4-argon','gemini-4-argon-thinking-medium']
    assert calls[0].full_url=='http://gateway.test/v1/models'
    assert calls[0].get_header('Authorization')=='Bearer fake-key'
    route={'id':'default','connection_id':settings['id'],'model':models[0],'inherits_default':False}
    provider_settings.save_text_routes([route])
    with pytest.raises(ValueError,match='模型 ID'):
        provider_settings.save_text_routes([{**route,'reasoning_effort':'high'}])
    assert provider_settings.resolve_text_route('default')['reasoning_effort']==''
    with pytest.raises(ValueError,match='媒体工具'):
        provider_settings.save_vision_route('cover_review',{'connection_id':settings['id'],'model':'gemini-image'})


def test_context_probe_api_runs_once_with_synthetic_input(settings,tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import create_app
    import re,time
    seen=[]
    class Fake:
        def __init__(self,**kwargs):seen.append(kwargs)
        def complete(self,system,user):
            seen.append((system,user))
            return json.dumps(dict(re.findall(r'ANCHOR (\w+): ([0-9a-f]+)',user)))
    monkeypatch.setattr('core.llm_client.LLMClient',Fake)
    provider_settings.save_text_routes([{'id':'default','connection_id':settings['id'],
        'model':'gemini-4-argon-thinking','inherits_default':False}])
    with TestClient(create_app(projects_root=tmp_path/'projects',db_url=f'sqlite:///{tmp_path}/api.db',media_root=tmp_path/'media')) as app:
        response=app.post('/api/studio/model-routes/default/test',json={'mode':'context','context_bytes':100000})
        assert response.status_code==202
        for _ in range(100):
            job=app.get('/api/jobs/'+response.json()['job_id']).json()
            if job['status']!='running':break
            time.sleep(.01)
        assert job['status']=='done',job
        assert job['meta']['context_check']['passed']
        assert job['meta']['request_text_bytes']==100000
        assert len(seen)==2
        assert seen[0]['reasoning_effort']==''
        assert app.post('/api/studio/model-routes/default/test',json={'mode':'context','context_bytes':1000000}).status_code==422
