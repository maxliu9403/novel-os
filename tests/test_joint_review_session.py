"""Durable review responses are processed once without a second paid review."""
from copy import deepcopy
import hashlib
import json

import pytest

from core import chapter_review
from core.joint_review_session import ReviewSession, ReviewSessionStopped


CANDIDATE = 'She returns the key.\n\nHe closes the door.'
VALID = 'Complete saved review with matching evidence.'


class Callbacks:
    def __init__(self, replies=(VALID,)):
        self.replies = iter(replies)
        self.calls, self.prepared, self.verified, self.validated, self.recovered = [], [], [], [], []
        self.dependency = 'frozen-foundation'

    def prepare(self, prompt):
        self.prepared.append(prompt)
        return prompt + '\nCONTEXT', {'foundation': self.dependency, 'user_sha256': hashlib.sha256((prompt + '\nCONTEXT').encode()).hexdigest()}

    def complete(self, user, receipt):
        self.calls.append((user, deepcopy(receipt)))
        response = next(self.replies)
        if isinstance(response, Exception):
            raise response
        return response

    def verify(self, user, receipt):
        self.verified.append((user, deepcopy(receipt)))
        if receipt['foundation'] != self.dependency:
            raise ValueError('Changed foundation')
        assert receipt['user_sha256'] == hashlib.sha256(user.encode()).hexdigest()

    def validate(self, report):
        self.validated.append(report)
        if report == 'Invalid continuity':
            raise ValueError('Continuity block missing')

    def recover(self, user, receipt, raw):
        self.recovered.append((user, deepcopy(receipt), raw))
        return raw if raw in (VALID, 'bad quote') else None

    def session(self, path, **kwargs):
        return ReviewSession(path, kwargs.pop('candidate', CANDIDATE), prepare=self.prepare,
            complete=self.complete, verify=self.verify, validate_report=self.validate,
            recover=kwargs.pop('recover', self.recover), **kwargs)


@pytest.fixture(autouse=True)
def resolver(monkeypatch, request):
    if request.node.name.startswith('test_real_'):
        return
    def resolve(raw, candidate):
        if raw == 'bad quote' or raw == 'bad schema':
            raise ValueError(raw)
        return raw, {'candidate_sha256': hashlib.sha256(candidate.encode()).hexdigest(), 'exact': True}
    monkeypatch.setattr(chapter_review, 'resolve_review', resolve, raising=False)
    monkeypatch.setattr(chapter_review, 'prompt', lambda candidate: 'COMPLETE CANDIDATE\n' + candidate)


def files(path):
    return {p: p.read_bytes() for p in path.rglob('*') if p.is_file()}


def test_success_persists_raw_before_validation_and_replays_without_paid_calls(tmp_path):
    cb = Callbacks()
    assert cb.session(tmp_path).run() == VALID
    before = files(tmp_path)
    assert any(p.name == 'response.raw' and raw == VALID.encode() for p, raw in before.items())
    assert any(p.name == 'binding.json' for p in before)
    assert cb.session(tmp_path).run() == VALID
    assert len(cb.calls) == 1 and len(cb.verified) >= 2
    assert files(tmp_path) == before


def test_one_correction_retains_invalid_initial_and_reuses_both_paid_replies(tmp_path):
    cb = Callbacks(('bad quote', VALID))
    assert cb.session(tmp_path).run() == VALID
    assert len(cb.calls) == 2
    correction = cb.calls[1][0]
    assert 'bad quote' in correction and CANDIDATE in correction
    assert 'unsupported' in correction.lower() and 'revise' in correction.lower()
    assert sorted(p.read_text() for p in tmp_path.rglob('response.raw')) == sorted(['bad quote', VALID])
    before = files(tmp_path)
    assert cb.session(tmp_path).run() == VALID
    assert len(cb.calls) == 2 and files(tmp_path) == before


def test_imported_legacy_result_resumes_without_relying_on_mutable_prompt_alias(tmp_path):
    cb = Callbacks(())
    user, receipt = cb.prepare('Original v1 prompt')
    assert cb.session(tmp_path).adopt(user, receipt, VALID) == VALID
    before = files(tmp_path)
    assert cb.session(tmp_path).run() == VALID
    assert cb.calls == [] and files(tmp_path) == before


def test_provider_received_reply_survives_crash_before_local_response_write(tmp_path):
    cb = Callbacks((RuntimeError('Crash after provider persisted reply'),))
    with pytest.raises(RuntimeError):
        cb.session(tmp_path, recover_received=lambda *args: None).run()
    assert not list(tmp_path.rglob('response.raw'))
    assert cb.session(tmp_path, recover_received=lambda *args: VALID).run() == VALID
    assert len(cb.calls) == 1


def test_imported_legacy_correction_resumes_its_issued_request_without_new_initial(tmp_path):
    cb = Callbacks((RuntimeError('Transport interrupted'), VALID))
    user, receipt = cb.prepare('Original v1 prompt')
    with pytest.raises(RuntimeError, match='Transport interrupted'):
        cb.session(tmp_path).adopt(user, receipt, 'bad quote')
    sent = cb.calls[0]
    assert cb.session(tmp_path).run() == VALID
    assert len(cb.calls) == 2 and cb.calls[1] == sent


