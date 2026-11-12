"""A verified Workshop baseline survives handoff without upgrading old books."""
from copy import deepcopy
import json

import pytest

from core import creative_execution as ce
from core.author_requirements import build_requirements
from core.creative_baseline import build
from core.creative_development import DevelopmentReview
from core.reader_investment import snapshot
from test_creative_development import Model


SOURCE = '# Complete skeleton\nShe chooses independence and pays its real cost.'


def verification(tmp_path, source_context=None, packet=None):
    baseline = build({'id': 'handoff-story', 'revision': 3,
        'input': {'premise': 'She chooses independence and pays its real cost.'},
        'messages': [], 'source_context': source_context,
        'creative_design': {'rounds': [], 'decisions': []}})
    result = DevelopmentReview(tmp_path / 'review', baseline).assess(Model(), {
        'story_design': {'ending': SOURCE}, 'chapter_plan': [{'number': 1, 'scene': SOURCE}]}, evidence_packet=packet)
    assert result['status'] == 'passed'
    return {'status': 'passed', 'source_sha256': ce.text_hash(SOURCE),
            'baseline': baseline, 'review': result['receipt']}


def kwargs():
    return {'source': SOURCE, 'origin': {'workshop_id': 'handoff-story', 'revision': 4},
            'reader_investment': snapshot(), 'story_engine': 'story-first.v1',
            'author_requirements': build_requirements('story', [], source_sha256=ce.text_hash(SOURCE))}


def test_verified_delivery_enters_v6_and_unverified_legacy_stays_byte_identical(tmp_path):
    legacy = ce.make_handoff(**kwargs())
    before = ce.encoded(legacy)
    handoff = ce.make_handoff(**kwargs(), development_basis=verification(tmp_path))
    assert legacy['schema'] == 'creative-handoff.v4'
    assert handoff['schema'] == 'creative-handoff.v6'
    assert handoff['development_basis']['baseline']['items'][0]['value'] == 'She chooses independence and pays its real cost.'
    assert ce.validate_handoff(handoff) == handoff
    assert ce.encoded(ce.make_handoff(**kwargs())) == before


@pytest.mark.parametrize('mutate', [
    lambda value: value.update(status='needs_revision'),
    lambda value: value.update(source_sha256='a' * 64),
    lambda value: value['baseline']['items'][0].update(value='She gives away her freedom.'),
    lambda value: value['review'].update(baseline_sha256='b' * 64),
    lambda value: value['review'].update(session_id='a-different-book'),
    lambda value: value['review']['coverage'][0].update(status='revise'),
    lambda value: value['review'].update(missing_placements=['missing-payoff']),
    lambda value: value['review'].update(request_ids=[]),
])
def test_invalid_or_foreign_verification_never_upgrades_a_handoff(tmp_path, mutate):
    value = verification(tmp_path)
    mutate(value)
    with pytest.raises(ValueError):
        ce.make_handoff(**kwargs(), development_basis=value)


def test_title_packaging_chain_is_explicit_and_original_review_unchanged(tmp_path):
    value = verification(tmp_path)
    original = deepcopy(value)
    updated = SOURCE.replace('Complete skeleton', 'Chosen title')
    value.update(reviewed_source_sha256=value['source_sha256'], source_sha256=ce.text_hash(updated),
        transformations=[{'kind': 'title', 'before_sha256': ce.text_hash(SOURCE),
                          'after_sha256': ce.text_hash(updated), 'author_title': 'Chosen title'}])
    inputs = {**kwargs(), 'source': updated,
              'author_requirements': build_requirements('story', [], source_sha256=ce.text_hash(updated))}
    handoff = ce.make_handoff(**inputs, development_basis=value)
    assert handoff['development_basis']['review'] == original['review']
    assert handoff['development_basis']['baseline'] == original['baseline']
    assert handoff['development_basis']['reviewed_source_sha256'] == original['source_sha256']
    value['transformations'][0]['kind'] = 'rewrite_story'
    with pytest.raises(ValueError, match='transformation'):
        ce.make_handoff(**inputs, development_basis=value)


def project(tmp_path, handoff):
    root = tmp_path / 'book'
    inputs = root / 'outputs/input'
    inputs.mkdir(parents=True)
    (inputs / 'prompt.md').write_text(SOURCE)
    return root


def restore_projection(value, supplied):
    if isinstance(value, dict) and set(value) == {'context_ref', 'value_sha256'}:
        actual = supplied
        for part in value['context_ref']:
            actual = actual[part]
        assert ce.digest(actual) == value['value_sha256']
        return deepcopy(actual)
    if isinstance(value, dict):
        return {key: restore_projection(child, supplied) for key, child in value.items()}
    if isinstance(value, list):
        return [restore_projection(child, supplied) for child in value]
    return value


