from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage, SystemMessage


ANTHROPIC_CACHE_MARKER: dict[str, str] = {"type": "ephemeral"}


@dataclass(frozen=True)
class PromptCacheMarkerResult:
    provider_id: str
    applied: bool
    marker_count: int
    eligible_system_count: int
    stable_fingerprint: str = ""
    reason: str = ""


def content_has_cache_control(content: Any) -> bool:
    return isinstance(content, list) and any(
        isinstance(block, dict) and "cache_control" in block
        for block in content
    )


def _int_value(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _mapping_value(mapping: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = mapping
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_int(mapping: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> int | None:
    for path in paths:
        value = _int_value(_mapping_value(mapping, path))
        if value is not None:
            return value
    return None


def normalize_prompt_cache_usage(response_metadata: dict[str, Any] | None) -> dict[str, int]:
    """Return prompt-cache token counts from provider response metadata."""
    metadata = dict(response_metadata or {})
    usage = metadata.get("token_usage")
    if not isinstance(usage, dict):
        usage = metadata.get("usage")
    if not isinstance(usage, dict):
        usage = metadata.get("usage_metadata")
    if not isinstance(usage, dict):
        usage = metadata

    read_tokens = _first_int(
        usage,
        (
            ("cache_read_input_tokens",),
            ("prompt_cache_read_tokens",),
            ("input_cached_tokens",),
            ("cached_tokens",),
            ("input_tokens_details", "cached_tokens"),
            ("prompt_tokens_details", "cached_tokens"),
        ),
    )
    write_tokens = _first_int(
        usage,
        (
            ("cache_creation_input_tokens",),
            ("cache_write_input_tokens",),
            ("prompt_cache_write_tokens",),
            ("input_cache_creation_tokens",),
            ("input_tokens_details", "cache_creation_tokens"),
            ("prompt_tokens_details", "cache_creation_tokens"),
        ),
    )

    normalized: dict[str, int] = {}
    if read_tokens is not None:
        normalized["prompt_cache_read_tokens"] = read_tokens
    if write_tokens is not None:
        normalized["prompt_cache_write_tokens"] = write_tokens
    return normalized


def _with_anthropic_cache_marker(message: SystemMessage) -> SystemMessage:
    content = message.content
    if isinstance(content, str):
        next_content: Any = [
            {
                "type": "text",
                "text": content,
                "cache_control": dict(ANTHROPIC_CACHE_MARKER),
            }
        ]
    elif isinstance(content, list):
        next_content = list(content)
        for index in range(len(next_content) - 1, -1, -1):
            block = next_content[index]
            if isinstance(block, dict) and str(block.get("type") or "text") == "text":
                next_content[index] = {
                    **block,
                    "cache_control": dict(ANTHROPIC_CACHE_MARKER),
                }
                break
        else:
            next_content.append({
                "type": "text",
                "text": "",
                "cache_control": dict(ANTHROPIC_CACHE_MARKER),
            })
    else:
        next_content = [
            {
                "type": "text",
                "text": str(content or ""),
                "cache_control": dict(ANTHROPIC_CACHE_MARKER),
            }
        ]

    try:
        return message.model_copy(update={"content": next_content})
    except AttributeError:
        return message.copy(update={"content": next_content})


def apply_anthropic_system_cache_marker(
    messages: list[BaseMessage],
    *,
    provider_id: str | None,
    cache_eligible_system_message_ids: set[int],
    stable_fingerprint: str = "",
) -> tuple[list[BaseMessage], PromptCacheMarkerResult]:
    provider = str(provider_id or "")
    eligible_indices = [
        index
        for index, message in enumerate(messages)
        if isinstance(message, SystemMessage) and id(message) in cache_eligible_system_message_ids
    ]
    if provider != "anthropic":
        return list(messages), PromptCacheMarkerResult(
            provider_id=provider,
            applied=False,
            marker_count=0,
            eligible_system_count=len(eligible_indices),
            stable_fingerprint=stable_fingerprint,
            reason="provider_not_anthropic",
        )
    if not eligible_indices:
        return list(messages), PromptCacheMarkerResult(
            provider_id=provider,
            applied=False,
            marker_count=0,
            eligible_system_count=0,
            stable_fingerprint=stable_fingerprint,
            reason="no_cache_eligible_system",
        )

    target_index = eligible_indices[-1]
    marked_messages = list(messages)
    marked_messages[target_index] = _with_anthropic_cache_marker(marked_messages[target_index])
    return marked_messages, PromptCacheMarkerResult(
        provider_id=provider,
        applied=True,
        marker_count=1,
        eligible_system_count=len(eligible_indices),
        stable_fingerprint=stable_fingerprint,
        reason="stable_system_breakpoint",
    )
