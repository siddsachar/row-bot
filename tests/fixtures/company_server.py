"""A fake Row-Bot company server (C1a's device routes, S3's effective policy, S4's catalog and gateway, S5's audit
ingest, the company brain's routes and B4a's review queue for its member) and a fake browser, for the app's company
tests.

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
import sys
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
from row_bot.audit import InvalidAuditEvent, from_dict, to_dict
from row_bot.company import audit, client, managed, policy, provider, sign_in, store
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
AUDIT_INGEST = "/api/audit/events"
# B1a's spaces and B7's company knowledge graph, as the server reads them for the fixture person: the company
# space and a group space they own.
COMPANY_SPACE = "5a000000-0000-4000-8000-000000000001"
SALES_SPACE = "5a000000-0000-4000-8000-000000000002"
BRAIN_SPACES = [{"id": COMPANY_SPACE, "name": "Company", "kind": "company", "is_owner": False},
                {"id": SALES_SPACE, "name": "Sales", "kind": "group", "is_owner": True}]
ATLAS, ADA, ORION, OLD = (f"e0000000-0000-4000-8000-00000000000{number}" for number in range(1, 5))
_ENTITY = {"aliases": [], "status": "active", "updated_at": "2026-11-20T09:00:00+00:00", "orphan": False}
BRAIN_NODES = [
    {**_ENTITY, "id": ATLAS, "space_id": COMPANY_SPACE, "subject": "Atlas", "entity_type": "project",
     "description": "The billing platform rebuild.", "aliases": ["Project Atlas"], "relation_count": 2},
    {**_ENTITY, "id": ADA, "space_id": COMPANY_SPACE, "subject": "Ada Lovelace", "entity_type": "person",
     "description": "Leads the platform team.", "relation_count": 1},
    {**_ENTITY, "id": ORION, "space_id": SALES_SPACE, "subject": "Orion Foods", "entity_type": "customer",
     "description": "A customer since 2024.", "relation_count": 1},
]
BRAIN_EDGES = [
    {"id": "f0000000-0000-4000-8000-000000000001", "space_id": COMPANY_SPACE, "source_id": ADA, "target_id": ATLAS,
     "relation_type": "leads", "status": "active", "origin": "extracted", "updated_at": "2026-11-20T09:00:00+00:00"},
    {"id": "f0000000-0000-4000-8000-000000000002", "space_id": SALES_SPACE, "source_id": ORION, "target_id": ATLAS,
     "relation_type": "uses", "status": "active", "origin": "manual", "updated_at": "2026-11-21T09:00:00+00:00"},
]
# B1a's recall and documents, as the server answers the fixture person (they own Sales).
HANDBOOK, ACCOUNTS = "d0000000-0000-4000-8000-000000000001", "d0000000-0000-4000-8000-000000000002"
RENEWAL_FACT = "c0000000-0000-4000-8000-000000000001"
_DOCUMENT = {"media_type": "text/markdown", "sha256": "0" * 64, "status_reason": None, "version": 1,
             "uploaded_by": PERSON["id"], "uploaded_at": "2026-11-20T09:00:00+00:00",
             "processed_at": "2026-11-20T09:01:00+00:00", "chunk_count": 3, "graph_status": "done",
             "graph_reason": None}
BRAIN_DOCUMENTS = [
    {**_DOCUMENT, "id": ACCOUNTS, "space_id": SALES_SPACE, "title": "Accounts 2026", "file_name": "accounts.md",
     "byte_count": 2048, "status": "ready"},
    {**_DOCUMENT, "id": "d0000000-0000-4000-8000-000000000003", "space_id": SALES_SPACE, "title": "Old prices",
     "file_name": "prices.docx", "byte_count": 4096, "status": "failed",
     "status_reason": "This Word document can't be read.", "chunk_count": 0, "graph_status": "pending"},
    {**_DOCUMENT, "id": HANDBOOK, "space_id": COMPANY_SPACE, "title": "Staff handbook", "file_name": "handbook.docx",
     "byte_count": 8192, "status": "ready"},
]
_SCORE = {"score": 0.9, "layer": "company", "owner": "organisation"}
BRAIN_HITS = [
    {"hit": {**_SCORE, "label": "From Company"}, "citation": {
        "type": "document", "space": {"id": COMPANY_SPACE, "name": "Company"},
        "document": {"id": HANDBOOK, "title": "Staff handbook", "file_name": "handbook.docx"}, "chunk": 2,
        "excerpt": "Everyone gets 25 days of holiday a year, plus public holidays."}},
    {"hit": {**_SCORE, "layer": "team", "label": "From Sales"}, "citation": {
        "type": "fact", "space": {"id": SALES_SPACE, "name": "Sales"},
        "fact": {"id": RENEWAL_FACT, "text": "Orion Foods renews its contract every March."},
        "document": {"id": ACCOUNTS, "title": "Accounts 2026", "file_name": "accounts.md"}}},
    {"hit": {**_SCORE, "layer": "team", "label": "From Sales"}, "citation": {
        "type": "document", "space": {"id": SALES_SPACE, "name": "Sales"},
        "document": {"id": ACCOUNTS, "title": "Accounts 2026", "file_name": "accounts.md"}, "chunk": 0,
        "excerpt": "Orion Foods is our largest customer in the north."}},
]
BRAIN_TYPES = [".docx", ".markdown", ".md", ".txt"]
# S5's ingest: what a Row-Bot may report, and how much at once.
DEVICE_ACTIONS = frozenset({"tool.called", "data.exported", "device.locked_down"})
MAX_AUDIT_BATCH = 100
MAX_AUDIT_BYTES = 256 * 1024


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


def _form(request: httpx.Request) -> dict[str, tuple[str | None, bytes]]:
    """A ``multipart/form-data`` body's fields, as httpx writes one: name to (file name or None, bytes)."""
    boundary = request.headers["content-type"].split("boundary=", 1)[1].strip('"').encode("latin-1")
    fields: dict[str, tuple[str | None, bytes]] = {}
    for part in request.read().split(b"--" + boundary)[1:-1]:
        head, _, value = part[2:].partition(b"\r\n\r\n")
        disposition = head.split(b"\r\n")[0].decode("utf-8")
        name = re.search(r'\bname="([^"]*)"', disposition)
        file_name = re.search(r'\bfilename="([^"]*)"', disposition)
        if name:
            fields[name.group(1)] = (file_name.group(1) if file_name else None, value[:-2])
    return fields


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
        # S5's audit ingest: each batch's body as sent, the events it stored (contract JSON, by id), and the
        # answers to give the next batches instead of storing them.
        self.audit_batches: list[bytes] = []
        self.audit: dict[str, dict] = {}
        self.audit_answers: list[httpx.Response] = []
        # The background audit sends the app started (held: a test fires them).
        self.timers: list[HeldTimer] = []
        # The company brain (B1a's spaces, B7's graph and entities), read as the fixture person; None: a server
        # without it (every brain route 404s). Entity answers are B7's, by id; one missing is 404.
        self.brain_spaces: list[dict] | None = [dict(space) for space in BRAIN_SPACES]
        self.brain_nodes: list[dict] = [dict(node) for node in BRAIN_NODES]
        self.brain_edges: list[dict] = [dict(edge) for edge in BRAIN_EDGES]
        self.brain_entities: dict[str, dict] = {}
        # B1a's recall: its hits (each with its citation), whether it searched by words only, each body asked,
        # and an event a recall waits for (a slow server) while set.
        self.brain_hits: list[dict] = [json.loads(json.dumps(hit)) for hit in BRAIN_HITS]
        self.recall_search = "hybrid"
        self.recall_bodies: list[dict] = []
        self.recall_hold: threading.Event | None = None
        # B1a's documents (newest first), each upload or replace as it came ({"method", "path", "fields"}: field
        # name to (file name, bytes)), the upload limit, and answers to give a write instead: (method, response).
        self.brain_documents: list[dict] = [dict(document) for document in BRAIN_DOCUMENTS]
        self.uploads: list[dict] = []
        self.max_upload_bytes = 25 * 1024 * 1024
        self.brain_answers: list[tuple[str, httpx.Response]] = []
        # B4a's review queue: the person's shares (newest first, B4a's Submission shape), each share's body as
        # sent, and how many they shared today (B4a's daily count: withdrawn ones count too).
        self.submissions: list[dict] = []
        self.submission_bodies: list[dict] = []
        self.shares_today = 0
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
        if (request.method, path) == ("POST", AUDIT_INGEST):
            return self._ingest(request)
        if path.startswith("/api/brain/") and self.brain_spaces is not None:
            if self._bearer(request) is None:
                return httpx.Response(401, json={"error": "invalid_token"})
            answer = next((found for found in self.brain_answers if found[0] == request.method), None)
            if answer is not None:
                self.brain_answers.remove(answer)
                return answer[1]
            return self._brain(request)
        return httpx.Response(404, json={"error": "not_found"})

    def _brain(self, request: httpx.Request) -> httpx.Response:
        """B1a's spaces, recall and documents, and B7's ``GET /api/brain/graph`` and ``/api/brain/entities/{id}``."""
        path, params = request.url.path, request.url.params
        readable = {space["id"]: space for space in self.brain_spaces or []}
        if (request.method, path) == ("POST", "/api/brain/recall"):
            return self._recall(request, readable)
        parts = path.split("/")
        if len(parts) == 6 and path.startswith("/api/brain/spaces/") and parts[5] == "documents":
            return self._space_documents(request, readable, parts[4])
        if len(parts) == 5 and path.startswith("/api/brain/documents/"):
            return self._document(request, readable, parts[4])
        if len(parts) == 6 and path.startswith("/api/brain/spaces/") and parts[5] == "submissions":
            return self._submit(request, readable, parts[4])
        if (request.method, len(parts), parts[3], parts[-1]) == ("POST", 6, "submissions", "withdraw"):
            return self._withdraw(parts[4])
        if (request.method, path) == ("GET", "/api/brain/submissions"):
            if params.get("mine") != "1" or params.get("status", "all") not in ("all", "pending", "approved",
                                                                                 "rejected"):
                return httpx.Response(422, json={"detail": [{"loc": ["query"], "msg": "invalid"}]})
            status = params.get("status", "all")
            found = [share for share in self.submissions if status in ("all", share["status"])]
            return httpx.Response(200, json={"submissions": found[:200], "truncated": len(found) > 200})
        if request.method != "GET":
            return httpx.Response(404, json={"error": "not_found"})
        if path == "/api/brain/spaces":
            return httpx.Response(200, json={"spaces": list(readable.values())})
        if path == "/api/brain/graph":
            asked = [space for space in params.get("spaces", "").split(",") if space]
            if any(space not in readable for space in asked):
                return httpx.Response(404, json={"error": "space_not_found"})
            spaces = set(asked or readable)
            needle = params.get("q", "").casefold()
            limit = int(params.get("limit", "300"))
            found = [node for node in self.brain_nodes if node["space_id"] in spaces and (
                not needle or any(needle in name.casefold() for name in [node["subject"], *node["aliases"]]))]
            found.sort(key=lambda node: (-node["relation_count"], node["subject"], node["id"]))
            shown = found[:limit]
            ids = {node["id"] for node in shown}
            every = {node["id"] for node in found}
            edges = [edge for edge in self.brain_edges if edge["source_id"] in ids and edge["target_id"] in ids]
            total_relations = sum(1 for edge in self.brain_edges
                                  if edge["source_id"] in every and edge["target_id"] in every)
            return httpx.Response(200, json={
                "nodes": shown, "edges": edges,
                "spaces": [{key: readable[space][key] for key in ("id", "name", "kind")}
                           for space in dict.fromkeys(node["space_id"] for node in shown)],
                "total_entities": len(found), "total_relations": total_relations,
                "shown_entities": len(shown), "shown_relations": len(edges),
                "truncated": len(shown) < len(found) or len(edges) < total_relations,
                "entity_types": sorted({node["entity_type"] for node in shown})})
        if path.startswith("/api/brain/entities/"):
            answer = self.brain_entities.get(path.removeprefix("/api/brain/entities/"))
            if answer is None:
                return httpx.Response(404, json={"error": "entity_not_found"})
            return httpx.Response(200, json=answer)
        return httpx.Response(404, json={"error": "not_found"})

    def _recall(self, request: httpx.Request, readable: dict[str, dict]) -> httpx.Response:
        """B1a's ``POST /api/brain/recall``: the hits in the spaces asked for (none: every readable one), ``k`` at
        most."""
        body = json.loads(request.content or b"{}")
        self.recall_bodies.append(body)
        if self.recall_hold is not None:
            self.recall_hold.wait(5)
        query, spaces, k = body.get("query"), body.get("spaces"), body.get("k", 8)
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 1000 or not 1 <= k <= 50:
            return httpx.Response(422, json={"error": "invalid_query"})
        if spaces is not None and any(space not in readable for space in spaces):
            return httpx.Response(404, json={"error": "space_not_found"})
        hits = [hit for hit in self.brain_hits if hit["citation"]["space"]["id"] in (spaces or readable)]
        return httpx.Response(200, json={"search": self.recall_search, "hits": hits[:k]})

    def _space_documents(self, request: httpx.Request, readable: dict[str, dict], space_id: str) -> httpx.Response:
        """B1a's ``GET`` and ``POST /api/brain/spaces/{id}/documents`` (readers list them, owners upload)."""
        space = readable.get(space_id)
        if space is None:
            return httpx.Response(404, json={"error": "space_not_found"})
        if request.method == "GET":
            return httpx.Response(200, json={"documents": [
                document for document in self.brain_documents if document["space_id"] == space_id]})
        if request.method != "POST":
            return httpx.Response(405, json={"detail": "Method Not Allowed"})
        if not space["is_owner"]:
            return httpx.Response(403, json={"error": "space_owner_required"})
        return self._upload(request, space_id, None)

    def _document(self, request: httpx.Request, readable: dict[str, dict], document_id: str) -> httpx.Response:
        """B1a's ``GET``, ``PUT`` and ``DELETE /api/brain/documents/{id}`` (readers read it, owners change it)."""
        document = next((found for found in self.brain_documents
                         if found["id"] == document_id and found["space_id"] in readable), None)
        if document is None:
            return httpx.Response(404, json={"error": "document_not_found"})
        if request.method == "GET":
            return httpx.Response(200, json=document)
        if not readable[document["space_id"]]["is_owner"]:
            return httpx.Response(403, json={"error": "space_owner_required"})
        if request.method == "DELETE":
            self.brain_documents.remove(document)
            return httpx.Response(204)
        if request.method != "PUT":
            return httpx.Response(405, json={"detail": "Method Not Allowed"})
        return self._upload(request, document["space_id"], document)

    def _upload(self, request: httpx.Request, space_id: str, document: dict | None) -> httpx.Response:
        """An upload, or a replace of ``document``: B1a's checks, then the document, queued."""
        fields = _form(request)
        self.uploads.append({"method": request.method, "path": request.url.path, "fields": fields})
        name, content = fields.get("file", (None, b""))
        if not name:
            return httpx.Response(400, json={"error": "file_required"})
        if "." + name.rpartition(".")[2].lower() not in BRAIN_TYPES:
            return httpx.Response(415, json={"error": "unsupported_type", "supported": BRAIN_TYPES})
        if len(content) > self.max_upload_bytes:
            return httpx.Response(413, json={"error": "file_too_large", "max_bytes": self.max_upload_bytes})
        written = {**_DOCUMENT, "space_id": space_id, "file_name": name, "byte_count": len(content),
                   "sha256": hashlib.sha256(content).hexdigest(), "status": "queued", "processed_at": None,
                   "chunk_count": 0, "graph_status": "pending", "uploaded_at": "2026-11-24T09:00:00+00:00"}
        if document is not None:
            document.update(written, version=document["version"] + 1)
            return httpx.Response(200, json=document)
        created = {**written, "id": str(uuid.uuid4()), "title": name.rpartition(".")[0]}
        self.brain_documents.insert(0, created)
        return httpx.Response(201, json=created)

    def _submit(self, request: httpx.Request, readable: dict[str, dict], space_id: str) -> httpx.Response:
        """B4a's ``POST /api/brain/spaces/{id}/submissions``: the body checked first (FastAPI's 422), then the
        space, the policy, the person's waiting shares and today's."""
        if request.method != "POST":
            return httpx.Response(405, json={"detail": "Method Not Allowed"})
        body = json.loads(request.content or b"{}")
        self.submission_bodies.append(body)
        title, text, note = body.get("title"), body.get("text"), body.get("source_note")
        if (set(body) - {"title", "text", "source_note"} or not isinstance(title, str) or not 1 <= len(title) <= 200
                or "\n" in title or not isinstance(text, str) or not 1 <= len(text) <= 8000
                or not (note is None or (isinstance(note, str) and len(note) <= 500 and "\n" not in note))):
            return httpx.Response(422, json={"detail": [{"loc": ["body"], "msg": "invalid"}]})
        space = readable.get(space_id)
        if space is None:
            return httpx.Response(404, json={"error": "space_not_found"})
        if self.policy["data"]["share_to_company"] is not True:
            return httpx.Response(403, json={"error": "share_to_company_off"})
        if sum(1 for share in self.submissions if share["status"] == "pending") >= 20:
            return httpx.Response(429, json={"error": "too_many_pending", "max_pending": 20})
        if self.shares_today >= 50:
            return httpx.Response(429, json={"error": "too_many_shares_today", "max_per_day": 50})
        self.shares_today += 1
        created = {"id": str(uuid.uuid4()), "kind": "share", "space_id": space_id, "space_name": space["name"],
                   "sent_space_id": space_id, "title": title, "text": text, "source_note": note or None,
                   "submitted_by": PERSON["id"], "submitted_at": "2026-11-24T10:00:00+00:00", "status": "pending",
                   "reviewed_by": None, "decided_at": None, "reason": None, "fact_id": None}
        self.submissions.insert(0, created)
        return httpx.Response(201, json=created)

    def _withdraw(self, share_id: str) -> httpx.Response:
        """B4a's ``POST /api/brain/submissions/{id}/withdraw``: one's own waiting share is deleted (204)."""
        share = next((found for found in self.submissions if found["id"] == share_id), None)
        if share is None:
            return httpx.Response(404, json={"error": "submission_not_found"})
        if share["status"] != "pending":
            return httpx.Response(409, json={"error": "submission_decided", "status": share["status"]})
        self.submissions.remove(share)
        return httpx.Response(204)

    def _ingest(self, request: httpx.Request) -> httpx.Response:
        """S5's ``POST /api/audit/events`` for a device's bearer: the contract checked event by event, all or
        nothing, an id already stored skipped."""
        token = self._bearer(request)
        if token is None:
            return httpx.Response(401, json={"error": "invalid_token"})
        body = request.content
        self.audit_batches.append(body)
        if self.audit_answers:
            return self.audit_answers.pop(0)
        if len(body) > MAX_AUDIT_BYTES:
            return httpx.Response(413, json={"error": "batch_too_large"})
        raw = json.loads(body)
        events = raw.get("events") if isinstance(raw, dict) and raw.keys() == {"events"} else None
        if not isinstance(events, list) or not events:
            return httpx.Response(422, json={"error": "invalid_batch"})
        if len(events) > MAX_AUDIT_BATCH:
            return httpx.Response(413, json={"error": "batch_too_large"})
        device = self.access[token]
        checked = []
        for index, data in enumerate(events):
            try:
                event = from_dict(data)
            except InvalidAuditEvent as error:
                return httpx.Response(422, json={"error": "invalid_event", "index": index, "detail": str(error)})
            if event.action.value not in DEVICE_ACTIONS or (
                    event.action.value == "data.exported" and event.details.get("scope") != "person"):
                return httpx.Response(422, json={"error": "action_not_accepted", "index": index})
            if event.device_id != device or (event.action.value == "device.locked_down"
                                             and event.target.ref != device):
                return httpx.Response(422, json={"error": "device_mismatch", "index": index})
            checked.append(event)
        fresh = [event for event in checked if event.id not in self.audit]
        for event in fresh:
            self.audit[event.id] = to_dict(event)
        return httpx.Response(200, json={"accepted": len(fresh), "duplicates": len(checked) - len(fresh)})

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


