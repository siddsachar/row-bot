"""Isolated MCP server runtime and dynamic LangChain tool wrappers."""

from __future__ import annotations

import asyncio
import collections
import concurrent.futures
import contextvars
import copy
import contextlib
import datetime as _dt
import hashlib
import json
import logging
import os
import re
import threading
import time
import traceback
import uuid
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, create_model

from row_bot.cancellation import current_cancellation_scope
from row_bot.mcp_client import config as mcp_config
from row_bot.mcp_client.logging import log_event, redact
from row_bot.mcp_client.requirements import apply_managed_runtime_env, missing_command_message, resolve_command
from row_bot.mcp_client.results import normalize_call_result
from row_bot.integrations.presets import locked as recorded_locked
from row_bot.mcp_client.safety import (asks_first, classify_tool_effect, hints, is_destructive_tool, prefixed_tool_name,
                                       sanitize_name_component, tool_enabled_by_default)

logger = logging.getLogger(__name__)

try:  # optional until requirements are installed
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except Exception:  # pragma: no cover - exercised in environments without mcp
    ClientSession = None
    StdioServerParameters = None
    stdio_client = None

try:
    from mcp.client.streamable_http import streamablehttp_client
except Exception:  # pragma: no cover
    streamablehttp_client = None
# The SDK logs each server's session id at INFO; ids stay out of Row-Bot's logs.
logging.getLogger("mcp.client.streamable_http").setLevel(logging.WARNING)


class _TransportLog(logging.Filter):
    """The SDK's own transport errors carry the request URL, which can hold a key the person gave for
    the address: only that something failed is logged. Row-Bot's own failure line is redacted."""
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING and not getattr(record, "row_bot_withheld", False):
            record.row_bot_withheld = True
            kind = record.exc_info[0].__name__ if record.exc_info and record.exc_info[0] else ""
            record.msg = "MCP transport error" + (f" ({kind})" if kind else "") + "; details withheld."
            record.args, record.exc_info, record.exc_text = (), None, None
        return True


for _name in ("mcp.client.streamable_http", "mcp.client.sse"):
    if not any(type(item).__name__ == "_TransportLog" for item in logging.getLogger(_name).filters):  # Once, even reloaded.
        logging.getLogger(_name).addFilter(_TransportLog())

try:
    from mcp.client.sse import sse_client
except Exception:  # pragma: no cover
    sse_client = None


@dataclass
class McpToolInfo:
    server_name: str
    name: str
    prefixed_name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    enabled: bool = False
    destructive: bool = False
    requires_approval: bool = False
    source: dict[str, Any] = field(default_factory=dict)
    effect: str = ""
    title: str = ""  # The tool's own readable title, when the server gives one.
    ui: str = ""  # The ``ui://`` view it declares (MCP Apps), if any.
    visibility: tuple[str, ...] = ("model", "app")  # Who may call it: the agent ("model"), its view ("app").
    locked: bool = False  # Asks every time in every approval mode: destructive, unknown effect, or recorded so.
    annotations: dict[str, bool] = field(default_factory=dict)  # Its readOnlyHint/destructiveHint, kept with its catalog.


@dataclass
class McpServerStatus:
    name: str
    enabled: bool = False
    status: str = "disabled"
    transport: str = "stdio"
    tool_count: int = 0
    enabled_tool_count: int = 0
    destructive_tool_count: int = 0
    last_error: str = ""
    last_connected_at: str = ""
    last_discovered_at: str = ""
    source: dict[str, Any] = field(default_factory=dict)


_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_runtime_lock = threading.RLock()
_servers: dict[str, "McpServerRuntime"] = {}
_catalog: dict[str, dict[str, McpToolInfo]] = {}
# Local app programs this runtime starts are recorded (pid and creation time) so that a later Row-Bot,
# after a crash, stops the ones it provably owned (owned_processes).
APP_PROCESSES = "app-processes.json"
_spawning: contextvars.ContextVar["McpServerRuntime | None"] = contextvars.ContextVar("mcp_spawning", default=None)
# Every chat tool name this runtime gave an agent -> (server, the server's own tool or helper name). Which
# app a step belongs to is read only from here or the discovered catalog, never guessed from a name.
_issued: dict[str, tuple[str, str]] = {}
_statuses: dict[str, McpServerStatus] = {}
_stderr_tails: dict[str, list[str]] = {}  # What a server that failed to start last wrote, secrets masked (F22).


class _StderrTail:
    """The last lines a stdio program writes to stderr, so a failed start can say why (F22)."""

    def __init__(self) -> None:
        read_fd, write_fd = os.pipe()
        self.stream = os.fdopen(write_fd, "w")  # The program's stderr; this process never writes to it.
        self._lines: collections.deque[str] = collections.deque(maxlen=40)
        self._thread = threading.Thread(target=self._read, args=(os.fdopen(read_fd, "rb"),), daemon=True,
                                        name="mcp-stderr")
        self._thread.start()

    def _read(self, pipe) -> None:
        with pipe, contextlib.suppress(OSError, ValueError):
            while chunk := pipe.readline(4096):  # A line without an end is read in bounded pieces.
                self._lines.append(chunk.decode("utf-8", "replace").rstrip()[:300])

    def release(self) -> None:
        """The program holds its own copy; closing this one lets the program's exit end the tail."""
        with contextlib.suppress(OSError):
            self.stream.close()

    def lines(self, wait: float = 0.0) -> list[str]:
        self._thread.join(wait)
        return list(self._lines)


def _launch_values(cfg: dict) -> list[str]:
    """What a server starts with that may be a key: every variable and header value, and each argument that
    looks like one or follows (or is given to) a flag named like one, since a key can go on a command line."""
    from row_bot.integrations.inputs import secret_segment, secretish
    values = [str(value) for field in ("env", "headers") for value in (cfg.get(field) or {}).values()]
    flag = ""
    for arg in map(str, cfg.get("args") or []):
        name, given, value = arg.partition("=")
        if secretish(flag.lstrip("-")) or (given and secretish(name.lstrip("-"))) or secret_segment(value or name):
            values.append(value if given else arg)
        flag = arg if arg.startswith("-") and not given else ""
    return [value for value in values if len(value) >= 4]


def stderr_tails() -> dict[str, list[str]]:
    """By server name: what each server that failed to start last wrote, its secrets masked."""
    with _runtime_lock:
        return {name: list(lines) for name, lines in _stderr_tails.items()}


def _failure(exc: BaseException) -> str:
    """An error's text, including what failed inside a task group (a refused sign-in often does)."""
    parts, pending = [], [exc]
    while pending and len(parts) < 8:
        current = pending.pop(0)
        parts.append(str(current) if current is exc else type(current).__name__ + ": " + str(current))
        pending += list(getattr(current, "exceptions", ()))
    return " | ".join(parts)[:2000]


def _get_effective_config() -> dict[str, Any]:
    cfg = mcp_config.get_config()
    try:
        from row_bot.plugins.mcp import with_plugin_mcp_servers

        return with_plugin_mcp_servers(cfg)
    except Exception as exc:
        logger.debug("Plugin MCP overlay skipped: %s", exc, exc_info=True)
        return cfg


def _get_effective_server_config(name: str) -> dict[str, Any]:
    servers = _get_effective_config().get("servers", {})
    server = servers.get(name, {}) if isinstance(servers, dict) else {}
    return dict(server) if isinstance(server, dict) else {}


class McpStdioCommandNotFound(RuntimeError):
    pass


