"""Request outcome evidence shared by source design and its portable handoff.

A returned HTTP error is distinct from a response whose outcome is unknown.
Classify structured exceptions (including adapter wrappers), never their prose.
This does not assert that a failed remote request incurred no provider charges.
"""
from __future__ import annotations

import socket


def classify_failure(error: BaseException) -> dict:
    chain = []
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        pending.extend(cause for cause in (current.__cause__, current.__context__)
                       if isinstance(cause, BaseException))

    evidence = {'error_type': type(error).__name__}
    for current in chain:
        if type(current).__name__ == 'LLMInputLimitError':
            return {**evidence, 'outcome': 'not_sent', 'retryable': False,
                    'reason': 'local_capacity_preflight'}
        response = getattr(current, 'response', None)
        statuses = (getattr(current, 'status_code', None),
                    getattr(response, 'status_code', None), getattr(current, 'code', None))
        for status in statuses:
            if type(status) is int and 400 <= status <= 599:
                return {**evidence, 'outcome': 'failed', 'http_status': status,
                        'retryable': status in (408, 425, 429) or status >= 500,
                        'reason': 'returned_http_error'}
    if any(isinstance(current, (ConnectionRefusedError, socket.gaierror)) for current in chain):
        return {**evidence, 'outcome': 'not_sent', 'retryable': True,
                'reason': 'connection_not_established'}
    return {**evidence, 'outcome': 'unknown', 'retryable': False,
            'reason': 'no_verified_terminal_response'}
