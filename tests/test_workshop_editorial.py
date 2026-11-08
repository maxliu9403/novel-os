import json
import re
from dataclasses import replace

from fastapi import HTTPException
import pytest

from api import workshop
from api.services import ProjectService
from scripts.revise_workshop_skeleton import revise, digest
from test_workshop_api import client, ready, finish, model_result


def setup_revision(client, monkeypatch, tmp_path):
    data = ready(client, monkeypatch, tmp_path)
    service = workshop.WorkshopService(ProjectService(tmp_path / 'projects'))
    candidate = data['prompt_md'].replace('## 14. 文风与正文创作规范',
        '## 14. 文风与正文创作规范\n\n关键关系变化通过现场反应展开，避免只报告结果。')
    args = dict(revision=data['revision'], source_sha256=digest(data['prompt_md']),
                prompt=candidate, note='作者确认局部编辑，不改变故事选择。')
    return service, data, args


def test_preview_apply_history_stale_preparation_and_idempotency(client, monkeypatch, tmp_path):
    service, before, args = setup_revision(client, monkeypatch, tmp_path)
    monkeypatch.setattr(workshop, '_complete', lambda *_: pytest.fail('No model call'))
    with service._locked(before['id']) as data:
        data['prepared'] = {'old': 'command must expire'}
        service._save(data)
    path = service._path(before['id'])
    original = (path / 'session.json').read_bytes()
    result = revise(service, before['id'], **args)
    assert result['preview'] is True
    assert (path / 'session.json').read_bytes() == original
    result = revise(service, before['id'], **args, apply=True)
    after = json.loads((path / 'session.json').read_text())
    assert after['revision'] == before['revision'] + 1
    assert after['prompt_md'] == args['prompt']
    assert after['prepared'] is None and after['project_id'] is None
    assert after['delivery_run_id'] is None
    assert after['creative_design'] == before['creative_design']
    assert after['input'] == before['input']
    assert (path / 'versions' / f"{before['revision']}.md").read_text() == before['prompt_md']
    assert (path / 'versions' / f"{after['revision']}.md").read_text() == args['prompt']
    assert (path / 'history' / f"editorial-{result['request_sha256']}.json").read_bytes() == original
    committed = (path / 'session.json').read_bytes()
    assert revise(service, before['id'], **args, apply=True)['already_applied']
    assert (path / 'session.json').read_bytes() == committed


@pytest.mark.parametrize('mutation', ['revision', 'hash', 'running', 'launched', 'pending', 'invalid'])
def test_conflicts_never_replace_author_source(client, monkeypatch, tmp_path, mutation):
    service, before, args = setup_revision(client, monkeypatch, tmp_path)
    with service._locked(before['id']) as data:
        if mutation == 'revision': args['revision'] -= 1
        elif mutation == 'hash': args['source_sha256'] = '0' * 64
        elif mutation in {'running', 'launched'}: data['status'] = mutation
        elif mutation == 'pending': data['input_needs_revision'] = True
        elif mutation == 'invalid': args['prompt'] = args['prompt'].replace('## 12.', '## 99.')
        service._save(data)
    path = service._path(before['id']) / 'session.json'
    original = path.read_bytes()
    with pytest.raises((ValueError, HTTPException, workshop.ModelResponseError)):
        revise(service, before['id'], **args, apply=True)
    assert path.read_bytes() == original


def test_word_change_requires_confirmation_and_updates_input_atomically(client, monkeypatch, tmp_path):
    from core.narrative_format import parse_narrative_format_block
    service, before, args = setup_revision(client, monkeypatch, tmp_path)
    contract = parse_narrative_format_block(before['prompt_md'])
    old = contract.target_words
    new = contract.total_chapters * 1500
    assert new != old
    revised = replace(contract, target_words=new,
                      volumes=(replace(contract.volumes[0], target_words=new),))
    args['prompt'] = re.sub(r'\[NARRATIVE_FORMAT_JSON\].*?\[/NARRATIVE_FORMAT_JSON\]',
        lambda _: '[NARRATIVE_FORMAT_JSON]\n' + json.dumps(revised.to_dict()) + '\n[/NARRATIVE_FORMAT_JSON]',
        args['prompt'].replace(f'Words: {old}', f'Words: {new}'), flags=re.S)
    with pytest.raises(ValueError, match='author-confirmed'):
        revise(service, before['id'], **args, apply=True)
    with pytest.raises(ValueError, match='disagree'):
        revise(service, before['id'], **args, words_per_chapter=999, apply=True)
    revise(service, before['id'], **args, words_per_chapter=1500, apply=True)
    after = json.loads((service._path(before['id']) / 'session.json').read_text())
    assert after['input']['words_per_chapter'] == 1500
    assert parse_narrative_format_block(after['prompt_md']).target_words == new


def test_future_delivery_cannot_reuse_failed_pre_edit_candidate(client, monkeypatch, tmp_path):
    service, before, args = setup_revision(client, monkeypatch, tmp_path)
    root = service._path(before['id'])
    (root / 'candidates' / f"{before['revision']}.md").write_text('obsolete failed skeleton')
    result = revise(service, before['id'], **args, apply=True)
    captured = []
    def complete(snapshot, _):
        captured.append(snapshot)
        return model_result(args['prompt'])
    monkeypatch.setattr(workshop, '_complete', complete)
    response = client.post(f"/api/workshop/sessions/{before['id']}/turns", json={
        'revision': result['new_revision'], 'message': '请交付当前完整骨架', 'intent': 'deliver'})
    assert response.status_code == 202
    after = finish(client, before['id'])
    assert after['status'] == 'ready'
    assert len(captured) == 1 and '_previous_candidate' not in captured[0]
    assert captured[0]['prompt_md'] == args['prompt']


def test_interrupted_commit_reuses_original_backup_without_overwrite(client, monkeypatch, tmp_path):
    service, before, args = setup_revision(client, monkeypatch, tmp_path)
    path = service._path(before['id']) / 'session.json'
    original = path.read_bytes()
    save = service._save
    monkeypatch.setattr(service, '_save', lambda _: (_ for _ in ()).throw(OSError('disk failure')))
    with pytest.raises(OSError):
        revise(service, before['id'], **args, apply=True)
    assert path.read_bytes() == original
    monkeypatch.setattr(service, '_save', save)
    result = revise(service, before['id'], **args, apply=True)
    assert (path.parent / 'history' / f"editorial-{result['request_sha256']}.json").read_bytes() == original
