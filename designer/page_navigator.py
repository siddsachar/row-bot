"""Designer — horizontal page navigator strip with live iframe thumbnails."""

from __future__ import annotations

import logging
from typing import Callable

from nicegui import ui

from designer.canvas_resize import resize_project_canvas
from designer.session import prepare_project_mutation
from designer.state import DesignerProject, ASPECT_RATIOS, CANVAS_PRESETS
from designer.storage import save_project
from designer.thumbnail import compute_thumbnail_dimensions, render_static_page_thumbnail
from designer.ui_theme import dialog_card_style, style_ghost_button, style_primary_button

logger = logging.getLogger(__name__)

# Modes that treat pages as interactive routes — shared with preview.py.
_INTERACTIVE_NAV_MODES = {"landing", "app_mockup", "storyboard"}


def navigator_item_caption(
    project: DesignerProject, page_index: int
) -> tuple[str, str | None]:
    """Return ``(index_label, route_label)`` for navigator rendering.

    ``route_label`` is ``None`` in deck/document modes so the existing
    look is preserved; in interactive modes it surfaces the page's
    ``route_id`` (falling back to "page-N" when missing).
    """

    index_label = f"{page_index + 1}"
    mode = getattr(project, "mode", "deck") or "deck"
    if mode not in _INTERACTIVE_NAV_MODES:
        return index_label, None
    if not (0 <= page_index < len(project.pages)):
        return index_label, None
    rid = (getattr(project.pages[page_index], "route_id", "") or "").strip()
    if not rid:
        rid = f"page-{page_index + 1}"
    return index_label, rid


def navigator_action_labels(project: DesignerProject) -> dict:
    """Return mode-aware button tooltips used by the navigator."""

    mode = getattr(project, "mode", "deck") or "deck"
    if mode in _INTERACTIVE_NAV_MODES:
        return {
            "add": "Add screen",
            "delete": "Delete active screen",
            "prev": "Previous screen",
            "next": "Next screen",
            "counter": "Screen {current} of {total}",
        }
    return {
        "add": "Add page",
        "delete": "Delete active page",
        "prev": "Previous page",
        "next": "Next page",
        "counter": "Page {current} of {total}",
    }


def _deferred(fn):
    """Schedule a callback via a one-shot timer from the client root.

    The page navigator frequently clears and rebuilds its own slot, so
    creating a timer directly from the current context can fail with
    "parent element deleted" while the UI is being re-rendered.
    Re-enter the client content slot first, then create the timer there.
    """
    if fn:
        def _safe():
            try:
                fn()
            except RuntimeError:
                pass  # parent slot deleted — page navigated away
        try:
            ui.context.client.safe_invoke(
                lambda: ui.timer(0.05, _safe, once=True)
            )
        except RuntimeError:
            pass


def _branded_blank_html(project: DesignerProject, title: str) -> str:
    """Generate a minimal branded HTML skeleton for a new blank page."""
    brand = project.brand
    w, h = project.canvas_width, project.canvas_height
    if brand:
        from designer.preview import _build_brand_css
        brand_css = _build_brand_css(brand)
    else:
        brand_css = ""
    return (
        f"<!DOCTYPE html><html><head>{brand_css}"
        f"<style>html,body{{margin:0;width:{w}px;height:{h}px;overflow:hidden;"
        f"background:var(--bg,#0F172A);color:var(--text,#F8FAFC);"
        f"font-family:var(--body-font,sans-serif);}}"
        f"h1,h2,h3,h4{{font-family:var(--heading-font,sans-serif);}}</style>"
        f"</head><body>"
        f"<div style=\"display:flex;align-items:center;justify-content:center;"
        f"height:100%;\">"
        f"<h1 style=\"font-size:2.5rem;opacity:0.3;\">{title}</h1>"
        f"</div></body></html>"
    )


