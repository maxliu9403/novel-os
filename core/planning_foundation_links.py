"""Repair derived cast registrations and broken payoff links under issue-specific write scope.

The registry is append-only for character identities. A payoff may move only from
a missing setup to an earlier, uniquely defined setup selected from cited evidence.
Every candidate edit is still subject to full assembled semantic verification.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import json
import re

from core.outline_planning import content_hash, _json
from core.planning_capacity import input_limit
from core.skeleton_projection import chapter_locations, chapter_source

_REGISTRIES = ('character_registration', 'payoff_binding')
_ID = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,95}')
DISCOVERY = '''STAGE: planning_registry_scope
Classify these exact historical review findings, in any language. For each, identify
only the registry corrections explicitly requested by the finding. "character_registration"
means missing source-backed people/character IDs in the generated cast, "payoff_binding"
means broken references to existing setup IDs. Ordinary story corrections require neither.
Return {"issues":[{"issue_index":0,"foundation_repairs":[]}]}, once for each supplied index.
This bookkeeping cannot authorize an edit or an author decision.'''
INSTRUCTION = '''STAGE: planning_registry_repair
Repair only the supplied review finding and writable chapters, using the supplied adopted
source as authority. Candidate registers and candidate passages describe generated work.
Return {"characters":[{"id":"new_id","name":"existing descriptive label in the candidate or source",
"source_label":"verbatim identifying name or role from the source",
"appearances":[{"chapter":1,"quote":"verbatim source passage establishing this role there"}]}],
"payoff_links":[{"chapter":1,"index":0,"before":"missing_id","setup_id":"existing_id",
"source_quote":"verbatim relevant author passage","reason":"why this exact setup is paid here"}]}.
Only use tools named in foundation_repairs. Use [] if unsupported or unnecessary.
Register only a person who is explicitly present in the adopted source, absent from the
existing cast and required by this issue. For an unnamed person copy a neutral descriptive
label from the candidate/source in its original language; never invent a proper name,
gender, pronouns, aliases, relatives or background. Different source roles need distinct
IDs. List only supported writable appearances. Preserve every existing identity.
For each dangling_payoffs slot pick a unique earlier eligible_setups entry whose actual
promise and the full relevant author passage support this particular payoff. If evidence
is inconclusive, preserve the existing slot for fresh review. Never fix an unrelated slot,
rewrite a setup, rename a valid reference or decide a new author fact.'''


def _text(v):
    return isinstance(v, str) and bool(v.strip())


def _source(context):
    return context.get('complete_adopted_source') or context['adopted_source_projection']['text']


def _catalog(candidate):
    definitions = defaultdict(list)
    for chapter in candidate['chapters']:
        for setup in chapter.get('setups', []):
            if isinstance(setup, dict):
                key = setup.get('setup_id', setup.get('id'))
                if _text(key):
                    definitions[key].append((chapter['number'], setup))
    ledger = defaultdict(list)
    for record in candidate.get('promise_ledger', []):
        if isinstance(record, dict):
            key = record.get('setup_id', record.get('id'))
            if _text(key):
                ledger[key].append((record.get('setup_chapter'), record))
    unique = {}
    for key in definitions.keys() | ledger.keys():
        owners, mirror = definitions[key], ledger[key]
        # A single global mirror for the same chapter is a reference to the same setup.
        if (len(owners) > 1 or len(mirror) > 1 or
                (owners and mirror and mirror[0][0] != owners[0][0])):
            continue
        number, record = (owners or mirror)[0]
        if type(number) is int and 1 <= number <= len(candidate['chapters']):
            unique[key] = {'setup_id':key, 'chapter':number, 'definition':record}
    return set(definitions) | set(ledger), unique


def build_context(candidate, issue, item, source, registries):
    writable = set(item['writable_chapters'])
    read = writable | set(item['dependency_chapters']) | set(item.get('affected_chapters', []))
    known, unique = _catalog(candidate)
    slots, chapters = [], []
    for ch in candidate['chapters']:
        n = ch['number']
        if n not in writable:
            continue
        if 'character_registration' in registries and isinstance(ch.get('character_ids'), list):
            chapters.append({'chapter': n, 'existing_ids':ch['character_ids'],
                             'title':ch.get('title'), 'summary':ch.get('summary'), 'scenes':ch.get('scenes', [])})
        if 'payoff_binding' in registries:
            for index, payoff in enumerate(ch.get('payoffs', [])):
                if not isinstance(payoff, dict):
                    continue
                old = payoff.get('setup_id')
                if _text(old) and old not in known:
                    slots.append({'chapter':n, 'index':index, 'before':old, 'payoff':payoff})
    finding = json.dumps(issue, ensure_ascii=False)
    named = {slot['before'] for slot in slots if re.search(
        r'(?<![\w-])' + re.escape(slot['before']) + r'(?![\w-])', finding)}
    if named:
        slots = [slot for slot in slots if slot['before'] in named]
    last = max((s['chapter'] for s in slots), default=0)
    setups = [v for _,v in sorted(unique.items()) if v['chapter'] in read and v['chapter'] <= last]
    cast = [{k:c[k] for k in ('id','name','aliases','role') if k in c}
            for c in candidate.get('characters', []) if isinstance(c,dict)] if chapters else []
    locations = chapter_locations(source, len(candidate['chapters']))
    return {'foundation_repairs':list(registries), 'original_finding':issue,
            'issue_source_quotes':item['source_quotes'], 'complete_adopted_source':source,
            **{k:item[k] for k in ('prior_author_choices','binding_author_decision') if k in item},
            'writable_chapters':sorted(writable), 'character_chapters':chapters, 'existing_cast':cast,
            'dangling_payoffs':slots, 'eligible_setups':setups,
            'source_chapter_spans':{str(n):loc['source_chars'] for n,loc in locations.items() if n in writable}}


def _object(v, fields):
    if not isinstance(v,dict) or set(v) != set(fields):
        raise ValueError(f'Registry proposal requires exactly {sorted(fields)}')


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)


def _chapter_lookup(candidate):
    return {c['number']:c for c in candidate['chapters']}


def apply(candidate, context, proposal):
    """Apply checked metadata to a copy. The caller obtains each context from the frozen candidate."""
    _object(proposal, ('characters','payoff_links'))
    if not all(isinstance(v,list) for v in proposal.values()):
        raise ValueError('Registry repair must return two lists')
    result = deepcopy(candidate)
    source = _source(context)
    chapters = _chapter_lookup(result)
    appearances = {c['chapter']:c for c in context['character_chapters']}
    names = set()
    ids = set()
    for c in result.get('characters', []):
        ids.add(c['id'])
        names.update(s.strip().casefold() for s in [c.get('name'),*(c.get('aliases') or [])] if _text(s))
    for char in proposal['characters']:
        _object(char, ('id','name','source_label','appearances'))
        key, name, label, refs = (char[k] for k in ('id','name','source_label','appearances'))
        if ('character_registration' not in context['foundation_repairs']
                or not _text(key) or not _ID.fullmatch(key) or key in ids or not _text(name) or not _text(label)
                or any(x.strip().casefold() in names for x in (name,label)) or label not in source
                or not isinstance(refs, list) or not refs):
            raise ValueError('Character registration needs a new identity and a real source role')
        visited = set()
        attested_label = any(label in q for q in context['issue_source_quotes'])
        for entry in refs:
            _object(entry, ('chapter','quote'))
            n, quote = entry['chapter'], entry['quote']
            if (type(n) is not int or n not in appearances or n in visited or not _text(quote)
                    or quote not in source):
                raise ValueError('New character appearance needs an authorized chapter and author quotation')
            span = context['source_chapter_spans'].get(str(n))
            if span and quote not in source[span[0]:span[1]]:
                raise ValueError('Appearance quote must belong to the corresponding original chapter')
            visited.add(n)
            attested_label |= label in quote
        # A pre-existing dangling reference in another chapter cannot be rebound by
        # adding a new global identity under the same identifier.
        if any(key in (ch.get('character_ids') or []) or ch.get('pov_character_id') == key
               or any(isinstance(scene, dict) and scene.get('pov_character_id') == key
                      for scene in ch.get('scenes', []))
               for n, ch in chapters.items() if n not in visited):
            raise ValueError('New character ID collides with an out-of-scope chapter reference')
        narrative = [text for n in visited for text in _strings(appearances[n])
                     if _text(text)]
        cited = context['issue_source_quotes'] + [entry['quote'] for entry in refs]
        if not attested_label or not any(name.casefold() in text.casefold() for text in cited + narrative):
            raise ValueError('New character name must already occur in its source or supplied chapter evidence')
        ids.add(key)
        names.update(x.strip().casefold() for x in (name,label))
        result['characters'].append({'id':key,'name':name,'role':'supporting','gender':'','pronouns':'',
                                    'aliases':[]})
        for n in sorted(visited):
            if key not in chapters[n]['character_ids']:
                chapters[n]['character_ids'].append(key)

    existing, unique = _catalog(candidate)
    slots = {(s['chapter'], s['index']):s for s in context['dangling_payoffs']}
    targets = {s['setup_id']:s for s in context['eligible_setups']}
    used = set()
    for link in proposal['payoff_links']:
        _object(link, ('chapter','index','before','setup_id','source_quote','reason'))
        n,index,before,after,quote = (link[k] for k in ('chapter','index','before','setup_id','source_quote'))
        if (type(n) is not int or type(index) is not int or (n,index) not in slots or (n,index) in used
                or 'payoff_binding' not in context['foundation_repairs']
                or not _text(before) or before in existing or not _text(after) or after not in unique
                or after not in targets or unique[after]['chapter'] > n or not _text(link['reason'])
                or not _text(quote) or quote not in source):
            raise ValueError('Payoff must reference an authorized dangling slot and a unique supported existing setup')
        old = chapters[n]['payoffs'][index]
        if slots[(n,index)]['before'] != before or old.get('setup_id') != before:
            raise ValueError('Stale payoff reference')
        old['setup_id'] = after
        used.add((n,index))
    return result


def _request_binding(name, context, instruction):
    deps = {'context':content_hash(context),'policy':content_hash(instruction)}
    return name + '-' + content_hash(deps)[:16], deps


def _started(planner, stage, dependencies):
    if planner._load(stage, dependencies, lambda value: value, strict_dependencies=True) is not None:
        return True
    cache = getattr(planner, 'cache', None)
    if cache is None:
        return False
    raw = cache / 'raw'
    return ((cache / f'{stage}-response.json').is_file()
            or any(raw.glob(f'{stage}.*.prompt.txt')) or any(raw.glob(f'{stage}.*.response.txt')))


def source_context(planner, name, complete, project, instruction, limit):
    """An issued request fixes its source view. Unsent work can adapt to a smaller route."""
    full_stage, full_deps = _request_binding(name, complete, instruction)
    if _started(planner, full_stage, full_deps):
        return complete
    scoped = project()
    if scoped is None:
        return complete
    stage, deps = _request_binding(name, scoped, instruction)
    if _started(planner, stage, deps):
        return scoped
    fits = planner._prompt_fits(instruction+'\n\n'+_json(complete), 'review', limit)
    return complete if fits else scoped


def _responses(planner, name, context, instruction, validate, limit):
    stage, deps = _request_binding(name, context, instruction)
    cached = planner._load(stage,deps,validate,strict_dependencies=True)
    if cached is None:
        cached = planner._call(stage,instruction,context,validate,strict_dependencies=True,
                               recover_archived=True,input_limit_bytes=limit)
        planner._save(stage,deps,cached)
    return cached


def _discover(planner, pending, review, limit):
    context = {'issues':[{'issue_index':i,'finding':review['issues'][i]} for i in sorted(pending)]}

    def validate(reply):
        _object(reply, ('issues',))
        if not isinstance(reply['issues'], list):
            raise ValueError('Classify the supplied historical issues')
        seen = set()
        for entry in reply['issues']:
            _object(entry, ('issue_index','foundation_repairs'))
            i, tools = entry['issue_index'],entry['foundation_repairs']
            if (type(i) is not int or i not in pending or i in seen or not isinstance(tools,list)
                    or any(t not in _REGISTRIES for t in tools) or len(set(tools)) != len(tools)):
                raise ValueError('Historical registry scope must use the supplied issues and capabilities')
            seen.add(i)
        if seen != set(pending):
            raise ValueError('Every historical issue requires an explicit registry scope')
        return reply
    response = _responses(planner,'evidence-registry-scope-v1',context,DISCOVERY,validate,limit)
    return {s['issue_index']:s['foundation_repairs'] for s in response['issues']}


def repair(planner, candidate, review, evidence_plan, *, blocked_indices):
    items = {i['issue_index']:i for i in evidence_plan['issues'] if i['issue_index'] not in blocked_indices
             and i['classification'] in ('contradiction','continuity_gap') and i['writable_chapters']}
    if not items:
        return candidate, []
    limit = input_limit(planner.client, negotiate=True)
    scopes = {idx:i['foundation_repairs'] for idx,i in items.items() if 'foundation_repairs' in i}
    pending = items.keys() - scopes.keys()
    if pending:
        scopes.update(_discover(planner,pending,review,limit))
    derived, audit = candidate, []
    for idx,item in items.items():
        if not scopes[idx]:
            continue
        context = build_context(derived,review['issues'][idx],item,planner.source,scopes[idx])
        if not context['character_chapters'] and not context['dangling_payoffs']:
            continue
        def project():
            numbers = sorted(set(item['writable_chapters']+item['dependency_chapters']+item['affected_chapters']))
            view = chapter_source(planner.source,numbers,len(candidate['chapters']))
            if view['mode'] != 'chapter_selection' or not all(q in view['text'] for q in item['source_quotes']):
                return None
            scoped = {**context, 'adopted_source_projection':view, 'source_chapter_spans':{}}
            del scoped['complete_adopted_source']
            for n, (a,b) in context['source_chapter_spans'].items():
                original = planner.source[a:b]
                start = view['text'].find(original)
                if start < 0 or view['text'].find(original,start+1) >= 0:
                    return None
                scoped['source_chapter_spans'][n] = [start,start+len(original)]
            return scoped
        name = f'evidence-registry-repair-v1-{idx}'
        context = source_context(planner, name, context, project, INSTRUCTION, limit)
        def validate(value):
            apply(derived,context,value)
            return value
        patch = _responses(planner, name,context,INSTRUCTION,validate,limit)
        derived = apply(derived,context,patch)
        audit.append({'issue_index':idx,'foundation_repairs':scopes[idx],'proposal':deepcopy(patch)})
    return derived, audit
