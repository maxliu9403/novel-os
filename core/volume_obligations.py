"""Compile serialization duties into a chapter plan, without asserting delivery.

The application owns milestone identifiers and their due chapters. These bindings
say what the chapter is planned to do; they are not observations of manuscript
events or evidence that a reader promise has already been fulfilled.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from typing import Any, Mapping, Sequence

try:
    from .contracts import ChapterContract
    from .narrative_format import NarrativeFormat, obligations_for_chapter, validate_volume_contracts
except ImportError:  # Top-level imports in the command-line pipeline.
    from contracts import ChapterContract
    from narrative_format import NarrativeFormat, obligations_for_chapter, validate_volume_contracts


VERSION = 'volume-obligation-binding.v1'


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def bind_planned_volume_obligations(
    contract: ChapterContract,
    volumes: Sequence[Mapping[str, Any]],
    *,
    format_contract: NarrativeFormat,
) -> tuple[ChapterContract, dict[str, Any]]:
    """Return a derived contract and a deterministic, source-bound receipt.

    ``volumes`` must be the complete canonical ``validate_volume_contracts``
    result for this format. Rechecking that boundary prevents invented event IDs
    or a partial volume list from becoming application-owned obligations. Existing
    event IDs retain their exact order, including unrecognized IDs and duplicates.
    """
    span = format_contract.volume_for_chapter(contract.chapter)
    supplied = list(volumes)
    canonical = validate_volume_contracts(format_contract, supplied)
    if supplied != canonical:
        raise ValueError('Planned obligations require canonical validated volume contracts')
    due = list(obligations_for_chapter(canonical, contract.chapter)) if format_contract.is_long_form else []
    events = list(contract.world_event_ids)
    added = []
    for obligation in due:
        identifier = obligation['event_id']
        if identifier not in events:
            events.append(identifier)
            added.append(identifier)
    effective = replace(contract, world_event_ids=tuple(events)) if added else contract
    receipt = {
        'schema_version': VERSION,
        'kind': 'planned_volume_obligations',
        'evidence_scope': 'planned_not_observed',
        'chapter': contract.chapter,
        'volume_id': span.volume_id,
        'source_contract_id': contract.contract_id,
        'effective_contract_id': effective.contract_id,
        'source_contract_sha256': _sha(contract.to_dict()),
        'effective_contract_sha256': _sha(effective.to_dict()),
        'narrative_format_id': format_contract.format_id,
        'volume_contracts_sha256': _sha(canonical),
        'source_world_event_ids': list(contract.world_event_ids),
        'due_obligations': deepcopy(due),
        'added_event_ids': added,
    }
    return effective, receipt
