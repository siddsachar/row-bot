"""App views in chat (MCP Apps, spec 2026-01-26): the server side of Row-Bot's host.

A finished tool step whose tool declares a view (``_meta.ui.resourceUri``) can show it, when the person's
switch for views and the app's own are on (an app's is on when what they agreed to included a view). The
view's HTML is read from that app's own connection (``resources/read``, its exact media type, at most
1 MiB) and served once, with a header CSP that admits nothing but the app's declared https domains, to a
frame the client creates with ``sandbox="allow-scripts"`` only: an opaque origin, with no Row-Bot cookie,
storage, page or API. A view's calls reach only its own app's tools that allow it (visibility ``app``), at
most 20 a minute, and go through the chat's agent profile, the app's access and the chat's approvals as
any other call does: destructive, high-impact and unknown tools always ask, and a routine change asks
unless the person let it run (Full access, or that one tool), in Allow all too; Block refuses either.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import secrets
import threading
import time
from typing import Any

from row_bot.integrations.safe import TtlCache

logger = logging.getLogger(__name__)

RENDER_SECONDS = 600
CALLS_PER_MINUTE = 20
APPROVAL_MINUTES = 5
MAX_ARGUMENTS = 64 * 1024
_RENDERS = TtlCache(RENDER_SECONDS, 256)
_FRAMES = TtlCache(120, 256)  # Each view's HTML, served once to the frame its render created.
_CALLS = TtlCache(RENDER_SECONDS, 256)  # Each view's recent calls, for its rate limit.
_lock = threading.Lock()
_waiting: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Future]] = {}
# Views with a call waiting for the person: one each, and few at all (each holds one of the few connections
# a browser keeps to Row-Bot until it is answered; it holds no server thread).
_asking: set[str] = set()
MAX_WAITING = 3
_OPERATIONS = threading.BoundedSemaphore(4)  # Reads of and calls to apps for views at once; more are refused.
# Names that reach this computer or its network, whatever their address: private-use suffixes and public
# wildcard-DNS services (127.0.0.1.nip.io). A view never loads from them.
_PRIVATE_SUFFIXES = (".localhost", ".local", ".internal", ".test", ".example", ".invalid", ".lan", ".home", ".corp",
                     ".intranet", ".private", ".localdomain", ".arpa")
_WILDCARD_DNS = ("nip.io", "sslip.io", "xip.io", "localtest.me", "lvh.me", "vcap.me", "traefik.me", "localhost.direct",
                 "lacolhost.com", "local.gd", "nip.direct")


class ViewError(ValueError):
    """Why a view can't be shown, or a call from it can't run; ``str()`` is the code, ``message`` the words."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(code)
        self.message = message or "This view isn't available."


# --- The person's switches ----------------------------------------------------------------------------

def _saved() -> dict:
    from row_bot.integrations import catalogs
    value = catalogs.setting("views")
    return value if isinstance(value, dict) else {}


def enabled() -> bool:
    """The person's switch for app views in chat (on unless they turned it off)."""
    return _saved().get("enabled") is not False


def settings() -> dict:
    saved = _saved()
    apps = saved.get("apps") if isinstance(saved.get("apps"), dict) else {}
    return {"enabled": enabled(), "apps": {key: value for key, value in apps.items() if isinstance(value, bool)}}


def set_enabled(on: bool) -> dict:
    from row_bot.integrations import catalogs
    catalogs.set_setting("views", {**_saved(), "enabled": bool(on)})
    return settings()


def set_app(item_id: str, on: bool) -> dict:
    """One app's views on or off (its page's switch)."""
    from row_bot.integrations import catalogs, facts
    if not isinstance(item_id, str) or facts.read(item_id) is None:
        raise ViewError("not_found")
    saved = _saved()
    apps = {**(saved.get("apps") if isinstance(saved.get("apps"), dict) else {}), item_id: bool(on)}
    catalogs.set_setting("views", {**saved, "apps": apps})
    return settings()


def offered(item: dict) -> bool:
    """Whether the app's views were part of what the person agreed to (a tool with a view was accepted)."""
    from row_bot.integrations.plans import _saved
    catalog = ((_saved(item.get("target"), item["owner_ref"])[1].get("tools") or {}).get("catalog")) or {}
    return any(isinstance(tool, dict) and tool.get("view") for tool in catalog.values())


