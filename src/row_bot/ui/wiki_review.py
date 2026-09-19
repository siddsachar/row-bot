"""Explicit local review of a vault article before importing its contents."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import logging

from nicegui import run, ui

from row_bot import wiki_vault
from row_bot.ui.performance import safe_ui_callback, safe_ui_task

logger = logging.getLogger(__name__)


def open_wiki_import_review(
    item: Mapping[str, object],
    on_saved: Callable[[], None] | None = None,
) -> None:
    """Show both captured versions; accepting binds both reviewed revisions."""
    entity_id = item.get("entity_id")
    filepath = item.get("vault_path")
    if not isinstance(entity_id, str) or not isinstance(filepath, str):
        ui.notify(
            "This vault item is unavailable. Check vault sync again.", type="warning"
        )
        return

    review: dict[str, str] | None = None
    busy = False
    loading = False
    attempted = False
    closed = False

    def close() -> None:
        nonlocal closed
        if busy:
            return
        closed = True
        dialog.close()

    def hide() -> None:
        nonlocal closed
        closed = True

    async def reload_review() -> None:
        nonlocal review, attempted, loading
        if busy or loading or closed:
            return
        loading = True
        review = None
        accept_button.disable()
        reload_button.disable()
        status.set_text("Loading both versions…")
        try:
            captured = await run.io_bound(
                wiki_vault.read_import_review, entity_id, filepath
            )
        except Exception:
            logger.debug("Wiki import review could not be loaded", exc_info=True)
            captured = None
        finally:
            loading = False
        if closed:
            return
        reload_button.enable()
        if captured is None:
            database.value = ""
            vault.value = ""
            status.set_text(
                "Could not load both versions. Reload the review or cancel and check vault sync again."
            )
            return
        review = captured
        attempted = False
        title.set_text(f"Review vault import: {captured['subject']}")
        database.value = captured["database_text"]
        vault.value = captured["vault_text"]
        status.set_text(
            "Compare both versions, then explicitly accept the vault version to import it."
        )
        accept_button.enable()

    async def accept() -> None:
        nonlocal busy, attempted, closed
        if busy or loading or attempted or closed or review is None:
            return
        captured = review
        busy = True
        attempted = True
        accept_button.disable()
        reload_button.disable()
        cancel_button.disable()
        status.set_text("Importing the reviewed vault version…")
        try:
            saved = await run.io_bound(
                wiki_vault.import_from_vault,
                captured["entity_id"],
                captured["vault_path"],
                expected_db_revision=captured["expected_db_revision"],
                expected_vault_hash=captured["expected_vault_hash"],
            )
        except Exception:
            logger.debug("Reviewed wiki import failed", exc_info=True)
            saved = False
        finally:
            busy = False
            reload_button.enable()
            cancel_button.enable()
        if not saved:
            status.set_text(
                "Import could not be confirmed. A version may have changed or saving failed. Reload both versions before trying again."
            )
            return
        closed = True
        dialog.close()
        ui.notify("Reviewed vault version imported.", type="positive")
        if on_saved is not None:
            safe_ui_callback("wiki_review.on_saved", on_saved)()

    with (
        ui.dialog().props("persistent") as dialog,
        ui.card()
        .classes("w-full")
        .style("width: 960px; max-width: 95vw; max-height: 90vh; overflow-y: auto;"),
    ):
        dialog.on("hide", hide)
        title = ui.label("Review vault import").classes("text-h6")
        ui.label(
            "Both complete versions are shown as plain text. Accepting imports the vault fields into the database; retained recovery copies stay available."
        ).classes("text-sm text-grey-6")
        with ui.row().classes("w-full items-start gap-4"):
            database = (
                ui.textarea("Database version — all saved fields")
                .classes("flex-1 min-w-0 w-full")
                .style("min-width: min(320px, 100%);")
                .props("readonly outlined rows=14")
            )
            vault = (
                ui.textarea("Vault version")
                .classes("flex-1 min-w-0 w-full")
                .style("min-width: min(320px, 100%);")
                .props("readonly outlined rows=14")
            )
        status = ui.label("").classes("text-sm").props("role=status aria-live=polite")
        with ui.row().classes("w-full justify-end gap-2"):
            cancel_button = ui.button("Cancel", on_click=close).props("flat no-caps")
            reload_button = ui.button(
                "Reload review", icon="refresh", on_click=reload_review
            ).props("flat no-caps")
            accept_button = ui.button(
                "Accept vault version", icon="check", on_click=accept
            ).props("color=primary no-caps")
    accept_button.disable()
    dialog.open()
    safe_ui_task("wiki_review.load", reload_review)
