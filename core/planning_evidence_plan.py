"""Authority and provenance for repairing a saved planning candidate.

A classifier can nominate a conflict. Only a separate reading of the adopted
source can defer a decision to the author. A valid plan never approves a book.
"""
from __future__ import annotations

from copy import deepcopy

from core import planning_coordinated as original

VERSION = 'coordinated-evidence-v2'
PLAN_INSTRUCTION = '''STAGE: coordinated_repair_plan
Use the complete adopted source as author authority. The generated candidate, including
its assumptions, and the saved review are observations to check. A reviewer's assertion
is not an author fact. Candidate chapter bodies and the generated map are omitted at
this planning step; do not claim to have inspected them. Account for every saved issue
by its zero-based issue_index exactly once. Return one JSON object:
{"issues":[{"issue_index":0,"classification":"contradiction|continuity_gap|interpretation|source_conflict",
"reason":"...","affected_chapters":[1],"writable_chapters":[],"dependency_chapters":[],
"foundation_repairs":[],"source_quotes":["verbatim adopted passage"],"candidate_evidence":[{"path":"/assumptions/0","quote":"verbatim candidate passage"}]}]}.
Use one classification value, existing chapter numbers and a concrete explanation.
affected_chapters are ALL chapters implicated in the finding, including read-only ones.
writable_chapters are ONLY the affected chapters that actually need a substantive repair.
dependency_chapters are additional chapters whose design must be read. For contradiction
or continuity_gap propose the necessary writes, including the chapter of a chapter-scoped
repair. An incorrect generated assumption is candidate drift: cite its exact path and
quote so it can be corrected along with the affected design. Use JSON Pointer paths
relative to candidate for candidate_evidence and [] when no supplied candidate text is
being cited. Copy original phrases exactly in source_quotes, keeping their language.
Use foundation_repairs ["character_registration"] for a missing source-backed cast entry,
["payoff_binding"] for broken payoff references to existing setups, both when appropriate,
or [] for ordinary narrative findings. Never use either tool to alter existing identities or setups.
For interpretation or a possible source_conflict writable_chapters MUST be [].
Always name the scope, even when requesting no edits. Choose source_conflict only for
an alleged incompatibility between two explicit adopted statements about the same
thing and circumstances. Supply both literal passages for independent re-reading.
A gap in mechanism, candidate assumption, character's belief, unresolved mystery
or a plausible inference is never a second author statement. Distinguish differing
POVs, times, fake records and reported beliefs from an actual source contradiction.
Prefer a source-supported repair or a fresh review of an unsupported finding.
The complete assembled candidate still receives a final semantic review.
'''
CONFLICT_INSTRUCTION = '''STAGE: coordinated_source_adjudication
Independently read the complete adopted source. Assess the allegation without assuming
the earlier classifier is correct. Only complete_adopted_source is author authority.
Respect explicit provisional or hypothetical labels even inside the source document. A proposed
option, an embedded example, or instructions about technique are not binding story facts.
The disputed candidate text is model-generated. Check both sides of the alleged
conflict against what the author explicitly wrote, in context. First test whether
both statements can be true, including changes over time, viewpoint, rumour, forged
records, or unstated mechanisms. An inference is never an explicit requirement.
Return one JSON object with exactly these fields:
{"verdict":"candidate_drift|compatible|confirmed_conflict","reason":"...",
 "source_quotes":["verbatim author passage"],"writable_chapters":[],"conflict_basis":null}.
Choose candidate_drift if the generated design contradicts an adopted statement and
requires repair. Name the specific permitted writable chapters within affected_chapters.
Choose compatible if the accusation is unsupported or an advisory requiring fresh
review, and return no writes. Cite at least one relevant exact source passage in both
cases. A confirmed_conflict requires exactly two distinct independent verbatim original
passages, each explicitly binding the same subject in the same circumstances, which
cannot coexist even after considering context. For confirmed_conflict write no chapters,
and supply conflict_basis {"subject":"...", "circumstances":"...", "incompatibility":"..."}
explaining precisely why both explicit statements cannot hold. Use original-language
quotations without translations. Never declare a book passed or change the source.
'''

_ITEM = {'issue_index', 'classification', 'reason', 'affected_chapters', 'writable_chapters',
         'dependency_chapters', 'source_quotes', 'candidate_evidence'}
