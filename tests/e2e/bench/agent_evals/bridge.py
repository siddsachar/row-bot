"""Row-Bot as the agent of a benchmark whose environment runs the tools (the worker side of ``wire``).

τ²-bench, Gaia2's vanilla ARE and similar benchmarks own the tools: the benchmark executes each
call against its own database and grades the trajectory. Row-Bot still runs its own turn loop,
prompts and context; only the tools it reaches are the benchmark's. Here:

- :class:`ExternalTools` is a Row-Bot tool group (``bench_external``) whose model-facing tools are
  the benchmark's function schemas. Calling one sends it to the benchmark and waits for the result.
- :class:`Exchange` runs one Row-Bot turn in a thread and turns it into the benchmark's turns: each
  tool call is handed out and its result fed back in; the turn's final reply ends the exchange.
  Calls are handed out one at a time, in the order the model made them.
- :class:`BridgeServer` serves one benchmark connection, one request at a time.
- :class:`Host` is what a server needs from Row-Bot (a conversation, a turn, a plain model call,
  a record of the outcome). The worker gives it a :class:`~tests.e2e.bench.runtime.BenchRuntime`;
  the tests give it fakes.
"""
from __future__ import annotations

import hmac
import logging
import queue
import secrets
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from . import wire

logger = logging.getLogger("row_bot.bench")

GROUP = "bench_external"
#: How long a tool call waits for the benchmark's result before the call fails (the turn goes on).
RESULT_TIMEOUT_S = 900.0


class Stopped(RuntimeError):
    """The run stopped (a usage limit, the cost cap): every later session is refused at once."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


# ── The benchmark's tools as Row-Bot tools ─────────────────────────────────


def _schema_of(spec: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    function = spec.get("function") if spec.get("type") == "function" else spec
    if not isinstance(function, dict) or not function.get("name"):
        raise ValueError(f"not an OpenAI function schema: {str(spec)[:120]}")
    parameters = dict(function.get("parameters") or {"type": "object", "properties": {}})
    parameters.setdefault("type", "object")
    parameters.setdefault("properties", {})
    return str(function["name"]), str(function.get("description") or ""), parameters


def proxy_tools(specs: list[dict[str, Any]], call: Callable[[str, dict[str, Any]], str]) -> list[Any]:
    """One LangChain tool per function schema, with the schema exactly as the benchmark wrote it; calling
    it calls *call* with the tool's name and arguments."""
    from langchain_core.tools import StructuredTool

    tools = []
    for spec in specs:
        name, description, parameters = _schema_of(spec)
        tools.append(StructuredTool(name=name, description=description, args_schema=parameters,
                                    func=lambda _n=name, **kw: call(_n, kw)))
    return tools


class ToolRouter:
    """Where a proxy tool's call goes: the exchange of the turn that is running (one at a time)."""

    def __init__(self) -> None:
        self.active: Exchange | None = None

    def call(self, name: str, arguments: dict[str, Any]) -> str:
        exchange = self.active
        if exchange is None:
            return f"Error: {name} was called outside a benchmark turn"
        return exchange.call(name, arguments)


def external_tool_group(specs: list[dict[str, Any]], router: ToolRouter) -> Any:
    """The ``bench_external`` tool group for Row-Bot's registry (built when the worker starts, before the
    tools are pinned, so the pinned set and the tool-text hashes include every benchmark tool)."""
    from row_bot.tools.base import BaseTool

    class ExternalTools(BaseTool):
        @property
        def name(self) -> str:
            return GROUP

        @property
        def display_name(self) -> str:
            return "Benchmark tools"

        @property
        def description(self) -> str:
            return "The benchmark's own tools, run by the benchmark."

        @property
        def enabled_by_default(self) -> bool:
            return False

        def as_langchain_tools(self) -> list:
            return proxy_tools(specs, router.call)

    return ExternalTools()


def tool_names(specs: list[dict[str, Any]]) -> list[str]:
    return [_schema_of(spec)[0] for spec in specs]


# ── One turn, handed out call by call ──────────────────────────────────────