class PrivateMcpSession:
    """Dedicated stdio MCP connection that never enters the MCP catalog.

    This is for reviewed internal adapters such as Computer Use.  It reuses
    Row-Bot's MCP loop and SDK transport while deliberately skipping config,
    discovery, status, dynamic tool wrapping, and generic model exposure.
    """

    def __init__(
        self,
        *,
        command: str,
        args: list[str] | tuple[str, ...] = (),
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.command = str(command)
        self.args = tuple(str(arg) for arg in args)
        self.env = dict(env or {})
        self.cwd = cwd
        self.timeout = float(timeout)
        self._session: Any = None
        self._exit_stack: AsyncExitStack | None = None
        self._session_lock: asyncio.Lock | None = None

    async def _open_async(self) -> None:
        if not sdk_available() or StdioServerParameters is None or stdio_client is None:
            raise RuntimeError("Python package 'mcp' with stdio support is not installed")
        if self._session is not None:
            return
        stack = AsyncExitStack()
        try:
            params = StdioServerParameters(
                command=self.command,
                args=list(self.args),
                env=dict(self.env),
                cwd=self.cwd,
            )
            read_stream, write_stream = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await asyncio.wait_for(session.initialize(), timeout=min(self.timeout, 30.0))
        except BaseException:
            with contextlib.suppress(Exception):
                await stack.aclose()
            raise
        self._exit_stack = stack
        self._session = session
        self._session_lock = asyncio.Lock()

    def open(self) -> None:
        _schedule(self._open_async()).result(timeout=min(self.timeout, 35.0))

    async def _call_raw_async(self, tool_name: str, arguments: dict[str, Any], *,
                              deadline: float | None = None) -> Any:
        if self._session is None or self._session_lock is None:
            raise RuntimeError("Private MCP session is not connected")
        deadline = deadline if deadline is not None else time.monotonic() + self.timeout
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("MCP deadline expired")
        async with asyncio.timeout(remaining):
            async with self._session_lock:
                if time.monotonic() >= deadline:
                    raise TimeoutError("MCP deadline expired")
                if self._session is None:
                    raise RuntimeError("Private MCP session is not connected")
                return await self._session.call_tool(str(tool_name), dict(arguments or {}))

    def call_raw(self, tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Return the SDK CallToolResult without generic text normalization."""

        scope = current_cancellation_scope()
        if scope is not None and scope.is_cancelled():
            raise concurrent.futures.CancelledError()
        deadline = time.monotonic() + self.timeout
        future = _schedule(self._call_raw_async(tool_name, dict(arguments or {}), deadline=deadline))
        unregister = scope.register(self.close, "private_mcp.close") if scope is not None else None
        try:
            while True:
                if scope is not None and scope.is_cancelled():
                    future.cancel()
                    raise concurrent.futures.CancelledError()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    future.cancel()
                    raise concurrent.futures.TimeoutError()
                try:
                    return future.result(timeout=min(0.1, remaining))
                except concurrent.futures.TimeoutError:
                    continue
        finally:
            if unregister is not None:
                unregister()

    async def _close_async(self) -> None:
        self._session = None
        self._session_lock = None
        stack = self._exit_stack
        self._exit_stack = None
        if stack is not None:
            with contextlib.suppress(Exception):
                await stack.aclose()

    def close(self) -> None:
        if self._session is None and self._exit_stack is None:
            return
        future = _schedule(self._close_async())
        with contextlib.suppress(Exception):
            future.result(timeout=5.0)


class _GenericArgs(BaseModel):
    """Fallback for complex schemas: accept a JSON object as kwargs."""

    model_config = ConfigDict(extra="allow")


class _ResourceReadArgs(BaseModel):
    uri: str = Field(description="Resource URI to read from the MCP server.")


class _PromptGetArgs(BaseModel):
    name: str = Field(description="Prompt name to retrieve from the MCP server.")
    arguments: dict[str, Any] | None = Field(default=None, description="Optional prompt arguments.")


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def sdk_available() -> bool:
    return ClientSession is not None


def _resolve_stdio_command(command: str, env: dict[str, str]) -> str:
    expanded = os.path.expandvars(os.path.expanduser(command.strip()))
    if not expanded:
        raise RuntimeError("stdio MCP server requires a command")
    resolved, resolved_env, missing = resolve_command(expanded, env)
    if resolved:
        env.clear()
        env.update(resolved_env)
        return resolved
    raise McpStdioCommandNotFound(missing_command_message(command, missing))


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop, _thread
    with _runtime_lock:
        if _loop is not None:
            if _thread is not None and _thread.is_alive() and not _loop.is_closed():
                return _loop
            if _servers:
                raise RuntimeError("MCP runtime cleanup is incomplete")
        loop = _loop = asyncio.new_event_loop()

        def _run() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        _thread = threading.Thread(target=_run, name="Row-Bot-MCP-Runtime", daemon=True)
        _thread.start()
        return _loop


def _schedule(coro: Any) -> concurrent.futures.Future:
    return asyncio.run_coroutine_threadsafe(coro, _ensure_loop())


def _future_result_with_generation_cancellation(
    future: concurrent.futures.Future,
    *,
    timeout: float,
    stopped_message: str,
    label: str,
) -> str:
    scope = current_cancellation_scope()
    if scope is None:
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise
    if scope.is_cancelled():
        future.cancel()
        return stopped_message
    unregister = scope.register(future.cancel, label)
    try:
        deadline = time.monotonic() + timeout
        while True:
            if scope.is_cancelled():
                future.cancel()
                return stopped_message
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                future.cancel()
                raise concurrent.futures.TimeoutError()
            try:
                return future.result(timeout=min(0.1, remaining))
            except concurrent.futures.TimeoutError:
                continue
            except concurrent.futures.CancelledError:
                if scope.is_cancelled():
                    return stopped_message
                raise
    finally:
        unregister()


def _update_status(name: str, **updates: Any) -> None:
    with _runtime_lock:
        status = _statuses.get(name)
        if not status:
            server = _get_effective_server_config(name)
            status = McpServerStatus(
                name=name,
                enabled=bool(server.get("enabled")),
                transport=str(server.get("transport", "stdio")),
                source=dict(server.get("source") or {}),
            )
            _statuses[name] = status
        for key, value in updates.items():
            if hasattr(status, key):
                setattr(status, key, value)


def _tool_attr(tool: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(tool, dict) and name in tool:
            return tool.get(name)
        if hasattr(tool, name):
            return getattr(tool, name)
    return default


UI_EXTENSION = "io.modelcontextprotocol/ui"
VIEW_MEDIA_TYPE = "text/html;profile=mcp-app"
MAX_VIEW_BYTES = 1024 * 1024


def _view_meta(tool: Any) -> tuple[str, tuple[str, ...]]:
    """A tool's MCP Apps view (``_meta.ui.resourceUri``, or the older flat ``ui/resourceUri``) and who may
    call it (``_meta.ui.visibility``: ``model``, ``app``); anything malformed means no view, callable by both."""
    meta = _tool_attr(tool, "meta", "_meta", default=None)
    if not isinstance(meta, dict):
        return "", ("model", "app")
    ui = meta.get("ui") if isinstance(meta.get("ui"), dict) else {}
    uri = ui.get("resourceUri", meta.get("ui/resourceUri"))
    view = uri if isinstance(uri, str) and uri.startswith("ui://") and len(uri) <= 512 and uri.isprintable() else ""
    raw = ui.get("visibility")
    visibility = tuple(v for v in ("model", "app") if isinstance(raw, list) and v in raw) or ("model", "app")
    return view, visibility


_SDK_SESSION = ClientSession
if ClientSession is not None:
    class _AppsSession(ClientSession):  # type: ignore[misc, valid-type]
        """Says, when it connects, that Row-Bot can show an app's views (the MCP Apps extension), so a server
        may describe them. Whether a view is shown is still Row-Bot's and the person's choice."""

        async def send_request(self, request: Any, result_type: Any, *args: Any, **kwargs: Any) -> Any:
            from mcp import types
            root = getattr(request, "root", None)
            if isinstance(root, types.InitializeRequest) and _views_offered():
                capabilities = types.ClientCapabilities(**root.params.capabilities.model_dump(exclude_none=True),
                                                        extensions={UI_EXTENSION: {"mimeTypes": [VIEW_MEDIA_TYPE]}})
                request = types.ClientRequest(root.model_copy(
                    update={"params": root.params.model_copy(update={"capabilities": capabilities})}))
            return await super().send_request(request, result_type, *args, **kwargs)
else:  # pragma: no cover - the SDK is missing
    _AppsSession = None


def _views_offered() -> bool:
    try:
        from row_bot.integrations import views
        return views.enabled()
    except Exception:
        return False


def _annotation_title(tool: Any) -> str:
    annotations = _tool_attr(tool, "annotations", default=None)
    return str(_tool_attr(annotations, "title", default="") or "") if annotations is not None else ""


def _accepted_tool_matches(server_cfg: dict, info: McpToolInfo) -> bool:
    """A previously accepted catalog never grants a newly deployed capability."""
    accepted = server_cfg.get("tools", {}).get("catalog")
    if not isinstance(accepted, dict):
        return True  # Existing legacy configurations retain their owner semantics.
    names = server_cfg.get("tools", {}).get("accepted_names", list(accepted))
    if type(names) is not list or info.name not in names:
        return False
    old = accepted.get(info.name)
    return isinstance(old, dict) and all(old.get(key) == value for key, value in (
        ("description", info.description), ("input_schema", info.input_schema), ("effect", info.effect)))


def _normalize_tools(server_name: str, server_cfg: dict[str, Any], tools: list[Any]) -> dict[str, McpToolInfo]:
    tool_cfg = server_cfg.get("tools", {}) if isinstance(server_cfg, dict) else {}
    saved_enabled = dict(tool_cfg.get("enabled") or {})
    include = set(tool_cfg.get("include") or [])
    exclude = set(tool_cfg.get("exclude") or [])
    approval_overrides = set(tool_cfg.get("require_approval") or [])
    allowed = set(tool_cfg.get("run_without_asking") or [])
    normalized: dict[str, McpToolInfo] = {}
    for tool in tools:
        tool_name = str(_tool_attr(tool, "name", default="") or "").strip()
        if not tool_name:
            continue
        if include and tool_name not in include:
            continue
        if tool_name in exclude:
            continue
        description = str(_tool_attr(tool, "description", default="") or "")
        title = _tool_attr(tool, "title", default="") or _annotation_title(tool)
        view, visibility = _view_meta(tool)
        schema = _tool_attr(tool, "inputSchema", "input_schema", default={}) or {}
        destructive = is_destructive_tool(tool_name, description, tool)
        effect = classify_tool_effect(tool_name, description, tool)
        enabled = bool(saved_enabled.get(tool_name, tool_enabled_by_default(destructive or effect in {"unknown", "mutation"})))
        recorded = (tool_cfg.get("catalog") or {}).get(tool_name)
        locked = destructive or effect == "unknown" or (isinstance(recorded, dict) and recorded_locked(recorded))
        requires = asks_first(tool_name, destructive, effect, approval_overrides, allowed) or locked  # Locked asks in Ask too.
        normalized[tool_name] = McpToolInfo(
            server_name=server_name,
            name=tool_name,
            prefixed_name=prefixed_tool_name(server_name, tool_name),
            description=description or f"MCP tool {tool_name} from {server_name}",
            input_schema=schema if isinstance(schema, dict) else {},
            enabled=enabled,
            destructive=destructive,
            requires_approval=requires,
            source=dict(server_cfg.get("source") or {}),
            effect=effect,
            title=" ".join(str(title or "").split())[:96],
            ui=view,
            visibility=visibility,
            locked=locked,
            annotations=hints(tool),
        )
    for info in normalized.values():
        info.enabled = info.enabled and _accepted_tool_matches(server_cfg, info)
    return normalized


def _sync_catalog_from_config(config: dict[str, Any] | None = None) -> None:
    cfg = config or _get_effective_config()
    servers_cfg = cfg.get("servers", {}) if isinstance(cfg.get("servers"), dict) else {}
    with _runtime_lock:
        for server_name, tools in _catalog.items():
            server_cfg = servers_cfg.get(server_name, {}) if isinstance(servers_cfg.get(server_name, {}), dict) else {}
            tools_cfg = server_cfg.get("tools", {}) if isinstance(server_cfg.get("tools"), dict) else {}
            enabled_map = dict(tools_cfg.get("enabled") or {})
            approval_overrides = set(tools_cfg.get("require_approval") or [])
            allowed = set(tools_cfg.get("run_without_asking") or [])
            for info in tools.values():
                effect = info.effect or classify_tool_effect(info.name, info.description)
                info.enabled = bool(enabled_map.get(info.name, tool_enabled_by_default(
                    info.destructive or effect in {"unknown", "mutation"}))) and _accepted_tool_matches(server_cfg, info)
                recorded = (tools_cfg.get("catalog") or {}).get(info.name)
                info.locked = info.destructive or effect == "unknown" or (isinstance(recorded, dict)
                                                                          and recorded_locked(recorded))
                info.requires_approval = (asks_first(info.name, info.destructive, effect, approval_overrides, allowed)
                                          or info.locked)
            status = _statuses.get(server_name)
            if status:
                status.tool_count = len(tools)
                status.enabled_tool_count = sum(1 for info in tools.values() if info.enabled)
                status.destructive_tool_count = sum(1 for info in tools.values() if info.destructive)


def _json_schema_type(schema: dict[str, Any]) -> Any:
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((item for item in schema_type if item != "null"), None)
    if schema_type == "integer":
        return int
    if schema_type == "number":
        return float
    if schema_type == "boolean":
        return bool
    if schema_type == "array":
        items = schema.get("items")
        item_type = _json_schema_type(items) if isinstance(items, dict) else Any
        return list[item_type]
    if schema_type == "object":
        return dict[str, Any]
    return str if schema_type == "string" else Any


def _schema_to_model(tool_info: McpToolInfo) -> type[BaseModel]:
    schema = tool_info.input_schema or {}
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict):
        return _GenericArgs
    required = set(schema.get("required") or [])
    fields: dict[str, tuple[Any, Any]] = {}
    for field_name, spec in properties.items():
        if not isinstance(spec, dict) or not str(field_name).isidentifier():
            return _GenericArgs
        field_type = _json_schema_type(spec)
        default = ... if field_name in required else spec.get("default", None)
        fields[str(field_name)] = (field_type, Field(default, description=str(spec.get("description") or "")))
    try:
        return create_model(f"{tool_info.prefixed_name}_Args", **fields)
    except Exception:
        return _GenericArgs


class McpServerRuntime:
    def __init__(self, name: str, cfg: dict[str, Any]) -> None:
        self.name = name
        self.cfg = cfg
        self._redact: tuple[str, ...] = ()  # Secret values this connection was given, never shown in its errors.
        self._stderr: _StderrTail | None = None
        self.runtime_id = str(uuid.uuid4())
        self.state = "not_started"
        self.session: Any = None
        self.exit_stack: AsyncExitStack | None = None
        self.stop_event: asyncio.Event | None = None
        self._session_lock = asyncio.Lock()
        self._start_task: asyncio.Task | None = None
        self._start_admitted = False
        self._started = asyncio.Event()
        self._stop_requested = threading.Event()
        self._stop_future: concurrent.futures.Future | None = None
        self.cleanup_complete = False
        self._cleanup_failed = False
        self._cancel_requested = False
        self._launch_validate: Callable[[], None] | None = None
        self._launch_future: concurrent.futures.Future | None = None
        self._ready = threading.Event()
        self._finished = threading.Event()
        self._connected_admitted = False
        self._probe_result: dict[str, Any] | None = None
        self._before_release: Callable[["McpServerRuntime"], None] | None = None
        self._release_confirmed = True
        self._release_inflight = False
        self._release_epoch = 0
        self._child_pid: int | None = None  # The local program this connection started, if any.

    def _confirm_release(self) -> bool:
        """Persist exact completion before forgetting a client-owned transport."""
        with _runtime_lock:
            if not self.cleanup_complete:
                return False
            if self._before_release is None or self._release_confirmed:
                return True
            if self._release_inflight:
                return False
            self._release_inflight = True
            epoch = self._release_epoch
        try:
            self._before_release(self)
            with _runtime_lock:
                self._release_confirmed = self._release_epoch == epoch
                return self._release_confirmed
        except Exception:
            return False  # Transport is closed; only its durable receipt needs retry.
        finally:
            with _runtime_lock:
                self._release_inflight = False

    def _validate_launch(self) -> None:
        if self._stop_requested.is_set():
            raise asyncio.CancelledError
        with _runtime_lock:
            if self._start_admitted and _servers.get(self.name) is not self:
                raise RuntimeError("MCP runtime reservation changed")
        if self._launch_validate is not None:
            self._launch_validate()

    def _status(self, **updates: Any) -> None:
        if "status" in updates:
            self.state = updates["status"]
        with _runtime_lock:
            current = _servers.get(self.name)
            if current is not None and current is not self:
                return
            _update_status(self.name, **updates)

    async def start(self) -> None:
        self._start_task = asyncio.current_task()
        self._started.set()
        self.stop_event = asyncio.Event()
        try:
            if self._stop_requested.is_set():
                return
            if not sdk_available():
                self._status(status="dependency_missing", last_error="Python package 'mcp' is not installed")
                return
            self._status(status="connecting", enabled=True, transport=self.cfg.get("transport", "stdio"), last_error="")
            self._forget_stderr()
            async with asyncio.timeout(float(self.cfg.get("connect_timeout", 30))):
                self._validate_launch()
                await self._connect()
                self._validate_launch()
                await self._discover_tools()
                self._validate_launch()
            self._connected_admitted = True
            self._ready.set()
            await self.stop_event.wait()
        except asyncio.CancelledError:
            raise
        except McpStdioCommandNotFound as exc:
            self._status(status="dependency_missing", last_error=redact(str(exc), self._redact))
            log_event("mcp.server.dependency_missing", level=logging.WARNING, server=self.name, error=redact(str(exc), self._redact))
        except Exception as exc:
            self._status(status="failed", last_error=redact(_failure(exc), self._redact))
            await self._keep_stderr()
            log_event("mcp.server.failed", level=logging.WARNING, server=self.name, error=redact(str(exc), self._redact),
                      traceback=redact(traceback.format_exc(), self._redact))
        finally:
            await self.close()
            released = self._confirm_release()
            with _runtime_lock:
                if released and self._release_confirmed and _servers.get(self.name) is self:
                    _servers.pop(self.name, None)
            self._finished.set()
            self._ready.set()

    async def _connect(self) -> None:
        from row_bot.mcp_client.auth import transport_options
        launch_cfg, auth_options = transport_options(self.name, self.cfg, validate=self._validate_launch)
        self._redact = tuple(launch_cfg.pop("_redact", ()))  # Kept out of every error this connection reports.
        transport = str(self.cfg.get("transport") or "stdio")
        self.exit_stack = AsyncExitStack()
        if transport == "stdio":
            if StdioServerParameters is None or stdio_client is None:
                raise RuntimeError("MCP stdio transport is unavailable")
            command = str(self.cfg.get("command") or "").strip()
            if not command:
                raise RuntimeError("stdio MCP server requires a command")
            from row_bot.plugins.mcp import resolve_prepared_plugin_mcp_launch
            launch = resolve_prepared_plugin_mcp_launch(self.name, self.cfg,
                for_setup=getattr(self, "_temporary_setup", False) and self._launch_validate is not None)
            self._prepared_plugin_launch = launch
            if launch is not None:
                from row_bot.plugins.worker import _run_directory, _worker_environment
                env = _worker_environment(_run_directory(launch.plugin_id))
                env.update(launch.declared_env)
            else:
                env = ({key: value for key, value in os.environ.items() if key.upper() in
                        {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "LANG", "LC_ALL"}}
                       if self.cfg.get("environment_mode") == "minimal" else os.environ.copy())
                # The connection's variables with its declared inputs and saved keys filled in, for this launch only.
                env.update({str(k): str(v) for k, v in dict(launch_cfg.get("env") or {}).items()})
                env = apply_managed_runtime_env(self.cfg, env)
                if self.cfg.get("plugin_data"):
                    from row_bot.data_paths import get_row_bot_data_dir
                    from row_bot.package_files import contained_path
                    plugin_id = self.cfg.get("source", {}).get("plugin_id", "")
                    data = contained_path(get_row_bot_data_dir(create=False), "plugin_data/" + plugin_id)
                    if str(data) != self.cfg["plugin_data"]:
                        raise RuntimeError("plugin_data_owner_changed")
                    data.mkdir(parents=True, exist_ok=True)
            args = [str(arg) for arg in launch_cfg.get("args") or []]
            package_launch = None
            if self.cfg.get("environment_mode") == "minimal" or self.cfg.get("managed_launch"):
                # A locked package runs from its reviewed private folder; it is never fetched now.
                from row_bot.mcp_client import packages
                if packages.kind(self.cfg):
                    package_launch = packages.resolve_launch(self.cfg, args=packages.arguments(launch_cfg))
            if package_launch:
                command, args = package_launch
                env = packages.launch_environment(self.cfg, env)
                if packages.kind(self.cfg) in {"pypi", "mcpb"}:
                    env["PYTHONDONTWRITEBYTECODE"] = "1"  # The reviewed folder stays exactly as it was checked.
                if packages.kind(self.cfg) == "mcpb":
                    root = packages.bundle_root(self.cfg)
                    env = {key: value.replace("{bundle}", root) for key, value in env.items()}
            else:
                command = _resolve_stdio_command(command, env)
            if launch is not None:
                # Prepared workers retain their isolated HOME and runtime paths.
                # Only explicitly reviewed credential bindings augment that environment.
                secret_env = {item["name"] for item in self.cfg.get("auth", {}).get("bindings", [])
                    if item.get("kind") == "env"}
                env.update({str(k): str(v) for k, v in launch_cfg.get("env", {}).items() if k in secret_env})
            params = StdioServerParameters(
                command=command,
                args=args,
                env=env,
                cwd=self.cfg.get("cwd") or None,
            )
            spawning = _spawning.set(self)  # The program the SDK starts now is this connection's.
            self._stderr = _StderrTail()
            self.exit_stack.callback(self._stderr.release)
            try:
                read_stream, write_stream = await self.exit_stack.enter_async_context(
                    stdio_client(params, errlog=self._stderr.stream))
            finally:
                _spawning.reset(spawning)
            self._stderr.release()
        elif transport in {"streamable_http", "http", "streamable-http"}:
            if streamablehttp_client is None:
                raise RuntimeError("MCP Streamable HTTP transport is unavailable")
            url = str(launch_cfg.get("url") or "").strip()
            if not url:
                raise RuntimeError("HTTP MCP server requires a URL")
            read_stream, write_stream, _ = await self.exit_stack.enter_async_context(
                streamablehttp_client(url, headers=dict(launch_cfg.get("headers") or {}), **auth_options)
            )
        elif transport == "sse":
            if sse_client is None:
                raise RuntimeError("MCP SSE transport is unavailable")
            url = str(launch_cfg.get("url") or "").strip()
            if not url:
                raise RuntimeError("SSE MCP server requires a URL")
            read_stream, write_stream = await self.exit_stack.enter_async_context(
                sse_client(url, headers=dict(launch_cfg.get("headers") or {}), **auth_options)
            )
        else:
            raise RuntimeError(f"Unsupported MCP transport: {transport}")
        # The SDK's own session says it can show app views; any other (a test's fake) is used as it is.
        session_type = _AppsSession if ClientSession is _SDK_SESSION and _AppsSession is not None else ClientSession
        self.session = await self.exit_stack.enter_async_context(session_type(read_stream, write_stream))
        await asyncio.wait_for(self.session.initialize(), timeout=float(self.cfg.get("connect_timeout", 30)))
        self._status(status="connected", last_connected_at=_now(), last_error="")
        with _runtime_lock:
            _stderr_tails.pop(self.name, None)
        host = ""  # Only where it connected: an address's path, query or sign-in, or an argument, can hold a key.
        with contextlib.suppress(ValueError):
            address = urlsplit(str(self.cfg.get("url") or ""))
            host = f"{address.scheme}://{address.hostname}" if address.hostname else ""
        log_event("mcp.server.connected", server=self.name, transport=transport, host=host,
                  args=len(self.cfg.get("args") or []))

    async def _discover_tools(self) -> None:
        if not self.session:
            return
        session = self.session
        result = await asyncio.wait_for(session.list_tools(), timeout=float(self.cfg.get("connect_timeout", 30)))
        if self._launch_validate is not None:
            self._validate_launch()
        tools = list(getattr(result, "tools", result if isinstance(result, list) else []))
        normalized = _normalize_tools(self.name, self.cfg, tools)
        with _runtime_lock:
            if (self.session is not session or (self.stop_event is not None and self.stop_event.is_set())
                    or _servers.get(self.name, self) is not self):
                return  # An old discovery callback cannot revive a replaced runtime.
            _catalog[self.name] = normalized
            for name in [name for name, (server, tool) in _issued.items() if server == self.name and tool not in _HELPER_TITLES]:
                del _issued[name]  # Its tools are read from the new catalog; a gone one names no app.
        accepted = self.cfg.get("tools", {}).get("catalog")
        options = self.cfg.get("tools", {})
        expected = {name for name in options.get("accepted_names", list(accepted)) if name not in options.get("exclude", []) and (not options.get("include") or name in options["include"])} if isinstance(accepted, dict) else set(normalized)
        changed = isinstance(accepted, dict) and (expected != set(normalized) or any(not _accepted_tool_matches(self.cfg, info) for info in normalized.values()))
        self._status(
            status="error" if changed else "connected",
            tool_count=len(normalized),
            enabled_tool_count=sum(1 for info in normalized.values() if info.enabled),
            destructive_tool_count=sum(1 for info in normalized.values() if info.destructive),
            last_discovered_at=_now(),
            last_error="Tool catalog changed. Test and accept the current tools before using changed capabilities." if changed else "",
        )
        log_event("mcp.tools.discovered", server=self.name, tools=len(normalized))
        mcp_config.clear_agent_cache_if_loaded()

    async def call_tool(self, tool_name: str, arguments: dict[str, Any], *,
                        deadline: float | None = None,
                        validate: Callable[[], None] | None = None) -> str:
        deadline = deadline if deadline is not None else time.monotonic() + float(self.cfg.get("tool_timeout", 120))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("MCP deadline expired")
        async with asyncio.timeout(remaining):
            async with self._session_lock:
                if validate is not None:
                    validate()
                if deadline <= time.monotonic():
                    raise TimeoutError("MCP deadline expired")
                if not self.session:
                    raise RuntimeError(f"MCP server '{self.name}' is not connected")
                output_limit = int(self.cfg.get("output_limit", 24000))
                log_event("mcp.tool.call", server=self.name, tool=tool_name)
                result = await self.session.call_tool(tool_name, arguments or {})
                return normalize_call_result(result, output_limit=output_limit)

    async def read_view(self, uri: str, *, deadline: float) -> dict[str, Any]:
        """One ``ui://`` view's HTML and its ``_meta.ui`` (CSP, border), from this connection only."""
        import base64
        async with asyncio.timeout(max(0.0, deadline - time.monotonic())):
            async with self._session_lock:
                if not self.session:
                    raise RuntimeError(f"MCP server '{self.name}' is not connected")
                result = await self.session.read_resource(uri)
        for item in getattr(result, "contents", None) or []:
            if getattr(item, "mimeType", None) != VIEW_MEDIA_TYPE or str(getattr(item, "uri", "")) != uri:
                continue
            text = getattr(item, "text", None)
            data = text.encode("utf-8") if isinstance(text, str) else base64.b64decode(getattr(item, "blob", "") or "",
                                                                                      validate=True)
            if not data or len(data) > MAX_VIEW_BYTES:
                raise ValueError("view_too_large")
            meta = getattr(item, "meta", None) or {}
            return {"html": data.decode("utf-8"), "meta": meta.get("ui") if isinstance(meta.get("ui"), dict) else {}}
        raise ValueError("view_unavailable")

    async def call_tool_for_view(self, tool_name: str, arguments: dict[str, Any], *, deadline: float,
                                 validate: Callable[[], None] | None = None) -> dict[str, Any]:
        """A tool call for an app's own view: its result as the view expects it (content, structured content).
        ``validate`` runs just before the call, under the session lock: the access the call was decided under."""
        async with asyncio.timeout(max(0.0, deadline - time.monotonic())):
            async with self._session_lock:
                if not self.session:
                    raise RuntimeError(f"MCP server '{self.name}' is not connected")
                if validate is not None:
                    validate()
                log_event("mcp.tool.call", server=self.name, tool=tool_name, initiator="view")
                result = await self.session.call_tool(tool_name, arguments or {})
        dumped = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        found = {key: dumped[key] for key in ("content", "structuredContent", "isError") if key in dumped}
        if len(json.dumps(found, default=str)) > MAX_VIEW_BYTES:
            return {"content": [{"type": "text", "text": "The result was too large to show here."}], "isError": True}
        return found

    async def list_resources(self) -> str:
        if not self.session:
            raise RuntimeError(f"MCP server '{self.name}' is not connected")
        if not hasattr(self.session, "list_resources"):
            return "This MCP server/session does not expose resource listing."
        result = await asyncio.wait_for(self.session.list_resources(), timeout=float(self.cfg.get("tool_timeout", 120)))
        resources = list(getattr(result, "resources", []) or [])
        if not resources:
            return "No MCP resources found."
        lines = ["MCP resources:"]
        for resource in resources:
            uri = getattr(resource, "uri", "")
            name = getattr(resource, "name", "") or uri
            description = getattr(resource, "description", "") or ""
            lines.append(f"- {name}: {uri}" + (f" — {description}" if description else ""))
        return "\n".join(lines)

    async def read_resource(self, uri: str) -> str:
        if not self.session:
            raise RuntimeError(f"MCP server '{self.name}' is not connected")
        result = await asyncio.wait_for(self.session.read_resource(uri), timeout=float(self.cfg.get("tool_timeout", 120)))
        contents = list(getattr(result, "contents", []) or [])
        if not contents:
            return "MCP resource returned no content."
        parts: list[str] = []
        for item in contents:
            text = getattr(item, "text", None)
            if text is not None:
                parts.append(str(text))
            else:
                mime = getattr(item, "mimeType", "") or getattr(item, "mime_type", "") or "binary"
                parts.append(f"[MCP resource content omitted: {mime}]")
        output = "\n\n".join(parts)
        limit = int(self.cfg.get("output_limit", 24000))
        if len(output) > limit:
            output = output[:limit] + f"\n\n[Truncated MCP resource at {limit} characters]"
        return output

    async def list_prompts(self) -> str:
        if not self.session:
            raise RuntimeError(f"MCP server '{self.name}' is not connected")
        if not hasattr(self.session, "list_prompts"):
            return "This MCP server/session does not expose prompt listing."
        result = await asyncio.wait_for(self.session.list_prompts(), timeout=float(self.cfg.get("tool_timeout", 120)))
        prompts = list(getattr(result, "prompts", []) or [])
        if not prompts:
            return "No MCP prompts found."
        lines = ["MCP prompts:"]
        for prompt in prompts:
            name = getattr(prompt, "name", "")
            description = getattr(prompt, "description", "") or ""
            lines.append(f"- {name}" + (f": {description}" if description else ""))
        return "\n".join(lines)

    async def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        if not self.session:
            raise RuntimeError(f"MCP server '{self.name}' is not connected")
        result = await asyncio.wait_for(self.session.get_prompt(name, arguments or {}), timeout=float(self.cfg.get("tool_timeout", 120)))
        messages = list(getattr(result, "messages", []) or [])
        description = getattr(result, "description", "") or ""
        parts = [description] if description else []
        for message in messages:
            role = getattr(message, "role", "message")
            content = getattr(message, "content", "")
            text = getattr(content, "text", None) if content is not None else None
            parts.append(f"[{role}] {text if text is not None else content}")
        return "\n\n".join(parts) or "MCP prompt returned no messages."

    async def _keep_stderr(self) -> None:
        """Keep what a program that failed to start last wrote, its secrets masked, for the person to read."""
        if self._stderr is not None:
            lines = await asyncio.to_thread(self._stderr.lines, 1.0)  # Its last words, once it has exited.
            masked = (*self._redact, *_launch_values(self.cfg))
            with _runtime_lock:
                _stderr_tails[self.name] = [redact(line, masked) for line in lines]

    def _forget_stderr(self) -> None:
        """A new start: an earlier failure's lines never describe it."""
        with _runtime_lock:
            _stderr_tails.pop(self.name, None)

    async def close(self) -> None:
        if self._start_task is not None and self._start_task is not asyncio.current_task() and not self._start_task.done():
            await self.stop()
            return
        if self.cleanup_complete or self._cleanup_failed:
            return
        self._connected_admitted = False
        self.session = None
        if self.exit_stack:
            try:
                await self.exit_stack.aclose()
            except (asyncio.CancelledError, Exception):
                # AsyncExitStack may already have popped a failing callback.
                # Calling it again cannot prove that transport was cleaned up.
                self._cleanup_failed = True
                self._status(status="cleanup_incomplete", last_error="MCP transport cleanup is unconfirmed")
                return
        self.exit_stack = None
        self.cleanup_complete = True
        if self._child_pid is not None:  # The SDK has ended the program and its tree.
            from row_bot import owned_processes
            owned_processes.forget(owned_processes.ledger_path(APP_PROCESSES), self._child_pid)
            self._child_pid = None
        with _runtime_lock:
            current_status = _statuses.get(self.name)
            preserve_status = current_status and current_status.status in {"failed", "dependency_missing"}
        if not preserve_status:
            self._status(status="stopped")

    async def stop(self) -> None:
        self._stop_requested.set()
        if self.cleanup_complete:
            self._confirm_release()
            return
        if not self.cleanup_complete and not self._cleanup_failed:
            self._status(status="stopping")
        if self.stop_event and not self.stop_event.is_set():
            self.stop_event.set()
        if self._start_admitted and self._start_task is None:
            await self._started.wait()
        task = self._start_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            if not self._cancel_requested:
                self._cancel_requested = True
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(task)
            return
        await self.close()


def _record_app_processes() -> None:
    """Note each local app program the MCP SDK starts for a connection (its pid and creation time), so a
    later Row-Bot can stop it if this one dies without ending it."""
    try:
        from mcp.client import stdio as sdk_stdio
    except Exception:
        return
    original = getattr(sdk_stdio, "_create_platform_compatible_process", None)
    if original is None or getattr(original, "row_bot_records", False):
        return

    async def create(*args: Any, **kwargs: Any) -> Any:
        process = await original(*args, **kwargs)
        server, pid = _spawning.get(), getattr(process, "pid", None)
        if server is not None and isinstance(pid, int):
            from row_bot import owned_processes
            server._child_pid = pid
            if not owned_processes.record(owned_processes.ledger_path(APP_PROCESSES), pid, server=server.name):
                log_event("mcp.server.unrecorded_process", level=logging.WARNING, server=server.name)
        return process
    create.row_bot_records = True  # type: ignore[attr-defined]
    sdk_stdio._create_platform_compatible_process = create


def cleanup_app_processes() -> int:
    """Stop local app programs (with their process trees) that a Row-Bot which has since ended started and
    recorded; nothing else. Returns how many were stopped."""
    from row_bot import owned_processes
    stopped = owned_processes.cleanup(owned_processes.ledger_path(APP_PROCESSES), tree=True)
    if stopped:
        log_event("mcp.server.orphans_stopped", count=stopped)
    return stopped


_record_app_processes()


def discover_enabled_servers() -> None:
    """Start or refresh enabled MCP servers without blocking startup."""
    cfg = _get_effective_config()
    if not cfg.get("enabled"):
        with _runtime_lock:
            running_names = list(_servers)
        for name in running_names:
            stop_server(name)
        with _runtime_lock:
            _catalog.clear()
            for name, server_cfg in cfg.get("servers", {}).items():
                if name not in _servers:
                    _statuses[name] = McpServerStatus(name=name, enabled=bool(server_cfg.get("enabled")), status="global_disabled")
        return
    if not sdk_available():
        log_event("mcp.dependency_missing", level=logging.WARNING, package="mcp")
        for name, server_cfg in cfg.get("servers", {}).items():
            if server_cfg.get("enabled"):
                _update_status(name, enabled=True, status="dependency_missing", last_error="Python package 'mcp' is not installed")
        return
    desired = {name: server for name, server in cfg.get("servers", {}).items() if server.get("enabled")}
    with _runtime_lock:
        existing = set(_servers)
    for stale in existing - set(desired):
        stop_server(stale)
    for name, server_cfg in desired.items():
        with _runtime_lock:
            running = name in _servers
            status = _statuses.get(name)
            failed_until_refresh = status is not None and status.status == "failed"
            if failed_until_refresh or running:
                continue
            runtime = McpServerRuntime(name, server_cfg)
            runtime._start_admitted = True
            _servers[name] = runtime
            _statuses[name] = McpServerStatus(
                name=name,
                enabled=True,
                status="connecting",
                transport=str(server_cfg.get("transport", "stdio")),
                source=dict(server_cfg.get("source") or {}),
            )
        coroutine = runtime.start()
        try:
            _schedule(coroutine)
        except Exception:
            coroutine.close()
            runtime._stop_requested.set()
            runtime._status(status="cleanup_incomplete", last_error="MCP start admission is unconfirmed")


def stop_server(name: str) -> None:
    _stop_server(name, expected_runtime_id=None, wait_seconds=5)


def disconnect_plugin_servers(plugin_id: str) -> None:
    """Withdraw only this package's live MCP owners, preserving cleanup proof."""
    with _runtime_lock:
        owned = [(name, server.runtime_id) for name, server in _servers.items()
                 if server.cfg.get("source", {}).get("kind") == "plugin"
                 and server.cfg["source"].get("plugin_id") == plugin_id]
    for name, runtime_id in owned:
        result = stop_server_owned(name, runtime_id)
        if result["state"] != "stopped":
            raise RuntimeError("plugin_mcp_cleanup_incomplete")


def _stop_server(name: str, *, expected_runtime_id: str | None, wait_seconds: float) -> McpServerRuntime | None:
    with _runtime_lock:
        runtime = _servers.get(name)
        if expected_runtime_id is not None and (runtime is None or runtime.runtime_id != expected_runtime_id):
            raise ValueError("mcp_runtime_identity_changed")
        _catalog.pop(name, None)
        if runtime is None:
            return
        runtime._stop_requested.set()
        if not runtime.cleanup_complete and not runtime._cleanup_failed:
            runtime._status(status="stopping")
        future = runtime._stop_future
        if future is None or future.done():
            coroutine = runtime.stop()
            try:
                future = runtime._stop_future = _schedule(coroutine)
            except Exception:
                coroutine.close()
                runtime._status(status="cleanup_incomplete", last_error="MCP stop admission is unconfirmed")
                return runtime
    try:
        future.result(timeout=wait_seconds)
    except Exception:
        runtime._status(status="cleanup_incomplete", last_error="MCP cleanup has not returned")
    with _runtime_lock:
        if runtime.cleanup_complete and runtime._release_confirmed and _servers.get(name) is runtime:
            _servers.pop(name, None)
    return runtime


def stop_server_owned(name: str, expected_runtime_id: str, *, wait_seconds: float = 5) -> dict[str, Any]:
    """Stop only the exact saved owner; timeout never discards its reservation."""
    if type(wait_seconds) not in (int, float) or not 0 <= wait_seconds <= 5:
        raise ValueError("invalid_mcp_cleanup_wait")
    runtime = _stop_server(name, expected_runtime_id=expected_runtime_id, wait_seconds=wait_seconds)
    assert runtime is not None
    quiesced = runtime.cleanup_complete and (runtime._finished.is_set() or not runtime._start_admitted)
    return {"runtime_id": expected_runtime_id,
            "state": "stopped" if quiesced and runtime._release_confirmed else "cleanup_incomplete",
            "session_quiesced": quiesced, "receipt_confirmed": runtime._release_confirmed}


def get_server_lifecycle(name: str, *, expected_runtime_id: str | None = None) -> dict[str, Any]:
    """Passive exact-owner metadata; absence alone does not prove prior cleanup."""
    with _runtime_lock:
        runtime = _servers.get(name)
        if runtime is None:
            return {"runtime_id": None, "state": "missing", "session_quiesced": None}
        if expected_runtime_id is not None and runtime.runtime_id != expected_runtime_id:
            raise ValueError("mcp_runtime_identity_changed")
        status = _statuses.get(name)
        states = {"connecting", "connected", "stopping", "stopped", "failed", "dependency_missing", "cleanup_incomplete"}
        state = status.status if status and type(status.status) is str and status.status in states else "connecting"
        if runtime.cleanup_complete and not runtime._release_confirmed:
            state = "cleanup_incomplete"
        return {"runtime_id": runtime.runtime_id, "state": state,
                "session_quiesced": runtime.cleanup_complete and runtime._finished.is_set()}


def launch_server_owned(name: str, cfg: dict[str, Any], *, before_start: Callable[[str], None],
                        validate: Callable[[], None], temporary: bool = False,
                        before_release: Callable[[McpServerRuntime], None] | None = None) -> McpServerRuntime:
    """Reserve, checkpoint, then schedule one owner without holding locks over IO."""
    validate()
    runtime = McpServerRuntime(name, copy.deepcopy(cfg))
    runtime._launch_validate = validate
    runtime._temporary_setup = temporary
    runtime._before_release = before_release
    runtime._release_confirmed = before_release is None
    with _runtime_lock:
        if name in _servers:
            raise ValueError("mcp_runtime_busy")
        runtime._start_admitted = True
        _servers[name] = runtime
        runtime._status(status="connecting", enabled=bool(cfg.get("enabled", False)), transport=str(cfg.get("transport", "stdio")))
    try:
        before_start(runtime.runtime_id)
        validate()
        if runtime._stop_requested.is_set():
            raise ValueError("mcp_runtime_cancelled")
        with _runtime_lock:
            if _servers.get(name) is not runtime:
                raise ValueError("mcp_runtime_identity_changed")
    except BaseException:
        # No coroutine was scheduled: this reservation cannot have connected.
        runtime._stop_requested.set()
        runtime.cleanup_complete = True
        runtime._finished.set()
        runtime._ready.set()
        runtime._started.set()
        with _runtime_lock:
            if _servers.get(name) is runtime:
                _servers.pop(name, None)
        raise
    coroutine = probe_server_async(name, runtime.cfg, _runtime=runtime) if temporary else runtime.start()
    try:
        runtime._launch_future = _schedule(coroutine)
    except Exception:
        coroutine.close()
        runtime._stop_requested.set()
        runtime._status(status="cleanup_incomplete", last_error="MCP launch admission is unconfirmed")
        raise
    return runtime


def reconcile_server_release_owned(name: str, expected_runtime_id: str) -> dict[str, Any]:
    """Retry only the original completion receipt; never connect or close again."""
    with _runtime_lock:
        runtime = _servers.get(name)
        if runtime is None or runtime.runtime_id != expected_runtime_id:
            raise ValueError("mcp_runtime_identity_changed")
    confirmed = runtime._confirm_release()
    with _runtime_lock:
        if confirmed and runtime._release_confirmed and runtime._finished.is_set() and _servers.get(name) is runtime:
            _servers.pop(name, None)
        confirmed = confirmed and runtime._release_confirmed
    return {"runtime_id": expected_runtime_id, "receipt_confirmed": confirmed,
            "session_quiesced": runtime.cleanup_complete and runtime._finished.is_set()}


def shutdown() -> None:
    """Stop MCP child sessions and runtime loop. Safe to call repeatedly."""
    global _loop, _thread
    with _runtime_lock:
        names = list(_servers)
    for name in names:
        stop_server(name)
    with _runtime_lock:
        if _servers:
            log_event("mcp.runtime.shutdown_incomplete", level=logging.WARNING)
            return
        loop, thread = _loop, _thread
    if loop and loop.is_running():
        loop.call_soon_threadsafe(loop.stop)
    if thread and thread is not threading.current_thread():
        thread.join(timeout=5)
    with _runtime_lock:
        if thread and thread.is_alive():
            log_event("mcp.runtime.shutdown_incomplete", level=logging.WARNING)
            return
        if _loop is loop and _thread is thread:
            _loop = None
            _thread = None
    if loop and not loop.is_running() and not loop.is_closed():
        loop.close()
    log_event("mcp.runtime.shutdown")


async def probe_server_async(name: str, server_cfg: dict[str, Any], *,
                             _runtime: McpServerRuntime | None = None) -> dict[str, Any]:
    """Connect to a server temporarily and return discovered tools/status."""
    runtime = _runtime or McpServerRuntime(name, server_cfg)
    with _runtime_lock:
        if name in _servers and _servers[name] is not runtime:
            return {"ok": False, "error": "MCP server already has an active or draining connection", "tools": []}
        runtime._start_admitted = True
        runtime._start_task = asyncio.current_task()
        runtime._started.set()
        _servers[name] = runtime
    outcome = None
    try:
        runtime._forget_stderr()
        async with asyncio.timeout(float(server_cfg.get("connect_timeout", 30))):
            runtime._validate_launch()
            await runtime._connect()
            runtime._validate_launch()
            result = await runtime.session.list_tools()
            runtime._validate_launch()
        tools = list(getattr(result, "tools", result if isinstance(result, list) else []))
        normalized = _normalize_tools(name, server_cfg, tools)
        outcome = {
            "ok": True,
            "tools": [info.__dict__ for info in normalized.values()],
            "tool_count": len(normalized),
            "destructive_tool_count": sum(1 for info in normalized.values() if info.destructive),
        }
    except Exception as exc:
        outcome = {"ok": False, "error": redact(str(exc), getattr(runtime, "_redact", ())), "tools": []}
        await runtime._keep_stderr()
    finally:
        await runtime.close()
        runtime._probe_result = outcome
        released = runtime._confirm_release()
        with _runtime_lock:
            if released and runtime._release_confirmed and _servers.get(name) is runtime:
                _servers.pop(name, None)
        runtime._finished.set()
        runtime._ready.set()
    if not runtime.cleanup_complete:
        return {"ok": False, "error": "MCP transport cleanup is unconfirmed", "tools": []}
    return outcome


def probe_server(name: str, server_cfg: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
    if not sdk_available():
        return {"ok": False, "error": "Python package 'mcp' is not installed", "tools": []}
    future = _schedule(probe_server_async(name, server_cfg))
    wait_seconds = timeout or float(server_cfg.get("connect_timeout", 30)) + 5
    try:
        return future.result(timeout=wait_seconds)
    except concurrent.futures.CancelledError:
        return {
            "ok": False,
            "error": "MCP connection ended before the server completed its handshake.",
            "tools": [],
        }
    except concurrent.futures.TimeoutError:
        future.cancel()
        return {
            "ok": False,
            "error": f"MCP connection timed out after {wait_seconds:g} seconds.",
            "tools": [],
        }


@dataclass(frozen=True)
class _BoundAuthority:
    runtime: Any
    revision: str


def _authority_revision(server_name: str, cfg: dict[str, Any], tool_name: str = "") -> str:
    """Private digest only; a bound schema/approval decision cannot adopt edits."""
    info = _catalog.get(server_name, {}).get(tool_name) if tool_name else None
    value = {
        "global": {key: value for key, value in cfg.items() if key != "servers"},
        "server": cfg.get("servers", {}).get(server_name, {}),
        "tool": asdict(info) if info is not None else None,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _bind_authority(server_name: str, tool_name: str = "") -> _BoundAuthority:
    cfg = _get_effective_config()
    with _runtime_lock:
        return _BoundAuthority(_servers.get(server_name, object()),
                               _authority_revision(server_name, cfg, tool_name))


def _validate_bound_runtime(server_name: str, expected: Any, *, tool_name: str = "",
                            feature: str = "") -> None:
    cfg = _get_effective_config()
    server_cfg = cfg.get("servers", {}).get(server_name, {})
    if not cfg.get("enabled") or not server_cfg.get("enabled"):
        raise RuntimeError("MCP capability was revoked")
    from row_bot.plugins.mcp import resolve_prepared_plugin_mcp_launch
    prepared = resolve_prepared_plugin_mcp_launch(server_name, server_cfg)
    with _runtime_lock:
        bound_runtime = expected.runtime if isinstance(expected, _BoundAuthority) else expected
        if (isinstance(bound_runtime, McpServerRuntime) and bound_runtime._start_admitted
                and (not bound_runtime._connected_admitted or bound_runtime._stop_requested.is_set()
                     or bound_runtime._cleanup_failed)):
            raise RuntimeError("MCP connection is not admitted for execution")
        if _servers.get(server_name) is not bound_runtime:
            raise RuntimeError("MCP registration was replaced")
        if getattr(bound_runtime, "_prepared_plugin_launch", None) != prepared:
            raise RuntimeError("MCP plugin environment changed; reconnect before execution")
        if tool_name:
            info = _catalog.get(server_name, {}).get(tool_name)
            options = server_cfg.get("tools", {})
            if (info is None or not _accepted_tool_matches(server_cfg, info) or tool_name in options.get("exclude", [])
                    or (options.get("include") and tool_name not in options["include"])
                    or not options.get("enabled", {}).get(tool_name, tool_enabled_by_default(info.destructive or info.effect == "unknown"))):
                raise RuntimeError("MCP capability was revoked")
        if feature and not server_cfg.get("tools", {}).get(feature):
            raise RuntimeError("MCP capability was revoked")
        if (isinstance(expected, _BoundAuthority)
                and _authority_revision(server_name, cfg, tool_name) != expected.revision):
            raise RuntimeError("MCP policy changed; refresh the capability before execution")


def _call_tool_sync(server_name: str, tool_name: str, kwargs: dict[str, Any], *, expected: Any = None) -> str:
    with _runtime_lock:
        runtime = _servers.get(server_name)
    if not runtime:
        raise RuntimeError(f"MCP server '{server_name}' is not running")
    timeout = float(runtime.cfg.get("tool_timeout", 120))
    deadline = time.monotonic() + timeout
    validate = None
    if expected is not None:
        validate = lambda: _validate_bound_runtime(server_name, expected, tool_name=tool_name)
        validate()
    future = _schedule(runtime.call_tool(tool_name, kwargs, deadline=deadline, validate=validate))
    return _future_result_with_generation_cancellation(
        future,
        timeout=max(0, deadline - time.monotonic()),
        stopped_message="MCP tool call stopped by user.",
        label=f"mcp_tool.{server_name}.{tool_name}.cancel",
    )


def _make_tool_func(server_name: str, tool_name: str, *, enforce_policy: bool = False) -> Callable[..., str]:
    expected = _bind_authority(server_name, tool_name) if enforce_policy else None
    def _run(**kwargs: Any) -> str:
        return _call_tool_sync(server_name, tool_name, kwargs, expected=expected)

    return _run


async def _authorized_operation(server_name: str, expected: Any, feature: str,
                                operation: Callable[[], Any], deadline: float) -> str:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("MCP deadline expired")
    async with asyncio.timeout(remaining):
        if expected is not None:
            _validate_bound_runtime(server_name, expected, feature=feature)
        return await operation()


def _make_resource_list_func(server_name: str, *, enforce_policy: bool = False) -> Callable[[], str]:
    expected = _bind_authority(server_name) if enforce_policy else None
    def _run() -> str:
        with _runtime_lock:
            runtime = _servers.get(server_name)
        if not runtime:
            raise RuntimeError(f"MCP server '{server_name}' is not running")
        deadline = time.monotonic() + float(runtime.cfg.get("tool_timeout", 120))
        future = _schedule(_authorized_operation(server_name, expected, "resources_enabled", runtime.list_resources, deadline))
        return _future_result_with_generation_cancellation(
            future,
            timeout=max(0, deadline - time.monotonic()),
            stopped_message="MCP resource listing stopped by user.",
            label=f"mcp_resources.{server_name}.cancel",
        )
    return _run


def _make_resource_read_func(server_name: str, *, enforce_policy: bool = False) -> Callable[..., str]:
    expected = _bind_authority(server_name) if enforce_policy else None
    def _run(uri: str) -> str:
        with _runtime_lock:
            runtime = _servers.get(server_name)
        if not runtime:
            raise RuntimeError(f"MCP server '{server_name}' is not running")
        deadline = time.monotonic() + float(runtime.cfg.get("tool_timeout", 120))
        future = _schedule(_authorized_operation(server_name, expected, "resources_enabled", lambda: runtime.read_resource(uri), deadline))
        return _future_result_with_generation_cancellation(
            future,
            timeout=max(0, deadline - time.monotonic()),
            stopped_message="MCP resource read stopped by user.",
            label=f"mcp_resource.{server_name}.cancel",
        )
    return _run


def _make_prompt_list_func(server_name: str, *, enforce_policy: bool = False) -> Callable[[], str]:
    expected = _bind_authority(server_name) if enforce_policy else None
    def _run() -> str:
        with _runtime_lock:
            runtime = _servers.get(server_name)
        if not runtime:
            raise RuntimeError(f"MCP server '{server_name}' is not running")
        deadline = time.monotonic() + float(runtime.cfg.get("tool_timeout", 120))
        future = _schedule(_authorized_operation(server_name, expected, "prompts_enabled", runtime.list_prompts, deadline))
        return _future_result_with_generation_cancellation(
            future,
            timeout=max(0, deadline - time.monotonic()),
            stopped_message="MCP prompt listing stopped by user.",
            label=f"mcp_prompts.{server_name}.cancel",
        )
    return _run


def _make_prompt_get_func(server_name: str, *, enforce_policy: bool = False) -> Callable[..., str]:
    expected = _bind_authority(server_name) if enforce_policy else None
    def _run(name: str, arguments: dict[str, Any] | None = None) -> str:
        with _runtime_lock:
            runtime = _servers.get(server_name)
        if not runtime:
            raise RuntimeError(f"MCP server '{server_name}' is not running")
        deadline = time.monotonic() + float(runtime.cfg.get("tool_timeout", 120))
        future = _schedule(_authorized_operation(server_name, expected, "prompts_enabled", lambda: runtime.get_prompt(name, arguments), deadline))
        return _future_result_with_generation_cancellation(
            future,
            timeout=max(0, deadline - time.monotonic()),
            stopped_message="MCP prompt read stopped by user.",
            label=f"mcp_prompt.{server_name}.cancel",
        )
    return _run


def _allow_names_set(allow_names: Iterable[str] | None) -> set[str] | None:
    if allow_names is None:
        return None
    return {str(name) for name in allow_names if str(name or "").strip()}


def _mcp_runtime_name_allowed(name: str, allow: set[str] | None) -> bool:
    return allow is None or "mcp" in allow or str(name or "") in allow


def _source_plugin_allowed(info: McpToolInfo, plugin_id: str | None) -> bool:
    if plugin_id is None:
        return True
    source = info.source or {}
    return source.get("kind") == "plugin" and source.get("plugin_id") == plugin_id


def _server_source_plugin_allowed(server_cfg: dict[str, Any], plugin_id: str | None) -> bool:
    if plugin_id is None:
        return True
    source = server_cfg.get("source", {}) if isinstance(server_cfg, dict) else {}
    return source.get("kind") == "plugin" and source.get("plugin_id") == plugin_id


def get_langchain_tools(
    allow_names: Iterable[str] | None = None,
    *,
    source_plugin_id: str | None = None,
    refresh: bool = True,
) -> list[StructuredTool]:
    cfg = _get_effective_config()
    if not cfg.get("enabled"):
        return []
    allow = _allow_names_set(allow_names)
    if refresh:
        discover_enabled_servers()
    _sync_catalog_from_config(cfg)
    wrappers: list[StructuredTool] = []
    with _runtime_lock:
        infos = [
            info for tools in _catalog.values() for info in tools.values()
            if info.enabled and _source_plugin_allowed(info, source_plugin_id)
        ]
    issued: dict[str, tuple[str, str]] = {}
    for info in infos:
        if not _mcp_runtime_name_allowed(info.prefixed_name, allow) or "model" not in info.visibility:
            continue  # A tool only its own view may call is never the agent's.
        issued[info.prefixed_name] = (info.server_name, info.name)
        try:
            wrappers.append(StructuredTool.from_function(
                func=_make_tool_func(info.server_name, info.name, enforce_policy=True),
                name=info.prefixed_name,
                description=f"External MCP tool from server '{info.server_name}'. {info.description}",
                args_schema=_schema_to_model(info),
            ))
        except Exception as exc:
            log_event("mcp.tool.wrap_failed", level=logging.WARNING, server=info.server_name, tool=info.name, error=str(exc))
    for server_name, server_cfg in cfg.get("servers", {}).items():
        if not _server_source_plugin_allowed(server_cfg, source_plugin_id):
            continue
        if server_name not in _servers:
            continue
        tools_cfg = server_cfg.get("tools", {})
        safe_server = sanitize_name_component(server_name)
        for helper in (("list_resources", "read_resource") if tools_cfg.get("resources_enabled") else ()) + (
                ("list_prompts", "get_prompt") if tools_cfg.get("prompts_enabled") else ()):
            if _mcp_runtime_name_allowed(f"mcp_{safe_server}_{helper}", allow):
                issued[f"mcp_{safe_server}_{helper}"] = (server_name, helper)
        if tools_cfg.get("resources_enabled"):
            list_name = f"mcp_{safe_server}_list_resources"
            read_name = f"mcp_{safe_server}_read_resource"
            if _mcp_runtime_name_allowed(list_name, allow):
                wrappers.append(StructuredTool.from_function(
                    func=_make_resource_list_func(server_name, enforce_policy=True),
                    name=list_name,
                    description=f"List resources exposed by MCP server '{server_name}'.",
                ))
            if _mcp_runtime_name_allowed(read_name, allow):
                wrappers.append(StructuredTool.from_function(
                    func=_make_resource_read_func(server_name, enforce_policy=True),
                    name=read_name,
                    description=f"Read a resource URI from MCP server '{server_name}'.",
                    args_schema=_ResourceReadArgs,
                ))
        if tools_cfg.get("prompts_enabled"):
            list_name = f"mcp_{safe_server}_list_prompts"
            get_name = f"mcp_{safe_server}_get_prompt"
            if _mcp_runtime_name_allowed(list_name, allow):
                wrappers.append(StructuredTool.from_function(
                    func=_make_prompt_list_func(server_name, enforce_policy=True),
                    name=list_name,
                    description=f"List prompts exposed by MCP server '{server_name}'.",
                ))
            if _mcp_runtime_name_allowed(get_name, allow):
                wrappers.append(StructuredTool.from_function(
                    func=_make_prompt_get_func(server_name, enforce_policy=True),
                    name=get_name,
                    description=f"Get a prompt from MCP server '{server_name}'.",
                    args_schema=_PromptGetArgs,
                ))
    with _runtime_lock:
        _issued.update(issued)
    return wrappers


def _candidates(name: str) -> set[tuple[str, str]]:
    """Every (server, tool or helper) a chat tool name could come from: two apps can share one ("acme" +
    "files_delete" and "acme files" + "delete")."""
    with _runtime_lock:
        return {(info.server_name, info.name) for tools in _catalog.values() for info in tools.values()
                if info.prefixed_name == name} | ({_issued[name]} if name in _issued else set())


def _issued_tool(name: str) -> tuple[str, str] | None:
    """(server, its own tool or helper name) for a chat tool name this runtime issued or discovered; None
    when two apps' tools share that name."""
    found = _candidates(name)
    return found.pop() if len(found) == 1 else None


def servers_for_tool(name: str) -> set[str]:
    """Every server a chat tool's name could come from, so leaving an app out leaves out a shared name too."""
    return {server for server, _ in _candidates(name)}


def tool_names(server_name: str) -> list[str]:
    """The tools one server has, as discovered; never connects."""
    with _runtime_lock:
        return list(_catalog.get(server_name) or {})


def tool_info(server_name: str, tool_name: str) -> McpToolInfo | None:
    """A discovered tool of one server, as known now; never connects."""
    with _runtime_lock:
        info = (_catalog.get(server_name) or {}).get(tool_name)
        return copy.copy(info) if info is not None else None


def view_operation(server_name: str, operation: Callable[["McpServerRuntime", float], Any], *, timeout: float = 60) -> Any:
    """Run one view operation (read its HTML, call one of its tools) on that server's own connection."""
    with _runtime_lock:
        runtime = _servers.get(server_name)
    if runtime is None or runtime.session is None:
        raise RuntimeError("server_not_running")
    deadline = time.monotonic() + timeout
    return _future_result_with_generation_cancellation(_schedule(operation(runtime, deadline)), timeout=timeout + 1,
                                                       stopped_message="Stopped.", label=f"mcp_view.{server_name}.cancel")


def server_for_tool(name: str) -> str | None:
    """The server a chat tool's runtime name comes from: one of its discovered tools, or a resource
    or prompt helper this runtime gave the agent for it. A look-alike name has none. Never connects."""
    found = _issued_tool(name)
    return found[0] if found else None


_HELPER_TITLES = {"list_resources": "List resources", "read_resource": "Read a resource",
                  "list_prompts": "List prompts", "get_prompt": "Get a prompt"}


def tool_title(name: str) -> str:
    """A chat tool's readable title within its app; "" for a name this runtime never issued."""
    found = _issued_tool(name)
    return title_of(*found) if found else ""


def title_of(server: str, tool: str) -> str:
    """One server's tool (or helper) in words: the server's own title, else its own name in words
    ("delete_page" -> "Delete page"). A view names its own app's tool this way, never by a shared name."""
    with _runtime_lock:
        info = (_catalog.get(server) or {}).get(tool)
    if info is not None and info.title:
        return info.title
    if tool in _HELPER_TITLES and info is None:
        return _HELPER_TITLES[tool]
    words = " ".join(re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", tool).replace("_", " ").replace("-", " ").split())
    return (words[:1].upper() + words[1:].lower())[:96]


def get_plugin_langchain_tools(
    plugin_id: str,
    allow_names: Iterable[str] | None = None,
    *,
    refresh: bool = True,
) -> list[StructuredTool]:
    return get_langchain_tools(
        allow_names=allow_names,
        source_plugin_id=plugin_id,
        refresh=refresh,
    )


def get_destructive_tool_names(
    allow_names: Iterable[str] | None = None,
    *,
    source_plugin_id: str | None = None,
) -> set[str]:
    """App tools whose access says ask (and every locked one): they ask in Ask and in Allow all alike."""
    allow = _allow_names_set(allow_names)
    _sync_catalog_from_config()
    with _runtime_lock:
        return {
            info.prefixed_name
            for tools in _catalog.values()
            for info in tools.values()
            if (
                info.enabled
                and info.requires_approval
                and _source_plugin_allowed(info, source_plugin_id)
                and _mcp_runtime_name_allowed(info.prefixed_name, allow)
            )
        }


def get_plugin_destructive_tool_names(
    plugin_id: str,
    allow_names: Iterable[str] | None = None,
) -> set[str]:
    return get_destructive_tool_names(allow_names=allow_names, source_plugin_id=plugin_id)


def get_plugin_tool_records(plugin_id: str) -> list[dict[str, Any]]:
    _sync_catalog_from_config()
    with _runtime_lock:
        infos = [
            info for tools in _catalog.values() for info in tools.values()
            if info.enabled and _source_plugin_allowed(info, plugin_id)
        ]
    records: list[dict[str, Any]] = []
    for info in infos:
        source = dict(info.source or {})
        records.append({
            "runtime_name": info.prefixed_name,
            "parent_name": source.get("server_id") or info.server_name,
            "plugin_id": plugin_id,
            "plugin_name": source.get("plugin_name") or plugin_id,
            "tags": ["mcp"],
            "label": info.name,
            "description": info.description,
            "destructive": info.requires_approval,
            "source": "mcp",
            "server_name": info.server_name,
        })
    return records


def get_catalog_snapshot() -> dict[str, list[dict[str, Any]]]:
    _sync_catalog_from_config()
    with _runtime_lock:
        return {
            server: [info.__dict__.copy() for info in tools.values()]
            for server, tools in _catalog.items()
        }


def get_passive_tool_records() -> list[dict[str, Any]]:
    """Project cached discovery and current known toggles without synchronizing.

    No configuration loading, plugin overlay/secret resolution, connection or
    discovery occurs. Unknown configuration yields unknown enablement. These
    descriptors are observations, never dispatch authorization.
    """
    import sys
    from itertools import islice

    state = sys.modules.get("row_bot.plugins.state")
    plugins = state.get_cached_plugin_enablement() if state is not None else None
    with _runtime_lock:
        infos = [(info.server_name, info.name, info.prefixed_name, info.destructive,
                  info.effect or classify_tool_effect(info.name, info.description),
                  info.source.get("plugin_id") if type(info.source) is dict else None, info.enabled)
                 for info in islice((info for tools in _catalog.values() for info in tools.values()), 10001)]
    requested: dict[str, list[str]] = {}
    for server_name, name, *_ in infos:
        requested.setdefault(server_name, []).append(name)
    config = mcp_config.get_cached_enablement({server: tuple(names) for server, names in requested.items()})
    records = []
    for server_name, name, identity, destructive, effect, plugin_id, runtime_enabled in infos:
        enabled = None
        configured = None
        requires = True if destructive is True or effect in {"unknown", "mutation"} else None
        if type(plugin_id) is str and plugin_id:
            # Overlay tool toggles cannot be resolved without plugin declarations;
            # cached disabled owners are definitive, enabled owners are not enough.
            if plugins is not None and plugins.get(plugin_id, False) is False:
                enabled = False
        elif config is not None:
            server = config["servers"].get(server_name)
            configured = server is not None
            if server is None or config["enabled"] is False or server["enabled"] is False:
                enabled = False
            elif config["enabled"] is True and server["enabled"] is True and type(destructive) is bool:
                enabled = server["tools"].get(name, tool_enabled_by_default(destructive or effect in {"unknown", "mutation"}))
            if server is not None and type(destructive) is bool:
                requires = asks_first(name, destructive, effect, server["require_approval"], server["run_without_asking"])
        if runtime_enabled is False:
            enabled = False
        records.append({"id": identity, "label": name, "server_name": server_name,
                        "plugin_id": plugin_id, "destructive": destructive,
                        "requires_approval": requires, "enabled": enabled,
                        "configured": configured})
    return records


# A refresh that failed (or a token that stopped working) leaves the SDK wanting a browser it doesn't have.
_SIGN_IN_FAILURES = ("mcp_sign_in_required", "mcp_credentials_unavailable", "mcp_credentials_endpoint_changed",
                     "401 Unauthorized", "invalid_grant", "No redirect handler", "Token exchange failed", "OAuthFlowError",
                     "OAuthTokenError")


def get_passive_server_statuses(names: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """Read bounded instantiated status only, without refreshing its authority."""
    if len(names) > 50:
        raise ValueError("too_many_servers")
    states = {"disabled", "global_disabled", "not_started", "connecting", "connected",
              "error", "stopped", "disconnected", "sdk_missing", "failed", "dependency_missing",
              "stopping", "cleanup_incomplete"}
    with _runtime_lock:
        result = {}
        for name in names:
            status = _statuses.get(name)
            if status is None:
                continue
            count = status.tool_count
            result[name] = {
                "status": status.status if type(status.status) is str and status.status in states else "unknown",
                "tool_count": count if type(count) is int and 0 <= count <= 10000 else None,
                "connection_present": name in _servers,
                # A refused or expired account; the error text itself never leaves.
                "sign_in_failed": any(code in str(getattr(status, "last_error", "")) for code in _SIGN_IN_FAILURES),
            }
        return result


def get_status_summary() -> dict[str, Any]:
    cfg = _get_effective_config()
    _sync_catalog_from_config(cfg)
    with _runtime_lock:
        statuses = {name: status.__dict__.copy() for name, status in _statuses.items()}
        catalog = {
            server: [info.__dict__.copy() for info in tools.values()]
            for server, tools in _catalog.items()
        }
    for name, server_cfg in cfg.get("servers", {}).items():
        statuses.setdefault(name, McpServerStatus(
            name=name,
            enabled=bool(server_cfg.get("enabled")),
            status="disabled" if not server_cfg.get("enabled") else "not_started",
            transport=str(server_cfg.get("transport", "stdio")),
            source=dict(server_cfg.get("source") or {}),
        ).__dict__.copy())
    return {
        "enabled": bool(cfg.get("enabled")),
        "sdk_available": sdk_available(),
        "server_count": len(cfg.get("servers", {})),
        "enabled_server_count": sum(1 for server in cfg.get("servers", {}).values() if server.get("enabled")),
        "connected_server_count": sum(1 for status in statuses.values() if status.get("status") == "connected"),
        "tool_count": sum(len(tools) for tools in catalog.values()),
        "enabled_tool_count": sum(1 for tools in catalog.values() for info in tools if info.get("enabled")),
        "destructive_tool_count": sum(1 for tools in catalog.values() for info in tools if info.get("requires_approval")),
        "servers": statuses,
        "tools": catalog,
    }
