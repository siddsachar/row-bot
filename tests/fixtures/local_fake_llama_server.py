"""A stand-in for ``llama-server`` serving one model (standard library only), for Run on this computer.

It speaks the few endpoints Row-Bot uses:

- ``GET /health`` (no key, like the real one);
- ``GET /slots``: whether a request is being answered (``POST /__busy`` sets it);
- ``GET /v1/models``: the one model, by its ``--alias``;
- ``POST /v1/chat/completions``: answers "ready" (streamed when asked);
- ``POST /__crash``: the process ends at once.

Every endpoint but ``/health`` needs ``Authorization: Bearer <LLAMA_API_KEY>`` from its environment. It
starts a child process of its own (a model worker stand-in) so tests can check the whole tree ends.
``--version`` prints a version line like the real binary. ``FAKE_LLAMA_RECORD`` gets its argv, the names
of its environment variables and the address it listens on.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _arg(argv: list[str], name: str, default: str = "") -> str:
    return argv[argv.index(name) + 1] if name in argv else default


def main() -> int:
    argv = sys.argv[1:]
    if "--version" in argv:
        print("version: 12359 (b1b770d9)", file=sys.stderr)
        return 0
    host, port = _arg(argv, "--host", "0.0.0.0"), int(_arg(argv, "--port", "0"))
    alias = _arg(argv, "--alias", "model")
    key = os.environ.get("LLAMA_API_KEY", "")
    state = {"busy": False}
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    folder = os.environ.get("FAKE_LLAMA_CHILDREN", "")
    if folder:
        with open(os.path.join(folder, "child.pid"), "w", encoding="utf-8") as handle:
            handle.write(str(child.pid))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: object) -> None:
            return

        def _send(self, status: int, payload: object, content_type: str = "application/json") -> None:
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self) -> bool:
            if self.headers.get("Authorization") == f"Bearer {key}" and key:
                return True
            self._send(401, {"error": {"message": "Invalid API Key"}})
            return False

        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/health") or self.path.startswith("/v1/health"):
                self._send(200, {"status": "ok"})
            elif not self._authorised():
                return
            elif self.path.startswith("/slots"):
                self._send(200, [{"id": 0, "is_processing": state["busy"]}])
            elif self.path.startswith("/v1/models") or self.path.startswith("/models"):
                self._send(200, {"object": "list", "data": [{"id": alias, "object": "model"}]})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            if self.path == "/__crash":
                os._exit(3)
            if not self._authorised():
                return
            if self.path == "/__busy":
                state["busy"] = bool(body.get("busy"))
                self._send(200, {"busy": state["busy"]})
            elif self.path.startswith("/v1/chat/completions"):
                if body.get("stream"):
                    chunks = [{"choices": [{"index": 0, "delta": {"role": "assistant", "content": "ready"}}]},
                              {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
                    data = b"".join(b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in chunks)
                    self._send(200, data + b"data: [DONE]\n\n", "text/event-stream")
                else:
                    self._send(200, {"id": "x", "object": "chat.completion", "model": alias, "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": "ready"}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}})
            else:
                self._send(404, {"error": "not found"})

    server = ThreadingHTTPServer((host, port), Handler)
    record = os.environ.get("FAKE_LLAMA_RECORD", "")
    if record:
        with open(record, "w", encoding="utf-8") as handle:
            json.dump({"argv": argv, "env_keys": sorted(os.environ), "pid": os.getpid(),
                       "address": list(server.server_address)[:2]}, handle)
    try:
        server.serve_forever()
    finally:
        child.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
