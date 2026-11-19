"""The wire between a benchmark's own runner and the Row-Bot worker that plays its agent.

Standard library only: the benchmark side (tau2-bench's runner in its own virtual environment, a
Harbor or PawBench adapter) imports this file by path, never Row-Bot.

One loopback TCP connection, JSON lines, strictly request then reply. The benchmark side
sends a request and reads exactly one reply:

- ``{"op": "hello", "token": ...}``: the shared secret from ``ROW_BOT_BENCH_BRIDGE``. A wrong
  token closes the connection.
- ``{"op": "start", "session", "tools": [OpenAI function schemas], "instructions", "meta"}``:
  a new Row-Bot conversation whose only pinned tools are *tools* (run by the benchmark).
- ``{"op": "user", "session", "text"}``: the person's next message. Reply: an :data:`ACT`.
- ``{"op": "tool_result", "session", "id", "content", "error"}``: the result of the tool call
  the last reply asked for. Reply: an :data:`ACT`.
- ``{"op": "complete", "purpose", "model", "effort", "messages": [{"role", "content"}]}``: one
  plain model call through Row-Bot's model layer (a user simulator, a judge). Reply:
  ``{"text": ...}``.
- ``{"op": "end", "session", "outcome": {...}}``: the benchmark's verdict, recorded in the
  trajectory. Reply ``{"ok": true}``.
- ``{"op": "bye"}``: no more sessions.

An ACT reply is ``{"type": "tool_call", "id", "name", "arguments"}`` (Row-Bot called one of
the benchmark's tools and waits for its result), ``{"type": "message", "text"}`` (its turn
ended with this reply to the person) or ``{"type": "error", "error", "kind"}`` (``kind`` is
``limit`` when the provider's usage limit stopped it, ``budget`` for the cost cap).
"""
from __future__ import annotations

import json
import os
import socket
from typing import Any

TOKEN_ENV = "ROW_BOT_BENCH_BRIDGE"  # "<port>:<secret>"
ACT = ("tool_call", "message", "error")
#: Error kinds after which the whole run stops (no later session would get anywhere).
STOPPING = ("limit", "budget", "stop")
MAX_LINE = 16 * 1024 * 1024


class BridgeError(RuntimeError):
    """The other side broke the protocol or went away."""


def stopping(reply: dict[str, Any]) -> bool:
    """Whether *reply* says the run stopped (a usage limit, the cost cap, the STOP file)."""
    return reply.get("type") == "error" and reply.get("kind") in STOPPING


def send_line(stream: Any, payload: dict[str, Any]) -> None:
    stream.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    stream.flush()


def read_line(stream: Any) -> dict[str, Any]:
    raw = stream.readline(MAX_LINE)
    if not raw:
        raise BridgeError("the connection closed")
    try:
        value = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise BridgeError(f"not a JSON line: {raw[:80]!r}") from exc
    if not isinstance(value, dict):
        raise BridgeError("a line must be a JSON object")
    return value


class BridgeClient:
    """The benchmark side: one connection to the Row-Bot worker."""

    def __init__(self, address: str | None = None, *, timeout_s: float = 3600.0) -> None:
        text = address if address is not None else os.environ.get(TOKEN_ENV, "")
        port, _, secret = str(text).partition(":")
        if not port.isdigit() or not secret:
            raise BridgeError(f"{TOKEN_ENV} must be <port>:<secret>")
        self._socket = socket.create_connection(("127.0.0.1", int(port)), timeout=timeout_s)
        self._stream = self._socket.makefile("rwb")
        self.request({"op": "hello", "token": secret})

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        send_line(self._stream, payload)
        reply = read_line(self._stream)
        if reply.get("refused"):
            raise BridgeError(str(reply["refused"]))
        return reply

    def start(self, session: str, tools: list[dict[str, Any]], instructions: str,
              meta: dict[str, Any]) -> dict[str, Any]:
        return self.request({"op": "start", "session": session, "tools": tools, "instructions": instructions,
                             "meta": meta})

    def user(self, session: str, text: str) -> dict[str, Any]:
        return self.request({"op": "user", "session": session, "text": text})

    def tool_result(self, session: str, call_id: str, content: str, *, error: bool = False) -> dict[str, Any]:
        return self.request({"op": "tool_result", "session": session, "id": call_id, "content": content,
                             "error": bool(error)})

    def complete(self, purpose: str, model: str, effort: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        return self.request({"op": "complete", "purpose": purpose, "model": model, "effort": effort,
                             "messages": messages})

    def end(self, session: str, outcome: dict[str, Any]) -> None:
        self.request({"op": "end", "session": session, "outcome": outcome})

    def close(self) -> None:
        try:
            self.request({"op": "bye"})
        except (BridgeError, OSError):
            pass
        finally:
            self._socket.close()
