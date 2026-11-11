"""Close reviewed inconsistencies in existing, generated narrative text.

Each field is addressed in the original candidate, including its original array
position. A proposal can replace one unique span of that field. All other bytes,
structure, author material, IDs and registry records stay with the caller.
"""
from __future__ import annotations

from copy import deepcopy

from core.outline_planning import _json, content_hash
from core.planning_capacity import input_limit
from core import planning_residual_evidence as evidence

INSTRUCTION = """STAGE: coordinated_residual_narrative
Inspect EVERY supplied generated field for this one previously audited finding. Across all
copies of the same story constraint, correct only actual contradictions or missing bridges.
Preserve unrelated details and the language of the field. The review and other generated
text are not author testimony. Respect the exact adopted passages, prior author choices and binding author decision.
When present, shared_edit_direction is the common source-grounded direction for this finding.
The supplied scoped_evidence constrains the edit even when it addresses another page.
Keep uncertainty when the source leaves identity or fate open, and preserve compatible
established design choices. Do not invent a choice or infer a private fact from public belief.
Fields are [typed path,current complete text] in the full candidate. Only these fields can
be edited. Array positions are zero-based. Each field may be corrected once per response.
Return {"edits":[{"path":["chapter_map",0,"ending_obligations",1],"old_span":"exact unique
substring","replacement":"corrected substring","source_indices":[0]}],"reason":"explanation"}.
Use the smallest exact span that fixes the issue and preserves the rest. A wholly wrong
instruction can be replaced with the positive source-grounded constraint. Cite the indices
of directly relevant adopted passages for EVERY edit. If this window needs no narrative
change or the available evidence cannot justify one, return an empty edits list and say why.
Registration and typed promise references are handled separately. Never certify the book."""
# Bound one editor turn even when the provider has not advertised an input limit.
# The actual full request (including the frozen creative context) is checked separately.
_PAGE_BYTES = 48 * 1024
_GLOBAL = ('structural_risks', 'assumptions')
_MAP = ('purpose', 'incoming_consequence', 'outgoing_commitment', 'obligations',
        'ending_obligations', 'reserved_reveals')
_CHAPTER = ('summary', 'chapter_function', 'goal', 'conflict', 'incoming_consequence',
            'active_choice', 'emotional_cause', 'emotional_change', 'cost_and_aftermath',
            'local_payoff', 'ending_hook', 'continuity_obligations', 'unidentified_roles',
            'reserved_later_payoffs')
_SCENE = ('goal', 'conflict', 'change', 'setting', 'emotional_progression',
          'choice_and_cost', 'subtext', 'continuity')
_CONTRACT = ('goal', 'obstacle', 'active_choice', 'cost', 'irreversible_change',
             'local_payoff', 'ending_pressure', 'preserve_facts', 'allowed_knowledge',
             'resource_change')


def _fields(row, keys, prefix):
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            yield [prefix + [key], value]
        elif isinstance(value, list):
            for index, text in enumerate(value):
                if isinstance(text, str) and text.strip():
                    yield [prefix + [key, index], text]


def _inventory(candidate, writable):
    # Actual array positions matter for migrated candidates with a reordered map.
    yield from _fields(candidate, (key for key in _GLOBAL if isinstance(candidate.get(key), list)), [])
    for section in ('chapter_map', 'chapters'):
        for index, row in enumerate(candidate[section]):
            if row['number'] not in writable:
                continue
            prefix = [section, index]
            if section == 'chapter_map':
                yield from _fields(row, _MAP, prefix)
                continue
            yield from _fields(row, _CHAPTER, prefix)
            for key, keys in [('hook_answer', ('short_term', 'arc_level', 'book_level')),
                              ('execution_contract', _CONTRACT)]:
                if isinstance(row.get(key), dict):
                    yield from _fields(row[key], keys, prefix + [key])
                elif key == 'hook_answer' and isinstance(row.get(key), str):
                    yield from _fields(row, (key,), prefix)
            for i, scene in enumerate(row.get('scenes', [])):
                if isinstance(scene, dict):
                    yield from _fields(scene, _SCENE, prefix + ['scenes', i])
            for key, text_key in [('setups', 'promise'), ('payoffs', 'payoff'),
                                  ('reserved_later_payoffs', 'reserved_payoff')]:
                for i, record in enumerate(row.get(key, [])):
                    if isinstance(record, dict):
                        yield from _fields(record, (text_key,), prefix + [key, i])


