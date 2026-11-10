from copy import deepcopy
import json
import pytest
from core import planning_author_review as authority
from core import planning_author_decisions
from test_outline_planning import PlanningModel, make_planner, review, issue

CHOICE = {'choice_id':'abc','author_quote':'Mara keeps the studio.','author_quotes':['Mara keeps the studio.','She sells the studio.']}
CHECK = {'choice_id':'abc','status':'honored','reason':'She refuses the sale in the saved resolution.'}


def test_old_pass_does_not_approve_new_author_choice_and_never_overwrites_old_review(tmp_path, monkeypatch):
    planner = make_planner(tmp_path, PlanningModel(), count=4)
    first = planner.run()
    path = planner.cache/'review-initial.json'
    pre = path.read_bytes()
    old_publication = (planner.cache/'validated.json').read_bytes()
    monkeypatch.setattr(planning_author_decisions,'authorized_choices', lambda p:[CHOICE])
    model = PlanningModel()
    resumed = make_planner(tmp_path, model, count=4)
    assert resumed.run() == first
    assert [stage for stage,_ in model.calls]==['review']
    assert model.calls[0][1]['binding_author_choices'] == [CHOICE]
    assert path.read_bytes()==pre and (planner.cache/'validated.json').read_bytes() == old_publication
    assert list(resumed.cache.glob('validated-choices-*.json'))
    model.calls.clear()
    assert resumed.run() == first and not model.calls


def test_brand_new_model_review_sees_choice_from_first_request(tmp_path,monkeypatch):
    monkeypatch.setattr(planning_author_decisions,'authorized_choices',lambda p:[CHOICE])
    model=PlanningModel()
    planner=make_planner(tmp_path,model,count=4)
    planner.run()
    checks=[c for stage,c in model.calls if stage=='review']
    assert len(checks)==1 and checks[0]['binding_author_choices']==[CHOICE]


def test_window_coverage_does_not_mix_absence_with_approval():
    outside={**CHECK, 'status':'outside_window'}
    bad={**CHECK,'status':'contradicted'}
    with pytest.raises(ValueError): authority.inspect_result(review(),[CHOICE])
    with pytest.raises(ValueError): authority.inspect_result({**review(),'author_choice_checks':[bad]},[CHOICE])
    with pytest.raises(ValueError): authority.inspect_result({**review(),'author_choice_checks':[outside]},[CHOICE])
    with pytest.raises(ValueError): authority.reduce_windows([[outside],[outside]],[CHOICE])
    assert authority.reduce_windows([[outside],[CHECK]],[CHOICE])['author_choice_checks']==[CHECK]
    assert authority.reduce_windows([[CHECK],[bad]],[CHOICE])['author_choice_checks']==[bad]


def test_story_choice_keeps_unsupported_second_hand_report_from_becoming_canon(tmp_path, monkeypatch):
    """A newly selected canonical passage invalidates an earlier passed review."""
    monkeypatch.setattr(planning_author_decisions,'authorized_choices',lambda p:[CHOICE])
    class Inconsistent(PlanningModel):
        def complete(self,system,user):
            if user.startswith('STAGE: semantic_review'):
                result=review()
                result['author_choice_checks']=[{**CHECK,'status':'contradicted'}]
                return json.dumps(result)
            return super().complete(system,user)
    planner=make_planner(tmp_path,Inconsistent(),count=4)
    with pytest.raises(ValueError,match='cannot pass'):
        planner.run()
    assert not list(planner.cache.glob('validated*.json'))
