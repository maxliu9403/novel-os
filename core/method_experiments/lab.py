"""Offline plans and explicitly budgeted, non-production Scribe experiments."""
import random
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from narrative_methods import MethodPolicy, NarrativeMethods
from narrative_methods.models import digest, text_sha

from .models import Specification
from .prompts import enhanced_system
from .prompts import chapter_user
from .store import ExperimentStore


def implementation_sha():
    root = Path(__file__).parent.parent
    files = list(Path(__file__).parent.glob('*.py')) + [root / name for name in (
        'prose_sanitize.py', 'compile_book.py', 'model_router.py', 'llm_client.py', 'provider_settings.py',
        'narrative_methods/models.py', 'narrative_methods/compiler.py',
    )]
    return digest({str(p.relative_to(root)): text_sha(p.read_text(encoding='utf-8')) for p in sorted(files)})


def _prose(raw):
    # Strip metadata only: never normalize punctuation or repair the experiment text.
    from prose_sanitize import extract_html_comments, strip_state_blocks
    if not isinstance(raw, str) or len(raw) > 100000:
        raise ValueError('response_budget')
    if raw.count('[SCRIBE_STATE_UPDATE]') != 1 or not raw.rstrip().endswith('[/SCRIBE_STATE_UPDATE]'):
        raise ValueError('invalid_scribe_output_contract')
    text = strip_state_blocks(extract_html_comments(raw)[0]).strip()
    if not text or '```' in text or 'STORY_LEAD:' in text:
        raise ValueError('invalid_scribe_prose')
    return text


def _word_count(text):
    from compile_book import parse_chapter
    return sum(len(b.text.split()) for b in parse_chapter(text)
               if b.kind not in {'chapter_title', 'scene_break', 'story_lead', 'story_lead_title'})


