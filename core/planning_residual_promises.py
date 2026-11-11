"""Source-backed, issue-scoped edits to a generated setup and payoff chain."""
from __future__ import annotations

from copy import deepcopy
import re

from core.outline_planning import content_hash
from core.planning_capacity import input_limit
from core import planning_foundation_links as links
from core.skeleton_projection import chapter_locations, chapter_source
from core.story_markdown import chinese_number

INSTRUCTION = """STAGE: coordinated_residual_promises
Resolve only the supplied promise-ledger finding. Author text is authority. Candidate IDs
are opaque: preserve a declared identity even if its name contains the wrong chapter.
Return {"registrations":[],"bindings":[]} with only supported changes. Use the candidate's
language for all story text. Prefer an integer zero-based index into issue_source_quotes for
each source_quote. If none supports the event, copy a brief exact phrase from the supplied
author text in its original language. Use an index or a string, never a paraphrase.
To restore a missing or misplaced setup use:
{"setup_id":"stable_id","previous_chapter":null,"origin_chapter":1,"horizon":"book-level",
 "promise":"what is actually planted at the origin","source_quote":"origin evidence",
 "events":[{"chapter":4,"status":"recalled","payoff":"later reminder, still outstanding",
 "source_quote":"evidence for reminder"},{"chapter":8,"status":"paid","payoff":"actual result",
 "source_quote":"evidence for realization"}]}
Use previous_chapter=null ONLY for an undeclared setup. Otherwise give the existing
origin. A later mistaken origin with a chapter setup needs a recalled event at that
chapter. Include every realization chapter actually supported by this finding and its
source. A reminder of a promise does not fulfill it. Preserve other plot threads.
For a dangling reference use {"chapter":3,"field":"reserved_later_payoffs","index":0,
"before":"undeclared_id","target":"declared_id","source_quote":"relevant source evidence",
"reason":"why this specific declared promise is the right one"}. The other supported field
is payoffs. Use only the exact dangling slots shown. A paid event must link to a planted
setup. A reserved promise may also link to a declared book-level payoff chain with
registered setup IDs. Never rename a valid reference, invent an author requirement,
or use an unrelated setup simply because it exists. Leave unsupported operations empty.
Changes are checked together and require subsequent assembled-book review."""

DISCOVERY = """STAGE: coordinated_residual_promise_scope
From the saved review findings, select the indices explicitly requiring repair of a
generated promise's origin, reactivation, fulfillment, registry or dangling promise/setup
ID. A narrative continuity issue alone does not qualify. Return {"issue_indices":[]}.
Use only supplied indices. This classification cannot itself authorize a chapter write."""

_FIELDS = {'payoffs':'setup_id', 'reserved_later_payoffs':'promise_id'}
# Existing runtime foreshadowing IDs include namespace separators, such as ch1:fs1.
_SETUP_ID = re.compile(r'[A-Za-z][A-Za-z0-9_:-]{0,95}')


def _ending(candidate):
    records = candidate.get('ending_contract', {}).get('plot_payoffs', [])
    groups = {}
    for record in records:
        if isinstance(record, dict) and links._text(record.get('id')):
            groups.setdefault(record['id'], []).append(record)
    return {key: rows[0] for key, rows in groups.items() if len(rows) == 1}


def _id(record):
    return record.get('setup_id', record.get('id')) if isinstance(record, dict) else None


def _array(obj, field):
    if field not in obj:
        obj[field] = []
    if not isinstance(obj[field], list):
        raise ValueError(f'Cannot edit malformed promise array {field}')
    return obj[field]


