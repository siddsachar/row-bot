"""Microsoft's sign-in (login.microsoftonline.com) and Graph v1.0, faked on loopback for Outlook tests.

    with fake_microsoft() as microsoft:          # microsoft_account.LOGIN and GRAPH point at it
        ...
        microsoft.seen("POST", "/v1.0/me/sendMail")  # the requests Row-Bot sent, with their JSON bodies

The login side issues codes and tokens as Microsoft does: the authorization code flow checks PKCE (the
verifier must hash to the challenge the authorize request carried) and the redirect address; the device
code flow answers ``authorization_pending`` ``pending`` times, then tokens (or ``device_error``); a refresh
rotates the refresh token, and a revoked one answers ``invalid_grant``. Graph answers only the current
access token; ``graph`` maps ``(method, path)`` to an answer: a dict (200 JSON), an int (that status, no
body) or a callable of the request. Nothing here reaches the network.
"""
from __future__ import annotations

import base64
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

CLIENT_ID = "11111111-2222-3333-4444-555555555555"
ADDRESS = "you@yourdomain.test"
SCOPE = "Mail.ReadWrite Mail.Send Calendars.ReadWrite User.Read profile openid email"


@dataclass
class Seen:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    form: dict[str, str]
    body: Any


@dataclass
class FakeMicrosoft:
    url: str = ""
    requests: list[Seen] = field(default_factory=list)
    graph: dict[tuple[str, str], Any] = field(default_factory=dict)
    access_token: str = "access-1"
    refresh_token: str = "refresh-1"
    revoked: set[str] = field(default_factory=set)
    scope: str = SCOPE
    pending: int = 1  # Device code answers before the person "allows" it.
    device_error: str = ""  # e.g. "authorization_declined" or "expired_token" instead of tokens.
    authorize_error: str = ""  # e.g. "access_denied", sent back to the redirect address instead of a code.
    token_error: dict | None = None  # A refusal for the next code exchange (an AADSTS answer, say).
    codes: dict[str, dict] = field(default_factory=dict)
    issued: int = 1
    uploaded: dict[str, bytearray] = field(default_factory=dict)  # Upload session -> the bytes it took.

    def seen(self, method: str, path: str) -> list[Seen]:
        return [request for request in self.requests if request.method == method and request.path == path]

    def _tokens(self) -> dict:
        self.issued += 1
        self.access_token, self.refresh_token = f"access-{self.issued}", f"refresh-{self.issued}"
        return {"token_type": "Bearer", "access_token": self.access_token, "refresh_token": self.refresh_token,
                "expires_in": 3600, "scope": self.scope}

    def token(self, form: dict[str, str]) -> tuple[int, dict]:
        if form.get("client_id") != CLIENT_ID:
            return 400, {"error": "unauthorized_client", "error_codes": [700016]}
        grant = form.get("grant_type")
        if grant == "authorization_code":
            if self.token_error:
                return 400, self.token_error
            issued = self.codes.pop(form.get("code", ""), None)
            verifier = form.get("code_verifier", "").encode("ascii")
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).rstrip(b"=").decode("ascii")
            if issued is None or issued["challenge"] != challenge or issued["redirect_uri"] != form.get("redirect_uri"):
                return 400, {"error": "invalid_grant", "error_codes": [54005]}
            return 200, self._tokens()
        if grant == "urn:ietf:params:oauth:grant-type:device_code":
            if form.get("device_code") != "device-code-1":
                return 400, {"error": "bad_verification_code"}
            if self.pending > 0:
                self.pending -= 1
                return 400, {"error": "authorization_pending"}
            if self.device_error:
                return 400, {"error": self.device_error}
            return 200, self._tokens()
        if grant == "refresh_token":
            if form.get("refresh_token") in self.revoked or form.get("refresh_token") != self.refresh_token:
                return 400, {"error": "invalid_grant", "error_codes": [70008]}
            return 200, self._tokens()
        return 400, {"error": "unsupported_grant_type"}


