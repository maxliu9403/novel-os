"""Effective UTF-8 planning request budget for the selected connection."""
from core.gemini_gateway import ATTACHMENT_LIMIT_BYTES, INPUT_LIMIT_BYTES


def input_limit(client, *, negotiate: bool = False) -> int | None:
    """Unknown capacity stays unknown. Only a gateway can advertise its attachment mode."""
    configured = getattr(client, "planning_input_bytes", None)
    provider = getattr(client, "provider_name", None) or getattr(client, "provider", None)
    if provider != "gemini_openai":
        return configured
    limit = INPUT_LIMIT_BYTES
    capability = getattr(client, "text_input_limit_bytes", None)
    if negotiate and callable(capability) and (configured is None or configured > limit):
        advertised = capability()
        if type(advertised) is int and limit <= advertised <= ATTACHMENT_LIMIT_BYTES:
            limit = advertised
    return min(limit, configured) if configured is not None else limit
