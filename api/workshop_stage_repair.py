"""Durable, field-scoped repair of a complete delivery-stage envelope.

This is an unvalidated candidate, never a delivery checkpoint. The service owns
the session lock and revision/job fencing; callers persist before dispatch and
after accepting a patch. Reads never create directories or call a provider.
"""
from __future__ import annotations

from copy import deepcopy
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from .workshop_checkpoints import CheckpointError, _open_directory
from .workshop_delivery import (DeliveryError, StageEnvelopeResponseError,
    checkpoint_fingerprint, parse_stage, repair_stage_envelope,
    stage_envelope_contract, stage_envelope_rules)

VERSION = 'workshop.stage-envelope-repair.v2'
LEGACY_VERSION = 'workshop.stage-envelope-repair.v1'
MAX_BYTES = 4 * 1024 * 1024
_ID = re.compile(r'[0-9a-f]{32}\Z')

RULES = '''
Repair ONLY the failed serialization fields of a complete saved stage.
This is not creative planning or permission to rewrite the story.
Return ONLY {"stage_envelope":{...}}, containing exactly the fields named by
issues. Use the supplied canonical enum and locked values. The candidate and
author_context are read-only; the application preserves every other field.
Do not return story_design, chapters, prose, hashes or additional top-level keys.
An unknown name is not permission to change the author's chosen organization.
Distinguish short_novel, standalone_long, multi_volume and series_installment by
their meaning and the complete author context, not chapter count alone.
'''


class StageRepairError(DeliveryError):
    pass


class StageRepairStorageError(RuntimeError):
    """Storage/lineage failure; never spend a model retry to repair this."""


