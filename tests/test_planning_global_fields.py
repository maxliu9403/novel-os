import json

import pytest

from outline_planning import OutlinePlanningError
from test_outline_planning import PlanningModel, global_design, make_planner
from test_skeleton_projection import source


class MetadataModel(PlanningModel):
    def __init__(self, *, change_response=None):
        super().__init__()
        self.change_response = change_response

    def complete(self, system, user):
        data = json.loads(user.rsplit('\n\n', 1)[1])
        if user.startswith('STAGE: global_design'):
            self.calls.append(('global', data))
            result = global_design(data)
            result['source_anchors'][0]['quote'] = 'Complete global 1:'
            result['characters'][0].pop('name')
            result['chapter_map'][-1]['source_anchor_ids'].append('missing_citation')
            return json.dumps(result)
        if user.startswith('STAGE: format_correction'):
            self.calls.append(('format', data))
            old = json.loads(data['completed_response'])
            old['characters'][0]['name'] = 'Mara'
            old['ending_note'] = 'This additional choice belongs to the latest saved response.'
            return json.dumps(old)
        if user.startswith('STAGE: global_metadata'):
            self.calls.append(('fields', data))
            wanted = data['requested']
            patch = {'names': {k: {'name': 'Mara', 'quote': 'Mara keeps the studio.'}
                                for k in wanted['names']},
                     'anchors': {k: f"Exact scene {uses[0]['number']}: 限定代价𠀀。"
                                 for k, uses in wanted['anchors'].items()}}
            if self.change_response:
                self.change_response(patch)
            return json.dumps(patch)
        return super().complete(system, user)


def book(n):
    return source(n).replace('Complete global 1:', 'Mara keeps the studio. Complete global 1:')


def call_global(planner):
    context = {**planner._context(), 'volume_contract_template': planner.volume_template}
    return planner._call('global', planner.global_prompt, context, planner._global,
                         repair_fields=planner._repair_global_fields)


def test_many_chapters_correct_only_the_fields_in_one_reply(tmp_path):
    model = MetadataModel()
    planner = make_planner(tmp_path, model, count=100, source=book(100))
    corrected = call_global(planner)
    assert [stage for stage, _ in model.calls] == ['global', 'fields']
    request = model.calls[-1][1]
    assert set(request['requested']['names']) == {'mara'}
    assert [row['number'] for row in request['requested']['anchors']['missing_citation']] == [100]
    assert request['input']['complete_adopted_source'] == planner.source
    original = json.loads(next((planner.cache / 'raw').glob('global.*.response.txt')).read_text())
    assert original['chapter_map'] == corrected['chapter_map']
    assert corrected['characters'][0]['name'] == 'Mara'
    anchor = corrected['source_anchors'][-1]
    a, b = anchor['source_chars']
    assert anchor['chapter'] == 100 and planner.source[a:b] == anchor['quote']
    original['characters'][0]['name'] = 'Mara'
    original['source_anchors'].append(anchor)
    assert planner._global(original) == corrected

    no_calls = MetadataModel()
    resumed = make_planner(tmp_path, no_calls, count=100, source=book(100))
    assert call_global(resumed) == corrected
    assert no_calls.calls == []


def test_recovery_continues_from_complete_historical_format_response(tmp_path, monkeypatch):
    old = make_planner(tmp_path, MetadataModel(), count=4, source=book(4))
    # Simulate the historical engine on the same frozen prompt and identity.
    monkeypatch.setattr(old, '_repair_global_fields', lambda *args: None)
    with pytest.raises(OutlinePlanningError, match='one bounded format correction'):
        call_global(old)
    assert [stage for stage, _ in old.client.calls] == ['global', 'format']
    old_records = {p: p.read_bytes() for p in old.cache.rglob('*') if p.is_file()}

    recovered = make_planner(tmp_path, MetadataModel(), count=4, source=book(4))
    corrected = call_global(recovered)
    assert [stage for stage, _ in recovered.client.calls] == ['fields']
    assert recovered.client.calls[0][1]['requested']['names'] == {}
    assert corrected['ending_note'].startswith('This additional choice')
    assert all(p.read_bytes() == data for p, data in old_records.items())


