"""A fake Row-Bot company server (C1a's device routes, S3's effective policy, S4's catalog and gateway) and a fake
browser, for the app's company tests.

The server is an ``httpx.MockTransport``: nothing leaves the process. The browser follows the app's sign-in
address the way a person pressing Connect would, then visits Row-Bot's loopback address for real (loopback
only). Tokens are shaped like the server's (``rboc_``, ``rboa_``, ``rbor_`` and 43 characters). The gateway
answers both wires, whole or streamed, the way S4's does (its refusals in the caller's wire format).
"""

from __future__ import annotations

import base64
import hashlib
import itertools
import json
import re
import threading
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from row_bot import secret_store
from row_bot.application import client_company
from row_bot.company import client, policy, provider, sign_in, store
from row_bot.policy import DEFAULT_POLICY, canonical_json, policy_to_dict
from tests.subsystem.plugins.conftest import MemoryKeyring

ORIGIN = "https://rowbot.example.test"
COMPANY = "rowbot.example.test"
PERSON = {"id": "00000000-0000-4000-8000-000000000001", "display_name": "Fixture Person",
          "email": "person@example.test", "status": "active", "groups": ["staff"], "admin": False}
# S4's GET /api/models: two chat models (one on each wire, the first the company's default) and an embedding one.
CATALOG = [
    {"id": "qwen3-32b", "display_name": "Qwen3 32B", "kind": "chat", "api": "openai", "context_window": 131072,
     "default": True},
    {"id": "claude-team", "display_name": "Claude for the team", "kind": "chat", "api": "anthropic",
     "context_window": 200000, "default": False},
    {"id": "bge-m3", "display_name": "BGE M3", "kind": "embeddings", "api": "openai", "context_window": 8192,
     "default": False},
]
REPLY = "Hello from the company"
GATEWAY_OPENAI = "/api/gateway/openai/v1/chat/completions"
GATEWAY_ANTHROPIC = "/api/gateway/anthropic/v1/messages"


def _sse(events: list[tuple[str | None, dict | str]]) -> bytes:
    lines = []
    for event, data in events:
        if event:
            lines.append(f"event: {event}")
        lines.append(f"data: {data if isinstance(data, str) else json.dumps(data)}")
        lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _openai_answer(model: str, stream: bool) -> httpx.Response:
    if not stream:
        return httpx.Response(200, json={
            "id": "chatcmpl-1", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": REPLY}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9}})
    words = REPLY.split(" ")
    chunks = [(None, {"id": "chatcmpl-1", "object": "chat.completion.chunk", "model": model,
                      "choices": [{"index": 0, "delta": {"content": (" " if index else "") + word},
                                   "finish_reason": None}]}) for index, word in enumerate(words)]
    chunks.append((None, {"id": "chatcmpl-1", "object": "chat.completion.chunk", "model": model,
                          "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                          "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9}}))
    chunks.append((None, "[DONE]"))
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(chunks))