def test_original_v6_runtime_preserves_full_basis_and_needs_no_source_packet(tmp_path):
    value = verification(tmp_path)
    handoff = ce.make_handoff(**kwargs(), development_basis=value)
    root = project(tmp_path, handoff)
    runtime = ce.CreativeExecution.open(project=root, run_id='original-v6', handoff=handoff)
    context = runtime.design_context('story_design')
    projected = restore_projection(context['development_basis']['data'], context)
    assert projected['basis_sha256'] == handoff['development_basis']['sha256']
    assert runtime.read_development_evidence([])['value'] == handoff['development_basis']
    assert 'source_context' not in context
    saved = (runtime.directory / 'lock.json').read_bytes()
    resumed = ce.CreativeExecution.open(project=root, run_id='original-v6', handoff=handoff,
                                        expected_sha256=runtime.lock_sha256, already_started=True)
    assert resumed.design_context('story_design') == context
    assert (runtime.directory / 'lock.json').read_bytes() == saved


def test_v6_source_evidence_remains_portable_and_projection_restores_every_value(tmp_path):
    from types import SimpleNamespace
    from test_source_route_handoff import evidence_fixture
    from core.source_route_context import save_packet
    context, packet = evidence_fixture(tmp_path / 'source', 'A full source passage: ' + SOURCE * 5)
    context['selected_proposal'] = {'title': 'Chosen route', 'story': SOURCE * 6}
    context['selected_proposal']['sha256'] = ce.digest(context['selected_proposal'])
    context['context_sha256'] = ce.digest({k: v for k, v in context.items() if k != 'context_sha256'})
    value = verification(tmp_path, context, packet)
    handoff = ce.make_handoff(**kwargs(), source_context=context, development_basis=value)
    root = project(tmp_path, handoff)
    save_packet(root / 'outputs/private', packet)
    runtime = ce.CreativeExecution.open(project=root, run_id='source-v6', handoff=handoff)
    supplied = runtime.design_context('story_design')
    projected = restore_projection(supplied['development_basis']['data'], supplied)
    assert projected['source_identity'] == handoff['development_basis']['baseline']['source_identity']
    assert runtime.read_development_evidence([])['value'] == handoff['development_basis']
    calls = []
    user, receipt = runtime.prepare('outline_planner', SOURCE, None, None)
    model = SimpleNamespace(complete=lambda system, user: calls.append(user) or '{"result":"complete"}')
    assert runtime.complete(model, 'outline_planner', user, receipt) == '{"result":"complete"}'
    assert 'A full source passage' in calls[0] and '开头承诺必须保留' in calls[0]
    # Packet integrity remains required for v6, exactly as for v5.
    (root / 'outputs/private/source-evidence' / (packet['sha256'] + '.json')).unlink()
    with pytest.raises((ValueError, OSError)):
        runtime.verify_source()


def test_workshop_upgrade_requires_actual_verified_delivery_not_enabled_flag(tmp_path):
    from api.workshop_design import execution_handoff
    import hashlib
    policy = {'reader_investment': snapshot(), 'story_engine': 'story-first.v1', 'guidance': {}}
    session = {'id': 'handoff-story', 'revision': 4, 'prompt_md': SOURCE, 'input': {}, 'messages': [],
               'creative_design': {'schema': 'workshop-design.v1', 'policy': policy, 'rounds': [], 'decisions': [],
                                  'policy_sha256': hashlib.sha256(json.dumps(policy, ensure_ascii=False, sort_keys=True).encode()).hexdigest()},
               'development': {'schema': 'creative-development.v1'}}
    assert execution_handoff(session)['schema'] == 'creative-handoff.v4'
    session['development']['delivery_verification'] = verification(tmp_path)
    assert execution_handoff(session)['schema'] == 'creative-handoff.v6'
    session['prompt_md'] += '\nA different ending.'
    with pytest.raises(ValueError, match='骨架与已核验'):
        execution_handoff(session)


def test_host_packaging_rebind_retains_original_review_and_only_appends_actual_changes(tmp_path):
    from api.workshop_development_binding import rebind_development_source
    value = verification(tmp_path)
    session = {'development': {'delivery_verification': value}, 'revision': 4}
    original = deepcopy(value)
    assert rebind_development_source(session, SOURCE, SOURCE, 'packaging') is False
    assert session['development']['delivery_verification'] == original
    packaged = SOURCE + '\n<!-- verified metadata -->'
    renamed = packaged.replace('Complete skeleton', 'Author chosen title')
    assert rebind_development_source(session, SOURCE, packaged, 'packaging') is True
    assert rebind_development_source(session, packaged, renamed, 'title') is True
    updated = session['development']['delivery_verification']
    assert updated['review'] == original['review']
    assert updated['baseline'] == original['baseline']
    assert updated['reviewed_source_sha256'] == original['source_sha256']
    assert updated['source_sha256'] == ce.text_hash(renamed)
    assert [row['kind'] for row in updated['transformations']] == ['packaging', 'title']
    assert ce.make_development_basis(updated, ce.text_hash(renamed))['source_sha256'] == ce.text_hash(renamed)
    assert original == value  # do not mutate a prior snapshot through aliases


