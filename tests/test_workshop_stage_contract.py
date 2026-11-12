"""The actual delivery-stage contract must make metadata repair lossless."""
from copy import deepcopy
import json

import pytest

from api import workshop_delivery as delivery


def stage(mode="standalone_long", count=103):
    return {"narrative_mode": mode, "total_chapters": count, "story_design": {
        name: f"完整保留的{name}：人物选择、代价、情绪兑现。" for name in (
            "title", "author", "premise", "characters", "world_rules", "causal_chain",
            "ending", "decisions", "voice", "setups_payoffs")}}


@pytest.mark.parametrize("story_first", [False, True])
def test_actual_first_stage_rules_disclose_all_accepted_modes(story_first):
    session = {"id": "a" * 32, "revision": 15, "skill_version": "test",
               "input": {"mode": "", "chapters": 103}, "messages": []}
    if story_first:
        from api.workshop_design import new_design
        session["creative_design"] = new_design(session['input'], guided=False)
        session['creative_design']['policy']['story_engine'] = 'story-first.v1'
    plan = delivery.DeliveryPlan(session, None)
    rules = plan.rules()
    assert all(mode in rules for mode in delivery.MODES)
    assert 'total_chapters' in rules and 'integer' in rules


@pytest.mark.parametrize("mode", ["single_volume", "standalone", None, 3, []])
def test_invalid_mode_has_precise_lossless_repair_evidence(mode):
    candidate = stage(mode)
    before = deepcopy(candidate)
    with pytest.raises(delivery.StageEnvelopeResponseError) as caught:
        delivery.parse_stage(json.dumps(candidate), 'story_design', None, story_first=True)
    error = caught.value
    assert error.candidate == before
    assert error.issues == [{"field": "narrative_mode", "code": "enum",
                             "expected": sorted(delivery.MODES), "actual": mode}]
    assert 'narrative_mode' in str(error) and 'standalone_long' in str(error)
    fixed = delivery.repair_stage_envelope(error.candidate, {"narrative_mode": "standalone_long"})
    assert fixed == {**before, "narrative_mode": "standalone_long"}
    assert candidate == before
    assert delivery.parse_stage(json.dumps(fixed), 'story_design', None, story_first=True) == fixed


@pytest.mark.parametrize("count", ["103", 0, True, None, 1.5])
def test_invalid_chapter_count_is_independent_from_valid_mode(count):
    candidate = stage(count=count)
    with pytest.raises(delivery.StageEnvelopeResponseError) as caught:
        delivery.parse_stage(json.dumps(candidate), 'story_design', None)
    assert [issue['field'] for issue in caught.value.issues] == ['total_chapters']
    with pytest.raises(delivery.DeliveryError, match='范围'):
        delivery.repair_stage_envelope(candidate, {"total_chapters": 103, "narrative_mode": "multi_volume"})
    fixed = delivery.repair_stage_envelope(candidate, {"total_chapters": 103})
    assert fixed == stage()


def test_bound_mode_and_count_cannot_be_redefined_by_repair():
    candidate = stage('standalone_long', 102)
    with pytest.raises(delivery.StageEnvelopeResponseError) as caught:
        delivery.parse_stage(json.dumps(candidate), 'story_design', 'short_novel', 103)
    assert [i['code'] for i in caught.value.issues] == ['const', 'const']
    with pytest.raises(delivery.StageEnvelopeResponseError):
        delivery.repair_stage_envelope(candidate, {"narrative_mode": "multi_volume", "total_chapters": 103},
                                       mode='short_novel', chapter_count=103)
    fixed = delivery.repair_stage_envelope(candidate, {"narrative_mode": "short_novel", "total_chapters": 103},
                                           mode='short_novel', chapter_count=103)
    assert fixed == stage('short_novel', 103)


