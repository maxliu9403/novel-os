from pathlib import Path
import json
import zipfile

import pytest

from method_experiments import ExperimentLab

ROOT = Path(__file__).resolve().parents[1]


def specification():
    return {
        "schema_version": "novel-method-experiment.v1", "name": "probe", "seed": 41,
        "repetitions": 1,
        "model": {
            "provider": "codex", "model": "fixture-model", "connection_id": "fixture-connection",
            "base_url": "", "configured_base_url": "", "reasoning_effort": "medium",
            "max_tokens": 4096, "timeout_seconds": 60, "azure_endpoint": "", "azure_api_version": "",
        },
        "cases": [{
            "id": "window", "kind": "free_trial_window", "free_trial_end": 3,
            "language": "en", "audience": "Western women", "facts": "Jo owns a repair shop.",
            "prior_context": "", "rules": ["nonfunctional_reaction", "ownership"],
            "chapters": [{"number": n, "outline": f"Jo makes choice {n}.", "min_words": 2, "max_words": 10}
                         for n in range(1, 4)],
        }],
    }


def plan(lab, spec=None):
    return lab.plan(spec or specification(), baseline_system=(ROOT / 'agents/scribe/prompt.md').read_text())


def test_plan_freezes_exact_baseline_and_counts_chapter_calls_without_model(tmp_path):
    lab = ExperimentLab(tmp_path / 'lab', client_factory=lambda _: pytest.fail('plan called a model'))
    result = plan(lab)
    assert result['planned_windows'] == 2
    assert result['planned_model_calls'] == 6
    assert result['systems']['A'] == (ROOT / 'agents/scribe/prompt.md').read_text()
    assert 'Every scene needs at least three senses:' not in result['systems']['B']['window']
    assert plan(lab) == result
    assert not (tmp_path / 'lab/outputs').exists()


class Writer:
    def __init__(self):
        self.calls = []
        self.snapshot = specification()['model']

    def complete(self, system, user):
        self.calls.append((system, user))
        return '<!-- CHAPTER: fixture -->\n# Chapter\n\nJo chose the open door.\n\n[SCRIBE_STATE_UPDATE]\nKey_Events:\n  - Jo chose.\n[/SCRIBE_STATE_UPDATE]'


def test_execution_requires_consent_caps_total_calls_and_resumes_without_replaying(tmp_path):
    writer = Writer()
    lab = ExperimentLab(tmp_path / 'lab', client_factory=lambda snapshot: writer)
    plan(lab)
    with pytest.raises(ValueError, match='explicit'):
        lab.run(max_model_calls=6)
    assert not writer.calls
    first = lab.run(max_model_calls=2, allow_model_calls=True)
    assert first['confirmed_model_calls'] == 2
    assert first['state'] == 'budget_exhausted'
    final = lab.run(max_model_calls=6, allow_model_calls=True)
    assert final['confirmed_model_calls'] == 6
    assert final['state'] == 'complete'
    assert len(writer.calls) == 6
    assert lab.run(max_model_calls=6, allow_model_calls=True) == final
    assert len(writer.calls) == 6
    histories = [json.loads(user[user.index('{'):])['previous_chapters_in_this_sample'] for _, user in writer.calls]
    assert [len(h) for h in histories] == [0, 1, 2, 0, 1, 2]


