"""Host-owned chapter design references must never repurchase unchanged stories."""
from copy import deepcopy
import json

import pytest

from core.outline_planning import OutlinePlanningError, _json, _parse_object, content_hash
from core.planning_detail_batches import build_batch
from core.planning_roadmap import build_global_design
from test_outline_planning import PlanningModel, make_planner
from test_planning_design_recovery import DesignPathModel, NoNewModel, snapshot
from test_planning_roadmap import STORIES


NAME = 'batch-001'
NUMBERS = [1, 2, 3, 4]
PUBLICATION_METADATA = {
    'volume_id', 'volume_number', 'chapter_in_volume', 'volume_role',
    'series_id', 'series_book_number',
}


class BatchReferenceModel(DesignPathModel):
    def __init__(self, story, *, defect='unknown', split=False, stop_at=None):
        super().__init__(story)
        self.defect, self.split, self.stop_at = defect, split, stop_at
        self.proposals = {}

    def complete(self, system, user):
        stage = user.splitlines()[0].removeprefix('STAGE: ')
        context = json.loads(user.rsplit('\n\n', 1)[1])
        if stage == 'format_correction':
            self.calls.append((stage, context))
            return context['completed_response']
        if stage == 'chapter_batch':
            numbers = context['requested_chapters']
            if numbers[0] == self.stop_at:
                raise TimeoutError('fixture stops before next batch')
            if self.split and len(numbers) > 2:
                self.calls.append((stage, context))
                return '{"chapters":['
        value = _parse_object(super().complete(system, user))
        if stage == 'chapter_roadmap':
            for row in value['chapter_map']:
                row['design_reference_paths'] = [
                    ['opening_investment', 'protected_long_range_discoveries', (row['number'] - 1) % 2]]
        if stage == 'chapter_batch':
            mapped = {row['number']: row.get('design_reference_ids', [])
                      for row in context['frozen_global_design']['chapter_map']}
            for chapter in value['chapters']:
                number = chapter['number']
                chapter['bespoke_story_field'] = {
                    'subtext': 'She retains both the promise and its explicit emotional qualifier.',
                    'unabridged_scenes': ['first decision', 'cost', 'deliberately unresolved reaction'],
                }
                if self.defect == 'missing':
                    chapter.pop('design_reference_ids', None)
                elif self.defect == 'empty':
                    chapter['design_reference_ids'] = []
                elif self.defect == 'unknown':
                    chapter['design_reference_ids'] = ['design_not_in_frozen_catalog']
                elif self.defect == 'malformed':
                    chapter['design_reference_ids'] = {'model_selected': True}
                elif self.defect == 'source_namespace':
                    chapter['design_reference_ids'] = list(chapter['source_anchor_ids'])
                elif self.defect == 'wrong_chapter':
                    chapter['design_reference_ids'] = list(mapped[number + 1 if number % 2 else number - 1])
                elif self.defect == 'correct':
                    chapter['design_reference_ids'] = list(mapped[number])
                else:
                    raise AssertionError(self.defect)
            self.proposals[tuple(context['requested_chapters'])] = deepcopy(value)
        return json.dumps(value, ensure_ascii=False)


def story_without_references(chapter):
    return {key: value for key, value in chapter.items() if key != 'design_reference_ids'}


def assert_published_story_preserved(published, original):
    # The established final validator adds these six serialization fields. Every
    # originally returned field, including any supplied metadata, remains exact.
    added = set(published) - set(original) - {'design_reference_ids'}
    assert added <= PUBLICATION_METADATA
    comparable = {key: value for key, value in published.items() if key not in added}
    assert story_without_references(comparable) == story_without_references(original)


def planner_for(tmp_path, model):
    return make_planner(tmp_path, model, count=60, source=STORIES[0].source(60))


def assert_bound_chapters(result, proposals):
    mapped = {row['number']: row.get('design_reference_ids', []) for row in result['chapter_map']}
    original = {chapter['number']: chapter for value in proposals.values() for chapter in value['chapters']}
    for chapter in result['chapters']:
        assert chapter['design_reference_ids'] == mapped[chapter['number']]
        assert_published_story_preserved(chapter, original[chapter['number']])