def app_on(item: dict) -> bool:
    """An app's views show: the person's switch is on, and the app's own (its page) is on, which it is by
    default when what they agreed to included a view."""
    if not enabled():
        return False
    choice = settings()["apps"].get(item["id"])
    return choice if choice is not None else offered(item)


def about(item: dict) -> dict | None:
    """What an app's page says about its views: none, or whether they show (and that it has them)."""
    if item.get("kind") != "mcp" or item.get("lifecycle") == "available":
        return None
    from row_bot.mcp_client import runtime
    server = item.get("server") or ""
    current = any(info.ui for info in (runtime.tool_info(server, name) for name in runtime.tool_names(server)) if info)
    if not (current or offered(item)):
        return None
    return {"on": app_on(item), "everywhere": enabled()}


# --- What a view may load --------------------------------------------------------------------------------

def _origin(value: object) -> str:
    """One declared https origin (``https://cdn.example.com`` or ``https://*.example.com``), or ''. Never a
    loopback, private or bare address, a single label or a shared public suffix."""
    from row_bot.integrations.inputs import public_suffix
    text = str(value or "").strip().lower().rstrip("/")
    if not re.fullmatch(r"https://(\*\.)?[a-z0-9.-]{1,253}", text):
        return ""
    host = text.removeprefix("https://").removeprefix("*.")
    try:
        ipaddress.ip_address(host)
        return ""
    except ValueError:
        pass
    if ("." not in host or host == "localhost" or ("." + host).endswith(_PRIVATE_SUFFIXES)
            or any(host == name or host.endswith("." + name) for name in _WILDCARD_DNS)
            or any(not label for label in host.split(".")) or public_suffix(host)):
        return ""
    return text


def declared_domains(csp: object) -> dict[str, list[str]]:
    """The https origins an app's view declares (``_meta.ui.csp``), each kind at most 16."""
    csp = csp if isinstance(csp, dict) else {}
    found = {}
    for key in ("resourceDomains", "connectDomains", "frameDomains", "baseUriDomains"):
        values = csp.get(key) if isinstance(csp.get(key), list) else []
        found[key] = list(dict.fromkeys(origin for origin in map(_origin, values[:64]) if origin))[:16]
    return found


def content_security_policy(csp: object) -> str:
    """The view's whole policy: nothing but its own inline code and data, plus the app's declared https
    domains; never ``'self'`` (which would be Row-Bot's origin), and the sandbox again, as a header."""
    domains = declared_domains(csp)
    resource = " ".join(domains["resourceDomains"])
    return "; ".join([
        "default-src 'none'",
        f"script-src 'unsafe-inline' {resource}".strip(),
        f"style-src 'unsafe-inline' {resource}".strip(),
        f"img-src data: {resource}".strip(),
        f"media-src data: {resource}".strip(),
        f"font-src {resource or chr(39) + 'none' + chr(39)}",
        "connect-src " + (" ".join(domains["connectDomains"]) or "'none'"),
        "frame-src " + (" ".join(domains["frameDomains"]) or "'none'"),
        "object-src 'none'",
        "base-uri " + (" ".join(domains["baseUriDomains"]) or "'none'"),
        "form-action 'none'",
        "frame-ancestors 'self'",
        "sandbox allow-scripts",
    ])


FRAME_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), clipboard-read=(), clipboard-write=(), usb=(), "
                          "serial=(), payment=(), display-capture=()",
}


# --- Showing one view ------------------------------------------------------------------------------------

