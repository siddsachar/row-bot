"""MCP CallToolResult normalization."""

from __future__ import annotations

import json
from typing import Any


def _content_block_to_text(block: Any) -> str:
    text = getattr(block, "text", None)
    if text is not None:
        return str(text)
    block_type = getattr(block, "type", None)
    if block_type == "image" or hasattr(block, "mimeType"):
        mime = getattr(block, "mimeType", "") or "binary"
        return f"[MCP {block_type or 'content'} omitted: {mime}]"
    resource = getattr(block, "resource", None)
    if resource is not None:
        uri = getattr(resource, "uri", "resource")
        resource_text = getattr(resource, "text", None)
        if resource_text is not None:
            return f"[Embedded resource: {uri}]\n{resource_text}"
        return f"[Embedded resource omitted: {uri}]"
    uri = getattr(block, "uri", None)
    if uri is not None:
        return f"[MCP resource link: {uri}]"
    return str(block)


def normalize_call_result(result: Any, *, output_limit: int = 24000) -> str:
    """Convert an MCP call result into model-facing text."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        parts.append(_content_block_to_text(block))
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        try:
            parts.append("STRUCTURED_CONTENT:\n" + json.dumps(structured, ensure_ascii=False, indent=2, default=str))
        except Exception:
            parts.append(f"STRUCTURED_CONTENT:\n{structured!r}")
    text = "\n\n".join(part for part in parts if part).strip()
    if getattr(result, "isError", False):
        text = "MCP tool error: " + (text or "tool returned an error")
    if not text:
        text = "MCP tool completed with no content."
    if output_limit > 0 and len(text) > output_limit:
        text = text[:output_limit] + f"\n\n[Truncated MCP output at {output_limit} characters]"
    return text