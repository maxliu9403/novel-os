"""Read an immutable coordinated correction and its candidate-bound semantic verdict."""
from pathlib import Path
import json
import re

from core import planning_coordinated as contract
from core import planning_author_review as authority
from core.outline_planning import SCHEMA_VERSION, StagedOutlinePlanner, content_hash
from core.planning_author_decisions import authorized_choices
from core.planning_context import REVIEW_CONTEXT_VERSION
from core import planning_review_reuse

_EXECUTION_KEYS = ('issue_index', 'classification', 'reason', 'requested_chapters',
                   'dependency_chapters', 'source_quotes')


def _record(planner, name):
    from core.planning_recovery import _artifact

    path = planner.cache / (name + '.json')
    envelope = json.loads(_artifact(path.absolute(), limit=32 * 1024 * 1024))
    if (not isinstance(envelope, dict) or envelope.get('schema_version') != SCHEMA_VERSION
            or envelope.get('identity_hash') != planner.identity_hash
            or not isinstance(envelope.get('payload'), dict)
            or envelope.get('payload_hash') != content_hash(envelope['payload'])):
        raise ValueError(f'Invalid coordinated checkpoint {name}')
    return envelope


def _check_dependencies(record, dependencies, name):
    if record.get('dependencies_hash') != content_hash(dependencies):
        raise ValueError(f'Coordinated checkpoint {name} has different inputs or policy; completed work cannot be reset')


def final_names(root: Path, name: str):
    return [p.stem for p in sorted(root.glob(name + '*.json')) if re.fullmatch(
        re.escape(name) + r'(?:-choices-[0-9a-f]{16})?', p.stem)]


def _read_candidate(planner, prefix, baseline, previous_review):
    name = prefix + '-candidate'
    record = _record(planner, name)
    child = record['payload']
    audit, candidate = child['verification'], child['candidate']
    if (not isinstance(audit, dict) or audit.get('schema') != prefix
            or audit.get('baseline_candidate_sha256') != content_hash(baseline)):
        raise ValueError(f'{name} does not descend from the verified candidate')
    # The old writer hashed the execution plan, not the model's preliminary
    # proposal. Its original fields are preserved verbatim in each audit row.
    rows = audit['issues']
    plan = {'issues': [{key: row[key] for key in _EXECUTION_KEYS} for row in rows]}
    contract.validate_plan(baseline, previous_review, planner.source, plan)
    if any(row['original_issue'] != previous_review['issues'][row['issue_index']] for row in rows):
        raise ValueError(f'{name} does not resolve the preceding review')
    _check_dependencies(record, {'plan': content_hash(plan), 'audit': content_hash(audit)}, name)
    if (not isinstance(candidate, dict) or
            any([item['number'] for item in candidate[field]] != [item['number'] for item in baseline[field]]
                for field in ('chapter_map', 'chapters')) or
            any(candidate.get(key) != value for key, value in planner.locked.items())):
        raise ValueError(f'{name} lost chapters or changed an author-locked contract')
    return candidate, audit


def _read_round(planner, prefix, baseline, previous_review, final_names):
    candidate, audit = _read_candidate(planner, prefix, baseline, previous_review)
    deps = {'candidate': content_hash(candidate), 'representation': REVIEW_CONTEXT_VERSION,
            'coordinated_verification': content_hash(audit),
            'verification_policy': content_hash(planning_review_reuse.RULES)}
    result, label = read_verdict(planner, 'review-' + prefix, deps, final_names, include_label=True)
    return candidate, result, label


def read_verdict(planner, label, dependencies, final_names, *, include_label=False):
    """Read the saved verdict authenticated against its inputs and original author choices."""
    choices = authorized_choices(planner)
    found = []
    for name in final_names:
        envelope = _record(planner, name)
        result = StagedOutlinePlanner._review(planner, envelope['payload'])
        deps = dict(dependencies)
        bound = []
        if name != label:
            ids = {row['choice_id'] for row in result['author_choice_checks']}
            bound = [choice for choice in choices if choice['choice_id'] in ids]
            if not bound or name != label + '-choices-' + content_hash(bound)[:16]:
                raise ValueError('Saved verdict no longer matches the author choices')
            deps['authority'] = content_hash([bound, authority.RULES])
        _check_dependencies(envelope, deps, name)
        authority.inspect_result(result, bound)
        found.append((bound, result, name))
    if len(found) != 1:
        found = [entry for entry in found if entry[0] == choices]
        if len(found) != 1:
            raise ValueError('Cannot identify a unique completed coordinated review')
    bound, selected, name = found[0]
    if bound and bound == choices and selected['status'] == 'passed':
        planner._verified_author_review = (dependencies['candidate'], content_hash(choices))
    return (selected, name) if include_label else selected


def verified_terminal(planner, candidate, review, prefix, *, include_label=False):
    """Return the latest completed historical pair; native execution owns incomplete work.

    A later completed review must have a verified parent. Never select an older
    result by file time or silently restart after a damaged terminal receipt.
    """
    followup = prefix + '-followup'
    second = final_names(planner.cache, 'review-' + followup)
    started_followup = bool(second or any(planner.cache.glob(followup + '-*.json'))
                            or any((planner.cache / 'raw').glob(followup + '-*')))
    first_prefix = prefix
    first = final_names(planner.cache, 'review-' + prefix)
    if not first and started_followup and prefix == 'coordinated-evidence-v2':
        # Current migrations can reuse a v1 group candidate and verdict. A
        # v1-only result still enters its original evidence migration first.
        first_prefix = contract.VERSION
        first = final_names(planner.cache, 'review-' + first_prefix)
    if not first:
        if started_followup:
            raise ValueError('The saved follow-up has no completed parent review')
        return None
    try:
        pair = _read_round(planner, first_prefix, candidate, review, first)
        if started_followup:
            if pair[1]['status'] == 'passed':
                raise ValueError('Saved follow-up is inconsistent with the preceding pass')
            if not second:
                # The native v2 path must finish its pending calls using their
                # exact namespace and dependencies, including promoted responses.
                return None
            pair = _read_round(planner, followup, pair[0], pair[1], second)
        return pair if include_label else pair[:2]
    except (OSError, KeyError, IndexError, TypeError) as exc:
        raise ValueError('Incomplete or invalid coordinated review lineage') from exc
