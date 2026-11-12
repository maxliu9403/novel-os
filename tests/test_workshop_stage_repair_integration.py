"""Replay envelope failures through real delivery orchestration and durable recovery."""
from copy import deepcopy
import json

import pytest

from api import workshop
from api.workshop_delivery import DeliveryError
from core.llm_client import LLMCancelledError
from test_story_first_engine import design  # noqa: F401
from test_workshop_api import client, create, finish  # noqa: F401
from test_workshop_contract_repair import stub_model, submit


def prepared(design, tmp_path):
    from api.workshop_design import new_design
    from api.workshop_stage_repair import load_repair, save_repair
    session, story, chapters = deepcopy(design)
    session.update(id='a' * 32, delivery_run_id='b' * 32, delivery_source_revision=1)
    session['creative_design'] = new_design(session['input'], guided=False)
    session['messages'] = [
        {'role': 'user', 'content': '保留门口夺权、女儿直问和离婚后的亲吻叫停，不能改结局。'},
        {'role': 'assistant', 'content': '这只是建议，并非作者已采用的新事实。'},
        {'role': 'user', 'content': '只调整篇幅，完整保留前面的故事。'}]
    directory = tmp_path / 'session'
    directory.mkdir()
    replies = directory / 'stage-responses'
    replies.mkdir()
    session['_save_delivery_response'] = lambda record: workshop._atomic(
        replies / f"{record['revision']}-{record['attempt']}.json",
        json.dumps(record, ensure_ascii=False))
    records = []
    session['_save_delivery_checkpoint'] = lambda record: records.append(deepcopy(record))
    session['_save_stage_repair'] = lambda record: save_repair(directory, record)
    session['_load_stage_repair'] = lambda stage, chapter_range=None: load_repair(
        directory, session, stage=stage, chapter_range=chapter_range)
    return session, story, chapters, records, directory


def test_complete_candidate_uses_only_metadata_patch_and_preserves_author_history(design, tmp_path, monkeypatch):
    session, story, chapters, records, directory = prepared(design, tmp_path)
    broken = {**story, 'narrative_mode': 'single_volume'}
    original_messages = deepcopy(session['messages'])

    def respond(attempt, system, user):
        context = json.loads(user)
        if attempt == 1:
            assert all(value in system for value in ('short_novel', 'standalone_long', 'multi_volume', 'series_installment'))
            return json.dumps(broken)
        if attempt == 2:
            from api.workshop_stage_repair import load_repair
            retained = load_repair(directory, session, stage='story_design')
            assert retained is not None and retained.candidate == broken and retained.saved_patch is None
            assert context['operation'] == 'repair_stage_envelope'
            assert context['candidate'] == broken
            assert context['author_context']['conversation'] == original_messages
            assert context['author_context']['initial_brief'] == session['input']
            assert [issue['field'] for issue in context['issues']] == ['narrative_mode']
            return json.dumps({'stage_envelope': {'narrative_mode': 'short_novel'}})
        assert attempt == 3
        assert context['delivery_stage'] == 'chapter_plan'
        assert context['saved_story_stages']['story_design'] == story
        assert 'previous_model_response' not in context
        return json.dumps(chapters)

    calls = stub_model(monkeypatch, respond)
    result, _ = workshop._complete(session, 'Story design')
    assert len(calls) == 3
    assert result['prompt_md'].startswith('<!-- story-first.v1 -->')
    assert records[-1]['completed']['story_design']['content'] == story
    assert session['messages'] == original_messages


def test_saved_metadata_patch_recovers_after_crash_before_stage_checkpoint(design, tmp_path, monkeypatch):
    from api.workshop_delivery import parse_stage, StageEnvelopeResponseError
    from api.workshop_stage_repair import StageEnvelopeRepair, save_repair
    session, story, chapters, records, directory = prepared(design, tmp_path)
    broken = {**story, 'narrative_mode': 'single_volume'}
    with pytest.raises(StageEnvelopeResponseError) as caught:
        parse_stage(json.dumps(broken), 'story_design', None, story_first=True)
    item = StageEnvelopeRepair(broken, caught.value.issues, stage='story_design')
    save_repair(directory, item.record(session))
    item.accept(json.dumps({'stage_envelope': {'narrative_mode': 'short_novel'}}))
    save_repair(directory, item.record(session))

    def respond(attempt, _system, user):
        context = json.loads(user)
        assert attempt == 1 and context['delivery_stage'] == 'chapter_plan'
        assert context['saved_story_stages']['story_design'] == story
        return json.dumps(chapters)

    calls = stub_model(monkeypatch, respond)
    result, _ = workshop._complete(session, 'Story design')
    assert len(calls) == 1 and result['ready_for_confirmation']
    assert records[-1]['completed']['story_design']['content'] == story