_LEGACY = {'issue_index', 'classification', 'reason', 'requested_chapters',
           'dependency_chapters', 'source_quotes'}
_CLASSES = ('contradiction', 'continuity_gap', 'interpretation', 'source_conflict')
_MATERIAL = ('contradiction', 'continuity_gap')


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _numbers(value, existing, label, errors):
    if (not isinstance(value, list) or any(type(n) is not int or n not in existing for n in value)
            or len(value) != len(set(value))):
        errors.append(f'{label} must contain unique existing chapter numbers')
        return set()
    return set(value)


def _schema(value, fields, label, errors):
    if not isinstance(value, dict):
        errors.append(f'{label} must be an object')
        return {}
    unknown, missing = set(value) - fields, fields - set(value)
    if unknown or missing:
        errors.append(f'{label} has unexpected fields {sorted(unknown)} and missing fields {sorted(missing)}')
    return value


def _check(errors):
    if errors:
        raise ValueError('\n'.join(errors))


def _source_quotes(source, value, label, errors):
    if not isinstance(value, list) or not value:
        errors.append(f'{label} requires at least one exact adopted-source passage')
        return []
    for i, quote in enumerate(value):
        if not _text(quote) or quote not in source:
            errors.append(f'{label}[{i}] must be a verbatim passage from the adopted source')
    return value


def _candidate_strings(candidate):
    """Index only original scalar strings actually visible in the plan's candidate view."""
    def walk(path, node):
        if isinstance(node, dict):
            for key, child in node.items():
                token = key.replace('~', '~0').replace('/', '~1')
                yield from walk(path + '/' + token, child)
        elif isinstance(node, list):
            for i, child in enumerate(node):
                yield from walk(f'{path}/{i}', child)
        elif isinstance(node, str):
            yield path, node

    return dict(walk('', {k: v for k, v in candidate.items() if k not in ('chapters', 'chapter_map')}))


def matches_candidate_evidence(reference, strings):
    path, quote = reference.get('path'), reference.get('quote')
    return (_text(path) and path in strings and isinstance(quote, str)
            and (quote == strings[path] if quote == '' else bool(quote.strip()) and quote in strings[path]))


def _check_candidate(value, strings, label, errors):
    if not isinstance(value, list):
        errors.append(f'{label} must be a list of candidate passage references')
        return
    for n, ref in enumerate(value):
        ref_label = f'{label}[{n}]'
        ref = _schema(ref, {'path', 'quote'}, ref_label, errors)
        if not matches_candidate_evidence(ref, strings):
            errors.append(f'{ref_label} must quote the declared visible candidate field exactly')


def validate_plan(candidate: dict, review: dict, source: str, value: dict) -> dict:
    """Report independent defects together, retaining scope even for read-only findings."""
    existing = original._existing(candidate)
    issues = original._issues(review, existing)
    if not _text(source):
        raise ValueError('The complete adopted source is required')
    errors = []
    root = _schema(value, {'issues'}, 'plan', errors)
    items = root.get('issues')
    if not isinstance(items, list):
        raise ValueError('plan.issues must be a list')
    if not errors and items and all(isinstance(item, dict) and set(item) == _LEGACY for item in items):
        return migrate_legacy(candidate, review, source, value)
    strings = _candidate_strings(candidate)
    seen = set()
    for pos, item in enumerate(items):
        name = f'issues[{pos}]'
        fields = _ITEM | {'foundation_repairs'} if isinstance(item, dict) and 'foundation_repairs' in item else _ITEM
        item = _schema(item, fields, name, errors)
        tools = item.get('foundation_repairs', [])
        if (not isinstance(tools, list) or any(t not in ('character_registration', 'payoff_binding') for t in tools)
                or len(tools) != len(set(tools))):
            errors.append(f'{name}.foundation_repairs may only name each permitted registry operation once')
        idx, kind = item.get('issue_index'), item.get('classification')
        valid_index = type(idx) is int and 0 <= idx < len(issues)
        if not valid_index or idx in seen:
            errors.append(f'{name}.issue_index must be unique and in the saved review')
        if valid_index:
            seen.add(idx)
        if kind not in _CLASSES:
            errors.append(f'{name}.classification must be one of {_CLASSES}')
        if not _text(item.get('reason')):
            errors.append(f'{name}.reason must explain the finding')
        affected = _numbers(item.get('affected_chapters'), existing, name+'.affected_chapters', errors)
        writes = _numbers(item.get('writable_chapters'), existing, name+'.writable_chapters', errors)
        reads = _numbers(item.get('dependency_chapters'), existing, name+'.dependency_chapters', errors)
        if not affected and kind != 'interpretation':
            errors.append(f'{name}.affected_chapters must locate the affected story before acting')
        if writes - affected or writes & reads:
            errors.append(f'{name}.writable_chapters must be affected and disjoint from dependencies')
        if kind in _MATERIAL and not writes:
            errors.append(f'{name}.writable_chapters must identify the actual material correction')
        if kind not in _MATERIAL and writes:
            errors.append(f'{name}.writable_chapters must be empty for {kind}')
        if valid_index and issues[idx]['scope'] == 'chapter':
            chapter = issues[idx]['chapter']
            if chapter not in affected or (kind in _MATERIAL and chapter not in writes):
                errors.append(f'{name} must include its chapter {chapter} in the applicable affected/write scope')
        _source_quotes(source, item.get('source_quotes'), name+'.source_quotes', errors)
        _check_candidate(item.get('candidate_evidence'), strings, name+'.candidate_evidence', errors)
    if seen != set(range(len(issues))):
        errors.append('plan.issues must account for every saved issue exactly once')
    _check(errors)
    return deepcopy(value)