def _storage_operation(function):
    @wraps(function)
    def protected(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            detail = str(exc) if isinstance(exc, (StageRepairError, CheckpointError)) else '阶段格式修复记录无法安全保存或读取；原候选仍保留。'
            raise StageRepairStorageError(detail) from exc
    return protected


def _canonical(value) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode()
    except (TypeError, ValueError, UnicodeError) as exc:
        raise StageRepairError('阶段格式修复包含不可保存的数据；原回复仍保留。') from exc


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _decode(raw: bytes):
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError('duplicate key')
            obj[key] = value
        return obj
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise StageRepairError('阶段格式修复必须是完整且字段唯一的 JSON；原回复仍保留。') from exc


def _binding(session: dict) -> dict:
    sid, rid = session.get('id'), session.get('delivery_run_id')
    source = session.get('delivery_source_revision')
    if (not isinstance(sid, str) or not _ID.fullmatch(sid)
            or not isinstance(rid, str) or not _ID.fullmatch(rid)
            or type(source) is not int or source < 0
            or type(session.get('revision')) is not int or source > session['revision']):
        raise StageRepairError('阶段格式修复与当前讨论身份不匹配。')
    return {'session_id': sid, 'run_id': rid, 'source_revision': source,
            'skill_version': session['skill_version'],
            'input_sha256': checkpoint_fingerprint(session, session['skill_version'])}


def _filename(run_id: str, stage: str, chapter_range) -> str:
    if not isinstance(run_id, str) or not _ID.fullmatch(run_id) or stage not in {'story_design', 'chapter_plan'}:
        raise StageRepairError('阶段格式修复位置不合法。')
    suffix = ''
    if chapter_range is not None:
        if (not isinstance(chapter_range, (list, tuple)) or len(chapter_range) != 2
                or any(type(n) is not int for n in chapter_range)
                or not 1 <= chapter_range[0] <= chapter_range[1] <= 511
                or stage != 'chapter_plan'):
            raise StageRepairError('阶段格式修复的章节范围不合法。')
        suffix = f'-{chapter_range[0]}-{chapter_range[1]}'
    return f'{run_id}-{stage}{suffix}.json'


class StageEnvelopeRepair:
    def __init__(self, candidate: dict, issues: list, *, stage: str,
                 mode: str | None = None, chapter_count: int | None = None,
                 chapter_range: tuple[int, int] | None = None):
        self.candidate = deepcopy(candidate)
        self.issues = deepcopy(issues)
        self.stage, self.mode, self.chapter_count = stage, mode, chapter_count
        self.chapter_range = tuple(chapter_range) if chapter_range is not None else None
        _filename('0' * 32, stage, self.chapter_range)
        self.saved_patch = None
        self.status = 'active'
        self.binding = None
        self.source_receipt = None
        self.generating_request = None
        self.request_needs_save = False
        # Issues are code-owned. Recalculate rather than trusting stored/model
        # issue names to grant write access to arbitrary candidate fields.
        actual = []
        try:
            parse_stage(_canonical(self.candidate).decode(), stage, mode, chapter_count,
                        chapter_range=self.chapter_range)
        except StageEnvelopeResponseError as exc:
            actual = exc.issues
        except DeliveryError:
            pass
        if not actual or actual != self.issues:
            raise StageRepairError('阶段格式修复的问题范围已改变。')

    @_storage_operation
    def bind_request(self, user: str, source_coverage: dict | None = None) -> None:
        """Retain the exact projection that generated this candidate, not a new view.

        The response receipt binds the original request bytes. Coverage is derived
        from that view, so a resumed patch cannot claim unobserved source chapters.
        """
        if not isinstance(user, str):
            raise StageRepairError('阶段格式修复缺少原请求；原候选仍保留。')
        context = _decode(user.encode())
        if not isinstance(context, dict) or context.get('delivery_stage') != self.stage:
            raise StageRepairError('阶段格式修复的原请求阶段不匹配。')
        if self.source_receipt is not None and hashlib.sha256(user.encode()).hexdigest() != self.source_receipt['user_sha256']:
            raise StageRepairError('无法核对阶段候选的原请求上下文；候选与检查点仍保留，请恢复原请求记录后继续。')
        coverage = request_source_coverage(context, self.chapter_range, self.chapter_count)
        if source_coverage != coverage:
            raise StageRepairError('阶段格式修复的原文读取范围与原请求不一致。')
        request = {'user': user, 'source_coverage': deepcopy(coverage)}
        if self.generating_request is not None and self.generating_request != request:
            raise StageRepairError('阶段格式修复的原请求不能被替换。')
        if self.generating_request is None:
            self.generating_request = request
            self.request_needs_save = True

    @property
    def source_coverage(self):
        return deepcopy(self.generating_request['source_coverage']) if self.generating_request else None

    def request(self, context: dict | None = None) -> tuple[str, str]:
        if self.generating_request is not None:
            context = _decode(self.generating_request['user'].encode())
        if context is None:
            raise StageRepairStorageError('阶段格式修复缺少原请求上下文；候选与检查点仍保留。')
        body = {'operation': 'repair_stage_envelope', 'delivery_stage': self.stage,
                'candidate': self.candidate, 'issues': self.issues,
                'stage_envelope_contract': stage_envelope_contract(self.mode, self.chapter_count),
                'author_context': context}
        return RULES + stage_envelope_rules(self.mode, self.chapter_count), _canonical(body).decode()

    def merge(self, text: str) -> str:
        clean = text.strip()
        if clean.startswith('```'):
            clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', clean, flags=re.IGNORECASE)
        response = _decode(clean.encode())
        if not isinstance(response, dict) or set(response) != {'stage_envelope'} or not isinstance(response['stage_envelope'], dict):
            raise StageRepairError('阶段格式修复只能返回 stage_envelope，不能重写故事。')
        result = repair_stage_envelope(self.candidate, response['stage_envelope'],
                                      mode=self.mode, chapter_count=self.chapter_count)
        return _canonical(result).decode()

    def accept(self, text: str) -> str:
        merged = self.merge(text)
        response = _decode(re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip(), flags=re.IGNORECASE).encode())
        if self.saved_patch is not None and self.saved_patch != response['stage_envelope']:
            raise StageRepairError('已保存的阶段格式修正不能被替换。')
        self.saved_patch = deepcopy(response['stage_envelope'])
        return merged

    def saved_result(self) -> str | None:
        if self.saved_patch is None:
            return None
        return self.merge(_canonical({'stage_envelope': self.saved_patch}).decode())

    @_storage_operation
    def record(self, session: dict, receipt: dict | None = None, *, status: str | None = None) -> dict:
        identity = _binding(session)
        if self.binding is not None and self.binding != identity:
            raise StageRepairError('讨论依据已改变，不能复用旧阶段格式修复。')
        self.binding = identity
        if status is not None:
            if status not in {'active', 'resolved'} or (self.status == 'resolved' and status != 'resolved'):
                raise StageRepairError('阶段格式修复不能退回已结束的状态。')
            self.status = status
        if receipt is not None:
            _check_receipt(receipt, identity, self.stage, self.candidate)
            if receipt['revision'] > session['revision']:
                raise StageRepairError('阶段原回复来自尚未发生的讨论版本。')
            self.source_receipt = {key: deepcopy(receipt[key]) for key in
                ('revision', 'attempt', 'stage', 'part', 'job_id', 'response_sha256', 'system_sha256', 'user_sha256')}
        if self.generating_request is not None:
            self.bind_request(**self.generating_request)
        record = {'schema': VERSION, 'binding': identity, 'stage': self.stage, 'status': self.status,
                  'chapter_range': list(self.chapter_range) if self.chapter_range else None,
                  'mode': self.mode, 'chapter_count': self.chapter_count,
                  'candidate': self.candidate, 'issues': self.issues,
                  'source_receipt': self.source_receipt, 'saved_patch': self.saved_patch,
                  'generating_request': self.generating_request}
        return {**deepcopy(record), 'sha256': _digest(record)}


