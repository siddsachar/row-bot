"""Bounded framework-neutral projection of public transcript content.

The checkpoint remains the transcript owner.  This module recognizes only
reviewed public text markers and emits inert data for both UI clients; it never
renders HTML or executes content supplied by a model or tool.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


MAX_BLOCKS = 256
MAX_MERMAID_CHARS = 64 * 1024
MAX_CHART_JSON_CHARS = 128 * 1024

_YOUTUBE_RE = re.compile(
    r"(?:\[([^\]\n]{0,240})\]\()?"
    r"(https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})[^\s)\]]*)\)?",
    re.IGNORECASE,
)
_MERMAID_FENCE_RE = re.compile(
    r"```mermaid\s*\n([\s\S]*?)\n```", re.IGNORECASE
)
_MERMAID_START_RE = re.compile(
    r"^(graph|flowchart|sequenceDiagram|classDiagram|erDiagram|journey|gantt|"
    r"stateDiagram(?:-v2)?|mindmap|timeline|pie)\b",
    re.IGNORECASE,
)
_REMOTE_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)")
_B64_SEGMENT_RE = re.compile(r"[A-Za-z0-9+/=]{100,}")
_AUTOLINK_RE = re.compile(
    r"```[\s\S]*?```|`[^`\n]+`|\[[^\]]*\]\([^)]+\)|<https?://[^>]+>|"
    r"(https?://[^\s<>\)\]\"']+)"
)


def _block_id(row_id: str, order: int, kind: str, value: str) -> str:
    digest = hashlib.sha256(
        f"{row_id}\0{order}\0{kind}\0{value}".encode("utf-8")
    ).hexdigest()[:24]
    return f"block:{digest}"


def sanitize_remote_images(text: str, *, allow_safe_images: bool = True) -> str:
    """Prevent suspicious remote Markdown images from auto-fetching secrets."""

    def replace(match: re.Match[str]) -> str:
        alt, url = match.group(1) or "image", match.group(2)
        query = url.find("?")
        if (query >= 0 and len(url) - query > 200) or _B64_SEGMENT_RE.search(url):
            return f"⚠ *Blocked suspicious image link* — [{alt}]({url})"
        if allow_safe_images:
            return match.group(0)
        # The React shell CSP does not auto-fetch remote media. Preserve the
        # URL as an explicit user action rather than weakening that boundary.
        return f"[{alt or 'Remote image'}]({url})"

    return _REMOTE_IMAGE_RE.sub(replace, text)


def autolink_urls(text: str) -> str:
    """Wrap bare HTTP(S) URLs while preserving code and existing links."""

    def replace(match: re.Match[str]) -> str:
        url = match.group(1)
        if not url:
            return match.group(0)
        trailing = ""
        if url[-1] in ".,;:!?":
            trailing, url = url[-1], url[:-1]
        return f"[{url}]({url}){trailing}"

    return _AUTOLINK_RE.sub(replace, text) if "http" in text else text


def _chart(text: str) -> tuple[dict[str, Any] | None, str]:
    if not text.startswith("__CHART__:"):
        return None, text
    marker = text.find("\n\n", len("__CHART__:"))
    raw = text[len("__CHART__:") :] if marker < 0 else text[len("__CHART__:") : marker]
    fallback = "Chart created" if marker < 0 else text[marker + 2 :].strip() or "Chart created"
    if len(raw) > MAX_CHART_JSON_CHARS:
        return None, f"{fallback}\n\nChart data is too large to display."
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, f"{fallback}\n\nChart data is unavailable."
    if not isinstance(value, dict) or not isinstance(value.get("data"), list):
        return None, f"{fallback}\n\nChart data is unavailable."
    if len(value["data"]) > 128 or any(not isinstance(item, dict) for item in value["data"]):
        return None, f"{fallback}\n\nChart data is unavailable."
    layout = value.get("layout", {})
    if not isinstance(layout, dict):
        return None, f"{fallback}\n\nChart data is unavailable."
    canonical = json.dumps(
        {"data": value["data"], "layout": layout},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(canonical) > MAX_CHART_JSON_CHARS:
        return None, f"{fallback}\n\nChart data is too large to display."
    return {"figure_json": canonical, "text": fallback[:4096]}, ""


def _segments(text: str) -> list[tuple[str, dict[str, Any] | str]]:
    matches: list[tuple[int, int, str, Any]] = []
    for match in _MERMAID_FENCE_RE.finditer(text):
        source = match.group(1).strip()
        if source and len(source) <= MAX_MERMAID_CHARS:
            matches.append((match.start(), match.end(), "mermaid", source))
    for match in _YOUTUBE_RE.finditer(text):
        if any(start <= match.start() < end for start, end, _, _ in matches):
            continue
        matches.append(
            (
                match.start(),
                match.end(),
                "youtube",
                {
                    "video_id": match.group(3),
                    "url": match.group(2)[:2048],
                    "title": (match.group(1) or "YouTube video")[:240],
                },
            )
        )
    matches.sort(key=lambda value: value[0])
    output: list[tuple[str, dict[str, Any] | str]] = []
    cursor = 0
    for start, end, kind, value in matches:
        if start < cursor:
            continue
        before = text[cursor:start]
        if before.strip():
            output.append(("markdown", autolink_urls(sanitize_remote_images(before, allow_safe_images=False))))
        output.append((kind, value))
        cursor = end
    tail = text[cursor:]
    if tail.strip():
        output.append(("markdown", autolink_urls(sanitize_remote_images(tail, allow_safe_images=False))))
    return output or [("markdown", autolink_urls(sanitize_remote_images(text, allow_safe_images=False)))]


def canonical_transcript_blocks(row: dict[str, Any]) -> dict[str, Any]:
    """Return a copied row with ordered, stable, bounded public blocks."""

    raw = row.get("blocks")
    if not isinstance(raw, list) or row.get("content_status") == "lazy":
        return dict(row)
    row_id = str(row.get("id") or "row")[:1024]
    projected: list[dict[str, Any]] = []
    for raw_block in raw[:MAX_BLOCKS]:
        if not isinstance(raw_block, dict):
            continue
        if raw_block.get("type") == "attachment":
            if len(projected) < MAX_BLOCKS:
                projected.append(dict(raw_block))
            continue
        if raw_block.get("type") != "text":
            continue
        text = str(raw_block.get("text") or "")
        chart, remaining = _chart(text)
        candidates: list[tuple[str, dict[str, Any] | str]]
        if chart is not None:
            candidates = [("chart", chart)]
        else:
            candidates = _segments(auto_fence_mermaid(remaining))
        for kind, value in candidates:
            if len(projected) >= MAX_BLOCKS:
                break
            order = len(projected)
            identity_value = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
            block: dict[str, Any] = {
                "id": _block_id(row_id, order, kind, identity_value),
                "type": kind,
            }
            if kind == "markdown":
                block["text"] = str(value)
            elif kind == "mermaid":
                block["source"] = str(value)
                block["text"] = f"```mermaid\n{value}\n```"
            else:
                block.update(value if isinstance(value, dict) else {})
            projected.append(block)
    return {**row, "blocks": projected}


def auto_fence_mermaid(text: str) -> str:
    """Shared conservative normalization retained by the NiceGUI renderer."""

    if not text or "```mermaid" in text.lower():
        return text
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if _MERMAID_START_RE.match(line.strip())), None)
    if start is None:
        return text
    def continuation(line: str) -> bool:
        value = line.strip()
        if not value:
            return True
        lowered = value.lower()
        if lowered.startswith((
            "graph ", "flowchart ", "sequencediagram", "classdiagram",
            "erdiagram", "journey", "gantt", "statediagram", "mindmap",
            "timeline", "pie", "subgraph", "end", "classdef ", "class ",
            "style ", "linkstyle ", "click ", "direction ", "%%",
        )):
            return True
        return any(token in value for token in (
            "-->", "---", "-.->", "==>", "<--", "<->", ":::", "|", "[", "]", "(", ")", "{", "}",
        ))

    end = len(lines)
    body_lines: list[str] = []
    for index in range(start, len(lines)):
        if continuation(lines[index]):
            body_lines.append(lines[index])
        else:
            end = index
            break
    body = "\n".join(body_lines).strip()
    if len(body) > MAX_MERMAID_CHARS or ("-->" not in body and "subgraph" not in body.lower()):
        return text
    prefix = "\n".join(lines[:start]).rstrip()
    suffix = "\n".join(lines[end:]).strip()
    fenced = f"```mermaid\n{body}\n```"
    return "\n\n".join(part for part in (prefix, fenced, suffix) if part)
