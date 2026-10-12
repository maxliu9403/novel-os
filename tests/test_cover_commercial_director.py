"""Commercial covers preserve truth without buying cosmetic repair loops."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from core.cover_director import CoverArtDirector, CoverDirectionError
from core.cover_sources import canonical_hash
from tests.test_cover_analysis_snapshot import source  # noqa: F401
from tests.test_cover_director_source_context import bound  # noqa: F401
from tests.cover_review_fixtures import supported_review

POLICY = 'cover-commercial-review.v1'


def commercial_review(request, *, blockers=(), advisories=()):
    report = supported_review(request, accepted=not blockers)
    report.pop('findings')
    return {**report, 'schema': POLICY, 'blockers': list(blockers), 'advisories': list(advisories)}


def payload_v11(bound):
    payload = deepcopy(bound[5])
    payload['profile_version'] = 'cover-profiles.v11'
    for plan in payload['plans']:
        plan['campaign']['expression_mode'] = 'literal_scene'
    return payload


def issue(request, category='visual_ambiguity', *, concept_id=None):
    claim = request['evidence_claims'][0]
    return {'category': category, 'concept_id': concept_id or claim['concept_id'],
            'message': 'A compatible emotional reading remains possible at thumbnail size.',
            'evidence_refs': claim['evidence_refs']}


def plan(bound, complete, **kwargs):
    _, snapshot, analysis, brief, ledger, _, _ = bound
    return CoverArtDirector(complete=complete, profile_version='cover-profiles.v11').plan(
        brief, count=4, evidence_ledger=ledger, story_analysis=analysis.to_dict(),
        source_snapshot=snapshot, **kwargs)


def test_marketing_advice_finishes_after_one_draft_one_independent_review(bound):
    calls=[]
    def complete(system,user):
        request=json.loads(user);calls.append(request)
        if request['task']=='review_campaign_semantics':
            return json.dumps(commercial_review(request,advisories=[issue(request)]))
        return json.dumps(payload_v11(bound))
    result=plan(bound,complete)
    assert len(calls)==2
    receipt=result.source_binding['campaign_evidence_review']
    assert receipt['policy_version']==POLICY
    assert receipt['plans_sha256']==canonical_hash([p.to_dict() for p in result.plans])
    assert receipt['advisories'][0]['category']=='visual_ambiguity'
    assert calls[-1]['review_scope']['mode']=='full_portfolio'


def test_target_only_review_cannot_rejudge_unchanged_siblings(bound):
    first=plan(bound,lambda s,u:json.dumps(commercial_review(json.loads(u))
        if json.loads(u)['task']=='review_campaign_semantics' else payload_v11(bound)))
    base=first.to_dict()
    for key in ('source_binding','compiled_recipes','direction_id','direction_sha256','created_at','status','novelty_report'):
        base.pop(key,None)
    repair={'base':base,'quality_report':{},'concept_id':first.plans[1].concept_id}
    calls=[]
    def complete(system,user):
        request=json.loads(user);calls.append(request)
        if request['task']=='review_campaign_semantics':
            assert request['review_scope']['mode']=='target_only'
            assert [p['concept_id'] for p in request['plans']]==[repair['concept_id']]
            assert {c['concept_id'] for c in request['evidence_claims']}=={repair['concept_id']}
            return json.dumps(commercial_review(request))
        return json.dumps({'schema':'cover-direction-patch.v1',
            'base_sha256':canonical_hash(request['previous_response']), 'shared_updates':{},
            'plan_updates':[{'concept_id':repair['concept_id'],'changes':{'blocking':'A clear profile leaves the key and open hand distinct.'}}]})
    result=plan(bound,complete,targeted_repair=repair,prior_reviewed_directions=(first,))
    assert len(calls)==2
    assert result.plans[0]==first.plans[0]
    assert len(result.source_binding['campaign_evidence_review']['evidence_reviews'])==len(first.source_binding['campaign_evidence_review']['evidence_reviews'])


@pytest.mark.parametrize('malformation', ['legacy', 'unknown_category', 'advisory_as_blocker', 'hard_as_advisory',
    'conflicting_severity', 'missing_assertion', 'invented_ref', 'unknown_concept', 'accepted_false_for_advice'])
def test_invalid_classification_stops_without_buying_design_repairs(bound, malformation):
    calls=[]
    def complete(system,user):
        request=json.loads(user);calls.append(request)
        if request['task']!='review_campaign_semantics':
            return json.dumps(payload_v11(bound))
        report=commercial_review(request,advisories=[issue(request)])
        if malformation=='legacy': return json.dumps(supported_review(request))
        if malformation=='unknown_category': report['advisories'][0]['category']='miscellaneous'
        if malformation=='advisory_as_blocker': report.update(blockers=report.pop('advisories'),advisories=[],accepted=False)
        if malformation=='hard_as_advisory': report['advisories'][0]['category']='material_story_contradiction'
        if malformation=='conflicting_severity': report['advisories'][0]['severity']='blocker'
        if malformation=='missing_assertion': report.update(advisories=[],blockers=[issue(request,'material_story_contradiction')],accepted=False)
        if malformation=='invented_ref': report['advisories'][0]['evidence_refs']=['evidence:invented']
        if malformation=='unknown_concept': report['advisories'][0]['concept_id']='not-a-candidate'
        if malformation=='accepted_false_for_advice': report['accepted']=False
        return json.dumps(report)
    with pytest.raises(CoverDirectionError,match='semantic review unavailable'):
        plan(bound,complete)
    assert len(calls)==2


def test_hard_factual_error_repairs_but_advice_is_not_sent_as_blocker(bound):
    calls=[];reviews=[]
    def complete(system,user):
        request=json.loads(user);calls.append(request)
        if request['task']=='review_campaign_semantics':
            reviews.append(request)
            blockers=[] if len(reviews)>1 else [{**issue(request,'material_story_contradiction'),
                'claim':'The proposed exchange changes who possesses the key.', 'field':'frozen_action'}]
            return json.dumps(commercial_review(request,blockers=blockers,advisories=[issue(request)]))
        if len(calls)==1:return json.dumps(payload_v11(bound))
        feedback=request['validation_findings']
        assert len(feedback)==1 and feedback[0]['category']=='material_story_contradiction'
        assert feedback[0]['claim'] and feedback[0]['field']=='frozen_action'
        return json.dumps({'schema':'cover-direction-patch.v1','base_sha256':canonical_hash(request['previous_response']),
            'shared_updates':{},'plan_updates':[{'concept_id':request['previous_response']['plans'][0]['concept_id'],
                'changes':{'frozen_action':'Mara holds her key while Oren stops his empty hand.'}}]})
    result=plan(bound,complete)
    assert len(calls)==4 and len(reviews)==2
    assert result.source_binding['campaign_evidence_review']['advisories']


def _prior(bound):
    return plan(bound,lambda s,u:json.dumps(commercial_review(json.loads(u))
        if json.loads(u)['task']=='review_campaign_semantics' else payload_v11(bound)))


def _repair(prior):
    base=prior.to_dict()
    for key in ('source_binding','compiled_recipes','direction_id','direction_sha256','created_at','status','novelty_report'):
        base.pop(key,None)
    return {'base':base,'quality_report':{},'concept_id':prior.plans[1].concept_id}


def _target_patch(request, repair):
    return {'schema':'cover-direction-patch.v1','base_sha256':canonical_hash(request['previous_response']),
        'shared_updates':{},'plan_updates':[{'concept_id':repair['concept_id'],
            'changes':{'blocking':'Distinct profiles keep both hands visible without asserting a new plot event.'}}]}


@pytest.mark.parametrize('invalid', ['missing', 'analysis_hash', 'plans_hash', 'claim_missing', 'claim_duplicate',
    'unsupported', 'uncertain', 'sibling_changed', 'shared_changed'])
def test_untrusted_retained_receipt_falls_back_to_complete_facts_review(bound, invalid):
    prior=_prior(bound);repair=_repair(prior);binding=deepcopy(prior.source_binding)
    if invalid=='missing':binding={}
    if invalid in {'analysis_hash','plans_hash'}:binding['campaign_evidence_review'][invalid.replace('_hash','_sha256')]='f'*64
    if invalid=='claim_missing':binding['campaign_evidence_review']['evidence_reviews'].pop()
    if invalid=='claim_duplicate':binding['campaign_evidence_review']['evidence_reviews'].append(binding['campaign_evidence_review']['evidence_reviews'][0])
    if invalid in {'unsupported','uncertain'}:binding['campaign_evidence_review']['evidence_reviews'][0]['verdict']=invalid
    prior=replace(prior,source_binding=binding)
    if invalid=='sibling_changed':
        repair['base']['plans'][0]['blocking']+=' Minor change.'
    if invalid=='shared_changed':
        repair['base']['visual_identity']['palette_logic'] += ' A cooler restrained shadow.'
    calls=[]
    def complete(system,user):
        request=json.loads(user);calls.append(request)
        if request['task']=='review_campaign_semantics':
            assert request['review_scope']['mode']=='full_facts_fallback'
            assert len(request['plans'])==4
            return json.dumps(commercial_review(request))
        return json.dumps(_target_patch(request,repair))
    result=plan(bound,complete,targeted_repair=repair,prior_reviewed_directions=(prior,))
    assert len(calls)==2 and result.source_binding['campaign_evidence_review']['policy_version']==POLICY


@pytest.mark.parametrize('failure', ['retained_claim', 'missing_target_claim', 'sibling_blocker'])
def test_target_review_cannot_override_frozen_sibling_receipts(bound,failure):
    prior=_prior(bound);repair=_repair(prior);calls=[]
    def complete(system,user):
        request=json.loads(user);calls.append(request)
        if request['task']!='review_campaign_semantics':return json.dumps(_target_patch(request,repair))
        report=commercial_review(request)
        if failure=='retained_claim':
            report['evidence_reviews'].append(prior.source_binding['campaign_evidence_review']['evidence_reviews'][0])
        elif failure=='missing_target_claim':report['evidence_reviews'].pop()
        else:report.update(accepted=False,blockers=[{**issue(request,'material_story_contradiction',concept_id=prior.plans[0].concept_id),
            'claim':'Another candidate depicts an unsupported event.','field':'frozen_action'}])
        return json.dumps(report)
    with pytest.raises(CoverDirectionError,match='semantic review unavailable'):
        plan(bound,complete,targeted_repair=repair,prior_reviewed_directions=(prior,))
    assert len(calls)==2


def test_fallback_sibling_source_failure_stops_instead_of_asking_target_to_repair_it(bound):
    prior=_prior(bound);repair=_repair(prior);calls=[]
    def complete(system,user):
        request=json.loads(user);calls.append(request)
        if request['task']!='review_campaign_semantics':return json.dumps(_target_patch(request,repair))
        report=commercial_review(request)
        review=next(row for row in report['evidence_reviews'] if not row['claim_id'].startswith(repair['concept_id']+':'))
        review['verdict']='unsupported';report['accepted']=False
        return json.dumps(report)
    with pytest.raises(CoverDirectionError,match='campaign_review_scope_conflict'):
        plan(bound,complete,targeted_repair=repair)
    assert len(calls)==2


def test_commercial_review_persists_policy_advice_and_request_metrics_through_workflow(bound):
    from core.cover_workflow import CoverWorkflow
    from core.cover_store import CoverStore
    context,_,analysis,_,_,_,project=bound
    def complete(system,user):
        request=json.loads(user)
        return json.dumps(commercial_review(request,advisories=[issue(request)])
            if request['task']=='review_campaign_semantics' else payload_v11(bound))
    flow=CoverWorkflow(context,CoverStore(project))
    result=flow.plan(analysis.analysis_id,CoverArtDirector(complete=complete,profile_version='cover-profiles.v11'))
    loaded=flow.store.load_direction(result.direction_id)
    assert loaded.source_binding['campaign_evidence_review']['advisories'][0]['category']=='visual_ambiguity'
    assert flow.validate(result.direction_id)[0].direction_sha256==result.direction_sha256
    events=[json.loads(p.read_text()) for p in (flow.store.root/'direction_attempts').glob('*/*json')]
    starts=[e for e in events if e.get('stage','').endswith('_started')]
    assert len(starts)==2 and all(e['request_bytes']>0 for e in starts)
    semantic=next(e for e in events if e.get('stage')=='semantic_validation')
    assert semantic['advisories'] and semantic['findings']==[]
    assert semantic['review_scope']['mode']=='full_portfolio'


def test_mixed_portfolio_reuses_each_siblings_own_matching_direction(bound):
    prior=_prior(bound)
    changed=payload_v11(bound)
    changed['plans'][2]['blocking']='A later approved side-on treatment separates the people and key.'
    alternate=plan(bound,lambda s,u:json.dumps(commercial_review(json.loads(u))
        if json.loads(u)['task']=='review_campaign_semantics' else changed))
    repair=_repair(prior)
    repair['base']['plans'][2]=alternate.plans[2].to_dict()
    calls=[]
    def complete(system,user):
        request=json.loads(user);calls.append(request)
        if request['task']=='review_campaign_semantics':
            assert request['review_scope']['mode']=='target_only'
            assert len(request['plans'])==1
            return json.dumps(commercial_review(request))
        return json.dumps(_target_patch(request,repair))
    result=plan(bound,complete,targeted_repair=repair,prior_reviewed_directions=(prior,alternate))
    assert len(calls)==2
    assert result.plans[2]==alternate.plans[2] and result.plans[0]==prior.plans[0]


def test_legacy_reviewed_siblings_keep_exact_bytes_during_v11_target_upgrade(bound):
    _,snapshot,analysis,brief,ledger,payload,_=bound
    prior=CoverArtDirector(complete=lambda s,u:json.dumps(supported_review(json.loads(u))
        if json.loads(u)['task']=='review_campaign_semantics' else payload),profile_version='cover-profiles.v10').plan(
        brief,count=4,evidence_ledger=ledger,story_analysis=analysis.to_dict(),source_snapshot=snapshot)
    repair=_repair(prior);calls=[]
    def complete(system,user):
        request=json.loads(user);calls.append(request)
        if request['task']=='review_campaign_semantics':
            assert request['review_scope']['mode']=='target_only'
            return json.dumps(commercial_review(request))
        patch=_target_patch(request,repair)
        campaign=deepcopy(repair['base']['plans'][1]['campaign']);campaign['expression_mode']='literal_scene'
        patch['plan_updates'][0]['changes']['campaign']=campaign
        return json.dumps(patch)
    result=plan(bound,complete,targeted_repair=repair,prior_reviewed_directions=(prior,))
    assert len(calls)==2 and result.profile_version=='cover-profiles.v11'
    for index in (0,2,3):
        assert result.plans[index].to_dict()==prior.plans[index].to_dict()
        assert 'expression_mode' not in result.plans[index].campaign


# The same realistic 65–70 KB source and all 200 frozen evidence rows that
# reproduced the previous repair-budget defect must survive the new protocol.
from tests.test_cover_single_repair_budget import dense_repair, _repair_base, _assert_complete  # noqa: E402, F401


def test_dense_v11_refinement_preserves_source_budget_parent_and_user_request(dense_repair):
    from core.cover_workflow import digest
    flow,snapshot,analysis,ledger,direction,parent=dense_repair
    base=_repair_base(direction,parent);target=base['plans'][1]['concept_id']
    request_text=('保留故事和人物身份，仅改善画面层次与自然光线。'*150)[:2000]
    quality=deepcopy(parent.candidates[1].quality_report)
    repair={'base':base,'concept_id':target,'quality_report':quality,
        'cover_set_id':parent.cover_set_id,'candidate_id':parent.candidates[1].candidate_id,
        'parent_sha256':digest(parent.to_dict()),'quality_sha256':digest(quality),
        'refinement_prompt':request_text,'reference_image':{'sha256':'b'*64,'relative_path':'artwork.png',
            'kind':'artwork','content_type':'image/png','width':1024,'height':1536}}
    calls=[];before={p:p.read_bytes() for p in flow.store.root.rglob('*') if p.is_file()}
    def complete(system,user):
        request=json.loads(user);calls.append(request)
        assert len((system+'\n'+user).encode())<=384000
        _assert_complete(request,snapshot,analysis)
        if request['task']=='review_campaign_semantics':
            assert request['review_scope']['mode']=='full_facts_fallback'  # historical fixture has no receipt
            return json.dumps(commercial_review(request,advisories=[issue(request)]))
        assert request['single_candidate_repair']['user_refinement']['request']==request_text
        patch=_target_patch(request,repair)
        campaign=deepcopy(base['plans'][1]['campaign']);campaign['expression_mode']='literal_scene'
        patch['plan_updates'][0]['changes']['campaign']=campaign
        return json.dumps(patch)
    result=flow.plan(analysis.analysis_id,CoverArtDirector(complete=complete,profile_version='cover-profiles.v11'),targeted_repair=repair)
    assert len(calls)==2
    assert result.evidence_ledger.to_dict()==ledger.to_dict()
    assert flow.store.load(parent.cover_set_id)==parent
    assert all(path.read_bytes()==data for path,data in before.items())
    for index in (0,2,3):assert result.plans[index].to_dict()==base['plans'][index]
    assert result.source_binding['single_candidate_repair']['refinement_prompt_sha256']==digest(request_text)


def test_local_commercial_warning_survives_workflow_without_paid_repair(bound):
    from core.cover_workflow import CoverWorkflow
    from core.cover_store import CoverStore
    context,_,analysis,_,_,_,project=bound
    payload=payload_v11(bound)
    payload['plans'][1]['visual_strategy']=payload['plans'][0]['visual_strategy']
    calls=[]
    def complete(system,user):
        request=json.loads(user);calls.append(request)
        return json.dumps(commercial_review(request) if request['task']=='review_campaign_semantics' else payload)
    flow=CoverWorkflow(context,CoverStore(project))
    result=flow.plan(analysis.analysis_id,CoverArtDirector(complete=complete,profile_version='cover-profiles.v11'))
    assert len(calls)==2
    advice=result.source_binding['campaign_evidence_review']['advisories']
    assert any(item.get('code')=='duplicate_strategy' for item in advice)
    flow.validate(result.direction_id)
