"""Request outcome evidence shared by source design and its portable handoff.

A returned HTTP error is distinct from a response whose outcome is unknown.
Classify structured exceptions (including adapter wrappers), never their prose.
This does not assert that a failed remote request incurred no provider charges.
"""
from __future__ import annotations

import socket
import subprocess


def _timeout_seconds(value):
    return value if type(value) in (int, float) and 0 <= value <= 2**63 - 1 else None


def _request_timeout(error):
    if isinstance(error, TimeoutError):
        return True
    # SDK timeout hierarchies do not inherit Python's TimeoutError. Inspect
    # their structured types without importing every optional provider SDK.
    return any(cls.__name__ in {"TimeoutException", "APITimeoutError", "Timeout", "DeadlineExceeded"}
               and cls.__module__.split(".")[0] in {"httpx", "httpcore", "openai", "anthropic", "requests", "google"}
               for cls in type(error).__mro__)


def failure_message(failure: dict | None) -> str:
    """Render only verified fields; old exception prose is never safe UI content."""
    failure = failure if isinstance(failure, dict) else {}
    outcome = failure.get('outcome')
    status = failure.get('http_status')
    if outcome == 'failed' and type(status) is int and 400 <= status <= 599:
        if status == 402 and failure.get('provider_code') == 'deactivated_workspace':
            return ('当前模型工作区已停用（HTTP 402，deactivated_workspace），本次请求已明确失败。'
                    '请恢复该工作区后继续；当前连接、完整资料与已完成阶段均已保留，不会自动重试。')
        return f'模型服务返回 HTTP {status}，本次请求失败；已有资料和进度已保留。'
    if outcome == 'not_sent':
        if failure.get('reason') == 'local_capacity_preflight':
            return '本次请求超出当前连接声明的输入容量，尚未发送；完整资料已保留。'
        return '未能建立模型连接，本次请求尚未发送；已有资料和进度已保留。'
    local = failure.get('local_outcome')
    if local == 'incomplete' and outcome == 'failed':
        return '模型已结束本次输出，但回复不完整；已收到的内容与完整请求已保留，尚未生成可用结果。'
    if local == 'incomplete':
        prefix = '已收到的模型内容不完整，缺少正常结束确认'
    elif local == 'timeout':
        if failure.get('timeout_scope') == 'local_process':
            seconds = _timeout_seconds(failure.get('timeout_seconds'))
            duration = f'（{seconds:g} 秒）' if seconds is not None else ''
            prefix = f'本地模型进程等待超时{duration}'
        else:
            prefix = '等待模型响应超时'
    elif local == 'cancelled':
        prefix = '本地已停止等待模型响应'
    else:
        prefix = '本地未取得可确认的完整模型响应'
    return f'{prefix}；远端请求结果尚未确认，完整请求和已有进度已保留。'


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
                result = {**evidence, 'outcome': 'failed', 'http_status': status,
                        'retryable': status in (408, 425, 429) or status >= 500,
                        'reason': 'returned_http_error'}
                if status == 402 and getattr(current, 'provider_code', None) == 'deactivated_workspace':
                    result['provider_code'] = 'deactivated_workspace'
                return result
    if any(isinstance(current, (ConnectionRefusedError, socket.gaierror)) for current in chain):
        return {**evidence, 'outcome': 'not_sent', 'retryable': True,
                'reason': 'connection_not_established'}
    incomplete = next((current for current in chain
                       if type(current).__name__ == 'LLMIncompleteResponseError'), None)
    if incomplete is not None:
        finish = getattr(incomplete, 'finish_reason', None)
        terminal = finish in ('length', 'max_tokens', 'content_filter', 'tool_calls', 'tool_use')
        result = {**evidence, 'outcome': 'failed' if terminal else 'unknown', 'retryable': False,
                  'reason': 'returned_incomplete_response' if terminal else 'no_verified_terminal_response',
                  'local_outcome': 'incomplete', 'finish_reason': finish if terminal else 'unknown'}
        result['message'] = failure_message(result)
        return result
    unknown = {**evidence, 'outcome': 'unknown', 'retryable': False,
               'reason': 'no_verified_terminal_response'}
    process_timeout = next((current for current in chain
                            if isinstance(current, subprocess.TimeoutExpired)), None)
    if process_timeout is not None:
        unknown.update(local_outcome='timeout', timeout_scope='local_process')
        seconds = _timeout_seconds(process_timeout.timeout)
        if seconds is not None:
            unknown['timeout_seconds'] = seconds
    elif any(_request_timeout(current) for current in chain):
        unknown.update(local_outcome='timeout', timeout_scope='request')
    elif any(type(current).__name__ == 'LLMCancelledError' for current in chain):
        unknown['local_outcome'] = 'cancelled'
    else:
        unknown['local_outcome'] = 'unavailable'
    unknown['message'] = failure_message(unknown)
    return unknown
