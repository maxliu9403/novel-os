"""Composed title failures are repaired before image generation is authorized."""
from copy import deepcopy
from io import BytesIO
import json

from PIL import Image
import pytest

from core.cover_director import CoverArtDirector, CoverDirectionError
from tests.cover_review_fixtures import supported_review
from tests.test_cover_campaign import context


@pytest.mark.parametrize('invalid,expected', [
    ({'letter_spacing': -0.01}, 'Letter spacing'),
    ({'safe_zones': [[0, 0, 1, 1]]}, 'protected face or story action'),
    ({'region': [.1, .1, .01, .01]}, 'does not fit'),
])
def test_invalid_layout_and_semantics_are_repaired_together(invalid, expected, monkeypatch):
    from core import cover_typography
    brief, ledger, valid, analysis = context()
    bad = deepcopy(valid)
    bad['plans'][0]['campaign']['title_layout'].update(invalid)
    calls, dimensions, drafts, reviews = [], [], [], []
    problem = 'The posture does not distinguish a farewell from an ordinary arrival.'
    compose = cover_typography.compose_cover
    def observed_compose(artwork, *args, **kwargs):
        with Image.open(BytesIO(artwork)) as canvas:
            dimensions.append(canvas.size)
        return compose(artwork, *args, **kwargs)
    monkeypatch.setattr(cover_typography, 'compose_cover', observed_compose)
    def complete(system, user):
        request = json.loads(user)
        calls.append(request)
        if request['task'] == 'review_campaign_semantics':
            reviews.append(request)
            assert len(request['plans']) == 4
            if len(reviews) == 1:
                return json.dumps(supported_review(request, accepted=False, findings=[{
                    'concept_id': 'concept-2', 'message': problem,
                    'evidence_refs': [ledger.allowed_refs[0]],
                }]))
            return json.dumps(supported_review(request))
        drafts.append(request)
        return json.dumps(bad if len(drafts) == 1 else valid)
    # Default v9 must preserve the runtime canvas size when it selects v10.
    director = CoverArtDirector(complete=complete, image_size='1024x1536')
    result = director.plan(brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    assert result.profile_version == 'cover-profiles.v10'
    assert len(calls) == 4  # Both complete drafts receive independent review.
    assert len(drafts) == len(reviews) == 2
    assert [call['task'] == 'review_campaign_semantics' for call in calls] == [False, True, False, True]
    assert reviews[0]['plans'][0]['campaign']['title_layout'] == bad['plans'][0]['campaign']['title_layout']
    assert result.plans[0].campaign['title_layout'] == valid['plans'][0]['campaign']['title_layout']
    finding = next(item for item in drafts[1]['validation_findings'] if item['code'] == 'campaign_title_layout')
    assert expected in finding['message']
    assert 'concept-1' in finding['concept_or_evidence']
    assert 'campaign.title_layout' in finding['concept_or_evidence']
    assert '1024x1536' in finding['concept_or_evidence']
    semantic = next(item for item in drafts[1]['validation_findings'] if item['code'] == 'campaign_semantic_review')
    assert semantic['message'] == problem
    assert semantic['concept_or_evidence'] == 'concept-2'
    assert semantic['evidence_refs'] == [ledger.allowed_refs[0]]
    assert dimensions and set(dimensions) == {(1024, 1536)}


def test_unrepaired_layout_cannot_pass_with_accepted_semantics_or_add_draft_rounds():
    brief, ledger, payload, analysis = context()
    payload['plans'][0]['campaign']['title_layout']['letter_spacing'] = -.01
    calls = []
    def complete(system, user):
        request = json.loads(user)
        calls.append(request)
        if request['task'] == 'review_campaign_semantics':
            assert len(request['plans']) == 4
            return json.dumps(supported_review(request))
        return json.dumps(payload)
    with pytest.raises(CoverDirectionError, match='campaign_title_layout'):
        CoverArtDirector(complete=complete, image_size='768x1152').plan(
            brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    assert len(calls) == 6
    assert [call['task'] == 'review_campaign_semantics' for call in calls] == [False, True] * 3
    repairs = [call for call in calls if 'validation_findings' in call]
    assert len(repairs) == 2
    assert all(any(item['code'] == 'campaign_title_layout' for item in repair['validation_findings'])
               for repair in repairs)


def test_fixture_read_and_native_layouts_do_not_require_local_composition(monkeypatch):
    from core import cover_typography
    brief, ledger, payload, analysis = context()
    payload['plans'][0]['campaign']['title_layout']['letter_spacing'] = -.01
    def forbidden(*args, **kwargs):
        raise AssertionError('Historical fixture/native mode must not compose local typography')
    monkeypatch.setattr(cover_typography, 'prepare_title_layout', forbidden)
    CoverArtDirector.from_fixture(payload).plan(brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
    for plan in payload['plans']:
        plan['campaign']['title_render_mode'] = 'native'
    def complete(system, user):
        request = json.loads(user)
        if request['task'] == 'review_campaign_semantics':
            return json.dumps(supported_review(request))
        return json.dumps(payload)
    CoverArtDirector(complete=complete, image_size='1024x1536').plan(
        brief, count=4, evidence_ledger=ledger, story_analysis=analysis)
