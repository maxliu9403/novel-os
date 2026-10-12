"""Source-linked direction planning cannot substitute anchor-ID overlap for review."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from core.cover_director import CoverArtDirector, CoverDirectionError
from core.cover_validator import validate_direction
from tests.test_cover_analysis_snapshot import source  # noqa: F401
from tests.test_cover_director_source_context import bound  # noqa: F401


def alternate_source(bound):
    _, snapshot, analysis, brief, ledger, payload, _ = bound
    data, payload = analysis.to_dict(), deepcopy(payload)
    old_ref = payload['plans'][0]['campaign']['interaction_edges'][0]['evidence_refs'][0]
    original = next(row for row in data['evidence'] if 'evidence:' + row['evidence_id'] == old_ref)
    # A distinct verified span ID for the same source event is valid evidence.
    row = {**original, 'evidence_id': 'alternate-event-quote'}
    data['evidence'].append(row)
    item = next(item for item in ledger.items if 'evidence:' + item.evidence_id == old_ref)
    ledger = replace(ledger, items=(*ledger.items, replace(item, evidence_id=row['evidence_id'])))
    payload['plans'][0]['campaign']['interaction_edges'][0]['evidence_refs'] = ['evidence:' + row['evidence_id']]
    return snapshot, data, brief, ledger, payload


def reviewed(request, *, verdict='supported'):
    reviews = [{'claim_id': c['claim_id'], 'verdict': verdict,
                'evidence_refs': c['evidence_refs'],
                'reasoning': 'Fixture source supports the action.' if verdict == 'supported' else 'The action is not established.'}
               for c in request.get('evidence_claims', [])]
    return {'accepted': verdict == 'supported', 'findings': [], 'evidence_reviews': reviews}


def test_verified_alternate_quote_requires_independent_review_and_bound_receipt(bound):
    snapshot, data, brief, ledger, payload = alternate_source(bound)
    calls = []
    def complete(system, user):
        request = json.loads(user)
        calls.append(request)
        return json.dumps(reviewed(request) if request['task'] == 'review_campaign_semantics' else payload)
    result = CoverArtDirector(complete=complete).plan(brief, count=4, evidence_ledger=ledger,
        story_analysis=data, source_snapshot=snapshot)
    assert len(calls) == 2
    assert result.source_binding['campaign_evidence_review']
    assert not validate_direction(brief, result, story_analysis=data)
    unreviewed = replace(result, source_binding=None)
    assert 'campaign_interaction_evidence' in {f.code for f in validate_direction(brief, unreviewed, story_analysis=data)}
    # Keep the actual quote; never silently inject a summary's anchor reference.
    assert result.plans[0].campaign['interaction_edges'][0]['evidence_refs'] == ['evidence:alternate-event-quote']


@pytest.mark.parametrize('verdict', ['unsupported', 'uncertain'])
def test_uncorroborated_claims_still_fail_with_exact_repair_target(bound, verdict):
    snapshot, data, brief, ledger, payload = alternate_source(bound)
    calls, diagnostics = [], []
    def complete(system, user):
        request = json.loads(user)
        calls.append(request)
        return json.dumps(reviewed(request, verdict=verdict) if request['task'] == 'review_campaign_semantics' else payload)
    with pytest.raises(CoverDirectionError, match='campaign direction failed review'):
        CoverArtDirector(complete=complete).plan(brief, count=4, evidence_ledger=ledger,
            story_analysis=data, source_snapshot=snapshot, diagnostic_sink=diagnostics.append)
    assert len(calls) == 6  # Existing maximum: initial design + two repairs, each reviewed.
    assert any('campaign.interaction_edges[0]' in str(r.get('validation_findings', '')) for r in calls)
    assert diagnostics[-1]['stage'] == 'failure'
    assert sum(r['stage'] == 'draft_response' for r in diagnostics) == 3
    assert sum(r['stage'] == 'semantic_response' for r in diagnostics) == 3


def test_writer_cannot_self_attest_and_missing_review_receipts_block(bound):
    snapshot, data, brief, ledger, payload = alternate_source(bound)
    payload['source_binding'] = {'campaign_evidence_review': {'approved': True}}
    calls = []
    def complete(system, user):
        request = json.loads(user)
        calls.append(request)
        return json.dumps({'accepted': True, 'findings': []} if request['task'] == 'review_campaign_semantics' else payload)
    with pytest.raises(CoverDirectionError):
        CoverArtDirector(complete=complete).plan(brief, count=4, evidence_ledger=ledger,
            story_analysis=data, source_snapshot=snapshot)
    assert any(r['task'] == 'review_campaign_semantics' for r in calls)


def test_diagnostics_failure_stops_before_another_model_call(bound):
    snapshot, data, brief, ledger, payload = alternate_source(bound)
    calls = []
    def complete(system, user):
        calls.append(user)
        return json.dumps(payload)
    def unavailable(event):
        raise OSError('diagnostic storage unavailable')
    with pytest.raises(OSError, match='diagnostic storage unavailable'):
        CoverArtDirector(complete=complete).plan(brief, count=4, evidence_ledger=ledger,
            story_analysis=data, source_snapshot=snapshot, diagnostic_sink=unavailable)
    assert len(calls) == 0


def test_second_draft_transport_failure_records_current_attempt(bound):
    _, snapshot, analysis, brief, ledger, _, _ = bound
    calls, diagnostics = [], []
    def complete(system, user):
        calls.append(user)
        if len(calls) == 1:
            return '{}'  # Schema repair requires a second draft.
        raise RuntimeError('transport interrupted')
    with pytest.raises(CoverDirectionError, match='transport interrupted'):
        CoverArtDirector(complete=complete).plan(brief, count=4, evidence_ledger=ledger,
            story_analysis=analysis.to_dict(), source_snapshot=snapshot,
            diagnostic_sink=diagnostics.append)
    assert len(calls) == 2
    assert diagnostics[-1]['stage'] == 'failure' and diagnostics[-1]['attempt'] == 2
    assert [row['attempt'] for row in diagnostics if row['stage'] == 'draft_started'] == [1, 2]