@dataclass
class Exchange:
    """One Row-Bot turn as a sequence of benchmark acts (tool calls, then the reply)."""

    run_turn: Callable[[], Any]
    result_timeout_s: float = RESULT_TIMEOUT_S
    calls: list[dict[str, Any]] = field(default_factory=list)
    _events: queue.Queue = field(default_factory=queue.Queue)
    _results: queue.Queue = field(default_factory=queue.Queue)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _pending: str = ""
    closed: bool = False

    def begin(self) -> dict[str, Any]:
        def target() -> None:
            try:
                self._events.put(("final", self.run_turn()))
            except Stopped as exc:
                self._events.put(("stopped", exc))
            except Exception as exc:  # noqa: BLE001 - the benchmark is told; the run goes on
                logger.exception("bench bridge: the turn failed")
                self._events.put(("failed", exc))

        threading.Thread(target=target, name="bench-turn", daemon=True).start()
        return self._next()

    def call(self, name: str, arguments: dict[str, Any]) -> str:
        """Inside the turn: hand the call out and wait for its result."""
        with self._lock:  # one call out at a time, in the order the model made them
            if self.closed:
                return "Error: the benchmark connection closed"
            call_id = f"call_{uuid.uuid4().hex[:16]}"
            record = {"id": call_id, "name": name, "arguments": arguments}
            self._events.put(("tool_call", record))
            try:
                got_id, content, error = self._results.get(timeout=self.result_timeout_s)
            except queue.Empty:
                content, error, got_id = "Error: the benchmark gave no result in time", True, call_id
            self.calls.append({**record, "result": str(content)[:600], "error": bool(error)})
            if got_id != call_id:
                return "Error: the benchmark answered a different call"
            return f"Error: {content}" if error and not str(content).lower().startswith("error") else str(content)

    def deliver(self, call_id: str, content: str, error: bool) -> dict[str, Any]:
        if call_id != self._pending:
            return {"type": "error", "kind": "protocol", "error": f"no tool call {call_id!r} is waiting"}
        self._pending = ""
        self._results.put((call_id, content, error))
        return self._next()

    def close(self) -> None:
        self.closed = True
        if self._pending:
            self._results.put((self._pending, "the benchmark connection closed", True))

    def _next(self) -> dict[str, Any]:
        kind, value = self._events.get()
        if kind == "tool_call":
            self._pending = value["id"]
            return {"type": "tool_call", **value}
        self.result = value
        if kind == "final":
            step = value
            if getattr(step, "status", "done") != "done":
                return {"type": "error", "kind": "turn", "error": "; ".join(getattr(step, "errors", []) or [])
                        or f"the turn ended {getattr(step, 'status', '')}"}
            return {"type": "message", "text": str(getattr(step, "answer", "") or "")}
        if kind == "stopped":
            return {"type": "error", "kind": value.kind, "error": str(value)}
        return {"type": "error", "kind": "turn", "error": f"{type(value).__name__}: {value}"[:500]}


# ── What the server needs from Row-Bot ─────────────────────────────────────


class Host(Protocol):
    def new_session(self, session: str, tools: list[dict[str, Any]], instructions: str,
                    meta: dict[str, Any]) -> None: ...

    def turn(self, session: str, prompt: str) -> Any: ...

    def complete(self, purpose: str, model: str, effort: str, messages: list[dict[str, str]]) -> str: ...

    def end(self, session: str, outcome: dict[str, Any], calls: list[dict[str, Any]]) -> None: ...


@dataclass
class _Session:
    tools: list[str]
    exchange: Exchange | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    turns: int = 0


