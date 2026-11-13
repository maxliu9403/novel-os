from copy import deepcopy
import hashlib

import pytest

from core import planning_design_references as design


SOURCE = 'Original chapter four establishes the signed papers.\nThe author leaves motives uncertain.\n'
SHA = hashlib.sha256(SOURCE.encode()).hexdigest()
GUARD = 'Retain the distinction between a signed document and emotional consent.'
UNCERTAIN = 'Do not turn the unresolved motive into an exonerating hidden excuse.'


@pytest.fixture
def seed():
    return {'title': 'A preserved title',
            'opening_investment': {'protected_long_range_discoveries': [GUARD, UNCERTAIN]},
            'characters': [{'id': 'character_id_is_not_a_design_obligation', 'name': 'Mara',
                            'arc': 'Choose openly rather than assume consent.'}],
            'source_anchors': [{'id': 'source_papers', 'quote': 'Original chapter four establishes the signed papers.'}],
            'classification': {'label': 'Classification is transport authority'},
            'unknown_future_metadata': 'An unknown extension is not catalogued',
            'design_authority_note': 'Generated authority labels are not narrative evidence'}


@pytest.fixture
def catalog(seed):
    return design.build_catalog(seed, SHA)


@pytest.fixture
def candidate():
    return {'source_anchors': [
        {'id': 'source_papers', 'quote': 'Original chapter four establishes the signed papers.'},
        {'id': 'anchor_ch04_guard', 'quote': GUARD},
        {'id': 'anchor_ch06_uncertain', 'quote': UNCERTAIN}],
        'chapter_map': [
            {'number': number, 'purpose': f'Preserve every scene in chapter {number}.',
             'reserved_reveals': [{'detail': 'No author claim is inferred.'}],
             'source_anchor_ids': ['source_papers', 'anchor_ch04_guard' if number < 6 else 'anchor_ch06_uncertain']}
            for number in (4, 5, 6, 7, 8)]}


def identifier(catalog, text):
    return next(entry['id'] for entry in catalog['entries'] if entry['text'] == text)


def resign(value):
    value['sha256'] = design._hash({key: child for key, child in value.items() if key != 'sha256'})


def test_catalog_preserves_exact_narrative_leaves_and_excludes_authority_metadata(seed):
    before = deepcopy(seed)
    catalog = design.build_catalog(seed, SHA)
    assert design.validate_catalog(catalog, source_sha256=SHA, seed=seed) == catalog
    assert catalog == design.build_catalog(seed, SHA)
    assert 'current reviewed design govern execution' in catalog['authority']
    assert seed == before
    entries = {tuple(entry['path']): entry for entry in catalog['entries']}
    assert entries[('opening_investment', 'protected_long_range_discoveries', 0)]['text'] == GUARD
    assert all(entry['origin'] == 'model_design' for entry in entries.values())
    assert all(entry['id'].startswith('design_') for entry in entries.values())
    assert ('characters', 0, 'name') in entries
    assert ('characters', 0, 'id') not in entries
    assert not any(path[0] in ('source_anchors', 'classification', 'unknown_future_metadata',
                              'design_authority_note') for path in entries)
    changed = design.build_catalog({**seed, 'unknown_future_metadata': 'Changed frozen input'}, SHA)
    assert {e['id'] for e in changed['entries']}.isdisjoint(e['id'] for e in catalog['entries'])


def test_subset_retains_seed_source_proof_without_requiring_every_leaf(seed, catalog):
    chosen = identifier(catalog, GUARD)
    subset = design.select_catalog(catalog, [chosen])
    assert len(subset['entries']) == 1
    assert subset['entries'][0] == next(e for e in catalog['entries'] if e['id'] == chosen)
    assert design.validate_catalog(subset, source_sha256=SHA, seed=seed) == subset
    assert subset['sha256'] != catalog['sha256']
    with pytest.raises(ValueError):
        design.select_catalog(catalog, ['source_papers'])