def build_context(candidate, finding, item, source):
    writable = set(item['writable_chapters'])
    read = writable.union(item['affected_chapters'], item['dependency_chapters'])
    known, unique = links._catalog(candidate)
    ending = _ending(candidate)
    chapter_rows = []
    dangling = []
    for chapter in candidate['chapters']:
        n = chapter['number']
        if n not in read:
            continue
        chapter_rows.append({'chapter': n, **{key:chapter.get(key, []) for key in ('setups','payoffs')},
                             'summary':chapter.get('summary', '')})
        if n in writable:
            for field, ref in _FIELDS.items():
                for i, row in enumerate(chapter.get(field, [])):
                    before = row.get(ref) if isinstance(row, dict) else None
                    if (links._text(before) and before not in known
                            and (field != 'reserved_later_payoffs' or before not in ending)):
                        dangling.append({'chapter':n,'field':field,'index':i,'before':before,'entry':row})
    # Preserve the meanings of registered chains and their initiating setups, even if the
    # originating chapter itself is read-only or outside this local narrative window.
    chain_setups = {key for row in ending.values() for key in row.get('setup_ids', [])
                    if links._text(key)}
    setups = [row for key,row in sorted(unique.items()) if row['chapter'] in read or key in chain_setups]
    visible_ids = {s['setup_id'] for s in setups}
    mirrors = [r for r in candidate.get('promise_ledger', []) if _id(r) in visible_ids]
    return {'candidate_sha256':content_hash(candidate), 'finding':finding, 'issue_source_quotes':item['source_quotes'],
            'matching_ledger_entries':mirrors,
            'writable_chapters':sorted(writable), 'chapters':chapter_rows, 'dangling_slots':dangling,
            'eligible_setups':setups, 'book_payoff_chains':list(ending.values()),
            'complete_adopted_source':source,
            **{k:item[k] for k in ('prior_author_choices','binding_author_decision') if k in item}}


def _checked_evidence(quote, n, context, source, locations):
    visible = links._source(context)
    if not links._text(quote) or quote not in source or quote not in visible:
        raise ValueError('Each promise operation requires exact visible author evidence')
    # A global schedule or an explicit retrospective in an adjudicated issue may locate
    # an earlier event. Do not require the quote to occur in the origin chapter itself.
    if not locations:
        return
    pos = source.find(quote)
    while pos >= 0:
        overlapping = {chapter for chapter, span in locations.items()
                       if pos < span['source_chars'][1] and pos + len(quote) > span['source_chars'][0]}
        if not overlapping or n in overlapping:
            return
        pos = source.find(quote, pos + 1)
    mentions = [int(x) for x in re.findall(r'\bChapter\s+(\d+)\b', quote, re.I)]
    mentions.extend(chinese_number(x) for x in re.findall(r'第\s*([零〇一二三四五六七八九十百两\d]+)\s*章', quote))
    if n not in mentions and not any(quote in cited or cited in quote for cited in context['issue_source_quotes']):
        raise ValueError('Promise evidence belongs to another chapter outside this finding')


def _expand_quotes(proposal, context):
    # Raw responses keep the wire IDs. Checkpoints and audits carry the recovered
    # original passage so they can be read independently of the prompt.
    expanded = deepcopy(proposal)
    rows = [*expanded['bindings'], *expanded['registrations']]
    for registration in expanded['registrations']:
        if isinstance(registration, dict) and isinstance(registration.get('events'), list):
            rows.extend(registration['events'])
    for row in rows:
        if isinstance(row, dict) and type(row.get('source_quote')) is int:
            i = row['source_quote']
            if not 0 <= i < len(context['issue_source_quotes']):
                raise ValueError('Author source index must select a supplied original quotation')
            row['source_quote'] = context['issue_source_quotes'][i]
    return expanded


def _event(entry, context, source, locations, origin, seen):
    links._object(entry, ('chapter','status','payoff','source_quote'))
    n = entry['chapter']
    if (type(n) is not int or n not in context['writable_chapters'] or n <= origin or n in seen
            or entry['status'] not in ('paid','recalled') or not links._text(entry['payoff'])):
        raise ValueError('Promise event needs a unique authorized later chapter and supported status')
    _checked_evidence(entry['source_quote'], n, context, source, locations)
    seen.add(n)


def _would_rebind(candidate, key, event_chapters, explicit_slots):
    for chapter in candidate['chapters']:
        n = chapter['number']
        for field, ref in _FIELDS.items():
            for i, row in enumerate(chapter.get(field, [])):
                if not isinstance(row, dict) or row.get(ref) != key:
                    continue
                if (n, field, i, key) not in explicit_slots and not (field == 'payoffs' and n in event_chapters):
                    return True
    return any(key in row.get('setup_ids', []) for row in _ending(candidate).values())


