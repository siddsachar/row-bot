"""HTTP routes for Row-Bot mobile pairing and session management."""

from __future__ import annotations

from html import escape
from ipaddress import ip_address
from typing import Any

from fastapi import APIRouter
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from row_bot.brand import APP_BRAND_ACCENT, APP_DISPLAY_NAME
from row_bot.mobile.auth import PairingError, confirm_pairing, create_pairing_ticket, validate_device_token
from row_bot.mobile.cookies import clear_mobile_session_cookies, extract_mobile_cookie, set_mobile_session_cookie
from row_bot.mobile.store import MobileAuthStore

FORWARDED_HEADERS = {
    "forwarded",
    "x-forwarded-for",
    "x-real-ip",
    "x-client-ip",
    "x-forwarded-host",
    "x-forwarded-proto",
}

PAIRING_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Robots-Tag": "noindex",
}

PAIRING_ERROR_MESSAGES = {
    "expired": "This pairing code has expired. Create a new QR from desktop Mobile Access and try again.",
    "already_claimed": "This pairing code was already used. Create a new QR from desktop Mobile Access.",
    "locked": "This pairing code is temporarily locked after repeated failed attempts. Create a new QR from desktop Mobile Access.",
    "invalid_code": "This pairing link is invalid or incomplete. Create a new QR from desktop Mobile Access.",
}


def _client_ip(request: Request) -> str:
    return (request.client.host if request.client else "") or ""


def _has_forwarded_headers(request: Request) -> bool:
    return any(name in request.headers for name in FORWARDED_HEADERS)


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def is_true_local_request(request: Request) -> bool:
    """Return True only for direct loopback requests without proxy headers."""
    return _is_loopback_host(_client_ip(request)) and not _has_forwarded_headers(request)


def _safe_device(device) -> dict[str, Any]:
    return device.to_public_dict()