def test_archived_correction_before_checkpoint_commit_reuses_matching_receipt(tmp_path, monkeypatch):
    planner = make_planner(tmp_path, MetadataModel(), count=4, source=book(4))
    with monkeypatch.context() as old_engine:
        old_engine.setattr(planner, '_repair_global_fields', lambda *args: None)
        with pytest.raises(OutlinePlanningError, match='one bounded format correction'):
            call_global(planner)
    (planner.cache / 'global-format-correction-response.json').unlink()
    original = next((planner.cache / 'raw').glob('global-format-correction.*.response.txt')).read_text()
    resumed = make_planner(tmp_path, MetadataModel(), count=4, source=book(4))

    class VerifiedArchive:
        def prepare(self, agent, prompt, *args, **kwargs):
            return prompt, {'agent': agent}

        def recover_response(self, agent, prompt, receipt, archived):
            assert archived == original and agent == receipt['agent'] == 'outline_planner'
            return original

        def complete(self, client, agent, prompt, receipt):
            return client.complete('', prompt)

    resumed.creative_execution = VerifiedArchive()
    result = call_global(resumed)
    assert result['ending_note'].startswith('This additional choice')
    assert [stage for stage, _ in resumed.client.calls] == ['fields']


def test_cli_without_receipts_can_redispatch_an_unverified_orphan(tmp_path):
    planner = make_planner(tmp_path, MetadataModel(), count=4, source=book(4))
    call_global(planner)
    (planner.cache / 'global-fields-v1-format-correction-response.json').unlink()
    resumed = make_planner(tmp_path, MetadataModel(), count=4, source=book(4))
    corrected = call_global(resumed)
    assert corrected['characters'][0]['name'] == 'Mara'
    assert [stage for stage, _ in resumed.client.calls] == ['fields']


def test_unstructured_chapter_sources_keep_existing_bounded_correction(tmp_path):
    # The text contains exact quotes but has no authoritative chapter boundaries.
    planner = make_planner(tmp_path, MetadataModel(), count=4, source='Complete global 1: Mara keeps the studio.')
    with pytest.raises(OutlinePlanningError, match='one bounded format correction'):
        call_global(planner)
    assert [stage for stage, _ in planner.client.calls] == ['global', 'format']


def test_non_object_global_rows_keep_existing_correction(tmp_path):
    def malformed(value):
        value['chapter_map'][0] = 'Chapter pending'
    planner = make_planner(tmp_path, PlanningModel(mutate_global=malformed), count=4)
    with pytest.raises(OutlinePlanningError, match='one bounded format correction'):
        call_global(planner)
    assert [stage for stage, _ in planner.client.calls] == ['global', 'format']


def test_roles_shared_by_characters_cannot_supply_their_names():
    from core.planning_global_fields import targets
    source_text = 'The accountant signs. Anna, her sister Mina, and Mara are home.'
    candidate = {'characters': [{'id': 'char_ann', 'aliases': ['The accountant']},
                                {'id': 'char_mina', 'name': 'Mina', 'aliases': ['The accountant']}],
                 'source_anchors': [], 'chapter_map': []}
    with pytest.raises(ValueError, match='no unambiguous source-backed ID or alias'):
        targets(candidate, source_text)
    candidate['characters'][0]['aliases'].append('Mara')
    names = targets(candidate, source_text)['names']
    assert names['char_ann']['source_labels'] == ['Mara']


def test_public_planner_publishes_scoped_global_candidate_only_after_review(tmp_path):
    planner = make_planner(tmp_path, MetadataModel(), count=4, source=book(4))
    result = planner.run()
    assert result['characters'][0]['name'] == 'Mara'
    assert len(result['chapters']) == 4
    assert [stage for stage, _ in planner.client.calls] == ['global', 'fields', 'batch', 'review']
    assert json.loads((planner.directory / 'coverage.json').read_text())['semantic_review'] == 'passed'


@pytest.mark.parametrize('corrupt', [
    lambda patch: patch.update(title='Different story'),
    lambda patch: patch['names'].update(other={'name': 'Mara', 'quote': 'Mara keeps the studio.'}),
    lambda patch: patch['names']['mara'].update(name='unsupported name'),
    lambda patch: patch['names']['mara'].update(name='studio'),
    lambda patch: patch['anchors'].update(missing_citation='Exact scene 1: 限定代价𠀀。'),
    lambda patch: patch['anchors'].update(missing_citation='限定代价𠀀。'),
])
def test_invalid_patch_is_preserved_without_replacing_story_or_repeating_paid_calls(tmp_path, corrupt):
    planner = make_planner(tmp_path, MetadataModel(change_response=corrupt), count=4, source=book(4))
    with pytest.raises(OutlinePlanningError, match='Global metadata correction remains invalid'):
        planner.run()
    assert not (planner.cache / 'global.json').exists()
    assert not (planner.directory / 'coverage.json').exists()
    assert len(list((planner.cache / 'raw').glob('*.response.txt'))) == 2
    resumed = make_planner(tmp_path, MetadataModel(), count=4, source=book(4))
    with pytest.raises(OutlinePlanningError, match='Global metadata correction remains invalid'):
        resumed.run()
    assert resumed.client.calls == []
