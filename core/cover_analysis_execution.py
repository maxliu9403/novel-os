"""Bounded model execution; only the coordinator owns durable analysis state."""
from __future__ import annotations

import json
import threading
from collections import deque
from contextvars import copy_context
from functools import partial
from core.usage_tracking import usage_request_identity
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping


@dataclass(frozen=True)
class AnalysisCall:
    key: str
    phase: str
    request: dict
    validate: Callable[[Mapping[str, Any]], Any]


def execute_calls(calls: Iterable[AnalysisCall], *, max_workers: int, max_repairs: int,
                  complete: Callable, complete_factory: Callable | None,
                  begin: Callable, finish: Callable, cancel_event: Any,
                  cancellation_error: Callable, is_terminal_error: Callable):
    """Yield validated results while serially recording all dispatched attempts.

    In-flight results are drained and retained after failure/cancellation. No
    repair or new work starts after a terminal failure, and executor shutdown
    finishes before the caller releases its analysis lock. Model clients are
    isolated per thread when a factory is supplied.
    """
    queued = deque((call, 0, call.request) for call in calls)
    pending = {}
    local = threading.local()
    failure = None

    def invoke(call, system, user, request_id=None):
        raw = None
        try:
            if not hasattr(local, "complete"):
                local.complete = complete_factory() if complete_factory else complete
            with usage_request_identity(request_id):
                raw = local.complete(system, user)
            decoded = dict(raw) if isinstance(raw, Mapping) else json.loads(raw)
            if not isinstance(decoded, dict):
                raise ValueError("Model response must be one JSON object")
            return raw, call.validate(decoded), None
        except Exception as exc:
            return raw, None, exc

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="cover-analysis") as pool:
        while queued or pending:
            if failure is None and cancel_event is not None and cancel_event.is_set():
                failure = cancellation_error()
            # Yielding lets the coordinator persist a result. Other workers
            # may finish (including terminal failures) during that write; reap
            # those futures before replenishing a newly free concurrency slot.
            while (failure is None and queued and len(pending) < max_workers
                   and not any(future.done() for future in pending)):
                if cancel_event is not None and cancel_event.is_set():
                    failure = cancellation_error()
                    break
                call, attempt, request = queued.popleft()
                try:
                    record, system, user = begin(call, request)
                    future = pool.submit(partial(copy_context().run, invoke), call, system, user, record.get("attempt_id"))
                    pending[future] = (call, attempt, request, record)
                except Exception as exc:
                    failure = exc
            if not pending:
                break
            done, _ = wait(pending, timeout=.1, return_when=FIRST_COMPLETED)
            successes, retries = [], []
            # Process the entire completion batch before dispatching anything:
            # a simultaneous failure must prevent a successful sibling from
            # causing more model requests to be sent.
            for future in done:
                call, attempt, request, record = pending.pop(future)
                raw, result, error = future.result()
                finish(record, raw, error)
                if error is None:
                    successes.append((call.key, result))
                elif is_terminal_error(error) or attempt >= max_repairs:
                    failure = failure or error
                else:
                    retries.append((call, attempt + 1,
                                    {**request, "repair": {"error": str(error), "previous_response": raw}}))
            if failure is None and cancel_event is not None and cancel_event.is_set():
                failure = cancellation_error()
            if failure is None:
                queued.extendleft(reversed(retries))
            for value in successes:
                yield value
        if failure is not None:
            raise failure