def _register(result, context, entry, source, locations, used, explicit_slots):
    links._object(entry, ('setup_id','previous_chapter','origin_chapter','horizon','promise','source_quote','events'))
    key, previous, origin = (entry[k] for k in ('setup_id','previous_chapter','origin_chapter'))
    known, unique = links._catalog(result)
    if (not links._text(key) or key in used or type(origin) is not int or origin not in context['writable_chapters']
            or not links._text(entry['promise']) or not links._text(entry['horizon'])):
        raise ValueError('Invalid setup identity, description, or origin write scope')
    if key in known:
        if (key not in unique or type(previous) is not int or previous != unique[key]['chapter']
                or previous not in context['writable_chapters'] or origin > previous):
            raise ValueError('Setup origin does not match an unambiguous writable registration')
    elif previous is not None or key in _ending(result) or not _SETUP_ID.fullmatch(key):
        raise ValueError('New setup identity collides with a declared payoff chain or stale origin')
    _checked_evidence(entry['source_quote'], origin, context, source, locations)
    events = entry['events']
    if not isinstance(events, list) or not events:
        raise ValueError('A setup restoration needs its source-backed subsequent events')
    seen = set()
    for event in events:
        _event(event, context, source, locations, origin, seen)
    if key not in known and _would_rebind(result, key, seen, explicit_slots):
        raise ValueError('New setup identity would implicitly rebind existing references')
    chapters = links._chapter_lookup(result)
    old_setups = _array(chapters[previous], 'setups') if previous is not None else []
    setup = next((r for r in old_setups if _id(r) == key), None)
    if setup is not None and previous != origin:
        if not any(ev['chapter'] == previous and ev['status'] == 'recalled' for ev in events):
            raise ValueError('A displaced later setup must retain its source-backed reactivation')
        old_setups.remove(setup)
    if setup is None:
        setup = {'setup_id':key}
    if previous != origin or setup not in _array(chapters[origin], 'setups'):
        _array(chapters[origin], 'setups').append(setup)
    ledger = _array(result,'promise_ledger')
    record = next((r for r in ledger if _id(r) == key), None)
    if record is None:
        record = {'setup_id':key}
        ledger.append(record)
    targets = set()
    for row in (record, setup):
        numbers = row.get('intended_payoff_chapters', [])
        if not isinstance(numbers,list) or any(type(n) is not int for n in numbers):
            raise ValueError('Cannot merge malformed intended payoff chapters')
        targets.update(numbers)
    paid = [ev for ev in events if ev['status'] == 'paid']
    targets.update(ev['chapter'] for ev in paid)
    if previous != origin:
        targets.discard(previous)
    for row in (record, setup):
        row.update(promise=entry['promise'], horizon=entry['horizon'], intended_payoff_chapters=sorted(targets))
    record['setup_chapter'] = origin
    if 'setup_chapter' in setup:
        setup['setup_chapter'] = origin
    if not record.get('payoff'):
        record['payoff'] = ' '.join(ev['payoff'] for ev in paid)
    for ev in events:
        rows = _array(chapters[ev['chapter']], 'payoffs')
        matches = [r for r in rows if _id(r) == key]
        if len(matches) > 1:
            raise ValueError('Cannot update an ambiguous chapter payoff')
        if not matches:
            rows.append({'setup_id':key})
        row = matches[0] if matches else rows[-1]
        row.update(horizon=entry['horizon'],payoff_chapter=ev['chapter'],payoff=ev['payoff'],
                   promise_status=ev['status'])
    used.add(key)


