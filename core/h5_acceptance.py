"""Read-only release evidence tools. This is not the H5 importer or an updater."""
import json
import re
import zipfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

try:
    from .h5_import import digest, epub_index, object_digest
    from .novel_classification import NovelClassification
    from .narrative_format import validate_serialization
    from .h5_contract import (PROFILE, BOOK_ID, TOKEN, ContractError, require as _require,
                             strict_json as _json, checked_archive as _archive,
                             verify_image, bound, safe_path, xml_document, valid_text)
except ImportError:
    from h5_import import digest, epub_index, object_digest
    from novel_classification import NovelClassification
    from narrative_format import validate_serialization
    from h5_contract import (PROFILE, BOOK_ID, TOKEN, ContractError, require as _require,
                             strict_json as _json, checked_archive as _archive,
                             verify_image, bound, safe_path, xml_document, valid_text)

MAX_ARCHIVE = PROFILE.zip_bytes
MAX_EXPANDED = PROFILE.zip_expanded
MEDIA = {'.epub': 'application/epub+zip', '.json': 'application/json', '.md': 'text/markdown',
         '.pdf': 'application/pdf', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
         '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}
PRIVATE_KEYS = {'method_policy', 'method_lock', 'method_lock_sha256', 'raw_critique', 'secret_knowledge',
                'method_reviews', 'knowledge_observations', 'run_method_lock', 'preference_id', 'raw_response',
                'creative_handoff', 'creative_lock_sha256', 'creative_snapshot_started', 'creative_execution_context',
                'reader_reviews', 'reader_review', 'reading_observations', 'review_snapshot'}
PRIVATE_SCHEMAS = ('novel-method-', 'narrative-method', 'novel-knowledge', 'novel-preference', 'novel-experiment',
                   'creative-handoff.', 'creative-execution.', 'reader-experience.')


def _public_json(value):
    if isinstance(value, dict):
        for key, child in value.items():
            _require(key.casefold().replace('-', '_') not in PRIVATE_KEYS, 'private_metadata_in_package')
            if key in {'schema', 'schema_version'} and isinstance(child, str):
                _require(not child.casefold().startswith(PRIVATE_SCHEMAS), 'private_schema_in_package')
            _public_json(child)
    elif isinstance(value, list):
        for child in value:
            _public_json(child)


def _public_path(entry):
    path = Path(entry['path'])
    parts, suffix, role = path.parts, path.suffix.lower(), entry['role']
    if role == 'book_export':
        allowed = len(parts) == 1 and suffix in {'.epub', '.md', '.pdf', '.docx'}
    elif role in {'h5_import', 'novel_classification', 'novel_serialization', 'publication_copy'}:
        allowed = len(parts) == 2 and parts[0] == 'meta' and suffix == '.json'
    elif role == 'cover_metadata':
        allowed = len(parts) == 2 and parts[0] == 'covers' and suffix == '.json'
    elif role in {'cover_candidate', 'selected_cover'}:
        _require(entry.get('media_type') in {'image/png', 'image/jpeg'}, 'cover_media_type',
                 path=entry['path'], actual=entry.get('media_type'), expected='PNG/JPEG')
        allowed = suffix in {'.png', '.jpg', '.jpeg'} and (
            (role == 'cover_candidate' and len(parts) >= 3 and parts[:2] == ('covers', 'pending'))
            or (role == 'selected_cover' and len(parts) == 2 and parts[0] == 'covers'))
    else:
        # Exact object families emitted by h5_publication.py, not arbitrary JSON
        # hidden under an otherwise public directory.
        relative = '/'.join(parts[2:])
        allowed = (role == 'h5_publication_object' and len(parts) >= 3 and parts[0] == 'h5-publication'
                   and parts[1].startswith('pkg-') and (relative in {
                       'meta/publication_package.json', 'meta/finalization.json', 'meta/delivery.json', '正文.md',
                   } or re.fullmatch(r'chapters/[0-9]{2,4}\.md', relative)))
    _require(allowed, 'invalid_public_role_path', path=entry['path'])


def inspect_package(path: str | Path) -> dict:
    """Validate exact ZIP bytes without extraction, writing, or external calls."""
    path = Path(path)
    _require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_ARCHIVE, 'invalid_package_file')
    with path.open('rb') as stream:
        raw = stream.read(MAX_ARCHIVE + 1)
    return inspect_package_bytes(raw)


