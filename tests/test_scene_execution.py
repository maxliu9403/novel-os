import hashlib
import json

import pytest

from core.scene_execution import outline_view
from core import reader_investment as ri
import creative_execution as ce
from orchestrator import NovelOrchestrator
from test_creative_execution import setup, SOURCE


OUTLINE = '''# Chapter 1: Refusal

## Chapter Goal
Rin refuses a bargain despite needing shelter.

## Beats
1. She returns the key rather than signing.

## Continuity Notes
- Shelter never grants consent to a mate.

## Ending Hook
She asks who else will lose their bed.

[CHAPTER_CONTRACT]
{"preserve_facts": ["Consent is not payment."], "goal": "Refuse the bargain"}
[/CHAPTER_CONTRACT]
'''


@pytest.mark.parametrize('outline', [OUTLINE, OUTLINE.replace('\n', '\r\n'), 'An older freeform plan.\n',
                                    '```markdown\n' + OUTLINE + '```\n'])
def test_outline_view_never_discards_or_invents_source(outline):
    view = outline_view(outline)
    assert ''.join(s['text'] for s in view['segments']) == outline
    assert view['source_sha256'] == hashlib.sha256(outline.encode()).hexdigest()
    assert view['coverage'] == 'complete_original_outline'
    lines = outline.splitlines(keepends=True)
    for segment in view['segments']:
        assert ''.join(lines[segment['start_line'] - 1:segment['end_line']]) == segment['text']


def test_continuity_and_contract_are_not_presented_as_scene_beats():
    groups = outline_view(OUTLINE)['segments']
    assert next(s for s in groups if s['role'] == 'continuity_constraints')['text'].startswith('## Continuity Notes')
    contract = next(s for s in groups if s['role'] == 'chapter_contract')['text']
    assert '"preserve_facts"' in contract and '"goal"' in contract
    scene = ''.join(s['text'] for s in groups if s['role'] == 'scene_plan')
    assert 'returns the key' in scene and 'Ending Hook' in scene
    assert 'Shelter never' not in scene and 'preserve_facts' not in scene


@pytest.mark.parametrize('mode', ['new_workshop', 'new_direct', 'old_v2', 'old_without_method'])
def test_actual_scribe_request_uses_view_only_for_new_frozen_policy(setup, mode):
    project, old_runtime, _, state, _ = setup
    state.save_state()
    engine = NovelOrchestrator(str(project), state_update_mode='proposal_only')
    policy = ri.snapshot()
    if mode == 'old_v2':
        policy['schema'] = 'reader-investment.v2'
        policy['sha256'] = ri._digest({k: v for k, v in policy.items() if k != 'sha256'})
    engine.reader_investment = policy
    if mode in {'new_workshop', 'old_v2'}:
        handoff = ce.make_handoff(source=SOURCE, origin={}, reader_investment=policy)
        engine.creative_execution = ce.CreativeExecution.open(project=project, run_id=mode, handoff=handoff)
    elif mode == 'old_without_method':
        engine.creative_execution = old_runtime
    (project / 'outputs/chapter_001_outline.md').write_text(OUTLINE)
    engine.write_chapter(1, dry_run=True)
    user = (project / 'outputs/chapter_001_scribe_prompt.md').read_text()
    if mode.startswith('new_'):
        view = json.loads(user.split('[SCENE_EXECUTION_BRIEF]\n')[1].split('\n[/SCENE_EXECUTION_BRIEF]')[0])
        assert ''.join(s['text'] for s in view['segments']) == OUTLINE
    else:
        assert user.startswith(OUTLINE)
        assert '[SCENE_EXECUTION_BRIEF]' not in user
    assert (project / 'outputs/chapter_001_outline.md').read_text() == OUTLINE
