"""Mobile Access settings section for the desktop settings dialog."""

from __future__ import annotations

import json
from typing import Any, Callable

from nicegui import ui

from row_bot.app_port import get_app_port
from row_bot.mobile.access_info import build_access_info
from row_bot.mobile.auth import create_pairing_ticket
from row_bot.mobile.store import MobileAuthStore
from row_bot.mobile.tailscale import detect_tailscale


def _copy_button(value: str, tooltip: str = "Copy") -> None:
    safe = json.dumps(value)
    ui.button(
        icon="content_copy",
        on_click=lambda: (
            ui.run_javascript(f"navigator.clipboard.writeText({safe})"),
            ui.notify("Copied", type="info"),
        ),
    ).props("flat dense round size=sm").tooltip(tooltip)


def _candidate_options(candidates: list[dict[str, Any]]) -> dict[str, str]:
    options: dict[str, str] = {}
    for candidate in candidates:
        url = str(candidate.get("url") or "").strip()
        if not url:
            continue
        label = str(candidate.get("label") or url)
        available = bool(candidate.get("available"))
        suffix = "" if available else " (setup needed)"
        options[url] = f"{label}{suffix}"
    return options


def _access_mode_for_url(candidates: list[dict[str, Any]], url: str) -> str:
    for candidate in candidates:
        if candidate.get("url") == url:
            return str(candidate.get("access_mode") or "mobile")
    return "custom"


def _preferred_pairing_origin(candidates: list[dict[str, Any]]) -> str:
    """Pick the best reachable non-local URL for phone pairing."""
    priority = {
        "tailscale-serve": 0,
        "ngrok": 1,
        "tailscale-direct": 2,
        "lan": 3,
        "localhost": 9,
    }
    usable = [
        candidate
        for candidate in candidates
        if str(candidate.get("url") or "").strip()
        and (bool(candidate.get("available")) or candidate.get("access_mode") == "localhost")
    ]
    if not usable:
        return ""
    usable.sort(key=lambda candidate: priority.get(str(candidate.get("access_mode") or ""), 8))
    return str(usable[0].get("url") or "").strip()


