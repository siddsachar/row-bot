"""Reviewed discovery eligibility; availability never grants runtime authority."""
from __future__ import annotations

# Public contracts checked 2026-10-03; detailed evidence in docs/INTEGRATION_SOURCES.md.
SOURCES = {
    "recommended": ("mcp", "local", "eligible", "Vendor setup metadata; live accounts untested."),
    "official": ("mcp", "snapshot", "eligible", "Local Registry v0.1 snapshot; explicit development refresh only."),
    "hermes_mcp": ("mcp", "public", "eligible", "Pinned recipe metadata; inspect before setup."),
    "clawhub": ("skill", "public", "eligible", "Public v1 skill search and complete version downloads."),
    "github": ("skill", "public", "eligible", "Maintainer skill repositories through the existing GitHub owner."),
    "hermes": ("plugin", "public", "eligible", "Pinned catalog packages; native foreign SDKs unsupported."),
    "native": ("plugin", "local", "eligible", "Saved Row-Bot marketplace; refresh through reviewed plugin lifecycle."),
    "skills_sh": ("skill", "unavailable", "auth_required", "Documented v1 needs Vercel OIDC; desktop access is not implemented."),
    "browse_sh": ("skill", "unavailable", "contract_unresolved", "A supported public discovery contract has not been established."),
    "lobehub": ("skill", "unavailable", "contract_unresolved", "Agent prompt conversion is an explicit import, not an Agent Skills catalog."),
    "glama": ("mcp", "unavailable", "auth_required", "Directory key, data license, visible Glama credit and listing backlinks require a separate integration."),
    "pulsemcp": ("mcp", "unavailable", "auth_required", "B2B tenant and API key integration is not implemented."),
    "smithery": ("mcp", "unavailable", "contract_unresolved", "Documented bearer authentication and anonymous access differ; desktop access unresolved."),
    "clawhub_plugins": ("plugin", "unavailable", "unsupported", "Bundle labels do not establish Agent Plugins 1.0 compatibility; native SDKs unsupported."),
    "examples": ("plugin", "local", "explicit_only", "Developer fixtures, available only by explicit source selection or import."),
}


def source_status(source: str, **fields: object) -> dict:
    kind, mode, eligibility, message = SOURCES[source]
    return {"source": source, "kind": kind, "access": mode, "eligibility": eligibility,
            "enabled": eligibility == "eligible", "status": "unavailable" if mode == "unavailable" else "empty",
            "message": message, "fetched_at": None, "snapshot_version": "", "snapshot_digest": "",
            "truncated": False, **fields}
