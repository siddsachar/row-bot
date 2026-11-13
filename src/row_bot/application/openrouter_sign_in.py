"""Try Row-Bot free: sign in to OpenRouter, which hands back a key the person owns.

OpenRouter's sign-in is OAuth with PKCE (no client secret). Its page sends the
browser back with a one-time code, which Row-Bot trades for an API key and saves
like a pasted one, so every OpenRouter model (its free ones too) then works.

The browser comes back to a one-time loopback address when it runs on the
computer Row-Bot runs on, and to Row-Bot's public HTTPS address from another
device (a phone, a hosted Row-Bot). Nothing is sent before the person starts it.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import logging
import secrets
import socket
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
import uuid

from row_bot.application.client_platform import ClientPlatformError

logger = logging.getLogger(__name__)

AUTH_URL = "https://openrouter.ai/auth"
KEYS_URL = "https://openrouter.ai/api/v1/auth/keys"
CALLBACK_PATH = "/api/v1/settings/providers/openrouter/sign-in/callback"
TTL = 600  # Seconds a sign-in waits for the browser to come back.
_DONE = {"done", "failed", "cancelled", "expired", "unavailable"}


@dataclass
class _Flow:
    flow_id: str
    owner_id: str
    state: str
    verifier: str
    route: str
    url: str = ""
    phase: str = "waiting"
    message: str = ""
    started: float = field(default_factory=time.monotonic)
    servers: list = field(default_factory=list)


_FLOWS: dict[str, _Flow] = {}
_LOCK = threading.RLock()


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _auth_url(callback: str, challenge: str) -> str:
    return f"{AUTH_URL}?{urlencode({'callback_url': callback, 'code_challenge': challenge, 'code_challenge_method': 'S256'})}"


def start(*, owner_id: str, device: Any) -> dict:
    """Start a sign-in; the page to open comes back with it (this computer's browser opens it too)."""
    from row_bot.application.client_account_oauth import public_callback_origin

    verifier, challenge = _pkce()
    flow = _Flow(str(uuid.uuid4()), owner_id, secrets.token_urlsafe(32), verifier,
                 "this_computer" if device.on_this_computer else "device")
    if device.on_this_computer:
        callback = _listen(flow)
    else:
        origin = public_callback_origin(device.public_origins)
        if origin is None:
            flow.phase = "unavailable"
            flow.message = ("Signing in to OpenRouter from this device needs Row-Bot's HTTPS address. "
                            "Sign in on the computer Row-Bot runs on, or use an OpenRouter key.")
            with _LOCK:
                _FLOWS[flow.flow_id] = flow
            return _snapshot(flow)
        callback = f"{origin}{CALLBACK_PATH}?{urlencode({'state': flow.state})}"
    flow.url = _auth_url(callback, challenge)
    with _LOCK:
        _expire()
        _FLOWS[flow.flow_id] = flow
    if device.on_this_computer:
        _open_browser(flow.url)
    return _snapshot(flow)


def read(*, owner_id: str, flow_id: str) -> dict:
    with _LOCK:
        _expire()
        return _snapshot(_owned(owner_id, flow_id))


def cancel(*, owner_id: str, flow_id: str) -> dict:
    with _LOCK:
        flow = _owned(owner_id, flow_id)
        if flow.phase == "waiting":
            flow.phase, flow.message = "cancelled", ""
            _close(flow)
        return _snapshot(flow)


def accept_callback(*, state: str, code: str) -> str:
    """The public callback: only a waiting sign-in's one-time state is accepted. Returns its phase."""
    # OpenRouter adds its code to the callback's own query; a "?code=" inside the state is that too.
    state, _, appended = state.partition("?code=")
    code = code or appended
    with _LOCK:
        _expire()
        flow = next((item for item in _FLOWS.values() if item.phase == "waiting" and item.route == "device"
                     and hmac.compare_digest(item.state.encode(), state.encode())), None)
        if flow is None:
            return "invalid"
    _finish(flow, code)
    return flow.phase


def _owned(owner_id: str, flow_id: str) -> _Flow:
    flow = _FLOWS.get(flow_id)
    if flow is None or flow.owner_id != owner_id:
        raise ClientPlatformError("not_found")
    return flow


def _snapshot(flow: _Flow) -> dict:
    return {"schema_version": 1, "flow_id": flow.flow_id, "phase": flow.phase, "route": flow.route,
            "url": flow.url or None, "message": flow.message}


def _expire() -> None:
    now = time.monotonic()
    for flow_id, flow in list(_FLOWS.items()):
        if flow.phase == "waiting" and now - flow.started > TTL:
            flow.phase, flow.message = "expired", "The sign-in ran out of time. Start it again."
            _close(flow)
        elif flow.phase in _DONE and now - flow.started > 2 * TTL:
            del _FLOWS[flow_id]


def _close(flow: _Flow) -> None:
    for server in flow.servers:
        threading.Thread(target=_stop_server, args=(server,), daemon=True).start()
    flow.servers = []


def _stop_server(server: HTTPServer) -> None:
    server.shutdown()
    server.server_close()


def _finish(flow: _Flow, code: str) -> None:
    """Trade the code for the person's key and save it as OpenRouter's key."""
    with _LOCK:
        if flow.phase != "waiting":
            return
        flow.phase = "finishing"
    try:
        if not code:
            raise RuntimeError("OpenRouter didn't allow the sign-in.")
        key = _exchange(code[:4096], flow.verifier)
        from row_bot.application.provider_settings_commands import invalidate_provider_runtime
        from row_bot.providers.credential_controls import save_api_key
        from row_bot.providers.model_catalog_cache import provider_connection_changed

        save_api_key("openrouter", key)
        invalidate_provider_runtime()
        provider_connection_changed("openrouter", connected=True)
        phase, message = "done", ""
    except Exception as exc:
        logger.warning("OpenRouter sign-in didn't finish: %s", type(exc).__name__)
        phase, message = "failed", "Signing in to OpenRouter didn't finish. Try again, or use an OpenRouter key."
    with _LOCK:
        flow.phase, flow.message = phase, message
        _close(flow)


def _exchange(code: str, verifier: str) -> str:
    import httpx

    response = httpx.post(KEYS_URL, json={"code": code, "code_verifier": verifier,
                                          "code_challenge_method": "S256"}, timeout=20)
    response.raise_for_status()
    key = str((response.json() or {}).get("key") or "")
    if not key:
        raise RuntimeError("OpenRouter returned no key")
    return key


def _open_browser(url: str) -> None:
    import webbrowser
    webbrowser.open(url)


class _Loopback6(HTTPServer):
    address_family = socket.AF_INET6


def _listen(flow: _Flow) -> str:
    """A one-time loopback address for this sign-in; OpenRouter allows localhost on any port.

    The state is the address's path: OpenRouter adds its code as the query."""

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 -- http.server's name
            parsed = urlparse(self.path)
            query = {key: values[0] for key, values in parse_qs(parsed.query).items() if len(values) == 1}
            given = parsed.path.lstrip("/")
            if flow.phase != "waiting" or not hmac.compare_digest(given.encode(), flow.state.encode()):
                self._say(400, "This isn't Row-Bot's current sign-in. Return to Row-Bot and sign in again.")
                return
            # The key is saved before the page says so; its own thread keeps the server answering.
            done = threading.Event()
            threading.Thread(target=lambda: (_finish(flow, str(query.get("code") or "")), done.set()),
                             daemon=True).start()
            done.wait(25)
            self._say(200 if flow.phase == "done" else 400,
                      "Row-Bot is signed in to OpenRouter. You can close this tab." if flow.phase == "done"
                      else "Signing in to OpenRouter didn't finish. Return to Row-Bot and try again.")

        def _say(self, status: int, text: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(text.encode("utf-8"))

        def log_message(self, *_: Any) -> None:
            pass  # The address holds the code: never logged.

    servers = [HTTPServer(("127.0.0.1", 0), Callback)]
    try:
        servers.append(_Loopback6(("::1", servers[0].server_address[1]), Callback))
    except OSError:
        pass  # No IPv6 loopback here: a browser goes on to 127.0.0.1.
    for server in servers:
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True,
                         name="row-bot-openrouter-callback").start()
    flow.servers = servers
    return f"http://localhost:{servers[0].server_address[1]}/{flow.state}"