def test_imported_legacy_failed_correction_never_gets_new_paid_attempt_on_plain_resume(tmp_path):
    cb = Callbacks(('bad schema',))
    user, receipt = cb.prepare('Original v1 prompt')
    with pytest.raises(ReviewSessionStopped):
        cb.session(tmp_path).adopt(user, receipt, 'bad quote')
    with pytest.raises(ReviewSessionStopped):
        cb.session(tmp_path).run()
    assert len(cb.calls) == 1


def test_invalid_correction_stops_durably_and_never_gets_a_third_paid_try(tmp_path):
    cb = Callbacks(('bad quote', 'bad schema'))
    for _ in range(2):
        with pytest.raises(ReviewSessionStopped):
            cb.session(tmp_path).run()
    assert len(cb.calls) == 2
    assert list(tmp_path.rglob('stopped.json'))
    assert sorted(p.read_text() for p in tmp_path.rglob('response.raw')) == ['bad quote', 'bad schema']


def test_full_continuity_validation_precedes_success_and_can_request_one_correction(tmp_path):
    cb = Callbacks(('Invalid continuity', VALID))
    assert cb.session(tmp_path).run() == VALID
    assert cb.validated == ['Invalid continuity', VALID]
    assert len(cb.calls) == 2


def test_candidate_changes_use_a_new_namespace_and_never_reuse_old_response(tmp_path):
    cb = Callbacks((VALID, VALID))
    cb.session(tmp_path).run()
    before = files(tmp_path)
    cb.session(tmp_path, candidate=CANDIDATE + '\nNew exact paragraph.').run()
    assert len(cb.calls) == 2
    assert all(p.read_bytes() == value for p, value in before.items())


def test_complete_prepared_request_changes_create_a_separate_bound_session(tmp_path):
    cb = Callbacks((VALID, VALID))
    cb.session(tmp_path).run()
    cb.dependency = 'author-amended-foundation'
    cb.session(tmp_path).run()
    assert len(cb.calls) == 2


def test_dependency_verification_runs_before_reusing_paid_response(tmp_path):
    cb = Callbacks()
    cb.session(tmp_path).run()
    cb.verify = lambda *args: (_ for _ in ()).throw(ValueError('Changed dependency'))
    with pytest.raises(ValueError, match='Changed dependency'):
        cb.session(tmp_path).run()
    assert len(cb.calls) == 1


@pytest.mark.parametrize('target', ['response.raw', 'response.json', 'binding.json', 'derived.txt', 'request.json'])
def test_corrupt_bound_artifacts_never_become_fresh_paid_attempts(tmp_path, target):
    cb = Callbacks()
    cb.session(tmp_path).run()
    path = next(tmp_path.rglob(target))
    path.write_bytes(path.read_bytes() + b' changed')
    with pytest.raises((ReviewSessionStopped, ValueError)):
        cb.session(tmp_path).run()
    assert len(cb.calls) == 1


def test_orphan_received_raw_requires_provider_receipt_recovery(tmp_path):
    cb = Callbacks()
    cb.session(tmp_path).run()
    next(tmp_path.rglob('response.json')).unlink()
    assert cb.session(tmp_path).run() == VALID
    assert len(cb.calls) == 1 and len(cb.recovered) == 1


@pytest.mark.parametrize('recovery', [None, lambda *args: None, lambda *args: 'different response'])
def test_unproven_orphan_raw_blocks_without_model_or_semantic_calls(tmp_path, recovery):
    cb = Callbacks()
    cb.session(tmp_path).run()
    next(tmp_path.rglob('response.json')).unlink()
    validated = len(cb.validated)
    with pytest.raises(ReviewSessionStopped):
        cb.session(tmp_path, recover=recovery).run()
    assert len(cb.calls) == 1 and len(cb.validated) == validated


def test_response_receipt_without_raw_cannot_regenerate_paid_work(tmp_path):
    cb = Callbacks()
    cb.session(tmp_path).run()
    next(tmp_path.rglob('response.raw')).unlink()
    with pytest.raises(ReviewSessionStopped):
        cb.session(tmp_path).run()
    assert len(cb.calls) == 1


def test_trusted_legacy_adoption_resolves_without_a_new_model_and_keeps_original_prompt(tmp_path):
    cb = Callbacks(())
    user, receipt = cb.prepare('HISTORICAL V1 PROMPT ' + CANDIDATE)
    session = cb.session(tmp_path)
    assert session.run(legacy=(user, receipt, VALID)) == VALID
    assert not cb.calls
    assert next(tmp_path.rglob('prompt.txt')).read_text() == user
    assert session.run(legacy=(user, receipt, VALID)) == VALID
    assert not cb.calls


def test_legacy_bad_quote_gets_only_one_supported_full_review_correction(tmp_path):
    cb = Callbacks((VALID,))
    user, receipt = cb.prepare('HISTORICAL V1 PROMPT ' + CANDIDATE)
    assert cb.session(tmp_path).run(legacy=(user, receipt, 'bad quote')) == VALID
    assert len(cb.calls) == 1
    assert cb.session(tmp_path).run(legacy=(user, receipt, 'bad quote')) == VALID
    assert len(cb.calls) == 1


