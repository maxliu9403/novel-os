"""Image transport capabilities, separate from the text agent's model.

The deployed Codex ImagegenArgs has no model selector. A transport selector
must not be presented as selection or confirmation of an actual image model.
"""

DEFAULT_IMAGE_MODEL = "gpt-image-2.5-sunburst"
CODEX_IMAGE_MODEL = "codex-image-auto"
LEGACY_CODEX_IMAGE_MODEL = "gpt-image-2"
IMAGE_25_MODELS = ("gpt-image-2.5-sunburst", "gpt-image-2.5-flare")
_MODEL_SNAPSHOTS = {
    "gpt-image-2": "gpt-image-2-2026-04-21",
    "gpt-image-2.5-sunburst": "gpt-image-2.5-sunburst-2026-09-08",
    "gpt-image-2.5-flare": "gpt-image-2.5-flare-2026-09-08",
}


def default_image_model(provider: str = "") -> str:
    return CODEX_IMAGE_MODEL if provider == "codex" else DEFAULT_IMAGE_MODEL


def validate_image_model(model: str, provider: str = "") -> None:
    if not model.strip():
        raise ValueError("Cover image model is not configured")
    if model == CODEX_IMAGE_MODEL and provider != "codex":
        raise ValueError("Codex automatic image selection requires a Codex connection; choose an exact model ID for an image API connection.")
    if model == "gpt-image-2.5":
        raise ValueError(
            "Select an exact image model: gpt-image-2.5-sunburst or gpt-image-2.5-flare. "
            "No model is substituted automatically; use an image API connection for these models."
        )
    if provider == "codex" and model not in {CODEX_IMAGE_MODEL, LEGACY_CODEX_IMAGE_MODEL}:
        raise ValueError(
            "This Codex CLI image tool cannot select an exact image model. "
            "Choose an image API connection to use gpt-image-2.5-sunburst or gpt-image-2.5-flare; "
            "use codex-image-auto for the built-in tool. The legacy gpt-image-2 setting remains a compatibility selector, not verified model identity."
        )


def image_models_match(requested: str, observed: str) -> bool:
    """Accept exact identity or a documented snapshot of the requested alias."""
    return bool(observed) and (observed == requested or _MODEL_SNAPSHOTS.get(requested) == observed)


def image_edit_transport(provider: str) -> str | None:
    """Supported reference-image protocols, never a text-to-image fallback."""
    if provider == "codex":
        return "codex_image_attachment"
    if provider in {"openai", "openai_compatible"}:
        return "images_edits_multipart"
    return None


def image_model_capabilities(provider: str) -> dict:
    codex = provider == "codex"
    return {
        "exact_model_selection": not codex,
        "default_model": default_image_model(provider),
        "model_verification": "unavailable" if codex else "when_reported_by_provider",
        "reference_edit_transport": image_edit_transport(provider),
        "note": (
            "Codex automatically selects the model behind its built-in image tool; this CLI cannot list, select or verify its image model. "
            "Choose an image API connection for exact model selection."
            if codex else "The exact configured model is sent to the image API; actual model identity is verified only when the provider reports it."
        ),
    }
