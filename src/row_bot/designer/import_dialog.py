"""Designer — import dialog for uploading PPTX/DOCX files."""

from __future__ import annotations

import logging
from typing import Callable, Optional

from nicegui import ui

from row_bot.designer.session import prepare_project_mutation
from row_bot.designer.state import DesignerProject
from row_bot.designer.storage import save_project
from row_bot.designer.ui_theme import dialog_card_style, style_ghost_button, style_primary_button

logger = logging.getLogger(__name__)


def show_import_dialog(
    project: DesignerProject,
    on_done: Optional[Callable] = None,
) -> None:
    """Open a dialog to import PPTX or DOCX files into the project."""

    with ui.dialog() as dlg, ui.card().style(
        dialog_card_style(min_width="450px", max_width="560px", max_height="80vh")
    ):
        ui.label("Import Document").classes("text-h6 text-weight-bold q-mb-sm")
        ui.label(
            "Upload a PPTX or DOCX file to import its content as designer pages."
        ).classes("text-grey-5 text-sm q-mb-md")

        status = ui.label("").classes("text-sm q-mb-sm")
        preview_area = ui.column().classes("w-full q-mb-md").style(
            "max-height: 300px; overflow-y: auto;"
        )
        imported_pages_ref: list = []

        async def _handle_upload(e):
            """Process the uploaded file."""
            if not getattr(e, "file", None):
                status.set_text("No file content received.")
                return

            file_bytes = await e.file.read()
            filename = e.file.name.lower() if e.file.name else ""

            status.set_text(f"Processing {e.file.name}…")
            preview_area.clear()

            try:
                if filename.endswith(".pptx"):
                    from row_bot.designer.importer import import_pptx
                    pages = import_pptx(file_bytes)
                elif filename.endswith(".docx"):
                    from row_bot.designer.importer import import_docx
                    pages = import_docx(file_bytes)
                else:
                    status.set_text("Unsupported format. Please upload a .pptx or .docx file.")
                    return

                imported_pages_ref.clear()
                imported_pages_ref.extend(pages)

                status.set_text(f"Found {len(pages)} page(s). Preview:")
                with preview_area:
                    for i, page in enumerate(pages):
                        with ui.row().classes("w-full items-center gap-2").style(
                            "padding: 6px 8px; background: rgba(255,255,255,0.04); "
                            "border-radius: 6px; margin-bottom: 4px;"
                        ):
                            ui.label(f"{i + 1}.").classes("text-grey-5 text-sm").style("min-width: 24px;")
                            ui.label(page.title[:50]).classes("text-sm")
                            if page.notes:
                                ui.icon("speaker_notes", size="xs").classes("text-grey-5").tooltip("Has speaker notes")

                confirm_btn.set_visibility(True)

            except Exception as ex:
                logger.exception("Import failed")
                status.set_text(f"Import failed: {ex}")

        ui.upload(
            label="Choose PPTX or DOCX",
            on_upload=_handle_upload,
            auto_upload=True,
        ).props('accept=".pptx,.docx" flat bordered').classes("w-full").style(
            "max-width: 100%;"
        )

        with ui.row().classes("w-full justify-end gap-2 q-mt-md"):
            cancel_btn = ui.button("Cancel", on_click=dlg.close)
            style_ghost_button(cancel_btn)

            def _confirm():
                if not imported_pages_ref:
                    return
                from row_bot.designer.render_assets import normalize_inline_image_sources

                prepare_project_mutation(project, "import_pages_ui")
                normalized_pages = []
                for imported_page in imported_pages_ref:
                    normalized_html, _ = normalize_inline_image_sources(
                        imported_page.html,
                        project,
                        default_asset_kind="imported-image",
                    )
                    normalized_pages.append(
                        imported_page.__class__(
                            html=normalized_html,
                            title=imported_page.title,
                            notes=imported_page.notes,
                            thumbnail_b64=imported_page.thumbnail_b64,
                        )
                    )
                # Append or replace?
                if replace_toggle.value:
                    project.pages = normalized_pages
                    project.active_page = 0
                else:
                    project.pages.extend(normalized_pages)
                project.manual_edits.append(
                    "User imported document pages via UI. "
                    f"Mode: {'replace' if replace_toggle.value else 'append'}. "
                    f"Imported {len(normalized_pages)} pages."
                )
                save_project(project)
                dlg.close()
                n = len(imported_pages_ref)
                mode = "Replaced all pages" if replace_toggle.value else f"Appended {n} pages"
                logger.info("%s in project %s", mode, project.id)
                if on_done:
                    on_done()

            replace_toggle = ui.switch("Replace existing pages").props("dense").classes("text-sm")
            confirm_btn = ui.button("Import", icon="download", on_click=_confirm)
            style_primary_button(confirm_btn)
            confirm_btn.set_visibility(False)

    dlg.open()
