"""Identity repair preserves paid contracts while interpreting original-language evidence."""
from copy import deepcopy

import pytest

from core import planning_residual_identity as identity, planning_residual_evidence as evidence
from core.outline_planning import content_hash
from test_planning_residual_identity import Planner, book, issue, finding, proposal


def test_completed_older_identity_reply_is_not_rerun_under_new_language_guidance(monkeypatch):
    p, candidate = Planner(), book()
    plan, review = {'issues':[issue()]}, {'issues':[finding()]}
    with monkeypatch.context() as old:
        old.setattr(identity, 'CURRENT_INSTRUCTION', identity.INSTRUCTION)
        first = identity.repair(p, candidate, review, plan, blocked_indices=set())
    saved = deepcopy(p.saved)
    assert identity.repair(p, candidate, review, plan, blocked_indices=set()) == first
    assert p.saved == saved and len(p.calls) == 1


def test_interrupted_identity_request_retains_its_exact_original_policy(tmp_path):
    p, candidate = Planner(), book()
    p.cache = tmp_path
    (tmp_path/'raw').mkdir()
    context = identity.build_context(p, candidate, finding(), issue())
    name, _ = evidence._key('identity', content_hash(candidate), identity.INSTRUCTION, context)
    path = tmp_path/'raw'/(name+'.request.prompt.txt')
    path.write_text(identity.INSTRUCTION)
    original = p._call
    seen = []
    def capture(name, instruction, context, validate, **options):
        seen.append(instruction)
        return original(name, instruction, context, validate, **options)
    p._call = capture
    identity.repair(p, candidate, {'issues':[finding()]}, {'issues':[issue()]}, blocked_indices=set())
    assert seen == [identity.INSTRUCTION]
    assert path.read_text() == identity.INSTRUCTION


@pytest.mark.parametrize('quote,gender,pronouns', [
    ('守卫走进来，他打开了门。', 'male', 'he/him'),
    ('The guard arrived. She opened the gate.', 'female', 'she/her'),
])
def test_source_supported_gender_fields_stay_bound_to_the_cited_role(quote, gender, pronouns):
    def response():
        return {'identity_fields':[
            {'character_id':'char_b','field':'gender','before':'','value':gender,'source_indices':[0]},
            {'character_id':'char_b','field':'pronouns','before':'','value':pronouns,'source_indices':[0]}],
            'identity_relations':[]}
    p, candidate = Planner(response), book()
    p.source = quote
    original = p._call
    def capture(name, instruction, context, validate, **options):
        assert instruction == identity.CURRENT_INSTRUCTION
        assert 'Neutral, ambiguous or collective references cannot establish gender' in instruction
        return original(name, instruction, context, validate, **options)
    p._call = capture
    result, audit = identity.repair(p, candidate, {'issues':[{'message':'Verify char_b’s source gender.'}]},
                                   {'issues':[issue(source_quotes=[quote])]}, blocked_indices=set())
    assert result['characters'][1] == {**candidate['characters'][1], 'gender':gender, 'pronouns':pronouns}
    assert result['characters'][0] == candidate['characters'][0]
    assert result['chapters'] == candidate['chapters']
    assert audit[0]['source_quotes'] == [quote]