def _step(conversation_id: str, call_id: str) -> tuple[dict, str | None]:
    """The tool call (its runtime name and arguments) and its result text, by call id, as the chat kept them."""
    from row_bot import threads
    call, result = None, None
    for message in threads.get_latest_checkpoint_messages(conversation_id):
        call = call or next((item for item in getattr(message, "tool_calls", None) or [] if item.get("id") == call_id), None)
        if getattr(message, "tool_call_id", None) == call_id:
            content = message.content
            result = content if isinstance(content, str) else "".join(
                part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
    if call is None:
        raise ViewError("not_found")
    name, args = call["name"], call.get("args") or {}
    if name == "tool_invoke" and isinstance(args, dict):  # Called through tool discovery: the tool it named.
        name, args = str(args.get("name") or ""), args.get("arguments") or {}
    return {"id": call["id"], "name": name, "args": args if isinstance(args, dict) else {}}, result


def _result(text: str | None) -> dict | None:
    """The tool's result as its view expects it, from what the chat kept (the agent's text form)."""
    if text is None:
        return None
    body = re.sub(r"\AApproval: [^\n]*\n?", "", text)  # The approval line Row-Bot adds is not the app's.
    error = body.startswith("MCP tool error: ")
    body = body.removeprefix("MCP tool error: ")
    content, _, structured = body.partition("STRUCTURED_CONTENT:\n")
    found: dict[str, Any] = {"content": [{"type": "text", "text": content.strip()}] if content.strip() else []}
    try:
        value = json.loads(structured) if structured else None
    except ValueError:
        value = None  # Cut short in the chat: the view gets the text only.
    if isinstance(value, dict):
        found["structuredContent"] = value
    if error:
        found["isError"] = True
    return found


def _app_of(server: str) -> dict | None:
    from row_bot.integrations import scope
    return next((item for item in scope._mcp_items() if item["server"] == server), None)


def has_view(tool_name: str, item: dict | None = None) -> bool:
    """Whether a chat tool's step can show its app's view now (a read; nothing connects). ``item``: the
    app's item, when the caller has already found it."""
    from row_bot.mcp_client import runtime
    try:
        found = runtime._issued_tool(tool_name)
        info = runtime.tool_info(*found) if found else None
        if info is None or not info.ui:
            return False
        item = item if item is not None and item.get("server") == found[0] else _app_of(found[0])
        return bool(item and app_on(item))
    except Exception:
        return False


def _in_chat(item: dict, conversation_id: str, info: Any = None, arguments: dict | None = None) -> bool:
    """Whether this chat may use the app (or, with ``info``, make this call to one of its tools) as a turn
    of it could: not switched off here, its agent profile still there and on, the app within the profile's
    tools, and the call not refused by the profile (denied, outside its tools, or a change while read-only)."""
    from row_bot.integrations import scope
    from row_bot.threads import get_thread_apps_off
    from row_bot.tools.profile_policy import dispatch_refusal
    if item["id"] in set(get_thread_apps_off(conversation_id)):
        return False
    profile = scope._profile(conversation_id)
    if profile is None or not scope._allowed(item, scope._profile_allow(conversation_id, profile)):
        return False
    if info is None:
        return True
    origin = info.source if isinstance(info.source, dict) else {}
    source, parent = "mcp", "mcp"  # As the agent binds it: a standalone connection's tool, or a plugin's.
    if origin.get("kind") == "plugin" and origin.get("plugin_id"):
        source, parent = f"plugin:{origin['plugin_id']}:mcp:{info.server_name}", str(origin.get("server_id") or info.server_name)
    return dispatch_refusal(profile, info.prefixed_name, arguments or {}, source=source, parent=parent) is None


def render(conversation_id: str, call_id: str) -> dict:
    """Prepare one step's view: what the client shows and the frame address it loads once."""
    from row_bot.integrations import scope
    from row_bot.mcp_client import runtime
    call, text = _step(conversation_id, call_id)
    found = runtime._issued_tool(str(call.get("name") or ""))
    info = runtime.tool_info(*found) if found else None
    if info is None or not info.ui:
        raise ViewError("view_unavailable", "This step has no view.")
    item = _app_of(info.server_name)
    if item is None:
        raise ViewError("view_unavailable", "The app this view belongs to isn't connected.")
    if not app_on(item):
        raise ViewError("views_off", "Views from this app are off.")
    if not _in_chat(item, conversation_id):
        raise ViewError("views_off", "This app is off in this chat.")
    try:
        view = _operation(info.server_name, lambda rt, deadline: rt.read_view(info.ui, deadline=deadline), 30)
    except ViewError:
        raise
    except (ValueError, RuntimeError, TimeoutError) as error:
        logger.warning("A view from %s didn't load: %s %s", info.server_name, type(error).__name__, str(error)[:80])
        raise ViewError("view_unavailable", "The app didn't send its view. Try again later.") from error
    render_id = secrets.token_hex(16)
    meta = view["meta"]
    _RENDERS.put(render_id, {"conversation_id": conversation_id, "server": info.server_name})
    _FRAMES.put(render_id, {"html": view["html"], "csp": content_security_policy(meta.get("csp"))})
    app = scope.app_for_tool(str(call.get("name") or "")) or {"item_id": item["id"], "name": item["name"],
                                                               "icon": item["icon"], "tool": ""}
    return {"render_id": render_id, "frame_url": f"/app-views/{render_id}", "app": app,
            # The tool as MCP defines one (a view's SDK refuses to start without its inputSchema).
            "tool": {"name": info.name, "title": runtime.tool_title(str(call.get("name") or "")) or info.name,
                     "description": (info.description or "")[:4096],
                     "inputSchema": info.input_schema if info.input_schema.get("type") == "object" else {"type": "object"}},
            "input": call.get("args") or {}, "result": _result(text), "prefers_border": meta.get("prefersBorder") is not False,
            "domains": sorted({origin for origins in declared_domains(meta.get("csp")).values() for origin in origins})}


def frame(render_id: str) -> tuple[str, dict[str, str]]:
    """The view's HTML and its headers, once: a second load (the view navigating, or anyone else) gets none."""
    if not isinstance(render_id, str) or not re.fullmatch(r"[0-9a-f]{32}", render_id):
        raise ViewError("not_found")
    with _lock:
        found = _FRAMES.get(render_id)
        _FRAMES.put(render_id, None)
    if not found:
        raise ViewError("not_found")
    return found["html"], {**FRAME_HEADERS, "Content-Security-Policy": found["csp"]}


# --- A view calling its own app --------------------------------------------------------------------------

def _operation(server: str, operation: Any, timeout: float) -> Any:
    """One read of, or call to, a view's app, among a few at a time (each holds a server worker)."""
    from row_bot.mcp_client import runtime
    if not _OPERATIONS.acquire(blocking=False):
        raise ViewError("view_busy", "Views are busy. Try again in a moment.")
    try:
        return runtime.view_operation(server, operation, timeout=timeout)
    finally:
        _OPERATIONS.release()


def _rate(render_id: str) -> None:
    now = time.monotonic()
    with _lock:
        recent = [at for at in _CALLS.get(render_id) or [] if now - at < 60]
        if len(recent) >= CALLS_PER_MINUTE:
            raise ViewError("view_rate_limited", "Too many requests from this view. Wait a moment.")
        _CALLS.put(render_id, [*recent, now])


def _approval_mode(conversation_id: str) -> str:
    """The chat's own approval choice (Ask, Allow all or Block), as its turns use it."""
    from row_bot.threads import _get_thread_approval_mode
    return _get_thread_approval_mode(conversation_id)


def gate(info: Any, approval_mode: str) -> str:
    """``run``, ``ask`` or ``refuse`` for one call from a view, as for the agent: a tool the app's access
    says to ask about (a routine change unless the app has Full access or that tool may run without
    asking; high-impact and unknown ones always) asks in Ask and in Allow all alike, and Block refuses it;
    anything else runs."""
    if info.requires_approval:
        return "refuse" if approval_mode == "block" else "ask"
    return "run"


async def _ask(record: dict, info: Any, runtime_name: str, arguments: dict) -> bool:
    """Ask the person in the chat, with the standard approval card, and wait for their answer (no thread
    waits: their answer, or the request's timeout, resolves it)."""
    from row_bot.application.approval_projection import project_approval_context
    from row_bot.application.client_platform import client_platform_service
    from row_bot.integrations.scope import _name
    from row_bot.mcp_client import runtime
    from row_bot.tasks import create_approval_request
    loop = asyncio.get_running_loop()
    answer = loop.create_future()

    def request() -> str:
        # ``id`` is the view's render id: its card in the chat shows this request (no turn is waiting on it).
        interrupt = {"id": record["render_id"], "tool": runtime_name, "args": arguments,
                     "label": info.title or info.name, "description": f"{info.title or info.name} (asked from its view in this chat)"}
        public = project_approval_context(interrupt)
        public.pop("app", None)
        item = _app_of(record["server"])  # The view's own app: never one read from a name two apps may share.
        if item is not None:
            public["app"] = {"item_id": item["id"], "name": _name(item)[:128], "icon": item["icon"],
                             "tool": runtime.title_of(record["server"], info.name)}
        _, approval_id = create_approval_request(
            record["render_id"], "", "view", public["reason"], timeout_minutes=APPROVAL_MINUTES, resume_kind="mcp_app",
            source_thread_id=record["conversation_id"], parent_thread_id=record["conversation_id"],
            approval_payload_json={"interrupt": interrupt, "render_id": record["render_id"]})
        with _lock:
            _waiting[approval_id] = (loop, answer)
        client_platform_service.projection.publish(record["conversation_id"], "approval.required", {
            "status": "waiting_approval", "approval_id": approval_id, **public})
        return approval_id

    approval_id = await asyncio.to_thread(request)
    try:
        return await asyncio.wait_for(answer, APPROVAL_MINUTES * 60 + 5)
    except TimeoutError:
        return False
    finally:
        with _lock:
            _waiting.pop(approval_id, None)


def decided(approval_id: str, approved: bool) -> None:
    """The person answered (or the request timed out, as a denial)."""
    with _lock:
        waiting = _waiting.get(approval_id)
    if waiting is not None:
        loop, answer = waiting
        try:
            loop.call_soon_threadsafe(lambda: answer.done() or answer.set_result(bool(approved)))
        except RuntimeError:  # The call it answers has ended.
            pass


def _admit(render_id: str, name: str, arguments: dict) -> tuple[dict, Any, Any, str]:
    """Whether a view may make this call: its record, the tool, what it is bound to, and run, ask or refuse."""
    from row_bot.mcp_client import runtime
    record = _RENDERS.get(render_id) if isinstance(render_id, str) else None
    if record is None:
        raise ViewError("not_found", "This view has ended. Open it again.")
    record = {**record, "render_id": render_id}
    if not isinstance(name, str) or not isinstance(arguments, dict) or len(json.dumps(arguments, default=str)) > MAX_ARGUMENTS:
        raise ViewError("invalid_command")
    _rate(render_id)
    runtime._sync_catalog_from_config()  # The app's access as saved now, not as last discovered.
    info = runtime.tool_info(record["server"], name)  # Only this view's own app: never another server.
    if info is None or "app" not in info.visibility:
        raise ViewError("view_tool_refused", "This view can't use that tool.")
    if not info.enabled:
        raise ViewError("view_tool_refused", "That tool is off for this app.")
    item = _app_of(record["server"])
    if item is None or not app_on(item):
        raise ViewError("views_off", "Views from this app are off.")
    if not _in_chat(item, record["conversation_id"], info, arguments):  # Before anything asks.
        raise ViewError("view_tool_refused", "This chat doesn't allow that.")
    expected = runtime._bind_authority(record["server"], name)  # What is asked about is what runs.
    decision = gate(info, _approval_mode(record["conversation_id"]))
    if decision == "refuse":
        raise ViewError("view_tool_refused", "This chat doesn't allow that kind of action.")
    return record, info, expected, decision


def _run(record: dict, name: str, arguments: dict, expected: Any) -> dict:
    from row_bot.mcp_client import runtime

    def validate() -> None:  # Checked again just before the call: a change of access since asking stops it.
        try:
            runtime._validate_bound_runtime(record["server"], expected, tool_name=name)
        except RuntimeError as error:
            raise ViewError("view_tool_refused", "This tool's access changed. Try again.") from error
    try:
        return _operation(record["server"], lambda rt, deadline: rt.call_tool_for_view(
            name, arguments, deadline=deadline, validate=validate), 60)
    except ViewError:
        raise
    except (RuntimeError, TimeoutError, ValueError) as error:
        raise ViewError("view_tool_failed", "The app couldn't do that just now.") from error


async def call(render_id: str, name: str, arguments: dict) -> dict:
    """One tool call from a view: its own app's tool that allows it, under the app's access and the chat's
    approvals. Returns the result as the view expects it."""
    record, info, expected, decision = await asyncio.to_thread(_admit, render_id, name, arguments)
    if decision == "ask":
        with _lock:
            if render_id in _asking or len(_asking) >= MAX_WAITING:
                raise ViewError("view_busy", "This view is already waiting for your answer.")
            _asking.add(render_id)
        try:
            allowed = await _ask(record, info, info.prefixed_name, arguments)
        finally:
            with _lock:
                _asking.discard(render_id)
        if not allowed:
            raise ViewError("view_tool_denied", "You didn't allow this action.")
    return await asyncio.to_thread(_run, record, name, arguments, expected)
