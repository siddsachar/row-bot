"""The company sign-in in this computer's browser, back to a loopback address here.

Row-Bot listens on ``127.0.0.1`` (the server accepts no other loopback name) on a port chosen for this
sign-in, with one path, and opens the browser at the server's ``/auth/device`` with PKCE (S256), a one-time
``state`` and this computer's name, platform and Row-Bot version. The server's Connect sends the browser back
with a one-time code; only this sign-in's ``state`` is accepted, once. The page the browser is shown loads
nothing and says to close the tab. The address (which holds the code) is never logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from row_bot.version import __version__

CALLBACK_PATH = "/row-bot/company-sign-in"
LOOPBACK_TTL = 300  # Seconds the sign-in waits for the browser to come back.
# Monotonic seconds; tests move it.
clock = time.monotonic

_PAGE = (
    "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><title>Row-Bot</title>"
    "<body style=\"font:16px system-ui,sans-serif;margin:3rem auto;max-width:32rem;padding:0 1rem\">"
    "<p>{text}</p></body></html>"
)


class TimedOut(Exception):
    """The browser didn't come back in time."""


class Cancelled(Exception):
    """The person cancelled in Row-Bot."""


def pkce() -> tuple[str, str]:
    """A code verifier and its S256 challenge."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def platform_name() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


def authorize_url(origin: str, *, redirect_uri: str, challenge: str, state: str, device_name: str,
                  device_id: str | None = None) -> str:
    query = {"redirect_uri": redirect_uri, "code_challenge": challenge, "code_challenge_method": "S256",
             "state": state, "device_name": device_name, "platform": platform_name(), "app_version": __version__}
    if device_id:
        query["device_id"] = device_id  # Signing in again: the same device is kept.
    return f"{origin}/auth/device?{urlencode(query)}"


def open_browser(url: str) -> None:
    import webbrowser

    if not webbrowser.open(url):
        raise OSError("no browser opened")


class Loopback:
    """One sign-in's listener: ``127.0.0.1``, a random port, one path, this sign-in's ``state`` only."""

    def __init__(self, state: str) -> None:
        self.state = state
        self.answer: dict[str, str] = {}
        self._arrived = threading.Event()
        listener = self

        class Callback(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 -- http.server's name
                parts = urlsplit(self.path)
                values = parse_qs(parts.query, keep_blank_values=True)
                query = {name: found[0] for name, found in values.items() if len(found) == 1}
                given = query.get("state", "")
                if (parts.path != CALLBACK_PATH or len(given) > 256 or listener._arrived.is_set()
                        or not hmac.compare_digest(given.encode(), listener.state.encode())):
                    self._say(400, "This isn't Row-Bot's current sign-in. Go back to Row-Bot and join again.")
                    return
                listener.answer = {"code": query.get("code", "")[:512], "error": query.get("error", "")[:64]}
                listener._arrived.set()
                self._say(200, "Row-Bot is finishing joining your company. You can close this tab."
                          if listener.answer["code"] else "The sign-in was cancelled. You can close this tab.")

            def _say(self, status: int, text: str) -> None:
                body = _PAGE.format(text=text).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_: Any) -> None:
                pass  # The address holds the code: never logged.

        self._server = HTTPServer(("127.0.0.1", 0), Callback)
        self.redirect_uri = f"http://127.0.0.1:{self._server.server_address[1]}{CALLBACK_PATH}"
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.1},
                                        daemon=True, name="row-bot-company-sign-in")
        self._thread.start()

    def wait(self, stop: threading.Event, ttl: float = LOOPBACK_TTL) -> dict[str, str]:
        """The browser's answer (``code`` or ``error``); raises Cancelled or TimedOut."""
        deadline = clock() + ttl
        while not self._arrived.wait(0.1):
            if stop.is_set():
                raise Cancelled
            if clock() >= deadline:
                raise TimedOut
        return self.answer

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
