"""A local OpenAI-compatible endpoint that answers without any model.

For manual custom-endpoint testing, and the fake provider of the hosting smoke
(``scripts/smoke_hosting_partner.py``), which runs it from its source inside the
container. It calls Row-Bot's endpoint probe tool when offered it, so the probe
finds it Agent ready. With ``--api-key-sha256`` it answers only requests whose
bearer key has that SHA-256. Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PROBE_TOOL = "row_bot_probe_echo"


MODELS = [
    {
        "id": "row-bot-dummy-chat",
        "object": "model",
        "created": 0,
        "owned_by": "row-bot-local-test",
        "context_length": 131072,
    },
    {
        "id": "row-bot-dummy-embedding",
        "object": "model",
        "created": 0,
        "owned_by": "row-bot-local-test",
        "context_length": 4096,
    },
]


class DummyOpenAIHandler(BaseHTTPRequestHandler):
    server_version = "RowBotDummyOpenAI/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def _authorized(self) -> bool:
        expected = getattr(self.server, "api_key_sha256", "")
        if not expected:
            return True
        scheme, _, key = str(self.headers.get("Authorization") or "").partition(" ")
        digest = hashlib.sha256(key.strip().encode("utf-8")).hexdigest()
        if scheme.lower() == "bearer" and key and hmac.compare_digest(digest, expected):
            return True
        self._send_json({"error": {"message": "Invalid API key", "type": "invalid_api_key"}}, status=401)
        return False

    def do_GET(self) -> None:
        if not self._authorized():
            return
        if self.path.rstrip("/") == "/v1/models":
            self._send_json({"object": "list", "data": MODELS})
            return
        self._send_json({"error": {"message": "Not found", "type": "not_found"}}, status=404)

    def do_POST(self) -> None:
        # Read the body first, even for a refusal: closing with unread bytes resets the connection
        # (macOS does at once), and the client never sees the answer.
        payload = self._read_json()
        if not self._authorized():
            return
        if self.path.rstrip("/") == "/v1/chat/completions":
            if _calls_probe_tool(payload):
                self._send_probe_tool_call(payload)
            elif payload.get("stream"):
                self._send_stream(payload)
            else:
                self._send_chat_completion(payload)
            return
        if self.path.rstrip("/") == "/v1/embeddings":
            self._send_embeddings()
            return
        self._send_json({"error": {"message": "Not found", "type": "not_found"}}, status=404)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _send_headers(self, status: int = 200, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "authorization, content-type")
        self.end_headers()

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        self._send_headers(status=status)
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def _send_chat_completion(self, payload: dict[str, Any]) -> None:
        model = str(payload.get("model") or "row-bot-dummy-chat")
        last_user = _last_user_message(payload.get("messages"))
        content = f"Dummy endpoint received: {last_user or 'hello from Row-Bot'}"
        self._send_json({
            "id": "chatcmpl-row-bot-dummy",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 8, "completion_tokens": 8, "total_tokens": 16},
        })

    def _send_stream(self, payload: dict[str, Any]) -> None:
        model = str(payload.get("model") or "row-bot-dummy-chat")
        self._send_headers(content_type="text/event-stream")
        chunks = ["Dummy ", "endpoint ", "stream OK"]
        for text in chunks:
            event = {
                "id": "chatcmpl-row-bot-dummy",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
            }
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _send_probe_tool_call(self, payload: dict[str, Any]) -> None:
        model = str(payload.get("model") or "row-bot-dummy-chat")
        call = {"id": "call_row_bot_dummy", "type": "function",
                "function": {"name": PROBE_TOOL, "arguments": json.dumps({"value": "ok"})}}
        if not payload.get("stream"):
            self._send_json({
                "id": "chatcmpl-row-bot-dummy",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": None, "tool_calls": [call]},
                    "finish_reason": "tool_calls",
                }],
            })
            return
        self._send_headers(content_type="text/event-stream")
        for delta, finish in (({"tool_calls": [{"index": 0, **call}]}, None), ({}, "tool_calls")):
            event = {
                "id": "chatcmpl-row-bot-dummy",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _send_embeddings(self) -> None:
        self._send_json({
            "object": "list",
            "data": [{"object": "embedding", "index": 0, "embedding": [0.0, 0.1, 0.2, 0.3]}],
            "model": "row-bot-dummy-embedding",
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        })


def _last_user_message(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            return content if isinstance(content, str) else json.dumps(content)
    return ""


def _calls_probe_tool(payload: dict[str, Any]) -> bool:
    """Row-Bot's endpoint probe offers its echo tool and waits for the call; a turn after the result gets text."""
    tools = payload.get("tools") if isinstance(payload.get("tools"), list) else []
    offered = any(isinstance(tool, dict) and isinstance(tool.get("function"), dict)
                  and tool["function"].get("name") == PROBE_TOOL for tool in tools)
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    return offered and bool(messages) and isinstance(messages[-1], dict) and messages[-1].get("role") == "user"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local dummy OpenAI-compatible endpoint for Row-Bot provider testing.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--api-key-sha256", default="", help="answer only requests whose bearer key has this SHA-256")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), DummyOpenAIHandler)
    server.api_key_sha256 = args.api_key_sha256.strip().lower()
    print(f"Dummy OpenAI-compatible endpoint listening at http://{args.host}:{args.port}/v1")
    print("Models: row-bot-dummy-chat, row-bot-dummy-embedding")
    server.serve_forever()


if __name__ == "__main__":
    main()