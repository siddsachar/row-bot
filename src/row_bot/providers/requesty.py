"""Requesty OpenAI-compatible provider helpers.

Requesty (https://requesty.ai) is an OpenAI-compatible LLM gateway that uses
the same ``provider/model`` naming as OpenRouter (e.g. ``openai/gpt-4o-mini``).
Its ``/v1/models`` endpoint returns an OpenAI-shaped catalog, but the capability
fields differ from OpenRouter: it exposes ``context_window`` (not
``context_length``) and ``supports_tool_calling`` / ``supports_reasoning`` /
``supports_vision`` booleans (not a ``supported_parameters`` array).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

REQUESTY_PROVIDER_ID = "requesty"
REQUESTY_BASE_URL = "https://router.requesty.ai/v1"


def requesty_models_url() -> str:
    return f"{REQUESTY_BASE_URL}/models"


def requesty_chat_url() -> str:
    return f"{REQUESTY_BASE_URL}/chat/completions"


def requesty_context_window(metadata: Mapping[str, Any], fallback: int = 0) -> int:
    """Resolve a context window from Requesty's ``context_window`` field.

    Falls back to OpenRouter-style ``context_length`` for robustness, then to
    the supplied ``fallback``.
    """
    for key in ("context_window", "contextWindow", "context_length", "max_tokens"):
        value = metadata.get(key)
        if value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return int(fallback or 0)


def requesty_tool_calling(metadata: Mapping[str, Any]) -> bool | None:
    """Map Requesty's ``supports_tool_calling`` boolean to tool support."""
    for key in ("supports_tool_calling", "supportsToolCalling", "tool_calling"):
        value = metadata.get(key)
        if isinstance(value, bool):
            return value
    return None


def requesty_supports_vision(metadata: Mapping[str, Any]) -> bool | None:
    """Map Requesty's ``supports_vision`` boolean to image-input support."""
    for key in ("supports_vision", "supportsVision", "vision"):
        value = metadata.get(key)
        if isinstance(value, bool):
            return value
    return None
