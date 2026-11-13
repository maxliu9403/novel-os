"""Source-bound chapter-number edits for global findings in a coordinated repair."""
from copy import deepcopy
import re

from core import planning_correction
from core.narrative_format import NarrativeFormat, validate_volume_contracts
from core.outline_planning import content_hash
from core.planning_chapter_repair import _checkpoint

VERSION = 'coordinated-global-references.v1'
_REFERENCE = re.compile(r'/(?:volume_contracts/(?:0|[1-9][0-9]*)/climax_chapter|plot_threads/(?:0|[1-9][0-9]*)/resolution_chapter)\Z')
INSTRUCTION = '''STAGE: coordinated_global_references
Read the adopted story, global finding, exact cited fields and supplied chapter designs.
Decide which permitted chapter-number references need changing. A citation or a reviewer's
proposal alone does not establish the correct value. Preserve chapter events and chronology;
use the adopted source to distinguish their functions. Derived volume obligations will be
rebuilt by the host. Return only {"edits":[{"path":"...","before":1,"after":2,
"source_quote":"verbatim adopted passage"}],"remaining_writable_chapters":[],"reason":"..."}.
Edit only allowed_references, using their exact original integer values. No other fields.
Return [] for edits when evidence supports keeping the existing references. Preserve unaffected
chapters. remaining_writable_chapters is the subset of writable_chapters that still requires
substantive narrative repair for THIS finding. If it concerns only global references, return
[] for remaining_writable_chapters. Do not move a scene just to align with a template, change
an outcome, or decide conflicting author facts. The complete candidate is reviewed afterward.'''


def targets(item):
    cited = {entry['path'] for entry in item['candidate_evidence'] if _REFERENCE.fullmatch(entry['path'])}
    return cited.intersection(item.get('writable_global_references', cited))


def _policy(planner, name, plan, *, create=False):
    if not any(targets(item) for item in plan['issues']):
        return False
    marker = name + '-global-references-v1'
    deps = {'plan': content_hash(plan)}
    expected = {'policy': VERSION}
    def validate(value):
        if value != expected:
            raise ValueError('Unexpected coordinated reference policy')
        return value
    saved = planner._load(marker, deps, validate, strict_dependencies=True)
    if create and saved is None:
        planner._save(marker, deps, expected)
    return saved is not None


def enable(planner, name, plan):
    """Record the reference protocol before committing a newly accepted plan."""
    _policy(planner, name, plan, create=True)


def enabled(planner, prefix, plan):
    if any('writable_global_references' in item for item in plan['issues']):
        return True
    # The initial plan marker authenticates interpretation of old string citations.
    # A decided phase can contain a newly validated choice plan with explicit scope.
    return _policy(planner, prefix + '-plan', plan)


def _apply(candidate, finding, source, count, allowed, writable, affected, response):
    if not isinstance(response, dict) or set(response) != {'edits', 'remaining_writable_chapters', 'reason'}:
        raise ValueError('Reference decision must give edits, remaining chapter permissions and reason')
    remaining = response['remaining_writable_chapters']
    if (not isinstance(remaining, list) or any(type(n) is not int or n not in writable for n in remaining)
            or len(set(remaining)) != len(remaining)):
        raise ValueError('Reference decision cannot expand the original chapter permissions')
    if not isinstance(response['reason'], str) or not response['reason'].strip() or len(response['reason']) > 2000:
        raise ValueError('Reference decision requires a concrete reason')
    edits = response['edits']
    if not isinstance(edits, list) or any(not isinstance(edit, dict) or set(edit) != {'path','before','after','source_quote'}
            or not isinstance(edit['path'], str) or edit['path'] not in allowed for edit in edits):
        raise ValueError('Numeric edit is outside the cited, permitted global reference fields')
    if not edits:
        return candidate
    if any(type(edit['after']) is not int or edit['after'] not in affected for edit in edits):
        raise ValueError('The new global reference must point to an affected chapter')
    patch = {'status':'corrected', 'reason':response['reason'],
             'edits':[{**edit,'issue_indices':[0]} for edit in edits]}
    result = planning_correction.apply(candidate, {'issues':[finding]}, source, patch, count)
    if any(edit['path'].startswith('/volume_contracts/') for edit in edits):
        # The dependent obligation is generated from the validated before/after contracts.
        # Only replace that obligation list. Keep every other original volume field.
        fmt = NarrativeFormat.from_dict(candidate['narrative_format'])
        old_volumes = validate_volume_contracts(fmt, candidate['volume_contracts'])
        new_volumes = validate_volume_contracts(fmt, result['volume_contracts'])
        for edit in edits:
            if edit['path'].startswith('/volume_contracts/'):
                i = int(edit['path'].split('/')[2])
                before, after = old_volumes[i], new_volumes[i]
                if (before['volume_id'] != candidate['volume_contracts'][i]['volume_id']
                        or before['obligations'] != candidate['volume_contracts'][i]['obligations']):
                    raise ValueError('Volume obligations differ from their verified original reference')
                result['volume_contracts'][i]['obligations'] = after['obligations']
    return result