def inspect_package_bytes(raw: bytes) -> dict:
    """Inspect the exact bytes to be published or returned to a downloader."""
    try:
        with _archive(raw) as archive:
            manifest = _json(archive.read('package-manifest.json'))
            _public_json(manifest)
            _require(manifest['schema_version'] == 4, 'manifest_version')
            files = manifest['files']
            _require(isinstance(files, list) and files, 'file_index')
            paths = [f['path'] for f in files]
            _require(paths == sorted(paths), 'manifest_order')
            _require(len(paths) == len(set(paths)), 'duplicate_manifest_path')
            _require(set(paths) | {'package-manifest.json'} == set(archive.namelist())
                     and 'package-manifest.json' not in paths, 'unindexed_zip_member')
            _require(manifest['package_revision_sha256'] == object_digest(files), 'package_revision')
            by_path = {f['path']: f for f in files}
            for entry in files:
                content = archive.read(entry['path'])
                _public_path(entry)
                _require(type(entry['size']) is int and entry['size'] == len(content)
                         and entry['sha256'] == digest(content), 'file_digest')
                suffix = Path(entry['path']).suffix.lower()
                if suffix == '.json':
                    _public_json(_json(content))
                if suffix in MEDIA:
                    _require(entry['media_type'] == MEDIA[suffix], 'epub_media_type' if suffix == '.epub' else 'file_media_type')
                _require(not any(p in {'methods', 'quality', 'state', 'critiques', 'preferences'}
                                 for p in Path(entry['path']).parts), 'private_artifact_in_package')
                _require(entry['role'] in {'book_export', 'h5_import', 'novel_classification', 'novel_serialization',
                                          'publication_copy', 'cover_metadata', 'cover_candidate', 'selected_cover',
                                          'h5_publication_object'},
                         'unknown_public_role')

            def singleton(role, *, required=False):
                matching = [f for f in files if f['role'] == role]
                _require(len(matching) <= 1 and (matching or not required), 'ambiguous_or_missing_role')
                return matching[0] if matching else None

            def referenced(pointer, role, *, required=True):
                entry = singleton(role, required=required)
                if entry is None:
                    _require(pointer is None or isinstance(pointer, dict) and not pointer.get('path'), 'conflicting_path_pointer')
                    return None
                _require(isinstance(pointer, dict) and pointer['path'] == entry['path'], 'conflicting_path_pointer')
                for key in ('size', 'sha256', 'media_type'):
                    if key in pointer:
                        _require(pointer[key] == entry[key], 'conflicting_file_descriptor')
                return _json(archive.read(entry['path']))

            data = referenced(manifest['h5_import'], 'h5_import')
            _require(data['schema_version'] == 'novel-h5-import.v1' and data['status'] == 'ready'
                     and data['body_source'] == 'epub', 'import_contract')
            _require(data['book_id'] == manifest['book_id'] and BOOK_ID.fullmatch(data['book_id']), 'book_identity')
            author_present = 'author' in data
            manifest_author_present = 'author' in manifest
            if author_present:
                _require(type(data['author']) is str, 'author_contract')
            if manifest_author_present:
                _require(type(manifest['author']) is str, 'author_contract')
            if author_present and manifest_author_present:
                _require(data['author'] == manifest['author'], 'author_contract')
            classification = referenced(data['classification'], 'novel_classification', required=False)
            referenced(manifest['classification'], 'novel_classification', required=False)
            if classification is not None:
                _require(NovelClassification.from_dict(classification).to_dict() == classification, 'classification_contract')
                _require(manifest['classification']['classification_id'] == classification['classification_id'], 'classification_identity')
            serialization = referenced(data['serialization'], 'novel_serialization', required=False)
            referenced(manifest['serialization'], 'novel_serialization', required=False)
            if serialization is not None:
                _require(validate_serialization(serialization) == serialization, 'serialization_contract')
                _require(manifest['serialization']['serialization_id'] == serialization['serialization_id'], 'serialization_identity')
            for entry in files:
                if entry['role'] == 'h5_publication_object' and entry['path'].endswith('/meta/publication_package.json'):
                    publication = _json(archive.read(entry['path']))
                    for key, expected in (('classification', classification), ('serialization', serialization)):
                        if publication.get(key) is not None:
                            _require(publication[key] == expected, 'publication_metadata_conflict', path=entry['path'])
            epub_entry = by_path[data['epub']['path']]
            _require(epub_entry['role'] == 'book_export' and epub_entry['media_type'] == 'application/epub+zip', 'epub_media_type')
            _require(all(data['epub'][k] == epub_entry[k] for k in ('path', 'size', 'sha256')), 'epub_descriptor')
            epub_raw = archive.read(epub_entry['path'])
            _require(sum(f['media_type'] == 'application/epub+zip' for f in files) == 1, 'epub_count')
            with _archive(epub_raw, epub=True) as epub:
                _require(epub.read('mimetype') == b'application/epub+zip', 'epub_mimetype')
            index = epub_index(epub_raw, serialization, classification)
            author = (
                data['author'] if author_present
                else manifest['author'] if manifest_author_present
                else index['dc_creator']
            )
            _require(not author or valid_text(author, 128, utf8_bytes=True), 'author_contract')
            with _archive(epub_raw, epub=True) as epub:
                opf = xml_document(epub.read(index['opf_path']), path=index['opf_path'])
                for meta in opf.findall('.//{*}meta'):
                    key = meta.get('property', meta.get('name', ''))
                    if key.startswith('novel-os:'):
                        _require(key in {'novel-os:classification', 'novel-os:serialization', 'novel-os:chapter-map'}, 'private_epub_metadata')
                        _public_json(_json(meta.text or 'null'))
                    _require(key.casefold().replace('-', '_') not in PRIVATE_KEYS, 'private_epub_metadata')
            expected_chapters = [{**c, 'chapter_id': f"{data['book_id']}:chapter:{c['number']}"} for c in index['chapters']]
            _require(data['chapters'] == expected_chapters and data['chapter_count'] == len(expected_chapters), 'chapter_mapping')
            _require(data['non_chapter_items'] == index['non_chapter_items'], 'front_matter_mapping')
            _require(data['epub']['opf_path'] == index['opf_path'] and data['epub']['dc_identifier'] == index['dc_identifier'], 'epub_identity')
            selected = singleton('selected_cover')
            _require(data['cover']['selected'] == selected, 'cover_selection')
            _require(data['cover']['selection_action'] == ('replace' if selected else 'keep_existing'), 'cover_action')
            cover_metadata = singleton('cover_metadata')
            _require(data['cover']['metadata'] == cover_metadata, 'cover_metadata')
            decoded = {}
            candidate_files = {}
            orders = set()
            for file in files:
                if file['role'] in {'selected_cover', 'cover_candidate'}:
                    decoded[file['path']] = verify_image(archive.read(file['path']), file['media_type'], file['path'])
                if file['role'] == 'cover_candidate':
                    candidate_id, order = file.get('candidate_id'), file.get('display_order')
                    _require(isinstance(candidate_id, str) and TOKEN.fullmatch(candidate_id)
                             and candidate_id not in candidate_files, 'cover_candidate_identity')
                    _require(type(order) is int and 1 <= order <= PROFILE.cover_candidates and order not in orders, 'cover_candidate_order')
                    candidate_files[candidate_id] = file
                    orders.add(order)
            if cover_metadata:
                try:
                    from .cover_models import CoverSet
                except ImportError:
                    from cover_models import CoverSet
                raw_cover = _json(archive.read(cover_metadata['path']))
                _require(type(raw_cover.get('revision')) is int and raw_cover['revision'] >= 1
                         and type(raw_cover.get('brief_schema_version')) is int
                         and raw_cover['brief_schema_version'] in {1, 2}
                         and isinstance(raw_cover.get('cover_set_id'), str)
                         and TOKEN.fullmatch(raw_cover['cover_set_id'])
                         and raw_cover.get('status') in {'generating', 'partial', 'ready', 'selected', 'failed', 'stale'},
                         'cover_metadata_identity')
                concepts = [c['concept_id'] for c in raw_cover['concepts']]
                candidate_ids = [c['candidate_id'] for c in raw_cover['candidates']]
                _require(all(isinstance(i, str) and TOKEN.fullmatch(i) for i in concepts + candidate_ids)
                         and len(set(concepts)) == len(concepts)
                         and len(set(candidate_ids)) == len(candidate_ids), 'cover_candidate_identity')
                _require(all(c['concept_id'] in concepts for c in raw_cover['candidates']), 'cover_candidate_concept')
                _require(all(c.get('status') in {'pending', 'ready', 'failed', 'rejected', 'selected'}
                             and type(c.get('width')) is int and type(c.get('height')) is int
                             for c in raw_cover['candidates']), 'cover_candidate_contract')
                cover = CoverSet.from_dict(raw_cover)
                bound(len(cover.candidates), PROFILE.cover_candidates, 'cover_candidate_count')
                _require(cover.selected_candidate_id == data['cover']['selected_candidate_id']
                         == manifest['cover']['selected_candidate_id'], 'cover_selection')
                _require(not manifest['cover']['cover_set_id'] or manifest['cover']['cover_set_id'] == cover.cover_set_id, 'cover_identity')
                for candidate in cover.candidates:
                    file = candidate_files.get(candidate.candidate_id)
                    is_selected = candidate.candidate_id == cover.selected_candidate_id
                    _require(candidate.status != 'selected' or is_selected, 'cover_selection')
                    if is_selected:
                        file = selected
                    if file is None:
                        _require(candidate.status not in {'ready', 'selected'}, 'cover_candidate')
                        continue
                    _require(file['sha256'] == candidate.sha256, 'cover_candidate')
                    _require(decoded[file['path']] == (candidate.width, candidate.height)
                             and file['media_type'] == candidate.content_type, 'cover_dimensions')
                    if candidate.relative_path:
                        _require(safe_path(candidate.relative_path.removeprefix('outputs/deliverables/')), 'cover_candidate_path')
                _require(set(candidate_files).issubset({c.candidate_id for c in cover.candidates}), 'cover_candidate_identity')
                if selected:
                    candidates = [c for c in cover.candidates if c.candidate_id == cover.selected_candidate_id]
                    _require(len(candidates) == 1 and candidates[0].sha256 == selected['sha256'], 'cover_selection')
            else:
                _require(not candidate_files and not data['cover']['selected_candidate_id'], 'cover_metadata')
            versions = {'epub_sha256': digest(epub_raw), 'content_sha256': index['content_sha256'],
                        'front_matter_sha256': index['front_matter_sha256'],
                        'selected_cover_sha256': selected['sha256'] if selected else None}
            _require(
                ('author_sha256' in data['versions']) == author_present,
                'component_revisions',
            )
            if author_present:
                versions['author_sha256'] = object_digest({'author': author})
            for name, role in (('classification', 'novel_classification'), ('serialization', 'novel_serialization'),
                               ('publication_copy', 'publication_copy'), ('cover_metadata', 'cover_metadata')):
                entry = singleton(role)
                versions[name + '_sha256'] = entry['sha256'] if entry else None
            _require(data['versions'] == versions, 'component_revisions')
            _require(data['import_revision_sha256'] == object_digest({'book_id': data['book_id'], 'versions': versions}), 'import_revision')
            return {'engine_contract_status': 'passed', 'external_import_status': 'pending',
                    'target_profile': PROFILE.id, 'catalog_sha256': PROFILE.catalog_sha256,
                    'validator_revision': PROFILE.validator_revision,
                    'package_sha256': digest(raw), 'package_revision_sha256': manifest['package_revision_sha256'],
                    'import_revision_sha256': data['import_revision_sha256'], 'book_id': data['book_id'],
                    'author': author,
                    'author_present': author_present or manifest_author_present,
                    'versions': versions, 'chapters': data['chapters'], 'chapter_count': data['chapter_count'],
                    'volumes': [{k: v[k] for k in ('volume_id', 'volume_number', 'title', 'chapter_start', 'chapter_end')}
                                for v in (serialization or {}).get('volumes', [])],
                    'volume_count': len({c['volume_id'] for c in data['chapters']}),
                    'introduction_count': sum(c['kind'] == 'introduction' for c in data['non_chapter_items']),
                    'non_chapter_items': data['non_chapter_items']}
    except ContractError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, OSError, zipfile.BadZipFile, RuntimeError, RecursionError) as exc:
        raise ContractError('malformed_package_contract') from exc