def test_repair_cannot_replace_story_or_supply_partial_metadata():
    candidate = stage('single_volume', '103')
    for patch in ({"narrative_mode": "standalone_long"},
                  {"narrative_mode": "standalone_long", "total_chapters": 103, "story_design": {}},
                  [], None):
        with pytest.raises(delivery.DeliveryError, match='范围'):
            delivery.repair_stage_envelope(candidate, patch)
    assert candidate == stage('single_volume', '103')


def test_content_failure_is_not_misclassified_as_metadata_repair():
    candidate = stage()
    candidate['story_design'].pop('premise')
    with pytest.raises(delivery.DeliveryError) as caught:
        delivery.parse_stage(json.dumps(candidate), 'story_design', None, story_first=True)
    assert not isinstance(caught.value, delivery.StageEnvelopeResponseError)


def test_error_text_never_echoes_arbitrary_model_metadata():
    candidate = stage('private model-generated text', {'secret': 'private'})
    with pytest.raises(delivery.StageEnvelopeResponseError) as caught:
        delivery.parse_stage(json.dumps(candidate), 'story_design', None)
    assert 'private' not in str(caught.value)
    assert caught.value.candidate == candidate


def test_metadata_repair_does_not_bypass_story_validation():
    candidate = stage('single_volume')
    candidate['story_design']['causal_chain'] = ''
    fixed = delivery.repair_stage_envelope(candidate, {'narrative_mode': 'standalone_long'})
    with pytest.raises(delivery.DeliveryError, match='因果主线'):
        delivery.parse_stage(json.dumps(fixed), 'story_design', None, story_first=True)
    assert fixed['story_design'] == candidate['story_design']


@pytest.mark.parametrize('story_first', [False, True])
def test_saved_chapter_windows_disclose_frozen_mode_and_total(story_first):
    from api.workshop_design import new_design
    session = {'id': 'a' * 32, 'revision': 15, 'skill_version': 'test',
               'input': {'chapters': 103}, 'messages': []}
    if story_first:
        session['creative_design'] = new_design(session['input'], guided=False)
        session['creative_design']['policy']['story_engine'] = 'story-first.v1'
    plan = delivery.DeliveryPlan(session, None)
    plan.finish('story_design', stage())
    assert plan.mode == 'standalone_long' and plan.chapter_count == 103
    contract = delivery.stage_envelope_contract('standalone_long', 103)
    assert json.dumps(contract, ensure_ascii=False, separators=(',', ':')) in plan.rules(window=True)


def test_first_stage_binds_confirmed_count_before_any_checkpoint_exists():
    from api.workshop_design import new_design
    from core.story_capacity import contract
    from test_story_capacity import capacity_plan
    session = {'id': 'a' * 32, 'revision': 15, 'skill_version': 'test',
               'input': {'chapters': 103, 'length_planning': 'story_first'}, 'messages': []}
    session['creative_design'] = new_design(session['input'], guided=False)
    session['creative_design']['policy']['story_engine'] = 'story-first.v1'
    session['capacity'] = {'status': 'confirmed', 'confirmed': contract(capacity_plan(103), 'keep_story', 'a' * 64)}
    plan = delivery.DeliveryPlan(session, None)
    assert plan.chapter_count is None
    assert plan.expected_chapter_count == 103
    assert '"const":103' in plan.rules()
    with pytest.raises(delivery.StageEnvelopeResponseError) as caught:
        plan.finish('story_design', stage(count=21))
    assert caught.value.issues == [{'field': 'total_chapters', 'code': 'const', 'expected': 103, 'actual': 21}]
    assert not plan.record['completed']
    plan.finish('story_design', stage(count=103))
    assert plan.chapter_count == plan.expected_chapter_count == 103


def test_unconfirmed_chapter_preference_is_not_a_frozen_count():
    session = {'id': 'a' * 32, 'revision': 15, 'skill_version': 'test',
               'input': {'chapters': 103}, 'messages': []}
    plan = delivery.DeliveryPlan(session, None)
    assert plan.expected_chapter_count is None
    plan.finish('story_design', stage(count=21))
    assert plan.expected_chapter_count == 21
