"""Native per-plugin Plugin Center detail dialog."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Callable

from nicegui import ui

from row_bot.plugins.ui_settings import (
    _get_missing_secrets,
    _get_missing_settings,
    _iter_secret_specs,
    _iter_setting_specs,
    _manifest_counts,
    _permission_label,
    _plugin_update_entry,
    _run_manifest_health,
)

logger = logging.getLogger(__name__)

PLUGIN_DETAIL_SECTIONS = (
    "Overview",
    "Permissions",
    "Setup",
    "Auth",
    "Tools",
    "Channels",
    "Skills",
    "Health",
    "Logs",
    "Updates",
)


def open_plugin_dialog(
    manifest: Any,
    *,
    on_change: Callable | None = None,
    on_uninstall: Callable | None = None,
) -> None:
    """Open a native detail/configuration dialog for a single plugin."""

    from row_bot.plugins import state as plugin_state

    plugin_id = manifest.id
    counts = _manifest_counts(manifest)
    setting_inputs: dict[str, Any] = {}
    secret_inputs: dict[str, Any] = {}

    with ui.dialog().props("persistent") as dlg, ui.card().classes("w-full").style(
        "min-width: 680px; max-width: 900px; max-height: 88vh;"
    ):
        with ui.row().classes("w-full items-start no-wrap gap-3"):
            ui.icon(getattr(manifest, "icon", "extension") or "extension").classes("text-primary q-mt-xs")
            with ui.column().classes("gap-0").style("min-width: 0; flex: 1;"):
                with ui.row().classes("items-center gap-2"):
                    ui.label(manifest.name).classes("text-h6 text-weight-bold")
                    ui.badge(f"v{manifest.version}", color="blue-grey").props("outline")
                ui.label(f"by {getattr(manifest.author, 'name', 'Unknown')}").classes("text-grey-6 text-sm")
            ui.button(icon="close", on_click=dlg.close).props("flat round dense")

        ui.separator().classes("q-my-sm")

        with ui.scroll_area().classes("w-full").style("max-height: calc(88vh - 150px);"):
            with ui.column().classes("w-full gap-2 q-pr-sm"):
                with _section("Overview", "info"):
                    ui.label(manifest.description).classes("text-grey-7 text-sm")
                    if getattr(manifest, "long_description", ""):
                        ui.label(manifest.long_description).classes("text-grey-6 text-sm")
                    with ui.row().classes("gap-2 flex-wrap q-mt-sm"):
                        _metric("Native tools", counts["native_tools"])
                        _metric("MCP servers", counts["mcp_servers"])
                        _metric("Channels", counts["channels"])
                        _metric("Skills", counts["skills"])
                        _metric("Min Row-Bot", getattr(manifest, "min_row_bot_version", ""))
                    install_info = plugin_state.get_plugin_install_info(plugin_id)
                    if install_info:
                        ui.label(
                            f"Installed from {install_info.get('source', 'local')}"
                        ).classes("text-grey-6 text-xs")

                with _section("Permissions", "verified_user"):
                    permissions = getattr(manifest, "permissions", []) or []
                    if permissions:
                        with ui.row().classes("gap-1 flex-wrap"):
                            for permission in permissions:
                                ui.badge(_permission_label(permission), color="orange").props("outline")
                    else:
                        ui.label("No additional permissions declared.").classes("text-grey-6 text-sm")

                with _section("Setup", "tune"):
                    settings = _iter_setting_specs(manifest)
                    secrets = _iter_secret_specs(manifest)
                    if not settings and not secrets:
                        ui.label("No settings or secrets declared.").classes("text-grey-6 text-sm")
                    if settings:
                        ui.label("Settings").classes("text-subtitle2")
                        for name, spec in settings:
                            setting_inputs[name] = _render_setting_input(manifest, name, spec)
                    if secrets:
                        ui.label("Secrets").classes("text-subtitle2 q-mt-sm")
                        for name, spec in secrets:
                            secret_inputs[name] = _render_secret_input(manifest, name, spec)

                with _section("Auth", "key"):
                    auth = getattr(manifest, "auth", {}) or {}
                    if not auth:
                        ui.label("No declarative auth flow declared.").classes("text-grey-6 text-sm")
                    for name, spec in auth.items():
                        if not isinstance(spec, dict):
                            continue
                        label = spec.get("label") or name
                        auth_type = str(spec.get("type") or "custom")
                        with ui.row().classes("items-center gap-2"):
                            ui.icon("vpn_key", size="xs").classes("text-grey-6")
                            ui.label(str(label)).classes("text-body2")
                            ui.badge(auth_type.replace("_", " "), color="blue-grey").props("outline dense")

                with _section("Tools", "build"):
                    provides = getattr(manifest, "provides", None)
                    _render_provide_entries(getattr(provides, "native_tools", []) or [], empty="No native tools declared.")
                    mcp_servers = getattr(provides, "mcp_servers", []) or []
                    if mcp_servers:
                        ui.label("MCP-backed tool servers").classes("text-subtitle2 q-mt-sm")
                        _render_provide_entries(mcp_servers, empty="")

                with _section("Channels", "forum"):
                    provides = getattr(manifest, "provides", None)
                    _render_provide_entries(getattr(provides, "channels", []) or [], empty="No channels declared.")

                with _section("Skills", "auto_fix_high"):
                    provides = getattr(manifest, "provides", None)
                    _render_provide_entries(getattr(provides, "skills", []) or [], empty="No bundled skills declared.")

                with _section("Health", "fact_check"):
                    checks = _run_manifest_health(manifest)
                    for check in checks:
                        ok = check["status"] == "ok"
                        with ui.row().classes("items-center gap-2"):
                            ui.icon("check_circle" if ok else "warning", size="xs").classes(
                                "text-positive" if ok else "text-warning"
                            )
                            ui.label(check["label"]).classes("text-sm")
                    declared = getattr(manifest, "health_checks", []) or []
                    if declared:
                        ui.label("Declared plugin health checks").classes("text-subtitle2 q-mt-sm")
                        _render_provide_entries(declared, empty="")

                with _section("Logs", "article"):
                    _render_load_logs(plugin_id)

                with _section("Updates", "update"):
                    ui.label(f"Installed version: {manifest.version}").classes("text-grey-6 text-sm")
                    update_entry = _plugin_update_entry(manifest)
                    if update_entry:
                        ui.label(f"Update available: v{update_entry.version}").classes(
                            "text-warning text-sm"
                        )
                        if update_entry.changelog_url:
                            ui.link("Changelog", update_entry.changelog_url).classes("text-sm")
                    else:
                        ui.label(
                            "No cached marketplace update is available. Refresh Marketplace to check again."
                        ).classes("text-grey-6 text-sm")

        ui.separator().classes("q-my-sm")

        with ui.row().classes("w-full justify-between items-center"):
            def _do_uninstall() -> None:
                dlg.close()
                if on_uninstall:
                    on_uninstall(plugin_id)

            ui.button("Uninstall", icon="delete", on_click=_do_uninstall).props(
                "flat color=negative size=sm no-caps"
            )

            with ui.row().classes("gap-2"):
                ui.button("Cancel", on_click=dlg.close).props("flat no-caps")

                def _save() -> None:
                    for name, widget in setting_inputs.items():
                        plugin_state.set_plugin_config(plugin_id, name, widget.value)
                    for name, widget in secret_inputs.items():
                        value = (widget.value or "").strip()
                        if value:
                            plugin_state.set_plugin_secret(plugin_id, name, value)
                    ui.notify(f"{manifest.name} settings saved", type="positive")
                    dlg.close()
                    if on_change:
                        on_change()

                ui.button("Save", icon="save", on_click=_save).props("color=primary no-caps")

    dlg.open()


@contextmanager
def _section(title: str, icon: str):
    with ui.expansion(title, icon=icon, value=(title == "Overview")).classes("w-full"):
        with ui.column().classes("w-full gap-2 q-pa-sm"):
            yield


def _metric(label: str, value: Any) -> None:
    with ui.row().classes("items-center gap-1 px-2 py-1 rounded-borders no-wrap").style(
        "border: 1px solid rgba(148, 163, 184, 0.24); background: rgba(148, 163, 184, 0.08);"
    ):
        ui.label(str(value)).classes("text-weight-bold text-xs")
        ui.label(label).classes("text-grey-6 text-xs")


def _render_setting_input(manifest: Any, name: str, spec: dict[str, Any]) -> Any:
    from row_bot.plugins import state as plugin_state

    label = str(spec.get("label") or name)
    required = " *" if spec.get("required", False) else ""
    field_type = str(spec.get("type", "text"))
    current = plugin_state.get_plugin_config(manifest.id, name, spec.get("default", ""))
    if field_type == "checkbox":
        return ui.checkbox(f"{label}{required}", value=bool(current))
    if field_type == "select":
        return ui.select(label=f"{label}{required}", options=spec.get("options", []), value=current).classes("w-full")
    if field_type == "multi-select":
        return ui.select(
            label=f"{label}{required}",
            options=spec.get("options", []),
            value=current or [],
            multiple=True,
        ).classes("w-full")
    if field_type == "number":
        return ui.number(
            label=f"{label}{required}",
            value=current,
            min=spec.get("min"),
            max=spec.get("max"),
        ).classes("w-full")
    if field_type == "textarea":
        return ui.textarea(label=f"{label}{required}", value=str(current or "")).classes("w-full")
    if field_type in {"password", "secret"}:
        return ui.input(
            label=f"{label}{required}",
            value=str(current or ""),
            password=True,
            password_toggle_button=True,
        ).classes("w-full")
    return ui.input(label=f"{label}{required}", value=str(current or "")).classes("w-full")


def _render_secret_input(manifest: Any, name: str, spec: dict[str, Any]) -> Any:
    from row_bot.plugins import state as plugin_state

    configured = bool(plugin_state.get_plugin_secret(manifest.id, name))
    label = str(spec.get("label") or name)
    required = " *" if spec.get("required", False) else ""
    placeholder = "Configured; paste a new value to replace it" if configured else str(spec.get("placeholder", ""))
    with ui.row().classes("w-full items-center gap-2 no-wrap"):
        widget = ui.input(
            f"{label}{required}",
            value="",
            placeholder=placeholder,
            password=True,
            password_toggle_button=True,
        ).classes("col")
        if configured:
            ui.button(
                icon="delete",
                on_click=lambda key=name: plugin_state.delete_plugin_secret(manifest.id, key),
            ).props("flat round dense color=negative").tooltip("Clear saved secret")
    return widget


def _render_provide_entries(entries: list[dict[str, Any]], *, empty: str) -> None:
    if not entries:
        if empty:
            ui.label(empty).classes("text-grey-6 text-sm")
        return
    for entry in entries:
        label = entry.get("name") or entry.get("id") or entry.get("path") or "Unnamed"
        detail = entry.get("description") or entry.get("entrypoint") or entry.get("path") or ""
        with ui.row().classes("items-center gap-2"):
            ui.icon("chevron_right", size="xs").classes("text-grey-6")
            ui.label(str(label)).classes("text-body2")
            if detail:
                ui.label(str(detail)).classes("text-grey-6 text-sm")


def _render_load_logs(plugin_id: str) -> None:
    from row_bot.plugins import loader

    results = [result for result in loader.get_load_results() if result.plugin_id == plugin_id]
    persisted = loader.read_plugin_logs(plugin_id, limit=5)
    if not results and not persisted:
        ui.label("No load log entries yet.").classes("text-grey-6 text-sm")
        return
    for result in results:
        status = "Loaded" if result.success else "Failed"
        with ui.row().classes("items-center gap-2"):
            ui.icon("check_circle" if result.success else "error", size="xs").classes(
                "text-positive" if result.success else "text-negative"
            )
            ui.label(status).classes("text-sm")
            if result.error:
                ui.label(result.error).classes("text-grey-6 text-sm")
        for warning in result.warnings:
            ui.label(warning).classes("text-warning text-xs q-ml-md")
    if persisted:
        ui.label("Recent persisted load events").classes("text-subtitle2 q-mt-sm")
        for entry in persisted[-5:]:
            success = bool(entry.get("success", False))
            with ui.row().classes("items-center gap-2"):
                ui.icon("check_circle" if success else "error", size="xs").classes(
                    "text-positive" if success else "text-negative"
                )
                ui.label(str(entry.get("ts", ""))).classes("text-grey-6 text-xs")
                ui.label("Loaded" if success else "Failed").classes("text-sm")
                if entry.get("error"):
                    ui.label(str(entry["error"])).classes("text-grey-6 text-sm")
            for warning in entry.get("warnings", []) or []:
                ui.label(str(warning)).classes("text-warning text-xs q-ml-md")
