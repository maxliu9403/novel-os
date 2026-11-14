"""Read-only, cached verification of a published ZIP and its frozen sources."""
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import re

try:
    from .h5_acceptance import inspect_package_bytes
    from .h5_contract import PROFILE, ContractError, require, safe_path, strict_json
    from .h5_import import object_digest
    from .novel_classification import NovelClassification
    from .publication_taxonomy import export_target
    from .safe_project_io import project_file_stamp, read_project_bytes
except ImportError:
    from h5_acceptance import inspect_package_bytes
    from h5_contract import PROFILE, ContractError, require, safe_path, strict_json
    from h5_import import object_digest
    from novel_classification import NovelClassification
    from publication_taxonomy import export_target
    from safe_project_io import project_file_stamp, read_project_bytes

RECEIPT = ('outputs', 'publication', 'export-validation.json')
ARCHIVE = ('outputs', 'deliverables', 'book-package.zip')


@lru_cache(maxsize=256)
def _receipt(project: str, stamp: tuple | None):
    del stamp
    raw = read_project_bytes(Path(project), RECEIPT, missing_ok=True, max_bytes=PROFILE.document_bytes)
    if raw is None:
        raise ContractError('delivery_validation_missing')
    value = strict_json(raw)
    require(isinstance(value, dict) and value.get('schema_version') == 'novel-h5-export-receipt.v1'
            and value.get('engine_contract_status') == 'passed', 'delivery_validation_version')
    sources = value.get('sources')
    require(isinstance(sources, dict) and bool(sources), 'delivery_source_receipt')
    require(len(sources) <= PROFILE.zip_entries + PROFILE.spine_items + 32, 'delivery_source_receipt')
    require(all(safe_path(p) and p.startswith('outputs/') for p in sources), 'delivery_source_receipt')
    require(all(v is None or isinstance(v, str) and re.fullmatch(r'[0-9a-f]{64}', v)
                for v in sources.values()), 'delivery_source_receipt')
    require(isinstance(value.get('package_sha256'), str)
            and re.fullmatch(r'[0-9a-f]{64}', value['package_sha256']), 'delivery_source_receipt')
    require(object_digest(sources) == value.get('source_snapshot_sha256'), 'delivery_source_receipt')
    return value


def _require_export_target(receipt: dict, target: dict) -> None:
    require(all(receipt.get(key) == target.get(key) for key in (
        'target_profile', 'validator_revision', 'catalog_sha256', 'required_h5_capabilities',
    )), 'delivery_validation_version')


def _bound_export_target(project: Path, receipt: dict) -> dict:
    """Resolve the target from published, hash-bound metadata, not mutable settings.

    The manifest decides whether classification is included. A leftover sidecar
    from an earlier export must not change an explicitly unclassified package.
    This runs in the source-dependency cache, never the receipt-only cache.
    """
    def document(relative: str):
        expected = receipt['sources'].get(relative)
        require(expected is not None, 'delivery_source_receipt', path=relative)
        raw = read_project_bytes(project, tuple(relative.split('/')), max_bytes=PROFILE.document_bytes)
        require(sha256(raw).hexdigest() == expected, 'delivery_source_changed', path=relative)
        return strict_json(raw)

    manifest = document('outputs/deliverables/package-manifest.json')
    require(isinstance(manifest, dict) and manifest.get('schema_version') == 4,
            'delivery_source_receipt')
    pointer = manifest.get('classification')
    require(isinstance(pointer, dict), 'delivery_source_receipt')
    path = pointer.get('path')
    if path == '':
        require(pointer.get('classification_id') == '', 'delivery_source_receipt')
        return export_target(None)
    require(isinstance(path, str) and safe_path(path), 'delivery_source_receipt')
    classification = document('outputs/deliverables/' + path)
    require(isinstance(classification, dict), 'classification_contract')
    try:
        canonical = NovelClassification.from_dict(classification).to_dict()
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ContractError('classification_contract') from exc
    require(canonical == classification, 'classification_contract')
    require(pointer.get('classification_id') == classification.get('classification_id'),
            'classification_identity')
    return export_target(classification)


def contract_dependencies(project: Path) -> tuple:
    """Cheap fd-based stamps; no file writes, ZIP reads or model calls."""
    receipt_stamp = project_file_stamp(project, RECEIPT)
    if receipt_stamp is None:
        return (None,)
    receipt = _receipt(str(project), receipt_stamp)
    return (receipt_stamp, project_file_stamp(project, ARCHIVE),
            tuple((p, project_file_stamp(project, tuple(p.split('/')))) for p in sorted(receipt['sources'])))


@lru_cache(maxsize=256)
def _bound_package(project: str, dependencies: tuple) -> str:
    folder = Path(project)
    receipt = _receipt(project, dependencies[0])
    for path, expected in receipt['sources'].items():
        raw = read_project_bytes(folder, tuple(path.split('/')), missing_ok=True, max_bytes=PROFILE.zip_expanded)
        actual = None if raw is None else sha256(raw).hexdigest()
        require(actual == expected, 'delivery_source_changed', path=path)
    _require_export_target(receipt, _bound_export_target(folder, receipt))
    raw = read_project_bytes(folder, ARCHIVE, max_bytes=PROFILE.zip_bytes)
    actual = sha256(raw).hexdigest()
    require(actual == receipt.get('package_sha256'), 'delivery_package_changed')
    return actual


def current_package_binding_sha(project: Path) -> str:
    """List projection: verify the saved receipt's bytes, without image decoding."""
    dependencies = contract_dependencies(project)
    if dependencies == (None,):
        raise ContractError('delivery_validation_missing')
    value = _bound_package(str(project), dependencies)
    require(contract_dependencies(project) == dependencies, 'delivery_source_changed')
    return value


@lru_cache(maxsize=256)
def _verified(project: str, dependencies: tuple) -> str:
    expected = _bound_package(project, dependencies)
    raw = read_project_bytes(Path(project), ARCHIVE, max_bytes=PROFILE.zip_bytes)
    require(sha256(raw).hexdigest() == expected, 'delivery_package_changed')
    # A forged local receipt must never bypass independent download validation.
    report = inspect_package_bytes(raw)
    _require_export_target(_receipt(project, dependencies[0]), report)
    return expected


def validated_package_sha(project: Path) -> str:
    """Return the verified byte digest only if all dependencies remain stable."""
    dependencies = contract_dependencies(project)
    if dependencies == (None,):
        raise ContractError('delivery_validation_missing')
    value = _verified(str(project), dependencies)
    require(contract_dependencies(project) == dependencies, 'delivery_source_changed')
    return value
