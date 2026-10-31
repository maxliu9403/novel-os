"""Read-only, cached verification of a published ZIP and its frozen sources."""
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import re

try:
    from .h5_acceptance import inspect_package_bytes
    from .h5_contract import PROFILE, ContractError, require, safe_path, strict_json
    from .h5_import import object_digest
    from .safe_project_io import project_file_stamp, read_project_bytes
except ImportError:
    from h5_acceptance import inspect_package_bytes
    from h5_contract import PROFILE, ContractError, require, safe_path, strict_json
    from h5_import import object_digest
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
            and value.get('target_profile') == PROFILE.id
            and value.get('validator_revision') == PROFILE.validator_revision
            and value.get('catalog_sha256') == PROFILE.catalog_sha256
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
    inspect_package_bytes(raw)  # A forged local receipt must never bypass download validation.
    return expected


def validated_package_sha(project: Path) -> str:
    """Return the verified byte digest only if all dependencies remain stable."""
    dependencies = contract_dependencies(project)
    if dependencies == (None,):
        raise ContractError('delivery_validation_missing')
    value = _verified(str(project), dependencies)
    require(contract_dependencies(project) == dependencies, 'delivery_source_changed')
    return value
