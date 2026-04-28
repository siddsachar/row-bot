"""Isolated MCP server runtime and dynamic LangChain tool wrappers."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import datetime as _dt
import logging
import os
import sys
import threading
import traceback
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, create_model

from mcp_client import config as mcp_config
from mcp_client.logging import log_event, mask_mapping
from mcp_client.requirements import apply_managed_runtime_env, missing_command_message, resolve_command
from mcp_client.results import normalize_call_result
from mcp_client.safety import is_destructive_tool, prefixed_tool_name, sanitize_name_component, tool_enabled_by_default

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
_statuses: dict[str, McpServerStatus] = {}


class McpStdioCommandNotFound(RuntimeError):
    pass


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


def _missing_stdio_command_message(command: str) -> str:
    return missing_command_message(command)


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
        if _loop and _loop.is_running():
            return _loop
        _loop = asyncio.new_event_loop()

        def _run() -> None:
            asyncio.set_event_loop(_loop)
            _loop.run_forever()

        _thread = threading.Thread(target=_run, name="Thoth-MCP-Runtime", daemon=True)
        _thread.start()
        return _loop


def _schedule(coro: Any) -> concurrent.futures.Future:
    return asyncio.run_coroutine_threadsafe(coro, _ensure_loop())


def _update_status(name: str, **updates: Any) -> None:
    with _runtime_lock:
        status = _statuses.get(name)
        if not status:
            server = mcp_config.get_servers().get(name, {})
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


def _normalize_tools(server_name: str, server_cfg: dict[str, Any], tools: list[Any]) -> dict[str, McpToolInfo]:
    tool_cfg = server_cfg.get("tools", {}) if isinstance(server_cfg, dict) else {}
    saved_enabled = dict(tool_cfg.get("enabled") or {})
    include = set(tool_cfg.get("include") or [])
    exclude = set(tool_cfg.get("exclude") or [])
    approval_overrides = set(tool_cfg.get("require_approval") or [])
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
        schema = _tool_attr(tool, "inputSchema", "input_schema", default={}) or {}
        destructive = is_destructive_tool(tool_name, description, tool)
        enabled = bool(saved_enabled.get(tool_name, tool_enabled_by_default(destructive)))
        requires = tool_name in approval_overrides or destructive
        normalized[tool_name] = McpToolInfo(
            server_name=server_name,
            name=tool_name,
            prefixed_name=prefixed_tool_name(server_name, tool_name),
            description=description or f"MCP tool {tool_name} from {server_name}",
            input_schema=schema if isinstance(schema, dict) else {},
            enabled=enabled,
            destructive=destructive,
            requires_approval=requires,
        )
    return normalized


def _sync_catalog_from_config(config: dict[str, Any] | None = None) -> None:
    cfg = config or mcp_config.get_config()
    servers_cfg = cfg.get("servers", {}) if isinstance(cfg.get("servers"), dict) else {}
    with _runtime_lock:
        for server_name, tools in _catalog.items():
            server_cfg = servers_cfg.get(server_name, {}) if isinstance(servers_cfg.get(server_name, {}), dict) else {}
            tools_cfg = server_cfg.get("tools", {}) if isinstance(server_cfg.get("tools"), dict) else {}
            enabled_map = dict(tools_cfg.get("enabled") or {})
            approval_overrides = set(tools_cfg.get("require_approval") or [])
            for info in tools.values():
                info.enabled = bool(enabled_map.get(info.name, tool_enabled_by_default(info.destructive)))
                info.requires_approval = info.destructive or info.name in approval_overrides
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
        return list
    if schema_type == "object":
        return dict
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
        self.session: Any = None
        self.exit_stack: AsyncExitStack | None = None
        self.stop_event: asyncio.Event | None = None
        self._session_lock = asyncio.Lock()

    async def start(self) -> None:
        if not sdk_available():
            _update_status(self.name, status="dependency_missing", last_error="Python package 'mcp' is not installed")
            return
        self.stop_event = asyncio.Event()
        _update_status(self.name, status="connecting", enabled=True, transport=self.cfg.get("transport", "stdio"), last_error="")
        try:
            await self._connect()
            await self._discover_tools()
            await self.stop_event.wait()
        except asyncio.CancelledError:
            raise
        except McpStdioCommandNotFound as exc:
            _update_status(self.name, status="dependency_missing", last_error=str(exc))
            log_event("mcp.server.dependency_missing", level=logging.WARNING, server=self.name, error=str(exc))
        except Exception as exc:
            _update_status(self.name, status="failed", last_error=str(exc))
            log_event("mcp.server.failed", level=logging.WARNING, server=self.name, error=str(exc), traceback=traceback.format_exc())
        finally:
            await self.close()
            with _runtime_lock:
                if _servers.get(self.name) is self:
                    _servers.pop(self.name, None)

    async def _connect(self) -> None:
        transport = str(self.cfg.get("transport") or "stdio")
        self.exit_stack = AsyncExitStack()
        if transport == "stdio":
            if StdioServerParameters is None or stdio_client is None:
                raise RuntimeError("MCP stdio transport is unavailable")
            command = str(self.cfg.get("command") or "").strip()
            if not command:
                raise RuntimeError("stdio MCP server requires a command")
            env = os.environ.copy()
            env.update({str(k): str(v) for k, v in dict(self.cfg.get("env") or {}).items()})
            env = apply_managed_runtime_env(self.cfg, env)
            command = _resolve_stdio_command(command, env)
            params = StdioServerParameters(
                command=command,
                args=[str(arg) for arg in self.cfg.get("args") or []],
                env=env,
                cwd=self.cfg.get("cwd") or None,
            )
            read_stream, write_stream = await self.exit_stack.enter_async_context(stdio_client(params))
        elif transport in {"streamable_http", "http", "streamable-http"}:
            if streamablehttp_client is None:
                raise RuntimeError("MCP Streamable HTTP transport is unavailable")
            url = str(self.cfg.get("url") or "").strip()
            if not url:
                raise RuntimeError("HTTP MCP server requires a URL")
            read_stream, write_stream, _ = await self.exit_stack.enter_async_context(
                streamablehttp_client(url, headers=dict(self.cfg.get("headers") or {}))
            )
        elif transport == "sse":
            if sse_client is None:
                raise RuntimeError("MCP SSE transport is unavailable")
            url = str(self.cfg.get("url") or "").strip()
            if not url:
                raise RuntimeError("SSE MCP server requires a URL")
            read_stream, write_stream = await self.exit_stack.enter_async_context(
                sse_client(url, headers=dict(self.cfg.get("headers") or {}))
            )
        else:
            raise RuntimeError(f"Unsupported MCP transport: {transport}")
        self.session = await self.exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
        await asyncio.wait_for(self.session.initialize(), timeout=float(self.cfg.get("connect_timeout", 30)))
        _update_status(self.name, status="connected", last_connected_at=_now(), last_error="")
        log_event("mcp.server.connected", server=self.name, transport=transport, cfg=mask_mapping(self.cfg))

    async def _discover_tools(self) -> None:
        if not self.session:
            return
        result = await asyncio.wait_for(self.session.list_tools(), timeout=float(self.cfg.get("connect_timeout", 30)))
        tools = list(getattr(result, "tools", result if isinstance(result, list) else []))
        normalized = _normalize_tools(self.name, self.cfg, tools)
        with _runtime_lock:
            _catalog[self.name] = normalized
        _update_status(
            self.name,
            status="connected",
            tool_count=len(normalized),
            enabled_tool_count=sum(1 for info in normalized.values() if info.enabled),
            destructive_tool_count=sum(1 for info in normalized.values() if info.destructive),
            last_discovered_at=_now(),
            last_error="",
        )
        log_event("mcp.tools.discovered", server=self.name, tools=len(normalized))
        agent_mod = sys.modules.get("agent")
        if agent_mod is not None and hasattr(agent_mod, "clear_agent_cache"):
            try:
                agent_mod.clear_agent_cache()
            except Exception:
                pass

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        async with self._session_lock:
            if not self.session:
                raise RuntimeError(f"MCP server '{self.name}' is not connected")
            timeout = float(self.cfg.get("tool_timeout", 120))
            output_limit = int(self.cfg.get("output_limit", 24000))
            log_event("mcp.tool.call", server=self.name, tool=tool_name)
            result = await asyncio.wait_for(self.session.call_tool(tool_name, arguments or {}), timeout=timeout)
            return normalize_call_result(result, output_limit=output_limit)

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

    async def close(self) -> None:
        self.session = None
        if self.exit_stack:
            with contextlib.suppress(Exception):
                await self.exit_stack.aclose()
        self.exit_stack = None
        with _runtime_lock:
            current_status = _statuses.get(self.name)
            preserve_status = current_status and current_status.status in {"failed", "dependency_missing"}
        if not preserve_status:
            _update_status(self.name, status="stopped")

    async def stop(self) -> None:
        if self.stop_event and not self.stop_event.is_set():
            self.stop_event.set()
        await self.close()


def discover_enabled_servers() -> None:
    """Start or refresh enabled MCP servers without blocking startup."""
    cfg = mcp_config.get_config()
    if not cfg.get("enabled"):
        with _runtime_lock:
            running_names = list(_servers)
        for name in running_names:
            stop_server(name)
        with _runtime_lock:
            _catalog.clear()
            for name, server_cfg in cfg.get("servers", {}).items():
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
        if failed_until_refresh:
            continue
        if running:
            continue
        runtime = McpServerRuntime(name, server_cfg)
        with _runtime_lock:
            _servers[name] = runtime
            _statuses[name] = McpServerStatus(
                name=name,
                enabled=True,
                status="connecting",
                transport=str(server_cfg.get("transport", "stdio")),
                source=dict(server_cfg.get("source") or {}),
            )
        _schedule(runtime.start())


def stop_server(name: str) -> None:
    with _runtime_lock:
        runtime = _servers.pop(name, None)
        _catalog.pop(name, None)
    if runtime:
        future = _schedule(runtime.stop())
        with contextlib.suppress(Exception):
            future.result(timeout=5)


def refresh_server(name: str) -> None:
    stop_server(name)
    with _runtime_lock:
        _statuses.pop(name, None)
    discover_enabled_servers()


def shutdown() -> None:
    """Stop MCP child sessions and runtime loop. Safe to call repeatedly."""
    global _loop, _thread
    with _runtime_lock:
        names = list(_servers)
    for name in names:
        stop_server(name)
    loop = _loop
    if loop and loop.is_running():
        loop.call_soon_threadsafe(loop.stop)
    _loop = None
    _thread = None
    log_event("mcp.runtime.shutdown")


async def probe_server_async(name: str, server_cfg: dict[str, Any]) -> dict[str, Any]:
    """Connect to a server temporarily and return discovered tools/status."""
    runtime = McpServerRuntime(name, server_cfg)
    try:
        await runtime._connect()
        result = await asyncio.wait_for(runtime.session.list_tools(), timeout=float(server_cfg.get("connect_timeout", 30)))
        tools = list(getattr(result, "tools", result if isinstance(result, list) else []))
        normalized = _normalize_tools(name, server_cfg, tools)
        return {
            "ok": True,
            "tools": [info.__dict__ for info in normalized.values()],
            "tool_count": len(normalized),
            "destructive_tool_count": sum(1 for info in normalized.values() if info.destructive),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "tools": []}
    finally:
        await runtime.close()


def probe_server(name: str, server_cfg: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
    if not sdk_available():
        return {"ok": False, "error": "Python package 'mcp' is not installed", "tools": []}
    future = _schedule(probe_server_async(name, server_cfg))
    return future.result(timeout=timeout or float(server_cfg.get("connect_timeout", 30)) + 5)


def _call_tool_sync(server_name: str, tool_name: str, kwargs: dict[str, Any]) -> str:
    with _runtime_lock:
        runtime = _servers.get(server_name)
    if not runtime:
        raise RuntimeError(f"MCP server '{server_name}' is not running")
    future = _schedule(runtime.call_tool(tool_name, kwargs))
    return future.result(timeout=float(runtime.cfg.get("tool_timeout", 120)) + 5)


def _make_tool_func(server_name: str, tool_name: str) -> Callable[..., str]:
    def _run(**kwargs: Any) -> str:
        return _call_tool_sync(server_name, tool_name, kwargs)

    return _run


def _make_resource_list_func(server_name: str) -> Callable[[], str]:
    def _run() -> str:
        with _runtime_lock:
            runtime = _servers.get(server_name)
        if not runtime:
            raise RuntimeError(f"MCP server '{server_name}' is not running")
        return _schedule(runtime.list_resources()).result(timeout=float(runtime.cfg.get("tool_timeout", 120)) + 5)
    return _run


def _make_resource_read_func(server_name: str) -> Callable[..., str]:
    def _run(uri: str) -> str:
        with _runtime_lock:
            runtime = _servers.get(server_name)
        if not runtime:
            raise RuntimeError(f"MCP server '{server_name}' is not running")
        return _schedule(runtime.read_resource(uri)).result(timeout=float(runtime.cfg.get("tool_timeout", 120)) + 5)
    return _run


def _make_prompt_list_func(server_name: str) -> Callable[[], str]:
    def _run() -> str:
        with _runtime_lock:
            runtime = _servers.get(server_name)
        if not runtime:
            raise RuntimeError(f"MCP server '{server_name}' is not running")
        return _schedule(runtime.list_prompts()).result(timeout=float(runtime.cfg.get("tool_timeout", 120)) + 5)
    return _run


def _make_prompt_get_func(server_name: str) -> Callable[..., str]:
    def _run(name: str, arguments: dict[str, Any] | None = None) -> str:
        with _runtime_lock:
            runtime = _servers.get(server_name)
        if not runtime:
            raise RuntimeError(f"MCP server '{server_name}' is not running")
        return _schedule(runtime.get_prompt(name, arguments)).result(timeout=float(runtime.cfg.get("tool_timeout", 120)) + 5)
    return _run


def get_langchain_tools() -> list[StructuredTool]:
    if not mcp_config.is_globally_enabled():
        return []
    discover_enabled_servers()
    _sync_catalog_from_config()
    wrappers: list[StructuredTool] = []
    with _runtime_lock:
        infos = [info for tools in _catalog.values() for info in tools.values() if info.enabled]
    for info in infos:
        try:
            wrappers.append(StructuredTool.from_function(
                func=_make_tool_func(info.server_name, info.name),
                name=info.prefixed_name,
                description=f"External MCP tool from server '{info.server_name}'. {info.description}",
                args_schema=_schema_to_model(info),
            ))
        except Exception as exc:
            log_event("mcp.tool.wrap_failed", level=logging.WARNING, server=info.server_name, tool=info.name, error=str(exc))
    cfg = mcp_config.get_config()
    for server_name, server_cfg in cfg.get("servers", {}).items():
        if server_name not in _servers:
            continue
        tools_cfg = server_cfg.get("tools", {})
        safe_server = sanitize_name_component(server_name)
        if tools_cfg.get("resources_enabled"):
            wrappers.append(StructuredTool.from_function(
                func=_make_resource_list_func(server_name),
                name=f"mcp_{safe_server}_list_resources",
                description=f"List resources exposed by MCP server '{server_name}'.",
            ))
            wrappers.append(StructuredTool.from_function(
                func=_make_resource_read_func(server_name),
                name=f"mcp_{safe_server}_read_resource",
                description=f"Read a resource URI from MCP server '{server_name}'.",
                args_schema=_ResourceReadArgs,
            ))
        if tools_cfg.get("prompts_enabled"):
            wrappers.append(StructuredTool.from_function(
                func=_make_prompt_list_func(server_name),
                name=f"mcp_{safe_server}_list_prompts",
                description=f"List prompts exposed by MCP server '{server_name}'.",
            ))
            wrappers.append(StructuredTool.from_function(
                func=_make_prompt_get_func(server_name),
                name=f"mcp_{safe_server}_get_prompt",
                description=f"Get a prompt from MCP server '{server_name}'.",
                args_schema=_PromptGetArgs,
            ))
    return wrappers


def get_destructive_tool_names() -> set[str]:
    _sync_catalog_from_config()
    with _runtime_lock:
        return {
            info.prefixed_name
            for tools in _catalog.values()
            for info in tools.values()
            if info.enabled and info.requires_approval
        }


def get_catalog_snapshot() -> dict[str, list[dict[str, Any]]]:
    _sync_catalog_from_config()
    with _runtime_lock:
        return {
            server: [info.__dict__.copy() for info in tools.values()]
            for server, tools in _catalog.items()
        }


def get_status_summary() -> dict[str, Any]:
    cfg = mcp_config.get_config()
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