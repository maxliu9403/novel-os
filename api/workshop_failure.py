"""Read-only failure advice, including bounded interpretation of old traces."""
import json
import os
import stat
from pathlib import Path

_TIMEOUTS = {'attempt.timeout', 'request.timeout', 'cli.timeout'}
_TERMINALS = _TIMEOUTS | {'attempt.failed', 'attempt.completed', 'attempt.cancelled',
                        'request.failed', 'request.completed', 'request.cancelled'}


def _terminal(directory_fd, name, progress):
    event = progress.get('terminal_event')
    if isinstance(event, str) and event in _TERMINALS:
        return event
    # Legacy progress omitted terminal kind. Inspect only a bounded private
    # trace tail, never infer timeout from arbitrary user-facing error prose.
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                return None
            start = max(0, info.st_size - 65536)
            lines = os.pread(fd, 65536, start).splitlines()
            if start:
                lines = lines[1:]
            for line in reversed(lines):
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(row, dict) or any(row.get(k) != progress.get(k)
                        for k in ('session_id', 'job_id', 'revision', 'operation', 'request_id', 'attempt')):
                    continue
                if isinstance(row.get('event'), str) and row['event'] in _TERMINALS:
                    return row['event']
        finally:
            os.close(fd)
    except OSError:
        pass
    return None


def _no_content(progress):
    # Absence of an observer is unknown. Do not claim no activity merely
    # because an older adapter did not collect it.
    return (progress.get('activity_time_basis') == 'batch_observed'
            and not any(progress.get(k) for k in ('content_chars', 'cumulative_content_chars',
                'reasoning_event_count', 'text_event_count', 'last_turn_content_at', 'last_completed_attempt')))


def failure_summary(session: dict, directory: Path, current: dict):
    if session.get('status') != 'error' or not current or current.get('phase') != 'failed':
        return None
    from .workshop_progress import _read
    operation, revision = current.get('operation'), current.get('revision')
    if not isinstance(operation, str) or operation not in {'brainstorm', 'titles'} or type(revision) is not int:
        return None
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        terminal = _terminal(fd, f'{revision}-{operation}.jsonl', current)
        if terminal not in _TIMEOUTS:
            return None
        if not _no_content(current):
            return {'kind': 'timeout', 'consecutive_count': 1}
        count = 1
        # At most five consecutive attempts; gaps, a changed input boundary,
        # another operation or any observed content stop the streak.
        boundary = session.get('context_start_revision', 0)
        boundary = boundary if type(boundary) is int else 0
        for prior_revision in range(revision - 1, max(boundary, revision - 5), -1):
            p = _read(fd, f'{prior_revision}-{operation}.progress.json')
            if (p.get('session_id') != session.get('id') or p.get('revision') != prior_revision
                    or p.get('operation') != operation or not p.get('job_id') or p.get('phase') != 'failed'
                    or not _no_content(p) or _terminal(fd, f'{prior_revision}-{operation}.jsonl', p) not in _TIMEOUTS):
                break
            count += 1
        return {'kind': 'no_content_timeout', 'consecutive_count': count}
    finally:
        os.close(fd)
