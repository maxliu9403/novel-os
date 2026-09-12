"""Complete paired blind exports and externally supplied English-editor judgments."""
import json
import math
import statistics
import zipfile
from io import BytesIO
from typing import Literal

from pydantic import Field
from narrative_methods.models import canonical, digest, text_sha

from .models import Record

METRICS = ('naturalness', 'distinct_voices', 'causality', 'local_value', 'read_on_intent')


class Finding(Record):
    side: Literal['left', 'right']
    chapter: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=4000)
    category: Literal['pov', 'causality', 'required_event', 'fact']
    explanation: str = Field(min_length=1, max_length=2400)


class PairReview(Record):
    pair_id: str
    preferred: Literal['left', 'right', 'tie', 'uncertain']
    scores: dict[str, dict[str, int]]
    note: str = Field(min_length=1, max_length=2400)
    critical_findings: list[Finding] = Field(max_length=20)


class Sheet(Record):
    schema_version: Literal['novel-method-blind-review.v1']
    blind_sha256: str
    reviewer_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    reviewer_role: Literal['english_editor']
    pairs: list[PairReview] = Field(min_length=1, max_length=9)


def blind_bundle(plan, records):
    if any(not r or r['status'] == 'response_saved' for r in records.values()):
        raise ValueError('complete all planned attempts before exporting; no selective partial windows')
    pairs, contexts = [], []
    files = {}
    for pair in plan['pairs']:
        case = next(c for c in plan['specification']['cases'] if c['id'] == pair['case_id'])
        contexts.append({'pair_id': pair['pair_id'], **{key: case[key] for key in (
            'kind', 'free_trial_end', 'volume_boundary_after', 'language', 'audience', 'facts', 'prior_context', 'chapters',
        )}})
        samples = {}
        for side in ('left', 'right'):
            chapters = []
            for chapter in case['chapters']:
                record = records[f"{pair['pair_id']}-{pair[side]}-{chapter['number']}"]
                chapters.append({'number': chapter['number'], 'status': record['status'],
                                 'text': record.get('prose', ''), 'text_sha256': text_sha(record.get('prose', '')),
                                 'word_count': record.get('word_count'),
                                 'within_word_range': record.get('within_word_range')})
            samples[side] = chapters
            body = '\n\n'.join(f"## Chapter {c['number']}\n\n" + (
                c['text'] if c['status'] == 'completed' else '[Chapter unavailable; retain this sample in the evaluation.]'
            ) for c in chapters)
            files[f"samples/{pair['pair_id']}/{side}.md"] = body.encode('utf-8')
        pairs.append({'pair_id': pair['pair_id'], 'samples': samples})
    data = {'pairs': pairs, 'contexts': contexts}
    data['blind_sha256'] = digest(data)
    template = {'schema_version': 'novel-method-blind-review.v1', 'blind_sha256': data['blind_sha256'],
                'reviewer_id': '', 'reviewer_role': 'english_editor', 'pairs': [
                    {'pair_id': p['pair_id'], 'preferred': None, 'note': '', 'critical_findings': [],
                     'scores': {side: {metric: None for metric in METRICS} for side in ('left', 'right')}}
                    for p in pairs]}
    files['context.json'] = canonical(contexts)
    files['samples.json'] = canonical(data)
    files['review-template.json'] = canonical(template)
    files['README.md'] = (
        '# Independent English editorial review\n\n'
        'Read both complete samples and their frozen facts/outlines. Work independently; use a pseudonymous reviewer ID. '
        'First check required events, facts, POV and causal integrity. Score each dimension 1 (weak) to 5 (strong). '
        'Preserve uncertainty and disagreements. Do not infer sales or payment conversion. '
        'Missing chapters and out-of-range word counts are visible defects, not reasons to omit a pair. '
        'Report critical errors with an exact quote and Unicode code-point [start,end) offsets into '
        'the chapter text in samples.json, not the added Markdown headings. Return the completed JSON template. '
        'The experiment coordinator retains the private arm mapping; this bundle intentionally omits it.\n'
    ).encode('utf-8')
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, content)
    return data, buffer.getvalue()