@pytest.mark.parametrize('defect', ['unknown', 'wrong_chapter', 'empty', 'missing', 'malformed', 'source_namespace'])
def test_model_reference_defects_bind_without_rewriting_story_or_whole_batch_correction(tmp_path, defect):
    model = BatchReferenceModel(STORIES[0], defect=defect)
    planner = planner_for(tmp_path, model)
    result = planner.run()
    assert_bound_chapters(result, model.proposals)
    assert not any(stage == 'format_correction' for stage, _ in model.calls)
    binding = json.loads((planner.cache / (NAME + '-reference-binding-v1.json')).read_text())
    assert binding['payload']['source_candidate'] == model.proposals[tuple(NUMBERS)]
    saved_batch = json.loads((planner.cache / (NAME + '.json')).read_text())['payload']
    assert binding['payload']['candidate'] == saved_batch
    for original, record, accepted in zip(model.proposals[tuple(NUMBERS)]['chapters'],
                                          binding['payload']['bindings'], result['chapters'][:4]):
        assert record == {'chapter': original['number'],
                          'supplied': 'design_reference_ids' in original,
                          'before': original.get('design_reference_ids'),
                          'after': accepted['design_reference_ids']}
    assert len(binding['dependencies_hash']) == 64
    originals = snapshot(planner.cache)
    resumed = planner_for(tmp_path, NoNewModel()).run()
    assert resumed == result
    # Publication revalidates its existing payload and refreshes only saved_at.
    # All paid raw replies, accepted batches and derived binding receipts remain
    # byte-for-byte unchanged on a no-call replay.
    for relative, raw in originals.items():
        current = (planner.cache / relative).read_bytes()
        if relative == 'validated.json':
            assert {k: v for k, v in json.loads(current).items() if k != 'saved_at'} == {
                k: v for k, v in json.loads(raw).items() if k != 'saved_at'}
        else:
            assert current == raw, relative


@pytest.mark.parametrize('tamper', ['unknown', 'wrong_chapter', 'empty', 'missing'])
def test_saved_batch_reference_tampering_is_not_repaired_even_with_recomputed_payload_hash(tmp_path, tamper):
    model = BatchReferenceModel(STORIES[0], defect='correct')
    planner = planner_for(tmp_path, model)
    result = planner.run()
    path = planner.cache / (NAME + '.json')
    saved = json.loads(path.read_text())
    chapter = saved['payload']['chapters'][0]
    if tamper == 'unknown':
        chapter['design_reference_ids'] = ['design_untrusted_saved_reference']
    elif tamper == 'wrong_chapter':
        chapter['design_reference_ids'] = result['chapter_map'][1]['design_reference_ids']
    elif tamper == 'empty':
        chapter['design_reference_ids'] = []
    else:
        chapter.pop('design_reference_ids')
    saved['payload_hash'] = content_hash(saved['payload'])
    path.write_text(json.dumps(saved, ensure_ascii=False))
    before = path.read_bytes()
    continued = NoNewModel()
    with pytest.raises(OutlinePlanningError, match='reference|mapped|Checkpoint'):
        planner_for(tmp_path, continued).run()
    assert not continued.calls
    assert path.read_bytes() == before


def legacy_context(planner, global_design):
    return {**planner._context(NUMBERS), 'frozen_global_design': global_design,
            'requested_chapters': NUMBERS, 'previous_completed_chapters': []}


def save_legacy_batch(planner, global_design, original, correction):
    context = legacy_context(planner, global_design)
    original_raw = json.dumps(original, ensure_ascii=False, indent=2)
    prompt = planner.batch_prompt + '\n\n' + _json(context)
    try:
        planner._batch(deepcopy(original), NUMBERS, global_design)
    except (ValueError, KeyError, TypeError) as exc:
        error = str(exc)
    else:
        error = 'Historical correction was already issued for this complete candidate'
    corrected_prompt = (
        'STAGE: format_correction\nCorrect only the reported JSON/schema defect in the completed response. '
        'Preserve every already valid story choice, source detail, scene, emotion, character, and obligation. '
        'Do not restart or redesign the book, recalculate immutable contracts, or omit sections to make JSON shorter. '
        'Return one complete corrected object for this SAME bounded stage. Use the original source context only '
        'to supply a missing required field; do not invent a contradictory fact. This is the sole automatic format correction.\n\n'
        + _json({'original_instruction': planner.batch_prompt, 'original_context': context,
                 'completed_response': original_raw, 'local_error': error}))
    raw_dir = planner.cache / 'raw'
    raw_dir.mkdir(parents=True, exist_ok=True)
    for name, request, raw in ((NAME, prompt, original_raw),
            (NAME + '-format-correction', corrected_prompt, json.dumps(correction, ensure_ascii=False, indent=2))):
        planner._save(name + '-response', {'prompt_hash': content_hash(request)}, {'raw': raw})
        (raw_dir / (name + '.legacy.prompt.txt')).write_text(request)
        (raw_dir / (name + '.legacy.response.txt')).write_text(raw)


