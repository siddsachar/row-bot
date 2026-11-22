"""Calling an endpoint: its URL and headers, and the tokens its answer says it used.

Two wire formats, each passed through as it is:

- **OpenAI-compatible** (vLLM, Ollama, llama.cpp, TGI, LM Studio, OpenRouter, Azure OpenAI's v1 API):
  the endpoint's base URL ends where the OpenAI SDK's ``base_url`` does (``https://host/v1``), and the
  key goes in ``Authorization: Bearer``. Usage is ``usage.prompt_tokens`` and
  ``usage.completion_tokens``; a stream carries it in its last chunk because the gateway asks for it
  (``stream_options.include_usage``).
- **Anthropic Messages**: the base URL is the SDK's (``https://api.anthropic.com``), the path
  ``/v1/messages`` and the key ``x-api-key``. Usage is ``usage.input_tokens`` (plus cache reads and
  writes) and ``usage.output_tokens``; a stream carries them in ``message_start`` and ``message_delta``.

Only these headers reach the endpoint: never the caller's cookie or anything else it sent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

OPENAI = "openai"
ANTHROPIC = "anthropic"
DEFAULT_ANTHROPIC_VERSION = "2023-06-01"
# The Anthropic headers a caller may pass on (the API version and beta features).
ANTHROPIC_PASS_THROUGH = ("anthropic-version", "anthropic-beta")
_HEADER_VALUE = re.compile(r"^[A-Za-z0-9._,-]{1,256}$")
PATHS = {
    (OPENAI, "chat"): "/chat/completions",
    (OPENAI, "embeddings"): "/embeddings",
    (ANTHROPIC, "chat"): "/v1/messages",
}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


def target_url(api: str, base_url: str, kind: str) -> str:
    return base_url + PATHS[(api, kind)]


def valid_header_value(value: str) -> bool:
    return _HEADER_VALUE.fullmatch(value) is not None


def request_headers(api: str, api_key: str | None, passed: dict[str, str]) -> dict[str, str]:
    """The endpoint's headers: the content type, the endpoint's key, and for Anthropic its version headers."""
    headers = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
    if api == OPENAI:
        if api_key is not None:
            headers["authorization"] = f"Bearer {api_key}"
        return headers
    headers["anthropic-version"] = passed.get("anthropic-version", DEFAULT_ANTHROPIC_VERSION)
    if "anthropic-beta" in passed:
        headers["anthropic-beta"] = passed["anthropic-beta"]
    if api_key is not None:
        headers["x-api-key"] = api_key
    return headers


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _merge(usage: Usage, fields: Any, api: str) -> None:
    """Fold one ``usage`` object into ``usage``. Counts are cumulative, so the larger one wins."""
    if not isinstance(fields, dict):
        return
    if api == OPENAI:
        inputs = [_count(fields.get("prompt_tokens"))]
        output = _count(fields.get("completion_tokens"))
    else:
        inputs = [_count(fields.get(name)) for name in
                  ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")]
        output = _count(fields.get("output_tokens"))
    if any(count is not None for count in inputs):
        usage.input_tokens = max(usage.input_tokens, sum(count or 0 for count in inputs))
    if output is not None:
        usage.output_tokens = max(usage.output_tokens, output)


def usage_of(api: str, body: Any) -> Usage:
    """The usage a whole (not streamed) answer reports."""
    usage = Usage()
    if isinstance(body, dict):
        _merge(usage, body.get("usage"), api)
    return usage


class StreamUsage:
    """Reads the usage out of a stream's ``data:`` lines as they pass."""

    def __init__(self, api: str) -> None:
        self.api = api
        self.usage = Usage()

    def feed(self, line: str) -> None:
        if not line.startswith("data:") or '"usage"' not in line:
            return
        try:
            event = json.loads(line[5:])
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        _merge(self.usage, event.get("usage"), self.api)
        message = event.get("message")
        if isinstance(message, dict):  # Anthropic's message_start
            _merge(self.usage, message.get("usage"), self.api)


def error_body(api: str, code: str, message: str) -> dict[str, Any]:
    """A refusal in the caller's wire format, so its SDK shows the message."""
    if api == ANTHROPIC:
        return {"type": "error", "error": {"type": code, "message": message}}
    return {"error": {"message": message, "type": code, "code": code}}


def stream_error_event(api: str, code: str, message: str) -> bytes:
    """The event that ends a stream the endpoint broke off."""
    data = json.dumps(error_body(api, code, message))
    return (f"event: error\ndata: {data}\n\n" if api == ANTHROPIC else f"data: {data}\n\n").encode("utf-8")


__all__ = ["ANTHROPIC", "ANTHROPIC_PASS_THROUGH", "OPENAI", "PATHS", "StreamUsage", "Usage", "error_body",
           "request_headers", "stream_error_event", "target_url", "usage_of", "valid_header_value"]