def build_mobile_access_settings_section(
    *,
    settings_section: Callable,
    status_dot: Callable[[str, str, str | None], None] | None = None,
    metric_chip: Callable[[str, Any, str | None, str], None] | None = None,
) -> None:
    """Render Settings -> System -> Mobile Access."""
    with settings_section(
        "Mobile Access",
        "Pair phones and manage browser-first mobile companion access.",
        icon="phone_iphone",
        tone="warning",
    ):
        store = MobileAuthStore()
        store.ensure_schema()
        port = get_app_port()
        try:
            from row_bot.tunnel import tunnel_manager

            ngrok_url = tunnel_manager.get_url(port)
        except Exception:
            ngrok_url = None
        try:
            tailscale_state = detect_tailscale(port=port)
        except Exception as exc:
            tailscale_state = None
            ui.label(f"Tailscale status unavailable: {exc}").classes("text-warning text-xs")

        access_info = build_access_info(
            port=port,
            ngrok_url=ngrok_url,
            tailscale_state=tailscale_state,
        )
        candidates = list(access_info.get("candidates") or [])
        devices_container = ui.column().classes("w-full gap-2")
        events_container = ui.column().classes("w-full gap-2")
        pairing_container = ui.column().classes("w-full gap-2")

        with ui.row().classes("items-center gap-2 w-full"):
            if status_dot:
                status_dot("Remote gate active", "ok", "Remote and forwarded requests require mobile session auth.")
            if metric_chip:
                metric_chip("paired devices", len(store.list_devices(include_revoked=False)), "devices", "primary")
                metric_chip("port", port, "settings_ethernet", "blue-grey")
            ui.label(f"Host mode: {access_info.get('bind_host')}").classes("text-grey-6 text-xs")

        ui.label(
            "Localhost desktop remains frictionless. LAN, Tailscale, ngrok, and forwarded routes require pairing."
        ).classes("text-grey-6 text-xs")

        with ui.column().classes("w-full gap-2"):
            ui.label("Access candidates").classes("text-weight-medium text-sm")
            for candidate in candidates:
                color = "positive" if candidate.get("available") else "warning"
                with ui.row().classes("items-start gap-2 w-full no-wrap"):
                    ui.badge(str(candidate.get("label") or "Access"), color=color).props("outline")
                    with ui.column().classes("gap-0").style("min-width: 0; flex: 1;"):
                        ui.label(str(candidate.get("url") or "")).classes("text-primary text-xs").style("word-break: break-all;")
                        ui.label(str(candidate.get("detail") or "")).classes("text-grey-6 text-xs")
                        if candidate.get("warning"):
                            ui.label(str(candidate["warning"])).classes("text-warning text-xs")
                        if candidate.get("requires_bind"):
                            ui.label("Start Row-Bot with a reachable host binding to use this direct URL.").classes("text-warning text-xs")
                    if candidate.get("url"):
                        _copy_button(str(candidate["url"]))

        options = _candidate_options(candidates)
        default_origin = _preferred_pairing_origin(candidates)
        selected_origin = ui.select(
            options=options or {"": "No URL candidates found"},
            value=default_origin,
            label="Pairing origin",
        ).classes("w-full").props("dense outlined")
        custom_origin = ui.input("Custom origin", value=default_origin).classes("w-full").props("dense outlined")

        def _sync_custom_origin(e) -> None:
            if e.value:
                custom_origin.value = e.value
                custom_origin.update()

        selected_origin.on("update:model-value", _sync_custom_origin)

        def _render_pairing(url: str, access_mode: str) -> None:
            pairing_container.clear()
            origin = url.rstrip("/")
            if not origin:
                ui.notify("Choose or enter an origin before pairing", type="warning")
                return
            ticket = create_pairing_ticket(store, intended_origin=origin, access_mode=access_mode)
            pair_url = ticket.pairing_url(origin)
            with pairing_container:
                ui.label("Pairing link").classes("text-weight-medium text-sm")
                with ui.row().classes("items-center gap-2 w-full no-wrap"):
                    ui.label(pair_url).classes("text-primary text-xs").style("word-break: break-all;")
                    _copy_button(pair_url, "Copy pairing URL")
                try:
                    from row_bot.designer.qr_utils import generate_qr_png_b64

                    qr = generate_qr_png_b64(pair_url, box_size=8)
                except Exception:
                    qr = ""
                if qr:
                    ui.image(qr).style("width: 192px; height: 192px; image-rendering: pixelated;")
                else:
                    ui.label("QR generation unavailable. Use the pairing link above.").classes("text-warning text-xs")
                ui.label(f"Expires at {ticket.expires_at}").classes("text-grey-6 text-xs")
                ui.label("The raw device session token is set only after confirmation and is never shown here.").classes("text-grey-6 text-xs")

        def _create_pairing() -> None:
            origin = str(custom_origin.value or selected_origin.value or "").strip()
            _render_pairing(origin, _access_mode_for_url(candidates, origin))

        ui.button("Create pairing QR", icon="qr_code_2", on_click=_create_pairing).props("flat dense no-caps color=primary")

        def _render_devices() -> None:
            devices_container.clear()
            with devices_container:
                ui.separator().classes("q-mt-sm")
                ui.label("Paired devices").classes("text-weight-medium text-sm")
                devices = store.list_devices(include_revoked=True)
                if not devices:
                    ui.label("No paired devices yet.").classes("text-grey-6 text-xs")
                    return
                for device in devices:
                    state = "revoked" if device.revoked_at else "active"
                    with ui.row().classes("items-center gap-2 w-full no-wrap"):
                        ui.badge(state, color="grey" if device.revoked_at else "positive").props("outline")
                        with ui.column().classes("gap-0").style("min-width: 0; flex: 1;"):
                            ui.label(device.display_name).classes("text-sm")
                            ui.label(
                                f"Created {device.created_at} | Last seen {device.last_seen_at or 'never'} | {device.access_mode or 'mobile'}"
                            ).classes("text-grey-6 text-xs")
                        if not device.revoked_at:
                            ui.button(
                                icon="block",
                                on_click=lambda d=device: (
                                    store.revoke_device(d.id),
                                    store.log_event("revoked", device_id=d.id, detail={"source": "settings"}),
                                    _render_devices(),
                                    _render_events(),
                                ),
                            ).props("flat dense round color=negative").tooltip("Revoke device")

        def _render_events() -> None:
            events_container.clear()
            with events_container:
                ui.separator().classes("q-mt-sm")
                ui.label("Recent access events").classes("text-weight-medium text-sm")
                events = store.recent_events(limit=12)
                if not events:
                    ui.label("No mobile access events yet.").classes("text-grey-6 text-xs")
                    return
                for event in events:
                    ui.label(
                        f"{event.created_at} | {event.event_type} | {event.ip or 'unknown'}"
                    ).classes("text-grey-6 text-xs")

        _render_devices()
        _render_events()
