"""Code-owned plot/arc lifecycle vocabulary, separate from story semantics.

The arc lifecycle is intentionally smaller than the set of words a model may
use when describing a finished character. ``completed`` appeared in a few
historical ending contracts as a terminal synonym for ``resolution``. Keep
that compatibility at the contract boundary instead of teaching the canon
state parser a second terminal state.
"""

PLOT_THREAD_STATUSES = frozenset({"active", "resolved", "abandoned", "foreshadowed"})
ARC_STAGES = frozenset({"beginning", "middle", "climax", "resolution"})
ARC_STAGE_ALIASES = {"completed": "resolution"}


def canonical_arc_stage(value, *, allow_legacy_alias: bool = True) -> str:
    """Return the code-owned arc stage for a contract value."""
    stage = str(value or "").strip().lower()
    if stage in ARC_STAGES:
        return stage
    if allow_legacy_alias and stage in ARC_STAGE_ALIASES:
        return ARC_STAGE_ALIASES[stage]
    raise ValueError(
        "ending_contract character arc required_arc_stage must be one of "
        f"{sorted(ARC_STAGES)}; got {value!r}. "
        "Use required_outcome for the semantic result, not a second lifecycle state."
    )


def normalize_ending_arc_stages(contract):
    """Copy a newly parsed ending contract and canonicalize legacy aliases.

    Existing persisted contracts are read through ``canonical_arc_stage`` and
    are not rewritten, so their original bytes and hashes remain auditable.
    """
    if not isinstance(contract, dict) or not contract.get("enforce"):
        return contract
    import copy

    result = copy.deepcopy(contract)
    arcs = result.get("character_arcs")
    if isinstance(arcs, list):
        for arc in arcs:
            if isinstance(arc, dict) and "required_arc_stage" in arc:
                arc["required_arc_stage"] = canonical_arc_stage(
                    arc["required_arc_stage"], allow_legacy_alias=True
                )
    return result


def validate_ending_arc_stages(contract) -> None:
    """Validate stage vocabulary while accepting the legacy alias."""
    if not isinstance(contract, dict) or not contract.get("enforce"):
        return
    arcs = contract.get("character_arcs")
    if arcs is None:
        return
    if not isinstance(arcs, list):
        raise ValueError("ending_contract.character_arcs must be a list")
    for arc in arcs:
        if not isinstance(arc, dict):
            raise ValueError("ending_contract.character_arcs entries must be objects")
        if "required_arc_stage" in arc:
            canonical_arc_stage(arc["required_arc_stage"], allow_legacy_alias=True)


def validate_ending_statuses(contract):
    """Validate newly generated contracts; never rewrite saved author obligations."""
    if not isinstance(contract, dict) or not contract.get("enforce"):
        return
    validate_ending_arc_stages(contract)
    for key in ("main_conflict", "antagonist_outcome"):
        item = contract.get(key)
        if key == "antagonist_outcome" and (
            not isinstance(item, dict) or not item.get("required") or not item.get("thread_id")
        ):
            continue
        if not isinstance(item, dict):
            raise ValueError(f"ending_contract.{key} must be an object")
        status = item.get("required_status", "resolved")
        if not isinstance(status, str) or status.strip().lower() not in PLOT_THREAD_STATUSES:
            raise ValueError(
                f"ending_contract.{key}.required_status must be one of "
                f"{sorted(PLOT_THREAD_STATUSES)}; got {status!r}. "
                "Keep the story-specific outcome in protagonist_choice/consequence, not in a lifecycle status."
            )