def test_saved_response_is_completed_after_crash_without_regeneration(tmp_path, monkeypatch):
    writer = Writer()
    lab = ExperimentLab(tmp_path / 'lab', client_factory=lambda _: writer)
    plan(lab)
    original = lab._finish
    monkeypatch.setattr(lab, '_finish', lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        lab.run(max_model_calls=6, allow_model_calls=True)
    assert len(writer.calls) == 1
    monkeypatch.setattr(lab, '_finish', original)
    assert lab.run(max_model_calls=6, allow_model_calls=True)['state'] == 'complete'
    assert len(writer.calls) == 6


def test_uncertain_request_blocks_its_descendants_and_is_not_retried(tmp_path):
    class Interrupted(Writer):
        def complete(self, system, user):
            self.calls.append((system, user))
            raise KeyboardInterrupt()
    interrupted = Interrupted()
    lab = ExperimentLab(tmp_path / 'lab', client_factory=lambda _: interrupted)
    plan(lab)
    with pytest.raises(KeyboardInterrupt):
        lab.run(max_model_calls=6, allow_model_calls=True)
    writer = Writer()
    resumed = ExperimentLab(tmp_path / 'lab', client_factory=lambda _: writer)
    result = resumed.run(max_model_calls=6, allow_model_calls=True)
    assert result['uncertain_model_calls'] == 1
    assert result['confirmed_model_calls'] == 3
    assert len(writer.calls) == 3
    assert result['state'] == 'completed_with_issues'


def test_blind_export_omits_mapping_and_uses_both_editors_not_a_selected_winner(tmp_path):
    writer = Writer()
    lab = ExperimentLab(tmp_path / 'lab', client_factory=lambda _: writer)
    frozen = plan(lab)
    lab.run(max_model_calls=6, allow_model_calls=True)
    exported = lab.export_blind()
    with zipfile.ZipFile(exported['path']) as archive:
        assert 'plan.json' not in archive.namelist()
        assert not any('mapping' in name for name in archive.namelist())
        sheet = json.loads(archive.read('review-template.json'))
        assert 'fixture-model' not in archive.read('context.json').decode()
        assert 'Selected editorial criteria' not in ''.join(archive.read(p).decode() for p in archive.namelist())
    assert lab.export_blind() == exported
    pair = frozen['pairs'][0]
    preferred = 'left' if pair['left'] == 'B' else 'right'
    sheet['reviewer_id'] = 'editor-one'
    for row in sheet['pairs']:
        row.update(preferred=preferred, note='More effective choice.',
                   scores={side: {metric: 4 for metric in row['scores'][side]} for side in ('left', 'right')})
    with pytest.raises(ValueError, match='two distinct'):
        lab.assess([sheet])
    second = json.loads(json.dumps(sheet))
    second['reviewer_id'] = 'editor-two'
    second['pairs'][0]['preferred'] = 'tie'
    result = lab.assess([sheet, second])
    assert result['enhanced_consensus_wins'] == 0
    assert result['disagreement_pairs'] == 1
    assert result['production_release_ready'] is False


@pytest.mark.parametrize('change', [
    lambda s: s['model'].update(api_key='NEVER_PERSIST_THIS'),
    lambda s: s['model'].update(base_url='https://user:password@host.test/v1'),
    lambda s: s['cases'][0].update(language='zh'),
    lambda s: s['cases'][0].update(chapters=s['cases'][0]['chapters'][:2]),
    lambda s: s['cases'][0].update(kind='volume_transition', free_trial_end=None, prior_context='Earlier events.',
                                   chapters=[s['cases'][0]['chapters'][0]]),
    lambda s: s['cases'][0].update(rules=['invented_rule']),
])
def test_invalid_or_partial_plans_fail_before_writing_or_resolving_models(tmp_path, change):
    spec = specification()
    change(spec)
    root = tmp_path / 'lab'
    with pytest.raises(ValueError):
        plan(ExperimentLab(root, client_factory=lambda _: pytest.fail('unexpected model')), spec)
    assert not root.exists()


def test_identity_only_project_and_symlink_output_are_rejected(tmp_path):
    from project_identity import ensure_project_instance_id
    project = tmp_path / 'novel'
    ensure_project_instance_id(project)
    with pytest.raises(ValueError, match='outside Novel OS'):
        plan(ExperimentLab(project / 'probe'))
    target = tmp_path / 'target'
    target.mkdir()
    (tmp_path / 'linked').symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        plan(ExperimentLab(tmp_path / 'linked/probe'))
    assert not list(target.iterdir())


def test_oversized_string_response_is_retained_in_full_with_digest(tmp_path):
    from hashlib import sha256
    class Oversized(Writer):
        def complete(self, *_):
            return 'x' * 100001
    lab = ExperimentLab(tmp_path / 'lab', client_factory=lambda _: Oversized())
    plan(lab)
    assert lab.run(max_model_calls=6, allow_model_calls=True)['state'] == 'completed_with_issues'
    records = [json.loads(p.read_text())['data'] for p in (tmp_path / 'lab/calls').glob('*.json')]
    bad = [r for r in records if r['status'] == 'invalid']
    assert len(bad) == 2
    for record in bad:
        raw = (tmp_path / 'lab' / record['raw_response_path']).read_bytes()
        assert raw == b'x' * 100001
        assert sha256(raw).hexdigest() == record['raw_response_sha256']
    assert lab.export_blind()['pairs'] == 1  # failed samples are not cherry-picked away


def test_parallel_runs_share_cumulative_budget_and_do_not_duplicate_calls(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    writer = Writer()
    root = tmp_path / 'lab'
    plan(ExperimentLab(root))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(ExperimentLab(root, client_factory=lambda _: writer).run,
                               max_model_calls=6, allow_model_calls=True) for _ in range(2)]
        assert all(f.result(timeout=5)['state'] == 'complete' for f in futures)
    assert len(writer.calls) == 6


def test_model_drift_and_parser_upgrade_are_explicit_not_silent(tmp_path, monkeypatch):
    from method_experiments import lab as implementation
    writer = Writer()
    writer.snapshot = {**writer.snapshot, 'model': 'changed-model'}
    lab = ExperimentLab(tmp_path / 'lab', client_factory=lambda _: writer)
    plan(lab)
    with pytest.raises(ValueError, match='configuration differs'):
        lab.run(max_model_calls=6, allow_model_calls=True)
    assert not writer.calls
    monkeypatch.setattr(implementation, 'implementation_sha', lambda: 'different-parser-version')
    with pytest.raises(ValueError, match='implementation changed'):
        lab.status()


def test_short_input_budget_does_not_truncate_or_spend_calls(tmp_path):
    writer = Writer()
    lab = ExperimentLab(tmp_path / 'lab', client_factory=lambda _: writer)
    spec = specification()
    spec['input_max_chars'] = 1000
    plan(lab, spec)
    result = lab.run(max_model_calls=6, allow_model_calls=True)
    assert result['confirmed_model_calls'] == 0
    assert not writer.calls
    assert sum(r['status'] == 'incomplete' for r in result['results']) == 2


def test_public_model_snapshot_preserves_p1_provenance():
    from model_router import ModelRouter
    from narrative_methods.runtime import _provenance
    from test_narrative_methods import FakeReviewer
    client = FakeReviewer()
    assert ModelRouter.public_snapshot(client) == _provenance(client)


def completed_sheets(lab):
    exported = lab.export_blind()
    with zipfile.ZipFile(exported['path']) as archive:
        sheet = json.loads(archive.read('review-template.json'))
    # The coordinator's private mapping is deliberately outside the blind ZIP.
    frozen = json.loads((lab.store.root / 'plan.json').read_text())['data']
    sides = {p['pair_id']: ('left' if p['left'] == 'B' else 'right') for p in frozen['pairs']}
    for row in sheet['pairs']:
        row.update(preferred=sides[row['pair_id']], note='Both required events are present; preferred prose is clearer.',
                   scores={side: {metric: 4 for metric in row['scores'][side]} for side in ('left', 'right')})
    sheet['reviewer_id'] = 'editor-one'
    second = json.loads(json.dumps(sheet))
    second['reviewer_id'] = 'editor-two'
    return [sheet, second]


def test_standard_pilot_separates_baseline_errors_from_enhanced_errors(tmp_path):
    from copy import deepcopy
    spec = json.loads((ROOT / 'resources/method-experiments/english-pilot.example.json').read_text())
    spec['model'] = specification()['model']
    for case in spec['cases']:
        for chapter in case['chapters']:
            chapter.update(min_words=2, max_words=10)
    lab = ExperimentLab(tmp_path / 'lab', client_factory=lambda _: Writer())
    assert plan(lab, spec)['planned_model_calls'] == 54
    lab.run(max_model_calls=54, allow_model_calls=True)
    sheets = completed_sheets(lab)
    row = sheets[0]['pairs'][0]
    preferred = row['preferred']
    other = 'right' if preferred == 'left' else 'left'
    # Evidence offsets are against the exact stored prose, including its heading.
    quote = 'Jo chose the open door.'
    start = len('# Chapter\n\n')
    finding = {'side': other, 'chapter': 1, 'start': start, 'end': start + len(quote),
               'quote': quote, 'category': 'required_event', 'explanation': 'Fixture baseline event concern.'}
    row['critical_findings'] = [finding]
    result = lab.assess(sheets)
    assert result['critical_findings_by_arm'] == {'A': 1, 'B': 0}
    assert result['enhanced_consensus_wins'] == 9
    assert result['editorial_pilot_signal'] is True
    assert result['production_release_ready'] is False
    changed = deepcopy(sheets)
    changed[0]['pairs'][0]['critical_findings'][0]['side'] = preferred
    assert lab.assess(changed)['editorial_pilot_signal'] is False


@pytest.mark.parametrize('mutation', [
    lambda sheets: sheets[0].update(blind_sha256='wrong'),
    lambda sheets: sheets[1].update(reviewer_id='editor-one'),
    lambda sheets: sheets[0]['pairs'][0]['scores']['left'].update(causality=True),
    lambda sheets: sheets[0]['pairs'].clear(),
    lambda sheets: sheets[0]['pairs'][0].update(critical_findings=[{
        'side': 'left', 'chapter': 1, 'start': 0, 'end': 3, 'quote': 'not in source',
        'category': 'pov', 'explanation': 'No invented quotations.'}]),
])
def test_editorial_receipts_reject_stale_missing_or_invented_evidence(tmp_path, mutation):
    lab = ExperimentLab(tmp_path / 'lab', client_factory=lambda _: Writer())
    plan(lab)
    lab.run(max_model_calls=6, allow_model_calls=True)
    sheets = completed_sheets(lab)
    mutation(sheets)
    with pytest.raises(ValueError):
        lab.assess(sheets)


def test_cli_plan_is_offline_and_run_without_consent_never_opens_a_connection(tmp_path):
    import subprocess
    import sys
    command = [sys.executable, str(ROOT / 'scripts/method_experiment.py')]
    spec = tmp_path / 'spec.json'
    spec.write_text(json.dumps(specification()))
    result = subprocess.run(command + ['plan', '--root', str(tmp_path / 'lab'), '--spec', str(spec)],
                            text=True, capture_output=True, check=True)
    assert json.loads(result.stdout)['planned_model_calls'] == 6
    result = subprocess.run(command + ['run', '--root', str(tmp_path / 'lab'), '--max-model-calls', '6'],
                            text=True, capture_output=True)
    assert result.returncode == 2
    assert 'explicit model-call consent' in result.stdout
    assert not (tmp_path / 'lab/calls').exists()