def _json_error(status_code: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": code, "detail": detail}, status_code=status_code)


def _pairing_error_message(reason: str) -> str:
    return PAIRING_ERROR_MESSAGES.get(reason, "Pairing code could not be confirmed. Create a new QR from desktop Mobile Access.")


def _request_origin(request: Request) -> str:
    return f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"


def _mobile_store(request: Request) -> MobileAuthStore:
    store = getattr(request.app.state, "row_bot_mobile_store", None)
    if store is None:
        store = MobileAuthStore()
        request.app.state.row_bot_mobile_store = store
    return store


def _current_device(request: Request) -> Any | None:
    token = extract_mobile_cookie(request)
    if not token:
        return None
    return validate_device_token(_mobile_store(request), token)


def _can_manage_mobile_access(request: Request) -> bool:
    device = _current_device(request)
    return is_true_local_request(request) or (device is not None and "settings" in device.scopes)


def _pairing_page(code: str = "", error: str = "") -> str:
    safe_code = escape(code)
    safe_error = escape(error)
    error_html = f"<p class=\"error\">{safe_error}</p>" if safe_error else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pair Row-Bot Mobile</title>
  <style>
    :root {{ color-scheme: dark; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #111719; color: #edf5f5; }}
    main {{ width: min(92vw, 440px); padding: 24px; }}
    h1 {{ font-size: 1.8rem; margin: 0 0 8px; }}
    p {{ color: #a9b8ba; line-height: 1.45; }}
    label {{ display: block; margin: 18px 0 8px; color: #c8d8da; }}
    input {{ width: 100%; box-sizing: border-box; padding: 13px 14px; border-radius: 8px; border: 1px solid #375257; background: #172225; color: #edf5f5; font: inherit; }}
    button {{ margin-top: 18px; width: 100%; border: 0; border-radius: 8px; padding: 13px 14px; background: #00b6c7; color: #061114; font-weight: 700; font: inherit; }}
    .error {{ color: #ffb4a8; }}
    .hint {{ font-size: 0.92rem; color: #7f9497; }}
  </style>
</head>
<body>
  <main>
    <h1>Pair Row-Bot</h1>
    <p>Name this device to finish pairing. The session token is stored in an HttpOnly cookie and never appears in the URL.</p>
    <p class="hint">Pairing links are single-use and expire after 10 minutes.</p>
    {error_html}
    <form method="post" action="/api/mobile/pair/confirm">
      <input type="hidden" name="code" value="{safe_code}">
      <label for="display_name">Device name</label>
      <input id="display_name" name="display_name" autocomplete="nickname" placeholder="My phone">
      <button type="submit">Pair device</button>
    </form>
  </main>
</body>
</html>"""


async def mobile_pair_page(request: Request) -> HTMLResponse:
    code = request.query_params.get("code", "")
    return HTMLResponse(_pairing_page(code=code), headers=PAIRING_PAGE_HEADERS)


async def mobile_manifest(request: Request) -> JSONResponse:  # noqa: ARG001
    return JSONResponse(
        {
            "name": f"{APP_DISPLAY_NAME} Mobile",
            "short_name": APP_DISPLAY_NAME,
            "description": "Local-first mobile companion for your running Row-Bot desktop host.",
            "start_url": "/?mobile=1",
            "scope": "/",
            "display": "standalone",
            "background_color": "#111719",
            "theme_color": APP_BRAND_ACCENT,
            "icons": [
                {
                    "src": "/static/row_bot_glyph_256.png",
                    "sizes": "256x256",
                    "type": "image/png",
                    "purpose": "any maskable",
                }
            ],
        },
        media_type="application/manifest+json",
    )


async def mobile_offline(request: Request) -> HTMLResponse:  # noqa: ARG001
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{APP_DISPLAY_NAME} unavailable</title>
  <style>
    :root {{ color-scheme: dark; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #111719; color: #edf5f5; }}
    main {{ width: min(90vw, 440px); padding: 24px; text-align: center; }}
    img {{ width: 96px; height: 96px; object-fit: contain; }}
    h1 {{ font-size: 1.6rem; }}
    p {{ color: #a9b8ba; line-height: 1.45; }}
  </style>
</head>
<body>
  <main>
    <img src="/static/row_bot_glyph_256.png" alt="">
    <h1>{APP_DISPLAY_NAME} is not reachable</h1>
    <p>Your desktop Row-Bot host needs to be awake, running, and reachable from this network or private access path.</p>
  </main>
</body>
</html>"""
    )


async def mobile_service_worker(request: Request) -> PlainTextResponse:  # noqa: ARG001
    body = """const CACHE_NAME = 'row-bot-mobile-shell-v2';
const SHELL_ASSETS = ['/mobile/offline', '/static/row_bot_glyph_256.png'];
const PRIVATE_PREFIXES = ['/api/', '/_media', '/published', '/_buddy', '/_nicegui_ws', '/_nicegui/'];
const BYPASS_PATHS = ['/mobile/pair', '/mobile/service-worker.js'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (BYPASS_PATHS.includes(url.pathname)) return;
  if (PRIVATE_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))) return;
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(() => caches.match('/mobile/offline')));
    return;
  }
  if (SHELL_ASSETS.includes(url.pathname)) {
    event.respondWith(caches.match(request).then((cached) => cached || fetch(request)));
  }
});
"""
    return PlainTextResponse(
        body,
        media_type="text/javascript; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


async def mobile_pair_start(request: Request) -> JSONResponse:
    if not is_true_local_request(request) and _current_device(request) is None:
        return _json_error(403, "local_required", "Pairing codes can only be created from localhost or an existing paired device.")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    origin = str(payload.get("intended_origin") or _request_origin(request)).rstrip("/")
    access_mode = str(payload.get("access_mode") or "localhost").strip()[:80]
    ticket = create_pairing_ticket(
        _mobile_store(request),
        intended_origin=origin,
        access_mode=access_mode,
    )
    return JSONResponse(
        {
            "ok": True,
            "pairing": {
                "id": ticket.id,
                "code": ticket.code,
                "expires_at": ticket.expires_at,
                "pairing_url": ticket.pairing_url(origin),
                "intended_origin": ticket.intended_origin,
                "access_mode": ticket.access_mode,
            },
        }
    )


async def mobile_pair_confirm(request: Request) -> Response:
    content_type = request.headers.get("content-type", "")
    is_json_request = content_type.startswith("application/json")
    if is_json_request:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
    else:
        try:
            form = await request.form()
            payload = dict(form)
        except Exception:
            payload = {}
    code = str(payload.get("code") or request.query_params.get("code") or "")
    display_name = str(payload.get("display_name") or "").strip() or "Mobile device"
    store = _mobile_store(request)
    try:
        confirmation = confirm_pairing(
            store,
            code=code,
            display_name=display_name,
            user_agent=request.headers.get("user-agent"),
            paired_from=_client_ip(request),
            access_mode=str(payload.get("access_mode") or "") or None,
        )
    except PairingError as exc:
        message = _pairing_error_message(exc.reason)
        store.log_event(
            "pairing_failed",
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            detail={"reason": exc.reason},
        )
        if not is_json_request:
            return HTMLResponse(
                _pairing_page(code=code, error=message),
                status_code=400,
                headers=PAIRING_PAGE_HEADERS,
            )
        return _json_error(400, exc.reason, message)

    store.log_event(
        "paired",
        device_id=confirmation.device.id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        detail={"access_mode": confirmation.device.access_mode},
    )
    if is_json_request:
        response = JSONResponse(
            {
                "ok": True,
                "authenticated": True,
                "device": _safe_device(confirmation.device),
            }
        )
    else:
        response = RedirectResponse("/?mobile=1", status_code=303)
    set_mobile_session_cookie(response, confirmation.token, scheme=request.url.scheme)
    return response


async def mobile_session(request: Request) -> JSONResponse:
    device = _current_device(request)
    if device is None:
        return JSONResponse({"ok": True, "authenticated": False, "device": None})
    _mobile_store(request).log_event(
        "session_validated",
        device_id=device.id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return JSONResponse({"ok": True, "authenticated": True, "device": _safe_device(device)})


async def mobile_devices(request: Request) -> JSONResponse:
    if not _can_manage_mobile_access(request):
        return _json_error(403, "forbidden", "Mobile device management requires localhost or a paired settings session.")
    devices = [_safe_device(device) for device in _mobile_store(request).list_devices(include_revoked=True)]
    return JSONResponse({"ok": True, "devices": devices})


async def mobile_revoke_device(request: Request) -> JSONResponse:
    if not _can_manage_mobile_access(request):
        return _json_error(403, "forbidden", "Mobile device management requires localhost or a paired settings session.")
    device_id = request.path_params.get("device_id", "")
    store = _mobile_store(request)
    revoked = store.revoke_device(device_id)
    store.log_event(
        "revoked",
        device_id=device_id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    response = JSONResponse({"ok": bool(revoked), "revoked": bool(revoked)})
    current = _current_device(request)
    if current and current.id == device_id:
        clear_mobile_session_cookies(response)
    return response


async def mobile_access_events(request: Request) -> JSONResponse:
    if not _can_manage_mobile_access(request):
        return _json_error(403, "forbidden", "Mobile access events require localhost or a paired settings session.")
    events = [event.to_public_dict() for event in _mobile_store(request).recent_events(limit=50)]
    return JSONResponse({"ok": True, "events": events})


def build_mobile_router() -> APIRouter:
    router = APIRouter()
    router.add_api_route("/mobile/pair", mobile_pair_page, methods=["GET"])
    router.add_api_route("/mobile/manifest.webmanifest", mobile_manifest, methods=["GET"])
    router.add_api_route("/mobile/offline", mobile_offline, methods=["GET"])
    router.add_api_route("/mobile/service-worker.js", mobile_service_worker, methods=["GET"])
    router.add_api_route("/api/mobile/pair/start", mobile_pair_start, methods=["POST"])
    router.add_api_route("/api/mobile/pair/confirm", mobile_pair_confirm, methods=["POST"])
    router.add_api_route("/api/mobile/session", mobile_session, methods=["GET"])
    router.add_api_route("/api/mobile/devices", mobile_devices, methods=["GET"])
    router.add_api_route("/api/mobile/devices/{device_id}/revoke", mobile_revoke_device, methods=["POST"])
    router.add_api_route("/api/mobile/access-events", mobile_access_events, methods=["GET"])
    return router


def register_mobile_routes(app, *, store: MobileAuthStore | None = None) -> None:
    """Register mobile routes on a FastAPI/NiceGUI app."""
    if store is not None:
        app.state.row_bot_mobile_store = store
    app.include_router(build_mobile_router())
