"""A stand-in for ``llama-server`` in its router mode (standard library only), for Run on this computer.

It speaks the few endpoints Row-Bot uses:

- ``GET /health`` (no key, like the real one);
- ``GET /models`` and ``/v1/models``: every preset with its status (unloaded, loaded);
- ``GET /slots?model=<id>``: whether a request is being answered (``POST /__busy`` sets it);
- ``POST /v1/chat/completions``: loads the named model (a child process, as the real router starts one
  per model) and answers "ready" (streamed when asked);
- ``POST /models/unload``;
- ``POST /__crash``: the process ends at once.

Every endpoint but ``/health`` needs ``Authorization: Bearer <LLAMA_API_KEY>`` from its environment.
``--version`` prints a version line like the real binary. ``FAKE_LLAMA_RECORD`` gets its argv, the names
of its environment variables and the address it listens on; ``FAKE_LLAMA_CHILDREN`` a ``<model>.pid``
file per loaded model.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def _arg(argv: list[str], name: str, default: str = "") -> str:
    return argv[argv.index(name) + 1] if name in argv else default


def _presets(path: str) -> list[str]:
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return []
    return [line.strip()[1:-1] for line in lines
            if line.strip().startswith("[") and line.strip().endswith("]") and line.strip() != "[*]"]


def main() -> int:
    argv = sys.argv[1:]
    if "--version" in argv:
        print("version: 12359 (b1b770d9)", file=sys.stderr)
        return 0
    host, port = _arg(argv, "--host", "0.0.0.0"), int(_arg(argv, "--port", "0"))
    key = os.environ.get("LLAMA_API_KEY", "")
    lock = threading.Lock()
    status = {name: "unloaded" for name in _presets(_arg(argv, "--models-preset"))}
    children: dict[str, subprocess.Popen] = {}
    busy = {"value": False}

    def load(name: str) -> None:
        if name in children:
            return
        for other in list(children):  # --models-max 1
            children.pop(other).kill()
            status[other] = "unloaded"
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        children[name] = child
        status[name] = "loaded"
        folder = os.environ.get("FAKE_LLAMA_CHILDREN", "")
        if folder:
            with open(os.path.join(folder, f"{name}.pid"), "w", encoding="utf-8") as handle:
                handle.write(str(child.pid))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: object) -> None:
            return

        def _send(self, code: int, payload: object, content_type: str = "application/json") -> None:
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self) -> bool:
            if key and self.headers.get("Authorization") == f"Bearer {key}":
                return True
            self._send(401, {"error": {"message": "Invalid API Key"}})
            return False

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path in ("/health", "/v1/health"):
                self._send(200, {"status": "ok"})
            elif not self._authorised():
                return
            elif path in ("/models", "/v1/models"):
                with lock:
                    data = [{"id": name, "object": "model", "status": {"value": value}}
                            for name, value in status.items()]
                self._send(200, {"object": "list", "data": data})
            elif path == "/slots":
                model = (parse_qs(urlsplit(self.path).query).get("model") or [""])[0]
                if model not in children:
                    self._send(400, {"error": "model is not loaded"})
                else:
                    self._send(200, [{"id": 0, "is_processing": busy["value"]}])
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
                busy["value"] = bool(body.get("busy"))
                self._send(200, {"busy": busy["value"]})
            elif self.path == "/models/unload":
                with lock:
                    child = children.pop(str(body.get("model") or ""), None)
                    if child is not None:
                        child.kill()
                        status[str(body.get("model"))] = "unloaded"
                self._send(200, {"success": True})
            elif self.path.startswith("/v1/chat/completions"):
                name = str(body.get("model") or "")
                if name not in status:
                    self._send(400, {"error": {"message": f"model {name} not found"}})
                    return
                with lock:
                    load(name)
                if body.get("stream"):
                    chunks = [{"choices": [{"index": 0, "delta": {"role": "assistant", "content": "ready"}}]},
                              {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
                    data = b"".join(b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in chunks)
                    self._send(200, data + b"data: [DONE]\n\n", "text/event-stream")
                else:
                    self._send(200, {"id": "x", "object": "chat.completion", "model": name, "choices": [
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
        for child in children.values():
            child.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
