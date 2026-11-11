"""Serialization duties are planned bindings, never evidence of delivered prose."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import re

import pytest

from core.chapter_review import project_outline
from core.contracts import ChapterContract
from core.narrative_format import validate_volume_contracts
from core.volume_obligations import bind_planned_volume_obligations
from test_long_form_quality import _contract, _foundation


def chapter_contract(number=1, events=()):
    return ChapterContract(chapter=number,
        **{key: f'{key} for chapter {number}' for key in (
            'goal', 'obstacle', 'active_choice', 'cost', 'irreversible_change',
            'local_payoff', 'ending_pressure')},
        preserve_facts=('The guard still holds the key.',),
        allowed_knowledge=('Only the guard knows the lock.',),
        world_event_ids=events)


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def projected_contract(text):
    return json.loads(re.search(r'\[CHAPTER_CONTRACT\]\s*(.*?)\s*\[/CHAPTER_CONTRACT\]', text, re.S)[1])


def test_binding_preserves_source_and_every_non_event_contract_field():
    fmt = _contract(60)
    volumes = _foundation(fmt)['volume_contracts']
    source = chapter_contract(events=('custom_arrival', 'unknown_future_id', 'custom_arrival'))
    original_volumes = deepcopy(volumes)
    effective, receipt = bind_planned_volume_obligations(source, volumes, format_contract=fmt)
    assert effective.world_event_ids == (*source.world_event_ids, 'volume_01_promise')
    assert {k: v for k, v in effective.to_dict().items() if k != 'world_event_ids'} == {
        k: v for k, v in source.to_dict().items() if k != 'world_event_ids'}
    assert source.world_event_ids == ('custom_arrival', 'unknown_future_id', 'custom_arrival')
    assert volumes == original_volumes
    assert receipt['source_world_event_ids'] == list(source.world_event_ids)
    assert receipt['source_contract_id'] == source.contract_id
    assert receipt['effective_contract_id'] == effective.contract_id
    assert receipt['source_contract_sha256'] == canonical_sha(source.to_dict())
    assert receipt['effective_contract_sha256'] == canonical_sha(effective.to_dict())
    assert receipt['volume_contracts_sha256'] == canonical_sha(volumes)
    assert receipt['narrative_format_id'] == fmt.format_id
    assert receipt['kind'] == 'planned_volume_obligations'
    assert receipt['evidence_scope'] == 'planned_not_observed'
    assert receipt['added_event_ids'] == ['volume_01_promise']
    assert json.loads(json.dumps(receipt)) == receipt


@pytest.mark.parametrize('count', [60, 100])
def test_all_volumes_and_coincident_milestones_are_bound_exactly_when_due(count):
    fmt = _contract(count)
    raw = _foundation(fmt)['volume_contracts']
    for volume in raw:
        volume['climax_chapter'] = volume['chapter_end']
    volumes = validate_volume_contracts(fmt, raw)
    seen = []
    for number in range(1, count + 1):
        effective, receipt = bind_planned_volume_obligations(chapter_contract(number), volumes, format_contract=fmt)
        due = [o for v in volumes for o in v['obligations'] if o['due_chapter'] == number]
        assert receipt['due_obligations'] == due
        assert effective.world_event_ids == tuple(o['event_id'] for o in due)
        seen.extend(effective.world_event_ids)
    assert sorted(seen) == sorted(o['event_id'] for v in volumes for o in v['obligations'])
    end = volumes[0]['chapter_end']
    effective, _ = bind_planned_volume_obligations(chapter_contract(end), volumes, format_contract=fmt)
    assert effective.world_event_ids == ('volume_01_climax', 'volume_01_payoff', 'volume_01_carryover')


def test_binding_is_idempotent_and_keeps_existing_ids_in_original_order():
    fmt = _contract(60)
    volumes = _foundation(fmt)['volume_contracts']
    source = chapter_contract(events=('volume_01_promise', 'custom_arrival'))
    first, receipt = bind_planned_volume_obligations(source, volumes, format_contract=fmt)
    second, again = bind_planned_volume_obligations(first, volumes, format_contract=fmt)
    assert source == first == second
    assert receipt == again
    assert receipt['added_event_ids'] == []


def test_shared_binding_preserves_schema_two_commercial_fields():
    from commercial_fixtures import chapter_contract_v2
    fmt = _contract(60)
    volumes = _foundation(fmt)['volume_contracts']
    source = chapter_contract_v2()
    effective, receipt = bind_planned_volume_obligations(source, volumes, format_contract=fmt)
    assert effective.schema_version == 2
    assert {k: v for k, v in effective.to_dict().items() if k != 'world_event_ids'} == {
        k: v for k, v in source.to_dict().items() if k != 'world_event_ids'}
    assert receipt['added_event_ids'] == ['volume_01_promise']


def test_short_novel_receives_no_automatic_volume_obligations():
    fmt = _contract(8)
    assert not fmt.is_long_form
    volumes = _foundation(fmt)['volume_contracts']
    source = chapter_contract(events=('custom_arrival',))
    effective, receipt = bind_planned_volume_obligations(source, volumes, format_contract=fmt)
    assert effective == source
    assert receipt['due_obligations'] == receipt['added_event_ids'] == []


@pytest.mark.parametrize('fault', ['missing_volume', 'wrong_range', 'invented_obligation', 'wrong_chapter'])
def test_binding_rejects_noncanonical_volume_or_chapter_permissions(fault):
    fmt = _contract(60)
    volumes = _foundation(fmt)['volume_contracts']
    source = chapter_contract()
    if fault == 'missing_volume':
        volumes.pop()
    elif fault == 'wrong_range':
        volumes[0]['chapter_start'] = 2
    elif fault == 'invented_obligation':
        volumes[0]['obligations'][0]['event_id'] = 'pretend_delivered'
    else:
        source = replace(source, chapter=61)
    with pytest.raises(ValueError):
        bind_planned_volume_obligations(source, volumes, format_contract=fmt)


def test_projection_preserves_complete_design_and_adds_planned_volume_context():
    fmt = _contract(60)
    foundation = _foundation(fmt)
    chapter = foundation['chapters'][0]
    chapter.update(scenes=[{'detail': 'A quiet admission.', 'nested': {'preserve': 'Every clue.'}}],
                   execution_contract=chapter_contract().to_dict())
    original = deepcopy(foundation)
    text = project_outline(foundation, 1)
    assert projected_contract(text)['world_event_ids'] == ['volume_01_promise']
    original_design = {k: v for k, v in chapter.items() if k != 'execution_contract'}
    assert json.dumps(original_design, ensure_ascii=False, indent=2) in text
    assert foundation == original
    assert 'planned_not_observed' in text
    assert foundation['volume_contracts'][0]['volume_promise'] in text
    assert foundation['volume_contracts'][0]['payoff'] in text
    assert 'reader expectation' in text and 'not' in text
    assert project_outline(foundation, 1) == text


def test_projection_without_format_remains_compatible_and_does_not_add_ids():
    source = chapter_contract(events=('custom_arrival',))
    foundation = {'chapters': [{'number': 1, 'title': 'The Key', 'scenes': [], 'execution_contract': source.to_dict()}]}
    assert projected_contract(project_outline(foundation, 1)) == source.to_dict()
    assert 'PLANNED_VOLUME_OBLIGATIONS' not in project_outline(foundation, 1)


def test_projection_with_short_format_does_not_add_long_form_duties():
    foundation = _foundation(_contract(8))
    source = chapter_contract()
    foundation['chapters'][0]['execution_contract'] = source.to_dict()
    text = project_outline(foundation, 1)
    assert projected_contract(text) == source.to_dict()
    assert 'PLANNED_VOLUME_OBLIGATIONS' not in text


@pytest.mark.parametrize('fault', ['invalid_format', 'missing_volumes', 'wrong_volume', 'wrong_volume_number', 'bool_chapter'])
def test_projection_rejects_invalid_declared_serialization(fault):
    fmt = _contract(60)
    foundation = _foundation(fmt)
    foundation['chapters'][0]['execution_contract'] = chapter_contract().to_dict()
    number = 1
    if fault == 'invalid_format':
        foundation['narrative_format']['mode'] = 'unknown_mode'
    elif fault == 'missing_volumes':
        foundation.pop('volume_contracts')
    elif fault == 'wrong_volume':
        foundation['chapters'][0]['volume_id'] = 'volume_02'
    elif fault == 'wrong_volume_number':
        foundation['chapters'][0]['volume_number'] = 2
    else:
        number = True
    with pytest.raises(ValueError):
        project_outline(foundation, number)
