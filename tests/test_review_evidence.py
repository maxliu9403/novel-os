"""References locate unchanged prose without rewriting editorial judgments."""
from copy import deepcopy
import hashlib
import json
import re

import pytest

from core.chapter_review import DIMENSIONS, legacy_prompt, prompt, read_review, resolve_review, validate_report
from core.review_evidence import build_catalog


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def wrap(payload):
    return ('Unchanged preface — with  double spacing.\n[JOINT_REVIEW_JSON]\n'
            + json.dumps(payload, ensure_ascii=False, indent=2)
            + '\n[/JOINT_REVIEW_JSON]\n[CONTINUITY_REPORT]\nStatus: PASS\n[/CONTINUITY_REPORT]\n'
            + '[CONTINUITY_STATE_UPDATE]\nKey_Events: [The key remained — unresolved.]\n[/CONTINUITY_STATE_UPDATE]\n')


def payload(text, *, quote=None, v2=False):
    value = {'candidate_sha256': sha(text), 'checks': [
        {'dimension': d, 'status': 'pass', 'reason': f'Judgment about {d} — unchanged.', 'repair': ''}
        for d in sorted(DIMENSIONS)]}
    if v2:
        catalog = build_catalog(text)
        value.update(schema_version='joint-editorial.v2', catalog_sha256=catalog['catalog_sha256'])
        for check in value['checks']:
            check['evidence_ids'] = [catalog['entries'][0]['id']]
    else:
        for check in value['checks']:
            check['quote'] = text if quote is None else quote
    return value


def extract(text):
    return json.loads(re.search(r'\[JOINT_REVIEW_JSON\]\s*(.*?)\s*\[/JOINT_REVIEW_JSON\]', text, re.S)[1])


def test_catalog_is_deterministic_lossless_and_bound_to_complete_candidate():
    text = '\n\n第一段：保留“引文”。\r\n\r\nSecond  paragraph — intact.\n\n  \nLast line.\n'
    catalog = build_catalog(text)
    assert catalog == build_catalog(text)
    assert catalog['candidate_sha256'] == sha(text)
    assert ''.join(e['text'] for e in catalog['entries']) == text
    previous = 0
    for entry in catalog['entries']:
        assert entry['start'] == previous
        assert text[entry['start']:entry['end']] == entry['text']
        previous = entry['end']
    assert previous == len(text)
    assert len({e['id'] for e in catalog['entries']}) == len(catalog['entries'])
    assert build_catalog(text + '!')['catalog_sha256'] != catalog['catalog_sha256']
    assert catalog['offset_unit'] == 'unicode_codepoints'


def test_new_prompt_supplies_each_complete_passage_once_and_only_requests_ids():
    text = 'Unique first sentence.\n\nUnique second sentence.'
    request = prompt(text)
    for line in text.split('\n\n'):
        assert request.count(line) == 1
    assert 'joint-editorial.v2' in request
    assert 'evidence_ids' in request
    assert '"quote":' not in request
    assert build_catalog(text)['catalog_sha256'] in request
    assert 'JOINT EDITORIAL REVIEW joint-editorial.v1' in legacy_prompt(text)
    assert legacy_prompt(text).endswith(text + '\n')


def test_v2_resolves_multiple_nonadjacent_paragraphs_without_changing_wire_reply():
    text = 'First evidence.\n\nMiddle unrelated passage.\n\nLast evidence.'
    value = payload(text, v2=True)
    catalog = build_catalog(text)
    value['checks'][0]['evidence_ids'] = [catalog['entries'][0]['id'], catalog['entries'][2]['id']]
    raw = wrap(value)
    derived, receipt = resolve_review(raw, text)
    assert derived == raw
    review = read_review(raw, text)
    assert review.passed
    assert 'First evidence.' in review.checks[0].quote
    assert 'Last evidence.' in review.checks[0].quote
    assert 'Middle unrelated' not in review.checks[0].quote
    spans = receipt['bindings'][0]['spans']
    assert len(spans) == 2
    assert [text[s['start']:s['end']] for s in spans] == [catalog['entries'][i]['text'] for i in (0, 2)]
    assert receipt['raw_response_sha256'] == receipt['derived_response_sha256'] == sha(raw)
    assert receipt['candidate_sha256'] == sha(text)
    assert receipt['changes'] == []


@pytest.mark.parametrize('fault', ['candidate', 'catalog', 'unknown_id', 'duplicate_id', 'empty_ids',
                                    'quote_and_ids', 'extra_root', 'missing_dimension', 'empty_repair', 'wrong_version'])
