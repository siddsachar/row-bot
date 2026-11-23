"""An OpenAI Chat Completions endpoint on loopback that answers from a script, for headless runs.

    with fake_chat_endpoint([calls(("run_command", {"command": "ls"})), text("Done.")]) as endpoint:
        ...  # --base-url endpoint.url --model fake-model
        endpoint.requests  # every chat request's JSON body, in order

Each request that carries tools (an agent's model round) takes the next reply of the script (the last one
repeats); a request without tools (a title, the stop check after a turn) gets ``side_reply``. Replies are
streamed when the request asks for a stream. ``GET /models`` lists the one model. Nothing leaves the machine.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MODEL = "fake-model"


@dataclass(frozen=True)
class ScriptedReply:
    text: str = ""
    tool_calls: tuple[tuple[str, dict[str, Any]], ...] = ()
    usage: tuple[int, int] = (100, 10)


def text(words: str) -> ScriptedReply:
    return ScriptedReply(text=words)


def calls(*tool_calls: tuple[str, dict[str, Any]]) -> ScriptedReply:
    return ScriptedReply(tool_calls=tuple(tool_calls))


@dataclass
class FakeChatEndpoint:
    script: list[ScriptedReply]
    url: str = ""
    side_reply: str = "NO"
    requests: list[dict[str, Any]] = field(default_factory=list)
    headers: list[dict[str, str]] = field(default_factory=list)

    def agent_requests(self) -> list[dict[str, Any]]:
        """The requests that carried the agent's tools (its rounds, not titles or checks)."""
        return [body for body in self.requests if body.get("tools")]

    def reply_to(self, body: dict[str, Any]) -> ScriptedReply:
        if not body.get("tools"):
            return text(self.side_reply)
        return self.script[min(len(self.agent_requests()) - 1, len(self.script) - 1)]


def _message(reply: ScriptedReply) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": reply.text or None}
    if reply.tool_calls:
        message["tool_calls"] = [
            {"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}
            for name, args in reply.tool_calls
        ]
    return message


def _usage(reply: ScriptedReply) -> dict[str, int]:
    prompt, completion = reply.usage
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}


def _handler(endpoint: FakeChatEndpoint) -> type[BaseHTTPRequestHandler]:
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            return

        def _send(self, payload: object) -> None:
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            self._send({"object": "list", "data": [{"id": MODEL, "object": "model"}]})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            with lock:
                endpoint.requests.append(body)
                endpoint.headers.append({key.lower(): value for key, value in self.headers.items()})
                reply = endpoint.reply_to(body)
            message = _message(reply)
            if not body.get("stream"):
                self._send({"id": "chatcmpl-fake", "object": "chat.completion", "model": MODEL,
                            "choices": [{"index": 0, "message": message,
                                         "finish_reason": "tool_calls" if reply.tool_calls else "stop"}],
                            "usage": _usage(reply)})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            delta: dict[str, Any] = {"role": "assistant", "content": reply.text or ""}
            if reply.tool_calls:
                delta["tool_calls"] = [{"index": index, **call} for index, call in enumerate(message["tool_calls"])]
            chunks = [
                {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "model": MODEL,
                 "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "model": MODEL,
                 "choices": [{"index": 0, "delta": {},
                              "finish_reason": "tool_calls" if reply.tool_calls else "stop"}]},
                {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "model": MODEL, "choices": [],
                 "usage": _usage(reply)},
            ]
            for chunk in chunks:
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True

    return Handler


@contextmanager
def fake_chat_endpoint(script: Sequence[ScriptedReply]) -> Iterator[FakeChatEndpoint]:
    if not script:
        raise ValueError("a script needs at least one reply")
    endpoint = FakeChatEndpoint(script=list(script))
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(endpoint))
    server.daemon_threads = True
    endpoint.url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield endpoint
    finally:
        server.shutdown()
        server.server_close()


__all__ = ["MODEL", "FakeChatEndpoint", "ScriptedReply", "calls", "fake_chat_endpoint", "text"]
