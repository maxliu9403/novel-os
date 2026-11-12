from copy import deepcopy
import hashlib
import json

import pytest

from api.workshop_delivery import DeliveryError, StageEnvelopeResponseError, parse_stage
from api.workshop_stage_repair import StageEnvelopeRepair, StageRepairError, StageRepairStorageError, import_legacy_repair, import_matching_legacy_repair, load_repair, save_repair


@pytest.fixture
def case():
    session = {'id': 'a' * 32, 'revision': 15, 'delivery_run_id': 'b' * 32,
               'delivery_source_revision': 15, 'skill_version': 'test',
               'input': {'chapters': 103, 'words_per_chapter': 800, 'mode': ''},
               'messages': [{'role': 'user', 'content': 'Keep every emotional payoff.'}],
               'questions': [], 'prompt_md': ''}
    candidate = {'narrative_mode': 'single_volume', 'total_chapters': 103,
                 'story_design': {key: key + '完整原文' for key in
                   ('title', 'author', 'premise', 'characters', 'world_rules', 'causal_chain',
                    'ending', 'decisions', 'voice', 'setups_payoffs')}}
    with pytest.raises(StageEnvelopeResponseError) as error:
        parse_stage(json.dumps(candidate), 'story_design', None, 103, story_first=True)
    repair = StageEnvelopeRepair(candidate, error.value.issues, stage='story_design', chapter_count=103)
    return session, candidate, repair


def patch(mode='standalone_long'):
    return json.dumps({'stage_envelope': {'narrative_mode': mode}})


def test_repairs_only_metadata_and_preserves_complete_story(case):
    session, candidate, repair = case
    before = deepcopy(candidate)
    system, user = repair.request({'complete_author_input': session})
    request = json.loads(user)
    assert request['candidate'] == before
    assert request['author_context']['complete_author_input'] == session
    assert 'not chapter count alone' in system
    result = json.loads(repair.accept(patch()))
    assert result['story_design'] == before['story_design']
    assert candidate == before and repair.candidate == before
    assert parse_stage(json.dumps(result), 'story_design', None, 103, story_first=True) == result
    assert repair.saved_result() == repair.merge(patch())


@pytest.mark.parametrize('reply', [
    {'stage_envelope': {'narrative_mode': 'standalone_long', 'total_chapters': 103}},
    {'stage_envelope': {'narrative_mode': 'standalone_long'}, 'story_design': {}},
    {'stage_envelope': {'story_design': 'replacement'}},
    {'stage_envelope': {}}, {'stage_envelope': {'narrative_mode': 'standalone'}},
    {'narrative_mode': 'standalone_long'}, {'stage_envelope': []},
])
def test_patch_cannot_widen_scope_or_accept_aliases(case, reply):
    _, candidate, repair = case
    with pytest.raises(DeliveryError):
        repair.accept(json.dumps(reply))
    assert repair.saved_patch is None and repair.candidate == candidate


def test_duplicate_patch_fields_rejected(case):
    with pytest.raises(StageRepairError, match='字段唯一'):
        case[2].merge('{"stage_envelope":{"narrative_mode":"short_novel","narrative_mode":"standalone_long"}}')


def test_pause_before_and_after_repair_reuses_candidate_and_result(case, tmp_path):
    session, candidate, repair = case
    save_repair(tmp_path, repair.record(session))
    resumed = {**session, 'revision': 16, 'job_id': 'new-job'}
    loaded = load_repair(tmp_path, resumed, stage='story_design')
    assert loaded.candidate == candidate and loaded.saved_result() is None
    loaded.accept(patch())
    save_repair(tmp_path, loaded.record(resumed))
    again = load_repair(tmp_path, resumed, stage='story_design')
    assert json.loads(again.saved_result())['story_design'] == candidate['story_design']
    with pytest.raises(StageRepairError, match='不能被替换'):
        again.accept(patch('short_novel'))


@pytest.mark.parametrize('change', ['input', 'author', 'skill', 'run', 'source', 'session'])
def test_saved_repair_cannot_cross_context_or_lineage(case, tmp_path, change):
    session, _, repair = case
    save_repair(tmp_path, repair.record(session))
    modified = deepcopy(session)
    if change == 'input': modified['input']['words_per_chapter'] = 1000
    elif change == 'author': modified['messages'][0]['content'] = 'New ending'
    elif change == 'skill': modified['skill_version'] = 'new-skill'
    elif change == 'run': modified['delivery_run_id'] = 'c' * 32
    elif change == 'source': modified['delivery_source_revision'] = 14
    else: modified['id'] = 'c' * 32
    if change == 'run':
        assert load_repair(tmp_path, modified, stage='story_design') is None
    else:
        with pytest.raises(StageRepairStorageError):
            load_repair(tmp_path, modified, stage='story_design')