def apply_group(planner, candidate, review, evidence, execution, group, prefix, choices, limit):
    """A narrower chapter executor sees only work left after the reference editor."""
    derived, revisions = candidate, []
    rows = {i['issue_index']:i for i in execution['issues']}
    evidence_rows = {i['issue_index']:i for i in evidence['issues']}
    for index in group['original_issue_indices']:
        finding, item, row = review['issues'][index], evidence_rows[index], rows[index]
        if finding['scope'] != 'global' or row['classification'] not in ('continuity_gap', 'contradiction'):
            continue
        current = planning_correction.references(derived)
        allowed = {path: value for path, value in current.items() if path in targets(item)}
        if not allowed:
            continue
        original = planning_correction.references(candidate)
        cited = {c['path']: c.get('quote') for c in item['candidate_evidence']}
        if any(cited[path] != str(original.get(path)) for path in allowed):
            raise ValueError('A proposed global reference no longer matches its bound candidate evidence')
        writable = row['requested_chapters']
        numbers = set(item['affected_chapters']) | set(row['dependency_chapters']) | set(writable)
        ctx = {'complete_adopted_source':planner.source, 'finding':finding, 'proposal':item,
               'allowed_references':allowed, 'writable_chapters':writable, 'adopted_passages':row['source_quotes'],
               'binding_author_choices':choices,
               'referenced_contracts':{k: derived[k] for k in sorted({p.split('/')[1] for p in allowed})},
               'affected_chapter_map':[c for c in derived['chapter_map'] if c['number'] in numbers],
               'writable_chapter_designs':[c for c in derived['chapters'] if c['number'] in writable]}
        def validate(answer):
            _apply(derived, finding, planner.source, planner.chapter_count, allowed, writable,
                   item['affected_chapters'], answer)
            return answer
        name = f'{prefix}-global-references-{index+1:03d}'
        deps = {'candidate':content_hash(derived), 'review':content_hash(review), 'version':VERSION}
        answer = _checkpoint(planner, name, INSTRUCTION, ctx, deps, validate, limit)
        derived = _apply(derived, finding, planner.source, planner.chapter_count, allowed, writable,
                         item['affected_chapters'], answer)
        rows[index] = {**row, 'requested_chapters':answer['remaining_writable_chapters'],
                       'dependency_chapters':sorted(set(row['dependency_chapters']) | (set(writable)-set(answer['remaining_writable_chapters'])))}
        revisions.append({'operation':VERSION, 'issue_index':index, 'checkpoint':name,
                          'dependencies':{**deps,'context':content_hash(ctx),'policy':content_hash(INSTRUCTION)},
                          'decision':answer})
    return derived, {'issues':list(rows.values())}, revisions


def merge_references(assembled, before, after, revisions):
    """Copy precisely the independently validated reference and its generated obligation."""
    paths = {edit['path'] for revision in revisions for edit in revision['decision']['edits']}
    for path in sorted(paths):
        _, section, pos, key = path.split('/')
        i = int(pos)
        fields = (key, 'obligations') if section == 'volume_contracts' else (key,)
        for field in fields:
            if assembled[section][i][field] != before[section][i][field]:
                raise ValueError('Independent groups overlap on a global chapter reference')
            assembled[section][i][field] = deepcopy(after[section][i][field])


def verify_terminal(planner, prefix, baseline, candidate, rows, review):
    """Revalidate each recorded numerical decision against its checkpoint and the adopted source."""
    from core.planning_terminal import _record, _check_dependencies
    expected = baseline
    accepted = set()
    for row in rows:
        index = row['issue_index']
        for revision in row.get('derived_revisions', []):
            if revision.get('operation') != VERSION:
                continue
            item = row.get('evidence')
            if (type(index) is not int or not 0 <= index < len(review['issues']) or not isinstance(item, dict)
                    or review['issues'][index]['scope'] != 'global'
                    or revision.get('issue_index') != index or item.get('issue_index') != index
                    or index in accepted):
                raise ValueError('Global reference audit belongs to another finding')
            name = revision['checkpoint']
            if name not in {f'{prefix}-global-references-{index+1:03d}', f'{prefix}-decided-global-references-{index+1:03d}'}:
                raise ValueError('Global reference decision must belong to the current repair round')
            envelope = _record(planner, name)
            deps = revision['dependencies']
            if (deps.get('review') != content_hash(review) or deps.get('policy') != content_hash(INSTRUCTION)
                    or deps.get('version') != VERSION or revision.get('decision') != envelope['payload']):
                raise ValueError('Global reference audit does not match its bound decision')
            _check_dependencies(envelope, deps, name)
            old = planning_correction.references(baseline)
            current = planning_correction.references(expected)
            allowed = {key: current[key] for key in targets(item) if key in old and any(
                ref['path'] == key and ref.get('quote') == str(old[key]) for ref in item['candidate_evidence'])}
            if not allowed:
                raise ValueError('Global reference decision lacks an exact qualifying candidate reference')
            expected = _apply(expected, review['issues'][index], planner.source, planner.chapter_count, allowed,
                              row['requested_chapters'], item['affected_chapters'], envelope['payload'])
            accepted.add(index)
    if accepted:
        if planning_correction.references(candidate) != planning_correction.references(expected):
            raise ValueError('Saved global references do not match the audited edits')
        if candidate.get('volume_contracts') != expected.get('volume_contracts'):
            raise ValueError('Saved volume contracts differ from the rebuilt reference obligations')
    return accepted
