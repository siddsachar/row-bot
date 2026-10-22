"""A fake Ollama on loopback that streams one NDJSON chunk per token, as the real one does.

    with fake_streaming_ollama(tokens=["Hello", " world"]) as server:
        httpx.post(f"{server.url}/api/chat", json={...})

Serves ``/api/chat`` and ``/api/generate``; ``/api/tags`` lists ``server.model``. Each token is written and
flushed as its own chunk, so a client reading the stream sees hundreds of small chunks for a long answer.
``tool_calls`` (Ollama's wire form) are sent in one chat chunk before the last, as the real server does.

``replies`` scripts one chat reply per request, in order (the last repeats): each a dict with ``tokens``,
``tool_calls``, ``eval_count`` and ``prompt_eval_count`` (reported on the last chunk), or ``status`` and
``error`` for a failed request, which the real server answers with that status and ``{"error": ...}``, or
``stream_error`` for an error the real server sends inside the stream after what it already sent.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class FakeOllama:
    url: str
    model: str
    tokens: list[str]
    requests: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    replies: list[dict] = field(default_factory=list)

    def chat_requests(self) -> list[dict]:
        return [request for request in self.requests if request["path"] == "/api/chat"]


@contextmanager
def fake_streaming_ollama(
    *,
    tokens: list[str],
    model: str = "fake-local:7b",
    tool_calls: list[dict] | None = None,
    replies: list[dict] | None = None,
) -> Iterator[FakeOllama]:
    state = FakeOllama(url="", model=model, tokens=list(tokens), tool_calls=list(tool_calls or ()),
                       replies=list(replies or ()))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args) -> None:
            pass

        def _json(self, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/api/tags":
                self._json({"models": [{"name": state.model, "model": state.model}]})
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            request = json.loads(self.rfile.read(length) or b"{}")
            state.requests.append({"path": self.path, **request})
            if self.path not in {"/api/chat", "/api/generate"}:
                self.send_error(404)
                return
            chat = self.path == "/api/chat"
            reply = {"tokens": state.tokens, "tool_calls": state.tool_calls}
            if chat and state.replies:
                reply = state.replies[min(len(state.chat_requests()) - 1, len(state.replies) - 1)]
            if reply.get("status"):
                body = json.dumps({"error": reply.get("error", "")}).encode()
                self.send_response(int(reply["status"]))
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            def chunk(payload: dict) -> None:
                line = json.dumps(payload).encode() + b"\n"
                self.wfile.write(f"{len(line):x}\r\n".encode() + line + b"\r\n")
                self.wfile.flush()

            for token in reply.get("tokens") or ():
                part = {"message": {"role": "assistant", "content": token}} if chat else {"response": token}
                chunk({"model": state.model, "done": False, **part})
            if chat and reply.get("tool_calls"):
                calls = {"role": "assistant", "content": "", "tool_calls": reply["tool_calls"]}
                chunk({"model": state.model, "done": False, "message": calls})
            if reply.get("stream_error"):
                chunk({"error": reply["stream_error"]})
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
                return
            last = {"message": {"role": "assistant", "content": ""}} if chat else {"response": ""}
            counts = {key: reply[key] for key in ("eval_count", "prompt_eval_count") if key in reply}
            chunk({"model": state.model, "done": True, "done_reason": "stop", **last, **counts})
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{server.server_address[1]}"
    # A short poll, so shutting the fake down doesn't add half a second to every test.
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
