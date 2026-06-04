"""Designer — share and publish dialog."""

from __future__ import annotations

import json
import os
import platform
import subprocess

from nicegui import run, ui

from row_bot.designer.share import list_share_channels, share_project_to_channel, share_project_to_x
from row_bot.designer.publish import publish_project
from row_bot.designer.qr_utils import generate_qr_png_b64
from row_bot.designer.state import DesignerProject
from row_bot.designer.ui_theme import (
    dialog_card_style,
    style_ghost_button,
    style_primary_button,
)


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


def _build_page_selector(default: str = "all") -> dict:
    toggle = ui.toggle(
        {"all": "All", "current": "Current Page", "range": "Range"},
        value=default,
    ).props("no-caps dense outline color=grey-8")
    range_input = ui.input(placeholder="e.g. 1-3 or 2,4").props("dense outlined")
    range_input.classes("q-mt-xs").style("width: 100%;")
    range_input.visible = default == "range"

    def _on_change(_e=None) -> None:
        range_input.visible = toggle.value == "range"
        range_input.update()

    toggle.on("update:model-value", _on_change)

    def _resolve(project: DesignerProject) -> str | None:
        if toggle.value == "current":
            return str(project.active_page + 1)
        if toggle.value == "range":
            return range_input.value.strip() or "all"
        return None

    return {"toggle": toggle, "range_input": range_input, "resolve": _resolve}


