"""Publication migration changes the storage vocabulary, never the story."""
from copy import deepcopy
import json

import pytest

from core.planning_arc_vocabulary import publication_contract
from core.planning_errors import OutlinePlanningError
from test_outline_planning import PlanningModel, make_planner


def story(*descriptions):
    arcs, characters = [], []
    for i, description in enumerate(descriptions):
        key = f'arc_{i}'
        characters.append({'id': key, 'arc': 'Protect the studio but refuse the sale.'})
        arcs.append({'character_id': key, 'required_arc_stage': description,
                     'required_choice': 'Refuse the sale.', 'required_outcome': 'Keep the studio.'})
    return {'characters': characters, 'ending_contract': {'enforce': True, 'character_arcs': arcs},
            'chapters': [{'number': 1, 'summary': 'The offer remains unanswered.'}]}


class Classifier(PlanningModel):
    def __init__(self, stage='climax', *, fabricated_quote=False):
        super().__init__()
        self.stage, self.fabricated_quote = stage, fabricated_quote

    def complete(self, system, user):
        self.calls.append(user)
        if user.startswith('STAGE: format_correction'):
            return json.loads(user.rsplit('\n\n', 1)[1])['completed_response']
        assert user.startswith('STAGE: ending_arc_vocabulary')
        records = json.loads(user.rsplit('\n\n', 1)[1])['obligations']
        return json.dumps({'stages': {k: {'stage': self.stage, 'quote': (
            'Not in this obligation' if self.fabricated_quote else row['obligation']['required_choice'])}
            for k, row in records.items()}})


def test_published_contract_keeps_original_language_and_only_classifies_ambiguous_rows(tmp_path):
    model = Classifier()
    planner = make_planner(tmp_path, model)
    candidate = story('complete', 'completed', 'still deciding what to risk', 'middle')
    original = deepcopy(candidate)
    adapted = publication_contract(planner, candidate)
    assert candidate == original
    arcs = adapted['character_arcs']
    assert [a['required_arc_stage'] for a in arcs] == ['resolution', 'resolution', 'climax', 'middle']
    assert [a.get('reviewed_stage_description') for a in arcs] == [
        'complete', 'completed', 'still deciding what to risk', None]
    assert list(json.loads(model.calls[0].rsplit('\n\n', 1)[1])['obligations']) == ['arc_2']
    for source, published in zip(original['ending_contract']['character_arcs'], arcs):
        assert all(published[k] == v for k, v in source.items() if k != 'required_arc_stage')
    assert publication_contract(make_planner(tmp_path, model), candidate) == adapted
    assert len(model.calls) == 1


@pytest.mark.parametrize('stage,invalid_quote', [('undetermined', False), ('resolution', True), ('impossible', False)])
def test_inconclusive_or_unsubstantiated_classification_cannot_publish_or_repurchase(tmp_path, stage, invalid_quote):
    model = Classifier(stage, fabricated_quote=invalid_quote)
    candidate = story('caught between two incompatible promises')
    original = deepcopy(candidate)
    for _ in range(2):
        with pytest.raises(OutlinePlanningError):
            publication_contract(make_planner(tmp_path, model), candidate)
    assert candidate == original and len(model.calls) == (1 if stage == 'undetermined' else 2)


def test_narrow_connection_partitions_whole_arc_obligations_and_resumes_each_once(tmp_path):
    from core.gemini_gateway import input_bytes
    from core.outline_planning import _json
    from core.planning_arc_vocabulary import INSTRUCTION

    model = Classifier()
    planner = make_planner(tmp_path, model)
    candidate = story('the promise remains open', 'the decision is still hers')
    rows = {c['id']: {'character': c, 'obligation': a} for c, a in zip(
        candidate['characters'], candidate['ending_contract']['character_arcs'])}
    model.planning_input_bytes = max(input_bytes(planner._stage_instructions('story_design'),
        INSTRUCTION + '\n\n' + _json({'obligations': {k: row}})) for k, row in rows.items())
    expected = publication_contract(planner, candidate)
    sent = [json.loads(user.rsplit('\n\n', 1)[1])['obligations'] for user in model.calls]
    assert sent == [{k: v} for k, v in rows.items()]
    model.planning_input_bytes = None
    assert publication_contract(make_planner(tmp_path, model), candidate) == expected
    assert len(model.calls) == 2


def test_classification_reuses_verified_reply_after_checkpoint_write_failure(tmp_path, monkeypatch):
    model, candidate = Classifier(), story('caught between two promises')
    planner = make_planner(tmp_path, model)
    receipts = {}

    class ArchivedExecution:
        def prepare(self, agent, prompt, *args, **kwargs):
            return prompt, {'user': prompt, 'agent': agent}

        def complete(self, client, agent, prompt, receipt):
            receipts[agent, prompt] = client.complete('', prompt)
            return receipts[agent, prompt]

        def recover_response(self, agent, prompt, receipt, raw):
            assert receipt == {'user': prompt, 'agent': agent}
            return raw if receipts.get((agent, prompt)) == raw else None

    planner.creative_execution = ArchivedExecution()
    save = planner._save

    def interrupted(name, *args, **kwargs):
        if name.endswith('-response'):
            raise OSError('interrupted after raw reply was saved')
        return save(name, *args, **kwargs)

    monkeypatch.setattr(planner, '_save', interrupted)
    with pytest.raises(OSError, match='interrupted'):
        publication_contract(planner, candidate)
    resumed = make_planner(tmp_path, model)
    resumed.creative_execution = planner.creative_execution
    arc, = publication_contract(resumed, candidate)['character_arcs']
    assert (arc['required_arc_stage'], arc['reviewed_stage_description']) == ('climax', 'caught between two promises')
    assert len(model.calls) == 1
