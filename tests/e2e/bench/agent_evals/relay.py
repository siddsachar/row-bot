"""A host-side relay to OpenRouter for agents that run inside task containers (Harbor, PawBench).

Terminal-Bench tasks run arbitrary code, so the OpenRouter key never enters a task container. The relay
holds it on the host and the container gets a **relay token** instead:

- It listens on ``127.0.0.1`` only, on a free port the OS picks (never one of the ports the machine
  reserves). Docker Desktop reaches it from a container as ``host.docker.internal``.
- It answers only ``POST /api/v1/chat/completions``, only with the run's token, and only for the run's
  pinned models. It overwrites the body's ``provider`` with that model's pin (no fallbacks), puts the real
  key on the way out, and streams the answer back as it comes.
- It holds a spend cap: OpenRouter's own cost of each answer (its ``usage.cost``) is added up, and once the
  cap is reached every further request is refused. It counts requests per minute and per day too, to pace a
  free model under OpenRouter's limits.
- Inside the container the token is given to Row-Bot as ``OPENROUTER_API_KEY``. Row-Bot never passes a saved
  or environment key to a shell command or child program it starts (``api_keys.child_environment``), so task
  code doesn't see even the token.

Standard library and ``httpx`` only.
"""
from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

UPSTREAM = "https://openrouter.ai"
PATH = "/api/v1/chat/completions"
MAX_BODY = 32 * 1024 * 1024


@dataclass
class RelayPolicy:
    pins: dict[str, dict[str, Any]]
    max_usd: float = 0.0
    per_minute: int = 0  # 0: no pacing
    per_day: int = 0
    spent_usd: float = 0.0
    requests: int = 0
    refused: list[str] = field(default_factory=list)
    _minute: deque = field(default_factory=deque)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def admit(self, model: str, now: float) -> str:
        """'' when a request for *model* may go now, else why not."""
        with self._lock:
            if model not in self.pins:
                return f"model {model!r} is not pinned for this run"
            if self.max_usd and self.spent_usd >= self.max_usd:
                return f"the run's spend cap (${self.max_usd:.2f}) is reached"
            if self.per_day and self.requests >= self.per_day:
                return f"the day's request allowance ({self.per_day}) is used"
            while self._minute and now - self._minute[0] > 60:
                self._minute.popleft()
            if self.per_minute and len(self._minute) >= self.per_minute:
                return "wait: the minute's request allowance is used"
            self._minute.append(now)
            self.requests += 1
            return ""

    def spend(self, usd: float) -> None:
        with self._lock:
            self.spent_usd += max(0.0, float(usd or 0.0))


def cost_in(data: bytes) -> float:
    """OpenRouter's ``usage.cost`` in a JSON answer or in the SSE lines of a streamed one."""
    total = 0.0
    for line in data.decode("utf-8", "replace").splitlines():
        text = line[5:].strip() if line.startswith("data:") else line.strip()
        if not text.startswith("{"):
            continue
        try:
            usage = (json.loads(text).get("usage") or {})
        except ValueError:
            continue
        total += float(usage.get("cost") or 0.0)
    return total


def make_handler(policy: RelayPolicy, token: str, key: str, *, send: Callable[..., Any] | None = None,
                 clock: Callable[[], float] = time.time) -> type:
    class Relay(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:  # never log requests (they carry prompts)
            return

        def _refuse(self, status: int, why: str) -> None:
            policy.refused.append(why)
            body = json.dumps({"error": {"message": f"bench relay: {why}", "code": status}}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - http.server's name
            self._refuse(404, "only chat completions are relayed")

        def do_POST(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] != PATH:
                return self._refuse(404, "only chat completions are relayed")
            offered = str(self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(offered, token):
                return self._refuse(401, "wrong relay token")
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._refuse(413, "no body, or too large")
            try:
                body = json.loads(self.rfile.read(length))
            except ValueError:
                return self._refuse(400, "not JSON")
            model = str(body.get("model") or "")
            why = policy.admit(model, clock())
            if why:
                return self._refuse(429 if why.startswith("wait") else 403, why)
            body["provider"] = policy.pins[model]
            outbound = send or _send
            status, headers, chunks = outbound(json.dumps(body).encode("utf-8"), key)
            self.send_response(status)
            for name, value in headers:
                if name.lower() in {"content-type", "cache-control"}:
                    self.send_header(name, value)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            seen = bytearray()
            for chunk in chunks:
                seen.extend(chunk)
                self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii") + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            policy.spend(cost_in(bytes(seen)))

    return Relay


def _send(body: bytes, key: str) -> tuple[int, list[tuple[str, str]], Any]:
    import httpx

    client = httpx.Client(timeout=httpx.Timeout(600, connect=30))
    request = client.build_request("POST", UPSTREAM + PATH, content=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    response = client.send(request, stream=True)

    def chunks() -> Any:
        try:
            yield from response.iter_bytes()
        finally:
            response.close()
            client.close()

    return response.status_code, list(response.headers.items()), chunks()


class RelayServer:
    """The relay on ``127.0.0.1`` and a free port, in a background thread; ``close()`` stops it."""

    def __init__(self, policy: RelayPolicy, key: str, *, send: Callable[..., Any] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.policy = policy
        self.token = f"relay-{secrets.token_urlsafe(24)}"
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(policy, self.token, key, send=send,
                                                                            clock=clock))
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, name="bench-relay", daemon=True)
        self._thread.start()

    def base_url(self, host: str = "127.0.0.1") -> str:
        return f"http://{host}:{self.port}/api/v1"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
