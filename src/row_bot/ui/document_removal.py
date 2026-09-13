"""Report and resume canonical document removal without repeating cleanup owners."""

from __future__ import annotations

from collections.abc import Callable
import logging

from nicegui import run, ui

from row_bot.ui.performance import safe_ui_callback, safe_ui_task

logger = logging.getLogger(__name__)


def open_document_removal(
    document_id: str | None,
    name: str,
    on_complete: Callable[[], None] | None = None,
) -> None:
    """Start an already authorized removal and retain its receipt for retry."""
    from row_bot.documents import clear_documents_details, remove_document_details

    removal_id: str | None = None
    busy = False
    complete = False
    closed = False

    def close() -> None:
        nonlocal closed
        if busy or closed:
            return
        closed = True
        dialog.close()
        if complete and on_complete is not None:
            safe_ui_callback("document_removal.on_complete", on_complete)()

    async def attempt() -> None:
        nonlocal removal_id, busy, complete
        if busy or closed or complete:
            return
        busy = True
        retry.disable()
        close_button.disable()
        status.set_text(f"Removing {name}…")
        try:
            result = await run.io_bound(
                clear_documents_details
                if document_id is None
                else remove_document_details,
                *(() if document_id is None else (document_id,)),
                removal_id=removal_id,
            )
        except Exception:
            logger.debug("Document removal could not be confirmed", exc_info=True)
            status.set_text(
                "Removal could not be confirmed. Retry to resume its saved operation."
            )
        else:
            removal_id = result["removal_id"]
            complete = result["status"] == "complete"
            status.set_text(
                f"Removed {name}."
                if complete
                else "Removal is pending while a worker stops. Retry to check and continue."
                if result["status"] == "pending"
                else "Removal is incomplete. Completed stages are saved; retry the remaining cleanup."
            )
            details.clear()
            with details:
                ui.label(f"Recovery reference: {removal_id}").classes(
                    "text-sm break-all"
                )
                ui.label(
                    f"Document-derived knowledge removed: {result['derived_entities_removed']}"
                ).classes("text-sm")
                if result["retained_copies"]:
                    ui.label(
                        "Recovery copies and externally edited files have been retained."
                    ).classes("text-sm")
                for stage, outcome in result["stages"].items():
                    ui.label(f"{stage}: {outcome}").classes("text-sm break-all")
                if result["failures"]:
                    ui.label(
                        "Some cleanup stages need another attempt or review. Existing files are preserved where ownership could not be confirmed."
                    ).classes("text-sm")
        finally:
            busy = False
            close_button.enable()
            if not complete:
                retry.enable()

    with (
        ui.dialog().props("persistent") as dialog,
        ui.card()
        .classes("w-full")
        .style("width: 640px; max-width: 95vw; max-height: 90vh; overflow-y: auto;"),
    ):
        ui.label("Document removal").classes("text-h6")
        status = ui.label("").props("role=status aria-live=polite")
        details = ui.column().classes("w-full gap-1")
        with ui.row().classes("w-full justify-end gap-2"):
            close_button = ui.button("Close", on_click=close).props("flat no-caps")
            retry = ui.button("Retry removal", icon="refresh", on_click=attempt).props(
                "no-caps"
            )
    retry.disable()
    dialog.open()
    safe_ui_task("document_removal.run", attempt)
