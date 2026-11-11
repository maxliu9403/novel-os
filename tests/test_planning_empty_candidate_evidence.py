"""An exact empty generated slot is evidence of missing metadata, never author testimony."""
import pytest

from core.planning_evidence_plan import _check_candidate, _source_quotes, matches_candidate_evidence


@pytest.mark.parametrize('quote,field,valid', [('', '', True), ('', 'he/him', False),
                                             (' ', '', False), (' ', ' ', False),
                                             ('he', 'he/him', True), (None, '', False)])
def test_candidate_empty_quotes_require_an_exact_empty_visible_slot(quote, field, valid):
    errors = []
    _check_candidate([{'path':'/characters/0/pronouns', 'quote':quote}],
                     {'/characters/0/pronouns':field}, 'evidence', errors)
    assert (not errors) is valid


def test_empty_candidate_evidence_cannot_quote_missing_objects_or_chapter_paths():
    for path in ('/characters/0', '/characters/1/pronouns', '/chapters/0/summary'):
        errors = []
        _check_candidate([{'path':path, 'quote':''}], {'/characters/0/pronouns':''}, 'evidence', errors)
        assert errors


def test_author_source_citations_still_require_nonblank_original_text():
    for quote in ('', ' ', None):
        errors = []
        _source_quotes('The guard opened the gate.', [quote], 'source_quotes', errors)
        assert errors


def test_reassessment_drops_an_empty_citation_after_the_slot_changes_or_disappears():
    reference = {'path':'/characters/0/pronouns', 'quote':''}
    assert matches_candidate_evidence(reference, {reference['path']:''})
    assert not matches_candidate_evidence(reference, {reference['path']:'he/him'})
    assert not matches_candidate_evidence(reference, {})
