"""Full-book execution limits and metadata-only, durable observations.

The context is shared by package and CLI imports. Cached LLM clients bind it
at call time; neither a heartbeat nor transport metadata proves model work.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

sys.modules.setdefault('pipeline_runtime' if __name__ == 'core.pipeline_runtime' else 'core.pipeline_runtime', sys.modules[__name__])
_current = ContextVar('pipeline_runtime', default=None)


def _now():
    return datetime.now(timezone.utc).isoformat()


class PipelineStopped(RuntimeError):
    """A bounded execution stopped; resume requires an explicit user action."""


def current_runtime():
    return _current.get()


def _seconds(name, default):
    value = float(os.environ.get(name, default))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} must be a positive finite number')
    return value


def is_execution_stop(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (PipelineStopped, TimeoutError, subprocess.TimeoutExpired)) or type(exc).__name__ in {'LLMCancelledError', 'APITimeoutError', 'ReadTimeout', 'ConnectTimeout'}:
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def check_cancelled():
    runtime = current_runtime()
    if runtime is not None:
        runtime.check()


@contextmanager
def runtime_stage(phase, chapter=None, *, bounded=True):
    runtime = current_runtime()
    if runtime is None:
        yield
    else:
        with runtime.stage(phase, chapter, bounded=bounded):
            yield


class PipelineRuntime:
    def __init__(self, project, run_id, *, cancellation_event=None, request_timeout=None, stage_timeout=None):
        self.path = Path(project) / 'outputs' / 'runs' / run_id / 'progress.json'
        self.cancel = cancellation_event if cancellation_event is not None else threading.Event()
        self.request_timeout = request_timeout if request_timeout is not None else _seconds('NOVEL_OS_PIPELINE_REQUEST_TIMEOUT_SECONDS', 900)
        self.stage_timeout = stage_timeout if stage_timeout is not None else _seconds('NOVEL_OS_PIPELINE_STAGE_TIMEOUT_SECONDS', 3600)
        self._lock = threading.RLock()
        self._closed = False
        self._stop = threading.Event()
        self._deadline = None
        self._stop_reason = None
        self._stage_depth = 0
        self._token = None
        now = _now()
        self.data = dict(schema_version=1, execution_id=uuid.uuid4().hex, run_id=run_id,
                         status='running', phase='', chapter=None, started_at=now, updated_at=now,
                         phase_started_at=None, heartbeat_at=now, last_upstream_activity_at=None,
                         last_model_activity_at=None, last_output_at=None,
                         request_timeout_seconds=self.request_timeout, stage_timeout_seconds=self.stage_timeout,
                         request=None, milestones=[], message='正在准备创作阶段。', next_action='等待阶段结果；可随时请求暂停。')

    def __enter__(self):
        self._token = _current.set(self)
        try:
            self._save()
        except BaseException:
            self._closed = True
            _current.reset(self._token)
            raise
        threading.Thread(target=self._heartbeat, daemon=True, name='pipeline-progress').start()
        return self

    def __exit__(self, typ, exc, tb):
        try:
            with self._lock:
                if self.data['status'] == 'running':
                    self.data['status'] = 'paused' if self._stop_reason or is_execution_stop(exc) else ('failed' if exc else 'completed')
                self._save()
        finally:
            self._closed = True
            self._stop.set()
            _current.reset(self._token)

    def finish(self, status):
        with self._lock:
            self.data['status'] = status
            self.data['next_action'] = '可从已验证的检查点恢复。' if status in {'failed', 'paused'} else ''
            self._save()

    def check(self):
        if self._stop_reason:
            raise PipelineStopped(self._stop_reason)
        if self.cancel.is_set():
            self._stop_reason = 'cancelled: 已暂停模型调用，保留已验证检查点；不会自动重试。'
            raise PipelineStopped(self._stop_reason)
        if self._deadline is not None and time.monotonic() >= self._deadline:
            self._stop_reason = 'stage_timeout: 当前阶段已达到时间预算，保留已验证检查点；不会自动重试。'
            raise PipelineStopped(self._stop_reason)

    def timeout(self):
        self.check()
        return min(self.request_timeout, max(.001, self._deadline - time.monotonic())) if self._deadline else self.request_timeout

    def _save(self):
        if self._closed:
            return
        self.data['updated_at'] = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f'.progress-{self.data["execution_id"]}.tmp')
        temporary.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.path)

    def _heartbeat(self):
        while not self._stop.wait(5):
            with self._lock:
                if self._closed:
                    return
                self.data['heartbeat_at'] = _now()
                self._save()

    @contextmanager
    def stage(self, phase, chapter, *, bounded=True):
        self.check()
        previous = (self._deadline, self.data['phase'], self.data['chapter'], self.data['phase_started_at'])
        self._stage_depth += 1
        deadline = time.monotonic() + self.stage_timeout if bounded else None
        self._deadline = min(previous[0], deadline) if previous[0] and deadline else (previous[0] or deadline)
        with self._lock:
            self.data.update(phase=phase, chapter=chapter, phase_started_at=_now(), request=None,
                             last_upstream_activity_at=None, last_model_activity_at=None, last_output_at=None,
                             message='正在执行当前阶段；结果通过校验后保存。')
            self._milestone('running', '开始当前阶段。')
        try:
            yield
            self.check()
        except BaseException as exc:
            with self._lock:
                if is_execution_stop(exc):
                    self.data['status'] = 'paused'
                self._milestone('paused' if is_execution_stop(exc) else 'failed', '阶段停止，已验证结果保留。')
            raise
        else:
            with self._lock:
                self._milestone('done', '当前阶段已完成。')
        finally:
            self._deadline = previous[0]
            self._stage_depth -= 1
            # Nested planning scopes restore their enclosing phase, while the
            # last top-level stage remains visible after completion.
            if self._stage_depth:
                self.data.update(phase=previous[1], chapter=previous[2], phase_started_at=previous[3])

    def _milestone(self, status, message):
        self.data['milestones'] = (self.data['milestones'] + [dict(at=_now(), phase=self.data['phase'],
            chapter=self.data['chapter'], status=status, message=message)])[-80:]
        self._save()

    def diagnostic(self, record):
        with self._lock:
            if self._closed:
                return
            event, now = record.get('event'), _now()
            request = self.data['request']
            if event == 'request.start':
                self.data.update(last_upstream_activity_at=None, last_model_activity_at=None, last_output_at=None,
                                 message='等待本次模型回复；后台检查与上游信号不等于内容输出。')
                request = self.data['request'] = dict(request_id=record.get('request_id'),
                    provider=record.get('provider'), model=record.get('model'), started_at=now,
                    status='running', output_chars=0, model_activity_count=0, error_type=None,
                    timeout_seconds=record.get('timeout_seconds', self.request_timeout))
            elif request and record.get('request_id') and record['request_id'] != request.get('request_id'):
                return
            if request and request['status'] != 'running' and event != 'request.start':
                return
            if event == 'upstream.event':
                self.data['last_upstream_activity_at'] = now
            if request:
                chars = record.get('model_output_chars', record.get('output_chars'))
                if isinstance(chars, (int, float)) and not isinstance(chars, bool) and chars > request['output_chars']:
                    request['output_chars'] = min(int(chars), 10**9)
                    self.data['last_model_activity_at'] = now
                if event == 'upstream.activity' and record.get('activity_kind') in {'text', 'reasoning_summary'}:
                    count = record.get('metric_count')
                    if isinstance(count, (int, float)) and count > 0:
                        request['model_activity_count'] += min(int(count), 10**9)
                        self.data['last_model_activity_at'] = now
                if event in {'request.completed', 'request.failed', 'request.timeout', 'request.cancelled'}:
                    request['status'] = event.split('.')[1]
                    request['error_type'] = record.get('error_type')
                    if event == 'request.completed':
                        self.data['last_output_at'] = now
                        self.data['message'] = '已收到完整模型回复，正在校验并保存阶段结果。'
            self.data['heartbeat_at'] = now
            if event in {'request.start', 'request.completed', 'request.failed', 'request.timeout', 'request.cancelled'}:
                # Preserve per-request evidence across later stages/resumes,
                # without storing prompts, response text or private thinking.
                entry = dict(at=now, event=event, execution_id=self.data['execution_id'],
                             run_id=self.data['run_id'], phase=self.data['phase'], chapter=self.data['chapter'],
                             request=request)
                with self.path.with_name('requests.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
            self._save()


@contextmanager
def client_scope(client):
    """Bind the current run on each call, including already cached clients."""
    runtime = current_runtime()
    if runtime is None:
        yield
        return
    runtime.check()
    old = {key: getattr(client, key, None) for key in ('cancellation_event', 'diagnostic_callback', 'timeout_seconds', '_backend')}
    configured = getattr(client, '_configured_timeout_seconds', old['timeout_seconds'])
    timeout = min(configured, runtime.timeout()) if configured else runtime.timeout()
    active = True
    def diagnostic(record):
        if active:
            runtime.diagnostic(record)
            if old['diagnostic_callback']:
                old['diagnostic_callback'](record)
    try:
        client.cancellation_event = runtime.cancel
        client.timeout_seconds = timeout
        client.diagnostic_callback = diagnostic
        if callable(getattr(old['_backend'], 'with_options', None)):
            # Official HTTP SDKs can cache their own timeout/retry settings.
            client._backend = old['_backend'].with_options(timeout=timeout, max_retries=0)
        yield
        runtime.check()
    except BaseException as exc:
        if is_execution_stop(exc):
            # Some advisory callers catch ordinary model failures. A timeout
            # must remain sticky so they cannot issue another paid call or
            # commit a late result after swallowing this exception.
            runtime._stop_reason = f'request_stopped: {type(exc).__name__}; 保留已验证检查点，不会自动重试。'
            if not isinstance(exc, PipelineStopped):
                raise PipelineStopped(runtime._stop_reason) from exc
        raise
    finally:
        active = False
        for key, value in old.items():
            setattr(client, key, value)