def test_transport_failure_does_not_get_misclassified_as_protocol_correction(tmp_path):
    cb = Callbacks((TimeoutError('transport timeout'),))
    with pytest.raises(TimeoutError, match='transport timeout'):
        cb.session(tmp_path).run()
    assert len(cb.calls) == 1 and not list(tmp_path.rglob('stopped.json'))


def test_large_candidate_and_original_reply_are_never_truncated(tmp_path):
    candidate = CANDIDATE + '\n' + '完整原文。' * 60000
    cb = Callbacks(('bad quote', VALID))
    cb.session(tmp_path, candidate=candidate).run()
    assert all(candidate in user for user, _ in cb.calls)


def test_completed_correction_stays_authoritative_after_a_codec_upgrade(tmp_path, monkeypatch):
    cb = Callbacks(('bad quote', 'A corrected revise decision'))
    assert cb.session(tmp_path).run() == 'A corrected revise decision'
    monkeypatch.setattr(chapter_review, 'resolve_review', lambda raw, candidate: (raw, {
        'candidate_sha256': hashlib.sha256(candidate.encode()).hexdigest(), 'exact': True}))
    assert cb.session(tmp_path).run() == 'A corrected revise decision'
    assert len(cb.calls) == 2


def test_concurrent_same_request_only_calls_the_provider_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    cb = Callbacks()
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: cb.session(tmp_path).run(), range(2)))
    assert results == [VALID, VALID] and len(cb.calls) == 1


def test_crash_between_raw_and_receipt_recovers_provider_evidence_without_resending(tmp_path, monkeypatch):
    cb = Callbacks()
    session = cb.session(tmp_path)
    save = session._record
    def crash(path, value):
        if path.name == 'response.json':
            raise OSError('Interrupted after raw was saved')
        return save(path, value)
    monkeypatch.setattr(session, '_record', crash)
    with pytest.raises(OSError, match='after raw'):
        session.run()
    assert next(tmp_path.rglob('response.raw')).read_text() == VALID
    assert cb.session(tmp_path).run() == VALID
    assert len(cb.calls) == len(cb.recovered) == 1


def test_real_v2_codec_keeps_wire_reply_and_continuity_unchanged(tmp_path):
    from test_review_evidence import payload, wrap
    raw = wrap(payload(CANDIDATE, v2=True))
    cb = Callbacks((raw,))
    cb.validate = lambda response: chapter_review.validate_report(response, CANDIDATE)
    assert cb.session(tmp_path).run() == raw
    before = files(tmp_path)
    assert cb.session(tmp_path).run() == raw
    assert len(cb.calls) == 1 and files(tmp_path) == before
    binding = json.loads(next(tmp_path.rglob('binding.json')).read_text())['binding']
    assert binding['protocol'] == 'joint-editorial.v2'
    assert binding['raw_response_sha256'] == binding['derived_response_sha256']


def test_real_legacy_quote_boundary_repair_is_free_and_preserves_judgment_and_state(tmp_path):
    from test_review_evidence import payload, wrap, extract
    candidate = 'She paused. He returned the key.” Then he left.'
    raw = wrap(payload(candidate, quote='“He returned the key.”'))
    cb = Callbacks(())
    cb.validate = lambda response: chapter_review.validate_report(response, candidate)
    user, receipt = cb.prepare(chapter_review.legacy_prompt(candidate))
    derived = cb.session(tmp_path, candidate=candidate).run(legacy=(user, receipt, raw))
    assert not cb.calls
    assert derived.endswith(raw[raw.index('[/JOINT_REVIEW_JSON]'):])
    for old, new in zip(extract(raw)['checks'], extract(derived)['checks'], strict=True):
        assert new == {**old, 'quote': 'He returned the key.”'}
    assert next(tmp_path.rglob('response.raw')).read_text() == raw
    assert cb.session(tmp_path, candidate=candidate).run(legacy=(user, receipt, raw)) == derived
    assert not cb.calls


def test_real_unsupported_old_judgment_can_only_recover_through_supported_revise(tmp_path):
    from test_review_evidence import payload, wrap
    old = wrap(payload(CANDIDATE, quote='An event that never appears.'))
    correction = payload(CANDIDATE, v2=True)
    correction['checks'][0].update(status='revise', reason='The claimed event is absent.',
                                    repair='Resolve the missing consequence in the existing scene.')
    fixed = wrap(correction).replace('Status: PASS', 'Status: FAIL')
    cb = Callbacks((fixed,))
    cb.validate = lambda response: chapter_review.validate_report(response, CANDIDATE)
    user, receipt = cb.prepare(chapter_review.legacy_prompt(CANDIDATE))
    result = cb.session(tmp_path).run(legacy=(user, receipt, old))
    assert result == fixed and not chapter_review.read_review(result, CANDIDATE).passed
    assert len(cb.calls) == 1
    assert cb.session(tmp_path).run(legacy=(user, receipt, old)) == result
    assert len(cb.calls) == 1