def request_source_coverage(context: dict, chapter_range=None, chapter_count=None):
    projected = (context.get('previous_skeleton') or {}).get('original_chapters')
    if projected is None:
        return None
    window = context.get('chapter_window') or {}
    if (not isinstance(projected, dict) or chapter_range is None
            or (window.get('first'), window.get('last')) != tuple(chapter_range)):
        raise StageRepairError('阶段原请求的章节窗口与修复范围不匹配。')
    digest, included = projected.get('source_sha256'), projected.get('included_chapters')
    # OriginalChapters.recognize currently permits this projection only when
    # the original and target chapter counts match. Count-changing revisions
    # keep their complete original input and do not enter this coverage contract.
    if (not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
            or not isinstance(included, list) or type(chapter_count) is not int
            or any(type(n) is not int or not 1 <= n <= chapter_count for n in included)
            or included != sorted(set(included))
            or not set(range(chapter_range[0], chapter_range[1] + 1)) <= set(included)):
        raise StageRepairError('阶段原请求未完整保留原文读取范围。')
    return {'source_sha256': digest, 'included_chapters': deepcopy(included)}


def _check_receipt(receipt, identity, stage, candidate):
    if (not isinstance(receipt, dict)
            or receipt.get('schema_version') != 'workshop.stage-response.v1'
            or receipt.get('session_id') != identity['session_id']
            or receipt.get('run_id') != identity['run_id'] or receipt.get('stage') != stage
            or type(receipt.get('revision')) is not int or receipt['revision'] < identity['source_revision']
            or type(receipt.get('attempt')) is not int or receipt['attempt'] < 1
            or not {'part', 'job_id', 'system_sha256', 'user_sha256'} <= set(receipt)
            or not isinstance(receipt.get('raw'), str)
            or hashlib.sha256(receipt['raw'].encode()).hexdigest() != receipt.get('response_sha256')
            or receipt.get('response_bytes') != len(receipt['raw'].encode())):
        raise StageRepairError('阶段原回复与修复候选的绑定不完整。')
    clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', receipt['raw'].strip(), flags=re.IGNORECASE)
    if _decode(clean.encode()) != candidate:
        raise StageRepairError('阶段修复候选与原回复不一致。')


