"""Repairs must not append a second manuscript to a near-limit design request."""
from copy import deepcopy
import json

import pytest

from api import workshop
from api.workshop_delivery import DeliveryError, parse_stage
from core.gemini_gateway import INPUT_LIMIT_BYTES, input_bytes
from test_story_first_engine import design  # noqa: F401
from test_workshop_narrative_response import complex_design
from test_workshop_contract_repair import stub_model


@pytest.mark.parametrize('mode', ['multi_volume', 'series_installment'])
def test_single_mode_wrapper_is_lossless_without_a_model_repair(design, mode):
    _, story, _ = complex_design(design, mode)
    wrapped = deepcopy(story)
    wrapped['narrative_format'] = {mode: wrapped['narrative_format']}
    before = deepcopy(wrapped)
    parsed = parse_stage(json.dumps(wrapped), 'story_design', mode, story_first=True)
    assert parsed == story
    assert wrapped == before


def test_near_limit_design_repair_is_scoped_and_preserves_story(design, monkeypatch):
    session, story, _ = complex_design(design)
    session['input']['premise'] = 'Full author brief. ' + 'x' * 100000
    story['story_design']['causal_chain'] += ' Preserved scene. ' * 1400
    broken = deepcopy(story)
    broken['narrative_format']['volumes'][0].pop('structural_role')
    before = deepcopy(session)
    saved = []
    session['_save_delivery_checkpoint'] = lambda item: saved.append(deepcopy(item))
    sizes = []

    def respond(n, system, user):
        sizes.append(input_bytes(system, user))
        assert sizes[-1] <= INPUT_LIMIT_BYTES, sizes
        data = json.loads(user)
        if n == 1:
            assert data['initial_brief'] == before['input']
            return json.dumps(broken)
        if n == 2:
            if data.get('operation') == 'repair_narrative_format':
                assert 'initial_brief' not in data and 'conversation' not in data
                assert data['story_design'] == story['story_design']
                return json.dumps({'narrative_format': story['narrative_format']})
            return json.dumps(story)
    # The third design call legitimately includes the completed story as well as
    # the author brief. Stop there so this test isolates the repair, not total size.
    class ReachedChapters(Exception): pass
    def limited(n, system, user):
        if n == 3:
            data = json.loads(user)
            assert data['delivery_stage'] == 'chapter_plan'
            assert data['saved_story_stages']['story_design'] == story
            raise ReachedChapters
        return respond(n, system, user)
    calls = stub_model(monkeypatch, limited)
    with pytest.raises(ReachedChapters):
        workshop._complete(session, 'Story design')
    assert len(calls) == 3 and sizes[0] > 100000 and sizes[1] < 40000
    assert saved[-1]['completed']['story_design']['content'] == story
    assert session['input'] == before['input'] and session['messages'] == before['messages']


@pytest.mark.parametrize('change', [
    lambda d: d['narrative_format'].update(other={}),
    lambda d: d['narrative_format']['multi_volume'].update(mode='standalone_long'),
    lambda d: d['narrative_format']['multi_volume']['volumes'][1].update(chapter_start=1),
])
def test_wrapper_acceptance_never_bypasses_ambiguity_or_full_validation(design, change):
    _, story, _ = complex_design(design)
    story['narrative_format'] = {'multi_volume': story['narrative_format']}
    change(story)
    with pytest.raises(DeliveryError):
        parse_stage(json.dumps(story), 'story_design', 'multi_volume', story_first=True)


@pytest.mark.parametrize('mutate', [
    lambda d: d.update(story_design={'premise': 'A replacement story'}),
    lambda d: d['narrative_format'].update(mode='standalone_long'),
    lambda d: d['narrative_format'].update(total_chapters=3),
    lambda d: d['narrative_format'].update(target_words=12000),
    lambda d: d['narrative_format']['volumes'][0].update(chapter_end=2),
    lambda d: d['narrative_format']['volumes'].append(deepcopy(d['narrative_format']['volumes'][0])),
    lambda d: d['narrative_format'].update(confirmation_status='pending_confirmation'),
])
def test_scoped_repair_cannot_replace_story_or_locked_structure(design, mutate):
    from api.workshop_format_repair import NarrativeFormatRepair
    _, story, _ = complex_design(design)
    original = deepcopy(story)
    repair = NarrativeFormatRepair(story, 'invalid role')
    reply = {'narrative_format': deepcopy(story['narrative_format'])}
    mutate(reply)
    with pytest.raises(DeliveryError):
        repair.merge(json.dumps(reply))
    assert story == original and repair.candidate == original


def test_series_identity_and_expected_word_budget_are_preserved(design):
    from api.workshop_format_repair import NarrativeFormatRepair
    _, story, _ = complex_design(design, 'series_installment')
    structure = deepcopy(story['narrative_format'])
    story['narrative_format'].pop('target_words')
    repair = NarrativeFormatRepair(story, 'missing target', target_words=1600)
    fixed = json.loads(repair.merge(json.dumps({'narrative_format': structure})))
    assert fixed['story_design'] == story['story_design']
    assert fixed['narrative_format'] == structure
    structure['series']['title'] = 'An unrelated series'
    with pytest.raises(DeliveryError, match='series.title'):
        repair.merge(json.dumps({'narrative_format': structure}))


def test_invalid_patch_then_valid_patch_keeps_original_story(design, monkeypatch):
    session, story, chapters = complex_design(design)
    broken = deepcopy(story)
    broken['narrative_format']['volumes'][0].pop('structural_role')
    replies = [json.dumps(broken), 'invalid JSON',
               json.dumps({'narrative_format': story['narrative_format']}), json.dumps(chapters)]
    calls = stub_model(monkeypatch, lambda n, *_: replies[n - 1])
    result, _ = workshop._complete(session, 'Story design')
    assert len(calls) == 4
    for i in (1, 2):
        request = json.loads(calls[i][1])
        assert request['operation'] == 'repair_narrative_format'
        assert request['story_design'] == story['story_design']
    assert story['story_design']['causal_chain'] in result['prompt_md']
    assert 'operation' not in json.loads(calls[3][1])


def test_non_packaging_story_error_keeps_full_creative_context(design, monkeypatch):
    session, story, chapters = complex_design(design)
    session['input']['premise'] = 'Keep this author fact.'
    broken = deepcopy(story)
    broken['story_design'].pop('characters')
    replies = [broken, story, chapters]
    calls = stub_model(monkeypatch, lambda n, *_: json.dumps(replies[n - 1]))
    workshop._complete(session, 'Story design')
    repair = json.loads(calls[1][1])
    assert 'operation' not in repair
    assert repair['initial_brief'] == session['input']
    assert json.loads(repair['previous_model_response']) == broken
