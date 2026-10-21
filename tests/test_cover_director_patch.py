"""Repair transport changes only returned fields; whole-portfolio review remains."""
from copy import deepcopy
import json

import pytest

from core.cover_director import CoverArtDirector, CoverDirectionError
from core.cover_sources import canonical_hash
from tests.cover_review_fixtures import supported_review
from tests.test_cover_analysis_snapshot import source  # noqa: F401
from tests.test_cover_director_source_context import bound  # noqa: F401


def patch(base, changes, index=0):
    return {'schema': 'cover-direction-patch.v1', 'base_sha256': canonical_hash(base),
            'plan_updates': [{'concept_id': base['plans'][index]['concept_id'], 'changes': changes}],
            'shared_updates': {}}


def test_bound_semantic_repair_returns_only_changed_fields_but_reviews_all_plans(bound):
    _, snapshot, analysis, brief, ledger, payload, _ = bound
    before = deepcopy(payload)
    invalid = deepcopy(payload)
    invalid['plans'][0]['campaign']['cover_epoch'] = 'wrong epoch'
    calls, events = [], []
    def complete(system, user):
        request = json.loads(user); calls.append(request)
        if len(calls) == 1:
            return json.dumps(invalid)
        if request['task'] == 'review_campaign_semantics':
            assert len(request['plans']) == 4
            assert len({c['concept_id'] for c in request['evidence_claims']}) == 4
            return json.dumps(supported_review(request))
        assert request['response_contract']['schema'] == 'cover-direction-patch.v1'
        assert request['response_contract']['base_sha256'] == canonical_hash(invalid)
        assert request['previous_response'] == invalid
        return json.dumps(patch(invalid, {'campaign': payload['plans'][0]['campaign']}))
    result = CoverArtDirector(complete=complete).plan(brief, count=4, evidence_ledger=ledger,
        story_analysis=analysis.to_dict(), source_snapshot=snapshot, diagnostic_sink=events.append)
    assert len(calls) == 3 and result.source_binding['campaign_evidence_review']
    assert payload == before
    assert result.plans[0].campaign['cover_epoch'] == payload['plans'][0]['campaign']['cover_epoch']
    assert sum(r['stage'] == 'draft_response' for r in events) == 2


@pytest.mark.parametrize('bad', ['hash', 'identity', 'schema'])
def test_invalid_patch_cannot_erase_base_or_add_more_repair_calls(bound, bad):
    _, snapshot, analysis, brief, ledger, payload, _ = bound
    invalid = deepcopy(payload); invalid['plans'][0]['campaign']['cover_epoch'] = 'wrong'
    calls = []
    def complete(system, user):
        request = json.loads(user); calls.append(request)
        if len(calls) == 1: return json.dumps(invalid)
        proposal = patch(invalid, {'campaign': payload['plans'][0]['campaign']})
        if bad == 'hash': proposal['base_sha256'] = '0' * 64
        elif bad == 'identity': proposal['plan_updates'][0]['concept_id'] = 'another'
        else: proposal['plan_updates'][0]['changes'] = {'unknown': 'x'}
        if len(calls) == 3:
            assert request['repair_base'] == invalid
            assert request['response_contract']['base_sha256'] == canonical_hash(invalid)
        return json.dumps(proposal)
    with pytest.raises(CoverDirectionError):
        CoverArtDirector(complete=complete).plan(brief, count=4, evidence_ledger=ledger,
            story_analysis=analysis.to_dict(), source_snapshot=snapshot)
    assert len(calls) == 3


def test_full_reply_compatibility_cannot_change_plan_ids_in_patch_round(bound):
    _, snapshot, analysis, brief, ledger, payload, _ = bound
    calls = []
    def complete(system, user):
        calls.append(json.loads(user))
        value = deepcopy(payload)
        if len(calls) == 1: value['plans'][0]['campaign']['cover_epoch'] = 'wrong'
        else: value['plans'][0]['concept_id'] = 'new-plan'
        return json.dumps(value)
    with pytest.raises(CoverDirectionError):
        CoverArtDirector(complete=complete).plan(brief, count=4, evidence_ledger=ledger,
            story_analysis=analysis.to_dict(), source_snapshot=snapshot)
    assert len(calls) == 3


def test_next_patch_binds_merged_payload_and_reviews_unchanged_plans_again(bound):
    _, snapshot, analysis, brief, ledger, payload, _ = bound
    first = deepcopy(payload); first['plans'][0]['campaign']['cover_epoch'] = 'wrong'
    second = deepcopy(payload); second['director_notes'] = ['First repair retains all design fields.']
    calls, reviews = [], []
    def complete(system, user):
        request = json.loads(user); calls.append(request)
        if len(calls) == 1: return json.dumps(first)
        if request['task'] == 'review_campaign_semantics':
            reviews.append(request)
            if len(reviews) == 1:
                return json.dumps(supported_review(request, accepted=False, findings=[{
                    'concept_id': payload['plans'][3]['concept_id'],
                    'message': 'The unchanged final plan requires a clearer visible story beat.',
                    'evidence_refs': request['evidence_claims'][-1]['evidence_refs'],
                }]))
            return json.dumps(supported_review(request))
        if len(calls) == 2:
            result = patch(first, {'campaign': payload['plans'][0]['campaign']})
            result['shared_updates'] = {'director_notes': second['director_notes']}
            return json.dumps(result)
        assert request['previous_response'] == second
        assert request['response_contract']['base_sha256'] == canonical_hash(second)
        return json.dumps(patch(second, {'design_rationale': payload['plans'][3]['design_rationale'] + ' Clear at thumbnail size.'}, index=3))
    result = CoverArtDirector(complete=complete).plan(brief, count=4, evidence_ledger=ledger,
        story_analysis=analysis.to_dict(), source_snapshot=snapshot)
    assert len(calls) == 5 and len(reviews) == 2
    assert all(len(r['plans']) == 4 for r in reviews)
    assert all(len({c['concept_id'] for c in r['evidence_claims']}) == 4 for r in reviews)
    assert result.source_binding['campaign_evidence_review']


def test_valid_patch_does_not_bypass_unrelated_evidence_rejection(bound):
    _, snapshot, analysis, brief, ledger, payload, _ = bound
    invalid = deepcopy(payload); invalid['plans'][0]['campaign']['cover_epoch'] = 'wrong'
    calls = []
    def complete(system, user):
        request = json.loads(user); calls.append(request)
        if len(calls) == 1: return json.dumps(invalid)
        if request['task'] == 'review_campaign_semantics':
            report = supported_review(request, accepted=False)
            report['evidence_reviews'][-1]['verdict'] = 'unsupported'
            return json.dumps(report)
        base = request['previous_response']
        changes = ({'campaign': payload['plans'][0]['campaign']} if len(calls) == 2 else
                   {'design_rationale': payload['plans'][0]['design_rationale'] + ' Visible gesture.'})
        return json.dumps(patch(base, changes))
    with pytest.raises(CoverDirectionError, match='campaign direction failed review'):
        CoverArtDirector(complete=complete).plan(brief, count=4, evidence_ledger=ledger,
            story_analysis=analysis.to_dict(), source_snapshot=snapshot)
    assert len(calls) == 5