def assess(plan, records, blind, sheets):
    if len(sheets) != 2:
        raise ValueError('exactly two distinct independent English-editor sheets required')
    checked = [Sheet.model_validate(s).model_dump() for s in sheets]
    if len({s['reviewer_id'].casefold() for s in checked}) != 2:
        raise ValueError('two distinct reviewer IDs required')
    current, _ = blind_bundle(plan, records)
    if current != blind:
        raise ValueError('blind source changed')
    ids = {p['pair_id'] for p in blind['pairs']}
    for sheet in checked:
        if sheet['blind_sha256'] != blind['blind_sha256']:
            raise ValueError('review uses a different blind snapshot')
        if len(sheet['pairs']) != len(ids) or {r['pair_id'] for r in sheet['pairs']} != ids:
            raise ValueError('review must cover every pair exactly once')
        for row in sheet['pairs']:
            if set(row['scores']) != {'left', 'right'} or any(
                set(scores) != set(METRICS) or any(type(v) is not int or not 1 <= v <= 5 for v in scores.values())
                for scores in row['scores'].values()
            ):
                raise ValueError('all editorial scores must be integers from 1 to 5')
            sample = next(p for p in blind['pairs'] if p['pair_id'] == row['pair_id'])
            for finding in row['critical_findings']:
                chapter = next((c for c in sample['samples'][finding['side']] if c['number'] == finding['chapter']), None)
                if not chapter or not 0 <= finding['start'] < finding['end'] <= len(chapter['text']):
                    raise ValueError('critical evidence has invalid chapter/offsets')
                if chapter['text'][finding['start']:finding['end']] != finding['quote']:
                    raise ValueError('critical evidence is not an exact source quote')
    wins, disagreements = 0, 0
    critical = {'A': 0, 'B': 0}
    pair_results = []
    for pair in plan['pairs']:
        votes = []
        for sheet in checked:
            row = next(r for r in sheet['pairs'] if r['pair_id'] == pair['pair_id'])
            choice = row['preferred']
            votes.append(pair[choice] if choice in {'left', 'right'} else choice)
            for finding in row['critical_findings']:
                critical[pair[finding['side']]] += 1
        wins += votes == ['B', 'B']
        disagreements += votes[0] != votes[1]
        pair_results.append({'pair_id': pair['pair_id'], 'editor_votes': votes})
    complete = all(r and r['status'] == 'completed' and r['within_word_range'] for r in records.values())
    spec = plan['specification']
    standard = (spec['repetitions'] == 3 and len(plan['pairs']) == 9 and {
        (c['kind'], c['free_trial_end']) for c in spec['cases']
    } == {('free_trial_window', 3), ('free_trial_window', 4), ('volume_transition', None)})
    timings = {}
    for arm in ('A', 'B'):
        values = sorted(records[t['id']]['elapsed_seconds'] for t in plan['tasks']
                        if t['arm'] == arm and records[t['id']] and 'elapsed_seconds' in records[t['id']])
        timings[arm] = {'count': len(values), 'median_seconds': statistics.median(values) if values else None,
                        'p95_seconds': values[max(0, math.ceil(len(values) * .95) - 1)] if values else None}
    result = {'blind_sha256': blind['blind_sha256'], 'pair_count': len(ids), 'pair_results': pair_results,
              'reviewers': [s['reviewer_id'] for s in checked], 'evidence_source': 'externally_supplied_editor_sheets',
              'enhanced_consensus_wins': wins, 'disagreement_pairs': disagreements, 'critical_findings_by_arm': critical,
              'complete_in_range_samples': complete, 'standard_pilot_scope': standard,
              'editorial_pilot_signal': bool(standard and complete and wins >= 6 and critical['B'] == 0),
              'timing_scope': 'scribe_complete_call_only_not_pipeline', 'timings': timings,
              'transport_attempts': None, 'tokens': None, 'cost': None,
              'production_release_ready': False,
              'pending': ['target_reader_assessment', 'full_pipeline_quality_and_cost', 'H5_importer_acceptance', 'P2_approval']}
    return result, checked
