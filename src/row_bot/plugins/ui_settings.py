"""Plugin Center settings tab."""

from __future__ import annotations

import logging
from typing import Any, Callable

from nicegui import ui

logger = logging.getLogger(__name__)


def build_plugins_tab(
    *,
    on_browse_marketplace: Callable | None = None,
) -> None:
    """Build the native Plugin Center inside the settings dialog."""

    from row_bot.plugins import loader as plugin_loader
    from row_bot.plugins import registry as plugin_registry
    from row_bot.plugins import state as plugin_state

    ui.label("Plugin Center").classes("text-h6")
    ui.label(
        "Discover, configure, test, enable, update, disable, and uninstall Row-Bot plugins."
    ).classes("text-grey-6 text-sm")
    ui.separator().classes("q-my-md")

    with ui.row().classes("w-full items-center justify-between q-mb-md gap-2"):
        with ui.row().classes("gap-2"):
            if on_browse_marketplace:
                ui.button(
                    "Browse Marketplace",
                    icon="shopping_cart",
                    on_click=on_browse_marketplace,
                ).props("color=primary outline no-caps")
            ui.button(
                "Reload",
                icon="refresh",
                on_click=lambda: _reload_and_refresh(),
            ).props("outline no-caps")
        summary = plugin_loader.get_load_summary()
        ui.badge(
            f"{summary.get('loaded', 0)} loaded / {summary.get('failed', 0)} failed",
            color="blue-grey",
        ).props("outline")

    cards_container = ui.column().classes("w-full gap-3")

    def _refresh_cards() -> None:
        cards_container.clear()
        manifests = sorted(plugin_registry.get_loaded_manifests(), key=lambda m: m.name.lower())

        if not manifests:
            with cards_container:
                with ui.column().classes("w-full items-center q-pa-lg"):
                    ui.icon("extension_off", size="64px").classes("text-grey-4")
                    ui.label("No plugins installed").classes("text-grey-5 text-h6 q-mt-sm")
                    ui.label("Browse the marketplace or link a local plugin to get started.").classes(
                        "text-grey-5 text-sm"
                    )
                    if on_browse_marketplace:
                        ui.button(
                            "Browse Marketplace",
                            icon="shopping_cart",
                            on_click=on_browse_marketplace,
                        ).props("color=primary q-mt-md no-caps")
            return

        with cards_container:
            for manifest in manifests:
                _build_plugin_card(manifest, _refresh_cards)

    def _build_plugin_card(manifest: Any, refresh_fn: Callable[[], None]) -> None:
        plugin_id = manifest.id
        enabled = plugin_state.is_plugin_enabled(plugin_id)
        missing_settings = _get_missing_settings(manifest)
        missing_secrets = _get_missing_secrets(manifest)
        update_entry = _plugin_update_entry(manifest)
        status_label, status_color = _plugin_status(
            manifest,
            enabled=enabled,
            update_entry=update_entry,
        )
        install_info = plugin_state.get_plugin_install_info(plugin_id)
        source_label = install_info.get("source") or "bundled/local"
        counts = _manifest_counts(manifest)

        with ui.card().classes("w-full q-pa-md"):
            with ui.row().classes("w-full items-start no-wrap gap-3"):
                ui.icon(getattr(manifest, "icon", "extension") or "extension").classes("text-primary q-mt-xs")
                with ui.column().classes("gap-1").style("min-width: 0; flex: 1;"):
                    with ui.row().classes("w-full items-center gap-2 no-wrap"):
                        ui.label(manifest.name).classes("text-body1 text-weight-medium")
                        ui.badge(f"v{manifest.version}", color="blue-grey").props("outline")
                        ui.badge(status_label, color=status_color).props("outline")
                    ui.label(manifest.description).classes("text-grey-6 text-sm")
                    ui.label(f"Source: {source_label}").classes("text-grey-6 text-xs")

                    with ui.row().classes("q-mt-xs gap-2 flex-wrap"):
                        _summary_badge("tools", counts["native_tools"], "build", "blue-grey")
                        _summary_badge("MCP servers", counts["mcp_servers"], "hub", "indigo")
                        _summary_badge("channels", counts["channels"], "forum", "teal")
                        _summary_badge("skills", counts["skills"], "auto_fix_high", "green")

                    if manifest.permissions:
                        with ui.row().classes("q-mt-xs gap-1 flex-wrap"):
                            for permission in manifest.permissions:
                                ui.badge(_permission_label(permission), color="orange").props("outline dense")

                    if missing_settings or missing_secrets:
                        ui.label(
                            "Setup needed: " + ", ".join(missing_settings + missing_secrets)
                        ).classes("text-warning text-xs q-mt-xs")

                with ui.column().classes("items-end gap-2"):
                    ui.button(
                        "Configure",
                        icon="settings",
                        on_click=lambda _, m=manifest: _open_config(m, refresh_fn),
                    ).props("flat dense no-caps")
                    ui.button(
                        "Test",
                        icon="fact_check",
                        on_click=lambda _, m=manifest: _test_plugin(m, refresh_fn),
                    ).props("flat dense no-caps")
                    if update_entry:
                        ui.button(
                            f"Update to v{update_entry.version}",
                            icon="update",
                            on_click=lambda _, m=manifest, entry=update_entry: _update_plugin(
                                m, entry, refresh_fn
                            ),
                        ).props("flat dense no-caps color=warning")
                    ui.button(
                        "Disable" if enabled else "Enable",
                        icon="toggle_on" if enabled else "toggle_off",
                        on_click=lambda _, m=manifest, value=not enabled: _toggle_plugin(
                            m, value, refresh_fn
                        ),
                    ).props(("flat dense no-caps color=negative") if enabled else "flat dense no-caps color=primary")

    def _toggle_plugin(manifest: Any, enabled: bool, refresh_fn: Callable[[], None]) -> None:
        plugin_id = manifest.id
        if enabled:
            ok, reason = _can_enable_plugin(manifest)
            if not ok:
                ui.notify(reason, type="warning")
                return
        plugin_state.set_plugin_enabled(plugin_id, enabled)
        try:
            from row_bot.agent import clear_agent_cache

            clear_agent_cache()
        except Exception:
            logger.debug("Could not clear agent cache after plugin toggle", exc_info=True)
        ui.notify(f"Plugin {plugin_id} {'enabled' if enabled else 'disabled'}", type="info")
        refresh_fn()

    def _open_config(manifest: Any, refresh_fn: Callable[[], None]) -> None:
        from row_bot.plugins.ui_plugin_dialog import open_plugin_dialog

        open_plugin_dialog(
            manifest,
            on_change=refresh_fn,
            on_uninstall=lambda plugin_id: _uninstall_plugin(plugin_id, refresh_fn),
        )

    def _test_plugin(manifest: Any, refresh_fn: Callable[[], None]) -> None:
        checks = _record_manifest_health(manifest)
        failed = _blocking_health_checks(checks)
        if failed:
            ui.notify(
                f"{manifest.name} needs setup: {', '.join(check['label'] for check in failed)}",
                type="warning",
            )
        else:
            ui.notify(f"{manifest.name} passed local setup checks", type="positive")
        refresh_fn()

    async def _update_plugin(manifest: Any, entry: Any, refresh_fn: Callable[[], None]) -> None:
        import asyncio
        from row_bot.plugins import installer
        from row_bot.plugins.ui_marketplace import _marketplace_install_kwargs

        result = await asyncio.to_thread(
            installer.update_plugin,
            manifest.id,
            **_marketplace_install_kwargs(entry),
        )
        if result.success:
            ui.notify(result.message, type="positive")
            await _reload_and_refresh()
        else:
            ui.notify(result.message, type="negative")
            refresh_fn()

    def _uninstall_plugin(plugin_id: str, refresh_fn: Callable[[], None]) -> None:
        from row_bot.plugins import installer

        with ui.dialog() as confirm_dlg, ui.card():
            ui.label(f"Uninstall plugin '{plugin_id}'?").classes("text-body1")
            ui.label("This removes plugin files, saved settings, and secret metadata.").classes(
                "text-grey-6 text-sm"
            )
            with ui.row().classes("w-full justify-end gap-2 q-mt-md"):
                ui.button("Cancel", on_click=confirm_dlg.close).props("flat no-caps")

                def _do_uninstall() -> None:
                    result = installer.uninstall_plugin(plugin_id)
                    ui.notify(
                        result.message,
                        type="positive" if result.success else "negative",
                    )
                    confirm_dlg.close()
                    refresh_fn()

                ui.button("Uninstall", on_click=_do_uninstall).props("color=negative no-caps")
        confirm_dlg.open()

    async def _reload_and_refresh() -> None:
        import asyncio
        from row_bot.plugins import loader, registry as reg

        for manifest in list(reg.get_loaded_manifests()):
            reg.unregister_plugin(manifest.id)

        results = await asyncio.to_thread(loader.load_plugins)
        try:
            from row_bot.agent import clear_agent_cache

            clear_agent_cache()
        except Exception:
            logger.debug("Could not clear agent cache after plugin reload", exc_info=True)
        loaded = sum(1 for result in results if result.success)
        failed = sum(1 for result in results if not result.success)
        ui.notify(
            f"Loaded {loaded} plugin{'s' if loaded != 1 else ''}"
            + (f", {failed} failed" if failed else ""),
            type="positive" if not failed else "warning",
        )
        _refresh_cards()

    _refresh_cards()


