"""Mobile Access settings section for the desktop settings dialog."""

from __future__ import annotations

from datetime import datetime, timezone
from ipaddress import ip_address, ip_network
import json
from typing import Any, Callable

from nicegui import ui

from row_bot.app_port import get_app_port
from row_bot.mobile.access_info import build_access_info
from row_bot.mobile.auth import create_pairing_ticket
from row_bot.mobile.store import MobileAccessEvent, MobileAuthStore, MobileDevice
from row_bot.mobile.tailscale import detect_tailscale

_TAILSCALE_CGNAT = ip_network("100.64.0.0/10")


def _copy_button(value: str, tooltip: str = "Copy") -> None:
    safe = json.dumps(value)
    ui.button(
        icon="content_copy",
        on_click=lambda: (
            ui.run_javascript(f"navigator.clipboard.writeText({safe})"),
            ui.notify("Copied", type="info"),
        ),
    ).props("flat dense round size=sm").tooltip(tooltip)


def _copy_value_row(label: str, value: str, *, tooltip: str = "Copy") -> None:
    with ui.row().classes("items-center gap-2 w-full no-wrap"):
        with ui.column().classes("gap-0").style("min-width: 0; flex: 1;"):
            ui.label(label).classes("text-grey-6 text-xs")
            ui.label(value or "Unavailable").classes("text-primary text-xs").style("word-break: break-all;")
        if value:
            _copy_button(value, tooltip)


def _candidate_url(candidate: dict[str, Any] | None) -> str:
    return str((candidate or {}).get("url") or "").strip()


def _route_kind(candidate: dict[str, Any] | None) -> str:
    mode = str((candidate or {}).get("access_mode") or "").strip().lower()
    if mode in {"tailscale-serve", "tailscale-direct"}:
        return "private"
    if mode == "ngrok":
        return "public"
    if mode == "lan":
        return "lan"
    if mode == "localhost":
        return "desktop"
    return "custom"


def _route_title(candidate: dict[str, Any] | None) -> str:
    kind = _route_kind(candidate)
    return {
        "private": "Private access",
        "public": "Public tunnel",
        "lan": "LAN",
        "desktop": "Desktop only",
        "custom": "Advanced custom URL",
    }.get(kind, "Advanced custom URL")


def _route_summary(candidate: dict[str, Any] | None) -> str:
    if not candidate:
        return "No pairing route is ready yet."
    url = _candidate_url(candidate)
    mode = str(candidate.get("access_mode") or "")
    kind = _route_kind(candidate)
    if kind == "private":
        if mode == "tailscale-serve":
            return "Using private access: Tailscale Serve"
        if candidate.get("available"):
            return "Using private access: Tailscale direct"
        return "Private access needs setup"
    if kind == "public":
        return f"Using public tunnel: {url}" if url else "Public tunnel is unavailable"
    if kind == "lan":
        if not candidate.get("available") and candidate.get("requires_bind"):
            return "LAN access is off"
        return f"Using LAN: {url}" if url else "LAN address unavailable"
    if kind == "desktop":
        return "No phone-ready connection method is active yet."
    return f"Using advanced custom URL: {url}" if url else "Advanced custom URL"


def _route_warning(candidate: dict[str, Any] | None) -> str:
    if not candidate:
        return "Start a private access route, public tunnel, or LAN setup before pairing a phone."
    kind = _route_kind(candidate)
    if kind == "public":
        return "Anyone with the URL can reach the pairing gate. Pairing is still required. Keep public tunnel links private."
    if kind == "lan":
        if not candidate.get("available") and candidate.get("requires_bind"):
            return "Your phone cannot use LAN addresses until Row-Bot is allowed to listen on your local network."
        return str(candidate.get("warning") or "Use LAN only on trusted local networks.")
    if kind == "private" and candidate.get("requires_bind"):
        return "Direct private access needs Row-Bot to listen beyond localhost. Tailscale Serve can avoid that."
    if kind == "desktop":
        return "Desktop localhost works only on this computer. Use private access, public tunnel, or LAN for a phone."
    return str(candidate.get("warning") or "")