def _anthropic_answer(model: str, stream: bool) -> httpx.Response:
    if not stream:
        return httpx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": REPLY}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 5, "output_tokens": 4}})
    events: list[tuple[str | None, dict | str]] = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 0}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
    ]
    for index, word in enumerate(REPLY.split(" ")):
        events.append(("content_block_delta", {"type": "content_block_delta", "index": 0,
                                               "delta": {"type": "text_delta", "text": (" " if index else "") + word}}))
    events += [
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": 4}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(events))


def gateway_error(wire: str, status: int, code: str, message: str) -> httpx.Response:
    """A gateway refusal in the caller's wire format (S4's ``upstream.error_body``)."""
    body = ({"type": "error", "error": {"type": code, "message": message}} if wire == "anthropic"
            else {"error": {"message": message, "type": code, "code": code}})
    return httpx.Response(status, json=body)
_REDIRECT = re.compile(r"^http://127\.0\.0\.1:[1-9][0-9]{0,4}/[A-Za-z0-9._~/-]*$")


@dataclass(frozen=True)
class LoopbackAnswer:
    status: int
    policy: str
    body: str


class FakeCompanyServer:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.authorize: list[dict[str, str]] = []  # Each /auth/device the browser opened.
        self.bad_requests: list[str] = []  # Sign-in addresses the real server would refuse.
        self.loopback_answers: list[LoopbackAnswer] = []
        self.devices: dict[str, dict] = {}
        self.codes: dict[str, dict] = {}
        self.access: dict[str, str] = {}  # Live access token -> device id.
        self.refresh: dict[str, str] = {}  # Live refresh token -> device id.
        self.refreshes = 0
        self.issued: list[str] = []  # Every token and code it gave out.
        # What the browser does: "connect", "cancel" (Cancel on the server's page), "wrong_state" (an answer with
        # another state first) or "ignore" (never comes back).
        self.browser = "connect"
        self.healthy = True
        self.limited = False  # The token endpoint answers 429.
        self.refresh_gate: tuple[threading.Event, threading.Event] | None = None  # (entered, release)
        self.on_bearer: Callable[[str], None] | None = None
        self.catalog: list[dict] = [dict(model) for model in CATALOG]
        self.gateway: list[httpx.Request] = []  # Every request that reached a gateway route.
        # The gateway's answer to the next calls instead of the model's: (status, code, message).
        self.refusal: tuple[int, str, str] | None = None
        # S3's GET /api/policy/effective: the person's effective policy (every setting, with its version).
        self.policy: dict = policy_to_dict(DEFAULT_POLICY)
        self.policy_answers: list[int] = []  # The status of each answer to it (200, or 304 for the same ETag).
        # Nothing reaches the server (a connection error for every request), while True.
        self.offline = False
        self._numbers = itertools.count(1)
        self._lock = threading.Lock()
        self.transport = httpx.MockTransport(self.handle)

    # -- the server ---------------------------------------------------------------------------------------

    def _token(self, prefix: str) -> str:
        token = f"{prefix}{next(self._numbers):043d}"
        self.issued.append(token)
        return token

    def _issue(self, device_id: str) -> dict:
        access, refresh = self._token("rboa_"), self._token("rbor_")
        self.access[access] = device_id
        self.refresh[refresh] = device_id
        return {"access_token": access, "token_type": "Bearer", "expires_in": 3600, "refresh_token": refresh,
                "device_id": device_id}

    def _end_tokens(self, device_id: str) -> None:
        for tokens in (self.access, self.refresh):
            for token in [token for token, owner in tokens.items() if owner == device_id]:
                del tokens[token]

    def revoke(self, device_id: str) -> None:
        """An admin removes the device."""
        with self._lock:
            self.devices[device_id]["revoked"] = True
            self._end_tokens(device_id)

    def end_refresh(self, device_id: str) -> None:
        """The device's refresh token reached its 30 days; the device itself is still live."""
        with self._lock:
            self._end_tokens(device_id)

    def end_access(self) -> None:
        """Every access token is refused from now on (its hour is up early); refresh tokens still work."""
        with self._lock:
            self.access.clear()

    def join_here(self) -> store.Record:
        """A Row-Bot joined to this server, as a finished sign-in leaves it, without the browser."""
        device_id = str(uuid.uuid4())
        with self._lock:
            self.devices[device_id] = {"name": "Fixture laptop", "platform": "windows", "app_version": None,
                                       "revoked": False}
            issued = self._issue(device_id)
        store.save_credential(store.Credential(issued["access_token"], issued["refresh_token"],
                                               client.now() + issued["expires_in"]))
        record = store.Record(origin=ORIGIN, device_id=device_id, company=COMPANY, name="Fixture Person",
                              email="person@example.test", device_name="Fixture laptop",
                              joined_at="2026-11-23T09:00:00+00:00")
        store.write_record(record)
        policy.refresh()  # A finished join reads the company's policy first.
        return record

    def _gateway(self, request: httpx.Request) -> httpx.Response:
        # A copy: a request sent again after a 401 is the same object, its Authorization changed.
        self.gateway.append(httpx.Request(request.method, request.url, headers=request.headers.copy(),
                                          content=request.content))
        if self._bearer(request) is None:
            return httpx.Response(401, json={"error": "invalid_token"},
                                  headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})
        wire = {GATEWAY_OPENAI: "openai", GATEWAY_ANTHROPIC: "anthropic"}.get(request.url.path)
        if wire is None or request.method != "POST":
            return httpx.Response(404, json={"detail": "Not Found"})
        if self.refusal is not None:
            return gateway_error(wire, *self.refusal)
        body = json.loads(request.content or b"{}")
        model = str(body.get("model") or "")
        if not any(item["id"] == model and item["api"] == wire for item in self.catalog):
            return gateway_error(wire, 403, "model_not_allowed", "This model isn't available to you.")
        return (_openai_answer if wire == "openai" else _anthropic_answer)(model, body.get("stream") is True)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.offline:
            raise httpx.ConnectError("offline", request=request)
        self.requests.append(request)
        response = self._route(request)
        # A jar would keep this and send it back; the server refuses a bearer with its session cookie.
        response.headers["Set-Cookie"] = "__Host-row_bot_org_session=fixture; Path=/; Secure; HttpOnly"
        return response

    def _bearer(self, request: httpx.Request) -> str | None:
        if "cookie" in request.headers:
            return None
        found = re.fullmatch(r"Bearer (rboa_[0-9]{43})", request.headers.get("authorization", ""))
        token = found.group(1) if found else None
        if token in self.access and self.on_bearer:
            self.on_bearer(token)
        return token if token in self.access else None

    def _route(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if (request.method, path) == ("GET", "/healthz"):
            return (httpx.Response(200, json={"status": "ok"}) if self.healthy
                    else httpx.Response(404, text="<html>Not here</html>"))
        if (request.method, path) == ("POST", "/api/device/token"):
            return self._token_endpoint(json.loads(request.content or b"{}"))
        if (request.method, path) == ("GET", "/api/me"):
            if self._bearer(request) is None:
                return httpx.Response(401, json={"error": "invalid_token"})
            return httpx.Response(200, json=PERSON)
        if (request.method, path) == ("POST", "/api/device/leave"):
            token = self._bearer(request)
            if token is None:
                return httpx.Response(401, json={"error": "invalid_token"})
            self.revoke(self.access[token])
            return httpx.Response(204)
        if (request.method, path) == ("GET", "/api/models"):
            if self._bearer(request) is None:
                return httpx.Response(401, json={"error": "invalid_token"})
            return httpx.Response(200, json={"models": self.catalog})
        if (request.method, path) == ("GET", "/api/policy/effective"):
            if self._bearer(request) is None:
                return httpx.Response(401, json={"error": "invalid_token"})
            body = canonical_json(self.policy)
            etag = '"' + hashlib.sha256(body).hexdigest() + '"'
            same = request.headers.get("if-none-match") == etag
            self.policy_answers.append(304 if same else 200)
            if same:
                return httpx.Response(304, headers={"ETag": etag})
            return httpx.Response(200, content=body, headers={"ETag": etag, "Content-Type": "application/json"})
        if path.startswith("/api/gateway/"):
            return self._gateway(request)
        return httpx.Response(404, json={"error": "not_found"})

    def _token_endpoint(self, body: dict) -> httpx.Response:
        if self.limited:
            return httpx.Response(429, json={"error": "too_many_requests"}, headers={"Retry-After": "60"})
        refused = httpx.Response(400, json={"error": "invalid_grant"})
        if body.get("grant_type") == "authorization_code":
            with self._lock:
                code = self.codes.get(str(body.get("code")))
                if code is None or code["used"]:
                    return refused
                code["used"] = True
                verifier = str(body.get("code_verifier") or "").encode("ascii")
                challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).rstrip(b"=").decode()
                if challenge != code["challenge"] or body.get("redirect_uri") != code["redirect_uri"]:
                    return refused
                return httpx.Response(200, json=self._issue(code["device_id"]))
        if body.get("grant_type") == "refresh_token":
            if self.refresh_gate:
                entered, release = self.refresh_gate
                entered.set()
                release.wait(5)
            with self._lock:
                self.refreshes += 1
                device_id = self.refresh.pop(str(body.get("refresh_token")), None)
                if device_id is None or self.devices[device_id]["revoked"]:
                    return refused
                return httpx.Response(200, json=self._issue(device_id))
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    # -- the browser --------------------------------------------------------------------------------------

    def browse(self, url: str) -> None:
        """The system browser at the app's sign-in address: the person is signed in and presses Connect."""
        parts = urlsplit(url)
        query = {name: values[0] for name, values in parse_qs(parts.query).items()}
        self.authorize.append(query)
        if f"{parts.scheme}://{parts.netloc}" != ORIGIN or parts.path != "/auth/device":
            self.bad_requests.append("not the server's sign-in page")
        if not _REDIRECT.fullmatch(query.get("redirect_uri", "")):
            self.bad_requests.append("redirect_uri")
        if query.get("code_challenge_method") != "S256" or len(query.get("code_challenge", "")) != 43:
            self.bad_requests.append("code_challenge")
        if not 1 <= len(query.get("state", "")) <= 256 or " " in query.get("state", ""):
            self.bad_requests.append("state")
        if query.get("platform") not in {"windows", "macos", "linux"}:
            self.bad_requests.append("platform")
        name = query.get("device_name", "")
        if not 1 <= len(name) <= 200 or name != name.strip():
            self.bad_requests.append("device_name")
        device_id = query.get("device_id")
        if device_id and (device_id not in self.devices or self.devices[device_id]["revoked"]):
            self.bad_requests.append("device_id")  # The server's "This link can't connect Row-Bot" page.
        if self.bad_requests or self.browser == "ignore":
            return
        redirect, state = query["redirect_uri"], query["state"]
        if self.browser == "cancel":
            self._visit(redirect, {"error": "access_denied", "state": state})
            return
        with self._lock:
            if device_id:
                self._end_tokens(device_id)  # Joining again: the device's earlier tokens end at Connect.
            else:
                device_id = str(uuid.uuid4())
            self.devices[device_id] = {"name": name, "platform": query["platform"],
                                       "app_version": query.get("app_version"), "revoked": False}
            code = self._token("rboc_")
            self.codes[code] = {"challenge": query["code_challenge"], "redirect_uri": redirect,
                                "device_id": device_id, "used": False}
        if self.browser == "wrong_state":
            self._visit(redirect, {"code": code, "state": "x" * 43})
        self._visit(redirect, {"code": code, "state": state})

    def _visit(self, redirect: str, params: dict[str, str]) -> None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(f"{redirect}?{urlencode(params)}", timeout=5) as answer:
                self.loopback_answers.append(LoopbackAnswer(answer.status, answer.headers.get(
                    "Content-Security-Policy", ""), answer.read().decode("utf-8")))
        except urllib.error.HTTPError as refused:
            self.loopback_answers.append(LoopbackAnswer(refused.code, refused.headers.get(
                "Content-Security-Policy", ""), refused.read().decode("utf-8")))