class _Record(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class AcceptanceCase(_Record):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    action: Literal['initial', 'repeat', 'cover_only', 'chapter_update', 'classification_update',
                    'volume_title_update', 'introduction_update', 'different_book', 'history_replay', 'reject_invalid']
    package: str
    baseline: str | None = None
    free_trial_end: int = Field(ge=1, le=20)
    expected_error: str | None = None


_ALLOWED_CHANGES = {
    'repeat': set(),
    'cover_only': {'selected_cover_sha256', 'cover_metadata_sha256'},
    'chapter_update': {'epub_sha256', 'content_sha256'},
    'classification_update': {'epub_sha256', 'classification_sha256'},
    'volume_title_update': {'epub_sha256', 'serialization_sha256'},
    'introduction_update': {'epub_sha256', 'front_matter_sha256', 'publication_copy_sha256'},
}
_REQUIRED_CHANGE = {'cover_only': 'selected_cover_sha256', 'chapter_update': 'content_sha256',
                    'classification_update': 'classification_sha256', 'volume_title_update': 'serialization_sha256',
                    'introduction_update': 'front_matter_sha256'}


def build_acceptance_plan(cases: list[dict]) -> dict:
    """Freeze exact package expectations; no importer is executed by this function."""
    _require(isinstance(cases, list) and 1 <= len(cases) <= 40, 'acceptance_case_count')
    parsed = [AcceptanceCase.model_validate(c).model_dump() for c in cases]
    _require(len({c['id'] for c in parsed}) == len(parsed), 'duplicate_acceptance_case')
    result = []
    for case in parsed:
        action = case['action']
        before = inspect_package(case['baseline']) if case['baseline'] else None
        _require((before is None) == (action == 'initial'), 'baseline_required_for_update_case')
        try:
            incoming = inspect_package(case['package'])
        except ContractError as exc:
            _require(action == 'reject_invalid' and case['expected_error'] == exc.code, 'unexpected_preflight_failure')
            incoming = None
        _require(action != 'reject_invalid' or incoming is None, 'invalid_case_contains_valid_package')
        _require(action == 'reject_invalid' or case['expected_error'] is None, 'unexpected_expected_error')
        path = Path(case['package'])
        _require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_ARCHIVE, 'invalid_case_file')
        changed = [k for k in before['versions'] if incoming and before['versions'][k] != incoming['versions'][k]] if before else []
        if before and incoming:
            _require((before['book_id'] != incoming['book_id']) == (action == 'different_book'), 'case_book_identity')
            if action == 'different_book':
                _require(before['versions']['content_sha256'] == incoming['versions']['content_sha256']
                         and before['versions']['serialization_sha256'] == incoming['versions']['serialization_sha256'],
                         'different_book_case_must_share_content_and_structure')
            else:
                before_rows, after_rows = before['chapters'], incoming['chapters']
                _require([{k: v for k, v in c.items() if k != 'epub'} for c in before_rows]
                         == [{k: v for k, v in c.items() if k != 'epub'} for c in after_rows], 'case_chapter_identity_or_binding')
                if action in _ALLOWED_CHANGES:
                    _require(set(changed) <= _ALLOWED_CHANGES[action], 'unexpected_component_change')
                    if action in _REQUIRED_CHANGE:
                        _require(_REQUIRED_CHANGE[action] in changed, 'required_component_not_changed')
                    modified = [c['number'] for c, d in zip(before_rows, after_rows) if c['epub']['xhtml_sha256'] != d['epub']['xhtml_sha256']]
                    _require(len(modified) == (1 if action == 'chapter_update' else 0), 'unexpected_chapter_text_change')
                elif action == 'history_replay':
                    _require(before['import_revision_sha256'] != incoming['import_revision_sha256'], 'replay_case_needs_distinct_revisions')
        expected = before if action in {'reject_invalid', 'history_replay'} else incoming
        _require(case['free_trial_end'] < expected['chapter_count'], 'free_window_requires_paid_chapters')
        result.append({**case, 'package': str(path.resolve()), 'baseline': str(Path(case['baseline']).resolve()) if before else None,
                       'package_sha256': digest(path.read_bytes()), 'before': before, 'incoming': incoming,
                       'changed_components': changed,
                       'expected_outcome': ('rejected' if action == 'reject_invalid' else 'history_replay_pending' if action == 'history_replay'
                                            else 'created' if action in {'initial', 'different_book'} else 'unchanged' if action == 'repeat' else 'updated')})
    plan = {'schema_version': 'novel-h5-acceptance-plan.v1', 'cases': result,
            'engine_contract_status': 'passed_for_expected_valid_and_invalid_cases', 'external_import_status': 'pending'}
    return {**plan, 'plan_sha256': object_digest(plan)}