def _quote_context(source, quotes):
    """Keep the complete source paragraph(s) around every exact citation, once."""
    spans = set()
    for quote in quotes:
        if not isinstance(quote, str) or not quote.strip() or quote not in source:
            raise ValueError('Residual narrative edit requires exact author evidence for its finding')
        start = source.index(quote)
        while start != -1:
            end = start + len(quote)
            previous = source.rfind('\n\n', 0, start)
            following = source.find('\n\n', end)
            spans.add((previous + 2 if previous >= 0 else 0, following if following >= 0 else len(source)))
            start = source.find(quote, end)
    return [source[start:end] for start, end in sorted(spans)]


def _validate(value, context):
    if (not isinstance(value, dict) or set(value) != {'edits', 'reason'}
            or not isinstance(value['edits'], list)
            or not isinstance(value['reason'], str) or not value['reason'].strip()):
        raise ValueError('Residual narrative response requires an edits list and a reason')
    visible = {tuple(path): before for path, before in context['fields']}
    seen = set()
    for edit in value['edits']:
        if not isinstance(edit, dict) or set(edit) != {'path', 'old_span', 'replacement', 'source_indices'}:
            raise ValueError('Each residual edit needs an exact path, original span, replacement and citations')
        path = edit['path']
        if not isinstance(path, list) or any(type(part) not in (int, str) for part in path):
            raise ValueError('Residual edit must use a typed candidate path')
        address = tuple(path)
        if address not in visible or address in seen:
            raise ValueError('Residual edit attempted a repeated or unauthorized field')
        seen.add(address)
        old, new, before = edit['old_span'], edit['replacement'], visible[address]
        if (not isinstance(old, str) or not old.strip() or before.count(old) != 1 or
                not isinstance(new, str) or new == old or not before.replace(old, new, 1).strip()):
            raise ValueError('Residual edit must replace an exact unique span without emptying a field')
        refs = edit['source_indices']
        if (not isinstance(refs, list) or not refs or
                any(type(i) is not int or not 0 <= i < len(context['adopted_passages']) for i in refs)
                or len(set(refs)) != len(refs)):
            raise ValueError('Residual edit must cite adopted evidence bound to this finding')
    return value


def _issue_context(item, finding, source):
    passages = item['source_quotes']
    if not isinstance(passages, list) or not passages:
        raise ValueError('Residual narrative repair requires author evidence')
    return {'finding': finding, 'issue_index': item['issue_index'],
            'resolution_basis': item['reason'],
            'writable_chapters': item['writable_chapters'],
            'read_only_chapters': sorted((set(item['affected_chapters']) |
                                          set(item['dependency_chapters'])) - set(item['writable_chapters'])),
            'adopted_passages': passages, 'source_paragraphs': _quote_context(source, passages),
            'generated_candidate_citations': item.get('candidate_evidence', []),
            **({'prior_author_choices': item['prior_author_choices']} if 'prior_author_choices' in item else {}),
            **({'binding_author_decision': item['binding_author_decision']}
               if 'binding_author_decision' in item else {})}


