import copy
import json

import pytest
from core.planning_assumptions import reconcile_assumptions


def test_candidate_inference_can_be_revised_without_changing_canon_or_sibling_fields(tmp_path):
    class Planner:
        source = 'The jury acquits her. The witness disappeared.'
        chapter_count = 3
        client = object()
        def _load(self, *a, **k): return None
        def _save(self, name, deps, value): self.saved = name, copy.deepcopy(value)
        def _prompt_fits(self, *a, **k): return True
        def _call(self, name, instruction, context, validate, **kwargs):
            assert 'The witness appeared at trial.' in str(context)
            assert self.source in str(context)
            return validate({'path':'/assumptions/0','before':'The witness appeared at trial.',
                             'after':'The acquittal does not establish that the missing witness testified.',
                             'reason':'The template inferred live testimony from acquittal.',
                             'source_quotes':['The jury acquits her.']})
    p = Planner()
    c = {'assumptions':['The witness appeared at trial.', 'Keep the bell.'], 'chapters':[{'number':1}]}
    i = {'issue_index':0,'classification':'contradiction','affected_chapters':[1], 'writable_chapters':[1],
         'dependency_chapters':[],'source_quotes':['The jury acquits her.'],
         'candidate_evidence':[{'path':'/assumptions/0','quote':'appeared at trial'}]}
    review = {'issues':[{'message':'Was the witness present?'}]}
    a, audit = reconcile_assumptions(p,c,review,{'issues':[i]},blocked_indices={0})
    assert a == c and not audit and not hasattr(p,'saved')
    a, audit = reconcile_assumptions(p,c,review,{'issues':[i]},blocked_indices=set())
    assert c['assumptions'][0] == 'The witness appeared at trial.' and c['chapters']==a['chapters']
    assert a['assumptions'][0].startswith('The acquittal') and a['assumptions'][1] == c['assumptions'][1]
    assert '/assumptions/0' in json.dumps(audit)
    other=copy.deepcopy(i); other['issue_index']=1
    a,audit = reconcile_assumptions(p,c,{'issues':review['issues']*2},{'issues':[i, other]}, blocked_indices={1})
    assert a==c and not audit, 'A pending issue sharing a global assumption fences that assumption.'


@pytest.mark.parametrize('changes', [
    {'path':'/characters/0/name'}, {'before':'not the frozen assumption'},
    {'source_quotes':['The witness appeared at trial.']}, {'source_quotes':[]}, {'after':'  '},
])
def test_candidate_revision_cannot_alter_another_field_or_cite_itself(changes):
    from core.planning_assumptions import _validate
    original='The witness appeared at trial.'
    source='The witness disappeared.'
    value={'path':'/assumptions/0','before':original,'after':source,'reason':'Omit trial inference.','source_quotes':[source]}
    with pytest.raises(ValueError):
        _validate({**value,**changes},'/assumptions/0',original,source)
