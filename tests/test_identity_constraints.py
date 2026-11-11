"""Role uncertainty survives foundation publication, canon round-trips and every prompt route."""
from copy import deepcopy
import json

import pytest

from canon_ledger import canonical_canon_sha
from context_pack import build_context_pack, format_context_pack
from core.identity_constraints import RULES, validate_identity_constraints
from state_codec import blank_story_state, state_from_payload, state_payload
from state_manager import StoryState
from story_foundation import apply_story_foundation
from test_creative_execution import setup

SOURCE = 'A courier brings the sealed letter. Later an unnamed guard blocks the gate.'


def constraint():
    return {'character_ids':['guard','courier'], 'relation':'unknown',
            'reason':'The two occurrences have no source-backed identity link.',
            'source_evidence':[{'character_id':'courier','quote':'A courier brings the sealed letter.'},
                               {'character_id':'guard','quote':'Later an unnamed guard blocks the gate.'}]}


def foundation():
    return {'characters':[{'id':'courier','name':'The courier','role':'supporting', 'gender':'', 'pronouns':''},
                          {'id':'guard','name':'The guard','role':'supporting', 'gender':'', 'pronouns':'he/him'}],
            'chapters':[{'number':1,'title':'Letter','summary':'The courier arrives.','pov':'courier'}],
            'plot_threads':[{'id':'secret','name':'The sealed letter'}],
            'identity_constraints':[constraint()]}


@pytest.mark.parametrize('defect', ['missing_role','same_id','same','distinct','fabricated','missing_evidence',
                                   'unrelated_evidence','duplicate_pair','bad_reason','wrong_shape'])
def test_contract_rejects_unsupported_or_ambiguous_identity_claims(defect):
    value = [constraint()]
    if defect == 'missing_role':
        value[0]['character_ids'][0] = 'absent'
    elif defect == 'same_id':
        value[0]['character_ids'] = ['guard','guard']
    elif defect in ('same','distinct'):
        value[0]['relation'] = defect
    elif defect == 'fabricated':
        value[0]['source_evidence'][0]['quote'] = 'He is the same courier.'
    elif defect == 'missing_evidence':
        value[0]['source_evidence'].pop()
    elif defect == 'unrelated_evidence':
        value[0]['source_evidence'][0]['character_id'] = 'absent'
    elif defect == 'duplicate_pair':
        value.append({**deepcopy(value[0]), 'character_ids':['courier','guard']})
    elif defect == 'bad_reason':
        value[0]['reason'] = ''
    else:
        value[0]['other'] = True
    with pytest.raises(ValueError):
        validate_identity_constraints(value, ['courier','guard'], source=SOURCE)


def test_foundation_canon_and_journal_round_trip_preserve_unknown_without_copying_state(tmp_path):
    state = StoryState(str(tmp_path / 'novel'))
    apply_story_foundation(state, foundation(), 2000)
    state.characters['guard'].knowledge.append('The vault combination')
    state.characters['guard'].outcome_state = 'dead'
    state.characters['guard'].outcome_evidence = 'The guard died at the gate.'
    expected = validate_identity_constraints([constraint()], state.characters, source=SOURCE)
    assert state.story_bible['identity_constraints'] == expected
    assert state.characters['courier'].knowledge == []
    assert state.characters['courier'].outcome_state == ''
    assert state.characters['courier'].pronouns == ''
    assert state.characters['guard'].pronouns == 'he/him'
    before = canonical_canon_sha(state)
    state.save_state()
    loaded = StoryState(str(tmp_path / 'novel'))
    assert canonical_canon_sha(loaded) == before
    restored = state_from_payload(state_payload(loaded, "2026-11-11T00:00:00+00:00"))
    assert canonical_canon_sha(restored) == before
    assert restored.get_continuity_context(1)['identity_constraints'] == expected
    restored.story_bible['identity_constraints'][0]['reason'] += ' preserved'
    assert canonical_canon_sha(restored) != before


def test_legacy_absent_or_empty_contract_does_not_add_canon_fields_or_change_hash(tmp_path):
    old = foundation()
    del old['identity_constraints']
    state = StoryState(str(tmp_path / 'novel'))
    apply_story_foundation(state, old, 2000)
    before = canonical_canon_sha(state)
    apply_story_foundation(state, {**old,'identity_constraints':[]}, 2000)
    assert 'identity_constraints' not in state.story_bible
    state.save_state()
    loaded = StoryState(str(tmp_path / 'novel'))
    assert canonical_canon_sha(loaded) == before
    assert 'identity_constraints' not in loaded.get_continuity_context(1)