class UserState(_Record):
    count: int = Field(ge=0)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class ChapterRow(_Record):
    chapter_pk: str = Field(min_length=1)
    chapter_id: str
    number: int = Field(ge=1)
    volume_id: str
    volume_pk: str = Field(min_length=1)
    chapter_in_volume: int = Field(ge=1)
    is_free: bool


class VolumeRow(_Record):
    volume_pk: str = Field(min_length=1)
    volume_id: str
    volume_number: int = Field(ge=1)
    title: str
    chapter_start: int = Field(ge=1)
    chapter_end: int = Field(ge=1)


class ObservedState(_Record):
    book_pk: str = Field(min_length=1)
    source_book_id: str
    introduction_count: int = Field(ge=0)
    chapter_rows: list[ChapterRow] = Field(min_length=1, max_length=1000)
    volume_rows: list[VolumeRow] = Field(min_length=1, max_length=100)
    versions: dict[str, str | None]
    progress: UserState
    unlocks: UserState
    collections: UserState


class ImportResult(_Record):
    case_id: str
    package_sha256: str
    outcome: Literal['created', 'updated', 'unchanged', 'rejected', 'history_replay_pending']
    error_code: str = ''
    book_count_before: int = Field(ge=0)
    book_count_after: int = Field(ge=1)
    before: ObservedState | None
    after: ObservedState