@pytest.mark.parametrize('patch', [
    {'stage_envelope': {'narrative_mode': 'short_novel'}, 'story_design': {'ending': 'Changed ending'}},
    {'stage_envelope': {'narrative_mode': 'short_novel', 'total_chapters': 10}},
    {'stage_envelope': {'narrative_mode': 'standalone'}},
])
def test_repeated_bad_patches_never_replace_frozen_candidate_or_save_story(design, tmp_path, monkeypatch, patch):
    session, story, _, records, directory = prepared(design, tmp_path)
    broken = {**story, 'narrative_mode': 'single_volume'}

    def respond(attempt, _system, user):
        if attempt > 1:
            context = json.loads(user)
            assert context['operation'] == 'repair_stage_envelope'
            assert context['candidate'] == broken
        return json.dumps(broken if attempt == 1 else patch)

    calls = stub_model(monkeypatch, respond)
    with pytest.raises(DeliveryError):
        workshop._complete(session, 'Story design')
    assert len(calls) == 3
    assert all(not value['completed'] for value in records)
    from api.workshop_stage_repair import load_repair
    saved = load_repair(directory, session, stage='story_design')
    assert saved is not None and saved.candidate == broken and saved.saved_patch is None


def test_interrupted_metadata_repair_resumes_original_candidate_without_regeneration(design, tmp_path, monkeypatch):
    session, story, chapters, records, _ = prepared(design, tmp_path)
    broken = {**story, 'narrative_mode': 'single_volume'}

    def initial(attempt, _system, user):
        if attempt == 1:
            return json.dumps(broken)
        assert json.loads(user)['operation'] == 'repair_stage_envelope'
        raise TimeoutError('Test interrupt after paid generation')

    first_calls = stub_model(monkeypatch, initial)
    with pytest.raises(TimeoutError):
        workshop._complete(session, 'Story design')
    assert len(first_calls) == 2
    assert all(not value['completed'] for value in records)
    # The service gives an explicit resume a new revision, retaining the same
    # source revision/run. Do not overwrite the original reply's filename.
    session['revision'] += 1

    def resumed(attempt, _system, user):
        context = json.loads(user)
        if attempt == 1:
            assert context['operation'] == 'repair_stage_envelope'
            assert context['candidate'] == broken
            return json.dumps({'stage_envelope': {'narrative_mode': 'short_novel'}})
        assert attempt == 2 and context['delivery_stage'] == 'chapter_plan'
        return json.dumps(chapters)

    second_calls = stub_model(monkeypatch, resumed)
    result, _ = workshop._complete(session, 'Story design')
    assert len(second_calls) == 2 and result['ready_for_confirmation']
    assert records[-1]['completed']['story_design']['content'] == story


@pytest.mark.parametrize('failed_save', [1, 2])
def test_storage_failure_never_purchases_another_model_request(design, tmp_path, monkeypatch, failed_save):
    from api.workshop_stage_repair import StageRepairStorageError
    session, story, _, records, _ = prepared(design, tmp_path)
    broken = {**story, 'narrative_mode': 'single_volume'}
    actual_save = session['_save_stage_repair']
    save_count = 0

    def failed(record):
        nonlocal save_count
        save_count += 1
        if save_count == failed_save:
            raise StageRepairStorageError('Test storage failure')
        actual_save(record)

    session['_save_stage_repair'] = failed
    calls = stub_model(monkeypatch, lambda n, *_: json.dumps(
        broken if n == 1 else {'stage_envelope': {'narrative_mode': 'short_novel'}}))
    with pytest.raises(StageRepairStorageError, match='storage failure'):
        workshop._complete(session, 'Story design')
    assert len(calls) == failed_save
    assert all(not value['completed'] for value in records)


def test_103_chapter_confirmed_capacity_survives_mode_repair_and_enters_chapter_windows(design, tmp_path, monkeypatch):
    from core.story_capacity import contract
    from test_story_capacity import capacity_plan, QUOTE
    session, story, _, records, _ = prepared(design, tmp_path)
    session['input'].update(chapters=103, words_per_chapter=800, target_total_words=82300, premise=QUOTE)
    plan = capacity_plan(103, 800)
    plan['options'][0]['chapters'][35]['target_words'] = 700
    plan['scenes'][35]['minimum'] = 700
    plan['options'][0]['total_words'] = 82300
    allocation = contract(plan, 'keep_story', 'c' * 64)
    session['creative_design']['policy']['capacity_version'] = 'story-capacity.v1'
    session['capacity'] = {'schema': 'story-capacity.v1', 'status': 'confirmed', 'confirmed': allocation}
    story['total_chapters'] = 103
    broken = {**story, 'narrative_mode': 'single_volume'}

    def respond(attempt, _system, user):
        context = json.loads(user)
        if attempt == 1:
            return json.dumps(broken)
        if attempt == 2:
            assert context['operation'] == 'repair_stage_envelope'
            assert context['candidate']['total_chapters'] == 103
            assert context['author_context']['story_capacity']['confirmed'] == allocation
            return json.dumps({'stage_envelope': {'narrative_mode': 'short_novel'}})
        assert attempt == 3 and context['delivery_stage'] == 'chapter_plan'
        assert context['saved_story_stages']['story_design'] == story
        assert context['chapter_window'] and context['story_capacity']['confirmed'] == allocation
        raise LLMCancelledError('Stop before chapter generation in this isolated test')

    calls = stub_model(monkeypatch, respond)
    with pytest.raises(LLMCancelledError):
        workshop._complete(session, 'Story design')
    assert len(calls) == 3
    assert records[-1]['completed']['story_design']['content'] == story
    assert session['capacity']['confirmed'] == allocation