def build_page_navigator(
    project: DesignerProject,
    *,
    on_page_change: Callable | None = None,
    on_add_page: Callable | None = None,
    on_delete_page: Callable | None = None,
) -> dict:
    """Build the page navigator strip at the bottom of the editor.

    Returns ``{"refresh": callable, "container": ui.element}``.
    """
    _nav_container: ui.row | None = None

    def _confirm_resize(new_ratio: str, label: str, description: str = "") -> None:
        if new_ratio not in ASPECT_RATIOS:
            return
        if new_ratio == project.aspect_ratio:
            ui.notify(
                f"Canvas already uses {label} ({project.canvas_width}×{project.canvas_height}).",
                type="info",
            )
            return

        target_width, target_height = ASPECT_RATIOS[new_ratio]

        with ui.dialog() as confirm_dlg, ui.card().style(dialog_card_style(min_width="420px", max_width="520px")):
            ui.label(f"Resize Canvas to {label}?").classes("text-h6 text-weight-bold")
            ui.label(
                f"This changes the whole project to {new_ratio} ({target_width}×{target_height}). "
                "All pages will be auto-fitted to prevent cropping."
            ).classes("text-sm text-grey-5")
            if description:
                ui.label(description).classes("text-xs text-grey-5")

            with ui.row().classes("w-full justify-end q-mt-sm"):
                cancel_btn = ui.button("Cancel", on_click=confirm_dlg.close)
                style_ghost_button(cancel_btn)

                def _apply_resize() -> None:
                    try:
                        info = resize_project_canvas(
                            project,
                            aspect_ratio=new_ratio,
                            label=label,
                            source="canvas menu",
                        )
                    except Exception as exc:
                        logger.exception("Canvas resize failed")
                        ui.notify(f"Canvas resize failed: {exc}", type="negative")
                        return
                    _render()
                    _deferred(on_page_change)
                    ui.notify(str(info["message"]), type="positive")
                    confirm_dlg.close()

                resize_btn = ui.button("Resize & Auto-fit", on_click=_apply_resize)
                style_primary_button(resize_btn)

        confirm_dlg.open()

    def _render():
        nonlocal _nav_container
        if _nav_container is None:
            return
        _nav_container.clear()
        labels = navigator_action_labels(project)
        with _nav_container:
            # Previous arrow
            def _prev():
                if project.active_page > 0:
                    project.active_page -= 1
                    save_project(project)
                    _render()
                    _deferred(on_page_change)

            ui.button(icon="chevron_left", on_click=_prev).props(
                "flat dense round size=sm"
            ).style(
                "opacity: 0.5;" if project.active_page == 0 else ""
            )

            # Thumbnails — live iframe previews
            _thumb_h = 70  # visible thumbnail height in px
            _cw, _ch = project.canvas_width, project.canvas_height
            _thumb_w, _scale = compute_thumbnail_dimensions(_cw, _ch, _thumb_h)


            for i, page in enumerate(project.pages):
                is_active = i == project.active_page
                border = "2px solid #F59E0B" if is_active else "2px solid rgba(255,255,255,0.1)"

                def _select(idx=i):
                    project.active_page = idx
                    save_project(project)
                    _render()
                    _deferred(on_page_change)

                # Outer wrapper — fixed visible size, overflow hidden
                with ui.element("div").style(
                    f"width:{_thumb_w}px;height:{_thumb_h}px;border:{border};"
                    "border-radius:6px;overflow:hidden;cursor:pointer;"
                    "position:relative;flex-shrink:0;background:#0F172A;"
                ).on("click", _select):
                    # Page number badge
                    _index_label, _route_label = navigator_item_caption(project, i)
                    ui.label(_index_label).classes("text-xs text-grey-5").style(
                        "position:absolute;top:2px;left:6px;z-index:2;"
                        "text-shadow:0 0 3px rgba(0,0,0,0.8);"
                    )
                    if _route_label:
                        ui.label(_route_label).classes(
                            "text-xs text-weight-medium"
                        ).style(
                            "position:absolute;bottom:2px;left:6px;right:6px;"
                            "z-index:2;color:#F59E0B;"
                            "text-shadow:0 0 3px rgba(0,0,0,0.85);"
                            "white-space:nowrap;overflow:hidden;"
                            "text-overflow:ellipsis;"
                        )
                    if page.notes.strip():
                        ui.icon("speaker_notes", size="xs").classes("text-grey-3").style(
                            "position:absolute;top:2px;right:6px;z-index:2;"
                            "text-shadow:0 0 3px rgba(0,0,0,0.8);"
                        )
                    _frame_id = f"thumb-{project.id[:8]}-{i}"
                    render_static_page_thumbnail(
                        frame_id=_frame_id,
                        page_html=page.html,
                        brand=project.brand,
                        project=project,
                        page_index=i,
                        canvas_width=_cw,
                        canvas_height=_ch,
                        preview_height=_thumb_h,
                        empty_label=page.title,
                    )

            # Add page button
            def _add():
                if on_add_page:
                    on_add_page()
                else:
                    from designer.state import DesignerPage
                    new_title = f"Page {len(project.pages) + 1}"
                    # Create a brand-aware blank page
                    blank_html = _branded_blank_html(project, new_title)
                    prepare_project_mutation(project, "add_page_ui")
                    project.pages.append(DesignerPage(title=new_title, html=blank_html))
                    project.active_page = len(project.pages) - 1
                    project.manual_edits.append(
                        f"User added blank page \"{new_title}\" via UI. "
                        f"Now {len(project.pages)} pages total."
                    )
                    save_project(project)
                _render()
                _deferred(on_page_change)

            with ui.column().style("gap:0;align-items:center;"):
                ui.button(icon="add", on_click=_add).props(
                    "flat dense round size=sm"
                ).tooltip(labels["add"])
                if len(project.pages) > 1:
                    def _del_active():
                        idx = project.active_page
                        if on_delete_page:
                            on_delete_page(idx)
                        else:
                            removed_title = project.pages[idx].title
                            prepare_project_mutation(project, "delete_page_ui")
                            project.pages.pop(idx)
                            if idx >= len(project.pages):
                                project.active_page = len(project.pages) - 1
                            project.manual_edits.append(
                                f"User deleted page {idx + 1} \"{removed_title}\" via UI. "
                                f"Now {len(project.pages)} pages remain."
                            )
                            save_project(project)
                        _render()
                        _deferred(on_page_change)
                    ui.button(icon="close", on_click=_del_active, color="red").props(
                        "flat dense round size=sm"
                    ).tooltip(labels["delete"])

            # Next arrow
            def _next():
                if project.active_page < len(project.pages) - 1:
                    project.active_page += 1
                    save_project(project)
                    _render()
                    _deferred(on_page_change)

            ui.button(icon="chevron_right", on_click=_next).props(
                "flat dense round size=sm"
            ).style(
                "opacity: 0.5;" if project.active_page >= len(project.pages) - 1 else ""
            )

            # Spacer
            ui.element("div").style("flex: 1;")

            # Page counter
            ui.label(
                labels["counter"].format(
                    current=project.active_page + 1,
                    total=len(project.pages),
                )
            ).classes("text-xs text-grey-5").style("white-space: nowrap;")

            # Canvas resize menu
            with ui.button(f"Canvas · {project.aspect_ratio}", icon="crop_free").props(
                "flat dense no-caps color=grey-6"
            ) as _canvas_btn:
                with ui.menu():
                    ui.label("Recommended formats").classes("text-xs text-grey-5 q-px-md q-pt-sm")
                    for preset_name, preset in CANVAS_PRESETS.items():
                        ui.menu_item(
                            f"{preset['label']} · {preset['aspect_ratio']}",
                            on_click=lambda _=None, ratio=preset["aspect_ratio"], label=preset["label"], description=preset.get("description", ""): _confirm_resize(
                                ratio,
                                label,
                                description,
                            ),
                        )
                    ui.separator()
                    ui.label("Exact ratios").classes("text-xs text-grey-5 q-px-md q-pt-sm")
                    for ratio in ASPECT_RATIOS:
                        ui.menu_item(
                            ratio,
                            on_click=lambda _=None, value=ratio: _confirm_resize(value, value),
                        )
            _canvas_btn.tooltip("Resize and auto-fit the whole project")

    # Build container
    with ui.row().classes("w-full items-center gap-2").style(
        "padding: 8px 12px; background: rgba(0,0,0,0.4); "
        "border-top: 1px solid rgba(255,255,255,0.08); "
        "overflow-x: auto; flex-wrap: nowrap;"
    ) as _nav_container:
        pass

    # Inject CSS for delete button hover effect
    ui.add_head_html("""
    <style>
    .page-nav-delete { opacity: 0 !important; }
    div:hover > .page-nav-delete { opacity: 0.7 !important; }
    </style>
    """)

    _render()
    return {"refresh": _render, "container": _nav_container}
