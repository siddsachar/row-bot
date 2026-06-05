"""Row-Bot UI — Auto-update dialog & 'What's New' banner helpers.

Provides:
- ``show_update_dialog(info)``        — modal with notes, Install / Later / Skip
- ``show_update_progress(info, ...)`` — download-and-install progress dialog
- ``build_update_section(container)`` — reusable Settings section

All UI work happens on the NiceGUI event loop; downloads run in a worker
thread via ``nicegui.run.io_bound``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

from row_bot.brand import APP_DISPLAY_NAME, APP_REPOSITORY_URL
from nicegui import app as nicegui_app, run, ui

import row_bot.updater as updater
from row_bot.updater import UpdateError, UpdateInfo
from row_bot.version import __version__

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# "WHAT'S NEW" DIALOG
# ══════════════════════════════════════════════════════════════════════════

def show_update_dialog(info: Optional[UpdateInfo] = None) -> None:
    """Open the What's-New modal for *info* (or the currently-available
    update if None). No-op if there's nothing to show."""
    if info is None:
        info = updater.get_update_state().available
    if info is None:
        ui.notify("No update available", type="info")
        return

    dlg = ui.dialog().props("persistent")
    with dlg, ui.card().style("max-width: 560px; width: 90vw;"):
        with ui.row().classes("items-center w-full"):
            ui.icon("system_update", size="1.5rem").classes("text-primary")
            ui.label(f"{APP_DISPLAY_NAME} v{info.version} is available").classes("text-h6")
            ui.space()
            ui.label(f"Channel: {info.channel}").classes("text-xs text-grey-6")
        ui.label(f"You're on v{__version__}.").classes("text-grey-7 text-sm")
        ui.separator()
        with ui.scroll_area().style("max-height: 45vh; min-height: 120px;"):
            ui.markdown(info.notes_md or "(no release notes)").classes("w-full")
        ui.separator()
        with ui.row().classes("w-full items-center"):
            ui.link("View on GitHub", info.html_url, new_tab=True).classes(
                "text-xs text-grey-6"
            )
            ui.space()

            def _later() -> None:
                dlg.close()

            def _skip() -> None:
                updater.skip_version(info.version)
                dlg.close()
                ui.notify(f"Skipped v{info.version}", type="info")

            def _install() -> None:
                dlg.close()
                show_update_progress(info)

            ui.button("Skip this version", on_click=_skip).props("flat")
            ui.button("Remind me later", on_click=_later).props("flat")
            ui.button("Install now", on_click=_install).props("color=primary")
    dlg.open()


# ══════════════════════════════════════════════════════════════════════════
# INSTALL PROGRESS
# ══════════════════════════════════════════════════════════════════════════

def show_update_progress(info: UpdateInfo) -> None:
    """Run download → verify → hand off to installer with visible progress."""
    dlg = ui.dialog().props("persistent")
    with dlg, ui.card().style("min-width: 420px;"):
        ui.label(f"Downloading {APP_DISPLAY_NAME} v{info.version}…").classes("text-h6")
        pct_label = ui.label("0%").classes("text-sm text-grey-6")
        bar = ui.linear_progress(value=0, show_value=False).classes("w-full")
        status = ui.label("").classes("text-xs text-grey-6")
        with ui.row().classes("w-full justify-end"):
            cancel_btn = ui.button("Cancel").props("flat")

    cancelled = threading.Event()

    def _on_cancel() -> None:
        cancelled.set()
        dlg.close()
        ui.notify("Update cancelled", type="warning")

    cancel_btn.on("click", _on_cancel)
    dlg.open()

    latest = {"done": 0, "total": info.asset_size or 0}

    def _progress(done: int, total: int) -> None:
        latest["done"] = done
        latest["total"] = total or latest["total"]

    async def _tick() -> None:
        while not cancelled.is_set() and dlg.value:
            total = latest["total"] or 1
            frac = min(1.0, latest["done"] / total) if total else 0.0
            bar.set_value(frac)
            pct_label.text = f"{int(frac * 100)}% — {latest['done'] / 1_000_000:.1f} / " \
                             f"{total / 1_000_000:.1f} MB"
            await asyncio.sleep(0.25)

    def _work() -> str:
        path = updater.download_update(info, progress=_progress)
        return str(path)

    async def _run() -> None:
        ticker = asyncio.create_task(_tick())
        try:
            try:
                path = await run.io_bound(_work)
            except UpdateError as exc:
                dlg.close()
                ui.notify(f"Update failed: {exc}", type="negative", multi_line=True)
                logger.warning("Update download failed: %s", exc)
                return
            except Exception as exc:
                dlg.close()
                ui.notify(f"Update failed: {exc}", type="negative", multi_line=True)
                logger.exception("Unexpected update error")
                return
            if cancelled.is_set():
                return
            status.text = "Verifying signature…"
            bar.set_value(1.0)
            pct_label.text = "100%"
            try:
                await run.io_bound(updater.install_and_restart, __import__("pathlib").Path(path))
            except UpdateError as exc:
                dlg.close()
                ui.notify(f"Install failed: {exc}", type="negative", multi_line=True)
                return
            status.text = f"Installer launched. {APP_DISPLAY_NAME} will now exit."
        finally:
            ticker.cancel()

    asyncio.create_task(_run())


