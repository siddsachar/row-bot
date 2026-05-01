from __future__ import annotations

from collections.abc import Callable

from nicegui import run, ui

from providers.model_catalog import CatalogModelRow, group_rows_by_provider, rows_for_surface


SURFACE_LABELS = {
    "chat": "Chat",
    "vision": "Vision",
    "image": "Image",
    "video": "Video",
}


def build_model_catalog_section(
    rows: list[CatalogModelRow],
    *,
    on_set_default: Callable[[str, CatalogModelRow], None] | None = None,
    on_download: Callable[[CatalogModelRow], object] | None = None,
    on_change: Callable[[], None] | None = None,
) -> None:
    state = {"surface": "chat", "query": "", "provider": ""}
    pinned_by_ref = {row.selection_ref: set(row.pinned_surfaces) for row in rows}
    defaults_by_ref = {row.selection_ref: set(row.default_surfaces) for row in rows}
    provider_options = {"": "All providers"}
    for row in rows:
        provider_options.setdefault(row.provider_id, row.provider_display_name or row.provider_id)

    with ui.expansion("Model Catalog", icon="view_list", value=False).classes("w-full"):
        ui.label("Browse every discovered model by category, then provider. Pin models here to make them available in everyday pickers.").classes("text-grey-6 text-sm")
        with ui.row().classes("items-center gap-2 w-full"):
            category = ui.toggle(SURFACE_LABELS, value="chat").props("dense unelevated toggle-color=primary")
            provider_filter = ui.select(provider_options, value="", label="Provider").props("dense outlined").classes("min-w-[180px]")
            search = ui.input(placeholder="Search models...").props("dense outlined clearable").classes("min-w-[220px] flex-grow")
        container = ui.column().classes("w-full gap-2 q-mt-sm")

        def _refresh() -> None:
            container.clear()
            surface = str(state["surface"] or "chat")
            query = str(state["query"] or "").strip().lower()
            provider = str(state["provider"] or "")
            surface_rows = rows_for_surface(rows, surface)
            if provider:
                surface_rows = [row for row in surface_rows if row.provider_id == provider]
            if query:
                surface_rows = [
                    row for row in surface_rows
                    if query in " ".join([row.display_name, row.model_id, row.provider_display_name, row.provider_id]).lower()
                ]
            with container:
                if not surface_rows:
                    ui.label("No models match this catalog view.").classes("text-grey-6 text-sm")
                    return
                for provider_id, provider_rows in group_rows_by_provider(surface_rows).items():
                    provider_label = provider_rows[0].provider_display_name or provider_id
                    with ui.expansion(f"{provider_label} ({len(provider_rows)})", value=not provider).classes("w-full"):
                        with ui.column().classes("w-full gap-1"):
                            for row in provider_rows:
                                _render_row(row, surface)

        def _on_category(e) -> None:
            state["surface"] = e.value or "chat"
            _refresh()

        def _on_provider(e) -> None:
            state["provider"] = e.value or ""
            _refresh()

        def _on_search(e) -> None:
            state["query"] = e.value or ""
            _refresh()

        category.on_value_change(_on_category)
        provider_filter.on_value_change(_on_provider)
        search.on_value_change(_on_search)

        def _render_row(row: CatalogModelRow, surface: str) -> None:
            pinned = surface in pinned_by_ref.get(row.selection_ref, set())
            is_default = surface in defaults_by_ref.get(row.selection_ref, set())
            can_use = row.runtime_ready and row.configured and row.installed
            with ui.row().classes("items-center gap-2 no-wrap w-full q-py-xs").style("border-bottom: 1px solid rgba(148, 163, 184, 0.12);"):
                ui.label(row.provider_icon or "AI").classes("text-sm").style("width: 22px; text-align: center;")
                with ui.column().classes("gap-0").style("min-width: 0; flex: 1;"):
                    ui.label(row.display_name).classes("text-sm text-weight-medium").style("line-height: 1.15;")
                    if row.display_name != row.model_id:
                        ui.label(row.model_id).classes("text-grey-6 text-xs").style("line-height: 1.15; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;")
                ui.badge(row.provider_id, color="blue-grey").props("outline dense")
                if row.context_window:
                    ui.badge(_ctx_label(row.context_window), color="grey").props("outline dense")
                for category_id in row.categories:
                    ui.badge(SURFACE_LABELS.get(category_id, category_id), color="grey").props("outline dense")
                if row.downloadable:
                    ui.badge("download", color="orange").props("outline dense")
                elif not row.configured:
                    ui.badge("connect", color="orange").props("outline dense")
                elif not row.runtime_ready:
                    ui.badge("unavailable", color="orange").props("outline dense")
                if is_default:
                    ui.badge("default", color="cyan").props("outline dense")
                ui.space()
                if row.status_reason:
                    ui.icon("info", size="xs").tooltip(row.status_reason)
                if row.downloadable and on_download:
                    ui.button(icon="download", on_click=lambda _, r=row: _download(r)).props("flat dense round size=sm color=primary").tooltip("Download model")
                pin_button = ui.button(icon="push_pin", on_click=lambda _, r=row, s=surface: _toggle_pin(r, s)).props(f"flat dense round size=sm color={'primary' if pinned else 'grey'}").tooltip("Remove from picker" if pinned else "Pin to picker")
                if not can_use:
                    pin_button.disable()
                default_button = ui.button(icon="check", on_click=lambda _, r=row, s=surface: _set_default(r, s)).props("flat dense round size=sm").tooltip("Set default")
                if not can_use or is_default:
                    default_button.disable()

        def _toggle_pin(row: CatalogModelRow, surface: str) -> None:
            from providers.selection import add_quick_choice_for_model, remove_quick_choice_for_model

            if surface in pinned_by_ref.get(row.selection_ref, set()):
                remove_quick_choice_for_model(row.model_id, provider_id=row.provider_id)
                pinned_by_ref.setdefault(row.selection_ref, set()).discard(surface)
                ui.notify("Removed from picker", type="info")
            else:
                add_quick_choice_for_model(
                    row.model_id,
                    provider_id=row.provider_id,
                    display_name=row.display_name,
                    source="models_catalog",
                    capabilities_snapshot=row.capabilities_snapshot,
                    surface=surface,
                )
                pinned_by_ref.setdefault(row.selection_ref, set()).add(surface)
                ui.notify("Pinned to picker", type="positive")
            if on_change:
                on_change()
            _refresh()

        def _set_default(row: CatalogModelRow, surface: str) -> None:
            from providers.selection import add_quick_choice_for_model

            add_quick_choice_for_model(
                row.model_id,
                provider_id=row.provider_id,
                display_name=row.display_name,
                source="models_catalog_default",
                capabilities_snapshot=row.capabilities_snapshot,
                surface=surface,
            )
            if on_set_default:
                on_set_default(surface, row)
            for ref, surfaces in defaults_by_ref.items():
                surfaces.discard(surface)
            defaults_by_ref.setdefault(row.selection_ref, set()).add(surface)
            pinned_by_ref.setdefault(row.selection_ref, set()).add(surface)
            if on_change:
                on_change()
            _refresh()

        async def _download(row: CatalogModelRow) -> None:
            if not on_download:
                return
            notification = ui.notification(f"Downloading {row.model_id}...", type="ongoing", spinner=True, timeout=None)
            try:
                await run.io_bound(on_download, row)
                notification.dismiss()
                ui.notify(f"{row.model_id} ready", type="positive")
                if on_change:
                    on_change()
            except Exception as exc:
                notification.dismiss()
                ui.notify(f"Download failed: {exc}", type="negative")

        _refresh()


def _ctx_label(ctx: int) -> str:
    if ctx >= 1_000_000:
        return f"{ctx // 1_000_000}M ctx"
    if ctx >= 1_000:
        return f"{ctx // 1_000}K ctx"
    return f"{ctx} ctx"