def migrate_legacy(candidate: dict, review: dict, source: str, value: dict, *, prior=None, historical: bool = False) -> dict:
    """Adapt a legacy response to typed evidence without asserting its classification.

    Only candidate references can be recovered from an earlier proposal. Their exact
    content and location must independently bind to the same frozen visible candidate.
    """
    if (not isinstance(value, dict) or set(value) != {'issues'}
            or not isinstance(value['issues'], list) or not value['issues']):
        raise ValueError('Legacy plan needs a complete saved issue list')
    strings = _candidate_strings(candidate)
    prior_items = prior.get('issues') if isinstance(prior, dict) else None
    existing = original._existing(candidate)
    issues = original._issues(review, existing)
    converted, errors = [], []
    for pos, raw in enumerate(value['issues']):
        item = _schema(raw, _LEGACY, f'legacy.issues[{pos}]', errors)
        index, kind = item.get('issue_index'), item.get('classification')
        reads = _numbers(item.get('dependency_chapters'), existing, f'issues[{pos}].dependency_chapters', errors)
        requested = _numbers(item.get('requested_chapters'), existing, f'issues[{pos}].requested_chapters', errors)
        writes = requested if kind in _MATERIAL else set()
        affected = set(requested)
        if type(index) is int and 0 <= index < len(issues) and issues[index]['scope'] == 'chapter':
            affected.add(issues[index]['chapter'])
        if kind == 'source_conflict':
            affected |= reads
            if not affected:
                affected = existing.copy()
        latest_quotes = item.get('source_quotes')
        if not isinstance(latest_quotes, list):
            errors.append(f'issues[{pos}].source_quotes must be a list')
            latest_quotes = []
        references = []

        def bind_candidate(quote):
            # Only identical scalar values are eligible for historical reattribution.
            # Guessing the field of origin from shared fragments could grant wrong edits.
            paths = [path for path, text in strings.items() if quote == text]
            if len(paths) != 1:
                return False
            ref = {'path': paths[0], 'quote': quote}
            if ref not in references:
                references.append(ref)
            return True

        original_quotes = []
        for i, quote in enumerate(latest_quotes):
            if _text(quote) and quote in source:
                original_quotes.append(quote)
            elif not _text(quote) or not bind_candidate(quote):
                errors.append(f'issues[{pos}].source_quotes[{i}] has no unambiguous original or candidate source')
        if isinstance(prior_items, list):
            matches = [p for p in prior_items if isinstance(p, dict) and type(p.get('issue_index')) is int
                       and p['issue_index'] == index and p.get('classification') == kind]
            if len(matches) == 1 and isinstance(matches[0].get('source_quotes'), list):
                for quote in matches[0]['source_quotes']:
                    if _text(quote) and quote not in source:
                        bind_candidate(quote)
        converted.append({'issue_index': index, 'classification': kind, 'reason': item.get('reason'),
                          'affected_chapters': sorted(affected), 'writable_chapters': sorted(writes),
                          'dependency_chapters': sorted(reads), 'source_quotes': original_quotes,
                          'candidate_evidence': references, **({} if historical else {'foundation_repairs': []})})
    try:
        result = validate_plan(candidate, review, source, {'issues': converted})
    except ValueError as exc:
        errors.append(str(exc))
    _check(errors)
    return result