@pytest.fixture
def keychain() -> Iterator[MemoryKeyring]:
    found = MemoryKeyring()
    secret_store._set_backend_for_tests(found)
    yield found
    secret_store._set_backend_for_tests(None)


@pytest.fixture
def company_state(monkeypatch) -> None:
    """No Join, outcome or notice left from another test (they live in the app's process)."""
    for name, value in {"_join": None, "_failure": None, "_notice": None, "_left": None, "_unreachable": None,
                        "_fresh_next": False}.items():
        monkeypatch.setattr(client_company, name, value)
    monkeypatch.setattr(client, "_backoff_until", 0.0)
    # No company catalog read by another test.
    for name, value in {"_models": (), "_origin": None, "_read_at": None, "_reading": None}.items():
        monkeypatch.setattr(provider, name, value)
    # No company policy fetched, remembered or logged by another test.
    for name, value in {"_memo": None, "_attempted_at": None, "_reading": None, "_unknown_logged": set()}.items():
        monkeypatch.setattr(policy, name, value)


@pytest.fixture
def company_server(tmp_path, monkeypatch, keychain, company_state) -> Iterator[FakeCompanyServer]:
    """A scratch data folder, the in-memory keychain, the fake server and browser, and no join left over."""
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    server = FakeCompanyServer()
    monkeypatch.setattr(client, "transport", server.transport)
    monkeypatch.setattr(sign_in, "open_browser", server.browse)
    yield server
    client_company.cancel()
    provider.settle()  # A background catalog read ends here, while the fake server still answers it.
    policy.settle()  # And a background policy refresh.