class BridgeServer:
    """Serves one benchmark runner's connection, one request at a time (``wire``'s protocol)."""

    def __init__(self, host: Host, router: ToolRouter, allowed_tools: list[str], *,
                 secret: str = "", result_timeout_s: float = RESULT_TIMEOUT_S) -> None:
        self.host = host
        self.router = router
        self.allowed = set(allowed_tools)
        self.secret = secret or secrets.token_urlsafe(24)
        self.result_timeout_s = result_timeout_s
        self.sessions: dict[str, _Session] = {}
        self.stopped: Stopped | None = None
        self.counts = {"sessions": 0, "turns": 0, "tool_calls": 0, "completions": 0}
        self._stop: Callable[[], bool] = lambda: False
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = int(self._listener.getsockname()[1])

    @property
    def address(self) -> str:
        return f"{self.port}:{self.secret}"

    def serve(self, *, accept_timeout_s: float = 600.0, alive: Callable[[], bool] = lambda: True,
              stop: Callable[[], bool] = lambda: False) -> None:
        """Accept the runner's one connection and serve it until ``bye`` or the connection closes. *stop*
        (the run's STOP file) is checked before each new session: a session running finishes."""
        self._stop = stop
        self._listener.settimeout(1.0)
        deadline = time.monotonic() + accept_timeout_s
        connection = None
        while connection is None:
            try:
                connection, _ = self._listener.accept()
            except TimeoutError:
                if time.monotonic() > deadline or not alive():
                    raise wire.BridgeError("the benchmark runner never connected") from None
        self._listener.close()
        connection.settimeout(None)
        stream = connection.makefile("rwb")
        try:
            first = wire.read_line(stream)
            if first.get("op") != "hello" or not hmac.compare_digest(str(first.get("token") or ""), self.secret):
                wire.send_line(stream, {"refused": "wrong bridge token"})
                return
            wire.send_line(stream, {"ok": True})
            while True:
                try:
                    request = wire.read_line(stream)
                except wire.BridgeError:
                    return
                if request.get("op") == "bye":
                    wire.send_line(stream, {"ok": True})
                    return
                wire.send_line(stream, self.handle(request))
        finally:
            for item in self.sessions.values():
                if item.exchange is not None:
                    item.exchange.close()
            self.router.active = None
            connection.close()

    # -- requests -------------------------------------------------------------

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        op = str(request.get("op") or "")
        try:
            if op == "start":
                return self._start(request)
            if op == "user":
                return self._user(request)
            if op == "tool_result":
                return self._tool_result(request)
            if op == "complete":
                return self._complete(request)
            if op == "end":
                return self._end(request)
        except Stopped as exc:
            self.stopped = self.stopped or exc
            return {"type": "error", "kind": exc.kind, "error": str(exc)}
        return {"refused": f"unknown op {op!r}"}

    def _session(self, request: dict[str, Any]) -> _Session:
        name = str(request.get("session") or "")
        if name not in self.sessions:
            raise KeyError(name)
        return self.sessions[name]

    def _start(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.stopped is not None:
            return {"type": "error", "kind": self.stopped.kind, "error": f"the run stopped: {self.stopped}"}
        if self._stop():
            raise Stopped("stop", "the run's STOP file")
        name = str(request.get("session") or "")
        tools = list(request.get("tools") or [])
        names = tool_names(tools)
        unknown = sorted(set(names) - self.allowed)
        if not name or name in self.sessions:
            return {"refused": f"session {name!r} is empty or already started"}
        if unknown:
            return {"refused": f"tools not pinned for this run: {unknown}"}
        self.sessions[name] = _Session(tools=names)
        self.counts["sessions"] += 1
        self.host.new_session(name, tools, str(request.get("instructions") or ""), dict(request.get("meta") or {}))
        return {"ok": True}

    def _user(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.stopped is not None:
            return {"type": "error", "kind": self.stopped.kind, "error": f"the run stopped: {self.stopped}"}
        session = self._session(request)
        name, text = str(request["session"]), str(request.get("text") or "")
        if session.exchange is not None and session.exchange._pending:
            return {"type": "error", "kind": "protocol", "error": "a tool call is still waiting for its result"}
        session.turns += 1
        self.counts["turns"] += 1
        exchange = Exchange(lambda: self.host.turn(name, text), result_timeout_s=self.result_timeout_s)
        session.exchange = exchange
        self.router.active = exchange
        return self._after(session, exchange.begin())

    def _tool_result(self, request: dict[str, Any]) -> dict[str, Any]:
        session = self._session(request)
        if session.exchange is None:
            return {"type": "error", "kind": "protocol", "error": "no turn is running"}
        reply = session.exchange.deliver(str(request.get("id") or ""), str(request.get("content") or ""),
                                         bool(request.get("error")))
        return reply if reply.get("kind") == "protocol" else self._after(session, reply)

    def _after(self, session: _Session, reply: dict[str, Any]) -> dict[str, Any]:
        if reply["type"] == "tool_call":
            self.counts["tool_calls"] += 1
            return reply
        exchange = session.exchange
        if exchange is not None:
            session.calls.extend(exchange.calls)
        self.router.active = None
        if reply["type"] == "error" and reply.get("kind") in {"limit", "budget"}:
            self.stopped = self.stopped or Stopped(str(reply["kind"]), str(reply.get("error") or ""))
        return reply

    def _complete(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.stopped is not None:
            return {"type": "error", "kind": self.stopped.kind, "error": f"the run stopped: {self.stopped}"}
        self.counts["completions"] += 1
        messages = [{"role": str(item.get("role") or "user"), "content": str(item.get("content") or "")}
                    for item in request.get("messages") or []]
        return {"text": self.host.complete(str(request.get("purpose") or ""), str(request.get("model") or ""),
                                           str(request.get("effort") or ""), messages)}

    def _end(self, request: dict[str, Any]) -> dict[str, Any]:
        name = str(request.get("session") or "")
        session = self.sessions.get(name)
        self.host.end(name, dict(request.get("outcome") or {}), list(session.calls) if session else [])
        return {"ok": True}
