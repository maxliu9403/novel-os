"""Resume chapter provenance binding without discarding issued story responses."""
from copy import deepcopy
import json

import pytest

from core.outline_planning import OutlinePlanningError, _PROJECTION_RULES, _json, _parse_object, content_hash
from core.planning_batch_references import RULES, instruction as reference_instruction
from core.planning_chapter_repair import repair_chapters
from core.planning_detail_batches import build_batch
from core.planning_model_continuation import open_predecessors
from core.planning_roadmap import build_global_design
from core.source_request_session import RequestOutcomeUnknown
from test_planning_batch_reference_binding import (
    NAME, NUMBERS, BatchReferenceModel, legacy_context, planner_for,
    save_legacy_batch, story_without_references,
)
from test_planning_design_recovery import NoNewModel, snapshot
from test_planning_roadmap import STORIES


def _saved_correction(tmp_path):
    model = BatchReferenceModel(STORIES[0], defect='unknown')
    planner = planner_for(tmp_path, model)
    global_design = build_global_design(planner)
    context = legacy_context(planner, global_design)
    original = _parse_object(model.complete('', planner.batch_prompt + '\n\n' + _json(context)))
    latest = deepcopy(original)
    latest['chapters'][0]['summary'] = 'The latest issued correction retains this distinct complete scene.'
    latest['chapters'][0]['bespoke_story_field']['latest_choice'] = ['attachment', 'hesitation', 'cost']
    save_legacy_batch(planner, global_design, original, latest)
    planner.client = NoNewModel()
    dependencies = {'global': content_hash(global_design), 'previous': content_hash([])}
    return planner, global_design, dependencies, latest


def _unchanged(directory, before):
    assert all((directory / relative).read_bytes() == raw for relative, raw in before.items())


@pytest.mark.parametrize('completed', [False, True])
def test_pending_batch_correction_resumes_exact_request_before_binding_old_candidate(tmp_path, monkeypatch, completed):
    planner, global_design, dependencies, latest = _saved_correction(tmp_path)
    correction = NAME + '-format-correction'
    raw_path = planner.cache / 'raw' / (correction + '.legacy.response.txt')
    latest_raw = raw_path.read_text()
    raw_path.unlink()
    (planner.cache / (correction + '-response.json')).unlink()
    frozen_prompt = (planner.cache / 'raw' / (correction + '.legacy.prompt.txt')).read_text()
    before = snapshot(planner.cache)
    response = planner._response
    resumed = []

    def request(name, prompt, *args, **kwargs):
        if name != correction:
            return response(name, prompt, *args, **kwargs)
        assert prompt == frozen_prompt
        assert kwargs['strict_dependencies'] and kwargs['recover_archived']
        resumed.append(name)
        if not completed:
            raise RequestOutcomeUnknown('The original correction still has no confirmed outcome')
        # The shared request-session boundary returns its verified saved reply.
        # A separate native replay covers its receipt and physical attempt I/O.
        return latest_raw

    monkeypatch.setattr(planner, '_response', request)
    if completed:
        result = build_batch(planner, NAME, NUMBERS, global_design, [], dependencies)
        for accepted, expected in zip(result['chapters'], latest['chapters']):
            assert story_without_references(accepted) == story_without_references(expected)
        child = json.loads((planner.cache / (NAME + '-reference-binding-v1.json')).read_text())
        assert child['payload']['source_candidate'] == latest
    else:
        with pytest.raises(RequestOutcomeUnknown, match='no confirmed outcome'):
            build_batch(planner, NAME, NUMBERS, global_design, [], dependencies)
        assert not (planner.cache / (NAME + '-reference-binding-v1.json')).exists()
        assert not (planner.cache / (NAME + '.json')).exists()
    assert resumed == [correction]
    assert planner.client.calls == []
    _unchanged(planner.cache, before)


@pytest.mark.parametrize('damage', ['payload_hash', 'unverified_archive'])
def test_corrupt_batch_correction_cannot_fall_back_to_bindable_original(tmp_path, damage):
    planner, global_design, dependencies, _ = _saved_correction(tmp_path)
    correction = NAME + '-format-correction'
    path = planner.cache / (correction + '-response.json')
    if damage == 'payload_hash':
        record = json.loads(path.read_text())
        record['payload']['raw'] += '\nChanged without updating the saved response hash.'
        path.write_text(json.dumps(record))
    else:
        # A loose archived reply is not a verified completion receipt.
        path.unlink()
        archived = planner.cache / 'raw' / (correction + '.legacy.response.txt')
        archived.write_text(archived.read_text() + '\nUnverified replacement.')
    before = snapshot(planner.cache)
    with pytest.raises(OutlinePlanningError, match='Saved correction'):
        build_batch(planner, NAME, NUMBERS, global_design, [], dependencies)
    assert not planner.client.calls
    assert not (planner.cache / (NAME + '-reference-binding-v1.json')).exists()
    assert not (planner.cache / (NAME + '.json')).exists()
    _unchanged(planner.cache, before)