def test_storage_preserves_original_candidate_and_rejects_tampering(case, tmp_path):
    session, _, repair = case
    record = repair.record(session)
    save_repair(tmp_path, record)
    changed = deepcopy(record)
    changed['candidate']['story_design']['ending'] = 'different'
    with pytest.raises(StageRepairStorageError, match='完整性'):
        save_repair(tmp_path, changed)
    path = next((tmp_path / 'stage-repairs').glob('*.json'))
    path.write_text(json.dumps(changed))
    with pytest.raises(StageRepairStorageError, match='完整性'):
        load_repair(tmp_path, session, stage='story_design')


def test_reads_have_no_side_effects_and_symlinks_are_refused(case, tmp_path):
    session, _, repair = case
    assert load_repair(tmp_path, session, stage='story_design') is None
    assert not (tmp_path / 'stage-repairs').exists()
    outside = tmp_path / 'outside'; outside.mkdir()
    (tmp_path / 'stage-repairs').symlink_to(outside, target_is_directory=True)
    with pytest.raises(StageRepairStorageError, match='unsafe'):
        save_repair(tmp_path, repair.record(session))
    assert not list(outside.iterdir())


def test_saved_source_receipt_remains_required_and_unchanged(case, tmp_path):
    session, candidate, repair = case
    raw = json.dumps(candidate, ensure_ascii=False)
    receipt = {'schema_version': 'workshop.stage-response.v1', 'session_id': session['id'],
               'run_id': session['delivery_run_id'], 'revision': 15, 'attempt': 3,
               'stage': 'story_design', 'part': None, 'job_id': 'original-job',
               'system_sha256': 'd'*64, 'user_sha256': 'e'*64,
               'raw': raw, 'response_sha256': hashlib.sha256(raw.encode()).hexdigest(),
               'response_bytes': len(raw.encode())}
    directory = tmp_path / 'stage-responses'; directory.mkdir()
    path = directory / '15-3.json'; path.write_text(json.dumps(receipt))
    original = path.read_bytes()
    save_repair(tmp_path, repair.record(session, receipt))
    assert load_repair(tmp_path, session, stage='story_design').candidate == candidate
    assert path.read_bytes() == original
    path.unlink()
    with pytest.raises(StageRepairStorageError, match='原回复'):
        load_repair(tmp_path, session, stage='story_design')


def test_multi_volume_repair_does_not_skip_full_stage_validation(case):
    _, candidate, repair = case
    result = repair.accept(patch('multi_volume'))
    assert json.loads(result)['story_design'] == candidate['story_design']
    with pytest.raises(DeliveryError, match='分卷'):
        parse_stage(result, 'story_design', None, 103, story_first=True)


def test_invalid_patch_does_not_replace_original_issues(case):
    _, candidate, repair = case
    issues = deepcopy(repair.issues)
    with pytest.raises(StageEnvelopeResponseError):
        repair.accept(patch('another_alias'))
    assert repair.candidate == candidate and repair.issues == issues and repair.saved_patch is None


def test_resolved_repair_cannot_resurrect_old_candidate(case, tmp_path):
    session, _, repair = case
    save_repair(tmp_path, repair.record(session))
    old = repair.record(session)
    save_repair(tmp_path, repair.record(session, status='resolved'))
    assert load_repair(tmp_path, session, stage='story_design') is None
    with pytest.raises(StageRepairStorageError, match='重新启用'):
        save_repair(tmp_path, old)


def legacy_receipt(case, tmp_path, *, attempt=3, **changes):
    session, candidate, _ = case
    session.update(status='error', turn_intent='deliver', job_id='original-job')
    raw = json.dumps(candidate, ensure_ascii=False)
    receipt = {'schema_version': 'workshop.stage-response.v1', 'session_id': session['id'],
               'run_id': session['delivery_run_id'], 'revision': 15, 'attempt': attempt,
               'stage': 'story_design', 'part': None, 'job_id': session['job_id'],
               'system_sha256': 'd'*64, 'user_sha256': 'e'*64,
               'raw': raw, 'response_sha256': hashlib.sha256(raw.encode()).hexdigest(),
               'response_bytes': len(raw.encode()), **changes}
    directory = tmp_path / 'stage-responses'; directory.mkdir(exist_ok=True)
    path = directory / f'15-{attempt}.json'; path.write_text(json.dumps(receipt))
    return path