def test_issued_historical_correction_wins_without_resending_or_altering_old_prompt_bytes(tmp_path):
    model = BatchReferenceModel(STORIES[0], defect='unknown')
    planner = planner_for(tmp_path, model)
    global_design = build_global_design(planner)
    context = legacy_context(planner, global_design)
    original = _parse_object(model.complete('', planner.batch_prompt + '\n\n' + _json(context)))
    latest = deepcopy(original)
    latest['chapters'][0]['summary'] = 'A complete later correction owns this precise scene choice.'
    latest['chapters'][0]['bespoke_story_field']['latest_choice'] = ['preserve', 'every', 'word']
    save_legacy_batch(planner, global_design, original, latest)
    before = snapshot(planner.cache)
    planner.client = NoNewModel()
    deps = {'global': content_hash(global_design), 'previous': content_hash([])}
    result = build_batch(planner, NAME, NUMBERS, global_design, [], deps)
    assert result['chapters'][0]['summary'] == latest['chapters'][0]['summary']
    for accepted, raw in zip(result['chapters'], latest['chapters']):
        assert story_without_references(accepted) == story_without_references(raw)
    assert not planner.client.calls
    assert all((planner.cache / relative).read_bytes() == raw for relative, raw in before.items())
    receipt = json.loads((planner.cache / (NAME + '-reference-binding-v1.json')).read_text())
    assert receipt['payload']['source_candidate'] == latest


def test_split_leaves_use_the_same_host_binding_before_strict_aggregate_validation(tmp_path):
    model = BatchReferenceModel(STORIES[0], defect='wrong_chapter', split=True)
    planner = planner_for(tmp_path, model)
    result = planner.run()
    assert_bound_chapters(result, model.proposals)
    corrections = [context for stage, context in model.calls if stage == 'format_correction']
    assert len(corrections) == 15
    assert all(context['completed_response'] == '{"chapters":[' for context in corrections)
    assert (planner.cache / 'batch-001-part-001-002-reference-binding-v1.json').is_file()
    assert (planner.cache / 'batch-001-part-003-004-reference-binding-v1.json').is_file()


def test_continued_model_binds_only_new_batches_and_preserves_saved_story(tmp_path):
    first = BatchReferenceModel(STORIES[0], defect='unknown', stop_at=5)
    first.model = 'reference-route-A'
    old = planner_for(tmp_path, first)
    with pytest.raises(TimeoutError):
        old.run()
    original = (old.cache / 'batch-001.json').read_bytes()
    second = BatchReferenceModel(STORIES[0], defect='wrong_chapter')
    second.model = 'reference-route-B'
    continued = planner_for(tmp_path, second)
    result = continued.run()
    assert (old.cache / 'batch-001.json').read_bytes() == original
    assert not any(stage == 'format_correction' for stage, _ in second.calls)
    assert not any(stage in ('chapter_roadmap', 'global_design_seed') for stage, _ in second.calls)
    assert [context['requested_chapters'][0] for stage, context in second.calls if stage == 'chapter_batch'] == list(range(5, 61, 4))
    for published, saved in zip(result['chapters'][:4], json.loads(original)['payload']['chapters']):
        assert_published_story_preserved(published, saved)
        assert published['design_reference_ids'] == saved['design_reference_ids']


def test_legacy_book_without_design_catalog_keeps_original_protocol_and_payload(tmp_path):
    model = PlanningModel()
    planner = make_planner(tmp_path, model, count=4)
    result = planner.run()
    assert 'design_references' not in result
    assert all('design_reference_ids' not in row for row in result['chapters'])
    assert not (planner.cache / (NAME + '-reference-protocol.json')).exists()
    assert not (planner.cache / (NAME + '-reference-binding-v1.json')).exists()
    assert [stage for stage, _ in model.calls] == ['global', 'batch', 'review']


def setup_unaccepted_batch(tmp_path, *, model=None):
    model = model or BatchReferenceModel(STORIES[0], defect='unknown')
    planner = planner_for(tmp_path, model)
    global_design = build_global_design(planner)
    dependencies = {'global': content_hash(global_design), 'previous': content_hash([])}
    return planner, model, global_design, dependencies