@pytest.mark.parametrize('reference_version', [1, 2])
def test_historical_source_view_and_prompt_survive_larger_model_route(tmp_path, monkeypatch, reference_version):
    model = BatchReferenceModel(STORIES[0], defect='missing')
    model.model = 'chapter-source-route-A'
    old = planner_for(tmp_path, model)
    global_design = build_global_design(old)
    dependencies = {'global': content_hash(global_design), 'previous': content_hash([])}
    scope_dependencies = {**dependencies, 'requested': NUMBERS, 'protocol': 'detail-bisection.v1'}
    old._save(NAME + '-source-view', scope_dependencies, {'scope': 'requested_and_adjacent'})
    instruction = old.batch_prompt
    if reference_version == 2:
        instruction = reference_instruction(old, NAME, dependencies, NUMBERS, instruction, global_design)
    instruction = instruction.replace('full original skeleton', 'supplied original skeleton') + _PROJECTION_RULES
    context = {**old._context(NUMBERS, project_source=True), 'frozen_global_design': global_design,
               'requested_chapters': NUMBERS, 'previous_completed_chapters': []}
    assert 'complete_adopted_source' not in context
    assert context['adopted_source_projection']['mode'] == 'chapter_selection'
    frozen_prompt = instruction + '\n\n' + _json(context)
    raw = model.complete('', frozen_prompt)
    old._save(NAME + '-response', {'prompt_hash': content_hash(frozen_prompt)}, {'raw': raw})
    raw_dir = old.cache / 'raw'
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / (NAME + '.historical.prompt.txt')).write_text(frozen_prompt)
    (raw_dir / (NAME + '.historical.response.txt')).write_text(raw)
    old._write_json(old.cache / 'identity.json', old.identity)
    old._progress('chapter_batches', 'running', 'A completed response awaits host binding')
    before = snapshot(old.cache)

    client = NoNewModel()
    client.model = 'larger-chapter-source-route-B'
    client.planning_input_bytes = 10_000_000
    continued = planner_for(tmp_path, client)
    continued.predecessors = open_predecessors(continued)
    continued._write_json(continued.cache / 'identity.json', continued.identity)
    replayed = []

    def recover(name, prompt, *args, **kwargs):
        assert name == NAME, 'An existing complete batch must not request a correction'
        assert prompt == frozen_prompt
        assert kwargs['recover_archived']
        replayed.append(prompt)
        return raw

    monkeypatch.setattr(continued, '_response', recover)
    result = build_batch(continued, NAME, NUMBERS, global_design, [], dependencies)
    mapped = {row['number']: row['design_reference_ids'] for row in global_design['chapter_map']}
    for chapter, original in zip(result['chapters'], _parse_object(raw)['chapters']):
        assert chapter['design_reference_ids'] == mapped[chapter['number']]
        assert story_without_references(chapter) == story_without_references(original)
    inherited = json.loads((continued.cache / (NAME + '-source-view.json')).read_text())
    assert inherited['inherited_from']['identity_hash'] == old.identity_hash
    protocol = json.loads((continued.cache / (NAME + '-reference-protocol.json')).read_text())
    assert protocol['payload'] == {'version': reference_version}
    if reference_version == 2:
        assert protocol['inherited_from']['identity_hash'] == old.identity_hash
    assert (RULES in replayed[0]) is (reference_version == 2)
    assert len(replayed) == 1 and client.calls == []
    _unchanged(old.cache, before)


def test_whole_chapter_repair_binds_omitted_ids_once_then_validates_saved_result_strictly(tmp_path):
    model = BatchReferenceModel(STORIES[0], defect='missing')
    planner = planner_for(tmp_path, model)
    candidate = planner.run()
    global_design = json.loads((planner.cache / 'global.json').read_text())['payload']
    targets = [1, 2]
    review = {'status': 'blocked', 'issues': [
        {'scope': 'chapter', 'chapter': 1, 'message': 'Clarify the existing choice and its emotional cost.'}]}
    proposed = {'chapters': deepcopy(candidate['chapters'][:2])}
    for chapter in proposed['chapters']:
        chapter.pop('design_reference_ids')
        chapter['summary'] += ' The repaired scene keeps the cost explicit.'

    class RepairModel:
        def __init__(self):
            self.calls = []

        def complete(self, system, user):
            assert user.startswith('STAGE: chapter_batch')
            assert 'STAGE: targeted_repair' in user
            context = json.loads(user.rsplit('\n\n', 1)[1])
            assert context['requested_chapters'] == targets
            self.calls.append(user)
            return json.dumps(proposed)

    repair_model = RepairModel()
    planner.client = repair_model
    result = repair_chapters(planner, candidate, global_design, review, targets)
    mapped = {row['number']: row['design_reference_ids'] for row in global_design['chapter_map']}
    for accepted, original in zip(result['chapters'], proposed['chapters']):
        assert accepted['design_reference_ids'] == mapped[accepted['number']]
        assert story_without_references(accepted) == story_without_references(original)
    assert len(repair_model.calls) == 1
    binding = json.loads((planner.cache / 'repair-reference-binding-v1.json').read_text())
    assert binding['payload']['source_candidate'] == proposed
    before = snapshot(planner.cache)
    planner.client = NoNewModel()
    assert repair_chapters(planner, candidate, global_design, review, targets) == result
    assert not planner.client.calls
    _unchanged(planner.cache, before)

    path = planner.cache / 'repair.json'
    saved = json.loads(path.read_text())
    saved['payload']['chapters'][0].pop('design_reference_ids')
    saved['payload_hash'] = content_hash(saved['payload'])
    path.write_text(json.dumps(saved))
    tampered = path.read_bytes()
    with pytest.raises(OutlinePlanningError, match='mapped design references'):
        repair_chapters(planner, candidate, global_design, review, targets)
    assert planner.client.calls == []
    assert path.read_bytes() == tampered
