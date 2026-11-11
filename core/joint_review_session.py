"""Durable, source-bound joint review processing; never writes prose or canon.

Callbacks own transport and dependency verification. This module owns immutable
request/response receipts and at most one complete protocol correction. A received
response is reused before requesting another review, including after a local crash.
"""
from __future__ import annotations

from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

from core import chapter_review

VERSION = 'joint-review-session.v1'


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _sha(value):
    return hashlib.sha256(value.encode('utf-8') if isinstance(value, str) else value).hexdigest()


class ReviewSessionStopped(ValueError):
    """Saved review work cannot be advanced under its verified request contract."""

    def __init__(self, reason, *, receipt_path=None):
        super().__init__(reason)
        self.reason = reason
        self.receipt_path = str(receipt_path) if receipt_path is not None else None


class ReviewSession:
    def __init__(self, directory, candidate, *, prepare, complete, verify, validate_report, recover=None, recover_received=None):
        if not isinstance(candidate, str) or not candidate.strip():
            raise ValueError('Joint review requires the complete nonempty candidate')
        self.directory = Path(directory).resolve()
        self.candidate = candidate
        self.candidate_sha256 = _sha(candidate)
        self.prepare, self.complete, self.verify = prepare, complete, verify
        self.validate_report, self.recover = validate_report, recover
        self.recover_received = recover_received

    def _path(self, path):
        if path.is_symlink() or not path.resolve().is_relative_to(self.directory):
            raise ReviewSessionStopped('Review session artifact path is unsafe', receipt_path=path)
        return path

    def _read(self, path):
        path = self._path(path)
        return path.read_bytes() if path.exists() else None

    def _json(self, path):
        raw = self._read(path)
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ReviewSessionStopped('Saved review receipt is malformed', receipt_path=path) from exc
        if not isinstance(value, dict):
            raise ReviewSessionStopped('Saved review receipt is not an object', receipt_path=path)
        return value

    def _put(self, path, raw):
        """Store once using an atomic link; never replace a paid or derived artifact."""
        self._path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        prior = self._read(path)
        if prior is not None:
            if prior != raw:
                raise ReviewSessionStopped('Saved review artifact failed integrity verification', receipt_path=path)
            return
        fd, temporary = tempfile.mkstemp(prefix='.review-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if self._read(path) != raw:
                    raise ReviewSessionStopped('Concurrent review artifact differs', receipt_path=path)
        finally:
            os.unlink(temporary)

    def _record(self, path, value):
        self._put(path, _encoded(value))

    def _verified(self, user, receipt):
        if not isinstance(user, str) or not isinstance(receipt, dict):
            raise ValueError('Prepared review request requires a string and receipt object')
        if self.verify(user, deepcopy(receipt)) is False:
            raise ReviewSessionStopped('Prepared review dependencies could not be verified')

    def _identity(self, user, receipt):
        return {'schema_version': VERSION, 'candidate_sha256': self.candidate_sha256,
                'user_sha256': _sha(user), 'prepared_receipt_sha256': _sha(_encoded(receipt))}

    def _request(self, directory, user, receipt):
        self._verified(user, receipt)
        identity = self._identity(user, receipt)
        request_id = _sha(_encoded(identity))
        request = {**identity, 'request_id': request_id}
        self._record(directory / 'request.json', request)
        self._put(directory / 'prompt.txt', user.encode('utf-8'))
        self._record(directory / 'prepared.json', receipt)
        return request

    def _saved_request(self, directory, expected_id):
        request = self._json(directory / 'request.json')
        prepared = self._json(directory / 'prepared.json')
        user = self._read(directory / 'prompt.txt')
        if request is None or prepared is None or user is None:
            raise ReviewSessionStopped('Saved correction request is incomplete', receipt_path=directory)
        try:
            user = user.decode('utf-8')
        except UnicodeError as exc:
            raise ReviewSessionStopped('Saved review request is not UTF-8', receipt_path=directory) from exc
        expected = self._identity(user, prepared)
        if request != {**expected, 'request_id': expected_id} or _sha(_encoded(expected)) != expected_id:
            raise ReviewSessionStopped('Saved correction request failed integrity verification', receipt_path=directory)
        self._verified(user, prepared)
        return user, prepared, request

    def _response(self, directory, request, user, prepared, *, adopted=None):
        """Bind response bytes before parsing. Only provider recovery proves orphans."""
        self._verified(user, prepared)
        path = directory / 'response.raw'
        raw = self._read(path)
        record = self._json(directory / 'response.json')
        if record is not None:
            if raw is None or record != {
                'schema_version': VERSION, 'request_id': request['request_id'],
                'candidate_sha256': self.candidate_sha256, 'response_sha256': _sha(raw),
            }:
                raise ReviewSessionStopped('Saved response failed request or content verification', receipt_path=path)
            if adopted is not None and raw != adopted.encode('utf-8'):
                raise ReviewSessionStopped('Legacy response differs from the saved received response', receipt_path=path)
        elif raw is not None:
            try:
                orphan = raw.decode('utf-8')
            except UnicodeError as exc:
                raise ReviewSessionStopped('Saved response is not UTF-8', receipt_path=path) from exc
            verified = self.recover(user, deepcopy(prepared), orphan) if self.recover is not None else None
            if verified != orphan:
                raise ReviewSessionStopped('Received response has no verified provider receipt; preserved without resending', receipt_path=path)
        else:
            # Transport exceptions remain transport exceptions. The existing runtime
            # decides whether a transport attempt can resume; no protocol retry hides it.
            received = adopted
            if adopted is None:
                dispatch_path = directory / 'dispatch.json'
                dispatched = self._json(dispatch_path)
                dispatch = {'schema_version': VERSION, 'request_id': request['request_id'],
                            'candidate_sha256': self.candidate_sha256}
                if dispatched is not None and dispatched != dispatch:
                    raise ReviewSessionStopped('Saved dispatch differs from the prepared request', receipt_path=dispatch_path)
                if dispatched is not None and self.recover_received is not None:
                    received = self.recover_received(user, deepcopy(prepared))
                self._record(dispatch_path, dispatch)
                if received is None:
                    received = self.complete(user, deepcopy(prepared))
            if not isinstance(received, str):
                raise ReviewSessionStopped('Review transport did not return complete text', receipt_path=path)
            raw = received.encode('utf-8')
            self._put(path, raw)
        # Complete raw bytes and their hash are durable before local validation.
        self._record(directory / 'response.json', {
            'schema_version': VERSION, 'request_id': request['request_id'],
            'candidate_sha256': self.candidate_sha256, 'response_sha256': _sha(raw),
        })
        self._verified(user, prepared)
        return raw.decode('utf-8')

    def _resolve(self, directory, request, raw):
        derived, binding = chapter_review.resolve_review(raw, self.candidate)
        if self.validate_report(derived) is False:
            raise ValueError('Joint review continuity protocol was not validated')
        record = {'schema_version': VERSION, 'request_id': request['request_id'],
                  'candidate_sha256': self.candidate_sha256, 'response_sha256': _sha(raw),
                  'derived_sha256': _sha(derived), 'binding_sha256': _sha(_encoded(binding)), 'binding': binding}
        self._put(directory / 'derived.txt', derived.encode('utf-8'))
        self._record(directory / 'binding.json', record)
        return derived

    def _correction(self, root, raw, error):
        pointer = self._json(root / 'correction.json')
        if pointer is not None:
            if (set(pointer) != {'request_id', 'initial_response_sha256'}
                    or pointer['initial_response_sha256'] != _sha(raw)
                    or not isinstance(pointer['request_id'], str)
                    or len(pointer['request_id']) != 64
                    or any(c not in '0123456789abcdef' for c in pointer['request_id'])):
                raise ReviewSessionStopped('Saved review correction binding is invalid', receipt_path=root)
            directory = root / ('correction-' + pointer['request_id'])
            user, prepared, request = self._saved_request(directory, pointer['request_id'])
            return directory, user, prepared, request
        prompt = (chapter_review.prompt(self.candidate) + '\n\nPROTOCOL RECOVERY — ONE COMPLETE REPLACEMENT REVIEW\n'
            'The following saved response failed local validation. Read the complete unchanged candidate above '
            'and correct its review protocol using the supplied evidence IDs. Preserve valid judgments and '
            'their reasons. Do not manufacture supporting evidence or change the manuscript to obtain PASS. '
            'If a previous judgment is unsupported by the candidate, return revise with an accurate explanation '
            'and repair. An unsupported judgment must never remain PASS. Recheck the continuity report and '
            'state update against actual events in this exact candidate. Return the complete review and '
            'continuity blocks under the current protocol. This is the only protocol correction.\n'
            + json.dumps({'local_error': str(error), 'saved_response': raw}, ensure_ascii=False))
        user, prepared = self.prepare(prompt)
        identity = self._identity(user, prepared)
        request_id = _sha(_encoded(identity))
        directory = root / ('correction-' + request_id)
        request = self._request(directory, user, prepared)
        self._record(root / 'correction.json', {'request_id': request_id, 'initial_response_sha256': _sha(raw)})
        return directory, user, prepared, request

    def adopt(self, user, receipt, raw):
        """Import a legacy response whose provider provenance the caller verified.

        ``verify`` still checks current dependencies. The caller must establish
        raw-response provenance with the original runtime recovery mechanism first.
        No new prompt is substituted for the paid historical request.
        """
        return self.run(legacy=(user, receipt, raw))

    def _legacy_lineage(self):
        """Find the verified imported request without consulting mutable aliases.

        A correction may replace the readable prompt file. That must not detach
        a subsequent resume from the original received reply and its correction.
        """
        found = []
        candidate_root = self.directory / self.candidate_sha256
        for path in sorted(candidate_root.glob('*/legacy-import.json')):
            marker = self._json(path)
            root = path.parent
            request_id = root.name
            if (len(request_id) != 64 or any(c not in '0123456789abcdef' for c in request_id)
                    or marker.get('request_id') != request_id
                    or marker.get('candidate_sha256') != self.candidate_sha256):
                raise ReviewSessionStopped('Legacy review lineage failed integrity verification', receipt_path=path)
            try:
                user, prepared, _ = self._saved_request(root / 'initial', request_id)
            except ReviewSessionStopped:
                raise
            except ValueError:
                # An explicitly changed source/context needs a new review. It
                # cannot reuse this historical request merely because prose matches.
                continue
            raw = self._read(root / 'initial/response.raw')
            if raw is None or marker != {
                'request_id': request_id, 'candidate_sha256': self.candidate_sha256,
                'response_sha256': _sha(raw), 'provenance': 'caller-verified-provider-receipt',
            }:
                raise ReviewSessionStopped('Legacy response lineage is incomplete or changed', receipt_path=path)
            found.append((root, user, prepared))
        if len(found) > 1:
            raise ReviewSessionStopped('Multiple applicable legacy review lineages require reconciliation')
        return found[0] if found else None

    def run(self, *, legacy=None):
        existing = self._legacy_lineage() if legacy is None else None
        if existing is not None:
            root, user, prepared = existing
            adopted = None
        elif legacy is None:
            user, prepared = self.prepare(chapter_review.prompt(self.candidate))
            adopted = None
        else:
            user, prepared, adopted = legacy
            if not isinstance(adopted, str):
                raise ValueError('Trusted legacy response must be complete text')
        if existing is None:
            identity = self._identity(user, prepared)
            root = self.directory / self.candidate_sha256 / _sha(_encoded(identity))
        self._path(root)
        root.mkdir(parents=True, exist_ok=True)
        lock = self._path(root / '.lock')
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            return self._run_bound(root, user, prepared, adopted)
        finally:
            os.close(descriptor)

    def _run_bound(self, root, user, prepared, adopted):
        initial = root / 'initial'
        request = self._request(initial, user, prepared)
        if adopted is not None:
            self._record(root / 'legacy-import.json', {'request_id': request['request_id'],
                'candidate_sha256': self.candidate_sha256, 'response_sha256': _sha(adopted),
                'provenance': 'caller-verified-provider-receipt'})
        raw = self._response(initial, request, user, prepared, adopted=adopted)
        if self._read(root / 'correction.json') is not None:
            # Once a later review was requested, it remains authoritative. A
            # parser upgrade cannot resurrect an earlier PASS over its revision.
            directory, user, prepared, request = self._correction(root, raw, None)
        else:
            try:
                return self._resolve(initial, request, raw)
            except ReviewSessionStopped:
                raise
            except ValueError as error:
                # A completed paid reply is never regenerated. The correction has its
                # own immutable request and is reused under that exact historical view.
                directory, user, prepared, request = self._correction(root, raw, error)
        corrected = self._response(directory, request, user, prepared)
        try:
            return self._resolve(directory, request, corrected)
        except ReviewSessionStopped:
            raise
        except ValueError as error:
            receipt = root / 'stopped.json'
            record = {'schema_version': VERSION, 'candidate_sha256': self.candidate_sha256,
                      'initial_response_sha256': _sha(raw), 'correction_request_id': request['request_id'],
                      'correction_response_sha256': _sha(corrected), 'reason': str(error)}
            self._record(receipt, record)
            raise ReviewSessionStopped('Joint review protocol correction still requires attention; complete responses are preserved: '
                                       + str(error), receipt_path=receipt) from error