@pytest.mark.parametrize('failure', [OSError, ValueError])
def test_binding_persistence_failure_never_purchases_a_format_correction(tmp_path, monkeypatch, failure):
    planner, model, global_design, deps = setup_unaccepted_batch(tmp_path)
    save = planner._save

    def unavailable(name, *args, **kwargs):
        if name == NAME + '-reference-binding-v1':
            raise failure('simulated binding persistence failure')
        return save(name, *args, **kwargs)

    monkeypatch.setattr(planner, '_save', unavailable)
    with pytest.raises(failure, match='simulated binding persistence failure'):
        build_batch(planner, NAME, NUMBERS, global_design, [], deps)
    assert len([stage for stage, _ in model.calls if stage == 'chapter_batch']) == 1
    assert not any(stage == 'format_correction' for stage, _ in model.calls)
    assert (planner.cache / (NAME + '-response.json')).is_file()
    assert not (planner.cache / (NAME + '-format-correction-response.json')).exists()
    assert not (planner.cache / (NAME + '-reference-binding-v1.json')).exists()
    before = snapshot(planner.cache)
    monkeypatch.setattr(planner, '_save', save)
    planner.client = NoNewModel()
    result = build_batch(planner, NAME, NUMBERS, global_design, [], deps)
    assert not planner.client.calls
    for accepted, original in zip(result['chapters'], model.proposals[tuple(NUMBERS)]['chapters']):
        assert story_without_references(accepted) == story_without_references(original)
    assert all((planner.cache / relative).read_bytes() == raw for relative, raw in before.items())


@pytest.mark.parametrize('tamper', ['candidate', 'source_candidate', 'bindings', 'dependencies'])
def test_recomputed_binding_receipt_tampering_is_rejected_before_promotion(tmp_path, tamper):
    planner, _, global_design, deps = setup_unaccepted_batch(tmp_path)
    build_batch(planner, NAME, NUMBERS, global_design, [], deps)
    path = planner.cache / (NAME + '-reference-binding-v1.json')
    record = json.loads(path.read_text())
    if tamper == 'candidate':
        record['payload']['candidate']['chapters'][0]['summary'] = 'An unapproved changed story.'
    elif tamper == 'source_candidate':
        record['payload']['source_candidate']['chapters'][0]['design_reference_ids'] = []
    elif tamper == 'bindings':
        record['payload']['bindings'][0]['after'] = []
    else:
        record['dependencies_hash'] = '0' * 64
    record['payload_hash'] = content_hash(record['payload'])
    path.write_text(json.dumps(record, ensure_ascii=False))
    before = snapshot(planner.cache)
    planner.client = NoNewModel()
    with pytest.raises(OutlinePlanningError, match='Checkpoint.*invalid'):
        build_batch(planner, NAME, NUMBERS, global_design, [], deps)
    assert not planner.client.calls
    assert not (planner.cache / (NAME + '.json')).exists()
    assert not (planner.cache / (NAME + '-format-correction-response.json')).exists()
    assert snapshot(planner.cache) == before


class InvalidStoryModel(BatchReferenceModel):
    def complete(self, system, user):
        raw = super().complete(system, user)
        if user.startswith('STAGE: chapter_batch'):
            value = _parse_object(raw)
            value['chapters'][0].pop('scenes')
            context = json.loads(user.rsplit('\n\n', 1)[1])
            self.proposals[tuple(context['requested_chapters'])] = deepcopy(value)
            return json.dumps(value)
        return raw


def test_reference_binding_does_not_waive_unrelated_story_schema_failure(tmp_path):
    planner, model, global_design, deps = setup_unaccepted_batch(
        tmp_path, model=InvalidStoryModel(STORIES[0], defect='unknown'))
    with pytest.raises(OutlinePlanningError, match='scenes'):
        build_batch(planner, NAME, NUMBERS, global_design, [], deps)
    assert len([stage for stage, _ in model.calls if stage == 'chapter_batch']) == 1
    assert len([stage for stage, _ in model.calls if stage == 'format_correction']) == 1
    assert not (planner.cache / (NAME + '-reference-binding-v1.json')).exists()
    assert not (planner.cache / (NAME + '.json')).exists()
    original = snapshot(planner.cache)
    planner.client = NoNewModel()
    with pytest.raises(OutlinePlanningError, match='scenes'):
        build_batch(planner, NAME, NUMBERS, global_design, [], deps)
    assert not planner.client.calls
    assert snapshot(planner.cache) == original