class ExperimentLab:
    def __init__(self, root: Path, *, client_factory=None):
        self.store = ExperimentStore(root)
        self.client_factory = client_factory

    def plan(self, specification: dict, *, baseline_system: str) -> dict:
        spec = Specification.model_validate(specification).model_dump()
        if not baseline_system.strip() or len(baseline_system) > 32000:
            raise ValueError("invalid baseline system prompt")
        assets = NarrativeMethods().assets(MethodPolicy())
        rules = {key: value for pack in assets.values() for key, value in pack['rules'].items()}
        variants = {}
        for case in spec['cases']:
            if any(rule not in rules for rule in case['rules']):
                raise ValueError("unknown experiment method rule")
            variants[case['id']] = enhanced_system(baseline_system, {r: rules[r] for r in case['rules']})
        inputs = {'specification': spec, 'systems': {'A': baseline_system, 'B': variants},
                  'method_assets': assets, 'implementation_sha256': implementation_sha()}
        with self.store.locked(initialize=True):
            previous = self.store.read('plan.json')
            if previous:
                if previous['input_sha256'] != digest(inputs):
                    raise ValueError("experiment plan is frozen; use a new empty directory")
                return previous
            rng = random.Random(spec['seed'])
            tasks, pairs = [], []
            for case in spec['cases']:
                for repetition in range(1, spec['repetitions'] + 1):
                    pair_id = secrets.token_hex(12)
                    arms = rng.sample(['A', 'B'], 2)
                    pairs.append({'pair_id': pair_id, 'case_id': case['id'], 'repetition': repetition,
                                  'left': arms[0], 'right': arms[1]})
                    for arm in arms:
                        for chapter in case['chapters']:
                            tasks.append({'id': f"{pair_id}-{arm}-{chapter['number']}", 'pair_id': pair_id,
                                          'arm': arm, 'case_id': case['id'], 'chapter': chapter['number']})
            return self.store.write('plan.json', {
                **inputs, 'input_sha256': digest(inputs), 'tasks': tasks, 'pairs': pairs,
                'planned_windows': len(pairs) * 2, 'planned_model_calls': len(tasks),
                'experiment_scope': 'isolated_scribe_prompt_probe_not_full_pipeline',
                'seed_scope': 'execution_and_blind_order_only_not_model_determinism',
            })

    def _plan(self):
        plan = self.store.read('plan.json')
        if not plan:
            raise ValueError('experiment has not been planned')
        if plan['implementation_sha256'] != implementation_sha():
            raise ValueError('experiment implementation changed; use the frozen code or a new plan')
        return plan

    def _records(self, plan):
        records = {}
        for task in plan['tasks']:
            record = self.store.read(f"calls/{task['id']}.json")
            if record and (record.get('task_id') != task['id'] or record.get('plan_input_sha256') != plan['input_sha256']):
                raise ValueError('experiment call belongs to a different task or plan')
            records[task['id']] = record
        return records

    def status(self):
        plan = self._plan()
        records = self._records(plan)
        available = [r for r in records.values() if r]
        reserved = sum(r['status'] == 'reserved' for r in available)
        confirmed = sum(r.get('model_called', False) for r in available)
        remaining = len(records) - len(available)
        statuses = {r['status'] for r in available}
        return {
            'state': ('pending' if remaining else 'complete' if statuses <= {'completed'} else 'completed_with_issues'),
            'planned_model_calls': len(records), 'confirmed_model_calls': confirmed,
            'uncertain_model_calls': reserved, 'remaining_chapters': remaining,
            'transport_attempts': None, 'tokens': None, 'cost': None,
            'results': [{'task_id': key, 'status': value['status'] if value else 'pending',
                         'word_count': value.get('word_count') if value else None,
                         'within_word_range': value.get('within_word_range') if value else None,
                         'error_code': value.get('error_code', '') if value else ''}
                        for key, value in records.items()],
        }

    def run(self, *, max_model_calls: int, allow_model_calls=False):
        if allow_model_calls is not True:
            raise ValueError('explicit model-call consent required')
        if type(max_model_calls) is not int or max_model_calls < 1:
            raise ValueError('model-call cap must be a positive integer')
        with self.store.locked():
            plan = self._plan()
            if max_model_calls > plan['planned_model_calls']:
                raise ValueError('model-call cap exceeds the frozen plan')
            spec = plan['specification']
            self.store.write(f'approvals/{max_model_calls}.json', {
                'input_sha256': plan['input_sha256'], 'max_model_calls': max_model_calls,
                'permission': 'explicit_complete_calls_not_money_or_transport_budget',
            })
            records = self._records(plan)
            consumed = sum(bool(r and (r.get('model_called') or r['status'] == 'reserved')) for r in records.values())
            client = None
            for task in plan['tasks']:
                relative = f"calls/{task['id']}.json"
                case = next(c for c in spec['cases'] if c['id'] == task['case_id'])
                chapter = next(c for c in case['chapters'] if c['number'] == task['chapter'])
                if records[task['id']] and records[task['id']]['status'] == 'response_saved':
                    records[task['id']] = self.store.write(relative, self._finish(records[task['id']], chapter), immutable=False)
                if records[task['id']]:
                    continue  # including failed, invalid and uncertain reservations
                parents = [t for t in plan['tasks'] if t['pair_id'] == task['pair_id'] and t['arm'] == task['arm']
                           and t['chapter'] < task['chapter']]
                previous = [records[t['id']] for t in parents]
                binding = {'task_id': task['id'], 'plan_input_sha256': plan['input_sha256']}
                if any(not r or r['status'] != 'completed' for r in previous):
                    records[task['id']] = self.store.write(relative, {**binding, 'status': 'blocked', 'model_called': False,
                                                                    'error_code': 'prior_chapter_not_complete'})
                    continue
                system = plan['systems']['A'] if task['arm'] == 'A' else plan['systems']['B'][case['id']]
                user = chapter_user(case, chapter, [{'number': t['chapter'], 'text': r['prose']}
                                                   for t, r in zip(parents, previous)])
                if len(system) + len(user) > spec['input_max_chars']:
                    records[task['id']] = self.store.write(relative, {**binding, 'status': 'incomplete', 'model_called': False,
                                                                    'error_code': 'full_input_budget'})
                    continue
                if consumed >= max_model_calls:
                    return {**self.status(), 'state': 'budget_exhausted'}
                if client is None:
                    from model_router import ModelRouter
                    try:
                        client = (self.client_factory(spec['model']) if self.client_factory
                                  else ModelRouter.client_from_snapshot(spec['model']))
                    except Exception:
                        raise ValueError('frozen experiment connection is unavailable; inspect connection settings') from None
                    actual = (client.snapshot if self.client_factory else ModelRouter.public_snapshot(client))
                    if actual != spec['model']:
                        raise ValueError('experiment model configuration differs from frozen plan')
                record = {**binding, 'status': 'reserved', 'model_called': False, 'system': system, 'user': user,
                          'input_sha256': digest({'system': system, 'user': user, 'model': spec['model']}),
                          'started_at': datetime.now(timezone.utc).isoformat()}
                self.store.write(relative, record)
                consumed += 1
                started = time.monotonic()
                try:
                    raw = client.complete(system, user)
                except Exception as exc:
                    # Do not persist headers, credentials or provider response bodies.
                    error = 'timeout' if isinstance(exc, TimeoutError) else 'model_request_failed'
                    record.update(status='failed', model_called=True, error_code=error)
                else:
                    if not isinstance(raw, str) or len(raw) > 100000:
                        record.update(status='invalid', model_called=True, error_code='response_budget')
                        if isinstance(raw, str):
                            raw_path = f"responses/{task['id']}.txt"
                            self.store.write_bytes(raw_path, raw.encode('utf-8'))
                            record.update(raw_response_path=raw_path, raw_response_sha256=text_sha(raw),
                                          raw_response_chars=len(raw))
                    else:
                        record.update(status='response_saved', raw_response=raw, raw_response_sha256=text_sha(raw), model_called=True)
                record['elapsed_seconds'] = time.monotonic() - started
                # Durable raw response BEFORE interpretation. Recovery must not regenerate it.
                self.store.write(relative, record, immutable=False)
                if record['status'] == 'response_saved':
                    record = self._finish(record, chapter)
                    self.store.write(relative, record, immutable=False)
                records[task['id']] = record
            return self.status()

    @staticmethod
    def _finish(record, chapter):
        try:
            prose = _prose(record['raw_response'])
        except ValueError as exc:
            return {**record, 'status': 'invalid', 'error_code': str(exc)}
        words = _word_count(prose)
        return {**record, 'status': 'completed', 'prose': prose, 'prose_sha256': text_sha(prose),
                'word_count': words, 'within_word_range': chapter['min_words'] <= words <= chapter['max_words'],
                'semantic_validity': 'not_evaluated'}

    def export_blind(self):
        from .review import blind_bundle
        with self.store.locked():
            plan = self._plan()
            data, raw = blind_bundle(plan, self._records(plan))
            self.store.write('blind.json', data)
            self.store.write_bytes('blind-review.zip', raw)
            import hashlib
            return {'path': str(self.store.path('blind-review.zip')), 'sha256': hashlib.sha256(raw).hexdigest(),
                    'blind_sha256': data['blind_sha256'], 'pairs': len(data['pairs'])}

    def assess(self, sheets: list[dict]):
        from .review import assess
        with self.store.locked():
            plan = self._plan()
            blind = self.store.read('blind.json')
            if not blind:
                raise ValueError('export a blind bundle before assessment')
            result, checked = assess(plan, self._records(plan), blind, sheets)
            for sheet in checked:
                self.store.write(f"reviews/{digest(sheet)}.json", sheet)
            return self.store.write(f"assessments/{digest(checked)}.json", result)
