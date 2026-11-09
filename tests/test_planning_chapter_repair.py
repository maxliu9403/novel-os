"""Chapter repair uses full distant evidence on a genuinely bounded connection."""
from copy import deepcopy
import json

import pytest

from core.gemini_gateway import INPUT_LIMIT_BYTES, input_bytes
from core.planning_chapter_repair import DISCOVER, EDIT, VERIFY, _changes, _selection
from core.planning_context import expand_review_context
from test_hundred_chapter_review_transport import MeteredPlannerModel
from test_outline_planning import chapter, issue, make_planner, review


DISCLOSURE = 'The key belongs to her former partner, and Mara promised to return it unopened.'


class EvidenceModel(MeteredPlannerModel):
    def __init__(self, *, interrupt=False, refuse=False):
        super().__init__()
        self.interrupt = interrupt
        self.refuse = refuse

    def complete(self, system, user):
        context = json.loads(user.rsplit('\n\n', 1)[1])
        size = input_bytes(system, user)
        assert size <= INPUT_LIMIT_BYTES
        if user.startswith((DISCOVER, EDIT, VERIFY)):
            self.request_sizes.append(size)
            if user.startswith(DISCOVER):
                self.calls.append(('discovery', context))
                page = context['scan_chapters']
                chapters = context['candidate']['chapters']
                needed = [c['number'] for c in chapters if c['number'] in page and DISCLOSURE in c['scenes'][0]['subtext']]
                return json.dumps({'inspected_chapters': page, 'needed_chapters': needed, 'unresolved': ''})
            by_number = {c['number']: c for c in context['candidate']['chapters']}
            assert DISCLOSURE == by_number[83]['scenes'][0]['subtext']
            assert len(context['candidate']['chapter_map']) == 100
            if user.startswith(EDIT):
                self.calls.append(('repair_edit', context))
                if self.interrupt:
                    raise TimeoutError('injected transport interruption after all discovery windows')
                return json.dumps({'unresolved': '', 'edits': [
                    {'chapter': 7, 'path': ['incoming_consequence'], 'value': DISCLOSURE},
                    {'chapter': 7, 'path': ['reserved_later_payoffs'], 'value': []}]})
            self.calls.append(('joint_review', context))
            assert by_number[7]['incoming_consequence'] == DISCLOSURE
            return json.dumps(review(issue('chapter', 7)) if self.refuse else review())
        response = super().complete(system, user)
        stage, context = self.calls[-1]
        if stage == 'batch':
            batch = json.loads(response)
            for c in batch['chapters']:
                if c['number'] == 83:
                    c['scenes'][0]['subtext'] = DISCLOSURE
                if c['number'] == 7:
                    c['reserved_later_payoffs'] = ['The missing key']
            return json.dumps(batch)
        if stage == 'review':
            if any(c['number'] == 7 and c['reserved_later_payoffs'] for c in context['candidate']['chapters']):
                return json.dumps(review(issue('chapter', 7)))
        return response


def test_hundred_chapter_remote_dependency_checked_on_resume_and_final_gate(tmp_path):
    client = EvidenceModel(interrupt=True)
    planner = make_planner(tmp_path, client, count=100)
    with pytest.raises(TimeoutError):
        planner.run()
    scans = [ctx for stage, ctx in client.calls if stage == 'discovery']
    assert len(scans) > 1
    assert [n for ctx in scans for n in ctx['scan_chapters']] == list(range(1, 101))
    assert all({7, *ctx['scan_chapters']} == {c['number'] for c in ctx['candidate']['chapters']} for ctx in scans)
    kept = {p: p.read_bytes() for p in planner.cache.glob('*.json')}
    old_target = next(c for c in json.loads((planner.cache / 'batch-002.json').read_text())['payload']['chapters']
                      if c['number'] == 7)
    recovered = EvidenceModel()
    result = make_planner(tmp_path, recovered, count=100).run()
    repaired = result['chapters'][6]
    assert {key: repaired[key] for key in old_target} == {
        **old_target, 'incoming_consequence': DISCLOSURE, 'reserved_later_payoffs': []}
    assert all(path.read_bytes() == contents for path, contents in kept.items() if path.name != 'identity.json')
    assert [stage for stage, _ in recovered.calls].count('discovery') == 0
    edit, confirm = [ctx for stage, ctx in recovered.calls if stage in ('repair_edit', 'joint_review')]
    for ctx in (edit, confirm):
        expanded = expand_review_context(ctx, complete_source=planner.source)
        assert all(expanded['candidate'][key] == planner.locked[key] for key in planner.locked)
        assert ctx['evidence_scope']['included_chapters'] == [6, 7, 8, 83]
        assert ctx['requested_chapters'] == [7]
        assert ctx['complete_adopted_source'] == planner.source
        distant = next(c for c in ctx['candidate']['chapters'] if c['number'] == 83)
        assert distant == {key: result['chapters'][82][key] for key in distant}
    assert (planner.cache / 'validated.json').exists()
    assert any(stage == 'review' for stage, _ in recovered.calls)
    assert max(client.request_sizes + recovered.request_sizes) <= INPUT_LIMIT_BYTES
    silent = EvidenceModel()
    silent.text_input_limit_bytes = lambda: pytest.fail('Completed replay queried the gateway')
    assert make_planner(tmp_path, silent, count=100).run() == result
    assert not silent.calls


