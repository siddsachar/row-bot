"""Device sign-in: a desktop Row-Bot joins the company and gets a credential of its own (C1a).

RFC 8252's native-app flow, with a loopback redirect and PKCE (S256 only):

1. ``GET /auth/device`` in the system browser, with ``redirect_uri`` (``http://127.0.0.1:<port>/<path>`` or
   ``http://[::1]:<port>/<path>``: no other host, no ``localhost``, no query, fragment or userinfo),
   ``code_challenge`` and ``code_challenge_method=S256``, ``state`` (1-256 printable characters), and
   ``device_name``, ``platform`` and ``app_version`` (as ``POST /api/devices`` bounds them). A bad request gets a
   plain error page, never a redirect.
   - Not signed in: the request waits, signed with the server's pepper, in its own ``__Host-`` cookie for ten
     minutes, and the browser goes through the company sign-in (``/auth/login?next=/auth/device``), which comes
     back to ``GET /auth/device``.
   - Signed in: the confirm page, "Connect Row-Bot on <device> to <server>?", a POST form with Connect and
     Cancel. The request's parameters travel in a signed form token bound to the browser's session (ten
     minutes), not in fields the server trusts.
2. ``POST /auth/device/confirm`` (the session and the same-origin rule, as every browser POST):
   - Cancel: 303 to ``redirect_uri?error=access_denied&state=...``.
   - Connect: a new device (``devices.create_device``, ``device.joined``) and a one-time authorization code
     (256 bits, 60 seconds, kept only as its verifier, bound to the challenge, the redirect URI, the person and
     the device), then 303 to ``redirect_uri?code=...&state=...``.
3. ``POST /api/device/token`` (JSON or a form; no cookie, outside the same-origin rule: it carries no ambient
   credential): ``grant_type=authorization_code`` with ``code``, ``code_verifier`` and ``redirect_uri``, or
   ``grant_type=refresh_token`` with ``refresh_token``. It answers ``{"access_token", "token_type": "Bearer",
   "expires_in", "refresh_token", "device_id"}``, or OAuth's errors (400 ``invalid_request``,
   ``invalid_grant``, ``unsupported_grant_type``) with nothing more. A code works once, and presented again
   revokes everything issued for its device; a refresh token rotates, and one presented again after it was
   rotated revokes all its device's tokens (the device record stays).
4. ``Authorization: Bearer <access token>`` on the person's API, the gateway and audit ingest (``identity.gate``):
   live only while the token is unexpired, its device unrevoked and its person active, checked on every request.
5. ``POST /api/device/leave`` (the bearer): the device is revoked (``device.removed``, reason ``left``) with its
   tokens: 204. Again with the same token: 401, since the token died with its device.

``/auth/device`` and the token endpoint share a per-address limit (``identity.limits``, the sign-in setting's
count a minute). No log line carries a code, a token or the request's state.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import html
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import psycopg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from row_bot.audit import Action, Target, TargetKind
from row_bot_org import audit
from row_bot_org.db.rls import principal_transaction
from row_bot_org.devices import DeviceIn, create_device, record_joined
from row_bot_org.identity import store
from row_bot_org.identity.gate import RequestPrincipal, request_principal, session_cookie
from row_bot_org.identity.limits import WINDOW_SECONDS, AddressRateLimit
from row_bot_org.identity.tokens import (
    ACCESS_PREFIX,
    CODE_PREFIX,
    REFRESH_PREFIX,
    is_code_verifier,
    new_secret,
    pkce_challenge,
    prefixed_secret,
)
from row_bot_org.identity.web import IdentityService, _clear_cookie, _client_address, _set_cookie, principal_connection

logger = logging.getLogger(__name__)

START_PATH = "/auth/device"
CONFIRM_PATH = "/auth/device/confirm"
STYLE_PATH = "/auth/device.css"
TOKEN_PATH = "/api/device/token"
LEAVE_PATH = "/api/device/leave"
# The device sign-in waiting for the company sign-in to finish.
REQUEST_COOKIE = "__Host-row_bot_org_device"
REQUEST_SECONDS = 600
# The credential's lifetimes (README "Devices"): an hour of access, a refresh token unused for 30 days ends, and
# none lasts past 180 days from the join.
ACCESS_LIFETIME = timedelta(hours=1)
REFRESH_IDLE = timedelta(days=30)
REFRESH_LIFETIME = timedelta(days=180)
LIFETIMES = store.Lifetimes(ACCESS_LIFETIME, REFRESH_IDLE, REFRESH_LIFETIME)
MAX_REDIRECT_URI = 512
MAX_SEALED = 4096
MAX_FORM_BYTES = 8 * 1024
MAX_TOKEN_BODY = 16 * 1024
_REDIRECT_URI = re.compile(r"http://(?:127\.0\.0\.1|\[::1\]):([1-9][0-9]{0,4})(/[A-Za-z0-9._~/-]*)")
_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
_STATE = re.compile(r"[!-~]{1,256}")
_PLATFORMS = {"windows": "Windows", "macos": "macOS", "linux": "Linux"}

DEVICE_CSS = """\
:root { color-scheme: light dark; --ink: #1c1d21; --muted: #5c606b; --page: #f6f7f9; --card: #ffffff;
  --line: #d9dce3; --accent: #2457d6; --on-accent: #ffffff; }
@media (prefers-color-scheme: dark) {
  :root { --ink: #eceef2; --muted: #a3a8b3; --page: #15161a; --card: #1f2126; --line: #343741;
    --accent: #7aa2ff; --on-accent: #0d1020; }
}
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 16px;
  background: var(--page); color: var(--ink); font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { width: 100%; max-width: 440px; background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  padding: 28px 24px; }
h1 { font-size: 1.25rem; line-height: 1.35; margin: 0 0 12px; overflow-wrap: anywhere; }
p { margin: 0 0 12px; color: var(--muted); overflow-wrap: anywhere; }
form { display: flex; gap: 12px; margin-top: 20px; flex-wrap: wrap; }
button { flex: 1 1 140px; font: inherit; font-weight: 600; padding: 10px 16px; border-radius: 8px; cursor: pointer;
  border: 1px solid var(--line); background: transparent; color: var(--ink); }
button[value="connect"] { background: var(--accent); border-color: var(--accent); color: var(--on-accent); }
button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
"""


@dataclass(frozen=True)
class DeviceRequest:
    """A device sign-in, as checked at ``GET /auth/device``."""

    redirect_uri: str
    code_challenge: str
    state: str
    name: str
    platform: str
    app_version: str


def device_request(params: list[tuple[str, str]]) -> DeviceRequest | None:
    """The request in ``params`` (each name at most once), or None when anything is missing or out of bounds."""
    values: dict[str, str] = {}
    for name, value in params:
        if name in values:
            return None
        values[name] = value
    uri, challenge, state = (values.get(name, "") for name in ("redirect_uri", "code_challenge", "state"))
    port = _REDIRECT_URI.fullmatch(uri) if len(uri) <= MAX_REDIRECT_URI else None
    if (port is None or int(port.group(1)) > 65535 or values.get("code_challenge_method") != "S256"
            or not _CHALLENGE.fullmatch(challenge) or not _STATE.fullmatch(state)):
        return None
    try:
        device = DeviceIn(name=values.get("device_name", ""), platform=values.get("platform", ""),
                          app_version=values.get("app_version", ""))
    except ValidationError:
        return None
    return DeviceRequest(uri, challenge, state, device.name, device.platform, device.app_version)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class DeviceService:
    """Device sign-in for one server: the identity service's pool and pepper, and its own per-address limit."""

    def __init__(self, identity: IdentityService, clock: Callable[[], float] = time.time) -> None:
        self.identity = identity
        self.pool = identity.pool
        self.host = urlsplit(identity.origin).netloc
        self.limit = AddressRateLimit(identity.settings.sign_in_rate_limit)
        self._clock = clock

    def verifier(self, purpose: str, secret: str) -> bytes:
        return self.identity.verifier(purpose, secret)

    def resolve(self, token: str) -> RequestPrincipal | None:
        """The gate's bearer lookup: the live device and person for an access token."""
        secret = prefixed_secret(ACCESS_PREFIX, token)
        if secret is None:
            return None
        found = store.resume_device_token(self.pool, self.verifier("device-access", secret))
        return None if found is None else RequestPrincipal(found.principal_id, found.groups, None, found.device_id)

    # -- the request, signed while it travels through the browser ----------------------------------------

    def seal(self, request: DeviceRequest, *, session_id: str | None = None) -> str:
        fields = {"r": request.redirect_uri, "c": request.code_challenge, "s": request.state, "n": request.name,
                  "p": request.platform, "v": request.app_version, "e": int(self._clock()) + REQUEST_SECONDS,
                  "t": session_id or ""}
        payload = _b64(json.dumps(fields, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        return payload + "." + _b64(self.verifier("device-request", payload))

    def unseal(self, sealed: str | None, *, session_id: str | None = None) -> DeviceRequest | None:
        """The request ``seal`` made, while unexpired and for this session (or for none, the pending cookie)."""
        if not sealed or len(sealed) > MAX_SEALED:
            return None
        payload, _, mac = sealed.partition(".")
        expected = _b64(self.verifier("device-request", payload)).encode("ascii")
        if not hmac.compare_digest(expected, mac.encode("utf-8")):
            return None
        try:
            fields = json.loads(_unb64(payload))
            request = DeviceRequest(fields["r"], fields["c"], fields["s"], fields["n"], fields["p"], fields["v"])
            live = int(fields["e"]) > self._clock() and fields["t"] == (session_id or "")
        except (binascii.Error, KeyError, TypeError, ValueError):
            return None
        return request if live else None


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{html.escape(title)}</title><link rel="stylesheet" href="{STYLE_PATH}"></head>'
        f"<body><main>{body}</main></body></html>", status_code=status)


def _refused(status: int = 400) -> HTMLResponse:
    return _page("Row-Bot can't connect", "<h1>This link can't connect Row-Bot</h1>"
                 "<p>It's incomplete, expired or already used. Start again from Row-Bot's settings.</p>", status)


def _too_many_pages() -> HTMLResponse:
    response = _page("Try again soon", "<h1>Too many tries</h1><p>Wait a minute, then start again from "
                     "Row-Bot's settings.</p>", 429)
    response.headers["Retry-After"] = str(int(WINDOW_SECONDS))
    return response


def _confirm_page(service: DeviceService, request: DeviceRequest, form_token: str,
                  person: dict[str, Any] | None) -> HTMLResponse:
    device, host = html.escape(request.name), html.escape(service.host)
    who = ""
    name, email = (person["display_name"] or person["email"], person["email"]) if person else (None, None)
    if name:
        also = f" ({html.escape(email)})" if email and email != name else ""
        who = f"<p>You're signed in as {html.escape(name)}{also}.</p>"
    return _page(
        "Connect Row-Bot",
        f"<h1>Connect Row-Bot on {device} to {host}?</h1>{who}"
        f"<p>{_PLATFORMS[request.platform]}, Row-Bot {html.escape(request.app_version)}. Row-Bot on this device "
        "will use the company's server as you until you leave or an admin removes it.</p>"
        f'<form method="post" action="{CONFIRM_PATH}">'
        f'<input type="hidden" name="form_token" value="{html.escape(form_token)}">'
        '<button type="submit" name="decision" value="connect">Connect</button>'
        '<button type="submit" name="decision" value="cancel">Cancel</button></form>')


def _back_to_app(request: DeviceRequest, **params: str) -> RedirectResponse:
    return RedirectResponse(request.redirect_uri + "?" + urlencode({**params, "state": request.state}),
                            status_code=303)


async def _read(request: Request, cap: int) -> bytes | None:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > cap:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _form_pairs(body: bytes) -> dict[str, str] | None:
    """A urlencoded form's fields, each at most once, or None."""
    try:
        pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True, strict_parsing=bool(body))
    except (UnicodeDecodeError, ValueError):
        return None
    fields = dict(pairs)
    return fields if len(fields) == len(pairs) else None


async def _confirm_form(request: Request) -> dict[str, str] | None:
    body = await _read(request, MAX_FORM_BYTES)
    return None if body is None else _form_pairs(body)


async def _token_params(request: Request) -> dict[str, str] | None:
    """The token request's parameters, from JSON (strings only) or a urlencoded form, or None."""
    body = await _read(request, MAX_TOKEN_BODY)
    if body is None:
        return None
    kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if kind == "application/x-www-form-urlencoded":
        return _form_pairs(body)
    if kind != "application/json":
        return None
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or not all(isinstance(value, str) for value in data.values()):
        return None
    return data


def _oauth_error(error: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=status, headers={"Pragma": "no-cache"})


def device_router(service: DeviceService) -> APIRouter:
    router = APIRouter()
    signed_in = Depends(principal_connection, scope="function")

    @router.get(STYLE_PATH)
    def style() -> Response:
        return Response(DEVICE_CSS, media_type="text/css")

    @router.get(START_PATH)
    def start(request: Request) -> Response:
        if not service.limit.allow(_client_address(request)):
            return _too_many_pages()
        params = request.query_params.multi_items()
        pending = device_request(params) if params else service.unseal(request.cookies.get(REQUEST_COOKIE))
        if pending is None:
            return _refused()
        token = session_cookie(request.scope)
        principal = service.identity.resolve(token) if token is not None else None
        if principal is None:
            if not params:  # Back from the sign-in without a session: it didn't finish.
                return _refused()
            response: Response = RedirectResponse("/auth/login?next=" + START_PATH, status_code=303)
            _set_cookie(response, REQUEST_COOKIE, service.seal(pending), REQUEST_SECONDS)
            return response
        with principal_transaction(service.pool, principal.principal_id, principal.groups) as conn:
            person = store.current_principal(conn, principal.principal_id)
        response = _confirm_page(service, pending, service.seal(pending, session_id=principal.session_id), person)
        _clear_cookie(response, REQUEST_COOKIE)
        return response

    @router.post(CONFIRM_PATH)
    def confirm(principal: RequestPrincipal = Depends(request_principal),
                form: dict[str, str] | None = Depends(_confirm_form),
                conn: psycopg.Connection = signed_in) -> Response:
        pending = None if form is None else service.unseal(form.get("form_token"), session_id=principal.session_id)
        decision = None if form is None else form.get("decision")
        if pending is None or principal.session_id is None or decision not in ("connect", "cancel"):
            return _refused()
        if decision == "cancel":
            return _back_to_app(pending, error="access_denied")
        device = create_device(conn, name=pending.name, platform=pending.platform, app_version=pending.app_version)
        code = CODE_PREFIX + new_secret()
        store.issue_device_code(conn, device_id=device["id"], verifier=service.verifier(
            "device-code", code[len(CODE_PREFIX):]), code_challenge=pending.code_challenge,
            redirect_uri=pending.redirect_uri)
        record_joined(conn, principal.principal_id, device)
        logger.info("principal %s connected device %s", principal.principal_id, device["id"])
        return _back_to_app(pending, code=code)

    @router.post(TOKEN_PATH)
    async def token(request: Request) -> Response:
        if not service.limit.allow(_client_address(request)):
            return JSONResponse({"error": "too_many_requests"}, status_code=429,
                                headers={"Retry-After": str(int(WINDOW_SECONDS))})
        params = await _token_params(request)
        if params is None:
            return _oauth_error("invalid_request")
        grant = params.get("grant_type")
        access, refresh = new_secret(), new_secret()
        if grant == "authorization_code":
            code, code_verifier, redirect_uri = (params.get(name) for name in ("code", "code_verifier",
                                                                                "redirect_uri"))
            if code is None or code_verifier is None or redirect_uri is None:
                return _oauth_error("invalid_request")
            secret = prefixed_secret(CODE_PREFIX, code)
            if secret is None or not is_code_verifier(code_verifier):
                return _oauth_error("invalid_grant")
            device_id = await run_in_threadpool(
                store.redeem_device_code, service.pool, code=service.verifier("device-code", secret),
                challenge=pkce_challenge(code_verifier), redirect_uri=redirect_uri,
                access=service.verifier("device-access", access), refresh=service.verifier("device-refresh", refresh),
                lifetimes=LIFETIMES)
        elif grant == "refresh_token":
            presented = params.get("refresh_token")
            if presented is None:
                return _oauth_error("invalid_request")
            secret = prefixed_secret(REFRESH_PREFIX, presented)
            if secret is None:
                return _oauth_error("invalid_grant")
            device_id = await run_in_threadpool(
                store.refresh_device_token, service.pool, refresh=service.verifier("device-refresh", secret),
                access=service.verifier("device-access", access),
                new_refresh=service.verifier("device-refresh", refresh), lifetimes=LIFETIMES)
        else:
            return _oauth_error("invalid_request" if grant is None else "unsupported_grant_type")
        if device_id is None:
            logger.info("a device token request (%s) was refused", grant)
            return _oauth_error("invalid_grant")
        return JSONResponse({"access_token": ACCESS_PREFIX + access, "token_type": "Bearer",
                             "expires_in": int(ACCESS_LIFETIME.total_seconds()),
                             "refresh_token": REFRESH_PREFIX + refresh, "device_id": device_id},
                            headers={"Pragma": "no-cache"})

    @router.post(LEAVE_PATH)
    def leave(principal: RequestPrincipal = Depends(request_principal), conn: psycopg.Connection = signed_in) -> Response:
        if principal.device_id is None:
            return JSONResponse({"error": "device_credential_required"}, status_code=403)
        if store.leave_device(conn, principal.device_id):
            audit.emit(conn, Action.DEVICE_REMOVED, actor=audit.person(principal.principal_id),
                       target=Target(TargetKind.DEVICE, principal.device_id), device_id=principal.device_id,
                       details={"reason": "left"})
        return Response(status_code=204)

    return router


__all__ = ["ACCESS_LIFETIME", "CONFIRM_PATH", "LEAVE_PATH", "LIFETIMES", "REFRESH_IDLE", "REFRESH_LIFETIME",
           "REQUEST_COOKIE", "START_PATH", "STYLE_PATH", "TOKEN_PATH", "DeviceRequest", "DeviceService",
           "device_request", "device_router"]
