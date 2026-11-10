"""Bind the author's explicit choice between two verified passages to a planning run."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from core.outline_planning import OutlinePlanningError, content_hash

ACTION = 'choose_author_source'
SCHEMA = 'planning-author-choice.v1'
_HASH = re.compile(r'[0-9a-f]{64}')


def _read(path: Path) -> dict:
    if path.resolve() != path.absolute() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('Invalid author decision record location or size')
    record = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(record, dict):
        raise ValueError('Invalid author decision record')
    return record


def _store_once(path: Path, record: dict, *, guard=lambda: None) -> None:
    """Link a complete fsynced file in one step, including on cross-process retries."""
    if path.parent.resolve() != path.parent.absolute() or path.is_symlink():
        raise ValueError('Invalid author decision record location')
    guard()
    if path.exists():
        if _read(path) != record:
            raise ValueError('已保存的作者选择与本次提交不同。')
        return
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.author-decision-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(record, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        guard()
        try:
            os.link(name, path)
        except FileExistsError:
            if _read(path) != record:
                raise ValueError('已保存的作者选择与本次提交不同。')
    finally:
        os.unlink(name)


def _issues(conflicts, source: str, chapter_count: int) -> list[dict]:
    if not isinstance(conflicts, list) or not conflicts:
        raise ValueError('Author choice requires confirmed source conflicts')
    seen = set()
    for issue in conflicts:
        if not isinstance(issue, dict) or set(issue) != {'issue_index', 'author_quotes', 'affected_chapters', 'reason'}:
            raise ValueError('Invalid author choice issue')
        index, quotes, chapters = issue['issue_index'], issue['author_quotes'], issue['affected_chapters']
        if (type(index) is not int or index < 0 or index in seen
                or not isinstance(issue['reason'], str) or not issue['reason'].strip()
                or not isinstance(quotes, list) or len(quotes) != 2 or quotes[0] == quotes[1]
                or any(not isinstance(q, str) or not q.strip() or q not in source for q in quotes)
                or not isinstance(chapters, list) or not chapters
                or any(type(n) is not int or not 1 <= n <= chapter_count for n in chapters)
                or len(set(chapters)) != len(chapters)):
            raise ValueError('Invalid original passages or chapter scope for author choice')
        seen.add(index)
    return sorted(conflicts, key=lambda i: i['issue_index'])


def _answer_map(question: dict, choices: list[dict]) -> dict:
    issues = {i['issue_index']: i for i in question['issues']}
    if not isinstance(choices, list) or len(issues) != len(choices):
        raise ValueError('请为每一处原文冲突选择要采用的版本。')
    answers = {}
    for item in choices:
        if not isinstance(item, dict) or set(item) != {'issue_index', 'selected_quote'}:
            raise ValueError('作者选择格式不正确。')
        index, selected = item['issue_index'], item['selected_quote']
        if (type(index) is not int or index not in issues or index in answers
                or type(selected) is not int or selected not in (0, 1)):
            raise ValueError('请为每一处原文冲突选择一个有效版本。')
        answers[index] = {**item, 'author_quote': issues[index]['author_quotes'][selected]}
    return answers


def _answers(cache: Path, questions: dict) -> dict:
    request_id = questions['request_id']
    path = cache / f'author-answer-{request_id}.json'
    if not path.exists() and not path.is_symlink():
        return {}
    record = _read(path)
    if (record.get('schema') != SCHEMA or record.get('request_id') != request_id
            or record.get('choices_hash') != content_hash(record.get('choices'))):
        raise ValueError('作者选择记录与当前问题不匹配。')
    return _answer_map(questions, record['choices'])


def _authority(directory: Path, source: str) -> dict:
    """Only authored choices cross model namespaces. Bind each answer back to its question."""
    choices = {}
    run_id = directory.parent.name
    source_digest = hashlib.sha256(source.encode()).hexdigest()
    for path in sorted((directory / 'checkpoints').glob('*/author-answer-*.json')):
        cache = path.parent
        identity = _read(cache / 'identity.json')
        if not _HASH.fullmatch(cache.name) or content_hash(identity) != cache.name:
            raise ValueError('作者选择的历史规划身份无法验证。')
        if identity.get('source_sha256') != source_digest:
            continue
        request_id = path.stem.removeprefix('author-answer-')
        if not _HASH.fullmatch(request_id):
            raise ValueError('作者选择的历史记录标识无效。')
        record = _read(cache / f'author-question-{request_id}.json')
        if (content_hash(record) != request_id or record.get('schema') != SCHEMA
                or record.get('run_id') != run_id or record.get('identity_hash') != cache.name
                or record.get('source_sha256') != source_digest):
            raise ValueError('历史作者选择没有与本任务和原文匹配的问题。')
        issues = _issues(record.get('issues'), source, identity['chapter_count'])
        answers = _answers(cache, {'request_id': request_id, 'issues': issues})
        for issue in issues:
            pair = tuple(sorted(issue['author_quotes']))
            selected = answers[issue['issue_index']]['author_quote']
            existing = choices.get(pair)
            if existing and selected != existing['author_quote']:
                raise ValueError('同一原文冲突存在互斥的历史作者选择。')
            choices[pair] = {
                'choice_id': content_hash({'run_id': run_id, 'source_sha256': source_digest, 'passages': pair}),
                'author_quotes': list(pair), 'author_quote': selected,
            }
    return choices


def authorized_choices(planner) -> list[dict]:
    """Stable story authority, independent of model, review order and proposal wording."""
    choices = _authority(planner.directory, planner.source)
    return sorted(choices.values(), key=lambda choice: choice['choice_id'])


def _known_answers(issues, authority):
    answers = {}
    for issue in issues:
        pair = tuple(sorted(issue['author_quotes']))
        if pair in authority:
            selected = authority[pair]['author_quote']
            index = issue['issue_index']
            answers[index] = {'issue_index': index, 'selected_quote': issue['author_quotes'].index(selected),
                              'author_quote': selected}
    return answers


def pending_decisions(planner, conflicts: list[dict]) -> tuple[dict, dict]:
    """Ask only about passages not already settled by this author for this run and source."""
    issues = _issues(conflicts, planner.source, planner.chapter_count)
    answers = _known_answers(issues, _authority(planner.directory, planner.source))
    pending = [issue for issue in issues if issue['issue_index'] not in answers]
    body = {'schema': SCHEMA, 'run_id': planner.directory.parent.name, 'identity_hash': planner.identity_hash,
            'source_sha256': hashlib.sha256(planner.source.encode()).hexdigest(), 'issues': pending}
    request_id = content_hash(body)
    if pending:
        _store_once(planner.cache / f'author-question-{request_id}.json', body, guard=planner._guard)
    return {'request_id': request_id, 'issues': pending}, answers


class AuthorChoiceRequired(OutlinePlanningError):
    def __init__(self, reason: str, questions: dict):
        super().__init__(reason)
        self.questions = questions


def defer_for_author(planner, questions: dict) -> None:
    reason = '作者原文有两种相互冲突的设定，请选择采用哪一条。独立的修订结果已保存。'
    planner.progress['recovery'] = {'retryable': False, 'action': ACTION, 'reason': reason,
                                    'request_id': questions['request_id']}
    planner.progress['next_action'] = reason
    raise AuthorChoiceRequired(reason, questions)


def _active_question(project: Path, run_id: str, progress: dict):
    recovery = progress.get('recovery') or {}
    if (not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', run_id) or progress.get('run_id') != run_id
            or progress.get('status') != 'failed' or recovery.get('action') != ACTION):
        raise ValueError('任务目前没有需要确认的原文冲突。')
    identity_hash = progress.get('identity_hash')
    request_id = recovery.get('request_id')
    if not all(isinstance(x, str) and _HASH.fullmatch(x) for x in (identity_hash, request_id)):
        raise ValueError('待确认的作者选择标识不正确。')
    cache = project / 'outputs/runs' / run_id / 'planning/checkpoints' / identity_hash
    identity = _read(cache / 'identity.json')
    question = _read(cache / f'author-question-{request_id}.json')
    source = project / 'outputs/input/prompt.md'
    if source.resolve() != source.absolute() or source.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('作者原文无法校验。')
    original = source.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    if (content_hash(identity) != identity_hash or content_hash(question) != request_id
            or question.get('schema') != SCHEMA or question.get('identity_hash') != identity_hash
            or question.get('run_id') != run_id or question.get('source_sha256') != digest
            or identity.get('source_sha256') != digest):
        raise ValueError('作者原文或已保存的冲突记录已变化，请刷新任务状态。')
    issues = _issues(question.get('issues'), original.decode('utf-8'), identity['chapter_count'])
    return cache, {'request_id': request_id, 'issues': issues}, original.decode('utf-8')


def decision_status(project: Path, run_id: str, progress: dict) -> tuple[dict, bool]:
    cache, questions, source = _active_question(project, run_id, progress)
    known = _known_answers(questions['issues'], _authority(cache.parent.parent, source))
    return questions, len(known) == len(questions['issues'])


def save_choices(project: Path, run_id: str, request_id: str, choices: list[dict]) -> None:
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', run_id):
        raise ValueError('任务标识不正确。')
    progress = _read(project / 'outputs/runs' / run_id / 'planning/progress.json')
    cache, questions, source = _active_question(project, run_id, progress)
    if questions['request_id'] != request_id:
        raise ValueError('待确认的设定已变化，请刷新任务状态。')
    _save_answer(cache, questions, choices, source)


def _save_answer(cache: Path, questions: dict, choices: list[dict], source: str, *, guard=lambda: None) -> None:
    request_id = questions['request_id']
    proposed = _answer_map(questions, choices)
    choices_by_passage = {pair: choice['author_quote'] for pair, choice in _authority(cache.parent.parent, source).items()}
    for issue in questions['issues']:
        pair = tuple(sorted(issue['author_quotes']))
        selected = proposed[issue['issue_index']]['author_quote']
        if choices_by_passage.setdefault(pair, selected) != selected:
            raise ValueError('此次选择与已确认的同一原文设定不同。')
    canonical = sorted(choices, key=lambda item: item['issue_index'])
    _store_once(cache / f'author-answer-{request_id}.json', {
        'schema': SCHEMA, 'request_id': request_id, 'choices': canonical, 'choices_hash': content_hash(canonical)}, guard=guard)


def active_decision_status(project: Path, run_id: str) -> tuple[dict, bool] | None:
    """Used by native clients to display the same source passages as Studio."""
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', run_id):
        raise ValueError('Invalid run identifier')
    progress_path = project / 'outputs/runs' / run_id / 'planning/progress.json'
    if not progress_path.exists():
        return None
    progress = _read(progress_path)
    if progress.get('status') != 'failed' or (progress.get('recovery') or {}).get('action') != ACTION:
        return None
    return decision_status(project, run_id, progress)


def save_standalone_choices(planner, request_id: str, choices: list[dict]) -> None:
    """Resolve against the same immutable author input the standalone planner uses."""
    progress = _read(planner.directory / 'progress.json')
    if (not isinstance(request_id, str) or not _HASH.fullmatch(request_id)
            or progress.get('identity_hash') != planner.identity_hash
            or progress.get('status') != 'failed' or (progress.get('recovery') or {}).get('request_id') != request_id
            or progress['recovery'].get('action') != ACTION):
        raise ValueError('待确认的独立规划任务已变化，请核对当前原文选项。')
    record = _read(planner.cache / f'author-question-{request_id}.json')
    expected = {'schema': SCHEMA, 'run_id': planner.directory.parent.name, 'identity_hash': planner.identity_hash,
                'source_sha256': hashlib.sha256(planner.source.encode()).hexdigest(),
                'issues': _issues(record.get('issues'), planner.source, planner.chapter_count)}
    if record != expected or content_hash(record) != request_id:
        raise ValueError('待确认的设定与当前作者原文不匹配。')
    _save_answer(planner.cache, {'request_id': request_id, 'issues': expected['issues']}, choices, planner.source,
                 guard=planner._guard)


def cli_choices(request_id, choices):
    if not request_id and not choices:
        return {}
    if not request_id or not choices or any(number not in (1, 2) for _, number in choices):
        raise ValueError('Specify --author-request and --author-choice ISSUE_INDEX 1|2 for all conflicts.')
    return {'author_decisions': {'request_id': request_id,
            'choices': [{'issue_index': index, 'selected_quote': number - 1} for index, number in choices]}}