def _access_mode_for_url(candidates: list[dict[str, Any]], url: str) -> str:
    for candidate in candidates:
        if candidate.get("url") == url:
            return str(candidate.get("access_mode") or "mobile")
    return "custom"


def _preferred_pairing_origin(candidates: list[dict[str, Any]]) -> str:
    """Pick the best reachable URL using the existing mobile pairing priority."""
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
        if _candidate_url(candidate)
        and (bool(candidate.get("available")) or candidate.get("access_mode") == "localhost")
    ]
    if not usable:
        return ""
    usable.sort(key=lambda candidate: priority.get(str(candidate.get("access_mode") or ""), 8))
    return _candidate_url(usable[0])


def _recommended_pairing_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    origin = _preferred_pairing_origin(candidates)
    if not origin:
        return None
    return next((candidate for candidate in candidates if _candidate_url(candidate) == origin), None)


def _lan_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [candidate for candidate in candidates if str(candidate.get("access_mode") or "") == "lan" and _candidate_url(candidate)]


def _candidate_by_mode(candidates: list[dict[str, Any]], *modes: str) -> dict[str, Any] | None:
    wanted = set(modes)
    return next((candidate for candidate in candidates if str(candidate.get("access_mode") or "") in wanted), None)


def _custom_candidate(origin: str) -> dict[str, Any]:
    url = origin.strip().rstrip("/")
    return {
        "id": "custom",
        "label": "Advanced custom URL",
        "url": url,
        "access_mode": "custom",
        "available": bool(url),
        "security": "custom",
        "detail": "Manual URL for reverse proxies, ngrok edge cases, or expert testing.",
        "warning": "Use only origins you control. Pairing is still required.",
        "requires_bind": False,
    }


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _format_relative_time(value: str | None) -> str:
    parsed = _parse_iso_datetime(value)
    if parsed is None:
        return "never"
    now = datetime.now(timezone.utc)
    seconds = max(0, int((now - parsed.astimezone(timezone.utc)).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hr ago"
    days = hours // 24
    if days < 7:
        return f"{days} days ago"
    return parsed.astimezone().strftime("%b %d, %H:%M")


def _format_time_until(value: str | None) -> str:
    parsed = _parse_iso_datetime(value)
    if parsed is None:
        return "expiry unknown"
    seconds = int((parsed.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())
    if seconds <= 0:
        return "expired"
    minutes = seconds // 60
    if minutes < 1:
        return "expires in less than a minute"
    if minutes < 60:
        return f"expires in {minutes} min"
    hours = minutes // 60
    return f"expires in {hours} hr"


def _format_short_time(value: str | None) -> str:
    parsed = _parse_iso_datetime(value)
    if parsed is None:
        return "unknown time"
    return parsed.astimezone().strftime("%b %d, %H:%M")


def _device_summary(devices: list[MobileDevice]) -> tuple[str, str]:
    count = len(devices)
    label = "No paired phones" if count == 0 else f"{count} paired phone" if count == 1 else f"{count} paired phones"
    last_seen_values = [device.last_seen_at for device in devices if device.last_seen_at]
    if last_seen_values:
        return label, f"Last seen {_format_relative_time(max(last_seen_values))}"
    if devices:
        created_values = [device.created_at for device in devices if device.created_at]
        created = max(created_values) if created_values else None
        return label, f"Newest pairing {_format_relative_time(created)}"
    return label, "Pair a phone to manage Row-Bot from a mobile browser."


def _event_label(event_type: str) -> str:
    labels = {
        "paired": "Phone paired",
        "pairing_failed": "Pairing failed",
        "revoked": "Device revoked",
        "session_validated": "Session validated",
    }
    return labels.get(event_type, event_type.replace("_", " ").strip().title() or "Access event")


def _event_source_label(event: MobileAccessEvent) -> str:
    detail = event.detail if isinstance(event.detail, dict) else {}
    mode = str(detail.get("access_mode") or "").strip()
    if mode == "ngrok":
        return "Public tunnel visitor"
    if mode in {"tailscale-serve", "tailscale-direct"}:
        return "Private access visitor"
    if mode == "lan":
        return "LAN visitor"
    if mode == "localhost":
        return "Desktop localhost"
    ip = str(event.ip or "").strip()
    if not ip:
        return "Unknown source"
    try:
        parsed = ip_address(ip)
    except ValueError:
        return "Remote visitor"
    if parsed.is_loopback:
        return "Desktop localhost"
    if parsed.version == 4 and parsed in _TAILSCALE_CGNAT:
        return "Private access visitor"
    if parsed.is_private:
        return "LAN visitor"
    return "Public tunnel visitor"


def _is_phone_ready_candidate(candidate: dict[str, Any] | None) -> bool:
    if not candidate or not _candidate_url(candidate):
        return False
    if _route_kind(candidate) == "desktop":
        return False
    return bool(candidate.get("available")) or _route_kind(candidate) == "custom"


def _container_style(*, muted: bool = False) -> str:
    border = "rgba(148, 163, 184, 0.24)"
    background = "rgba(15, 23, 42, 0.18)" if not muted else "rgba(148, 163, 184, 0.06)"
    return f"border: 1px solid {border}; background: {background}; border-radius: 8px;"


def build_mobile_access_settings_section(
    *,
    settings_section: Callable,
    status_dot: Callable[[str, str, str | None], None] | None = None,
    metric_chip: Callable[[str, Any, str | None, str], None] | None = None,
) -> None:
    """Render Settings -> System -> Mobile Access."""
    with settings_section(
        "Mobile Access",
        "Pair phones and manage browser access to this Row-Bot desktop.",
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
        default_candidate = _recommended_pairing_candidate(candidates)
        lan_candidates = _lan_candidates(candidates)

        summary_container: Any | None = None
        devices_container: Any | None = None
        events_container: Any | None = None
        pair_dialog_content: Any | None = None
        selected_pair_candidate: dict[str, Any] | None = default_candidate
        expansion_refs: dict[str, Any | None] = {"devices": None, "advanced": None}

        with ui.dialog() as lan_setup_dialog, ui.card().classes("w-[680px] max-w-full q-pa-md"):
            ui.label("LAN setup").classes("text-h6")
            ui.label(
                "LAN access lets devices on your local network reach this Row-Bot desktop. "
                "Row-Bot does not enable it silently because it changes the desktop from localhost-only to network-reachable."
            ).classes("text-grey-7 text-sm")
            ui.label("Pairing is still required. Use this only on trusted LANs, and restart Row-Bot after changing the host.").classes(
                "text-warning text-sm"
            )
            _copy_value_row("Launcher command", "row-bot --host 0.0.0.0", tooltip="Copy launcher command")
            _copy_value_row(
                "Development command",
                "$env:ROW_BOT_HOST='0.0.0.0'; uv run python app.py",
                tooltip="Copy development command",
            )
            with ui.row().classes("justify-end w-full"):
                ui.button("Close", on_click=lan_setup_dialog.close).props("flat no-caps")

        with ui.dialog() as pair_dialog:
            with ui.card().classes("w-[720px] max-w-full q-pa-md"):
                pair_dialog_content = ui.column().classes("w-full gap-3")

        def _open_lan_setup_dialog() -> None:
            lan_setup_dialog.open()

        def _render_pairing_ticket(candidate: dict[str, Any] | None) -> None:
            ui.label(_route_summary(candidate)).classes("text-subtitle2 text-weight-medium")
            warning = _route_warning(candidate)
            if warning:
                ui.label(warning).classes("text-warning text-sm")

            if not _is_phone_ready_candidate(candidate):
                ui.label(
                    "Start private access, a public tunnel, or explicit LAN setup before creating a phone QR."
                ).classes("text-grey-6 text-sm")
                with ui.row().classes("items-center gap-2"):
                    ui.button("Show LAN setup", icon="lan", on_click=_open_lan_setup_dialog).props("flat dense no-caps")
                    ui.button(
                        "Connection details",
                        icon="tune",
                        on_click=lambda: (pair_dialog.close(), _expand_advanced()),
                    ).props("flat dense no-caps")
                return

            origin = _candidate_url(candidate).rstrip("/")
            ticket = create_pairing_ticket(
                store,
                intended_origin=origin,
                access_mode=str(candidate.get("access_mode") or _route_kind(candidate)),
            )
            pair_url = ticket.pairing_url(origin)
            ui.label("Scan this QR code with your phone.").classes("text-grey-6 text-sm")
            try:
                from row_bot.designer.qr_utils import generate_qr_png_b64

                qr = generate_qr_png_b64(pair_url, box_size=8)
            except Exception:
                qr = ""
            if qr:
                ui.image(qr).style("width: 240px; height: 240px; image-rendering: pixelated;")
            else:
                ui.label("QR generation unavailable. Use the pairing link instead.").classes("text-warning text-sm")
            with ui.row().classes("items-center gap-2"):
                ui.button("Copy pairing link", icon="content_copy", on_click=lambda: _copy_pairing_url(pair_url)).props(
                    "flat dense no-caps color=primary"
                )
                ui.button("Refresh QR", icon="refresh", on_click=lambda: _render_pair_phone_dialog(candidate)).props(
                    "flat dense no-caps"
                )
            ui.label(f"{_format_time_until(ticket.expires_at)}. Absolute expiry: {_format_short_time(ticket.expires_at)}").classes(
                "text-grey-6 text-xs"
            )
            if _route_kind(candidate) == "public":
                ui.label("If ngrok shows a warning page, tap Visit Site once.").classes("text-warning text-xs")
                ui.label("Keep public tunnel links private.").classes("text-warning text-xs")
            ui.label("Session tokens are set only after confirmation and are never shown here.").classes("text-grey-6 text-xs")
            with ui.expansion("Pairing link", icon="link", value=False).classes("w-full"):
                _copy_value_row("Single-use link", pair_url, tooltip="Copy pairing URL")

        def _copy_pairing_url(pair_url: str) -> None:
            safe = json.dumps(pair_url)
            ui.run_javascript(f"navigator.clipboard.writeText({safe})")
            ui.notify("Pairing link copied", type="info")

        def _select_pairing_candidate(candidate: dict[str, Any]) -> None:
            nonlocal selected_pair_candidate
            selected_pair_candidate = candidate
            _render_pair_phone_dialog(candidate)

        def _render_route_option(candidate: dict[str, Any], *, recommended: bool = False) -> None:
            available = _is_phone_ready_candidate(candidate)
            with ui.column().classes("w-full gap-1 q-pa-sm").style(_container_style(muted=not available)):
                with ui.row().classes("items-start gap-2 w-full no-wrap"):
                    ui.icon("check_circle" if available else "info", size="1rem").classes(
                        "text-positive" if available else "text-warning"
                    )
                    with ui.column().classes("gap-0").style("min-width: 0; flex: 1;"):
                        title = _route_title(candidate)
                        if recommended:
                            title = f"{title} - recommended"
                        ui.label(title).classes("text-sm text-weight-medium")
                        ui.label(str(candidate.get("label") or "")).classes("text-grey-6 text-xs")
                        if _candidate_url(candidate):
                            ui.label(_candidate_url(candidate)).classes("text-primary text-xs").style("word-break: break-all;")
                        detail = str(candidate.get("detail") or "")
                        if detail:
                            ui.label(detail).classes("text-grey-6 text-xs")
                        warning = _route_warning(candidate)
                        if warning:
                            ui.label(warning).classes("text-warning text-xs")
                with ui.row().classes("items-center gap-2"):
                    if available:
                        ui.button(
                            "Use this route",
                            icon="qr_code_2",
                            on_click=lambda c=candidate: _select_pairing_candidate(c),
                        ).props("flat dense no-caps color=primary")
                    elif _route_kind(candidate) == "lan" or candidate.get("requires_bind"):
                        ui.button("Show LAN setup", icon="lan", on_click=_open_lan_setup_dialog).props("flat dense no-caps")
                    if _candidate_url(candidate):
                        _copy_button(_candidate_url(candidate), "Copy route URL")

        def _render_connection_picker() -> None:
            with ui.expansion("Change connection method", icon="tune", value=False).classes("w-full"):
                ui.label("Choose a route only after you have explicitly started or configured it.").classes("text-grey-6 text-xs")

                private = _candidate_by_mode(candidates, "tailscale-serve") or _candidate_by_mode(candidates, "tailscale-direct")
                with ui.column().classes("w-full gap-1 q-mt-sm"):
                    ui.label("Private access").classes("text-weight-medium text-sm")
                    if private:
                        _render_route_option(private, recommended=private.get("access_mode") == "tailscale-serve")
                    else:
                        ui.label("No Tailscale private access route is active.").classes("text-grey-6 text-xs")

                public = _candidate_by_mode(candidates, "ngrok")
                with ui.column().classes("w-full gap-1 q-mt-sm"):
                    ui.label("Public tunnel").classes("text-weight-medium text-sm")
                    if public:
                        _render_route_option(public, recommended=default_candidate is public)
                    else:
                        ui.label("No public tunnel is active. Start one from Tunnel settings when you choose to use it.").classes(
                            "text-grey-6 text-xs"
                        )

                with ui.column().classes("w-full gap-1 q-mt-sm"):
                    ui.label("LAN").classes("text-weight-medium text-sm")
                    if not lan_candidates:
                        ui.label("No LAN addresses were discovered for this desktop.").classes("text-grey-6 text-xs")
                    elif not bool(access_info.get("remote_bind_enabled")):
                        candidate = lan_candidates[0]
                        ui.label("LAN access is off").classes("text-warning text-sm")
                        ui.label(
                            "Your phone cannot use LAN addresses until Row-Bot is allowed to listen on your local network."
                        ).classes("text-grey-6 text-xs")
                        ui.label(
                            "Multiple LAN addresses can come from network adapters, VPNs, virtual adapters, or tethering."
                        ).classes("text-grey-6 text-xs")
                        ui.button("Show LAN setup", icon="lan", on_click=_open_lan_setup_dialog).props("flat dense no-caps")
                        with ui.expansion("Suggested LAN address", icon="lan", value=False).classes("w-full"):
                            _render_route_option(candidate)
                    else:
                        if len(lan_candidates) > 1:
                            ui.label("Multiple LAN addresses found. Use the address from the same local network as your phone.").classes(
                                "text-grey-6 text-xs"
                            )
                        _render_route_option(lan_candidates[0], recommended=default_candidate is lan_candidates[0])
                        if len(lan_candidates) > 1:
                            with ui.expansion("Other LAN addresses", icon="lan", value=False).classes("w-full"):
                                for candidate in lan_candidates[1:]:
                                    _render_route_option(candidate)

                with ui.column().classes("w-full gap-1 q-mt-sm"):
                    ui.label("Advanced custom URL").classes("text-weight-medium text-sm")
                    custom_input = ui.input("URL", placeholder="https://example.ngrok-free.app").classes("w-full").props(
                        "dense outlined"
                    )

                    def _use_custom() -> None:
                        candidate = _custom_candidate(str(custom_input.value or ""))
                        if not _candidate_url(candidate):
                            ui.notify("Enter a URL before pairing", type="warning")
                            return
                        _select_pairing_candidate(candidate)

                    ui.button("Use custom URL", icon="link", on_click=_use_custom).props("flat dense no-caps color=primary")

        def _render_pair_phone_dialog(candidate: dict[str, Any] | None = None) -> None:
            if pair_dialog_content is None:
                return
            pair_dialog_content.clear()
            candidate = candidate or selected_pair_candidate or default_candidate
            with pair_dialog_content:
                with ui.row().classes("items-start justify-between gap-3 w-full no-wrap"):
                    with ui.column().classes("gap-0").style("min-width: 0; flex: 1;"):
                        ui.label("Pair a phone").classes("text-h6")
                        ui.label("Create a short-lived QR for a phone browser.").classes("text-grey-6 text-sm")
                    ui.button(icon="close", on_click=pair_dialog.close).props("flat dense round").tooltip("Close")
                _render_pairing_ticket(candidate)
                _render_connection_picker()

        def _open_pair_phone_dialog(candidate: dict[str, Any] | None = None) -> None:
            nonlocal selected_pair_candidate
            selected_pair_candidate = candidate or default_candidate
            _render_pair_phone_dialog(selected_pair_candidate)
            pair_dialog.open()

        def _render_device_summary() -> None:
            if summary_container is None:
                return
            summary_container.clear()
            active_devices = store.list_devices(include_revoked=False)
            label, detail = _device_summary(active_devices)
            with summary_container:
                with ui.row().classes("items-center gap-2 w-full no-wrap"):
                    ui.icon("phonelink_lock", size="1.1rem").classes("text-primary")
                    ui.label(label).classes("text-subtitle2 text-weight-medium")
                ui.label(detail).classes("text-grey-6 text-xs")

        def _render_devices() -> None:
            if devices_container is None:
                return
            devices_container.clear()
            with devices_container:
                devices = store.list_devices(include_revoked=True)
                if not devices:
                    ui.label("No paired phones yet.").classes("text-grey-6 text-xs")
                    return
                for device in devices:
                    state = "revoked" if device.revoked_at else "active"
                    with ui.row().classes("items-start gap-2 w-full no-wrap"):
                        ui.badge(state, color="grey" if device.revoked_at else "positive").props("outline")
                        with ui.column().classes("gap-0").style("min-width: 0; flex: 1;"):
                            ui.label(device.display_name).classes("text-sm")
                            seen = _format_relative_time(device.last_seen_at) if device.last_seen_at else "never"
                            ui.label(f"Last seen {seen}").classes("text-grey-6 text-xs")
                            ui.label(f"Paired {_format_relative_time(device.created_at)} via {device.access_mode or 'mobile'}").classes(
                                "text-grey-6 text-xs"
                            )
                        if not device.revoked_at:
                            ui.button(
                                icon="block",
                                on_click=lambda d=device: (
                                    store.revoke_device(d.id),
                                    store.log_event("revoked", device_id=d.id, detail={"source": "settings"}),
                                    _render_device_summary(),
                                    _render_devices(),
                                    _render_events(),
                                ),
                            ).props("flat dense round color=negative").tooltip("Revoke device")

        def _render_events() -> None:
            if events_container is None:
                return
            events_container.clear()
            with events_container:
                events = store.recent_events(limit=12)
                if not events:
                    ui.label("No mobile access events yet.").classes("text-grey-6 text-xs")
                    return
                for event in events:
                    with ui.expansion(_event_label(event.event_type), icon="history", value=False).classes("w-full"):
                        ui.label(_format_short_time(event.created_at)).classes("text-grey-6 text-xs")
                        ui.label(_event_source_label(event)).classes("text-grey-6 text-xs")
                        ui.separator().classes("q-my-xs")
                        ui.label(f"Raw timestamp: {event.created_at}").classes("text-grey-6 text-xs")
                        ui.label(f"Event type: {event.event_type}").classes("text-grey-6 text-xs")
                        ui.label(f"IP: {event.ip or 'unknown'}").classes("text-grey-6 text-xs")
                        if event.device_id:
                            ui.label(f"Device ID: {event.device_id}").classes("text-grey-6 text-xs")
                        if event.user_agent:
                            ui.label(f"User agent: {event.user_agent}").classes("text-grey-6 text-xs").style(
                                "word-break: break-word;"
                            )
                        if event.detail:
                            ui.label(f"Details: {json.dumps(event.detail, sort_keys=True)}").classes("text-grey-6 text-xs").style(
                                "word-break: break-word;"
                            )

        def _expand_devices() -> None:
            expansion = expansion_refs["devices"]
            if expansion is not None:
                expansion.value = True
                expansion.update()

        def _expand_advanced() -> None:
            expansion = expansion_refs["advanced"]
            if expansion is not None:
                expansion.value = True
                expansion.update()

        active_devices = store.list_devices(include_revoked=False)
        with ui.row().classes("items-center gap-2 w-full"):
            if status_dot:
                status_dot("Protected", "ok", "Remote access requires a paired device. Desktop localhost stays open.")
            if metric_chip:
                metric_chip("paired phones", len(active_devices), "phone_iphone", "primary")

        ui.label("Remote access requires a paired device. Desktop localhost stays open.").classes("text-grey-6 text-xs")

        with ui.row().classes("items-center gap-2 w-full"):
            ui.button("Pair a phone", icon="qr_code_2", on_click=lambda: _open_pair_phone_dialog()).props(
                "unelevated no-caps color=primary"
            )
            ui.button("Manage devices", icon="devices", on_click=_expand_devices).props("flat dense no-caps")
            ui.button("Connection details", icon="tune", on_click=_expand_advanced).props("flat dense no-caps")

        summary_container = ui.column().classes("w-full gap-1")
        _render_device_summary()

        with ui.expansion("Manage devices", icon="devices", value=False).classes("w-full") as devices_panel:
            expansion_refs["devices"] = devices_panel
            devices_container = ui.column().classes("w-full gap-2")

        with ui.expansion("Recent activity", icon="history", value=False).classes("w-full"):
            events_container = ui.column().classes("w-full gap-2")

        with ui.expansion("Advanced connection details", icon="tune", value=False).classes("w-full") as advanced_panel:
            expansion_refs["advanced"] = advanced_panel
            with ui.column().classes("w-full gap-2"):
                ui.label("Diagnostics and expert connection options. Nothing here changes host binding automatically.").classes(
                    "text-grey-6 text-xs"
                )
                with ui.row().classes("items-center gap-2 w-full"):
                    if status_dot:
                        status_dot("Remote gate active", "ok", "Remote and forwarded requests require mobile session auth.")
                    if metric_chip:
                        metric_chip("port", port, "settings_ethernet", "blue-grey")
                ui.label(f"Host mode: {access_info.get('bind_host')}").classes("text-grey-6 text-xs")
                ui.label(f"Remote bind enabled: {bool(access_info.get('remote_bind_enabled'))}").classes("text-grey-6 text-xs")

                localhost = _candidate_by_mode(candidates, "localhost")
                if localhost:
                    _copy_value_row("Localhost desktop URL", _candidate_url(localhost), tooltip="Copy localhost URL")

                ui.label("All access candidates").classes("text-weight-medium text-sm q-mt-sm")
                for candidate in candidates:
                    color = "positive" if candidate.get("available") else "warning"
                    with ui.column().classes("w-full gap-1 q-pa-sm").style(_container_style(muted=not candidate.get("available"))):
                        with ui.row().classes("items-start gap-2 w-full no-wrap"):
                            ui.badge(str(candidate.get("label") or "Access"), color=color).props("outline")
                            with ui.column().classes("gap-0").style("min-width: 0; flex: 1;"):
                                ui.label(_candidate_url(candidate)).classes("text-primary text-xs").style("word-break: break-all;")
                                ui.label(str(candidate.get("detail") or "")).classes("text-grey-6 text-xs")
                                ui.label(f"Security: {candidate.get('security') or 'unknown'}").classes("text-grey-6 text-xs")
                                if candidate.get("warning"):
                                    ui.label(str(candidate["warning"])).classes("text-warning text-xs")
                                if candidate.get("requires_bind"):
                                    ui.label(
                                        "Requires explicit LAN host binding before direct remote use."
                                    ).classes("text-warning text-xs")
                            if _candidate_url(candidate):
                                _copy_button(_candidate_url(candidate), "Copy candidate URL")

                custom_origin = ui.input("Custom origin", value=_candidate_url(default_candidate)).classes("w-full").props(
                    "dense outlined"
                )

                def _create_custom_pairing() -> None:
                    origin = str(custom_origin.value or "").strip()
                    if not origin:
                        ui.notify("Enter a custom origin before pairing", type="warning")
                        return
                    candidate = _custom_candidate(origin)
                    candidate["access_mode"] = _access_mode_for_url(candidates, origin)
                    _open_pair_phone_dialog(candidate)

                ui.button("Create custom pairing QR", icon="qr_code_2", on_click=_create_custom_pairing).props(
                    "flat dense no-caps color=primary"
                )

        _render_devices()
        _render_events()