class HeldTimer:
    """The audit outbox's background send (``audit.Timer``), held: made and started, but run only when a test
    fires it, so no send ever runs on its own in a test."""

    def __init__(self, interval: float, function: Callable[[], None], made: list[HeldTimer]) -> None:
        self.interval = interval
        self.function = function
        self.daemon = False
        self.started = self.cancelled = False
        made.append(self)

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        assert self.started and not self.cancelled, "this send was never started, or was cancelled"
        self.cancelled = True
        self.function()


@pytest.fixture
def keychain() -> Iterator[MemoryKeyring]:
    found = MemoryKeyring()
    secret_store._set_backend_for_tests(found)
    yield found
    secret_store._set_backend_for_tests(None)


@pytest.fixture
def company_state(monkeypatch) -> list[HeldTimer]:
    """No Join, outcome or notice left from another test (they live in the app's process); the audit outbox's
    background sends held (the list of them)."""
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
    # No audit outbox remembered, no send waiting or backing off, and none started for real (C4).
    for name, value in {"_memo": None, "_timer": None, "_last_attempt": None, "_retry_at": 0.0, "_failures": 0,
                        "_dropped": 0}.items():
        monkeypatch.setattr(audit, name, value)
    # No company server set by IT (C5): this computer's registry, managed preferences and /etc are never read.
    monkeypatch.setattr(managed, "_read", lambda: None)
    monkeypatch.setattr(managed, "_logged", set())
    timers: list[HeldTimer] = []
    monkeypatch.setattr(audit, "Timer", lambda interval, function: HeldTimer(interval, function, timers))
    return timers


@pytest.fixture
def company_server(tmp_path, monkeypatch, keychain, company_state) -> Iterator[FakeCompanyServer]:
    """A scratch data folder, the in-memory keychain, the fake server and browser, and no join left over."""
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    server = FakeCompanyServer()
    server.timers = company_state
    monkeypatch.setattr(client, "transport", server.transport)
    monkeypatch.setattr(sign_in, "open_browser", server.browse)
    yield server
    client_company.cancel()
    provider.settle()  # A background catalog read ends here, while the fake server still answers it.
    policy.settle()  # And a background policy refresh.
    audit.stop()  # And an audit send.
    registry = sys.modules.get("row_bot.tools.registry")
    if registry is not None:
        registry.unregister("company_brain")  # The company brain's tool (B3) goes with the test's company.