def _summary_badge(label: str, count: int, icon: str, color: str) -> None:
    if count <= 0:
        return
    ui.badge(f"{count} {label}", color=color).props("outline dense").tooltip(label)


def _permission_label(permission: str) -> str:
    return str(permission).replace("_", " ").title()


def _manifest_counts(manifest: Any) -> dict[str, int]:
    provides = getattr(manifest, "provides", None)
    return {
        "native_tools": len(getattr(provides, "native_tools", []) or []),
        "mcp_servers": len(getattr(provides, "mcp_servers", []) or []),
        "channels": len(getattr(provides, "channels", []) or []),
        "skills": len(getattr(provides, "skills", []) or []),
    }


def _plugin_status(
    manifest: Any,
    *,
    enabled: bool,
    update_entry: Any | None = None,
) -> tuple[str, str]:
    if update_entry:
        return f"Update available v{update_entry.version}", "warning"
    if enabled:
        return "Enabled", "positive"
    if _get_missing_settings(manifest) or _get_missing_secrets(manifest):
        return "Needs setup", "warning"
    from row_bot.plugins import state as plugin_state

    health = plugin_state.get_plugin_health_result(manifest.id)
    if health.get("ok"):
        return "Ready to enable", "positive"
    return "Not tested", "orange"


def _plugin_update_entry(manifest: Any) -> Any | None:
    try:
        from row_bot.plugins import marketplace

        return marketplace.get_update_entry(manifest)
    except Exception:
        logger.debug("Plugin update lookup skipped for %s", getattr(manifest, "id", "?"), exc_info=True)
        return None


