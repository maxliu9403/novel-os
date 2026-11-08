"""Source locations are deterministic; story fidelity still needs review."""
import copy
import json

import pytest

from outline_planning import OutlinePlanningError
from test_outline_planning import PlanningModel, chapter, global_design, make_planner
from test_skeleton_projection import source
from test_story_first_engine import design  # noqa: F401


def context(planner):
    return {**planner._context(), 'volume_contract_template': planner.volume_template}


class QuoteCorrectionModel(PlanningModel):
    def complete(self, system, user):
        data = json.loads(user.rsplit('\n\n', 1)[1])
        if user.startswith('STAGE: format_correction'):
            self.calls.append(('format', data))
            value = json.loads(data['completed_response'])
            value['source_anchors'][0]['quote'] = 'Complete global 1:'
            return json.dumps(value)
        self.calls.append(('global', data))
        value = global_design(data)
        value['source_anchors'][0]['quote'] = 'Not an exact source quotation'
        for item in value['chapter_map'][:-1]:
            item['source_anchor_ids'] = []
        return json.dumps(value)


def test_saved_correction_with_empty_chapter_references_recovers_without_regeneration(tmp_path):
    model = QuoteCorrectionModel()
    planner = make_planner(tmp_path, model, count=60, source=source(60))
    result = planner._call('global', planner.global_prompt, context(planner), planner._global)
    assert [name for name, _ in model.calls] == ['global', 'format']
    assert len(result['chapter_map']) == 60
    anchors = {a['id']: a for a in result['source_anchors']}
    for n, item in enumerate(result['chapter_map'][:-1], 1):
        assert len(item['source_anchor_ids']) == 1
        anchor = anchors[item['source_anchor_ids'][0]]
        assert anchor['kind'] == 'chapter_locator'
        assert anchor['chapter'] == n
        assert anchor['quote'] == f'### Chapter {n} — Title'
        assert anchor['quote'] in planner.source
    # Existing valid model citations remain unchanged.
    assert result['chapter_map'][-1]['source_anchor_ids'] == ['source_1']
    original = json.loads(model.calls[-1][1]['completed_response'])
    assert [c['purpose'] for c in result['chapter_map']] == [c['purpose'] for c in original['chapter_map']]
    saved = {p: p.read_bytes() for p in planner.cache.rglob('*') if p.is_file()}
    resumed_model = PlanningModel()
    resumed = make_planner(tmp_path, resumed_model, count=60, source=source(60))
    assert resumed._call('global', resumed.global_prompt, context(resumed), resumed._global) == result
    assert resumed_model.calls == []
    assert all(p.read_bytes() == raw for p, raw in saved.items())


@pytest.mark.parametrize('missing', [False, True])
def test_global_locations_and_batch_inheritance_only_fill_absent_references(tmp_path, missing):
    planner = make_planner(tmp_path, PlanningModel(), count=4, source=source(4))
    value = global_design(context(planner))
    for item in value['chapter_map']:
        if missing:
            item.pop('source_anchor_ids')
        else:
            item['source_anchor_ids'] = []
    frozen = planner._global(value)
    batch = {'chapters': [chapter(n) for n in range(1, 5)]}
    for item in batch['chapters']:
        if missing:
            item.pop('source_anchor_ids')
        else:
            item['source_anchor_ids'] = []
    output = planner._batch(batch, [1, 2, 3, 4], frozen)
    assert [c['source_anchor_ids'] for c in output['chapters']] == [c['source_anchor_ids'] for c in frozen['chapter_map']]
    assert all(c['bespoke_detail'] == chapter(c['number'])['bespoke_detail'] for c in output['chapters'])


@pytest.mark.parametrize('bad', [None, 'source_1', {}, [None], [{}], ['unknown'], ['source_1', 'source_1']])
def test_malformed_or_false_citations_are_not_replaced_by_locators(tmp_path, bad):
    planner = make_planner(tmp_path, PlanningModel(), count=4, source=source(4))
    value = global_design(context(planner))
    value['chapter_map'][1]['source_anchor_ids'] = bad
    with pytest.raises(OutlinePlanningError, match='Chapter 2.*source_anchor_ids'):
        planner._global(value)
    frozen = planner._global(global_design(context(planner)))
    batch = {'chapters': [chapter(2)]}
    batch['chapters'][0]['source_anchor_ids'] = bad
    with pytest.raises(OutlinePlanningError, match='Chapter 2.*source_anchor_ids'):
        planner._batch(batch, [2], frozen)


@pytest.mark.parametrize('change', [
    lambda s: 'Unstructured source with no chapter map.',
    lambda s: s.replace('### Chapter 4', '### Chapter 3'),
    lambda s: s.replace('### Chapter 4', '### Chapter 5'),
    lambda s: s.replace('## 12. Section', '## Unknown'),
    lambda s: s + '\n```unclosed',
])
def test_ambiguous_skeleton_cannot_supply_automatic_citations(tmp_path, change):
    planner = make_planner(tmp_path, PlanningModel(), count=4, source=change(source(4)))
    value = global_design(context(planner))
    value['chapter_map'][0]['source_anchor_ids'] = []
    with pytest.raises(OutlinePlanningError, match='Chapter 1.*source_anchor_ids'):
        planner._global(value)


def test_source_locations_do_not_waive_exact_quotes_or_ending_validation(tmp_path):
    planner = make_planner(tmp_path, PlanningModel(), count=4, source=source(4))
    value = global_design(context(planner))
    value['chapter_map'][0]['source_anchor_ids'] = []
    value['source_anchors'][0]['quote'] = 'Not in the source'
    with pytest.raises(OutlinePlanningError, match='exact quotation'):
        planner._global(copy.deepcopy(value))
    value['source_anchors'][0]['quote'] = 'Complete global 1:'
    value['ending_contract']['main_conflict']['thread_id'] = 'nonexistent'
    with pytest.raises(OutlinePlanningError, match='unknown thread'):
        planner._global(value)


def test_story_first_skeleton_locations_are_exact_and_idempotent(design, tmp_path):
    from core.story_design import render
    text = render(*design)['prompt_md']
    planner = make_planner(tmp_path, PlanningModel(), count=2, source=text)
    value = global_design(context(planner))
    for row in value['chapter_map']:
        row['source_anchor_ids'] = []
    bound = planner._global(value)
    assert planner._global(copy.deepcopy(bound)) == bound
    for a in bound['source_anchors'][1:]:
        start, end = a['source_chars']
        assert text[start:end].startswith(a['quote'])
        assert design[2]['chapters'][a['chapter'] - 1]['core_plot'][0] in text[start:end]
    corrupted = copy.deepcopy(bound)
    corrupted['source_anchors'][1]['source_chars'][0] += 1
    with pytest.raises(OutlinePlanningError, match='does not match the frozen source'):
        planner._global(corrupted)


def test_locator_identity_cannot_overwrite_existing_model_anchor(tmp_path):
    import hashlib
    original = source(4)
    planner = make_planner(tmp_path, PlanningModel(), count=4, source=original)
    value = global_design(context(planner))
    value['source_anchors'].append({'id': f'source_chapter_1_{hashlib.sha256(original.encode()).hexdigest()[:12]}',
                                    'quote': 'Complete global 1:'})
    value['chapter_map'][0]['source_anchor_ids'] = []
    with pytest.raises(OutlinePlanningError, match='collides'):
        planner._global(value)