def test_legacy_import_requires_reconstructed_request_and_changes_no_receipts(case, tmp_path):
    path = legacy_receipt(case, tmp_path)
    before = path.read_bytes()
    imported = import_legacy_repair(tmp_path, case[0], expected_user_sha256='e'*64, chapter_count=103)
    assert imported.candidate == case[1]
    assert path.read_bytes() == before and not (tmp_path / 'stage-repairs').exists()
    save_repair(tmp_path, imported.record(case[0]))
    assert load_repair(tmp_path, case[0], stage='story_design').candidate == case[1]


@pytest.mark.parametrize('change', ['request', 'revision', 'job', 'run', 'digest'])
def test_legacy_import_does_not_guess_stale_or_unverified_lineage(case, tmp_path, change):
    changes = {'revision': {'revision': 14}, 'job': {'job_id': 'wrong'},
               'run': {'run_id': 'c'*32}, 'digest': {'response_sha256': 'f'*64}}
    legacy_receipt(case, tmp_path, **changes.get(change, {}))
    with pytest.raises(StageRepairStorageError):
        import_legacy_repair(tmp_path, case[0], expected_user_sha256=('f' if change == 'request' else 'e')*64)


def test_legacy_import_never_falls_back_to_older_reply(case, tmp_path):
    legacy_receipt(case, tmp_path, attempt=1)
    legacy_receipt(case, tmp_path, attempt=3, user_sha256='f'*64)
    with pytest.raises(StageRepairStorageError, match='当前完整作者依据'):
        import_legacy_repair(tmp_path, case[0], expected_user_sha256='e'*64)


def test_new_candidate_after_resolution_archives_old_reply(case, tmp_path):
    session, candidate, repair = case
    repair.accept(patch())
    old = repair.record(session, status='resolved')
    save_repair(tmp_path, old)
    changed = deepcopy(candidate)
    changed['story_design']['ending'] = 'Complete corrected ending; all other details retained.'
    following = StageEnvelopeRepair(changed, repair.issues, stage='story_design', chapter_count=103)
    save_repair(tmp_path, following.record(session))
    assert load_repair(tmp_path, session, stage='story_design').candidate == changed
    archives = list((tmp_path / 'stage-repairs').glob('*.history-*.json'))
    assert len(archives) == 1 and json.loads(archives[0].read_text()) == old


def test_archive_commits_before_replacing_active_record(case, tmp_path, monkeypatch):
    import api.workshop_stage_repair as module
    session, candidate, repair = case
    old = repair.record(session, status='resolved')
    save_repair(tmp_path, old)
    changed = deepcopy(candidate); changed['story_design']['ending'] += '更完整'
    following = StageEnvelopeRepair(changed, repair.issues, stage='story_design', chapter_count=103)
    original_write = module._write
    def fail_pointer(fd, name, payload):
        if '.history-' not in name:
            raise OSError('simulated interruption')
        return original_write(fd, name, payload)
    monkeypatch.setattr(module, '_write', fail_pointer)
    with pytest.raises(StageRepairStorageError):
        save_repair(tmp_path, following.record(session))
    assert load_repair(tmp_path, session, stage='story_design') is None
    assert len(list((tmp_path / 'stage-repairs').glob('*.history-*.json'))) == 1
    monkeypatch.setattr(module, '_write', original_write)
    save_repair(tmp_path, following.record(session))
    assert load_repair(tmp_path, session, stage='story_design').candidate == changed


def test_storage_errors_are_never_model_validation_errors(case, tmp_path):
    session, _, repair = case
    save_repair(tmp_path, repair.record(session))
    session['input']['chapters'] = 104
    with pytest.raises(StageRepairStorageError) as error:
        load_repair(tmp_path, session, stage='story_design')
    assert not isinstance(error.value, DeliveryError)