# ══════════════════════════════════════════════════════════════════════════
# SETTINGS SECTION
# ══════════════════════════════════════════════════════════════════════════

def build_update_section() -> None:
    """Render an 'About & Updates' section. Call from inside a NiceGUI
    container (e.g. the Preferences tab)."""
    st = updater.get_update_state()
    dev = updater.is_dev_install()

    ui.label("⬆ Updates").classes("text-subtitle2 q-mt-md")
    ui.label(
        f"{APP_DISPLAY_NAME} checks {APP_REPOSITORY_URL} for new releases in "
        f"the background. You'll be prompted before anything is installed."
    ).classes("text-grey-6 text-xs")

    with ui.row().classes("items-center gap-4 q-mt-sm"):
        ui.label(f"Current version: v{__version__}").classes("text-sm")
        channel_sel = ui.select(
            ["stable", "beta"], value=st.channel, label="Channel"
        ).classes("w-32")

        def _on_channel(e) -> None:
            updater.set_channel(e.value)
            ui.notify(f"Update channel: {e.value}", type="info")

        channel_sel.on_value_change(_on_channel)

    if dev:
        ui.label(
            "Dev install detected — automatic updates disabled."
        ).classes("text-warning text-xs")
        return

    status_label = ui.label("").classes("text-xs text-grey-6")

    def _refresh_status() -> None:
        st2 = updater.get_update_state()
        if st2.available:
            status_label.text = (
                f"Update available: v{st2.available.version} "
                f"(published {st2.available.published_at[:10] if st2.available.published_at else '?'})"
            )
        elif st2.last_check:
            status_label.text = f"Last check: {st2.last_check[:19].replace('T', ' ')} UTC — up to date"
        else:
            status_label.text = "No check performed yet."

    _refresh_status()

    with ui.row().classes("gap-2 q-mt-sm"):

        async def _check_now() -> None:
            check_btn.props("loading")
            try:
                info = await run.io_bound(lambda: updater.check_for_updates(force=True))
            finally:
                check_btn.props(remove="loading")
            _refresh_status()
            if info:
                ui.notify(f"Update available: v{info.version}", type="positive")
            else:
                ui.notify("You're on the latest version", type="info")

        check_btn = ui.button("Check for updates", icon="refresh", on_click=_check_now)

        def _open_dialog() -> None:
            info = updater.get_update_state().available
            if info:
                show_update_dialog(info)
            else:
                ui.notify("No update available", type="info")

        ui.button("View update", icon="info", on_click=_open_dialog).props("flat")

    if st.skipped_versions:
        ui.label(f"Skipped versions: {', '.join(st.skipped_versions)}").classes(
            "text-xs text-grey-6 q-mt-sm"
        )

        def _clear_skipped() -> None:
            st3 = updater.get_update_state()
            st3.skipped_versions.clear()
            updater._save_state(st3)
            ui.notify("Cleared skipped versions — recheck to see again.", type="info")

        ui.button("Clear skipped", on_click=_clear_skipped).props("flat size=sm")
