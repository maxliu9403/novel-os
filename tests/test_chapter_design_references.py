"""Design provenance survives chapter delivery without becoming author evidence."""
from copy import deepcopy
import hashlib
import json
import re

import pytest

from core.chapter_review import DESIGN_REFERENCE_RULES, design_reference_context, project_outline
from core.planning_design_references import build_catalog
from commercial_fixtures import legacy_chapter_contract_payload
from contracts import ChapterContract
from test_creative_execution import SOURCE, setup  # noqa: F401 -- shared frozen-runtime fixture


SEED = {'logline': 'Rin builds an archive alliance after refusing the pack.',
        'causal_chain': ['Rin hides a second key in a repaired lantern.']}


def catalog():
    return build_catalog(SEED, hashlib.sha256(SOURCE.encode()).hexdigest())


def attach(foundation):
    value = catalog()
    ids = [entry['id'] for entry in value['entries']]
    assert len(ids) >= 2
    foundation['design_references'] = value
    foundation['source_anchors'] = [{'id': 'author-choice', 'quote': SOURCE}]
    foundation['chapter_map'] = []
    for chapter in foundation['chapters']:
        refs = [ids[(chapter['number'] - 1) % len(ids)]]
        chapter['design_reference_ids'] = refs
        chapter['source_anchor_ids'] = ['author-choice']
        foundation['chapter_map'].append({'number': chapter['number'], 'design_reference_ids': refs,
                                          'source_anchor_ids': ['author-choice']})
    return foundation


def standalone():
    contract = legacy_chapter_contract_payload()
    contract['chapter'] = 1
    second = {**contract, 'chapter': 2}
    return attach({'chapters': [
        {'number': 1, 'title': 'The Lantern', 'scenes': [{'detail': 'A quiet admission.'}], 'execution_contract': contract},
        {'number': 2, 'title': 'The Archive', 'scenes': [{'detail': 'A later alliance.'}], 'execution_contract': second},
    ]})


def test_standalone_projection_resolves_only_current_design_and_keeps_scene_and_contract():
    foundation = standalone()
    before = deepcopy(foundation)
    rendered = project_outline(foundation, 1)
    payload = json.loads(re.search(r'\[PLANNED_DESIGN_REFERENCES\]\s*(.*?)\s*\[/PLANNED_DESIGN_REFERENCES\]', rendered, re.S)[1])
    assert payload['rule'] == DESIGN_REFERENCE_RULES
    assert payload['seed_sha256'] == foundation['design_references']['seed_sha256']
    assert payload['source_sha256'] == hashlib.sha256(SOURCE.encode()).hexdigest()
    assert payload['chapters'] == [{'number': 1,
        'design_reference_ids': foundation['chapters'][0]['design_reference_ids'],
        'references': [foundation['design_references']['entries'][0]]}]
    assert all(entry['origin'] == 'model_design' for entry in payload['chapters'][0]['references'])
    assert foundation['design_references']['entries'][1]['text'] not in rendered
    assert 'A quiet admission.' in rendered
    assert foundation['chapters'][0]['source_anchor_ids'] == ['author-choice']
    contract = json.loads(re.search(r'\[CHAPTER_CONTRACT\]\s*(.*?)\s*\[/CHAPTER_CONTRACT\]', rendered, re.S)[1])
    assert contract == ChapterContract.from_dict(foundation['chapters'][0]['execution_contract']).to_dict()
    assert foundation == before


def test_omitted_chapter_reference_resolves_only_its_own_map_without_mutating_foundation():
    foundation = standalone()
    del foundation['chapters'][0]['design_reference_ids']
    before = deepcopy(foundation)
    resolved = design_reference_context(foundation, [1])
    assert resolved['chapters'][0]['design_reference_ids'] == foundation['chapter_map'][0]['design_reference_ids']
    assert foundation == before


@pytest.mark.parametrize('damage', ['unknown_id', 'wrong_chapter', 'duplicate_id', 'lost_registry', 'altered_registry', 'invalid_origin'])
def test_standalone_projection_rejects_unbound_or_corrupt_design_provenance(damage):
    foundation = standalone()
    if damage == 'unknown_id':
        foundation['chapters'][0]['design_reference_ids'] = ['design_' + '0' * 24]
    elif damage == 'wrong_chapter':
        foundation['chapters'][0]['design_reference_ids'] = foundation['chapters'][1]['design_reference_ids']
    elif damage == 'duplicate_id':
        foundation['chapters'][0]['design_reference_ids'] *= 2
    elif damage == 'lost_registry':
        del foundation['design_references']
    elif damage == 'altered_registry':
        foundation['design_references']['entries'][0]['text'] = 'Changed after freezing'
    else:
        foundation['design_references']['entries'][0]['origin'] = 'author_source'
    before = deepcopy(foundation)
    with pytest.raises(ValueError):
        project_outline(foundation, 1)
    assert foundation == before