def apply(candidate, context, proposal, source):
    """Apply one issue atomically to a copy; dangling slots address the caller's input."""
    links._object(proposal, ('registrations','bindings'))
    if not all(isinstance(v, list) for v in proposal.values()):
        raise ValueError('Promise operations must be lists')
    proposal = _expand_quotes(proposal,context)
    if content_hash(candidate) != context['candidate_sha256']:
        raise ValueError('Stale promise candidate')
    result = deepcopy(candidate)
    locations = chapter_locations(source, len(candidate['chapters']))
    explicit_slots = {(row.get('chapter'),row.get('field'),row.get('index'),row.get('target'))
                      for row in proposal['bindings'] if isinstance(row,dict) and type(row.get('chapter')) is int
                      and type(row.get('index')) is int and links._text(row.get('field'))
                      and links._text(row.get('target')) and row.get('before') == row['target']}
    registered = set()
    for entry in proposal['registrations']:
        _register(result, context, entry, source, locations, registered, explicit_slots)
    slots = {(s['chapter'],s['field'],s['index']):s['before'] for s in context['dangling_slots']}
    _, unique = links._catalog(result)
    visible = registered | {row['setup_id'] for row in context['eligible_setups']}
    chains = _ending(result)
    chapters = links._chapter_lookup(result)
    for entry in proposal['bindings']:
        links._object(entry, ('chapter','field','index','before','target','source_quote','reason'))
        n, field, i, before, target = (entry[k] for k in ('chapter','field','index','before','target'))
        if (type(n) is not int or type(i) is not int or not links._text(field) or not links._text(before)
                or slots.pop((n,field,i),None) != before or not links._text(target)
                or not links._text(entry['reason'])):
            raise ValueError('Binding must address an authorized, unchanged dangling reference')
        _checked_evidence(entry['source_quote'], n, context, source, locations)
        chain = chains.get(target, {}).get('setup_ids', [])
        declared = target in visible and target in unique and unique[target]['chapter'] <= n
        if field == 'reserved_later_payoffs' and target in chains:
            declared = (target not in unique and isinstance(chain, list) and bool(chain)
                        and all(links._text(k) and k in unique for k in chain)
                        and any(unique[k]['chapter'] <= n for k in chain))
        if not declared:
            raise ValueError('Target must identify an already planted setup or a registered payoff chain')
        row = chapters[n][field][i]
        ref = _FIELDS[field]
        if row.get(ref) != before:
            raise ValueError('Stale promise reference')
        row[ref] = target
    return result


def _classify(planner, pending, review, limit):
    ctx = {'findings':[{'issue_index':i, 'finding':review['issues'][i]} for i in sorted(pending)]}
    def validate(reply):
        links._object(reply, ('issue_indices',))
        ids = reply['issue_indices']
        if (not isinstance(ids, list) or any(type(i) is not int or i not in pending for i in ids)
                or len(ids) != len(set(ids))):
            raise ValueError('Registry scope must select unique supplied issue indices')
        return reply
    reply = links._responses(planner,'residual-promise-scope-v1',ctx,DISCOVERY,validate,limit)
    return set(reply['issue_indices'])


def repair(planner, candidate, review, evidence_plan, *, blocked_indices):
    held = {n for i in evidence_plan['issues'] if i['issue_index'] in blocked_indices for n in i['affected_chapters']}
    items = {}
    for item in evidence_plan['issues']:
        if item['issue_index'] in blocked_indices or item['classification'] not in ('contradiction','continuity_gap'):
            continue
        writable = set(item['writable_chapters'])
        if not writable <= set(item['affected_chapters']) or writable.intersection(item['dependency_chapters']):
            raise ValueError('Promise write scope exceeds the material finding')
        if writable - held:
            items[item['issue_index']] = {**item, 'writable_chapters':sorted(writable - held)}
    if not items:
        return candidate, []
    limit = input_limit(planner.client, negotiate=True)
    selected = {i for i, item in items.items() if 'payoff_binding' in item.get('foundation_repairs', [])}
    pending = {i for i, item in items.items() if 'foundation_repairs' not in item}
    if pending:
        selected.update(_classify(planner,pending,review,limit))
    derived, audit = candidate, []
    for idx in sorted(selected):
        item = items[idx]
        context = build_context(derived, review['issues'][idx], item, planner.source)
        def project():
            read = sorted(set(item['affected_chapters']+item['dependency_chapters']+item['writable_chapters']))
            view = chapter_source(planner.source, read, len(candidate['chapters']))
            if view['mode'] != 'chapter_selection' or not all(q in view['text'] for q in item['source_quotes']):
                return None
            scoped = {**context, 'adopted_source_projection':view}
            del scoped['complete_adopted_source']
            return scoped
        name = f'residual-promises-v1-{idx}'
        context = links.source_context(planner, name, context, project, INSTRUCTION, limit)
        def validate(value):
            apply(derived,context,value,planner.source)
            return _expand_quotes(value,context)
        patch = links._responses(planner,name,context,INSTRUCTION,validate,limit)
        updated = apply(derived,context,patch,planner.source)
        if updated != derived:
            audit.append({'issue_index':idx, 'proposal':deepcopy(patch)})
        derived = updated
    return derived, audit