class Importer(_Record):
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=160)
    environment: Literal['test']
    executed_at: str = Field(min_length=1)
    trace_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class ImporterReceipt(_Record):
    schema_version: Literal['novel-h5-acceptance-receipt.v1']
    plan_sha256: str
    importer: Importer | None
    results: list[ImportResult] = Field(max_length=40)


def _check_observed(observed, expected, free_trial_end):
    _require(observed['source_book_id'] == expected['book_id'], 'observed_book_identity')
    _require(observed['introduction_count'] == expected['introduction_count'], 'observed_introduction_count')
    _require(observed['versions'] == expected['versions'], 'observed_component_revisions')
    expected_rows = [{k: c[k] for k in ('chapter_id', 'number', 'volume_id', 'chapter_in_volume')}
                     | {'is_free': c['number'] <= free_trial_end} for c in expected['chapters']]
    rows = observed['chapter_rows']
    _require([{k: v for k, v in c.items() if k not in {'chapter_pk', 'volume_pk'}} for c in rows] == expected_rows, 'observed_chapter_mapping_or_free_count')
    _require(len({c['chapter_pk'] for c in rows}) == len(rows), 'duplicate_observed_chapter_pk')
    volumes = observed['volume_rows']
    _require([{key: value for key, value in volume.items() if key != 'volume_pk'} for volume in volumes]
             == expected['volumes'], 'observed_volumes')
    _require(len({v['volume_pk'] for v in volumes}) == len(volumes), 'duplicate_observed_volume_pk')
    mapping = {v['volume_id']: v['volume_pk'] for v in volumes}
    _require(all(c['volume_pk'] == mapping[c['volume_id']] for c in rows), 'observed_volume_foreign_key')


