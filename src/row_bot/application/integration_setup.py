"""Passive setup facts from existing MCP configuration and runtime owners."""
from __future__ import annotations

from urllib.parse import urlsplit


def describe_mcp_setup(server: dict, cfg: dict) -> dict:
    """Expose requirements, never secret values or unreviewed executable recipes."""
    auth = cfg.get("auth") or {}
    source = cfg.get("source") or {}
    local = cfg.get("transport", "stdio") == "stdio"
    declared = source.get("auth_mode", "unknown")
    mode = auth.get("mode") if auth.get("mode") in {"oauth", "api_key"} else declared
    if mode not in {"none", "oauth", "api_key", "unsupported"}:
        mode = "none" if auth.get("mode") == "none" and not auth.get("operation_id") and not source.get("requires_auth") else "unknown"
    # Disconnect records mode=none; it cannot erase a declared account requirement.
    bindings = auth.get("bindings") or source.get("auth_bindings") or []
    from row_bot.mcp_client.auth import validate_metadata, McpAuthError
    try:
        bindings = validate_metadata({"mode": "api_key", "bindings": bindings})["bindings"]
        if any((binding["kind"] == "env") != local for binding in bindings):
            raise McpAuthError("invalid_mcp_auth")
    except McpAuthError:
        bindings, mode = [], "unsupported"
    destination = "Local process"
    if not local:
        try:
            url = urlsplit(str(cfg.get("url", "")))
            destination = f"{url.scheme}://{url.hostname or ''}" + (f":{url.port}" if url.port else "") + url.path
        except ValueError:
            destination = "Invalid connection destination"
    tools = cfg.get("tools") or {}
    return {"auth_mode": mode, "execution": "local" if local else "hosted", "destination": destination[:2048],
        "bindings": bindings, "credential_configured": bool(auth.get("credential_ref")),
        "catalog_accepted": isinstance(tools.get("catalog"), dict),
        "requirements": list(server.get("requirements") or []),
        "runtime_status": str(server.get("runtime_status") or "not_connected")[:64],
        "package_prepared": bool(cfg.get("managed_launch") or cfg.get("plugin_prepared")),
        "package_required": local and str(cfg.get("command", "")).lower() in {"npx", "npx.cmd"} and not cfg.get("managed_launch"),
        "account_requirements": str(source.get("account_requirements") or ("Account required; consult publisher requirements." if mode in {"oauth", "api_key"} else "Account requirements not supplied."))[:512],
        "cost": str(source.get("cost") or "Cost and subscription requirements not supplied. Check the publisher before connecting.")[:512],
        "evidence": str(source.get("evidence") or "Saved connection settings; live account access has not been verified.")[:512]}
