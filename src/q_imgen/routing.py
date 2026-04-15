"""Pure routing helpers for q-imgen engine selection."""

from __future__ import annotations

ENGINE_ALIASES = {
    "mj": "midjourney",
    "banana": "nanobanana",
}

VALID_ENGINES = {"midjourney", "nanobanana"}


def canonicalize_engine(name: str) -> str:
    canonical = ENGINE_ALIASES.get(name, name)
    if canonical not in VALID_ENGINES:
        raise ValueError(f"Unsupported engine: {name}")
    return canonical


def choose_engine(
    *,
    explicit_engine: str | None = None,
    operation: str = "generate",
    reference_image_count: int = 0,
    priority: str = "balanced",
    style: str | None = None,
) -> str:
    """Choose an engine using q-imgen's documented routing heuristics."""
    if explicit_engine:
        return canonicalize_engine(explicit_engine)

    normalized_operation = operation.strip().lower()
    normalized_priority = priority.strip().lower()
    normalized_style = (style or "").strip().lower()

    if normalized_operation in {"status", "action", "upscale", "variation", "reroll"}:
        return "midjourney"

    if normalized_operation == "batch":
        return "nanobanana"

    if reference_image_count > 0:
        return "nanobanana"

    if normalized_priority == "speed":
        return "nanobanana"

    if normalized_priority == "quality":
        return "midjourney"

    if "anime" in normalized_style:
        return "midjourney"

    return "midjourney"
