from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


MODELS = [
    {
        "id": "thoth-dummy-chat",
        "object": "model",
        "created": 0,
        "owned_by": "thoth-local-test",
        "context_length": 8192,
    },
    {
        "id": "thoth-dummy-embedding",
        "object": "model",
        "created": 0,
        "owned_by": "thoth-local-test",
        "context_length": 4096,
    },
]


class DummyOpenAIHandler(BaseHTTPRequestHandler):
    server_version = "ThothDummyOpenAI/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/v1/models":
            self._send_json({"object": "list", "data": MODELS})
            return
        self._send_json({"error": {"message": "Not found", "type": "not_found"}}, status=404)

    def do_POST(self) -> None:
        if self.path.rstrip("/") == "/v1/chat/completions":
            payload = self._read_json()
            if payload.get("stream"):
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
        model = str(payload.get("model") or "thoth-dummy-chat")
        last_user = _last_user_message(payload.get("messages"))
        content = f"Dummy endpoint received: {last_user or 'hello from Thoth'}"
        self._send_json({
            "id": "chatcmpl-thoth-dummy",
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
        model = str(payload.get("model") or "thoth-dummy-chat")
        self._send_headers(content_type="text/event-stream")
        chunks = ["Dummy ", "endpoint ", "stream OK"]
        for text in chunks:
            event = {
                "id": "chatcmpl-thoth-dummy",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
            }
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _send_embeddings(self) -> None:
        self._send_json({
            "object": "list",
            "data": [{"object": "embedding", "index": 0, "embedding": [0.0, 0.1, 0.2, 0.3]}],
            "model": "thoth-dummy-embedding",
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local dummy OpenAI-compatible endpoint for Thoth provider testing.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), DummyOpenAIHandler)
    print(f"Dummy OpenAI-compatible endpoint listening at http://{args.host}:{args.port}/v1")
    print("Models: thoth-dummy-chat, thoth-dummy-embedding")
    server.serve_forever()


if __name__ == "__main__":
    main()