@pytest.mark.parametrize('interrupt_format', [False, True])
def test_metadata_then_nested_format_repair_keeps_story_and_resumes(design, tmp_path, monkeypatch, interrupt_format):
    from test_workshop_narrative_response import complex_design
    session, story, chapters, records, _ = prepared(complex_design(design), tmp_path)
    broken = deepcopy(story)
    broken['narrative_mode'] = 'single_volume'
    broken['narrative_format']['volumes'][0].pop('structural_role')

    def initial(attempt, _system, user):
        context = json.loads(user)
        if attempt == 1:
            return json.dumps(broken)
        if attempt == 2:
            assert context['operation'] == 'repair_stage_envelope'
            return json.dumps({'stage_envelope': {'narrative_mode': 'multi_volume'}})
        if attempt == 3:
            assert context['operation'] == 'repair_narrative_format'
            assert context['story_design'] == story['story_design']
            if interrupt_format:
                raise TimeoutError('Nested format repair interrupted after metadata saved')
            return json.dumps({'narrative_format': story['narrative_format']})
        assert attempt == 4 and context['delivery_stage'] == 'chapter_plan'
        return json.dumps(chapters)

    first_calls = stub_model(monkeypatch, initial)
    if interrupt_format:
        with pytest.raises(TimeoutError):
            workshop._complete(session, 'Story design')
        assert len(first_calls) == 3
        session['revision'] += 1

        def resumed(attempt, _system, user):
            context = json.loads(user)
            if attempt == 1:
                assert context['operation'] == 'repair_narrative_format'
                assert context['story_design'] == story['story_design']
                return json.dumps({'narrative_format': story['narrative_format']})
            assert attempt == 2 and context['delivery_stage'] == 'chapter_plan'
            return json.dumps(chapters)

        calls = stub_model(monkeypatch, resumed)
        result, _ = workshop._complete(session, 'Story design')
        assert len(calls) == 2
    else:
        result, _ = workshop._complete(session, 'Story design')
        assert len(first_calls) == 4
    assert result['ready_for_confirmation']
    assert records[-1]['completed']['story_design']['content'] == story


@pytest.mark.parametrize('legacy', [False, True])
def test_service_resume_reuses_retained_stage_and_get_never_starts_repair(client, design, tmp_path, monkeypatch, legacy):
    _, story, chapters = design
    broken = {**story, 'narrative_mode': 'single_volume'}

    def initial(attempt, _system, user):
        if attempt == 1:
            return json.dumps(broken)
        assert json.loads(user)['operation'] == 'repair_stage_envelope'
        raise TimeoutError('Interrupt after the stage was durably saved')

    first = stub_model(monkeypatch, initial)
    data = create(client, mode='short_novel', chapters=2, words_per_chapter=800, language='English')
    submit(client, data, '保留先前的情绪回报和结局，生成完整骨架。')
    failed = finish(client, data['id'])
    assert failed['status'] == 'error', failed.get('last_error')
    assert len(first) == 2
    directory = tmp_path / 'workshop_v2' / data['id']
    if legacy:
        # A pre-upgrade execution retained its complete raw reply but had no
        # durable field-repair record. Explicit resume must import that reply.
        import shutil
        shutil.rmtree(directory / 'stage-repairs')
    originals = {path: path.read_bytes() for folder in ('stage-responses', 'stage-repairs')
                 for path in (directory / folder).glob('*.json')}
    assert len(originals) >= (1 if legacy else 2)
    for _ in range(2):
        viewed = client.get(f'/api/workshop/sessions/{data["id"]}')
        assert viewed.status_code == 200
    assert len(first) == 2
    assert all(path.read_bytes() == value for path, value in originals.items())
    if legacy:
        assert not (directory / 'stage-repairs').exists()

    def resumed(attempt, _system, user):
        context = json.loads(user)
        if attempt == 1:
            assert context['operation'] == 'repair_stage_envelope'
            assert context['candidate'] == broken
            return json.dumps({'stage_envelope': {'narrative_mode': 'short_novel'}})
        assert attempt == 2 and context['delivery_stage'] == 'chapter_plan'
        return json.dumps(chapters)

    calls = stub_model(monkeypatch, resumed)
    response = client.post(f'/api/workshop/sessions/{data["id"]}/resume', json={'revision': failed['revision']})
    assert response.status_code == 202, response.text
    ready = finish(client, data['id'])
    assert ready['status'] == 'ready', ready.get('last_error')
    assert len(calls) == 2
    assert ready['delivery_run_id'] == failed['delivery_run_id']
    assert ready['messages'][:len(failed['messages'])] == failed['messages']
    assert all(path.read_bytes() == value for path, value in originals.items() if path.parent.name == 'stage-responses')