def conflict_context(source: str, item: dict) -> dict:
    """One independent reading of the complete original, with the disputed inference clearly marked."""
    if item.get('classification') != 'source_conflict' or item.get('writable_chapters') != []:
        raise ValueError('Only a read-only provisional conflict can enter independent adjudication')
    return {'complete_adopted_source': source, 'issue_index': item['issue_index'],
            'affected_chapters': list(item['affected_chapters']),
            'prior_allegation_unverified': item['reason'],
            'cited_adopted_passages': list(item['source_quotes']),
            'generated_candidate_evidence': deepcopy(item['candidate_evidence'])}


def _independent_passages(source, first, second):
    # Two overlapping excerpts from the same assertion cannot stand in for two decisions.
    return (0 <= source.find(first) <= source.rfind(second) - len(first)
            or 0 <= source.find(second) <= source.rfind(first) - len(second))


def validate_verdict(context: dict, value: dict) -> dict:
    """Validate source ownership and scope; the independent reader judges semantics."""
    errors = []
    value = _schema(value, {'verdict','reason','source_quotes','writable_chapters','conflict_basis'},
                    'source adjudication', errors)
    decision = value.get('verdict')
    if decision not in ('confirmed_conflict', 'candidate_drift', 'compatible'):
        errors.append('Unknown independent source verdict')
    if not _text(value.get('reason')):
        errors.append('Source verdict requires a concrete source-grounded reason')
    writes = _numbers(value.get('writable_chapters'), set(context['affected_chapters']),
                      'verdict.writable_chapters', errors)
    if bool(writes) != (decision == 'candidate_drift'):
        errors.append('Only confirmed candidate drift must name writable chapters')
    quotes = _source_quotes(context['complete_adopted_source'], value.get('source_quotes'),
                            'verdict.source_quotes', errors)
    basis = value.get('conflict_basis')
    if decision == 'confirmed_conflict':
        expected = {'subject', 'circumstances', 'incompatibility'}
        basis = _schema(basis, expected, 'conflict_basis', errors)
        if any(not _text(basis.get(k)) for k in expected):
            errors.append('A verified conflict requires the same subject, binding circumstances and incompatibility')
        if (len(quotes) != 2 or not all(_text(q) for q in quotes)
                or quotes[0] in quotes[1] or quotes[1] in quotes[0]
                or not _independent_passages(context['complete_adopted_source'], *quotes)):
            errors.append('A verified conflict requires two distinct, independent exact source passages')
        elif not any(q in cited or cited in q for q in quotes for cited in context['cited_adopted_passages']):
            errors.append('At least one confirmed passage must bind the conflict to the original finding')
    elif basis is not None:
        errors.append('Only an independently confirmed author conflict can include conflict_basis')
    _check(errors)
    return deepcopy(value)


def execution_plan(plan: dict, verdicts: dict) -> dict:
    """Project the validated evidence and decisions onto the existing repair wire contract."""
    expected = {item['issue_index'] for item in plan['issues'] if item['classification'] == 'source_conflict'}
    if any(type(k) is not int for k in verdicts) or set(verdicts) != expected:
        raise ValueError('Every provisional conflict requires its own bound independent verdict')
    result = []
    for item in plan['issues']:
        idx, kind = item['issue_index'], item['classification']
        writes, quotes, reason = item['writable_chapters'], item['source_quotes'], item['reason']
        if idx in verdicts:
            verdict = verdicts[idx]
            kind = {'candidate_drift':'contradiction', 'compatible':'interpretation',
                    'confirmed_conflict':'source_conflict'}[verdict['verdict']]
            writes, reason = verdict['writable_chapters'], verdict['reason']
            quotes = list(dict.fromkeys(item['source_quotes'] + verdict['source_quotes']))
        reads = (set(item['dependency_chapters']) | set(item['affected_chapters'])) - set(writes)
        result.append({'issue_index':idx, 'classification':kind, 'reason':reason,
                       'requested_chapters':list(writes), 'dependency_chapters':sorted(reads),
                       'source_quotes':list(quotes)})
    return {'issues':result}