def _validated(record: dict, session: dict | None = None) -> StageEnvelopeRepair:
    fields = {'schema', 'binding', 'stage', 'status', 'chapter_range', 'mode', 'chapter_count',
              'candidate', 'issues', 'source_receipt', 'saved_patch', 'sha256'}
    if isinstance(record, dict) and record.get('schema') == VERSION:
        fields.add('generating_request')
    if (not isinstance(record, dict) or set(record) != fields or record['schema'] not in {VERSION, LEGACY_VERSION}
            or record['status'] not in {'active', 'resolved'}
            or record['sha256'] != _digest({k:v for k,v in record.items() if k != 'sha256'})):
        raise StageRepairError('阶段格式修复完整性检查失败；原文件仍保留。')
    binding = record['binding']
    if (not isinstance(binding, dict) or set(binding) != {'session_id', 'run_id', 'source_revision', 'skill_version', 'input_sha256'}
            or not isinstance(binding['session_id'], str) or not _ID.fullmatch(binding['session_id'])
            or type(binding['source_revision']) is not int or binding['source_revision'] < 0
            or not isinstance(binding['skill_version'], str) or not binding['skill_version']
            or not isinstance(binding['input_sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', binding['input_sha256'])):
        raise StageRepairError('阶段格式修复身份不完整。')
    if session is not None and binding != _binding(session):
        raise StageRepairError('讨论依据已改变，不能复用旧阶段格式修复。')
    _filename(record['binding']['run_id'], record['stage'], record['chapter_range'])
    item = StageEnvelopeRepair(record['candidate'], record['issues'], stage=record['stage'],
        mode=record['mode'], chapter_count=record['chapter_count'], chapter_range=record['chapter_range'])
    item.binding = deepcopy(record['binding'])
    item.status = record['status']
    item.source_receipt = deepcopy(record['source_receipt'])
    request = record.get('generating_request')
    if request is not None:
        if not isinstance(request, dict) or set(request) != {'user', 'source_coverage'}:
            raise StageRepairError('阶段格式修复的原请求记录不完整。')
        item.bind_request(**request)
        item.request_needs_save = False
    if record['saved_patch'] is not None:
        item.accept(_canonical({'stage_envelope': record['saved_patch']}).decode())
    return item


def _verify_source(session_dir: Path, record: dict) -> None:
    reference = record['source_receipt']
    if reference is None:
        return
    keys = {'revision', 'attempt', 'stage', 'part', 'job_id', 'response_sha256', 'system_sha256', 'user_sha256'}
    if (not isinstance(reference, dict) or set(reference) != keys
            or type(reference['revision']) is not int or reference['revision'] < 0
            or type(reference['attempt']) is not int or reference['attempt'] < 1):
        raise StageRepairError('阶段原回复引用不完整。')
    from core.safe_project_io import read_project_bytes
    try:
        payload = read_project_bytes(session_dir, ('stage-responses', f"{reference['revision']}-{reference['attempt']}.json"),
                                     max_bytes=MAX_BYTES)
        receipt = _decode(payload)
    except (OSError, ValueError) as exc:
        raise StageRepairError('阶段原回复不能安全读取；修复记录仍保留。') from exc
    _check_receipt(receipt, record['binding'], record['stage'], record['candidate'])
    if {key: receipt[key] for key in keys} != reference:
        raise StageRepairError('阶段原回复的引用已改变；修复记录仍保留。')


def _read(fd: int, name: str):
    try:
        source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StageRepairError('阶段格式修复文件不可安全读取。') from exc
    try:
        info = os.fstat(source)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
            raise StageRepairError('阶段格式修复文件不合法或超出保存容量；未截断。')
        with os.fdopen(source, 'rb', closefd=False) as stream:
            payload = stream.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            raise StageRepairError('阶段格式修复超出保存容量；未截断。')
        record = _decode(payload)
        _validated(record)
        return record
    finally:
        os.close(source)


@_storage_operation
def load_repair(session_dir: Path, session: dict, *, stage: str, chapter_range=None) -> StageEnvelopeRepair | None:
    identity = _binding(session)
    name = _filename(identity['run_id'], stage, chapter_range)
    fd = _open_directory(Path(session_dir) / 'stage-repairs', create=False)
    if fd is None:
        return None
    try:
        record = _read(fd, name)
        if record is not None:
            _verify_source(Path(session_dir), record)
        item = _validated(record, session) if record is not None else None
        if item is not None and item.status == 'active' and item.generating_request is None:
            _recover_retained_request(Path(session_dir), item)
        return item if item is not None and item.status == 'active' else None
    finally:
        os.close(fd)


def _write(fd: int, name: str, payload: bytes) -> None:
    temporary = '.stage-repair-' + uuid.uuid4().hex
    try:
        dest = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(dest, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass


@_storage_operation
def save_repair(session_dir: Path, record: dict) -> None:
    """Save under the service's session lock; never replace an older candidate."""
    _validated(record)
    _verify_source(Path(session_dir), record)
    payload = _canonical(record)
    if len(payload) > MAX_BYTES:
        raise StageRepairError('阶段格式修复超出保存容量；未截断。')
    name = _filename(record['binding']['run_id'], record['stage'], record['chapter_range'])
    fd = _open_directory(Path(session_dir) / 'stage-repairs', create=True)
    try:
        prior = _read(fd, name)
        if prior is not None:
            replacing = (prior['status'] == 'resolved' and record['status'] == 'active'
                         and any(prior[k] != record[k] for k in ('candidate', 'issues', 'source_receipt')))
            if replacing:
                locks = ('binding', 'stage', 'chapter_range', 'mode', 'chapter_count')
                if any(prior[k] != record[k] for k in locks):
                    raise StageRepairError('新阶段修复不能改变原执行的身份或范围。')
                archive = name[:-5] + '.history-' + prior['sha256'] + '.json'
                saved = _read(fd, archive)
                if saved is not None and saved != prior:
                    raise StageRepairError('历史阶段修复记录与待归档内容不一致。')
                if saved is None:
                    _write(fd, archive, _canonical(prior))
                _write(fd, name, payload)
                return
            stable = set(record) - {'sha256', 'saved_patch', 'status', 'schema', 'generating_request'}
            if any(prior[k] != record[k] for k in stable):
                raise StageRepairError('已保存的阶段修复候选不能被替换。')
            old_request, new_request = prior.get('generating_request'), record.get('generating_request')
            if old_request is not None and old_request != new_request:
                raise StageRepairError('已保存的阶段原请求不能被替换。')
            if old_request is None and new_request is not None and prior['source_receipt'] is None:
                raise StageRepairError('旧阶段候选缺少原请求凭据；不能补写未经核对的上下文。')
            if prior['schema'] == VERSION and record['schema'] == LEGACY_VERSION:
                raise StageRepairError('阶段格式修复不能退回旧格式。')
            if prior['saved_patch'] is not None and prior['saved_patch'] != record['saved_patch']:
                raise StageRepairError('已保存的阶段格式修正不能被替换。')
            if prior['status'] == 'resolved' and record['status'] != 'resolved':
                raise StageRepairError('已结束的阶段格式修复不能重新启用。')
        _write(fd, name, payload)
    finally:
        os.close(fd)


@_storage_operation
def import_legacy_repair(session_dir: Path, session: dict, *, expected_user_sha256: str,
                         mode: str | None = None, chapter_count: int | None = None,
                         story_first: bool = True) -> StageEnvelopeRepair | None:
    """Read the latest current failed turn's first-stage receipt, without writes.

    The caller must reconstruct the exact original request from the unchanged
    current author context and previous retained repair responses, then supply
    its digest. A receipt's own digest is NOT evidence of that reconstruction.
    This narrow bridge is called before resume changes revision/job identity.
    The returned candidate remains unvalidated and must be durably saved under
    the service lock before issuing its field-only repair request.
    """
    if not isinstance(expected_user_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_user_sha256):
        raise StageRepairError('旧阶段回复缺少已核对的原请求绑定。')
    identity = _binding(session)
    if session.get('status') != 'error' or session.get('turn_intent') != 'deliver':
        return None
    revision = session['revision']
    fd = _open_directory(Path(session_dir) / 'stage-responses', create=False)
    if fd is None:
        return None
    try:
        names = []
        pattern = re.compile(rf'{revision}-([1-9][0-9]*)\.json\Z')
        for name in os.listdir(fd):
            match = pattern.fullmatch(name)
            if match:
                names.append((int(match.group(1)), name))
    finally:
        os.close(fd)
    if not names:
        return None
    from core.safe_project_io import read_project_bytes
    try:
        receipt = _decode(read_project_bytes(Path(session_dir), ('stage-responses', max(names)[1]), max_bytes=MAX_BYTES))
    except (OSError, ValueError) as exc:
        raise StageRepairError('旧阶段原回复不能安全读取。') from exc
    if (receipt.get('revision') != revision or receipt.get('job_id') != session.get('job_id')
            or receipt.get('session_id') != session['id'] or receipt.get('run_id') != identity['run_id']):
        raise StageRepairError('旧阶段回复不属于当前失败的执行。')
    if receipt.get('stage') != 'story_design' or receipt.get('part') is not None:
        return None
    if receipt.get('user_sha256') != expected_user_sha256:
        raise StageRepairError('旧阶段请求与当前完整作者依据不一致；原回复仍保留。')
    raw = receipt.get('raw')
    if not isinstance(raw, str):
        raise StageRepairError('旧阶段原回复缺失，不能恢复格式修复。')
    try:
        candidate = _decode(re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.IGNORECASE).encode())
    except StageRepairError:
        return None  # Incomplete output is not a field-scoped metadata candidate.
    _check_receipt(receipt, identity, 'story_design', candidate)
    try:
        parse_stage(receipt['raw'], 'story_design', mode, chapter_count, story_first=story_first)
    except StageEnvelopeResponseError as exc:
        repair = StageEnvelopeRepair(exc.candidate, exc.issues, stage='story_design', mode=mode,
                                     chapter_count=chapter_count)
        repair.record(session, receipt)
        return repair
    except (DeliveryError, KeyError, TypeError):
        return None
    return None


_LEGACY_REPAIR_INSTRUCTION = (
    'Repair only the reported structural fields and check all required fields together. '
    'Return the complete stage JSON, preserving valid story text, author choices and saved '
    'stages; do not redesign the story or change the saved mode, chapter count or approved volume boundaries.'
)


def _legacy_turn_receipts(session_dir: Path, session: dict) -> list[dict]:
    fd = _open_directory(session_dir / 'stage-responses', create=False)
    if fd is None:
        return []
    pattern = re.compile(rf"{session['revision']}-([1-9][0-9]*)\.json\Z")
    try:
        names = sorted((int(match.group(1)), name) for name in os.listdir(fd)
                       if (match := pattern.fullmatch(name)))
    finally:
        os.close(fd)
    if not names or [number for number, _ in names] != list(range(1, names[-1][0] + 1)):
        return []
    from core.safe_project_io import read_project_bytes
    return [_decode(read_project_bytes(session_dir, ('stage-responses', name), max_bytes=MAX_BYTES))
            for _, name in names]


def _legacy_source_records(session_dir: Path) -> list[tuple[str, str, dict]]:
    """Read only private request envelopes; never descend into executions."""
    directory = session_dir / 'source-evidence' / 'requests'
    fd = _open_directory(directory, create=False)
    if fd is None:
        return []
    try:
        groups = [name for name in os.listdir(fd) if re.fullmatch(r'[0-9a-f]{64}', name)]
    finally:
        os.close(fd)
    from core.safe_project_io import read_project_bytes
    records = []
    for group in groups:
        group_fd = _open_directory(directory / group, create=False)
        if group_fd is None:
            continue
        try:
            names = [name for name in os.listdir(group_fd) if re.fullmatch(r'[0-9a-f]{64}\.json', name)]
        finally:
            os.close(group_fd)
        for name in names:
            record = _decode(read_project_bytes(session_dir, ('source-evidence', 'requests', group, name), max_bytes=MAX_BYTES))
            if isinstance(record, dict):
                records.append((group, name[:-5], record))
    return records


def _recover_retained_request(session_dir: Path, repair: StageEnvelopeRepair) -> None:
    """Recover historical evidence-backed request bytes without writing on read."""
    reference = repair.source_receipt
    if reference is None:
        return
    from core.creative_types import digest
    for _group, request_id, record in _legacy_source_records(session_dir):
        system, full_user = record.get('system'), record.get('user')
        if (record.get('state') not in {'received', 'response_received'}
                or record.get('response_sha256') != reference['response_sha256']
                or not isinstance(record.get('response'), str)
                or hashlib.sha256(record['response'].encode()).hexdigest() != reference['response_sha256']
                or not isinstance(system, str) or not isinstance(full_user, str)
                or hashlib.sha256(system.encode()).hexdigest() != reference['system_sha256']
                or request_id != digest({'system': system, 'user': full_user})):
            continue
        user = full_user.split('\n\n[SOURCE_EVIDENCE]\n', 1)[0]
        if hashlib.sha256(user.encode()).hexdigest() != reference['user_sha256']:
            continue
        context = _decode(user.encode())
        repair.bind_request(user, request_source_coverage(context, repair.chapter_range, repair.chapter_count))
        return


@_storage_operation
def import_matching_legacy_repair(session_dir: Path, failed_session: dict) -> StageEnvelopeRepair | None:
    """Recover a provably identical old first-stage metadata failure.

    Reconstruct every request in the current failed turn from the *complete*
    current author context and its preceding immutable response. For reference
    routes also verify the exact transmitted full evidence packet and received
    response. Unknown historical layouts remain on their existing recovery path;
    no fields are guessed, discarded, rewritten or adopted here.
    """
    session_dir = Path(session_dir)
    session = failed_session
    if session.get('status') != 'error' or session.get('turn_intent') != 'deliver':
        return None
    if not session.get('delivery_run_id') or type(session.get('delivery_source_revision')) is not int:
        return None
    identity = _binding(session)
    from .workshop_checkpoints import load_checkpoint
    checkpoint = load_checkpoint(session_dir / 'delivery', identity['run_id'])
    if checkpoint and checkpoint.get('completed'):
        return None
    receipts = _legacy_turn_receipts(session_dir, session)
    if not receipts:
        return None
    latest = receipts[-1]
    if (not isinstance(latest, dict) or latest.get('stage') != 'story_design'
            or latest.get('part') is not None or latest.get('revision') != session['revision']
            or latest.get('job_id') != session.get('job_id') or latest.get('session_id') != session['id']
            or latest.get('run_id') != identity['run_id']):
        return None
    confirmed = (session.get('capacity') or {}).get('confirmed') or {}
    chapters = (confirmed.get('option') or {}).get('chapters')
    count = len(chapters) if isinstance(chapters, list) and chapters else None
    from core.story_design import enabled as story_first_enabled
    story_first = story_first_enabled(session)
    try:
        parse_stage(latest.get('raw'), 'story_design', None, count, story_first=story_first)
    except StageEnvelopeResponseError:
        pass
    except (DeliveryError, TypeError, AttributeError):
        return None
    else:
        return None
    from .workshop_delivery import author_context
    from .workshop_request import delivery_context
    from core.source_route_context import enabled as source_enabled, validate_context, validate_packet
    from core.creative_types import digest
    from core.safe_project_io import read_project_bytes
    packet, source_records = None, []
    if source_enabled(session.get('source_context')):
        source = validate_context(session['source_context'])
        packet = validate_packet(_decode(read_project_bytes(session_dir,
            ('source-evidence', source['evidence']['sha256'] + '.json'), max_bytes=MAX_BYTES)), source)
        source_records = _legacy_source_records(session_dir)
    previous = None
    for index, receipt in enumerate(receipts, 1):
        if (not isinstance(receipt, dict) or receipt.get('stage') != 'story_design'
                or receipt.get('part') is not None or receipt.get('attempt') != index
                or receipt.get('revision') != session['revision'] or receipt.get('job_id') != session.get('job_id')):
            return None
        raw = receipt.get('raw')
        if not isinstance(raw, str):
            return None
        clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.IGNORECASE)
        try:
            candidate = _decode(clean.encode())
        except StageRepairError:
            return None
        _check_receipt(receipt, identity, 'story_design', candidate)
        context = author_context(session)
        if previous is not None:
            failure = previous.get('validation') or {}
            if failure.get('status') != 'invalid' or not isinstance(failure.get('detail'), str):
                return None
            context.update(previous_model_response=previous['raw'].strip(), validation_error=failure['detail'],
                           repair_instruction=_LEGACY_REPAIR_INSTRUCTION)
        context.update(delivery_stage='story_design', delivery_mode=None, saved_story_stages={})
        user = json.dumps(delivery_context(context), ensure_ascii=False, separators=(',', ':'))
        if hashlib.sha256(user.encode()).hexdigest() != receipt.get('user_sha256'):
            return None
        if packet is not None:
            full_user = user + '\n\n[SOURCE_EVIDENCE]\n' + json.dumps(packet, ensure_ascii=False, separators=(',', ':')) + '\n[/SOURCE_EVIDENCE]'
            verified = False
            for group, request_id, record in source_records:
                if record.get('response_sha256') != receipt['response_sha256']:
                    continue
                system = record.get('system')
                if (record.get('state') in {'received', 'response_received'}
                        and record.get('response') == raw and record.get('user') == full_user
                        and isinstance(system, str) and hashlib.sha256(system.encode()).hexdigest() == receipt.get('system_sha256')
                        and request_id == digest({'system': system, 'user': full_user})
                        and group == digest({'system': system, 'user': user, 'evidence': packet['sha256']})):
                    verified = True
                    break
            if not verified:
                return None
        previous = receipt
    repair = import_legacy_repair(session_dir, session, expected_user_sha256=latest['user_sha256'],
                                chapter_count=count, story_first=story_first)
    if repair is not None:
        repair.bind_request(user)
    return repair