def test_v2_rejects_unbound_or_mixed_evidence_and_invalid_judgments(fault):
    text = 'The source remains exact.'
    value = payload(text, v2=True)
    if fault == 'candidate': value['candidate_sha256'] = '0' * 64
    elif fault == 'catalog': value['catalog_sha256'] = '0' * 64
    elif fault == 'unknown_id': value['checks'][0]['evidence_ids'] = ['invented']
    elif fault == 'duplicate_id': value['checks'][0]['evidence_ids'] *= 2
    elif fault == 'empty_ids': value['checks'][0]['evidence_ids'] = []
    elif fault == 'quote_and_ids': value['checks'][0]['quote'] = text
    elif fault == 'extra_root': value['invented'] = True
    elif fault == 'missing_dimension': value['checks'][0]['dimension'] = value['checks'][1]['dimension']
    elif fault == 'empty_repair': value['checks'][0].update(status='revise', repair='  ')
    else: value['schema_version'] = 'joint-editorial.v9'
    with pytest.raises(ValueError):
        resolve_review(wrap(value), text)


@pytest.mark.parametrize('quote,expected', [
    ('“He put down the key.”', 'He put down the key.”'),
    ('“She closed the door.”', '“She closed the door.'),
    ('“The window remained open.”', 'The window remained open.'),
])
def test_legacy_recovery_only_removes_extra_outer_display_quotes(quote, expected):
    text = 'Intro. ' + expected + ' More exact context.'
    value = payload(text, quote=quote)
    value['checks'][0].update(status='revise', repair='Preserve the admitted uncertainty.')
    raw = wrap(value)
    derived, receipt = resolve_review(raw, text)
    actual = extract(derived)
    for old, new in zip(value['checks'], actual['checks'], strict=True):
        assert new == {**old, 'quote': expected}
    start, stop = raw.index('[JOINT_REVIEW_JSON]'), raw.index('[/JOINT_REVIEW_JSON]')
    assert derived[:start] == raw[:start]
    assert derived[derived.index('[/JOINT_REVIEW_JSON]'):] == raw[stop:]
    assert receipt['raw_response_sha256'] == sha(raw)
    assert receipt['derived_response_sha256'] == sha(derived)
    assert all(c['before'] == quote and c['after'] == expected for c in receipt['changes'])
    assert not read_review(derived, text).passed
    assert read_review(raw, text).checks[0].quote == expected
    for binding in receipt['bindings']:
        span, = binding['spans']
        assert text[span['start']:span['end']] == expected


@pytest.mark.parametrize('text,quote', [
    ('The key was returned.', 'The key is returned.'),
    ('One  space stays.', 'One space stays.'),
    ('First evidence. Intervening words. Last evidence.', 'First evidence. Last evidence.'),
    ('A repeated line. A repeated line.', '“A repeated line.”'),
    ('The key was returned.', '““The key was returned.””'),
])
def test_legacy_does_not_guess_words_whitespace_spans_or_ambiguous_references(text, quote):
    with pytest.raises(ValueError, match='引用'):
        resolve_review(wrap(payload(text, quote=quote)), text)


def test_valid_legacy_reply_is_byte_unchanged_even_with_repeated_exact_quote():
    text = 'The key. The key.'
    raw = wrap(payload(text, quote='The key.'))
    derived, receipt = resolve_review(raw, text)
    assert derived == raw
    assert len(receipt['bindings'][0]['spans']) == 2
    assert receipt['changes'] == []


@pytest.mark.parametrize('fault', ['candidate', 'dimensions', 'repair'])
def test_review_identity_and_judgment_validation_precedes_citation_recovery(fault):
    text = 'Exact words.'
    value = payload(text, quote='An invented quote.')
    if fault == 'candidate': value['candidate_sha256'] = '0' * 64
    elif fault == 'dimensions': value['checks'][0]['dimension'] = value['checks'][1]['dimension']
    else: value['checks'][0].update(status='revise', repair='')
    with pytest.raises(ValueError) as error:
        resolve_review(wrap(value), text)
    assert '正文引用不匹配' not in str(error.value)


def test_duplicate_json_properties_and_duplicate_review_blocks_are_rejected():
    text = 'Exact words.'
    raw = wrap(payload(text))
    with pytest.raises(ValueError):
        read_review(raw + raw, text)
    duplicate = raw.replace('"status": "pass"', '"status": "revise", "status": "pass"', 1)
    with pytest.raises(ValueError):
        read_review(duplicate, text)


@pytest.mark.parametrize('text,expected', [
    ('', 'd778f6e1ec161e80a5d9aa5b0d53b6bd7eca7becba846f20315ff94b6ae99460'),
    ('She returned the key.\n', 'b26b01ae8d73c1768aaa037c51bdac7f7332cae196c92ea39fb33d33207f5b7a'),
    ('中文：保留全部。\n\nA second paragraph.', '35d40ddbe627670ca630712db2d9daa609656fc76a9d19ae36eca146e163c93c'),
])
def test_issued_v1_prompt_is_byte_identical_to_pre_upgrade_policy(text, expected):
    assert sha(legacy_prompt(text)) == expected


