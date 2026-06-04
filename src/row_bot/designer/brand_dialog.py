"""Designer — brand configuration dialog with color pickers, font selectors, presets."""

from __future__ import annotations

import base64
import hashlib
import logging
import mimetypes
from typing import Callable

from row_bot.brand import APP_BRAND_ACCENT
from nicegui import ui

from row_bot.designer.brand import (
    get_all_presets, save_brand_preset, delete_brand_preset,
    extract_brand_from_url,
)
from row_bot.designer.session import prepare_project_mutation
from row_bot.designer.state import BrandConfig, DesignerProject
from row_bot.designer.ui_theme import (
    SECTION_LABEL_CLASSES,
    SECTION_LABEL_STYLE,
    dialog_card_style,
    style_destructive_button,
    style_ghost_button,
    style_primary_button,
    style_secondary_button,
    surface_style,
)

logger = logging.getLogger(__name__)

# All 25 bundled fonts (offline-ready)
_FONT_OPTIONS = [
    # Sans-serif
    "Inter", "Roboto", "Open Sans", "Lato", "Montserrat", "Poppins",
    "Raleway", "Nunito", "Source Sans 3", "Work Sans", "DM Sans",
    "Plus Jakarta Sans", "Space Grotesk",
    # Serif
    "Merriweather", "Playfair Display", "Lora", "PT Serif", "Libre Baskerville",
    # Display
    "Orbitron", "Bebas Neue", "Oswald", "Anton",
    # Monospace
    "Fira Code", "JetBrains Mono", "IBM Plex Mono",
]