def test_joint_dependency_conflict_cannot_pass_or_trigger_a_second_edit(tmp_path):
    planner = make_planner(tmp_path, EvidenceModel(refuse=True), count=100)
    with pytest.raises(ValueError, match='contradicts its dependency evidence'):
        planner.run()
    assert not (planner.cache / 'validated.json').exists()
    silent = EvidenceModel()
    with pytest.raises(ValueError, match='contradicts its dependency evidence'):
        make_planner(tmp_path, silent, count=100).run()
    assert not silent.calls


@pytest.mark.parametrize('record', [
    {'inspected_chapters': [7], 'needed_chapters': [], 'unresolved': ''},
    {'inspected_chapters': [7, 8], 'needed_chapters': [100], 'unresolved': ''},
    {'inspected_chapters': [7, 8], 'needed_chapters': [True], 'unresolved': ''},
    {'inspected_chapters': [7, 8], 'needed_chapters': [8, 8], 'unresolved': ''},
])
def test_discovery_requires_exact_complete_window_coverage(record):
    with pytest.raises(ValueError, match='inspect the whole supplied window'):
        _selection(record, [7, 8])


@pytest.mark.parametrize('edit', [
    {'chapter': 1, 'path': ['number'], 'value': 3},
    {'chapter': 3, 'path': ['summary'], 'value': 'A foreign chapter'},
    {'chapter': 1, 'path': ['scenes'], 'value': []},
    {'chapter': 1, 'path': ['scenes', -1, 'subtext'], 'value': 'Wrong position'},
    {'chapter': 1, 'path': ['source_anchor_ids'], 'value': []},
])
def test_edits_reject_dropped_structure_or_changed_identity(edit):
    candidate = {'chapters': [chapter(1), chapter(2)]}
    baseline = deepcopy(candidate)
    with pytest.raises(ValueError):
        _changes({'edits': [edit], 'unresolved': ''}, candidate, [1])
    assert candidate == baseline


def test_more_than_one_generation_batch_can_be_corrected_and_reviewed_together(tmp_path):
    from test_outline_planning import PlanningModel

    targets = [1, 4, 6, 9, 12]

    class Model(PlanningModel):
        def complete(self, system, user):
            if user.startswith((EDIT, VERIFY)):
                ctx = json.loads(user.rsplit('\n\n', 1)[1])
                self.calls.append(('edit' if user.startswith(EDIT) else 'confirm', ctx))
                assert ctx['requested_chapters'] == targets
                assert len(ctx['issues']) == len(targets)
                assert len(ctx['candidate']['chapters']) == 12
                if user.startswith(VERIFY):
                    return json.dumps(review())
                return json.dumps({'edits': [
                    {'chapter': n, 'path': ['incoming_consequence'],
                     'value': f'The consequences now carry into chapter {n}.'} for n in targets],
                    'unresolved': ''})
            return super().complete(system, user)

    client = Model(reviews=[review(*(issue('chapter', n) for n in targets))])
    result = make_planner(tmp_path, client, count=12).run()
    assert [s for s, _ in client.calls][-4:] == ['review', 'edit', 'confirm', 'review']
    assert all(result['chapters'][n - 1]['incoming_consequence'].endswith(f'into chapter {n}.') for n in targets)


def test_source_projection_keeps_original_remote_text_and_all_global_sections(tmp_path):
    from core.planning_chapter_repair import _context
    from test_planning_repair_context import source
    from test_outline_planning import PlanningModel

    planner = make_planner(tmp_path, PlanningModel(), count=12, source=source())
    candidate = {'chapter_map': [{'number': n} for n in range(1, 13)],
                 'chapters': [chapter(n) for n in range(1, 13)],
                 'extra_global': 'Her sister already knows the secret.'}
    ctx = _context(planner, candidate, [issue('chapter', 2)], [2], [2, 10], scan=[10])
    text = ctx['adopted_source_projection']['text']
    assert 'Exact source choice 10: ' + '限定代价𠀀。' * 80 in text
    assert 'Exact source choice 2: ' + '限定代价𠀀。' * 80 in text
    assert 'Keep this unknown global extension.' in text
    assert all(f'Complete global section {n}:' in text for n in range(1, 18) if n != 12)
    assert ctx['candidate']['chapter_map'] == candidate['chapter_map']
    assert ctx['candidate']['extra_global'] == candidate['extra_global']
    assert ctx['candidate']['chapters'] == [candidate['chapters'][1], candidate['chapters'][9]]


def test_irreducible_source_does_not_dispatch_partial_scan(tmp_path):
    from core.planning_chapter_repair import _plan
    from test_outline_planning import PlanningModel

    client = PlanningModel()
    unstructured = 'The adoption agreement excludes the younger sister.\n' * 1800
    planner = make_planner(tmp_path, client, count=12, source=unstructured)
    chapters = [chapter(n) for n in range(1, 13)]
    with pytest.raises(ValueError, match='indivisible evidence window'):
        _plan(planner, {'chapters': chapters}, review(issue('chapter', 3)), [3], 30_000)
    assert client.calls == []
    assert planner.source == unstructured


def test_completed_legacy_repair_response_remains_usable_after_connection_shrinks(tmp_path):
    from test_outline_planning import PlanningModel

    model = PlanningModel(reviews=[review(issue('chapter', 3))])
    planner = make_planner(tmp_path, model, count=8)
    complete = planner.run()
    (planner.cache / 'repair.json').unlink()  # Crash after the raw reply and before promotion.
    model.planning_input_bytes = 1_600
    model.calls.clear()
    assert planner.run() == complete
    assert not model.calls
    assert not list(planner.cache.glob('repair-evidence-v1*'))