def test_host_packaging_rebind_rejects_stale_source_without_partial_mutation(tmp_path):
    from api.workshop_development_binding import rebind_development_source
    session = {'development': {'delivery_verification': verification(tmp_path)}}
    original = deepcopy(session)
    for before, after, kind in [(SOURCE + 'changed', SOURCE, 'title'),
                                (SOURCE, SOURCE + 'changed', 'rewrite_story')]:
        with pytest.raises(ValueError):
            rebind_development_source(session, before, after, kind)
        assert session == original


def test_legacy_packaging_does_not_fabricate_verified_development():
    from api.workshop_development_binding import rebind_development_source
    for session in [{}, {'development': {'schema': 'creative-development.v1'}}]:
        original = deepcopy(session)
        assert rebind_development_source(session, SOURCE, SOURCE + 'metadata', 'packaging') is False
        assert session == original


def test_discussion_receipt_cannot_be_relabelled_as_verified_delivery(tmp_path):
    from test_creative_development import routed_baseline
    baseline = routed_baseline()
    result = DevelopmentReview(tmp_path, baseline).assess(Model(), {'reply': SOURCE})
    assert result['status'] == 'passed'
    with pytest.raises(ValueError, match='review'):
        ce.make_development_basis({'status': 'passed', 'source_sha256': ce.text_hash(SOURCE),
                                  'baseline': baseline, 'review': result['receipt']}, ce.text_hash(SOURCE))


def test_delivery_receipt_must_include_every_required_adopted_payoff(tmp_path):
    from test_creative_development import routed_baseline, passed
    baseline = routed_baseline()
    candidate = {'story_design': {'ending': SOURCE}, 'chapter_plan': [{'number': 1, 'scene': SOURCE}]}
    def respond(payload, _):
        return {**passed(), 'placements': [{'commitment_id': key, 'candidate_paths': [['chapter_plan', 0]],
            'quote': SOURCE} for key in payload['required_commitments']]}
    result = DevelopmentReview(tmp_path, baseline).assess(Model(respond), candidate)
    value = {'status': 'passed', 'source_sha256': ce.text_hash(SOURCE),
             'baseline': baseline, 'review': result['receipt']}
    assert ce.make_development_basis(value, ce.text_hash(SOURCE))['placements']
    value['review']['placements'].pop()
    with pytest.raises(ValueError, match='placement'):
        ce.make_development_basis(value, ce.text_hash(SOURCE))


def test_verified_author_adjustment_survives_handoff_and_runtime_projection(tmp_path):
    from core.creative_amendments import witness
    from test_creative_development import passed
    author_text = 'Keep her dignity and its real cost, but replace reconciliation with independence.'
    baseline = build({'id': 'handoff-story', 'revision': 3, 'input': {},
        'messages': [{'role': 'user', 'content': author_text}],
        'creative_design': {'rounds': [{'proposals': [{'id': 'adopted',
            'story': 'She keeps her dignity, pays the real cost and reconciles.'}]}],
            'decisions': [{'proposal_id': 'adopted', 'status': 'accepted'}]}})
    adopted = next(item for item in baseline['items'] if item['category'] == 'adopted_proposal')
    effective = deepcopy(adopted['value'])
    effective['proposal']['story'] = SOURCE
    adjustment = witness(baseline, {'commitment_id': adopted['id'],
        'author_requirement_id': 'author-message-1', 'author_quote': author_text, 'effective_value': effective},
        {'authorized': True, 'reason': 'The author explicitly changed this ending while preserving dignity and cost.'}, 'e' * 64)
    candidate = {'story_design': {'ending': SOURCE}, 'chapter_plan': [{'number': 1, 'scene': SOURCE}]}
    judge = Model(lambda payload, _: {**passed(), 'placements': [{'commitment_id': key,
        'candidate_paths': [['chapter_plan', 0]], 'quote': SOURCE} for key in payload['required_commitments']]})
    receipt = DevelopmentReview(tmp_path, baseline).assess(judge, candidate)['receipt']
    receipt['author_adjustments'] = [adjustment]
    value = {'status': 'passed', 'source_sha256': ce.text_hash(SOURCE), 'baseline': baseline, 'review': receipt}
    handoff = ce.make_handoff(**kwargs(), development_basis=value)
    runtime = ce.CreativeExecution.open(project=project(tmp_path, handoff), run_id='amended-v6', handoff=handoff)
    supplied = runtime.design_context('draft')
    restored = restore_projection(supplied['development_basis']['data'], supplied)
    assert restored['author_adjustments'] == [adjustment]
    assert any(row.get('effective_value_ref') == ['author_adjustments', 0, 'effective_value']
               for row in restored['effective_commitments'])
    assert runtime.read_development_evidence(['baseline', 'items'])['value'] == baseline['items']
    value['review']['author_adjustments'][0]['author_quote'] = 'Invented approval.'
    with pytest.raises(ValueError):
        ce.make_handoff(**kwargs(), development_basis=value)