def test_foundation_replay_preserves_author_identity_and_existing_uncertainty(tmp_path):
    state = StoryState(str(tmp_path / 'novel'))
    apply_story_foundation(state, foundation(), 2000)
    state.update_character('guard', {'pronouns':'they/them'}, allow_identity_change=True)
    before = canonical_canon_sha(state)
    replacement = foundation()
    replacement['identity_constraints'] = []
    apply_story_foundation(state, replacement, 2000)
    assert state.characters['guard'].pronouns == 'they/them'
    assert state.story_bible['identity_constraints']
    assert canonical_canon_sha(state) == before
    # A legacy published state with no constraints also keeps its prior canon.
    state.story_bible.pop('identity_constraints')
    before = canonical_canon_sha(state)
    apply_story_foundation(state, foundation(), 2000)
    assert canonical_canon_sha(state) == before


@pytest.mark.parametrize('purpose', ['architect','scribe','guardian','continue','consequence'])
def test_tiny_optional_context_budget_keeps_complete_identity_evidence(tmp_path, purpose):
    state = StoryState(str(tmp_path / 'novel'))
    apply_story_foundation(state, foundation(), 2000)
    expected = json.dumps(state.story_bible['identity_constraints'], ensure_ascii=False, sort_keys=True, indent=2)
    rendered = format_context_pack(build_context_pack(state, 1, purpose=purpose), max_chars=50)
    assert expected in rendered and RULES in rendered
    assert 'knowledge, memories, pronouns, actions, injuries or fate' in rendered


def test_guardian_fallback_codex_also_preserves_mandatory_uncertainty(tmp_path, monkeypatch):
    state = StoryState(str(tmp_path / 'novel'))
    apply_story_foundation(state, foundation(), 2000)
    def broken(*args, **kwargs):
        raise RuntimeError('Optional context unavailable')
    monkeypatch.setattr('context_pack.build_context_pack', broken)
    result = state.get_continuity_context(1)
    assert result['identity_constraints'] == state.story_bible['identity_constraints']
    assert RULES in result['codex_block']
    assert constraint()['source_evidence'][1]['quote'] in state.format_codex_block(max_chars=50)


def test_invalid_role_reference_is_rejected_by_state_and_journal_load(tmp_path):
    state = StoryState(str(tmp_path / 'novel'))
    apply_story_foundation(state, foundation(), 2000)
    payload = state_payload(state, "2026-11-11T00:00:00+00:00")
    del payload['characters']['guard']
    with pytest.raises(ValueError, match='existing role'):
        state_from_payload(payload)
    state.save_state()
    path = tmp_path / 'novel' / 'outputs' / 'state' / 'story_state.json'
    data = json.loads(path.read_text())
    del data['characters']['guard']
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='existing role'):
        StoryState(str(tmp_path / 'novel'))


def test_all_creative_agents_receive_unknown_identity_contract_and_rule(setup):
    project, runtime, _, state, _ = setup
    path = project / 'outputs/input/foundation.json'
    book = json.loads(path.read_text())
    book['characters'].extend(foundation()['characters'])
    book['identity_constraints'] = [constraint()]
    path.write_text(json.dumps(book))
    for role in ('architect','scribe','editor','continuity_guardian','style_curator'):
        context = runtime.chapter_context(1, role, state)
        assert context['planned_design']['book']['identity_constraints'] == [constraint()]
        assert context['planned_design']['identity_constraint_rule'] == RULES


def test_blank_reconciliation_can_explicitly_replace_constraints_but_omission_preserves_them(tmp_path):
    current = StoryState(str(tmp_path / 'novel'))
    apply_story_foundation(current, foundation(), 2000)
    replacement = {**foundation(), 'identity_constraints':[]}
    candidate = blank_story_state(tmp_path / 'novel')
    candidate.story_bible = deepcopy(current.story_bible)
    apply_story_foundation(candidate, replacement, 2000)
    assert 'identity_constraints' not in candidate.story_bible
    assert current.story_bible['identity_constraints']
    del replacement['identity_constraints']
    inherited = blank_story_state(tmp_path / 'novel')
    inherited.story_bible = deepcopy(current.story_bible)
    apply_story_foundation(inherited, replacement, 2000)
    assert inherited.story_bible['identity_constraints'] == current.story_bible['identity_constraints']


def test_native_foundation_publication_carries_constraints_into_the_receipt_and_state(tmp_path):
    from foundation_canon import FoundationCanonService
    from state_manager import initialize_project
    project = tmp_path / 'novel'
    initialize_project(str(project), 'The sealed letter', 'mystery')
    service = FoundationCanonService(project)
    receipt = service.initialize(foundation(), target_words=2000, idempotency_key='first-outline')
    published = StoryState(str(project))
    assert published.story_bible['identity_constraints']
    assert receipt.new_canon_sha == canonical_canon_sha(published)
    assert service.initialize(foundation(), target_words=2000, idempotency_key='first-outline') == receipt