class _Handler(BaseHTTPRequestHandler):
    fake: FakeMicrosoft

    def _handle(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        form = {key: values[0] for key, values in parse_qs(raw.decode("utf-8")).items()} \
            if "form" in str(self.headers.get("Content-Type")) else {}
        try:
            body = json.loads(raw) if raw and "json" in str(self.headers.get("Content-Type")) else None
        except ValueError:
            body = None
        seen = Seen(self.command, parsed.path, {k: v[0] for k, v in parse_qs(parsed.query).items()},
                    {k.lower(): v for k, v in self.headers.items()}, form, body)
        fake = self.fake
        fake.requests.append(seen)
        if parsed.path.endswith("/oauth2/v2.0/authorize"):
            query = seen.query
            target = query["redirect_uri"]
            if fake.authorize_error:
                back = {"error": fake.authorize_error, "state": query["state"],
                        "error_description": "AADSTS65004: User declined to consent."}
            else:
                code = f"code-{len(fake.codes) + 1}"
                fake.codes[code] = {"challenge": query["code_challenge"], "redirect_uri": target}
                back = {"code": code, "state": query["state"]}
            self.send_response(302)
            self.send_header("Location", target + "/?" + urlencode(back))
            self.end_headers()
            return
        if parsed.path.endswith("/oauth2/v2.0/devicecode"):
            return self._json(200, {"device_code": "device-code-1", "user_code": "ABCD-EFGH",
                                    "verification_uri": "https://microsoft.com/devicelogin", "expires_in": 900,
                                    "interval": 1, "message": "To sign in, use a web browser..."})
        if parsed.path.endswith("/oauth2/v2.0/token"):
            return self._json(*fake.token(form))
        if parsed.path.startswith("/upload/") and self.command == "PUT":
            # An upload session's own address: its key is in the address, so no token is expected.
            if self.headers.get("Authorization"):
                return self._json(400, {"error": {"code": "TokenNotExpected", "message": "no token here"}})
            start, end_total = self.headers["Content-Range"].removeprefix("bytes ").split("-")
            end, total = (int(value) for value in end_total.split("/"))
            received = fake.uploaded.setdefault(parsed.path, bytearray())
            if int(start) != len(received) or end - int(start) + 1 != len(raw):
                return self._json(416, {"error": {"code": "InvalidRange", "message": "out of order"}})
            received.extend(raw)
            return self._json(201 if len(received) == total else 200, {"nextExpectedRanges": [f"{len(received)}-"]})
        if parsed.path.startswith("/v1.0/"):
            if self.headers.get("Authorization") != f"Bearer {fake.access_token}":
                return self._json(401, {"error": {"code": "InvalidAuthenticationToken", "message": "expired"}})
            answer = fake.graph.get((self.command, parsed.path[len("/v1.0"):]))
            if answer is None and parsed.path == "/v1.0/me":
                answer = {"mail": ADDRESS, "userPrincipalName": ADDRESS, "displayName": "You", "id": "me-1"}
            if callable(answer):
                answer = answer(seen)
            if answer is None:
                return self._json(404, {"error": {"code": "ErrorItemNotFound", "message": "not found"}})
            if isinstance(answer, int):
                self.send_response(answer)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if isinstance(answer, tuple):
                return self._json(*answer)
            return self._json(200, answer)
        self._json(404, {})

    def _json(self, status: int, value: Any) -> None:
        data = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PATCH = do_DELETE = do_PUT = _handle

    def log_message(self, *_: Any) -> None:
        pass


@contextmanager
def fake_microsoft(monkeypatch: Any) -> Iterator[FakeMicrosoft]:
    """The fake, with Row-Bot's Microsoft sign-in and Graph addresses pointed at it."""
    from row_bot import microsoft_account

    fake = FakeMicrosoft()
    handler = type("Handler", (_Handler,), {"fake": fake})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    fake.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    monkeypatch.setattr(microsoft_account, "LOGIN", fake.url)
    monkeypatch.setattr(microsoft_account, "GRAPH", fake.url + "/v1.0")
    monkeypatch.setattr(microsoft_account, "_ACCESS", {})
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()


def browser(fake: FakeMicrosoft) -> Callable[[str], None]:
    """This computer's browser: opens Microsoft's sign-in page, which sends it back to Row-Bot's loopback."""
    import httpx

    def open_(url: str) -> None:
        assert url.startswith(fake.url + "/"), url  # Only Microsoft's own sign-in page is opened.
        threading.Thread(target=lambda: httpx.get(url, follow_redirects=True, timeout=10), daemon=True).start()
    return open_