def test_registry_free_projection_keeps_exact_legacy_bytes():
    foundation = standalone()
    del foundation['design_references']
    del foundation['chapter_map']
    for chapter in foundation['chapters']:
        del chapter['design_reference_ids']
    chapter = foundation['chapters'][0]
    expected = (f"# Chapter 1 · {chapter['title']}\n\n"
        + json.dumps({k: v for k, v in chapter.items() if k != 'execution_contract'}, ensure_ascii=False, indent=2)
        + '\n\n[CHAPTER_CONTRACT]\n'
        + json.dumps(ChapterContract.from_dict(chapter['execution_contract']).to_dict(), ensure_ascii=False)
        + '\n[/CHAPTER_CONTRACT]\n')
    assert project_outline(foundation, 1) == expected


@pytest.mark.parametrize('agent', ['architect', 'scribe', 'continuity_guardian'])
def test_creative_runtime_preserves_registry_and_distinguishes_model_design(setup, agent):
    project, runtime, _, state, _ = setup
    path = project / 'outputs/input/foundation.json'
    original_lock = (runtime.directory / 'lock.json').read_bytes()
    foundation = attach(json.loads(path.read_text()))
    path.write_text(json.dumps(foundation))
    original = path.read_bytes()
    context = runtime.chapter_context(1, agent, state)
    planned = context['planned_design']
    assert planned['book']['design_references'] == foundation['design_references']
    assert planned['book']['source_anchors'] == [{'id': 'author-choice', 'quote': SOURCE}]
    assert planned['chapters'] == foundation['chapters'][:2]
    assert planned['design_reference_rule'] == DESIGN_REFERENCE_RULES
    assert 'model-generated' in planned['authority'] and 'NOT author testimony' in planned['authority']
    assert context['approved_previous_prose'] == []
    assert path.read_bytes() == original and (runtime.directory / 'lock.json').read_bytes() == original_lock
    assert not (runtime.directory / 'requests').exists()


def test_creative_runtime_rejects_catalog_from_another_source_before_model(setup):
    project, runtime, _, state, _ = setup
    path = project / 'outputs/input/foundation.json'
    foundation = attach(json.loads(path.read_text()))
    foundation['design_references'] = build_catalog(SEED, hashlib.sha256(b'Another original book').hexdigest())
    path.write_text(json.dumps(foundation))
    with pytest.raises(ValueError, match='design references are invalid'):
        runtime.chapter_context(1, 'scribe', state)
    assert not (runtime.directory / 'requests').exists()


def test_registry_free_runtime_preserves_existing_authority_and_policy(setup):
    _, runtime, _, state, _ = setup
    context = runtime.chapter_context(1, 'scribe', state)
    assert context['planned_design']['authority'] == 'author plan, NOT occurred events or POV knowledge'
    assert 'design_reference_rule' not in context['planned_design']
    assert 'design_references' not in context['planned_design']['book']


def test_model_design_reference_does_not_satisfy_author_conflict_evidence():
    from core.planning_evidence_plan import validate_plan
    foundation = standalone()
    quote = foundation['design_references']['entries'][0]['text']
    assert quote not in SOURCE
    review = {'issues': [{'scope': 'global', 'message': 'Check generated design against author text.'}]}
    issue = {'issue_index': 0, 'classification': 'source_conflict', 'reason': 'Compare the two asserted statements.',
             'affected_chapters': [1], 'writable_chapters': [], 'dependency_chapters': [],
             'source_quotes': [SOURCE, quote],
             'candidate_evidence': [{'path': '/design_references/entries/0/text', 'quote': quote}]}
    with pytest.raises(ValueError, match='verbatim passage from the adopted source'):
        validate_plan(foundation, review, SOURCE, {'issues': [issue]})
    issue.update(classification='interpretation', source_quotes=[SOURCE])
    assert validate_plan(foundation, review, SOURCE, {'issues': [issue]})['issues'][0]['candidate_evidence'] == issue['candidate_evidence']
