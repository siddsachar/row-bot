"""Capability overlap detection for external MCP servers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from mcp_client.safety import sanitize_name_component


CORE_NATIVE_CAPABILITIES: dict[str, dict[str, str]] = {
    "memory": {"label": "Thoth Memory", "message": "Thoth Memory is the canonical user/project memory. External memory MCPs stay separate."},
    "browser": {"label": "Thoth Browser", "message": "Thoth already includes visible browser automation. Use this MCP only when you want its specific tool surface."},
    "filesystem": {"label": "Thoth Filesystem", "message": "Thoth already has native local file/document tools. Keep external file tools scoped and review writes."},
    "documents": {"label": "Thoth Documents", "message": "Thoth already has native document tools. Use this MCP for a specific external conversion or workspace surface."},
    "web_search": {"label": "Thoth Web Search", "message": "Thoth already has web/search tools. Use this MCP when you want this provider specifically."},
    "url_reader": {"label": "Thoth URL Reader", "message": "Thoth can already read URLs. Use this MCP only for provider-specific fetch behavior."},
    "channels": {"label": "Thoth Channels", "message": "Thoth has native channel concepts. Review messaging/send tools before enabling external channel MCPs."},
    "designer": {"label": "Thoth Designer", "message": "Thoth has native design-generation flows. Use this MCP for source design-system context."},
}

_HEURISTICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("memory", ("memory", "remember", "knowledge graph", "long term")),
    ("browser", ("browser", "playwright", "chrome", "devtools", "puppeteer", "web automation")),
    ("filesystem", ("filesystem", "file system", "files", "directory", "local file")),
    ("documents", ("document", "pdf", "markdown", "office", "docx", "notion")),
    ("web_search", ("web search", "search engine", "tavily", "brave", "firecrawl", "exa")),
    ("url_reader", ("fetch", "url", "web content")),
    ("channels", ("slack", "discord", "telegram", "microsoft 365", "gmail", "mail", "calendar")),
    ("designer", ("figma", "design", "designer")),
)


@dataclass(frozen=True)
class CapabilityConflict:
    capability: str
    label: str
    severity: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"capability": self.capability, "label": self.label, "severity": self.severity, "message": self.message}


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _server_source(server_cfg: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(server_cfg, dict):
        return {}
    source = server_cfg.get("source")
    return source if isinstance(source, dict) else {}


def _entry_text(entry: Any) -> str:
    parts: list[str] = []
    for attr in ("id", "name", "description", "publisher", "classification", "category", "action_scope"):
        parts.append(str(getattr(entry, attr, "") or ""))
    parts.extend(_as_list(getattr(entry, "capabilities", [])))
    return " ".join(parts).lower()


def conflicts_for_capabilities(capabilities: list[str], *, text: str = "") -> list[CapabilityConflict]:
    normalized = {sanitize_name_component(item) for item in capabilities if item}
    text_value = text.lower()
    for capability, needles in _HEURISTICS:
        if capability in normalized:
            continue
        if any(re.search(rf"\b{re.escape(needle)}\b", text_value) for needle in needles):
            normalized.add(capability)
    conflicts: list[CapabilityConflict] = []
    for capability in sorted(normalized):
        meta = CORE_NATIVE_CAPABILITIES.get(capability)
        if not meta:
            continue
        severity = "high" if capability == "memory" else "warning"
        conflicts.append(CapabilityConflict(capability=capability, label=meta["label"], severity=severity, message=meta["message"]))
    return conflicts


def conflicts_for_entry(entry: Any) -> list[CapabilityConflict]:
    return conflicts_for_capabilities(_as_list(getattr(entry, "overlaps_native", [])), text=_entry_text(entry))


def conflicts_for_server(server_name: str, server_cfg: dict[str, Any] | None) -> list[CapabilityConflict]:
    source = _server_source(server_cfg)
    overlaps = _as_list(source.get("overlaps_native"))
    capabilities = _as_list(source.get("capabilities"))
    text = " ".join([server_name, str(source.get("id") or ""), str(source.get("name") or ""), str(source.get("category") or ""), " ".join(capabilities)])
    return conflicts_for_capabilities(overlaps, text=text)


def requires_manual_tool_selection(server_name: str, server_cfg: dict[str, Any] | None) -> bool:
    conflicts = conflicts_for_server(server_name, server_cfg)
    if conflicts:
        return True
    source = _server_source(server_cfg)
    return str(source.get("risk_level") or "").lower() == "high"


def unique_server_name(base_name: str, existing_names: list[str] | set[str]) -> str:
    base = sanitize_name_component(base_name).replace("_", "-") or "mcp-server"
    existing = set(existing_names)
    if base not in existing:
        return base
    counter = 2
    while f"{base}-{counter}" in existing:
        counter += 1
    return f"{base}-{counter}"