def show_brand_dialog(
    project: DesignerProject,
    on_apply: Callable[[], None] | None = None,
) -> None:
    """Open the brand configuration modal."""

    base_brand = project.brand or BrandConfig()
    brand = BrandConfig.from_dict(base_brand.to_dict())

    if brand.logo_asset_id and not brand.logo_b64:
        try:
            from row_bot.designer.storage import load_asset_bytes

            asset = next((item for item in project.assets if item.id == brand.logo_asset_id), None)
            if asset is not None and asset.stored_name:
                logo_data = load_asset_bytes(project.id, asset.stored_name)
                if logo_data:
                    brand.logo_b64 = base64.b64encode(logo_data).decode("ascii")
                    brand.logo_mime_type = asset.mime_type or brand.logo_mime_type
                    brand.logo_filename = brand.logo_filename or asset.filename
        except Exception:
            logger.debug("Failed to load brand logo asset preview", exc_info=True)

    with ui.dialog() as dlg, ui.card().style(
        dialog_card_style(min_width="1000px", max_width="1240px", max_height="95vh")
    ):
        ui.label("Brand & Theme").classes("text-h6 text-weight-bold")
        ui.label("Tune the brand system once, then apply it cleanly across every page.").classes(
            "text-sm text-grey-5"
        )
        ui.separator()

        with ui.scroll_area().classes("w-full").style("height: 70vh;"):

            def _swatch(color: str) -> str:
                c = color or '#000000'
                return (
                    f'<div style="width:28px;height:28px;border-radius:4px;'
                    f'background:{c};border:1px solid rgba(255,255,255,0.15);'
                    f'box-shadow:inset 0 0 0 1px rgba(0,0,0,0.15);"></div>'
                )

            # ── Preset selector ──────────────────────────────────────
            ui.label("Presets").classes(SECTION_LABEL_CLASSES + " q-mt-sm").style(SECTION_LABEL_STYLE)
            presets = get_all_presets()

            def _load_preset(name: str):
                p = presets[name]
                primary.value = p.primary_color
                secondary.value = p.secondary_color
                accent.value = p.accent_color
                bg.value = p.bg_color
                text_c.value = p.text_color
                h_font.value = p.heading_font
                b_font.value = p.body_font
                _sw_p.set_content(_swatch(p.primary_color))
                _sw_s.set_content(_swatch(p.secondary_color))
                _sw_a.set_content(_swatch(p.accent_color))
                _sw_b.set_content(_swatch(p.bg_color))
                _sw_t.set_content(_swatch(p.text_color))
                brand.logo_b64 = p.logo_b64
                brand.logo_asset_id = ""
                brand.logo_mime_type = p.logo_mime_type
                brand.logo_filename = p.logo_filename
                brand.logo_mode = p.logo_mode
                brand.logo_scope = p.logo_scope
                brand.logo_position = p.logo_position
                brand.logo_max_height = p.logo_max_height
                brand.logo_padding = p.logo_padding
                logo_mode.value = p.logo_mode
                logo_scope.value = p.logo_scope
                logo_position.value = p.logo_position
                logo_max_height.value = p.logo_max_height
                logo_padding.value = p.logo_padding
                _refresh_logo_preview()
                _refresh_logo_mode_hint()
                ui.notify(f"Loaded preset: {name}", type="info")

            if presets:
                with ui.row().classes("w-full gap-2 flex-wrap"):
                    for _pname, _pval in presets.items():
                        with ui.card().classes(
                            "cursor-pointer q-pa-xs"
                        ).style(
                            surface_style(padding="8px") + " min-width:110px;"
                        ).on("click", lambda _, n=_pname: _load_preset(n)):
                            ui.html(
                                f'<div style="display:flex;width:100%;height:22px;border-radius:4px;'
                                f'overflow:hidden;border:1px solid rgba(255,255,255,0.1);">'
                                f'<div style="flex:1;background:{_pval.primary_color}"></div>'
                                f'<div style="flex:1;background:{_pval.secondary_color}"></div>'
                                f'<div style="flex:1;background:{_pval.accent_color}"></div>'
                                f'<div style="flex:0.6;background:{_pval.bg_color}"></div>'
                                f'</div>',
                                sanitize=False,
                            ).classes("w-full")
                            ui.label(_pname).classes("text-xs text-center q-mt-xs")
            else:
                ui.label("No presets saved yet.").classes("text-xs text-grey-6 q-mt-xs")

            # ── Colors ───────────────────────────────────────────────
            ui.label("Colors").classes(SECTION_LABEL_CLASSES + " q-mt-md").style(SECTION_LABEL_STYLE)

            with ui.row().classes("w-full gap-4"):
                with ui.column().classes("items-center"):
                    ui.label("Primary").classes("text-xs text-grey-5")
                    with ui.row().classes("items-center gap-1 no-wrap"):
                        _sw_p = ui.html(_swatch(brand.primary_color), sanitize=False)
                        primary = ui.color_input(
                            value=brand.primary_color,
                            on_change=lambda e, s=_sw_p: s.set_content(_swatch(e.value)),
                        ).props("dense")
                with ui.column().classes("items-center"):
                    ui.label("Secondary").classes("text-xs text-grey-5")
                    with ui.row().classes("items-center gap-1 no-wrap"):
                        _sw_s = ui.html(_swatch(brand.secondary_color), sanitize=False)
                        secondary = ui.color_input(
                            value=brand.secondary_color,
                            on_change=lambda e, s=_sw_s: s.set_content(_swatch(e.value)),
                        ).props("dense")
                with ui.column().classes("items-center"):
                    ui.label("Accent").classes("text-xs text-grey-5")
                    with ui.row().classes("items-center gap-1 no-wrap"):
                        _sw_a = ui.html(_swatch(brand.accent_color), sanitize=False)
                        accent = ui.color_input(
                            value=brand.accent_color,
                            on_change=lambda e, s=_sw_a: s.set_content(_swatch(e.value)),
                        ).props("dense")

            with ui.row().classes("w-full gap-4 q-mt-xs"):
                with ui.column().classes("items-center"):
                    ui.label("Background").classes("text-xs text-grey-5")
                    with ui.row().classes("items-center gap-1 no-wrap"):
                        _sw_b = ui.html(_swatch(brand.bg_color), sanitize=False)
                        bg = ui.color_input(
                            value=brand.bg_color,
                            on_change=lambda e, s=_sw_b: s.set_content(_swatch(e.value)),
                        ).props("dense")
                with ui.column().classes("items-center"):
                    ui.label("Text").classes("text-xs text-grey-5")
                    with ui.row().classes("items-center gap-1 no-wrap"):
                        _sw_t = ui.html(_swatch(brand.text_color), sanitize=False)
                        text_c = ui.color_input(
                            value=brand.text_color,
                            on_change=lambda e, s=_sw_t: s.set_content(_swatch(e.value)),
                        ).props("dense")

            # ── Fonts ────────────────────────────────────────────────
            ui.label("Fonts").classes(SECTION_LABEL_CLASSES + " q-mt-md").style(SECTION_LABEL_STYLE)

            with ui.row().classes("w-full gap-4"):
                h_font = ui.select(
                    _FONT_OPTIONS, value=brand.heading_font, label="Heading font",
                    new_value_mode="add",
                ).props("dense outlined").classes("col")
                b_font = ui.select(
                    _FONT_OPTIONS, value=brand.body_font, label="Body font",
                    new_value_mode="add",
                ).props("dense outlined").classes("col")

            # ── Logo upload ──────────────────────────────────────────
            ui.label("Logo").classes(SECTION_LABEL_CLASSES + " q-mt-md").style(SECTION_LABEL_STYLE)

            def _logo_preview_html() -> str:
                if not brand.logo_b64:
                    return ""
                mime = brand.logo_mime_type or "image/png"
                return (
                    f'<img src="data:{mime};base64,{brand.logo_b64}" '
                    f'style="max-height:60px;max-width:200px;border-radius:4px;'
                    f'background:#fff;padding:4px;" />'
                )

            # Logo preview + remove
            _init_logo = _logo_preview_html()
            logo_preview = ui.html(_init_logo, sanitize=False)
            _logo_hint_text = (
                "Upload a logo (PNG/SVG/JPG)" if not brand.logo_b64
                else "Logo set ✓"
            )
            logo_hint = ui.label(_logo_hint_text).classes("text-xs text-grey-5")
            remove_logo_btn = [None]

            def _refresh_logo_preview():
                if brand.logo_b64:
                    logo_preview.set_content(_logo_preview_html())
                    if brand.logo_filename:
                        logo_hint.text = f"Logo set ✓ ({brand.logo_filename})"
                    else:
                        logo_hint.text = "Logo set ✓"
                else:
                    logo_preview.set_content("")
                    logo_hint.text = "Upload a logo (PNG/SVG/JPG)"
                if remove_logo_btn[0] is not None:
                    remove_logo_btn[0].set_visibility(bool(brand.logo_b64))

            async def _handle_logo(e):
                content = await e.file.read()
                b64 = base64.b64encode(content).decode()
                mime_type = mimetypes.guess_type(e.file.name or "")[0] or "image/png"
                brand.logo_b64 = b64
                brand.logo_asset_id = brand.logo_asset_id or (base_brand.logo_asset_id or "brand-logo")
                brand.logo_mime_type = mime_type if mime_type.startswith("image/") else "image/png"
                brand.logo_filename = e.file.name or ""
                logger.info("Logo uploaded: %s, %d bytes", e.file.name, len(content))
                logo_hint.text = f"Logo set ✓ ({len(content) / 1024:.0f} KB)"
                _refresh_logo_preview()

            with ui.row().classes("items-center gap-2"):
                ui.upload(
                    label="Upload logo",
                    auto_upload=True,
                    on_upload=_handle_logo,
                ).props("dense flat accept='.png,.svg,.jpg,.jpeg,.webp'")

                def _remove_logo():
                    brand.logo_b64 = None
                    brand.logo_asset_id = ""
                    brand.logo_mime_type = "image/png"
                    brand.logo_filename = ""
                    _refresh_logo_preview()

                remove_logo_btn[0] = ui.button(
                    "Remove",
                    icon="delete",
                    on_click=_remove_logo,
                )
                style_destructive_button(remove_logo_btn[0], compact=True)
                remove_logo_btn[0].set_visibility(bool(brand.logo_b64))

            # ── Extract from URL ─────────────────────────────────────
            with ui.row().classes("w-full gap-4 q-mt-sm"):
                logo_mode = ui.select(
                    {
                        "auto": "Automatic overlay",
                        "manual": "Manual placeholder",
                    },
                    value=brand.logo_mode or "auto",
                    label="Logo mode",
                ).props("dense outlined").classes("col")
                logo_scope = ui.select(
                    {
                        "all": "All pages",
                        "first": "First page only",
                    },
                    value=brand.logo_scope or "all",
                    label="Auto scope",
                ).props("dense outlined").classes("col")

            with ui.row().classes("w-full gap-4 q-mt-xs"):
                logo_position = ui.select(
                    {
                        "top_left": "Top left",
                        "top_right": "Top right",
                        "bottom_left": "Bottom left",
                        "bottom_right": "Bottom right",
                    },
                    value=brand.logo_position or "top_right",
                    label="Auto position",
                ).props("dense outlined").classes("col")
                logo_max_height = ui.number(
                    label="Logo height (px)",
                    value=int(brand.logo_max_height or 72),
                    min=24,
                    max=240,
                    step=4,
                ).props("dense outlined").classes("col")
                logo_padding = ui.number(
                    label="Logo inset (px)",
                    value=int(brand.logo_padding or 24),
                    min=0,
                    max=160,
                    step=4,
                ).props("dense outlined").classes("col")

            logo_mode_hint = ui.label("").classes("text-xs text-grey-5 q-mt-xs")

            def _refresh_logo_mode_hint():
                if (logo_mode.value or "auto") == "manual":
                    logo_mode_hint.text = (
                        "Manual mode: pages need the <!-- BRAND_LOGO --> placeholder where the logo should appear."
                    )
                else:
                    scope_label = "all pages" if (logo_scope.value or "all") == "all" else "the first page only"
                    position_label = (logo_position.value or "top_right").replace("_", " ")
                    logo_mode_hint.text = (
                        f"Automatic mode overlays the logo on {scope_label} at the {position_label} corner."
                    )
                logo_mode_hint.update()

            logo_mode.on("update:model-value", lambda _e: _refresh_logo_mode_hint())
            logo_scope.on("update:model-value", lambda _e: _refresh_logo_mode_hint())
            logo_position.on("update:model-value", lambda _e: _refresh_logo_mode_hint())
            _refresh_logo_mode_hint()

            ui.label("Auto-Extract").classes(SECTION_LABEL_CLASSES + " q-mt-md").style(SECTION_LABEL_STYLE)
            url_input = ui.input(
                placeholder="https://example.com",
                label="Website URL",
            ).props("dense outlined").classes("w-full")

            async def _extract():
                url = url_input.value.strip()
                if not url:
                    ui.notify("Enter a URL first.", type="warning")
                    return
                if not url.startswith(("http://", "https://")):
                    url = "https://" + url
                ui.notify("Extracting brand…", type="info")
                import asyncio
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, lambda: extract_brand_from_url(url))
                if result is None:
                    ui.notify("Could not extract brand from that URL.", type="negative")
                    return
                primary.value = result.primary_color
                secondary.value = result.secondary_color
                accent.value = result.accent_color
                if result.heading_font != "Inter":
                    h_font.value = result.heading_font
                if result.body_font != "Inter":
                    b_font.value = result.body_font
                ui.notify("Brand colors/fonts extracted!", type="positive")

            extract_btn = ui.button(
                "Extract from URL", icon="language", on_click=_extract,
            ).classes("q-mt-xs")
            style_primary_button(extract_btn, compact=True)

            # ── Save as preset ───────────────────────────────────────
            ui.separator().classes("q-mt-md")
            with ui.row().classes("w-full items-center"):
                save_name = ui.input(
                    placeholder="My Brand",
                    label="Save as preset",
                ).props("dense outlined").classes("col")

                def _save():
                    name = save_name.value.strip()
                    if not name:
                        ui.notify("Enter a name to save.", type="warning")
                        return
                    b = BrandConfig(
                        primary_color=primary.value,
                        secondary_color=secondary.value,
                        accent_color=accent.value,
                        bg_color=bg.value,
                        text_color=text_c.value,
                        heading_font=h_font.value,
                        body_font=b_font.value,
                        logo_b64=brand.logo_b64,
                        logo_asset_id="",
                        logo_mime_type=brand.logo_mime_type,
                        logo_filename=brand.logo_filename,
                        logo_mode=logo_mode.value or "auto",
                        logo_scope=logo_scope.value or "all",
                        logo_position=logo_position.value or "top_right",
                        logo_max_height=int(logo_max_height.value or 72),
                        logo_padding=int(logo_padding.value or 24),
                    )
                    save_brand_preset(name, b)
                    ui.notify(f"Saved preset: {name}", type="positive")

                save_btn = ui.button("Save", icon="save", on_click=_save)
                style_secondary_button(save_btn, compact=True)

        # ── Action buttons ────────────────────────────────────────────
        ui.separator()
        with ui.row().classes("w-full justify-end q-mt-sm"):
            cancel_btn = ui.button("Cancel", on_click=dlg.close)
            style_ghost_button(cancel_btn)

            def _apply():
                from row_bot.designer.state import DesignerAsset
                from row_bot.designer.storage import delete_asset_bytes, save_asset_bytes

                def _persist_logo_asset() -> str:
                    if not brand.logo_b64:
                        return brand.logo_asset_id
                    logo_data = base64.b64decode(brand.logo_b64)
                    mime_type = brand.logo_mime_type or "image/png"
                    asset_id = (brand.logo_asset_id or base_brand.logo_asset_id or "brand-logo").strip() or "brand-logo"
                    filename = brand.logo_filename or f"brand-logo{mimetypes.guess_extension(mime_type) or '.png'}"
                    asset = next((item for item in project.assets if item.id == asset_id), None)
                    if asset is None:
                        asset = DesignerAsset(id=asset_id)
                        project.assets.append(asset)
                    asset.kind = "brand-logo"
                    asset.label = brand.logo_filename or "Brand logo"
                    asset.mime_type = mime_type
                    asset.filename = filename
                    asset.size_bytes = len(logo_data)
                    asset.sha256 = hashlib.sha256(logo_data).hexdigest()
                    asset.stored_name = save_asset_bytes(project.id, asset.id, filename, logo_data)
                    brand.logo_asset_id = asset.id
                    return asset.id

                def _remove_persisted_logo(asset_id: str) -> None:
                    if not asset_id:
                        return
                    asset = next((item for item in project.assets if item.id == asset_id), None)
                    if asset is not None and asset.stored_name:
                        delete_asset_bytes(project.id, asset.stored_name)
                    project.assets[:] = [item for item in project.assets if item.id != asset_id]

                brand.primary_color = primary.value or "#2563EB"
                brand.secondary_color = secondary.value or "#1E40AF"
                brand.accent_color = accent.value or APP_BRAND_ACCENT
                brand.bg_color = bg.value or "#0F172A"
                brand.text_color = text_c.value or "#F8FAFC"
                brand.heading_font = h_font.value or "Inter"
                brand.body_font = b_font.value or "Inter"
                brand.logo_mode = logo_mode.value or "auto"
                brand.logo_scope = logo_scope.value or "all"
                brand.logo_position = logo_position.value or "top_right"
                brand.logo_max_height = int(logo_max_height.value or 72)
                brand.logo_padding = int(logo_padding.value or 24)
                prepare_project_mutation(project, "apply_brand_ui")
                previous_logo_asset_id = (project.brand.logo_asset_id if project.brand else "")
                if brand.logo_b64:
                    _persist_logo_asset()
                elif previous_logo_asset_id and not brand.logo_asset_id:
                    _remove_persisted_logo(previous_logo_asset_id)
                project.brand = BrandConfig.from_dict(brand.to_dict())
                project.brand.logo_b64 = None if project.brand.logo_asset_id else project.brand.logo_b64
                project.updated_at = __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat()
                # Propagate brand CSS to all existing pages
                from row_bot.designer.preview import update_brand_in_html
                for page in project.pages:
                    if page.html.strip():
                        page.html = update_brand_in_html(page.html, project.brand)
                        page.thumbnail_b64 = None
                # Persist & notify
                from row_bot.designer.storage import save_project
                save_project(project)
                project.manual_edits.append(
                    f"User changed brand via dialog. "
                    f"Colors: primary={project.brand.primary_color}, secondary={project.brand.secondary_color}, "
                    f"accent={project.brand.accent_color}, bg={project.brand.bg_color}, text={project.brand.text_color}. "
                    f"Fonts: heading={project.brand.heading_font}, body={project.brand.body_font}. "
                    f"Logo mode={project.brand.logo_mode}, scope={project.brand.logo_scope}, "
                    f"position={project.brand.logo_position}, max_height={project.brand.logo_max_height}px. "
                    f"Brand CSS has been auto-applied to all pages."
                )
                ui.notify("Brand applied!", type="positive")
                if on_apply:
                    on_apply()
                dlg.close()

            apply_btn = ui.button("Apply", icon="palette", on_click=_apply)
            style_primary_button(apply_btn)

    dlg.open()
