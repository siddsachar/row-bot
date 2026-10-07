"""A fake Ollama on loopback that streams one NDJSON chunk per token, as the real one does.

    with fake_streaming_ollama(tokens=["Hello", " world"]) as server:
        httpx.post(f"{server.url}/api/chat", json={...})

Serves ``/api/chat`` and ``/api/generate``; ``/api/tags`` lists ``server.model``. Each token is written and
flushed as its own chunk, so a client reading the stream sees hundreds of small chunks for a long answer.
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


@contextmanager
def fake_streaming_ollama(*, tokens: list[str], model: str = "fake-local:7b") -> Iterator[FakeOllama]:
    state = FakeOllama(url="", model=model, tokens=list(tokens))

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
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            def chunk(payload: dict) -> None:
                line = json.dumps(payload).encode() + b"\n"
                self.wfile.write(f"{len(line):x}\r\n".encode() + line + b"\r\n")
                self.wfile.flush()

            for token in state.tokens:
                part = {"message": {"role": "assistant", "content": token}} if chat else {"response": token}
                chunk({"model": state.model, "done": False, **part})
            last = {"message": {"role": "assistant", "content": ""}} if chat else {"response": ""}
            chunk({"model": state.model, "done": True, "done_reason": "stop", **last})
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