def captured_legacy_chain(case, tmp_path, *, source=False):
    from api.workshop_delivery import author_context
    from api.workshop_request import delivery_context
    from api.workshop_stage_repair import _LEGACY_REPAIR_INSTRUCTION
    from core.creative_types import digest
    from core.source_route_context import make_packet, save_packet, read_packet
    session, candidate, _ = case
    session.update(status='error', turn_intent='deliver', job_id='original-job')
    packet = None
    if source:
        packet = make_packet({'text': 'Complete source story and unresolved emotional promise.'},
            source_id='source-'+'a'*64, source_sha256='b'*64, understanding_sha256='c'*64)
        context = {'schema':'source-route-context.v1', 'source_id':packet['source_id'],
            'source_sha256':packet['source_sha256'], 'understanding_sha256':packet['understanding_sha256'],
            'route_set_sha256':'d'*64, 'mode':'source_development',
            'selected_proposal':{'sha256':digest({})}, 'evidence':save_packet(tmp_path,packet)}
        context['context_sha256'] = digest(context)
        session['source_context'] = context
        packet = read_packet(tmp_path,context)
    previous = None
    paths = []
    for attempt in (1,2,3):
        context = author_context(session)
        if previous:
            context.update(previous_model_response=previous['raw'], validation_error=previous['validation']['detail'],
                           repair_instruction=_LEGACY_REPAIR_INSTRUCTION)
        context.update(delivery_stage='story_design',delivery_mode=None,saved_story_stages={})
        user = json.dumps(delivery_context(context),ensure_ascii=False,separators=(',',':'))
        system = 'Original complete story design contract'
        path = legacy_receipt(case,tmp_path,attempt=attempt,
            user_sha256=hashlib.sha256(user.encode()).hexdigest(),
            system_sha256=hashlib.sha256(system.encode()).hexdigest(),
            validation={'status':'invalid','detail':f'Preserved old format issue {attempt}.'})
        previous = json.loads(path.read_text()); paths.append(path)
        if packet:
            full = user+'\n\n[SOURCE_EVIDENCE]\n'+json.dumps(packet,ensure_ascii=False,separators=(',',':'))+'\n[/SOURCE_EVIDENCE]'
            group = digest({'system':system,'user':user,'evidence':packet['sha256']})
            request_id = digest({'system':system,'user':full})
            directory = tmp_path/'source-evidence'/'requests'/group;directory.mkdir(parents=True,exist_ok=True)
            (directory/f'{request_id}.json').write_text(json.dumps({'system':system,'user':full,
                'state':'received','response':previous['raw'],'response_sha256':previous['response_sha256']}))
    return paths


@pytest.mark.parametrize('source',[False,True])
def test_legacy_request_chain_matches_complete_current_context_without_writes(case,tmp_path,source):
    captured_legacy_chain(case,tmp_path,source=source)
    before = {str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    imported = import_matching_legacy_repair(tmp_path,case[0])
    assert imported is not None and imported.candidate == case[1]
    assert before == {str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert not (tmp_path/'stage-repairs').exists()


@pytest.mark.parametrize('change',['author','capacity','missing_previous','previous_error','latest_hash','other_job'])
def test_legacy_request_chain_refuses_unproven_current_context(case,tmp_path,change):
    paths = captured_legacy_chain(case,tmp_path)
    if change == 'author': case[0]['messages'][0]['content'] += ' Different ending.'
    elif change == 'capacity': case[0]['input']['words_per_chapter'] = 1200
    elif change == 'missing_previous': paths[1].unlink()
    else:
        p = paths[1] if change == 'previous_error' else paths[-1]
        r = json.loads(p.read_text())
        if change == 'previous_error': r['validation']['detail'] = 'Different preceding correction'
        elif change == 'latest_hash': r['user_sha256'] = 'f'*64
        else: r['job_id'] = 'another-job'
        p.write_text(json.dumps(r))
    assert import_matching_legacy_repair(tmp_path,case[0]) is None


@pytest.mark.parametrize('change',['source_text','missing_response','extra_source','source_alias'])
def test_legacy_route_recovery_requires_original_full_evidence_receipt(case,tmp_path,change):
    captured_legacy_chain(case,tmp_path,source=True)
    records = sorted((tmp_path/'source-evidence'/'requests').glob('*/*.json'))
    p = records[0]; r = json.loads(p.read_text())
    if change == 'source_text': r['user'] = r['user'].replace('Complete source story','Different source story')
    elif change == 'missing_response': r.pop('response')
    elif change == 'extra_source': r['user'] += '\nUnexpected instructions'
    else:
        p.rename(p.with_suffix('.saved'))
        p.symlink_to(p.with_suffix('.saved'))
        with pytest.raises(StageRepairStorageError):
            import_matching_legacy_repair(tmp_path,case[0])
        return
    p.write_text(json.dumps(r))
    assert import_matching_legacy_repair(tmp_path,case[0]) is None


def test_legacy_helper_skips_unrelated_failure_without_requiring_source_receipts(case,tmp_path):
    paths = captured_legacy_chain(case,tmp_path,source=True)
    latest = json.loads(paths[-1].read_text()); candidate = json.loads(latest['raw'])
    candidate['narrative_mode'] = 'standalone_long'
    candidate['story_design'] = {}
    latest['raw'] = json.dumps(candidate)
    paths[-1].write_text(json.dumps(latest))
    assert import_matching_legacy_repair(tmp_path,case[0]) is None
