"""Plugin marketplace browse dialog.

A standalone dialog that shows available plugins from the marketplace
index with search, tag filtering, and install buttons.
"""

from __future__ import annotations

import logging
from typing import Callable

from nicegui import ui

logger = logging.getLogger(__name__)


def open_marketplace_dialog(
    *,
    on_install: Callable | None = None,
) -> None:
    """Open the marketplace browse dialog."""
    from row_bot.plugins import marketplace as mkt
    from row_bot.plugins import installer

    with ui.dialog().props("persistent maximized=false") as dlg, ui.card().classes(
        "w-full"
    ).style("min-width: 600px; max-width: 800px; max-height: 80vh;"):

        # ── Header ───────────────────────────────────────────────────
        with ui.row().classes("w-full items-center no-wrap"):
            ui.label("🛒 Plugin Marketplace").classes("text-h6")
            ui.space()
            ui.button(icon="close", on_click=dlg.close).props("flat round dense")

        # ── Search bar ───────────────────────────────────────────────
        search_input = ui.input(
            placeholder="Search plugins...",
            on_change=lambda: _refresh_results(),
        ).classes("w-full").props('clearable dense outlined')

        # ── Tag filter row ───────────────────────────────────────────
        tag_row = ui.row().classes("w-full gap-1 flex-wrap q-mb-sm")
        active_tag = {"value": ""}  # mutable to use inside closures

        # ── Results container ────────────────────────────────────────
        results_container = ui.column().classes("w-full gap-2").style(
            "overflow-y: auto; max-height: 55vh;"
        )

        # ── Status footer ────────────────────────────────────────────
        status_label = ui.label("").classes("text-grey-5 text-sm q-mt-sm")
        with ui.row().classes("w-full justify-end gap-2 q-mt-sm"):
            ui.button(
                "Refresh", icon="refresh",
                on_click=lambda: _fetch_and_refresh(force=True),
            ).props("flat size=sm")
            ui.button("Close", on_click=dlg.close).props("flat")

        def _populate_tags(index):
            tag_row.clear()
            tags = mkt.get_all_tags(index)
            with tag_row:
                _make_tag_chip("All", "")
                for t in tags[:15]:  # Show top 15 tags
                    _make_tag_chip(t.title(), t)

        def _make_tag_chip(label: str, tag: str):
            is_active = active_tag["value"] == tag
            color = "primary" if is_active else "grey-5"
            chip = ui.button(
                label,
                on_click=lambda _, t=tag: _set_tag(t),
            ).props(f"flat dense size=sm {'color=' + color if is_active else 'outline'}")

        def _set_tag(tag: str):
            active_tag["value"] = tag
            _refresh_results()

        def _refresh_results():
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

        def _build_entry_card(entry):
            is_installed = installer.is_installed(entry.id)

            with ui.card().classes("w-full q-pa-sm"):
                with ui.row().classes("w-full items-center no-wrap"):
                    ui.label(f"{entry.icon} {entry.name}").classes(
                        "text-body1 text-weight-medium"
                    )
                    ui.space()
                    if entry.verified:
                        ui.badge("✅ Verified", color="positive").props("outline")
                    ui.label(f"v{entry.version}").classes("text-grey-5 text-sm")

                ui.label(entry.description).classes("text-grey-6 text-sm")

                with ui.row().classes("w-full items-center q-mt-xs gap-2"):
                    if entry.author_name:
                        ui.label(f"by {entry.author_name}").classes(
                            "text-grey-5 text-xs"
                        )

                    if entry.tool_count:
                        ui.badge(
                            f"🔧 {entry.tool_count}",
                            color="blue-grey",
                        ).props("outline dense")
                    if entry.skill_count:
                        ui.badge(
                            f"📜 {entry.skill_count}",
                            color="teal",
                        ).props("outline dense")

                    if entry.tags:
                        for tag in entry.tags[:3]:
                            ui.badge(tag, color="grey-5").props("outline dense")

                    ui.space()

                    if is_installed:
                        installed_ver = installer.get_installed_version(entry.id)
                        if installed_ver and installed_ver != entry.version:
                            ui.button(
                                f"Update to v{entry.version}", icon="update",
                                on_click=lambda _, eid=entry.id: _do_update(eid),
                            ).props("color=warning size=sm dense")
                        else:
                            ui.badge("Installed", color="positive").props("outline")
                    else:
                        ui.button(
                            "Install", icon="download",
                            on_click=lambda _, eid=entry.id: _do_install(eid),
                        ).props("color=primary size=sm dense")

        async def _do_install(plugin_id: str):
            import asyncio
            ui.notify(f"Installing {plugin_id}...", type="info")
            result = await asyncio.to_thread(
                installer.install_plugin, plugin_id
            )
            if result.success:
                ui.notify(f"✅ {result.message}", type="positive")
                await _reload_plugins_and_agent()
                _refresh_results()
                if on_install:
                    on_install()
            else:
                ui.notify(f"❌ {result.message}", type="negative")

        async def _do_update(plugin_id: str):
            import asyncio
            ui.notify(f"Updating {plugin_id}...", type="info")
            result = await asyncio.to_thread(
                installer.update_plugin, plugin_id
            )
            if result.success:
                ui.notify(f"✅ {result.message}", type="positive")
                await _reload_plugins_and_agent()
                _refresh_results()
                if on_install:
                    on_install()
            else:
                ui.notify(f"❌ {result.message}", type="negative")

        async def _reload_plugins_and_agent():
            """Reload plugins and clear agent cache so new tools are available."""
            import asyncio
            from row_bot.plugins import loader, registry as reg
            from row_bot.agent import clear_agent_cache
            for m in list(reg.get_loaded_manifests()):
                reg.unregister_plugin(m.id)
            await asyncio.to_thread(loader.load_plugins)
            clear_agent_cache()

        async def _fetch_and_refresh(force: bool = False):
            import asyncio
            status_label.text = "Refreshing..."
            await asyncio.to_thread(mkt.fetch_index, force)
            index = mkt.fetch_index()
            _populate_tags(index)
            _refresh_results()

        # Initial load
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
