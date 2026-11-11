"""Identity capability upgrades retain verified work and ordinary review gates."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from core.outline_planning import SCHEMA_VERSION, OutlinePlanningError, content_hash
from core.planning_residual_loop import VERSION, converge
from test_outline_planning import review


def memory_planner(path):
    path.mkdir()
    identity = 'a' * 64

    def save(name, dependencies, payload):
        envelope = {'schema_version': SCHEMA_VERSION, 'identity_hash': identity,
                    'dependencies_hash': content_hash(dependencies),
                    'payload_hash': content_hash(payload), 'payload': payload}
        (path / (name + '.json')).write_text(json.dumps(envelope))

    def load(name, dependencies, validate, **options):
        record = path / (name + '.json')
        if not record.exists():
            return None
        envelope = json.loads(record.read_text())
        assert envelope['dependencies_hash'] == content_hash(dependencies)
        return validate(envelope['payload'])

    return SimpleNamespace(cache=path, identity_hash=identity, progress={}, _save=save, _load=load,
                           _progress=lambda *args: None)


def seed_legacy_terminal(planner, candidate, findings):
    anchor = content_hash([VERSION, candidate, findings, []])
    prefix = VERSION + '-' + content_hash([anchor, candidate, findings])[:24]
    audit = {'schema': prefix, 'baseline_candidate_sha256': content_hash(candidate), 'issues': []}
    planner._save(prefix + '-candidate', {}, {'candidate': candidate, 'verification': audit})
    planner._save('review-' + prefix, {}, findings)
    return prefix


@pytest.mark.parametrize('resolved', [True, False])
def test_terminal_before_identity_capability_gets_one_durable_upgrade_and_reuses_it(tmp_path, resolved):
    candidate = {'characters': [{'id': 'guard', 'pronouns': ''}], 'chapters': []}
    findings = review({'scope': 'global', 'message': 'Restore the guard pronouns.', 'source_evidence': 'He waits.'})
    planner = memory_planner(tmp_path / 'cache')
    old_prefix = seed_legacy_terminal(planner, candidate, findings)
    originals = {p: p.read_bytes() for p in planner.cache.iterdir()}
    paid = []
    visits = []

    def advance(current, outstanding, prefix, previous_review):
        visits.append(prefix)
        if prefix == old_prefix:
            return deepcopy(candidate), deepcopy(findings)
        assert prefix == old_prefix + '-identity-v1'
        assert previous_review == 'review-' + old_prefix
        assert current == candidate and outstanding == findings
        dependencies = {'baseline_candidate': content_hash(current), 'review': content_hash(outstanding)}
        saved = planner._load(prefix + '-result', dependencies, lambda value: value)
        if saved is None:
            paid.append(prefix)
            planner._save(prefix + '-identity-policy', dependencies, {'version': 'residual-identity-v1'})
            fixed = deepcopy(current)
            if resolved:
                fixed['characters'][0]['pronouns'] = 'he/him'
            saved = {'candidate': fixed, 'review': review() if resolved else findings}
            planner._save(prefix + '-result', dependencies, saved)
        return saved['candidate'], saved['review']

    for _ in range(2):
        if resolved:
            fixed, verdict = converge(planner, candidate, findings, advance, authority=[])
            assert verdict['status'] == 'passed' and fixed['characters'][0]['pronouns'] == 'he/him'
        else:
            with pytest.raises(OutlinePlanningError, match='未产生可验证的新进展'):
                converge(planner, candidate, findings, advance, authority=[])
            assert planner.progress['recovery']['retryable'] is False
        assert all(p.read_bytes() == data for p, data in originals.items())
    assert paid == [old_prefix + '-identity-v1']
    assert visits == [old_prefix, old_prefix + '-identity-v1'] * 2


def test_current_identity_capability_stall_never_restarts_an_upgrade(tmp_path):
    candidate = {'characters': [], 'chapters': []}
    findings = review({'scope': 'global', 'message': 'Unresolved supported finding.', 'source_evidence': 'source'})
    planner = memory_planner(tmp_path / 'cache')
    prefix = seed_legacy_terminal(planner, candidate, findings)
    planner._save(prefix + '-identity-policy', {}, {'version': 'residual-identity-v1'})
    visited = []

    def advance(current, outstanding, stage, previous_review):
        visited.append(stage)
        return current, outstanding

    with pytest.raises(OutlinePlanningError, match='未产生可验证的新进展'):
        converge(planner, candidate, findings, advance, authority=[])
    assert visited == [prefix]


def test_identity_upgrade_rejects_changed_terminal_payload(tmp_path):
    candidate = {'characters': [], 'chapters': []}
    findings = review({'scope': 'global', 'message': 'Unresolved supported finding.', 'source_evidence': 'source'})
    planner = memory_planner(tmp_path / 'cache')
    prefix = seed_legacy_terminal(planner, candidate, findings)
    path = planner.cache / (prefix + '-candidate.json')
    record = json.loads(path.read_text())
    record['payload']['candidate']['characters'].append({'id': 'foreign'})
    record['payload_hash'] = content_hash(record['payload'])
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='does not match the verified terminal'):
        converge(planner, candidate, findings, lambda current, outstanding, *args: (current, outstanding), authority=[])
    assert not list(planner.cache.glob('*-identity-policy.json'))


GUARD_SOURCE = 'The guard blocks the door. He keeps the keys.'
COURIER_SOURCE = 'An unnamed courier delivered the earlier letter.'


def _identity_model(*, interrupt=False, reviews=None):
    from test_outline_coordinated_repair import CoordinatedModel
    from test_planning_evidence_orchestration import proposal

    def add_roles(foundation):
        foundation['characters'].extend([
            {'id': 'guard', 'name': 'guard', 'role': 'supporting', 'gender': '', 'pronouns': '', 'aliases': []},
            {'id': 'courier', 'name': 'courier', 'role': 'supporting', 'gender': '', 'pronouns': '', 'aliases': []},
        ])

    class IdentityModel(CoordinatedModel):
        def complete(self, system, user):
            context = json.loads(user.rsplit('\n\n', 1)[1])
            if user.startswith('STAGE: coordinated_repair_plan') and context['review']['issues'][0]['message'].startswith('Record guard'):
                self.calls.append(('identity_plan', context))
                return json.dumps({'issues': [{**proposal(0, 3), 'affected_chapters': [3, 6],
                    'writable_chapters': [3, 6], 'source_quotes': [GUARD_SOURCE, COURIER_SOURCE],
                    'candidate_evidence': [{'path': '/characters/1/name', 'quote': 'guard'},
                                           {'path': '/characters/2/name', 'quote': 'courier'}]}]})
            if user.startswith('STAGE: coordinated_residual_identity'):
                self.calls.append(('identity', context))
                if interrupt:
                    raise TimeoutError('Identity response interrupted')
                passages = context['adopted_passages']
                return json.dumps({'identity_fields': [{'character_id': 'guard', 'field': 'pronouns',
                    'before': '', 'value': 'he/him', 'source_indices': [passages.index(GUARD_SOURCE)]}],
                    'identity_relations': [{'character_ids': ['guard', 'courier'], 'relation': 'unknown',
                        'reason': 'The source does not establish whether these role occurrences identify the same person.',
                        'source_evidence': [{'character_id': 'guard', 'source_index': passages.index(GUARD_SOURCE)},
                                            {'character_id': 'courier', 'source_index': passages.index(COURIER_SOURCE)}]}]})
            return super().complete(system, user)

    return IdentityModel(reviews=reviews, mutate_global=add_roles)


def _identity_finding():
    return review({'scope': 'global', 'message': 'Record guard pronouns and keep guard/courier identity unresolved.',
                   'source_evidence': GUARD_SOURCE, 'finding_basis': 'source_departure'})


@pytest.mark.parametrize('interrupted', [False, True])
def test_native_planner_repairs_identity_after_saved_narrative_and_requires_complete_review(tmp_path, interrupted):
    from test_outline_coordinated_repair import MIXED
    from test_outline_planning import make_planner, SOURCE

    source = SOURCE + '\n' + GUARD_SOURCE + '\n' + COURIER_SOURCE
    first_model = _identity_model(interrupt=interrupted, reviews=[deepcopy(MIXED), _identity_finding(), review()])
    planner = make_planner(tmp_path, first_model, count=8, source=source)
    if interrupted:
        with pytest.raises(TimeoutError, match='Identity response interrupted'):
            planner.run()
        assert not (planner.cache / 'validated.json').exists()
        old = {path: path.read_bytes() for path in planner.cache.rglob('*') if path.is_file()}
        model = _identity_model(reviews=[review()])
        candidate = make_planner(tmp_path, model, count=8, source=source).run()
        assert [stage for stage, _ in model.calls] == ['identity', 'review']
        assert all(path.read_bytes() == raw for path, raw in old.items())
    else:
        candidate = planner.run()
        model = first_model
    assert candidate['characters'][1]['pronouns'] == 'he/him'
    assert candidate['characters'][1]['gender'] == ''
    constraint, = candidate['identity_constraints']
    assert constraint['character_ids'] == ['courier', 'guard'] and constraint['relation'] == 'unknown'
    stage, complete_review = model.calls[-1]
    assert stage == 'review'
    assert complete_review['candidate']['characters'][1]['pronouns'] == 'he/him'
    assert complete_review['candidate']['identity_constraints'] == candidate['identity_constraints']
    resumed = _identity_model()
    assert make_planner(tmp_path, resumed, count=8, source=source).run() == candidate
    assert resumed.calls == []


def test_new_identity_constraint_registration_can_advance_once_without_any_existing_leaf_edit(tmp_path):
    candidate = {'characters': [], 'chapters': []}
    findings = review({'scope': 'global', 'message': 'Still inspect the relation.', 'source_evidence': 'source'})
    planner = memory_planner(tmp_path / 'cache')
    visited = []

    def advance(current, outstanding, stage, previous_review):
        visited.append(stage)
        fixed = deepcopy(current)
        fixed.setdefault('identity_constraints', []).append({'pair': len(visited)})
        return fixed, findings

    with pytest.raises(OutlinePlanningError, match='未产生可验证的新进展'):
        converge(planner, candidate, findings, advance, authority=[])
    assert len(visited) == 2
    assert candidate == {'characters': [], 'chapters': []}


def _legacy_identity_stall(tmp_path, monkeypatch):
    from core import planning_coordinator, planning_residual_loop
    from test_outline_coordinated_repair import MIXED
    from test_outline_planning import make_planner, SOURCE

    project = tmp_path / 'book'
    run = project / 'outputs/runs/test-run'
    source = SOURCE + '\n' + GUARD_SOURCE + '\n' + COURIER_SOURCE
    model = _identity_model(reviews=[deepcopy(MIXED), _identity_finding()])
    planner = make_planner(run, model, count=8, source=source)
    inputs = project / 'outputs/input'
    inputs.mkdir(parents=True)
    (inputs / 'prompt.md').write_text(source)
    (inputs / 'brief.json').write_text(json.dumps(planner.brief))
    with monkeypatch.context() as old_version:
        old_version.setattr(planning_coordinator, '_identity_policy', lambda *args: False)
        old_version.setattr(planning_residual_loop, '_legacy_identity_upgrade', lambda *args: None)
        with pytest.raises(OutlinePlanningError, match='未产生可验证的新进展'):
            planner.run()
    assert planner.progress['recovery']['action'] == 'inspect_residual_closure'
    stop, = planner.cache.glob('residual-closure-v1-*-stalled.json')
    return project, planner, stop.name.removesuffix('-stalled.json')


def test_old_identity_stall_is_read_only_recoverable_and_native_upgrade_keeps_paid_work(tmp_path, monkeypatch):
    from api.project_task_summary import task_sidecar
    from core.planning_recovery import residual_recovery_availability
    from test_outline_planning import make_planner

    project, planner, prefix = _legacy_identity_stall(tmp_path, monkeypatch)
    files = {p: p.read_bytes() for p in project.rglob('*') if p.is_file()}
    assert residual_recovery_availability(project, 'test-run', planner.progress) is True
    sidecar = task_sidecar(project, 'test-run', 'planning/progress.json')
    assert sidecar['recovery']['retryable'] is True
    assert sidecar['recovery']['action'] == 'repair_saved_residuals'
    assert {p: p.read_bytes() for p in files} == files
    resumed = _identity_model(reviews=[review()])
    candidate = make_planner(planner.directory.parent, resumed, count=8, source=planner.source).run()
    assert [stage for stage, _ in resumed.calls] == ['identity_plan', 'identity', 'review']
    assert candidate['characters'][1]['pronouns'] == 'he/him'
    assert (planner.cache / (prefix + '-identity-v1-identity-policy.json')).is_file()
    assert all(path.read_bytes() == raw for path, raw in files.items() if path.parent != planner.directory)


@pytest.mark.parametrize('changed', ['stop_hash', 'source', 'new_capability_started', 'current_capability'])
def test_identity_stall_eligibility_rejects_unverified_or_already_attempted_upgrades(tmp_path, monkeypatch, changed):
    from core.planning_recovery import residual_recovery_availability

    project, planner, prefix = _legacy_identity_stall(tmp_path, monkeypatch)
    if changed == 'source':
        (project / 'outputs/input/prompt.md').write_text('Different adopted source.')
    elif changed == 'stop_hash':
        path = planner.cache / (prefix + '-stalled.json')
        record = json.loads(path.read_text())
        record['payload']['after'] = '0' * 64
        record['payload_hash'] = content_hash(record['payload'])
        path.write_text(json.dumps(record))
    else:
        marker = prefix + ('-identity-v1' if changed == 'new_capability_started' else '') + '-identity-policy'
        planner._save(marker, {}, {'version': 'residual-identity-v1'})
    before = {p: p.read_bytes() for p in project.rglob('*') if p.is_file()}
    assert residual_recovery_availability(project, 'test-run', planner.progress) is False
    assert {p: p.read_bytes() for p in before} == before
