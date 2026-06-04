"""Designer — export format picker dialog (PDF, PPTX, HTML, PNG)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import subprocess

from nicegui import ui

from row_bot.designer.state import DesignerProject
from row_bot.designer.ui_theme import (
    SECTION_LABEL_CLASSES,
    SECTION_LABEL_STYLE,
    dialog_card_style,
    style_ghost_button,
    style_primary_button,
    style_secondary_button,
)

logger = logging.getLogger(__name__)


def _open_folder(path: str) -> None:
    folder = os.path.dirname(path) or path
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(folder)
        elif system == "Darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
    except Exception as exc:
        ui.notify(f"Failed to open folder: {exc}", type="negative")


def show_export_dialog(project: DesignerProject) -> None:
    """Open a modal dialog for choosing export format and page range."""

    with ui.dialog() as dlg, ui.card().style(dialog_card_style(min_width="460px", max_width="560px")):
        ui.label("Export Project").classes("text-h6 text-weight-bold")
        ui.label("Export the current deck with the right fidelity for the next handoff.").classes(
            "text-sm text-grey-5"
        )
        ui.separator()

        ui.label("Quick Presets").classes(SECTION_LABEL_CLASSES + " q-mt-sm").style(SECTION_LABEL_STYLE)

        with ui.row().classes("w-full q-mt-xs flex-wrap").style("gap: 8px;"):
            pdf_preset_btn = ui.button("Presentation PDF", on_click=lambda: _apply_preset("pdf", "all"))
            style_secondary_button(pdf_preset_btn, compact=True)
            pptx_preset_btn = ui.button("Editable PPTX", on_click=lambda: _apply_preset("pptx", "all", mode="structured"))
            style_secondary_button(pptx_preset_btn, compact=True)
            html_preset_btn = ui.button("Shareable HTML", on_click=lambda: _apply_preset("html", "all"))
            style_secondary_button(html_preset_btn, compact=True)
            png_preset_btn = ui.button("Current Slide PNG", on_click=lambda: _apply_preset("png", "current"))
            style_secondary_button(png_preset_btn, compact=True)

        ui.label("Format").classes(SECTION_LABEL_CLASSES + " q-mt-sm").style(SECTION_LABEL_STYLE)
        format_toggle = ui.toggle(
            {"pdf": "PDF", "pptx": "PPTX", "html": "HTML", "png": "PNG"},
            value="pdf",
        ).props("no-caps dense outline color=grey-8")

        pptx_mode_row = ui.row().classes("w-full q-mt-xs")
        pptx_mode_row.visible = False
        with pptx_mode_row:
            ui.label("PPTX Mode").classes("text-xs text-grey-5")
            pptx_mode = ui.toggle(
                {"screenshot": "High-Fidelity", "structured": "Editable"},
                value="screenshot",
            ).props("no-caps dense outline size=sm")
        pptx_hint = ui.label("").classes("text-xs text-grey-6 q-ml-sm")
        pptx_hint.visible = False

        def _update_pptx_hint(value: str) -> None:
            pptx_hint.text = (
                "Pixel-perfect screenshots as slide images. Not text-editable."
                if value == "screenshot"
                else "Extracts headings and text as editable text boxes."
            )
            pptx_hint.update()

        def _on_format_change(_e=None) -> None:
            is_pptx = format_toggle.value == "pptx"
            pptx_mode_row.visible = is_pptx
            pptx_mode_row.update()
            pptx_hint.visible = is_pptx
            pptx_hint.update()
            if is_pptx:
                _update_pptx_hint(pptx_mode.value)

        format_toggle.on("update:model-value", _on_format_change)
        pptx_mode.on("update:model-value", lambda _e: _update_pptx_hint(pptx_mode.value))

        ui.label("Pages").classes(SECTION_LABEL_CLASSES + " q-mt-md").style(SECTION_LABEL_STYLE)
        page_range = ui.toggle(
            {"all": "All", "current": "Current Page", "range": "Range"},
            value="all",
        ).props("no-caps dense outline color=grey-8")

        range_input = ui.input(placeholder="e.g. 1-3 or 1,3,5").props("dense outlined")
        range_input.classes("q-mt-xs").style("width: 100%;")
        range_input.visible = False

        def _on_range_change(_e=None) -> None:
            range_input.visible = page_range.value == "range"
            range_input.update()

        page_range.on("update:model-value", _on_range_change)

        def _apply_preset(fmt: str, pages_value: str, *, mode: str | None = None) -> None:
            format_toggle.value = fmt
            format_toggle.update()
            page_range.value = pages_value
            page_range.update()
            range_input.visible = pages_value == "range"
            range_input.update()
            pptx_mode_row.visible = fmt == "pptx"
            pptx_mode_row.update()
            pptx_hint.visible = fmt == "pptx"
            pptx_hint.update()
            if mode is not None:
                pptx_mode.value = mode
                pptx_mode.update()
            if fmt == "pptx":
                _update_pptx_hint(pptx_mode.value)

        from row_bot.designer.export import get_export_workspace

        total = len(project.pages)
        ui.label(
            f"{total} page{'s' if total != 1 else ''} · "
            f"{project.aspect_ratio} ({project.canvas_width}×{project.canvas_height})"
        ).classes("text-xs text-grey-6 q-mt-sm")
        ui.label(f"Exports save to {get_export_workspace()}").classes("text-xs text-grey-5")

        status_label = ui.label("").classes("text-xs text-grey-5 q-mt-sm")
        path_label = ui.label("").classes("text-xs text-grey-4")
        path_label.visible = False
        _last_path: list[str] = [""]

        with ui.row().classes("w-full items-center q-mt-xs").style("gap: 8px;") as action_row:
            copy_btn = ui.button("Copy Path")
            style_ghost_button(copy_btn, compact=True)
            open_btn = ui.button("Open Folder")
            style_ghost_button(open_btn, compact=True)
        action_row.visible = False

        def _copy_path() -> None:
            if not _last_path[0]:
                return
            ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(_last_path[0])})")
            ui.notify("Export path copied to clipboard.", type="positive")

        def _open_export_folder() -> None:
            if _last_path[0]:
                _open_folder(_last_path[0])

        copy_btn.on_click(_copy_path)
        open_btn.on_click(_open_export_folder)

        def _resolve_pages() -> str | None:
            if page_range.value == "current":
                return str(project.active_page + 1)
            if page_range.value == "range":
                return range_input.value.strip() or "all"
            return None

        with ui.row().classes("w-full justify-end q-mt-md"):
            close_btn = ui.button("Close", on_click=dlg.close)
            style_ghost_button(close_btn)

            async def _do_export() -> None:
                fmt = format_toggle.value
                pages_str = _resolve_pages()
                mode = pptx_mode.value if fmt == "pptx" else None

                status_label.text = f"Exporting {fmt.upper()}…"
                export_btn.disable()

                try:
                    from row_bot.designer.export import (
                        describe_export_destination,
                        export_html,
                        export_pdf,
                        export_png,
                        export_pptx_screenshot,
                        export_pptx_structured,
                    )

                    expected_path = describe_export_destination(project, fmt, pages_str, mode)
                    loop = asyncio.get_event_loop()

                    if fmt == "pdf":
                        data = await loop.run_in_executor(None, lambda: export_pdf(project, pages_str))
                    elif fmt == "html":
                        data = await loop.run_in_executor(None, lambda: export_html(project, pages_str))
                    elif fmt == "png":
                        data = await loop.run_in_executor(None, lambda: export_png(project, pages_str))
                    elif fmt == "pptx":
                        if mode == "structured":
                            data = await loop.run_in_executor(None, lambda: export_pptx_structured(project, pages_str))
                        else:
                            data = await loop.run_in_executor(None, lambda: export_pptx_screenshot(project, pages_str))
                    else:
                        raise ValueError(f"Unknown format: {fmt}")

                    actual_path = getattr(data, "saved_path", expected_path)
                    size_kb = len(data) / 1024
                    _last_path[0] = str(actual_path)
                    status_label.text = (
                        f"Done — {size_kb:.0f} KB"
                        if actual_path == expected_path
                        else f"Done — {size_kb:.0f} KB (saved as {actual_path.name})"
                    )
                    path_label.text = _last_path[0]
                    path_label.visible = True
                    path_label.update()
                    action_row.visible = True
                    action_row.update()
                    if actual_path == expected_path:
                        ui.notify(
                            f"Exported {fmt.upper()} ({size_kb:.0f} KB) to {actual_path.parent}",
                            type="positive",
                        )
                    else:
                        ui.notify(
                            f"Exported {fmt.upper()} ({size_kb:.0f} KB). Target file was busy, so it was saved as {actual_path.name}.",
                            type="warning",
                        )
                except Exception as exc:
                    logger.exception("Export failed")
                    status_label.text = f"Error: {exc}"
                    ui.notify(f"Export failed: {exc}", type="negative")
                finally:
                    export_btn.enable()

            export_btn = ui.button("Export", icon="download", on_click=_do_export)
            style_primary_button(export_btn)

    dlg.open()