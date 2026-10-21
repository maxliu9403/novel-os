"""Commercial cover advice must not become mandatory paid redraws."""
from copy import deepcopy
from dataclasses import replace

import pytest

from core.cover_quality import normalize_quality_payload, CoverEvaluationProtocolError, classify_cover_repair
from tests.test_cover_quality import _scores
from tests.test_cover_service import _bound_inputs, _service, _actual_jpeg, _independent_evaluator

VERSION = 'cover-readability.v2'


def commercial_response():
    return {**_scores(), 'review_policy_version': VERSION, 'status': 'human_review_required',
            'core_conflict_fidelity': 55, 'causal_relationship_clarity': 45, 'protagonist_agency': 50,
            'blockers': [], 'repair_codes': [], 'findings': [{
                'code': 'visual_ambiguity', 'category': 'visual_ambiguity', 'severity': 'warning',
                'claim': '',
                'message': 'The marital distance reads clearly; the precise refusal is not uniquely provable.',
                'evidence': 'The woman turns partly away from the paperwork.'}]}


def _service_review(tmp_path, response, *, commercial=True):
    from api.media import digest
    if commercial:
        from tests.test_cover_commercial_campaign import commercial_context
        from core.cover_prompt_compiler import compile_direction_recipes, scene_to_cover_concept
        brief, direction, _ = commercial_context()
        direction = replace(direction, plans=tuple(replace(p, causal_visibility='direct',
            protagonist_action_visible=True) for p in direction.plans))
        concepts = [scene_to_cover_concept(brief, p, evidence_ledger=direction.evidence_ledger) for p in direction.plans]
        recipes = compile_direction_recipes(brief, direction)
    else:
        brief, concepts, recipes = _bound_inputs()
    service, client, _ = _service(tmp_path, [])
    service.visual_evaluator = _independent_evaluator(response)
    bound = service._bind_generation(concepts[:1], brief,
        {concepts[0].concept_id: recipes[concepts[0].concept_id]}, 0, {})[0]
    bound = replace(bound, scene_plan={**bound.scene_plan, 'causal_visibility': 'direct', 'protagonist_action_visible': True})
    image, supplemental = _actual_jpeg(), {}
    report = service._quality_report(brief, bound, image, digest(image), 'image/jpeg',
                                    artwork=image, supplemental=supplemental)
    assert client.prompts == []
    return report, supplemental


def test_advice_and_low_action_scores_do_not_turn_into_generation_blockers(tmp_path):
    report, supplemental = _service_review(tmp_path, commercial_response())
    assert report.status == 'human_review_required'
    assert report.blockers == () and report.repair_codes == ()
    assert classify_cover_repair(report)['kind'] == 'none'
    assert supplemental['evaluation']['review_policy_version'] == VERSION
    assert supplemental['evaluation']['classified_findings'][0]['category'] == 'visual_ambiguity'


@pytest.mark.parametrize('category', ['marketing_appeal', 'visual_ambiguity', 'composition_craft', 'thumbnail_readability'])
def test_advisory_category_cannot_smuggle_a_blocker(category):
    value = commercial_response()
    value['findings'][0].update(category=category, severity='blocker')
    value.update(status='blocked', blockers=['visual_ambiguity'])
    with pytest.raises(CoverEvaluationProtocolError, match='category|classification|advisory'):
        normalize_quality_payload(value)


def test_material_story_error_survives_new_policy(tmp_path):
    response = commercial_response()
    response.update(status='blocked', blockers=['core_conflict_missing'], repair_codes=['core_conflict_missing'],
        findings=[{'code': 'core_conflict_missing', 'category': 'material_story_contradiction',
                  'severity': 'blocker', 'message': 'The image explicitly presents the wrong parent-child relation.',
                  'claim': 'The depicted mother is a stranger in the confirmed story.',
                  'evidence': 'A matching family portrait explicitly labels them mother and daughter.'}])
    report, _ = _service_review(tmp_path, response)
    assert report.blockers == ('core_conflict_missing',)
    assert classify_cover_repair(report)['kind'] == 'design'


@pytest.mark.parametrize('commercial', [True, False])
def test_model_cannot_select_or_omit_the_policy_bound_to_the_scene(tmp_path, commercial):
    response = commercial_response()
    if commercial:
        response.pop('review_policy_version')
    report, supplemental = _service_review(tmp_path, response, commercial=commercial)
    assert supplemental['evaluation']['evaluation_status'] == 'protocol_error'
    assert 'review_policy_version' not in supplemental['evaluation']
    assert report.status == 'human_review_required'
    assert report.core_conflict_fidelity is None



def test_incomplete_or_unknown_commercial_classification_is_not_treated_as_permission():
    for change in ({'category': 'unknown'}, {'evidence': ''}, {'category': 'author_constraint', 'severity': 'blocker'}):
        response = commercial_response()
        response['findings'][0].update(change)
        with pytest.raises(CoverEvaluationProtocolError):
            normalize_quality_payload(response)


def test_legacy_report_is_not_reclassified_from_its_english_message():
    legacy = commercial_response()
    legacy.pop('review_policy_version')
    legacy.update(status='blocked', blockers=['core_conflict_missing'], repair_codes=['core_conflict_missing'])
    legacy['findings'] = [{'code': 'core_conflict_missing', 'severity': 'blocker', 'message': 'Could be a farewell.', 'evidence': 'Doorway.'}]
    before = deepcopy(legacy)
    assert normalize_quality_payload(legacy)['blockers'] == ['core_conflict_missing']
    assert legacy == before


def test_new_marketing_contract_reaches_stage_b_without_leaking_to_blind_read():
    from dataclasses import replace
    from core.cover_director import CoverArtDirector
    from core.cover_quality import build_llm_visual_evaluator
    from tests.test_cover_campaign import context
    from tests.test_cover_quality_campaign_scope import CaptureClient, review
    brief, ledger, payload, analysis = context()
    direction = CoverArtDirector.from_fixture(payload).plan(brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    scene = replace(direction.plans[0], campaign={**direction.plans[0].campaign, 'expression_mode': 'editorial'})
    client = CaptureClient(commercial_response())
    result = review(build_llm_visual_evaluator(client), brief, scene)
    assert len(client.calls) == 2
    assert 'readability_policy' not in client.calls[0][1]
    request = client.calls[1][1]
    assert request['readability_policy']['version'] == VERSION
    assert request['output_json_schema']['properties']['review_policy_version']['const'] == VERSION
    assert 'category' in request['output_json_schema']['properties']['findings']['items']['required']
    rules = ' '.join(request['semantic_rules'])
    assert 'full causal chain' in str(request['readability_policy'])
    assert 'compatible visual ambiguity' in str(request['readability_policy'])
    assert 'must_not_show' in rules and 'not a factual contradiction' in rules
    assert 'when causal_visibility is direct' not in rules
    assert result.report.status == 'human_review_required' and not result.report.blockers
    assert result.evaluation['review_policy_version'] == VERSION
    assert result.evaluation['blind_read'] == client.calls[1][1]['independent_blind_read']