@pytest.mark.parametrize('v2', [False, True])
def test_complete_report_validation_preserves_machine_text_and_accepts_crlf(v2):
    text = 'The key remained — unresolved.\n\nEvery  space matters.'
    raw = wrap(payload(text, v2=v2)).replace('\n', '\r\n')
    # JSON string escapes are unaffected by transport CRLF outside the strings.
    assert validate_report(raw, text).passed
    derived, _ = resolve_review(raw, text)
    assert derived == raw


@pytest.mark.parametrize('fault', ['missing_report', 'duplicate_report', 'missing_state', 'duplicate_state',
                                    'empty_state', 'duplicate_status', 'contradiction', 'status_only_in_reason',
                                    'extra_status_words', 'unclosed_report'])
def test_complete_report_rejects_ambiguous_or_inconsistent_state_adoption(fault):
    text = 'The key remained.'
    value = payload(text, v2=True)
    value['checks'][0]['reason'] = 'Status: PASS is only a quoted observation, not the report status.'
    raw = wrap(value)
    if fault == 'missing_report':
        raw = re.sub(r'\[CONTINUITY_REPORT\].*?\[/CONTINUITY_REPORT\]', '', raw, flags=re.S)
    elif fault == 'duplicate_report':
        raw += '[CONTINUITY_REPORT]\nStatus: PASS\n[/CONTINUITY_REPORT]'
    elif fault == 'missing_state':
        raw = re.sub(r'\[CONTINUITY_STATE_UPDATE\].*?\[/CONTINUITY_STATE_UPDATE\]', '', raw, flags=re.S)
    elif fault == 'duplicate_state':
        raw += '[CONTINUITY_STATE_UPDATE]New information[/CONTINUITY_STATE_UPDATE]'
    elif fault == 'empty_state':
        raw = re.sub(r'\[CONTINUITY_STATE_UPDATE\].*?\[/CONTINUITY_STATE_UPDATE\]',
                     '[CONTINUITY_STATE_UPDATE] \n [/CONTINUITY_STATE_UPDATE]', raw, flags=re.S)
    elif fault == 'duplicate_status':
        raw = raw.replace('\nStatus: PASS\n', '\nStatus: PASS\nStatus: PASS\n')
    elif fault == 'contradiction':
        raw = raw.replace('\nStatus: PASS\n', '\nStatus: FAIL\n')
    elif fault == 'status_only_in_reason':
        raw = raw.replace('\nStatus: PASS\n', '\nNo status was declared.\n')
    elif fault == 'extra_status_words':
        raw = raw.replace('\nStatus: PASS\n', '\nStatus: PASS although some failures remain\n')
    else:
        raw = raw.replace('[/CONTINUITY_REPORT]', '')
    assert read_review(raw, text).passed  # The lightweight reader remains compatible.
    with pytest.raises(ValueError):
        validate_report(raw, text)


def test_actual_revise_judgment_is_preserved_and_requires_fail_report():
    text = 'A clear passage.'
    value = payload(text, v2=True)
    value['checks'][0].update(status='revise', repair='Clarify the character’s actual knowledge.')
    raw = wrap(value).replace('\nStatus: PASS\n', '\nStatus: FAIL\n')
    assert not validate_report(raw, text).passed
    derived, receipt = resolve_review(raw, text)
    assert derived == raw and receipt['changes'] == []


def test_empty_candidate_has_no_fabricated_reference_catalog():
    with pytest.raises(ValueError):
        build_catalog(' \r\n\t')


@pytest.mark.parametrize('tag', ['CONTINUITY_REPORT', 'CONTINUITY_STATE_UPDATE'])
def test_quoted_machine_markers_inside_review_json_cannot_be_adopted(tag):
    text = 'A clear passage.'
    value = payload(text)
    value['checks'][0]['reason'] = f'[{tag}]Status: PASS[/{tag}]'
    raw = wrap(value)
    # Remove only the actual external block; retain the JSON-quoted impostor.
    actual_start = raw.index('[/JOINT_REVIEW_JSON]') + len('[/JOINT_REVIEW_JSON]')
    tail = re.sub(re.escape(f'[{tag}]') + r'.*?' + re.escape(f'[/{tag}]'), '', raw[actual_start:], flags=re.S)
    raw = raw[:actual_start] + tail
    with pytest.raises(ValueError, match='separate'):
        validate_report(raw, text)


def test_machine_state_update_cannot_be_nested_in_continuity_report():
    text = 'A clear passage.'
    raw = wrap(payload(text))
    raw = raw.replace('[/CONTINUITY_REPORT]\n', '').replace('[/CONTINUITY_STATE_UPDATE]',
                    '[/CONTINUITY_STATE_UPDATE]\n[/CONTINUITY_REPORT]')
    with pytest.raises(ValueError, match='separate'):
        validate_report(raw, text)
