"""An exact empty generated slot is evidence of missing metadata, never author testimony."""
import pytest

from core.planning_evidence_plan import _check_candidate, _source_quotes
from core.planning_candidate_evidence import bind


@pytest.mark.parametrize('quote,field,valid', [('', '', True), ('', 'he/him', False),
                                             (' ', '', False), (' ', ' ', False),
                                             ('he', 'he/him', True), (None, '', False)])
def test_candidate_empty_quotes_require_an_exact_empty_visible_slot(quote, field, valid):
    errors = []
    candidate = {'characters': [{'pronouns': field}]}
    _check_candidate([{'path':'/characters/0/pronouns', 'quote':quote}], candidate, candidate, 'evidence', errors)
    assert (not errors) is valid


def test_empty_candidate_evidence_cannot_quote_missing_objects_or_chapter_paths():
    for path in ('/characters/0', '/characters/1/pronouns', '/chapters/0/summary'):
        errors = []
        candidate = {'characters': [{'pronouns': ''}]}
        _check_candidate([{'path':path, 'quote':''}], candidate, candidate, 'evidence', errors)
        assert errors


def test_author_source_citations_still_require_nonblank_original_text():
    for quote in ('', ' ', None):
        errors = []
        _source_quotes('The guard opened the gate.', [quote], 'source_quotes', errors)
        assert errors


def test_reassessment_drops_an_empty_citation_after_the_slot_changes_or_disappears():
    reference = {'path':'/characters/0/pronouns', 'quote':''}
    for candidate, expected in [({'characters': [{'pronouns': ''}]}, reference),
                                ({'characters': [{'pronouns': 'he/him'}]}, None), ({}, None)]:
        assert bind(reference, candidate, candidate) == expected
