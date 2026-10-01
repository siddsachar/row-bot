"""A plugin's local self-test: the setup it still needs and its declared checks.

Used by Settings › Plugins' "Test" (``application/plugin_commands``) and
``row-bot plugins doctor`` (``plugins/devtools``). Nothing here loads or runs
the plugin.
"""

from __future__ import annotations

from typing import Any, Callable


def missing_settings(manifest: Any) -> list[str]:
    """Labels of required settings that have no value yet."""
    from row_bot.plugins import state as plugin_state

    missing: list[str] = []
    for name, spec in _setting_specs(manifest):
        if not spec.get("required", False):
            continue
        default = spec.get("default")
        value = plugin_state.get_plugin_config(manifest.id, name, default)
        if value in (None, "", []):
            missing.append(str(spec.get("label") or name))
    return missing


def missing_secrets(manifest: Any) -> list[str]:
    """Labels of required secrets that are not stored yet."""
    from row_bot.plugins import state as plugin_state

    missing: list[str] = []
    for name, spec in _secret_specs(manifest):
        if spec.get("required", False):
            value = plugin_state.get_plugin_secret(manifest.id, name)
            if not value:
                missing.append(str(spec.get("label") or name))
    return missing


def run_manifest_health(manifest: Any) -> list[dict[str, str]]:
    """The self-test's checks, each with a label and a status."""
    checks: list[dict[str, str]] = []
    settings = missing_settings(manifest)
    secrets = missing_secrets(manifest)
    for label in settings:
        checks.append({"label": label, "status": "missing_setting"})
    for label in secrets:
        checks.append({"label": label, "status": "missing_secret"})

    for check in getattr(manifest, "health_checks", []) or []:
        if not isinstance(check, dict):
            continue
        checks.append(
            _run_declared_health_check(
                manifest,
                check,
                missing_settings=settings,
                missing_secrets=secrets,
            )
        )

    if not checks:
        checks.append({"label": "Required local setup", "status": "ok"})
    return checks


def record_manifest_health(
    manifest: Any, *, validate: Callable[[], None] = lambda: None
) -> list[dict[str, str]]:
    """Run the self-test and save its result as the plugin's health."""
    from row_bot.plugins import state as plugin_state

    checks = run_manifest_health(manifest)
    validate()
    plugin_state.set_plugin_health_result(
        manifest.id,
        ok=_health_checks_ok(checks),
        checks=checks,
    )
    return checks


def _setting_specs(manifest: Any) -> list[tuple[str, dict[str, Any]]]:
    settings = getattr(manifest, "settings", {}) or {}
    if not isinstance(settings, dict):
        return []
    # v1 compatibility: settings.config nested field specs.
    if isinstance(settings.get("config"), dict):
        source = settings.get("config", {})
    else:
        source = settings
    return [
        (str(name), dict(spec))
        for name, spec in source.items()
        if isinstance(name, str) and isinstance(spec, dict)
    ]


def _secret_specs(manifest: Any) -> list[tuple[str, dict[str, Any]]]:
    secrets = getattr(manifest, "secrets", {}) or {}
    if isinstance(secrets, dict) and secrets:
        source = secrets
    else:
        settings = getattr(manifest, "settings", {}) or {}
        source = settings.get("api_keys", {}) if isinstance(settings, dict) else {}
    if not isinstance(source, dict):
        return []
    return [
        (str(name), dict(spec))
        for name, spec in source.items()
        if isinstance(name, str) and isinstance(spec, dict)
    ]


def _run_declared_health_check(
    manifest: Any,
    check: dict[str, Any],
    *,
    missing_settings: list[str],
    missing_secrets: list[str],
) -> dict[str, str]:
    check_type = str(check.get("type") or check.get("id") or "custom")
    label = _health_check_label(check)
    if missing_settings or missing_secrets:
        return {"label": label, "status": "blocked_missing_setup"}

    provides = getattr(manifest, "provides", None)
    if check_type in {"required_settings", "required_secrets", "required_setup"}:
        return {"label": label, "status": "ok"}
    if check_type == "channel_configured":
        channels = getattr(provides, "channels", []) or []
        return {
            "label": label,
            "status": "ok" if channels else "missing_channel",
        }
    if check_type in {"mcp_server_starts", "mcp_tools_discovered"}:
        servers = getattr(provides, "mcp_servers", []) or []
        return {
            "label": label,
            "status": "ok"
            if _mcp_servers_have_launch_config(servers)
            else "missing_mcp_server",
        }
    if check_type in {"api_probe", "oauth_refresh", "dry_run_send"}:
        return {"label": label, "status": "manual_required"}
    return {"label": label, "status": "unknown_check"}


def _health_check_label(check: dict[str, Any]) -> str:
    label = (
        check.get("label")
        or check.get("name")
        or check.get("id")
        or check.get("type")
        or "Health check"
    )
    return str(label).replace("_", " ").title()


def _mcp_servers_have_launch_config(servers: list[Any]) -> bool:
    if not servers:
        return False
    for server in servers:
        if not isinstance(server, dict):
            return False
        transport = str(server.get("transport") or "stdio")
        if transport == "stdio" and not server.get("command"):
            return False
        if transport in {"sse", "streamable_http"} and not server.get("url"):
            return False
    return True


def _health_checks_ok(checks: list[dict[str, str]]) -> bool:
    return bool(checks) and not _blocking_health_checks(checks)


def _blocking_health_checks(checks: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        check
        for check in checks
        if check.get("status") not in {"ok", "manual_required"}
    ]