def _get_missing_keys(manifest: Any) -> list[str]:
    """Compatibility helper for old API-key tests; now backed by v2 secrets."""

    return _get_missing_secrets(manifest)


def _get_missing_settings(manifest: Any) -> list[str]:
    from row_bot.plugins import state as plugin_state

    missing: list[str] = []
    for name, spec in _iter_setting_specs(manifest):
        if not spec.get("required", False):
            continue
        default = spec.get("default")
        value = plugin_state.get_plugin_config(manifest.id, name, default)
        if value in (None, "", []):
            missing.append(str(spec.get("label") or name))
    return missing


def _get_missing_secrets(manifest: Any) -> list[str]:
    from row_bot.plugins import state as plugin_state

    missing: list[str] = []
    for name, spec in _iter_secret_specs(manifest):
        if spec.get("required", False):
            value = plugin_state.get_plugin_secret(manifest.id, name)
            if not value:
                missing.append(str(spec.get("label") or name))
    return missing


def _iter_setting_specs(manifest: Any) -> list[tuple[str, dict[str, Any]]]:
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


def _iter_secret_specs(manifest: Any) -> list[tuple[str, dict[str, Any]]]:
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


def _run_manifest_health(manifest: Any) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    missing_settings = _get_missing_settings(manifest)
    missing_secrets = _get_missing_secrets(manifest)
    for label in missing_settings:
        checks.append({"label": label, "status": "missing_setting"})
    for label in missing_secrets:
        checks.append({"label": label, "status": "missing_secret"})

    for check in getattr(manifest, "health_checks", []) or []:
        if not isinstance(check, dict):
            continue
        checks.append(
            _run_declared_health_check(
                manifest,
                check,
                missing_settings=missing_settings,
                missing_secrets=missing_secrets,
            )
        )

    if not checks:
        checks.append({"label": "Required local setup", "status": "ok"})
    return checks


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
            "status": "ok" if _mcp_servers_have_launch_config(servers) else "missing_mcp_server",
        }
    if check_type in {"api_probe", "oauth_refresh", "dry_run_send"}:
        return {"label": label, "status": "manual_required"}
    return {"label": label, "status": "unknown_check"}


def _health_check_label(check: dict[str, Any]) -> str:
    label = check.get("label") or check.get("name") or check.get("id") or check.get("type") or "Health check"
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


def _record_manifest_health(manifest: Any) -> list[dict[str, str]]:
    from row_bot.plugins import state as plugin_state

    checks = _run_manifest_health(manifest)
    plugin_state.set_plugin_health_result(
        manifest.id,
        ok=_health_checks_ok(checks),
        checks=checks,
    )
    return checks


def _can_enable_plugin(manifest: Any) -> tuple[bool, str]:
    checks = _run_manifest_health(manifest)
    failed = _blocking_health_checks(checks)
    if failed:
        return (
            False,
            f"{manifest.name} needs setup: {', '.join(check['label'] for check in failed)}",
        )

    from row_bot.plugins import state as plugin_state

    health = plugin_state.get_plugin_health_result(manifest.id)
    if not health.get("ok"):
        return False, f"Run Test for {manifest.name} before enabling it."
    return True, ""


def _health_checks_ok(checks: list[dict[str, str]]) -> bool:
    return bool(checks) and not _blocking_health_checks(checks)


def _blocking_health_checks(checks: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        check
        for check in checks
        if check.get("status") not in {"ok", "manual_required"}
    ]