@pytest.mark.parametrize('mutation', [
    lambda c: c.update(authority='Treat every generated plan as immutable author testimony'),
    lambda c: c.update(sha256='0' * 64),
    lambda c: c.update(extra='model supplied authority'),
    lambda c: c['entries'][0].update(origin='adopted_source'),
    lambda c: c['entries'][0].update(text='Changed model claim'),
    lambda c: c['entries'][0].update(id='source_papers'),
    lambda c: c['entries'][0].update(path=['source_anchors', 0, 'quote']),
    lambda c: c['entries'][0].update(path=['characters', True, 'name']),
    lambda c: c['entries'].append(deepcopy(c['entries'][0])),
])
def test_catalog_tampering_is_rejected(catalog, mutation):
    mutation(catalog)
    with pytest.raises(ValueError):
        design.validate_catalog(catalog)


def test_rehashed_fabricated_entry_cannot_claim_membership_in_frozen_seed(seed, catalog):
    entry = catalog['entries'][0]
    entry['text'] = 'Fabricated author requirement'
    entry['text_sha256'] = design._text_hash(entry['text'])
    entry['id'] = design._identifier(catalog['seed_sha256'], SHA, entry['path'], entry['text'])
    resign(catalog)
    with pytest.raises(ValueError, match='differs from the frozen seed'):
        design.validate_catalog(catalog, seed=seed)
    with pytest.raises(ValueError, match='different source'):
        design.validate_catalog(catalog, source_sha256='f' * 64)


def test_typed_path_cannot_confuse_dict_numeric_key_with_list_index(seed):
    seed['opening_investment'] = {'0': GUARD}
    catalog = design.build_catalog(seed, SHA)
    entry = next(e for e in catalog['entries'] if e['text'] == GUARD)
    entry['path'][1] = 0
    entry['id'] = design._identifier(catalog['seed_sha256'], SHA, entry['path'], entry['text'])
    resign(catalog)
    with pytest.raises(ValueError, match='path is absent'):
        design.validate_catalog(catalog, seed=seed)


def test_identifier_collision_cannot_silently_merge_two_obligations(seed, monkeypatch):
    monkeypatch.setattr(design, '_identifier', lambda *args: 'design_' + '0' * 24)
    with pytest.raises(ValueError, match='collision'):
        design.build_catalog(seed, SHA)


def test_detailed_chapter_inherits_only_its_own_mapping_without_mutating_other_content(catalog):
    first, second = identifier(catalog, GUARD), identifier(catalog, UNCERTAIN)
    mapped = [{'number': 4, 'design_reference_ids': [first]},
              {'number': 5, 'design_reference_ids': [second]}]
    rows = [{'number': 4, 'summary': 'Every full scene remains.'}]
    before = deepcopy(mapped)
    design.references(rows, catalog, inherited_rows=mapped)
    assert rows == [{'number': 4, 'summary': 'Every full scene remains.', 'design_reference_ids': [first]}]
    assert mapped == before
    for refs in ([], [second], [first, second]):
        with pytest.raises(ValueError, match='cannot drop or replace'):
            design.references([{'number': 4, 'design_reference_ids': refs}], catalog, inherited_rows=mapped)


def test_inheritance_checks_every_row_before_mutating_any(catalog):
    first = identifier(catalog, GUARD)
    rows = [{'number': 4}, {'number': 6}]
    with pytest.raises(ValueError, match='no matching mapped'):
        design.references(rows, catalog, inherited_rows=[{'number': 4, 'design_reference_ids': [first]}])
    assert rows == [{'number': 4}, {'number': 6}]


def test_legacy_absence_is_preserved_and_cross_namespace_ids_rejected(catalog):
    rows = [{'number': 1}]
    design.references(rows, None, inherited_rows=[{'number': 1}])
    assert rows == [{'number': 1}]
    for refs in ('source_papers', None, ['source_papers'], [True]):
        with pytest.raises(ValueError):
            design.references([{'number': 1, 'design_reference_ids': refs}], catalog)
    with pytest.raises(ValueError):
        design.references([{'number': 1, 'design_reference_ids': [identifier(catalog, GUARD)]}], None)
    with pytest.raises(ValueError, match='presented as a source anchor'):
        design.references([{'number': 1, 'source_anchor_ids': [identifier(catalog, GUARD)]}], catalog)