def verify_importer_receipt(plan: dict, receipt: dict) -> dict:
    """Check external test claims, preserving their external provenance explicitly."""
    _require(plan.get('schema_version') == 'novel-h5-acceptance-plan.v1', 'acceptance_plan_version')
    _require(plan['plan_sha256'] == object_digest({k: v for k, v in plan.items() if k != 'plan_sha256'}), 'acceptance_plan_digest')
    rebuilt = build_acceptance_plan([{key: c[key] for key in AcceptanceCase.model_fields} for c in plan['cases']])
    _require(rebuilt == plan, 'acceptance_plan_source_changed')
    parsed = ImporterReceipt.model_validate(receipt).model_dump()
    _require(parsed['plan_sha256'] == plan['plan_sha256'], 'receipt_plan_mismatch')
    ids = {c['id'] for c in plan['cases']}
    supplied = {r['case_id'] for r in parsed['results']}
    _require(len(supplied) == len(parsed['results']) and supplied <= ids, 'duplicate_or_unknown_receipt_case')
    if parsed['results']:
        _require(parsed['importer'] is not None, 'importer_provenance_required')
        from datetime import datetime
        stamp = datetime.fromisoformat(parsed['importer']['executed_at'])
        _require(stamp.tzinfo is not None, 'importer_timestamp_needs_timezone')
    for result in parsed['results']:
        case = next(c for c in plan['cases'] if c['id'] == result['case_id'])
        _require(result['package_sha256'] == case['package_sha256'], 'receipt_package_mismatch')
        _require(result['outcome'] == case['expected_outcome'], 'unexpected_import_outcome')
        if case['action'] != 'reject_invalid':
            _require(result['error_code'] == '', 'unexpected_error_on_successful_import')
        _require(result['book_count_after'] == result['book_count_before'] + (1 if case['expected_outcome'] == 'created' else 0),
                 'unexpected_library_book_count')
        if case['before']:
            _require(result['book_count_before'] >= 1, 'baseline_missing_from_library_count')
            _require(result['before'] is not None, 'observed_baseline_required')
            _check_observed(result['before'], case['before'], case['free_trial_end'])
            for name in ('progress', 'unlocks', 'collections'):
                _require(result['before'][name]['count'] > 0, 'seed_user_state_before_update')
        else:
            _require(result['before'] is None, 'unexpected_observed_baseline')
        expected = case['before'] if case['action'] in {'reject_invalid', 'history_replay'} else case['incoming']
        _check_observed(result['after'], expected, case['free_trial_end'])
        if case['action'] in {'reject_invalid', 'history_replay'}:
            _require(result['after'] == result['before'], 'rejected_or_replayed_import_mutated_state')
            if case['action'] == 'reject_invalid':
                _require(result['error_code'] == case['expected_error'], 'unexpected_rejection_error')
        elif case['action'] == 'different_book':
            _require(result['after']['book_pk'] != result['before']['book_pk'], 'different_books_merged')
            _require(not ({c['chapter_pk'] for c in result['before']['chapter_rows']}
                          & {c['chapter_pk'] for c in result['after']['chapter_rows']}), 'different_book_chapters_merged')
            _require(not ({v['volume_pk'] for v in result['before']['volume_rows']}
                          & {v['volume_pk'] for v in result['after']['volume_rows']}), 'different_book_volumes_merged')
        elif case['before']:
            _require(result['after']['book_pk'] == result['before']['book_pk'], 'update_recreated_book')
            _require(result['after']['chapter_rows'] == result['before']['chapter_rows'], 'update_recreated_chapters')
            _require({v['volume_id']: v['volume_pk'] for v in result['after']['volume_rows']}
                     == {v['volume_id']: v['volume_pk'] for v in result['before']['volume_rows']}, 'update_recreated_volumes')
            for name in ('progress', 'unlocks', 'collections'):
                _require(result['before'][name] == result['after'][name], 'update_lost_user_state')
    missing = sorted(ids - supplied)
    return {'status': 'pending' if missing else 'externally_reported_pass',
            'plan_sha256': plan['plan_sha256'], 'receipt_sha256': object_digest(parsed),
            'checked_cases': sorted(supplied), 'remaining_cases': missing, 'importer': parsed['importer'],
            'current_importer_verified': False,
            'verification_scope': 'consistency_of_external_claims_not_independent_execution_or_authentication'}
