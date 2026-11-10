"""Revise a cited, generated premise without changing its source or the frozen candidate.

An explicitly evidenced assumption is reviewable, not presumed false. Its existing
slot and before-value are immutable preconditions. Each result is a separate child
checkpoint; publication still requires a review of the complete assembled book.
"""
from __future__ import annotations

import copy
import re
from core.outline_planning import _json, content_hash
from core.planning_capacity import input_limit

INSTRUCTION = """STAGE: coordinated_repair_assumption
Check the single generated assumption against the supplied adopted source and evidence.
The candidate is an inference, never author testimony. Preserve all supported facts and
unrelated detail. If supported, return the exact before text unchanged; otherwise
replace only the mistaken inference, in the candidate's language, with a faithful
assumption. Do not decide an ambiguity on the author's behalf. An official belief or
reported account does not itself establish the mechanism behind it. Keep narrative
facts and who believes them distinct. Return only {path,before,after,reason,source_quotes}
with the supplied path and before, a nonblank after, reason, and exact quotes from the
supplied author source. This proposal does not approve the story."""
_SLOT = re.compile(r'/assumptions/(0|[1-9][0-9]*)')


def _validate(value, path, before, source):
    if not isinstance(value, dict) or set(value) != {'path','before','after','reason','source_quotes'}:
        raise ValueError('Assumption revision must contain one complete cited proposal')
    if value['path'] != path or value['before'] != before:
        raise ValueError('Assumption revision attempted to edit an unrequested or stale value')
    if not all(isinstance(value[k], str) and value[k].strip() for k in ('after','reason')):
        raise ValueError('Assumption revision needs replacement text and reason')
    quotes = value['source_quotes']
    if not isinstance(quotes, list) or not quotes or not all(
            isinstance(q, str) and q.strip() and q in source for q in quotes):
        raise ValueError('Assumption revision needs exact author evidence from its input')
    return value


def reconcile_assumptions(planner, candidate, review, evidence_plan, *, blocked_indices):
    slots, held = {}, set()
    for item in evidence_plan['issues']:
        for entry in item.get('candidate_evidence', []):
            path = entry['path']
            m = _SLOT.fullmatch(path)
            if not m:
                continue
            n = int(m[1])
            assumptions = candidate.get('assumptions', [])
            if n >= len(assumptions) or not isinstance(assumptions[n], str) or not (
                    isinstance(entry['quote'], str) and entry['quote'].strip() and entry['quote'] in assumptions[n]):
                raise ValueError('Cited generated assumption does not match the frozen candidate')
            if item['issue_index'] in blocked_indices:
                held.add(path)
            elif item['classification'] in {'contradiction','continuity_gap'}:
                slots.setdefault(path, {})[item['issue_index']] = item
    revisions = []
    derived = copy.deepcopy(candidate)
    for path in sorted(slots.keys() - held):
        findings = slots[path]
        index = int(path.rsplit('/',1)[1])
        before = candidate['assumptions'][index]
        context = {'path':path,'before':before,'complete_adopted_source':planner.source,
                   'issues':[{'finding':review['issues'][i], 'evidence':item} for i,item in findings.items()]}
        limit = input_limit(planner.client, negotiate=True)
        source = planner.source
        if not planner._prompt_fits(INSTRUCTION+'\n\n'+_json(context), 'review', limit):
            from core.skeleton_projection import chapter_source
            ns = sorted({n for item in findings.values() for key in ('affected_chapters','dependency_chapters')
                         for n in item[key]})
            view = chapter_source(source, ns, planner.chapter_count)
            quotes = [q for item in findings.values() for q in item['source_quotes']]
            if view['mode'] == 'chapter_selection' and all(q in view['text'] for q in quotes):
                source = view['text']
                del context['complete_adopted_source']
                context['adopted_source_projection'] = view
        deps = {'candidate':content_hash(candidate),'evidence':content_hash(context),'policy':content_hash(INSTRUCTION)}
        name = 'evidence-assumption-v1-' + str(index) + '-' + content_hash(deps)[:16]
        def validate(value):
            return _validate(value, path, before, source)
        patch = planner._load(name,deps,validate,strict_dependencies=True)
        if patch is None:
            patch = planner._call(name,INSTRUCTION,context,validate,input_limit_bytes=limit,strict_dependencies=True)
            planner._save(name,deps,patch)
        derived['assumptions'][index] = patch['after']
        revisions.append({**copy.deepcopy(patch),'issue_indices':sorted(findings)})
    return derived, revisions
