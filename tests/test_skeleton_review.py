from copy import deepcopy
import pytest
from core.skeleton_review import record, revise
from test_workshop_api import complete_prompt
from test_workshop_capacity import review


@pytest.mark.parametrize('mutation', [
    lambda r: r['issues'][0].update(chapter=999),
    lambda r: r['issues'][0].update(quote='这段文字从未出现在对应章节中。'),
    lambda r: r['issues'].append(deepcopy(r['issues'][0])),
    lambda r: r['issues'][0].update(replacement='### Chapter 99 — New plot\nText'),
    lambda r: r['issues'][0].update(replacement='[NARRATIVE_FORMAT_JSON]\n{}\n[/NARRATIVE_FORMAT_JSON]'),
    lambda r: r.update(checked=['opening'] * 6),
])
def test_bad_review_cannot_claim_safe_edits_or_quality_pass(tmp_path, mutation):
    raw = review()
    mutation(raw)
    state = record(complete_prompt(tmp_path), raw, 8)
    assert state['status'] == 'unavailable' and not state['issues']


def test_exact_source_hash_and_issue_identity_required(tmp_path):
    prompt = complete_prompt(tmp_path)
    state = record(prompt, review(), 8)
    assert state['status'] == 'proposed'
    for source, ids in [(prompt + '\nchanged', ['issue_1']), (prompt, ['unknown']),
                        (prompt, ['issue_1', 'issue_1'])]:
        with pytest.raises(ValueError):
            revise(source, state, ids)
    corrupted = deepcopy(state)
    corrupted['issues'][0]['replacement'] = 'A completely different accepted ending.'
    with pytest.raises(ValueError):
        revise(prompt, corrupted, ['issue_1'])


def test_notes_are_not_mistaken_for_applied_or_verified_changes(tmp_path):
    raw = review()
    raw.update(issues=[], notes=['需要作者决定是否改变主角的结局。'])
    state = record(complete_prompt(tmp_path), raw, 8)
    assert state['status'] == 'proposed'
    assert revise(complete_prompt(tmp_path), state, []) == complete_prompt(tmp_path)


def test_editorial_patch_cannot_change_assembly_owned_budget(tmp_path):
    prompt = complete_prompt(tmp_path)
    original = review()['issues'][0]['quote']
    prompt = prompt.replace(original, original + '\n\n### 本章补充规划记录\n\n```json\n{"target_words": 550}\n```', 1)
    raw = review()
    raw['issues'][0].update(quote='{"target_words": 550}', replacement='{"target_words": 150}')
    assert record(prompt, raw, 8)['status'] == 'unavailable'
    # Ordinary scene improvements remain available before the protected record.
    assert record(prompt, review(), 8)['status'] == 'proposed'