def repair(planner, candidate, review, evidence_plan, *, blocked_indices):
    derived, audit = deepcopy(candidate), []
    # Shared globals also occur in proposals concerning a held author decision.
    # A field explicitly cited by a held finding is read-only until it is settled.
    held = {entry['path'] for item in evidence_plan['issues'] if item['issue_index'] in blocked_indices
            for entry in item.get('candidate_evidence', [])}
    held_chapters = {n for item in evidence_plan['issues'] if item['issue_index'] in blocked_indices
                     for n in item['affected_chapters']}
    limit = input_limit(planner.client, negotiate=True)
    for item in evidence_plan['issues']:
        index = item['issue_index']
        if index in blocked_indices or item['classification'] not in ('contradiction', 'continuity_gap'):
            continue
        writable = set(item['writable_chapters'])
        if (any(type(n) is not int for n in writable) or not writable <= set(item['affected_chapters'])
                or writable.intersection(item['dependency_chapters'])):
            raise ValueError('Residual narrative edit exceeds its per-finding chapter permission')
        base = _issue_context(item, review['issues'][index], planner.source)
        writable -= held_chapters
        base['writable_chapters'] = sorted(writable)
        base['read_only_chapters'] = sorted(set(base['read_only_chapters']) | (held_chapters & set(item['affected_chapters'])))
        base['chapter_numbers_by_position'] = {
            key: {str(i): row['number'] for i, row in enumerate(derived[key]) if row['number'] in writable}
            for key in ('chapters', 'chapter_map')
            if any(row['number'] != i + 1 for i, row in enumerate(derived[key]))
        }
        def held_field(path):
            pointer = '/' + '/'.join(map(str, path))
            return any(pointer == other or pointer.startswith(other + '/') or other.startswith(pointer + '/')
                       for other in held)

        fields = [row for row in _inventory(derived, writable) if not held_field(row[0])]
        if not fields:
            audit.append({'operation': 'narrative_fields', 'issue_index': index,
                          'inspected_fields': 0, 'edits': [], 'reason': 'No eligible narrative fields in scope'})
            continue
        root_hash = content_hash(derived)
        author, readonly, selected = evidence.scoped_material(planner, derived, {**item, 'writable_chapters': sorted(writable)})
        protected = evidence.protected_fields(selected, readonly, fields)
        direct = {**base, 'scoped_evidence': [*author, *readonly, *protected], 'fields': fields}
        binding = {}
        fits_direct = (len(_json(fields).encode()) <= _PAGE_BYTES and len(_json(direct).encode()) <= 2 * _PAGE_BYTES
                       and evidence.fits(planner, INSTRUCTION, direct, limit))
        if not evidence.partitioned(planner, 'narrative', root_hash, INSTRUCTION, direct, not fits_direct):
            contexts = [direct]
        else:
            constraint, binding = evidence.direction(planner, root_hash, base,
                                                     [*author, *selected], limit, _PAGE_BYTES)
            decision = constraint['shared_edit_direction']
            if not decision['direction'].strip():
                audit.append({'operation': 'narrative_fields', 'issue_index': index, **binding,
                              'edits': [], 'reason': decision['reason']})
                continue
            contexts = evidence.pages(planner, 'narrative', root_hash, INSTRUCTION, {**base, **constraint},
                                      'fields', fields, limit, _PAGE_BYTES)
        for context in contexts:
            def validate(value):
                return _validate(value, context)

            result, name = evidence.checkpoint(planner, 'narrative', root_hash, INSTRUCTION,
                                               context, validate, limit)
            writes = []
            for edit in result['edits']:
                path = edit['path']
                target = derived
                for part in path[:-1]:
                    target = target[part]
                before = target[path[-1]]
                after = before.replace(edit['old_span'], edit['replacement'], 1)
                target[path[-1]] = after
                writes.append({'path': path, 'before': before, 'after': after,
                               'source_quotes': [base['adopted_passages'][i] for i in edit['source_indices']]})
            audit.append({'operation': 'narrative_fields', 'issue_index': index, 'checkpoint': name, **binding,
                          'inspected_fields': len(context['fields']), 'edits': writes, 'reason': result['reason']})
    return derived, audit
