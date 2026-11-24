"""Fake model endpoints on loopback: an OpenAI-compatible server and an Anthropic Messages server.

``running_endpoint("openai")`` or ``running_endpoint("anthropic")`` serves, on 127.0.0.1 and a free port:

- OpenAI-compatible: ``POST /v1/chat/completions`` (whole, or streamed as SSE chunks with a last
  ``usage`` chunk when ``stream_options.include_usage`` asks for it) and ``POST /v1/embeddings``;
- Anthropic: ``POST /v1/messages`` (whole, or streamed: ``message_start`` with the input tokens,
  deltas, ``message_delta`` with the output tokens, ``message_stop``).

It checks the key (``Authorization: Bearer`` or ``x-api-key``) against ``KEY``. A wrong key gets a 401
that quotes the key it was sent, as some providers do, so tests can show it never reaches the caller.
Every request is kept in ``requests`` (path, headers, JSON body). ``error`` makes the next answers an
error with that status (the body quotes the key it was sent); ``holding`` holds answers until ``hang`` is
set or the test ends (for timeouts); ``answer_model`` names another model in whole answers,
``report_usage = False`` leaves their usage out, and ``embed`` (text to vector) gives the embeddings, which are
otherwise ``[0.1, 0.2, 0.3]`` for every text. The usage it reports is ``INPUT_TOKENS`` / ``OUTPUT_TOKENS``.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

KEY = "sk-fake-endpoint-key-0123456789abcdef"
INPUT_TOKENS = 1200
OUTPUT_TOKENS = 300
STREAM_WORDS = ("Hello", " from", " the", " endpoint")


@dataclass
class Seen:
    path: str
    headers: dict[str, str]
    body: Any


@dataclass
class FakeEndpoint:
    api: str
    base_url: str = ""
    requests: list[Seen] = field(default_factory=list)
    error: int | None = None
    hang: threading.Event = field(default_factory=threading.Event)
    holding: bool = False
    # Released once per request that arrives.
    arrived: threading.Semaphore = field(default_factory=lambda: threading.Semaphore(0))
    # A whole answer names this model instead of the one asked for.
    answer_model: str | None = None
    # Whole answers leave out their usage.
    report_usage: bool = True
    # Each input's embedding; None: [0.1, 0.2, 0.3].
    embed: Callable[[str], list[float]] | None = None

    def last(self) -> Seen:
        return self.requests[-1]


def _sse(handler: BaseHTTPRequestHandler, events: list[tuple[str | None, Any]]) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Connection", "close")
    handler.end_headers()
    for name, data in events:
        text = (f"event: {name}\n" if name else "") + f"data: {data if isinstance(data, str) else json.dumps(data)}\n\n"
        handler.wfile.write(text.encode("utf-8"))
        handler.wfile.flush()


def _openai_stream(body: dict) -> list[tuple[str | None, Any]]:
    chunk = {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "model": body["model"]}
    events: list[tuple[str | None, Any]] = [
        (None, {**chunk, "choices": [{"index": 0, "delta": {"content": word}}]}) for word in STREAM_WORDS]
    events.append((None, {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}))
    if (body.get("stream_options") or {}).get("include_usage"):
        events.append((None, {**chunk, "choices": [], "usage": {
            "prompt_tokens": INPUT_TOKENS, "completion_tokens": OUTPUT_TOKENS,
            "total_tokens": INPUT_TOKENS + OUTPUT_TOKENS}}))
    events.append((None, "[DONE]"))
    return events


def _anthropic_stream(body: dict) -> list[tuple[str | None, Any]]:
    return [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_fake", "type": "message", "role": "assistant", "model": body["model"], "content": [],
            "usage": {"input_tokens": INPUT_TOKENS - 200, "cache_read_input_tokens": 200, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        *[("content_block_delta", {"type": "content_block_delta", "index": 0,
                                   "delta": {"type": "text_delta", "text": word}}) for word in STREAM_WORDS],
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                           "usage": {"output_tokens": OUTPUT_TOKENS}}),
        ("message_stop", {"type": "message_stop"}),
    ]


def _handler(endpoint: FakeEndpoint) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: Any) -> None:
            pass

        def _json(self, status: int, payload: Any) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:  # noqa: N802 - http.server's name
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = json.loads(raw) if raw else None
            endpoint.requests.append(Seen(self.path, {k.lower(): v for k, v in self.headers.items()}, body))
            endpoint.arrived.release()
            if endpoint.holding:
                endpoint.hang.wait(30)
                self.close_connection = True  # no answer: the connection just ends
                return
            sent = (self.headers.get("x-api-key") if endpoint.api == "anthropic"
                    else (self.headers.get("Authorization") or "").removeprefix("Bearer "))
            if sent != KEY:
                self._json(401, {"error": {"message": f"Incorrect API key provided: {sent}"}})
                return
            if endpoint.error is not None:
                self._json(endpoint.error, {"error": {"message": f"Something failed for key {sent}",
                                                      "type": "invalid_request_error"}})
                return
            route = (endpoint.api, self.path)
            named = endpoint.answer_model or (body or {}).get("model")
            if route == ("openai", "/v1/chat/completions"):
                if body.get("stream"):
                    _sse(self, _openai_stream(body))
                    return
                answer = {"id": "chatcmpl-fake", "object": "chat.completion", "model": named,
                          "choices": [{"index": 0, "finish_reason": "stop",
                                       "message": {"role": "assistant", "content": "".join(STREAM_WORDS)}}],
                          "usage": {"prompt_tokens": INPUT_TOKENS, "completion_tokens": OUTPUT_TOKENS,
                                    "total_tokens": INPUT_TOKENS + OUTPUT_TOKENS}}
                if not endpoint.report_usage:
                    del answer["usage"]
                self._json(200, answer)
            elif route == ("openai", "/v1/embeddings"):
                inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
                self._json(200, {"object": "list", "model": body["model"],
                                 "data": [{"object": "embedding", "index": i,
                                           "embedding": endpoint.embed(text) if endpoint.embed else [0.1, 0.2, 0.3]}
                                          for i, text in enumerate(inputs)],
                                 "usage": {"prompt_tokens": INPUT_TOKENS, "total_tokens": INPUT_TOKENS}})
            elif route == ("anthropic", "/v1/messages"):
                if body.get("stream"):
                    _sse(self, _anthropic_stream(body))
                    return
                self._json(200, {"id": "msg_fake", "type": "message", "role": "assistant", "model": named,
                                 "content": [{"type": "text", "text": "".join(STREAM_WORDS)}],
                                 "stop_reason": "end_turn",
                                 "usage": {"input_tokens": INPUT_TOKENS, "output_tokens": OUTPUT_TOKENS}})
            else:
                self._json(404, {"error": {"message": "no such route"}})

    return Handler


@contextmanager
def running_endpoint(api: str) -> Iterator[FakeEndpoint]:
    """A fake ``api`` endpoint ("openai" or "anthropic") on loopback; ``base_url`` is what IT would enter."""
    endpoint = FakeEndpoint(api)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(endpoint))
    server.daemon_threads = True
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    endpoint.base_url = origin + "/v1" if api == "openai" else origin
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield endpoint
    finally:
        endpoint.hang.set()
        server.shutdown()
        server.server_close()


__all__ = ["INPUT_TOKENS", "KEY", "OUTPUT_TOKENS", "STREAM_WORDS", "FakeEndpoint", "Seen", "running_endpoint"]
