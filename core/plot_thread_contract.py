"""Code-owned plot lifecycle vocabulary, separate from a story's semantic ending."""

PLOT_THREAD_STATUSES = frozenset({"active", "resolved", "abandoned", "foreshadowed"})


def validate_ending_statuses(contract):
    """Validate newly generated contracts; never rewrite saved author obligations."""
    if not isinstance(contract, dict) or not contract.get("enforce"):
        return
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