def show_share_dialog(project: DesignerProject) -> None:
    """Open a modal dialog that groups publish, channel, and X sharing flows."""

    channels = list_share_channels()
    active_channels = [channel for channel in channels if channel["configured"] and channel["running"]]
    channel_options = {
        channel["name"]: (
            f"{channel['display_name']}"
            f"{' · photos' if channel['photo_out'] else ''}"
            f"{' · docs' if channel['document_out'] else ''}"
        )
        for channel in active_channels
    }

    with ui.dialog() as dlg, ui.card().style(dialog_card_style(min_width="620px", max_width="740px")):
        ui.label("Share & Publish").classes("text-h6 text-weight-bold")
        ui.label("Publish a link or push the deck out through the right delivery channel.").classes(
            "text-sm text-grey-5"
        )
        if project.publish_url:
            ui.label(f"Latest published link: {project.publish_url}").classes("text-xs text-grey-5")
        ui.separator()

        with ui.tabs().classes("w-full") as tabs:
            ui.tab("link", label="Publish Link")
            ui.tab("channel", label="Channel")
            ui.tab("x", label="X")

        with ui.tab_panels(tabs, value="link").classes("w-full"):
            with ui.tab_panel("link"):
                ui.label(
                    "Publish a self-contained HTML deck and reuse the app's static hosting. A public tunnel URL is used when available."
                ).classes("text-sm text-grey-5")
                link_pages = _build_page_selector(default="all")
                link_status = ui.label("").classes("text-xs text-grey-5 q-mt-sm")
                link_url = ui.label("").classes("text-xs text-grey-4")
                link_url.visible = False
                link_path = ui.label("").classes("text-xs text-grey-5")
                link_path.visible = False
                _link_info: list[dict | None] = [None]

                with ui.row().classes("w-full items-center q-mt-sm").style("gap: 8px;"):
                    async def _publish() -> None:
                        publish_btn.disable()
                        link_status.text = "Publishing…"
                        link_status.update()
                        try:
                            info = await run.io_bound(
                                publish_project,
                                project,
                                link_pages["resolve"](project),
                            )
                            _link_info[0] = info
                            link_status.text = "Published"
                            link_status.update()
                            link_url.text = info["url"]
                            link_url.visible = True
                            link_url.update()
                            link_path.text = info["path"]
                            link_path.visible = True
                            link_path.update()
                            ui.notify("Published shareable deck link.", type="positive")
                        except Exception as exc:
                            link_status.text = f"Error: {exc}"
                            link_status.update()
                            ui.notify(f"Publish failed: {exc}", type="negative")
                        finally:
                            publish_btn.enable()

                    publish_btn = ui.button("Publish Link", icon="publish", on_click=_publish)
                    style_primary_button(publish_btn)

                    def _copy_link() -> None:
                        if _link_info[0]:
                            ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(_link_info[0]['url'])})")
                            ui.notify("Link copied to clipboard.", type="positive")

                    def _open_link() -> None:
                        if _link_info[0]:
                            ui.run_javascript(f"window.open({json.dumps(_link_info[0]['url'])}, '_blank')")

                    def _open_publish_folder() -> None:
                        if _link_info[0]:
                            _open_folder(_link_info[0]["path"])

                    def _show_qr() -> None:
                        if not _link_info[0]:
                            ui.notify("Publish first to generate a QR code.",
                                      type="warning")
                            return
                        url = _link_info[0].get("url", "")
                        data_uri = generate_qr_png_b64(url)
                        with ui.dialog() as qr_dlg, ui.card().style(
                            dialog_card_style(min_width="320px",
                                              max_width="400px")
                        ):
                            ui.label("Scan to open").classes(
                                "text-subtitle1 text-weight-bold"
                            )
                            if data_uri:
                                ui.image(data_uri).style(
                                    "width: 260px; height: 260px;"
                                    " image-rendering: pixelated;"
                                    " margin: 4px auto;"
                                )
                            else:
                                ui.label(
                                    "QR generation unavailable — install qrcode."
                                ).classes("text-xs text-amber-5")
                            ui.label(url).classes(
                                "text-xs text-grey-4"
                            ).style("word-break: break-all;")
                            with ui.row().classes("w-full justify-end q-mt-sm"):
                                ui.button("Close", on_click=qr_dlg.close).props("flat")
                        qr_dlg.open()

                    copy_link_btn = ui.button("Copy Link", on_click=_copy_link)
                    style_ghost_button(copy_link_btn, compact=True)
                    open_link_btn = ui.button("Open Link", on_click=_open_link)
                    style_ghost_button(open_link_btn, compact=True)
                    open_folder_btn = ui.button("Open Folder", on_click=_open_publish_folder)
                    style_ghost_button(open_folder_btn, compact=True)
                    qr_btn = ui.button("Show QR", icon="qr_code_2",
                                        on_click=_show_qr)
                    style_ghost_button(qr_btn, compact=True)

            with ui.tab_panel("channel"):
                if not active_channels:
                    ui.label(
                        "No configured, running outbound channels are available right now. Start a channel in Settings before sharing."
                    ).classes("text-sm text-grey-5")
                else:
                    channel_select = ui.select(channel_options, value=next(iter(channel_options))).props(
                        "dense outlined"
                    ).style("width: 100%;")
                    delivery_toggle = ui.toggle(
                        {
                            "link": "Published Link",
                            "slides": "Slide PNGs",
                            "pdf": "PDF",
                            "pptx": "PPTX",
                            "html": "HTML",
                        },
                        value="link",
                    ).props("no-caps dense outline color=grey-8")
                    pptx_mode_row = ui.row().classes("w-full q-mt-xs")
                    pptx_mode_row.visible = False
                    with pptx_mode_row:
                        ui.label("PPTX Mode").classes("text-xs text-grey-5")
                        channel_pptx_mode = ui.toggle(
                            {"screenshot": "High-Fidelity", "structured": "Editable"},
                            value="screenshot",
                        ).props("no-caps dense outline size=sm")

                    def _on_delivery_change(_e=None) -> None:
                        pptx_mode_row.visible = delivery_toggle.value == "pptx"
                        pptx_mode_row.update()

                    delivery_toggle.on("update:model-value", _on_delivery_change)

                    target_input = ui.input(placeholder="Optional target override").props("dense outlined")
                    target_input.style("width: 100%;")
                    channel_message = ui.textarea(
                        placeholder="Optional caption or message for the share…"
                    ).props("dense outlined autogrow")
                    channel_message.classes("w-full")
                    channel_pages = _build_page_selector(default="all")
                    channel_status = ui.label("").classes("text-xs text-grey-5 q-mt-sm")

                    async def _share_channel() -> None:
                        share_btn.disable()
                        channel_status.text = "Sharing…"
                        channel_status.update()
                        try:
                            result = await run.io_bound(
                                lambda: share_project_to_channel(
                                    project,
                                    channel_select.value,
                                    delivery=delivery_toggle.value,
                                    target=target_input.value.strip(),
                                    text=channel_message.value or "",
                                    pages=channel_pages["resolve"](project),
                                    pptx_mode=channel_pptx_mode.value,
                                )
                            )
                            channel_status.text = result["detail"]
                            channel_status.update()
                            ui.notify(result["detail"], type="positive")
                        except Exception as exc:
                            channel_status.text = f"Error: {exc}"
                            channel_status.update()
                            ui.notify(f"Channel share failed: {exc}", type="negative")
                        finally:
                            share_btn.enable()

                    share_btn = ui.button(
                        "Share to Channel",
                        icon="send",
                        on_click=_share_channel,
                    )
                    style_primary_button(share_btn)

            with ui.tab_panel("x"):
                ui.label("Post up to four slide images to X using the existing X account integration.").classes(
                    "text-sm text-grey-5"
                )
                x_text = ui.textarea(
                    placeholder="Write the post text…"
                ).props("dense outlined autogrow")
                x_text.classes("w-full")
                x_pages = _build_page_selector(default="current")
                ui.label("If you choose more than four slides, only the first four selected images will be attached.").classes(
                    "text-xs text-grey-5 q-mt-xs"
                )
                x_status = ui.label("").classes("text-xs text-grey-5 q-mt-sm")

                async def _share_x() -> None:
                    x_btn.disable()
                    x_status.text = "Posting to X…"
                    x_status.update()
                    try:
                        result = await run.io_bound(
                            lambda: share_project_to_x(
                                project,
                                text=x_text.value or "",
                                pages=x_pages["resolve"](project),
                            )
                        )
                        x_status.text = result["detail"]
                        x_status.update()
                        ui.notify(
                            result["detail"],
                            type="positive" if result["success"] else "warning",
                        )
                    except Exception as exc:
                        x_status.text = f"Error: {exc}"
                        x_status.update()
                        ui.notify(f"X share failed: {exc}", type="negative")
                    finally:
                        x_btn.enable()

                x_btn = ui.button(
                    "Post to X",
                    icon="campaign",
                    on_click=_share_x,
                )
                style_primary_button(x_btn)

        with ui.row().classes("w-full justify-end q-mt-sm"):
            close_btn = ui.button("Close", on_click=dlg.close)
            style_ghost_button(close_btn)

    dlg.open()