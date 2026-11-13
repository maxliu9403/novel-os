"""Bind host-owned chapter provenance at the unaccepted model-output boundary.

Stored chapters still use strict validation. No story field, original source
anchor, accepted roadmap or previously received response is rewritten here.
"""
from copy import deepcopy

from core.outline_planning import content_hash
from core.planning_design_references import references, validate_catalog

VERSION = 'chapter-reference-binding.v1'
RULES = """
HOST-OWNED CHAPTER REFERENCES v1
The host binds design_reference_ids from frozen_global_design.chapter_map by
chapter number after receiving your response. Omit design_reference_ids and
design_reference_paths from chapter output; do not copy, calculate or choose
these technical identifiers. Read and honor the complete relevant design text,
source obligations, scenes, emotion and cause-effect requirements. This binding
does not certify their delivery: substantive fidelity still requires review.
Keep the ordinary exact source_anchor_ids contract. Never change chapter numbers
to adjust metadata, or shorten any story content to avoid a reference problem.
"""


def has_dispatched(planner, name):
    return any((folder / (name + '-response.json')).exists()
               or any((folder / 'raw').glob(name + '.*.prompt.txt'))
               for folder in (planner.cache, *planner.predecessors))


def instruction(planner, name, dependencies, numbers, original, global_design):
    """New requests opt in; all already dispatched request bytes stay frozen."""
    if 'design_references' not in global_design:
        return original
    deps = {**dependencies, 'requested': numbers, 'policy': VERSION,
            'original_instruction': content_hash(original)}

    def validate(value):
        if value not in ({'version': 1}, {'version': 2}):
            raise ValueError('Invalid saved chapter reference protocol')
        return value

    marker = name + '-reference-protocol'
    value = planner._load(marker, deps, validate, strict_dependencies=True)
    if value is None:
        value = {'version': 1 if has_dispatched(planner, name) else 2}
        planner._save(marker, deps, value)
    return original + (RULES if value['version'] == 2 else '')


def call(planner, name, instruction, context, numbers, global_design, dependencies, **options):
    """One boundary for new detail batches and whole-chapter repair replies."""
    accepted = {}

    def validate(value):
        child, changes = bind_output(value, numbers, global_design)
        result = planner._batch(child, numbers, global_design)
        accepted.update(source=deepcopy(value), candidate=deepcopy(result), changes=changes)
        return result

    result = planner._call(name, instruction, context, validate, recover_archived=True,
                           prefer_completed_correction=True, **options)
    # Persistence errors must not be interpreted as model format defects.
    retain_binding(planner, name, dependencies, numbers, instruction, context,
                   accepted['source'], accepted['candidate'], accepted['changes'])
    return result


def bind_output(value, numbers, global_design):
    """Ignore untrusted ID echoes; derive all bindings from the accepted map."""
    child = deepcopy(value)
    if 'design_references' not in global_design:
        return child, []
    catalog = validate_catalog(global_design['design_references'])
    mapped = global_design['chapter_map']
    references(mapped, catalog)
    by_number = {row['number']: row.get('design_reference_ids', []) for row in mapped}
    rows = child.get('chapters')
    if (not isinstance(rows, list) or len(rows) != len(numbers)
            or any(not isinstance(row, dict) or type(row.get('number')) is not int for row in rows)
            or sorted(row['number'] for row in rows) != numbers
            or any(number not in by_number for number in numbers)):
        raise ValueError('Chapter binding requires exactly the requested existing chapter numbers')
    bindings = []
    for row in rows:
        supplied = 'design_reference_ids' in row
        before = deepcopy(row.get('design_reference_ids'))
        expected = list(by_number[row['number']])
        if expected or supplied:
            if not supplied or before != expected:
                bindings.append({'chapter': row['number'], 'supplied': supplied,
                                 'before': before, 'after': expected})
            row['design_reference_ids'] = expected
    return child, bindings


def retain_binding(planner, name, dependencies, numbers, instruction, context, source, candidate, bindings):
    """Persist a recomputable child only after ordinary story validation passes."""
    if not bindings:
        return
    deps = {**dependencies, 'requested': numbers, 'policy': VERSION,
            'instruction': content_hash(instruction), 'context': content_hash(context),
            'source_candidate': content_hash(source)}
    payload = {'source_candidate': source, 'candidate': candidate, 'bindings': bindings}

    def validate(value):
        if value != payload:
            raise ValueError('Saved chapter reference binding differs from its original response or mapped design')
        return value

    checkpoint = name + '-reference-binding-v1'
    saved = planner._load(checkpoint, deps, validate, strict_dependencies=True)
    if saved is None:
        planner._save(checkpoint, deps, payload)
