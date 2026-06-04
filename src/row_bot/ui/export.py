"""Row-Bot UI export conversation dialog.

Self-contained dialog builder.  Receives ``state`` and ``p`` explicitly.
"""

from __future__ import annotations

import logging
import os
import pathlib
import re

from row_bot.brand import APP_NATIVE_ENV
from nicegui import ui

from row_bot.ui.state import AppState, P
from row_bot.ui.helpers import export_as_markdown, export_as_text, export_as_pdf

logger = logging.getLogger(__name__)


def _safe_filename(name: str) -> str:
    """Strip characters illegal in Windows filenames (\\/:*?\"<>|)."""
    return re.sub(r'[\\/:*?"<>|]', '-', name).strip('- ')


def _save_export(data: bytes, filename: str) -> None:
    """Deliver an export file to the user.

    In native mode (pywebview) blob downloads are silently ignored on
    macOS WebKit, so we write directly to ~/Downloads and notify.
    In browser mode we use the normal ``ui.download()`` API.
    """
    filename = _safe_filename(filename)
    if os.environ.get(APP_NATIVE_ENV) == "1":
        dl_dir = pathlib.Path.home() / "Downloads"
        dl_dir.mkdir(parents=True, exist_ok=True)
        dest = dl_dir / filename
        # Avoid overwriting — append (1), (2), … if needed
        counter = 1
        while dest.exists():
            stem = pathlib.Path(filename).stem
            suffix = pathlib.Path(filename).suffix
            dest = dl_dir / f"{stem} ({counter}){suffix}"
            counter += 1
        dest.write_bytes(data)
        ui.notify(f"Saved to {dest}", type="positive")
    else:
        ui.download(data, filename)


def open_export(state: AppState, p: P) -> None:
    """Open the export conversation dialog."""
    if not state.messages:
        ui.notify("Nothing to export.", type="warning")
        return
    name = state.thread_name or "conversation"
    msgs = state.messages
    p.export_dlg.clear()
    with p.export_dlg, ui.card().classes("w-96"):
        ui.label("📤 Export Conversation").classes("text-h6")
        ui.separator()
        with ui.column().classes("w-full gap-2"):
            def dl_md():
                try:
                    data = export_as_markdown(name, msgs).encode("utf-8")
                    fname = f"{name}.md"
                    p.export_dlg.close()
                    _save_export(data, fname)
                except Exception as exc:
                    logger.exception("Export markdown failed")
                    ui.notify(f"Export failed: {exc}", type="negative")

            def dl_txt():
                try:
                    data = export_as_text(name, msgs).encode("utf-8")
                    fname = f"{name}.txt"
                    p.export_dlg.close()
                    _save_export(data, fname)
                except Exception as exc:
                    logger.exception("Export text failed")
                    ui.notify(f"Export failed: {exc}", type="negative")

            def dl_pdf():
                try:
                    ui.notify("Generating PDF…", type="info", timeout=3000)
                    data = export_as_pdf(name, msgs, state.thread_id)
                    fname = f"{name}.pdf"
                    p.export_dlg.close()
                    _save_export(data, fname)
                except ImportError:
                    ui.notify("PDF export requires `fpdf2`. Run: pip install fpdf2", type="negative")
                except Exception as exc:
                    logger.exception("Export PDF failed")
                    ui.notify(f"PDF export failed: {exc}", type="negative")

            ui.button("📄 Markdown", on_click=dl_md).classes("w-full")
            ui.button("📃 Plain text", on_click=dl_txt).classes("w-full")
            ui.button("📕 PDF", on_click=dl_pdf).classes("w-full")
        ui.separator()
        ui.button("Close", on_click=p.export_dlg.close).props("flat").classes("w-full")
    p.export_dlg.open()
