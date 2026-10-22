"""A scripted OpenAI-compatible model on loopback, served like llama-server, for the opt-in
personal-model end-to-end test (L2c).

Row-Bot talks to it through its normal ``llama_cpp`` endpoint profile, so every request it sends
is exactly what Row-Bot sends a local model (the system prompt, the tools it binds, the turn
context, the real tool results) and Learning captures it like any other turn. The answers are the
"main model's" moves for one kind of task, a calculation: call the calculator (directly, or
through ``tool_search``/``tool_invoke`` when Row-Bot defers it), then answer with its result. Row-
Bot's own probes get the answers they ask for; anything else gets "ok".
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

ALIAS = "scripted-main"
N_CTX = 32768
CONTEXT_TAG = "<row_bot_turn_context>"


def _id() -> str:
    return secrets.token_hex(12)


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return ""


class ScriptedModel:
    """Serves the scripted moves; ``expression`` is what the current turn asks to calculate."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.expression = "1 + 1"
        self.requests = 0
        self.agent_requests = 0
        self.tools_seen: set[str] = set()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def ask(self, expression: str) -> None:
        with self.lock:
            self.expression = expression

    def __enter__(self) -> ScriptedModel:
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()

    # ── The moves ───────────────────────────────────────────────────────

    def answer(self, body: dict[str, Any]) -> dict[str, Any]:
        """``{"text": ...}`` or ``{"calls": [(name, arguments), ...]}``."""

        messages = body.get("messages") or []
        tools = {str((tool.get("function") or {}).get("name") or "") for tool in body.get("tools") or ()}
        with self.lock:
            self.requests += 1
            expression = self.expression
        probe = next((name for name in tools if name.startswith("row_bot_probe")), None)
        if probe:
            last = messages[-1] if messages else {}
            if last.get("role") == "tool":
                return {"text": "ok"}
            return {"calls": [(probe, {"value": "ok"})]}
        if not messages or messages[0].get("role") != "system" or not tools:
            return {"text": "ok"}
        with self.lock:
            self.agent_requests += 1
            self.tools_seen |= tools
        task = max((index for index, message in enumerate(messages) if message.get("role") == "user"
                    and not _text(message.get("content")).lstrip().startswith(CONTEXT_TAG)), default=0)
        after = messages[task + 1:]
        steps = sum(1 for message in after if message.get("role") == "assistant")
        results = [_text(message.get("content")) for message in after if message.get("role") == "tool"]
        direct = "calculate" in tools
        if direct and steps == 0:
            return {"calls": [("calculate", {"expression": expression})]}
        if not direct and "tool_invoke" in tools and steps == 0:  # the catalog's own name, so search finds it
            return {"calls": [("tool_search", {"query": "calculate", "limit": 3})]}
        if not direct and "tool_invoke" in tools and steps == 1:
            return {"calls": [("tool_invoke", {"name": "calculate", "arguments": {"expression": expression}})]}
        found = results[-1] if results else ""
        value = found.rsplit(" = ", 1)[-1].strip() if " = " in found else found.strip()
        return {"text": f"{expression} = {value}" if value else "I couldn't work that out."}

    def _handler(self):
        model = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                return

            def _json(self, payload: dict[str, Any], status: int = 200) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802 - the http.server hook
                if self.path.endswith("/models"):
                    self._json({"object": "list", "data": [{"id": ALIAS, "object": "model", "owned_by": "llamacpp",
                                                            "meta": {"n_ctx": N_CTX, "n_ctx_train": 262144}}]})
                elif self.path.endswith("/props"):
                    self._json({"default_generation_settings": {"params": {}, "n_ctx": N_CTX}, "total_slots": 1,
                                "model_alias": ALIAS, "modalities": {"vision": False, "audio": False}})
                elif self.path.endswith("/health"):
                    self._json({"status": "ok"})
                else:
                    self._json({"error": "not found"}, 404)

            def do_POST(self) -> None:  # noqa: N802 - the http.server hook
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                answer = model.answer(body)
                created = int(time.time())
                if "calls" in answer:
                    calls = [{"id": "call_" + _id(), "type": "function",
                              "function": {"name": name, "arguments": json.dumps(arguments)}}
                             for name, arguments in answer["calls"]]
                    message: dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": calls}
                    finish = "tool_calls"
                else:
                    message = {"role": "assistant", "content": answer["text"]}
                    finish = "stop"
                usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
                if not body.get("stream"):
                    self._json({"id": "chatcmpl-" + _id(), "object": "chat.completion", "created": created,
                                "model": ALIAS, "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                                "usage": usage})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()

                def chunk(delta: dict[str, Any], finish_reason: str | None = None, **extra: Any) -> None:
                    event = {"id": "chatcmpl-x", "object": "chat.completion.chunk", "created": created, "model": ALIAS,
                             "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}], **extra}
                    self.wfile.write(b"data: " + json.dumps(event).encode("utf-8") + b"\n\n")

                try:
                    chunk({"role": "assistant", "content": None})
                    if "calls" in answer:
                        for index, call in enumerate(message["tool_calls"]):
                            chunk({"tool_calls": [{"index": index, **call}]})
                    else:
                        text = answer["text"]
                        for start in range(0, len(text), 24):
                            chunk({"content": text[start:start + 24]})
                    chunk({}, finish, usage=usage)
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    pass
                self.close_connection = True

        return Handler


__all__ = ["ALIAS", "N_CTX", "ScriptedModel"]
