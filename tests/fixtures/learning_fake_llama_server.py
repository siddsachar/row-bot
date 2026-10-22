"""A stand-in for ``llama-server`` in its router mode (standard library only), for Row-Bot's managed
model server tests (L2x, O75). It speaks the few endpoints Row-Bot uses:

- ``GET /health`` (no key, like the real one);
- ``GET /models`` and ``/v1/models`` (``?reload=1`` reads the presets again): every preset with its
  status (unloaded, loaded, sleeping);
- ``POST /models/load`` and ``/models/unload``;
- ``POST /v1/chat/completions``: loads the named model (autoload) and answers "ok";
- ``POST /__crash``: the process ends at once (a crash); ``POST /__sleep``: a loaded model sleeps, as
  the real one's do after ``--sleep-idle-seconds`` unused.

Every endpoint but ``/health`` needs ``Authorization: Bearer <LLAMA_API_KEY>`` from its environment. A
loaded model is a child process of its own (``sleep``), as the real router starts one per model, so
tests can check the whole tree ends. ``--version`` prints the release like the real binary.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def _arg(argv: list[str], name: str, default: str = "") -> str:
    return argv[argv.index(name) + 1] if name in argv else default


def _presets(path: str) -> list[str]:
    names = []
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return names
    for line in lines:
        line = line.strip()
        if line.startswith("[") and line.endswith("]") and line != "[*]":
            names.append(line[1:-1])
    return names


def main() -> int:
    argv = sys.argv[1:]
    if "--version" in argv:
        print("version: 11669 (620d2225c)")
        return 0
    host, port = _arg(argv, "--host", "127.0.0.1"), int(_arg(argv, "--port", "0"))
    presets = _arg(argv, "--models-preset")
    sleep_after = float(_arg(argv, "--sleep-idle-seconds", "-1"))
    key = os.environ.get("LLAMA_API_KEY", "")
    record = os.environ.get("FAKE_LLAMA_RECORD", "")
    lock = threading.Lock()
    status: dict[str, str] = {name: "unloaded" for name in _presets(presets)}
    used: dict[str, float] = {}
    children: dict[str, subprocess.Popen] = {}

    if record:
        with open(record, "w", encoding="utf-8") as handle:
            json.dump({"argv": argv, "env_keys": sorted(os.environ), "pid": os.getpid()}, handle)

    def load(name: str) -> None:
        if status.get(name) in ("loaded", "sleeping") and name in children:
            status[name] = "loaded"
            used[name] = time.monotonic()
            return
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        children[name] = child
        status[name] = "loaded"
        used[name] = time.monotonic()
        folder = os.environ.get("FAKE_LLAMA_CHILDREN", "")
        if folder:
            with open(os.path.join(folder, f"{name}.pid"), "w", encoding="utf-8") as handle:
                handle.write(str(child.pid))

    def unload(name: str) -> None:
        child = children.pop(name, None)
        if child is not None:
            child.kill()
            child.wait()
        if name in status:
            status[name] = "unloaded"

    def sleeper() -> None:
        while sleep_after > 0:
            time.sleep(0.05)
            with lock:
                for name, value in list(status.items()):
                    if value == "loaded" and time.monotonic() - used.get(name, 0) >= sleep_after:
                        status[name] = "sleeping"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # quiet
            pass

        def _send(self, code: int, body: object) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorised(self) -> bool:
            if self.headers.get("Authorization", "") == f"Bearer {key}" and key:
                return True
            self._send(401, {"error": {"message": "Invalid API Key"}})
            return False

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}") if length else {}

        def do_GET(self) -> None:  # noqa: N802
            parts = urlsplit(self.path)
            if parts.path == "/health":
                self._send(200, {"status": "ok"})
                return
            if not self._authorised():
                return
            if parts.path in ("/models", "/v1/models"):
                with lock:
                    if parse_qs(parts.query).get("reload") == ["1"]:
                        for name in _presets(presets):
                            status.setdefault(name, "unloaded")
                        for name in [item for item in status if item not in _presets(presets)]:
                            unload(name)
                            status.pop(name, None)
                    data = [{"id": name, "object": "model", "status": {"value": value}}
                            for name, value in sorted(status.items())]
                self._send(200, {"object": "list", "data": data})
                return
            self._send(404, {"error": {"message": "not found"}})

        def do_POST(self) -> None:  # noqa: N802
            parts = urlsplit(self.path)
            if parts.path == "/__crash":
                os._exit(3)
            if parts.path == "/__sleep":  # what the real one does after --sleep-idle-seconds unused
                with lock:
                    name = str(self._body().get("model") or "")
                    if status.get(name) == "loaded":
                        status[name] = "sleeping"
                self._send(200, {"success": True})
                return
            if not self._authorised():
                return
            body = self._body()
            name = str(body.get("model") or "")
            with lock:
                if name not in status:
                    self._send(404, {"error": {"message": f"model {name} not found"}})
                    return
                if parts.path == "/models/load":
                    load(name)
                    self._send(200, {"success": True})
                    return
                if parts.path == "/models/unload":
                    unload(name)
                    self._send(200, {"success": True})
                    return
                if parts.path == "/v1/chat/completions":
                    load(name)
                    self._send(200, {"object": "chat.completion", "model": name,
                                     "choices": [{"index": 0, "finish_reason": "stop",
                                                  "message": {"role": "assistant", "content": "ok"}}],
                                     "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}})
                    return
            self._send(404, {"error": {"message": "not found"}})

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=sleeper, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        for name in list(children):
            unload(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
