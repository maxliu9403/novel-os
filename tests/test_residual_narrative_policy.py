from core import planning_residual_edits as narrative, planning_residual_evidence as evidence
from test_planning_residual_edits import Planner, respond, book, issue, repair


def test_new_editor_distinguishes_author_silence_from_forbidden_candidate_transitions():
    p = Planner(respond)
    selected = []
    original = p._call
    def capture(name, instruction, context, validate, **kwargs):
        selected.append(instruction)
        return original(name, instruction, context, validate, **kwargs)
    p._call = capture
    repair(p, book(), [issue()])
    assert selected == [narrative.CURRENT_INSTRUCTION]
    assert 'Source silence is not a prohibition' in selected[0]
    assert 'inspect all supplied later dependencies' in selected[0]


def test_completed_and_partitioned_legacy_editor_contracts_remain_frozen():
    for suffix, value in [('', {'edits': [], 'reason': 'Already recorded'}), ('-split', {'split': True})]:
        p = Planner(respond)
        context = {'fields': []}
        name, deps = evidence._key('narrative', 'candidate', narrative.INSTRUCTION, context)
        p._save(name + suffix, deps, value)
        assert narrative._instruction(p, 'candidate', context) == narrative.INSTRUCTION
        assert not p.calls


def test_interrupted_legacy_direct_editor_retains_wire_policy(tmp_path):
    p = Planner(respond)
    p.cache = tmp_path
    (tmp_path / 'raw').mkdir()
    context = {'fields': []}
    name, _ = evidence._key('narrative', 'candidate', narrative.INSTRUCTION, context)
    path = tmp_path / 'raw' / (name + '.request.prompt.txt')
    path.write_text('immutable historical prompt')
    assert narrative._instruction(p, 'candidate', context) == narrative.INSTRUCTION
    assert path.read_text() == 'immutable historical prompt'