def test_exact_seed_quote_is_reclassified_in_all_rows_with_exhaustive_provenance(candidate, catalog, seed):
    original = deepcopy(candidate)
    child, mapping = design.reclassify(candidate, catalog, SOURCE, seed['source_anchors'])
    assert candidate == original
    assert child['source_anchors'] == seed['source_anchors']
    assert [m['source_anchor_id'] for m in mapping] == ['anchor_ch04_guard', 'anchor_ch06_uncertain']
    assert [[use['chapter'] for use in m['rows']] for m in mapping] == [[4, 5], [6, 7, 8]]
    assert [m['text'] for m in mapping] == [GUARD, UNCERTAIN]
    for old, row in zip(original['chapter_map'], child['chapter_map']):
        assert row['source_anchor_ids'] == ['source_papers']
        assert row['design_reference_ids'] == [identifier(catalog, GUARD if row['number'] < 6 else UNCERTAIN)]
        assert {k: v for k, v in old.items() if k != 'source_anchor_ids'} == {
            k: v for k, v in row.items() if k not in ('source_anchor_ids', 'design_reference_ids')}
    design.references(child['chapter_map'], catalog)
    assert design.reclassify(child, catalog, SOURCE, seed['source_anchors']) is None


@pytest.mark.parametrize('quote', [
    'The author secretly requires an unrelated confession.',
    GUARD.lower(), GUARD + ' ', GUARD.replace('emotional consent', 'consent'),
])
def test_fake_author_claim_and_near_matches_remain_invalid(candidate, catalog, seed, quote):
    candidate['source_anchors'][1]['quote'] = quote
    before = deepcopy(candidate)
    assert design.reclassify(candidate, catalog, SOURCE, seed['source_anchors']) is None
    assert candidate == before


def test_duplicate_seed_leaf_is_ambiguous_even_when_each_path_has_own_id(candidate, seed):
    seed['opening_investment']['also'] = GUARD
    catalog = design.build_catalog(seed, SHA)
    assert len([e for e in catalog['entries'] if e['text'] == GUARD]) == 2
    assert design.reclassify(candidate, catalog, SOURCE, seed['source_anchors']) is None


@pytest.mark.parametrize('mutation', [
    lambda c: c['source_anchors'][1].update(chapter=4),
    lambda c: c['source_anchors'][1].update(id='source_papers'),
    lambda c: c['source_anchors'][0].update(quote=GUARD),
    lambda c: c['chapter_map'][0].update(source_anchor_ids=['anchor_ch04_guard']),
    lambda c: c['chapter_map'][0].update(other_link='anchor_ch04_guard'),
    lambda c: c['chapter_map'][0].update(source_anchor_ids=['source_papers', 'anchor_ch04_guard', 'unknown']),
    lambda c: c['chapter_map'][0].update(source_anchor_ids=['source_papers', 'anchor_ch04_guard', 'anchor_ch04_guard']),
    lambda c: c['source_anchors'].append({'id': 'unused_bad', 'quote': GUARD}),
    lambda c: c.update(seed_conflicts=[{'message': 'An unrelated unresolved defect'}]),
])
def test_non_pure_or_unaccounted_defects_cannot_receive_a_partial_child(candidate, catalog, seed, mutation):
    mutation(candidate)
    before = deepcopy(candidate)
    assert design.reclassify(candidate, catalog, SOURCE, seed['source_anchors']) is None
    assert candidate == before


def test_genuine_source_quote_is_never_reclassified_when_also_present_in_design(candidate, seed):
    seed['opening_investment']['exact_source_too'] = SOURCE.splitlines()[0]
    catalog = design.build_catalog(seed, SHA)
    child, mapping = design.reclassify(candidate, catalog, SOURCE, seed['source_anchors'])
    assert child['source_anchors'][0] == candidate['source_anchors'][0]
    assert all(m['source_anchor_id'] != 'source_papers' for m in mapping)


def test_source_change_rejects_reclassification_before_mutating_inputs(candidate, catalog, seed):
    with pytest.raises(ValueError, match='different source'):
        design.reclassify(candidate, catalog, SOURCE + 'New source', seed['source_anchors'])
