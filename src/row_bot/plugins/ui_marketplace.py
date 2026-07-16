"""Plugin marketplace browse dialog."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

from nicegui import ui

logger = logging.getLogger(__name__)


def open_marketplace_dialog(
    *,
    on_install: Callable | None = None,
) -> None:
    """Open the marketplace browse dialog."""

    from row_bot.plugins import installer
    from row_bot.plugins import marketplace as mkt

    with ui.dialog().props("persistent maximized=false data-docs-id=plugin-marketplace") as dlg, ui.card().classes(
        "w-full"
    ).style("min-width: 640px; max-width: 860px; max-height: 82vh;"):
        with ui.row().classes("w-full items-center no-wrap"):
            ui.icon("shopping_cart", size="sm")
            ui.label("Plugin Marketplace").classes("text-h6")
            ui.space()
            ui.button(icon="close", on_click=dlg.close).props("flat round dense")

        search_input = ui.input(
            placeholder="Search plugins...",
            on_change=lambda: _refresh_results(),
        ).classes("w-full").props("clearable dense outlined")

        tag_row = ui.row().classes("w-full gap-1 flex-wrap q-mb-sm")
        active_tag = {"value": ""}

        results_container = ui.column().classes("w-full gap-2").style(
            "overflow-y: auto; max-height: 56vh;"
        )

        status_label = ui.label("").classes("text-grey-5 text-sm q-mt-sm")
        with ui.row().classes("w-full justify-end gap-2 q-mt-sm"):
            ui.button(
                "Refresh",
                icon="refresh",
                on_click=lambda: _fetch_and_refresh(force=True),
            ).props("flat size=sm no-caps")
            ui.button("Close", on_click=dlg.close).props("flat no-caps")

        def _populate_tags(index) -> None:
            tag_row.clear()
            tags = mkt.get_all_tags(index)
            with tag_row:
                _make_tag_chip("All", "")
                for tag in tags[:15]:
                    _make_tag_chip(tag.title(), tag)

        def _make_tag_chip(label: str, tag: str) -> None:
            is_active = active_tag["value"] == tag
            props = "flat dense size=sm no-caps"
            if is_active:
                props += " color=primary"
            else:
                props += " outline"
            ui.button(label, on_click=lambda _, value=tag: _set_tag(value)).props(props)

        def _set_tag(tag: str) -> None:
            active_tag["value"] = tag
            _refresh_results()

        def _refresh_results() -> None:
            results_container.clear()
            try:
                index = mkt.fetch_index()
                results = mkt.search_plugins(
                    query=search_input.value or "",
                    tag=active_tag["value"],
                    index=index,
                )
                if not results:
                    with results_container:
                        ui.label("No plugins found matching your search.").classes(
                            "text-grey-5 italic q-pa-md"
                        )
                    status_label.text = "0 plugins"
                    return

                with results_container:
                    for entry in results:
                        _build_entry_card(entry)

                status_label.text = f"{len(results)} plugin{'s' if len(results) != 1 else ''}"
            except Exception as exc:
                logger.error("Marketplace refresh error: %s", exc, exc_info=True)
                with results_container:
                    ui.label(f"Error loading marketplace: {exc}").classes("text-negative")

        def _build_entry_card(entry) -> None:
            is_installed = installer.is_installed(entry.id)

            with ui.card().classes("w-full q-pa-sm"):
                with ui.row().classes("w-full items-start no-wrap gap-3"):
                    ui.icon(entry.icon or "extension").classes("text-primary q-mt-xs")
                    with ui.column().classes("gap-1").style("min-width: 0; flex: 1;"):
                        with ui.row().classes("w-full items-center no-wrap gap-2"):
                            ui.label(entry.name).classes("text-body1 text-weight-medium")
                            ui.label(f"v{entry.version}").classes("text-grey-5 text-sm")
                            if entry.verified:
                                ui.badge("Verified", color="positive").props("outline")
                        ui.label(entry.description).classes("text-grey-6 text-sm")
                        if entry.author_name:
                            ui.label(f"by {entry.author_name}").classes("text-grey-5 text-xs")

                        with ui.row().classes("w-full items-center q-mt-xs gap-2 flex-wrap"):
                            _count_badge("native", entry.native_tool_count, "blue-grey")
                            _count_badge("MCP", entry.mcp_server_count, "indigo")
                            _count_badge("channel", entry.channel_count, "teal")
                            _count_badge("skill", entry.skill_count, "green")
                            for permission in entry.permissions[:4]:
                                ui.badge(permission.replace("_", " ").title(), color="orange").props(
                                    "outline dense"
                                )
                            for tag in entry.tags[:3]:
                                ui.badge(tag, color="grey-5").props("outline dense")

                    with ui.column().classes("items-end gap-2"):
                        if is_installed:
                            installed_ver = installer.get_installed_version(entry.id)
                            if installed_ver and installed_ver != entry.version:
                                ui.button(
                                    f"Update to v{entry.version}",
                                    icon="update",
                                    on_click=lambda _, item=entry: _do_update(item),
                                ).props("color=warning size=sm dense no-caps")
                            else:
                                ui.badge("Installed", color="positive").props("outline")
                        else:
                            ui.button(
                                "Install",
                                icon="download",
                                on_click=lambda _, item=entry: _confirm_install(item),
                            ).props("color=primary size=sm dense no-caps")

        def _confirm_install(entry) -> None:
            with ui.dialog() as confirm_dlg, ui.card().classes("w-full").style("max-width: 620px;"):
                ui.label(f"Install {entry.name}?").classes("text-h6")
                ui.label(entry.description).classes("text-grey-6 text-sm")
                with ui.row().classes("gap-2 flex-wrap q-my-sm"):
                    _count_badge("native tools", entry.native_tool_count, "blue-grey")
                    _count_badge("MCP servers", entry.mcp_server_count, "indigo")
                    _count_badge("channels", entry.channel_count, "teal")
                    _count_badge("skills", entry.skill_count, "green")
                if entry.permissions:
                    ui.label("Permissions").classes("text-subtitle2")
                    with ui.row().classes("gap-1 flex-wrap"):
                        for permission in entry.permissions:
                            ui.badge(permission.replace("_", " ").title(), color="orange").props("outline")
                if entry.checksum:
                    ui.label(f"Checksum: {entry.checksum}").classes("text-grey-6 text-xs")
                source = entry.archive_url or entry.path or "marketplace"
                ui.label(f"Source: {source}").classes("text-grey-6 text-xs")
                ui.label(
                    "Row-Bot will copy this plugin now and keep it off. After install, "
                    "configure settings or secrets, run Test, then enable it in Plugin Center."
                ).classes("text-grey-6 text-sm")

                with ui.row().classes("w-full justify-end gap-2 q-mt-md"):
                    ui.button("Cancel", on_click=confirm_dlg.close).props("flat no-caps")

                    async def _install_and_close() -> None:
                        confirm_dlg.close()
                        await _do_install(entry)

                    ui.button("Install and Keep Off", icon="download", on_click=_install_and_close).props(
                        "color=primary no-caps"
                    )
            confirm_dlg.open()

        async def _do_install(entry) -> None:
            import asyncio

            plugin_id = entry.id
            ui.notify(f"Installing {plugin_id}...", type="info")
            result = await asyncio.to_thread(
                installer.install_plugin,
                plugin_id,
                **_marketplace_install_kwargs(entry),
            )
            if result.success:
                ui.notify(result.message, type="positive")
                await _reload_plugins_and_agent()
                _refresh_results()
                if on_install:
                    on_install()
            else:
                ui.notify(result.message, type="negative")

        async def _do_update(entry) -> None:
            import asyncio

            plugin_id = entry.id
            ui.notify(f"Updating {plugin_id}...", type="info")
            result = await asyncio.to_thread(
                installer.update_plugin,
                plugin_id,
                **_marketplace_install_kwargs(entry),
            )
            if result.success:
                ui.notify(result.message, type="positive")
                await _reload_plugins_and_agent()
                _refresh_results()
                if on_install:
                    on_install()
            else:
                ui.notify(result.message, type="negative")

        async def _reload_plugins_and_agent() -> None:
            import asyncio
            from row_bot.plugins import loader

            await asyncio.to_thread(loader.refresh_plugin_runtime, "marketplace install/update")

        async def _fetch_and_refresh(force: bool = False) -> None:
            import asyncio

            status_label.text = "Refreshing..."
            await asyncio.to_thread(mkt.fetch_index, force)
            index = mkt.fetch_index()
            _populate_tags(index)
            _refresh_results()

        try:
            index = mkt.fetch_index()
            _populate_tags(index)
            _refresh_results()
        except Exception:
            with results_container:
                ui.label("Could not load marketplace. Check your connection.").classes(
                    "text-grey-5 italic"
                )

    dlg.open()


def _count_badge(label: str, count: int, color: str) -> None:
    if count:
        ui.badge(f"{count} {label}", color=color).props("outline dense")


def _marketplace_install_kwargs(entry) -> dict:
    return {
        "source_dir": _source_dir_for_entry(entry),
        "source": "marketplace",
        "source_ref": entry.archive_url or entry.path,
        "archive_url": entry.archive_url,
        "expected_checksum": entry.checksum,
    }


def _source_dir_for_entry(entry) -> Path | None:
    if not entry.path:
        return None
    path = Path(entry.path).expanduser()
    if path.is_absolute() and path.is_dir():
        return path
    source = str(getattr(entry, "index_source", "") or "")
    root: Path | None = None
    if source.startswith("file://"):
        parsed = urlparse(source)
        root = Path(unquote(parsed.path)).expanduser()
    elif source and source not in {"local", "unit-test"}:
        candidate = Path(source).expanduser()
        if candidate.is_dir():
            root = candidate
    if root is None:
        return path if path.is_dir() else None
    candidate = root / entry.path
    return candidate if candidate.is_dir() else None
