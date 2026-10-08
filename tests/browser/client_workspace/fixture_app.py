"""Phase 3 real-service browser fixture with synthetic provider boundaries only."""
from __future__ import annotations

import json
import base64
import hashlib
import logging
import os
import sys
from pathlib import Path
import runpy
import traceback
import struct
import zlib
import queue
import threading
from dataclasses import replace

from fastapi import Header, HTTPException
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from row_bot.server import app

from row_bot.application import client_browser_controls as _browser_controls
from row_bot.tools import registry as _tool_registry
from tests.browser.client_platform import fixture_app as predecessor
from tests.helpers.client_platform_fakes import fixture_id


_large_library: dict | None = None
_orchestrations: dict[str, dict] = {}
_token_feeds: dict[str, queue.Queue[str]] = {}
_parent_releases: dict[str, threading.Event] = {}
_parent_threads: dict[str, threading.Thread] = {}
_expiry_starts: dict[str, threading.Event] = {}
_voice_fixture = {"transcriptions": 0, "bytes": 0, "syntheses": 0, "credentials": 0, "exchanges": 0}
_sharing_fixture: list[dict] = []
_wiki_fixture: dict[str, str] = {}
_browser_control_fixture: dict[str, dict] = {}


def _mount_static() -> None:
    """Serve /static as the desktop app does (app.py): the Design panel's font
    picker shows bundled fonts in their own face from /static/fonts."""
    from starlette.staticfiles import StaticFiles
    from row_bot.runtime_paths import static_dir

    app.mount("/static", StaticFiles(directory=static_dir()), name="static")


_mount_static()


class _SyntheticBrowserControlBackend:
    """Local in-memory managed browser; it never launches or contacts a site."""

    def status(self, conversation_id: str) -> dict:
        return dict(
            _browser_control_fixture.get(
                conversation_id,
                {
                    "engine": "browser",
                    "surface": "browser",
                    "active": False,
                    "paused": False,
                    "thread_id": conversation_id,
                    "state": "idle",
                    "url": "",
                    "last_action": "",
                    "revision": 0,
                },
            )
        )

    def execute(self, action: str, payload: dict, conversation_id: str) -> None:
        current = self.status(conversation_id)
        current["revision"] = int(current.get("revision", 0)) + 1
        if action == "browser.navigate":
            current.update(
                active=True,
                paused=False,
                state="observing",
                url=payload["url"],
                last_action="Opened reviewed address",
            )
        elif action == "browser.take_over":
            current.update(paused=True, state="waiting_user")
        elif action == "browser.check":
            current.update(last_action="Checked current page")
        elif action == "browser.back":
            current.update(
                url="https://example.test/previous",
                last_action="Went back",
            )
        elif action == "browser.end":
            current.update(
                active=False,
                paused=False,
                state="idle",
                url="",
                last_action="",
            )
        else:
            raise AssertionError("Unsupported synthetic browser action")
        _browser_control_fixture[conversation_id] = current


_browser_controls.CanonicalBrowserControlBackend = (
    lambda: _SyntheticBrowserControlBackend()
)
_original_tool_enabled = _tool_registry.is_enabled
_tool_registry.is_enabled = lambda name: (
    True
    if name in {"browser_navigate", "browser_snapshot", "browser_back"}
    else _original_tool_enabled(name)
)


@app.post("/__p4_fixture/capability-settings")
def p4_setup_capability_settings(x_fixture_token: str = Header(default="")) -> dict:
    """Seed local-only Channel, Plugin, and Skill settings fixtures."""
    predecessor._authorize(x_fixture_token)
    from row_bot.channels import config as channel_config, registry
    from row_bot.channels.base import ChannelCapabilities, ConfigField
    from row_bot.plugins import installer, state as plugin_state
    from row_bot import skills

    class SyntheticControlChannel:
        name = "p4_control"
        display_name = "Synthetic local channel"
        capabilities = ChannelCapabilities(streaming=True)

        @property
        def config_fields(self):
            return [ConfigField(key="label", label="Local label", field_type="text",
                                storage="config", help_text="Stored only in the isolated fixture.")]

        def is_configured(self):
            return bool(channel_config.get(self.name, "label", ""))

        def is_running(self):
            return False

        async def start(self):
            return False

        async def stop(self):
            return None

        def send_message(self, *_args, **_kwargs):
            raise AssertionError("Settings fixtures never deliver channel messages")

    registry.register(SyntheticControlChannel())
    channel_config.set("p4_control", "label", "")

    plugin_id = "p4-settings-plugin"
    plugin_dir = installer.PLUGINS_DIR / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(json.dumps({
        "schema_version": 2,
        "id": plugin_id,
        "name": "Synthetic settings plugin",
        "version": "1.0.0",
        "min_row_bot_version": "0.0.0",
        "author": {"name": "Fixture", "github": "fixture"},
        "description": "A local-only plugin configuration fixture.",
        "provides": {"native_tools": [], "mcp_servers": [], "channels": [], "skills": []},
        "permissions": [],
        "settings": {"region": {"label": "Region", "type": "select",
                                   "options": ["local", "isolated"], "required": True}},
        "secrets": {},
        "auth": {},
        "health_checks": [],
    }, separators=(",", ":")), encoding="utf-8")
    plugin_state.mark_plugin_installed(plugin_id, version="1.0.0")
    plugin_state.set_plugin_config(plugin_id, "region", "local")

    skill_id = "p4_browser_skill"
    skill_dir = skills.USER_SKILLS_DIR / skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {skill_id}\n"
        "display_name: Synthetic browser skill\n"
        "icon: 🧪\n"
        "description: A local-only skill settings fixture.\n"
        "enabled_by_default: true\n"
        "version: '1.0'\n"
        "author: Fixture\n"
        "---\n\nUse only deterministic fixture data.\n",
        encoding="utf-8",
    )
    skills.load_skills()
    skills.set_enabled(skill_id, True)
    return {"channel_id": "p4_control", "plugin_id": plugin_id, "skill_id": skill_id}


@app.get("/__p4_fixture/capability-settings")
def p4_capability_settings_state(x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    from row_bot.channels import config as channel_config
    from row_bot.plugins import state as plugin_state
    from row_bot import skills
    snapshot = skills.read_client_skills()
    return {
        "channel_label": channel_config.get("p4_control", "label", None),
        "plugin_region": plugin_state.get_plugin_config("p4-settings-plugin", "region"),
        "skill_available": snapshot["enabled"].get("p4_browser_skill"),
    }


@app.post("/__p4_fixture/wiki")
def p4_setup_wiki(x_fixture_token: str = Header(default="")) -> dict:
    """Create one managed article, then simulate a reviewed external edit."""
    predecessor._authorize(x_fixture_token)
    from row_bot import knowledge_graph, wiki_vault
    vault = (predecessor.DATA / "fixture-workspace").resolve()
    if not wiki_vault.is_enabled() or wiki_vault.get_vault_path().resolve() != vault:
        raise HTTPException(status_code=409, detail="Select and enable the synthetic wiki vault first")
    with knowledge_graph.projection_batch(drain_on_exit=False):
        entity = knowledge_graph.save_entity(
            "person",
            "Synthetic wiki review",
            "The saved database description is long enough for a managed wiki article.",
        )
    outcome = wiki_vault.export_entity_projection(entity)
    if not outcome.complete or outcome.path is None or not outcome.path.resolve().is_relative_to(vault):
        raise HTTPException(status_code=409, detail="Synthetic wiki publication failed")
    original = outcome.path.read_text(encoding="utf-8")
    edited = original.replace(
        "The saved database description is long enough for a managed wiki article.",
        "The externally edited vault description is reviewed before database import.",
    )
    if edited == original:
        raise HTTPException(status_code=409, detail="Synthetic wiki edit target missing")
    outcome.path.write_text(edited, encoding="utf-8")
    _wiki_fixture.clear()
    _wiki_fixture.update(entity_id=entity["id"], article=str(outcome.path))
    return {"entity_id": entity["id"], "title": entity["subject"]}


@app.get("/__p4_fixture/wiki")
def p4_wiki_state(x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    from row_bot import knowledge_graph
    identifier = _wiki_fixture.get("entity_id")
    entity = knowledge_graph.get_entity(identifier) if identifier else None
    return {
        "entity_id": identifier,
        "description": entity.get("description") if entity else None,
    }


@app.post("/__p4_fixture/sharing")
def p4_setup_sharing(x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    from row_bot.channels import registry
    from row_bot.channels.base import ChannelCapabilities
    from row_bot.designer import storage
    if not storage.DESIGNER_DIR.resolve().is_relative_to(predecessor.DATA.resolve()):
        raise HTTPException(status_code=403, detail="Synthetic data scope required")
    class SyntheticSharingChannel:
        name = 'p4_fake_share'
        display_name = 'Synthetic sharing channel'
        capabilities = ChannelCapabilities(document_out=True)
        def is_running(self): return True
        def is_configured(self): return True
        def get_default_target(self): return 'synthetic-recipient'
        def send_document(self, target, path, caption=None):
            file = Path(path).resolve()
            if not file.is_relative_to(storage.DESIGNER_DIR.resolve()):
                raise ValueError('Synthetic sharing scope required')
            data = file.read_bytes()
            _sharing_fixture.append({'target': str(target), 'size': len(data),
                'sha256': hashlib.sha256(data).hexdigest(), 'html': b'<html' in data.lower()})
    registry.register(SyntheticSharingChannel())
    return {'count': len(_sharing_fixture)}


@app.get("/__p4_fixture/sharing")
def p4_sharing_state(x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    return {'count': len(_sharing_fixture), 'last': _sharing_fixture[-1] if _sharing_fixture else None}


@app.get("/__p4_fixture/voice")
def p4_voice(x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    return dict(_voice_fixture)


class SyntheticRequestDiagnostics:
    """Observe lifecycle only; never read or serialize authentication/content."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        disconnected = False
        started = False
        exception_type = None

        async def observed_receive():
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
            return message

        async def observed_send(message):
            nonlocal started
            await send(message)
            if message["type"] == "http.response.start":
                started = True

        try:
            await self.app(scope, observed_receive, observed_send)
        except BaseException as error:
            exception_type = type(error).__name__
            raise
        finally:
            path = scope.get("path", "")
            if exception_type is not None or path == "/api/v1/events":
                logging.getLogger(__name__).info("Synthetic request lifecycle: %s", json.dumps({
                    "path": path, "disconnectSeen": disconnected,
                    "responseStarted": started, "exceptionType": exception_type,
                }, separators=(",", ":")))
def _synthetic_image() -> str:
    """A visible deterministic RGB chart fixture, with no external image data."""
    width, height = 240, 120
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows.extend((32, 112, 184) if x < width // 2 else (32, 144, 112) if y < height // 2 else (224, 160, 48))
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", width, height, 8, 2, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b"")
    return base64.b64encode(data).decode("ascii")


_IMAGE = _synthetic_image()


@app.get("/__p3_fixture/resources")
def resources(x_fixture_token: str = Header(default="")) -> dict:
    """Expose synthetic identities, never host paths or fixture credentials."""
    predecessor._authorize(x_fixture_token)
    state = json.loads((predecessor.DATA / "docs_real_ui_demo_state.json").read_text(encoding="utf-8"))
    folder = predecessor.DATA / "fixture-workspace"
    return {"workspace_id": state["developer"]["workspace_id"], "artifact_id": state["designer"]["project_id"],
            "fixture_file_sha256": hashlib.sha256((folder / "fixture.txt").read_bytes()).hexdigest(),
            "git_present": (folder / ".git").exists()}


@app.get("/__p3_fixture/calls")
def phase3_calls(x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    with predecessor._lock:
        return {"calls": [dict(call) for call in predecessor._calls], "external_calls": 0}


@app.get("/__p3_fixture/natural/{conversation_id}")
def natural_result(conversation_id: str, x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    from row_bot.conversation_resources import list_bindings
    from row_bot.developer.storage import get_workspace
    from row_bot.designer.client_service import read_artifact
    result = {"bindings": []}
    for binding in list_bindings(conversation_id).bindings:
        if binding.kind == "workspace":
            workspace = get_workspace(binding.resource_id)
            folder = Path(workspace.path).resolve()
            if not folder.is_relative_to(predecessor.DATA):
                raise HTTPException(status_code=403)
            file = folder / "index.html"
            result["bindings"].append({"kind": "workspace", "id": binding.resource_id,
                "file_exists": file.is_file(), "git_present": (folder / ".git").exists()})
        else:
            project = read_artifact(binding.resource_id)
            result["bindings"].append({"kind": "artifact", "id": binding.resource_id,
                "page_count": len(project.pages), "first_title": project.pages[0].title if project.pages else ""})
    return result


def _natural_final(call: dict, thread: str, final: str, key: str):
    from row_bot.threads import append_checkpoint_messages, get_latest_checkpoint_revision
    native_id = fixture_id(f"natural:{key}:" + call["generation_id"])
    append_checkpoint_messages(thread, [AIMessage(id=native_id, content=final)])
    yield "token", final
    yield "output_binding", {"native_message_id": native_id,
        "checkpoint_revision": get_latest_checkpoint_revision(thread)}
    yield "done", final


def _goal_verifier(goal, _context):
    """A goal's second step completes it; the first asks for one more."""
    if int(goal.get("turns_used") or 0) >= 2:
        return {"verdict": "complete", "reason": "Fixture goal: both steps are done."}
    return {"verdict": "continue", "reason": "Fixture goal: one more step."}


def stream(text: str, enabled_tools: list[str], config: dict, *, stop_event=None):
    """Script real tools/media projection and a durable final behind a barrier."""
    if (text.startswith("[Goal mode started]") or text.startswith("[Goal continuation]")) and "approval" not in text:
        # One goal step finishes at once; the real goal owner counts it and
        # the scripted verifier (_goal_verifier) decides what happens next.
        call = predecessor._record("submit", config, "goal-step")
        try:
            yield from _natural_final(call, call["conversation_id"], "Goal step done.", "goal")
        finally:
            call["quiesced"] = True
        return
    if text.startswith("[Continue in the new code folder]") or text.startswith("[Continue in the new design]"):
        # The follow-up turn the create_* tool scheduled: the new resource is
        # bound now, so the real Developer/Designer owners write into it.
        from row_bot import agent
        from row_bot.conversation_resources import current_execution_context
        call = predecessor._record("submit", config, "natural-followup")
        thread = call["conversation_id"]
        agent._set_active_runtime_context(thread_id=thread, runtime_surface="normal_chat",
            approval_mode=config["configurable"]["approval_mode"],
            agent_run_id=config["configurable"].get("agent_run_id", ""))
        context = current_execution_context()
        try:
            if "code folder" in text.split("\n", 1)[0]:
                from row_bot.tools.developer_tool import _write_file
                assert context and context.resolve("workspace")
                outcome = _write_file("index.html", "<!doctype html><title>Fixture landing</title>")
                assert outcome.startswith("Wrote index.html")
                final = "Built the synthetic landing page in the bound draft."
            else:
                from row_bot.designer.tool import _set_pages
                assert context and context.resolve("artifact")
                outcome = _set_pages([{"title": "Fixture cover", "html": "<!doctype html><html><body><h1>Fixture deck</h1></body></html>"}])
                assert outcome.startswith("Set 1 pages")
                final = "Created the synthetic presentation in the bound design."
            yield from _natural_final(call, thread, final, "followup")
        finally:
            call["quiesced"] = True
        return
    if "setup fixture" in text:
        # The work needs a tool that is off: the setting tool asks through the
        # standard approval, shown as a "Turn on" card (decision 12).
        from row_bot.threads import append_checkpoint_messages
        call = predecessor._record("submit", config, "setup-card")
        thread = call["conversation_id"]
        identity = f"setup:{call['generation_id']}"
        tool_id = fixture_id(identity + ":tool")
        args = {"setting": "tool_toggle", "value": "web_search:on"}
        try:
            append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":tool-call"), content="",
                tool_calls=[{"id": tool_id, "name": "row_bot_update_setting", "args": args}])])
            yield "tool_call", {"tool_call_id": tool_id, "name": "row_bot_update_setting", "args": args}
            yield "interrupt", [{"__interrupt_id": fixture_id(identity + ":approval"),
                                 "tool": "row_bot_update_setting", "label": "Turn on Web Search",
                                 "description": "Row-Bot needs Web Search for this. You can turn it off "
                                                "again in Settings › Tools.",
                                 "args": args, "setup": {"kind": "tool", "label": "Web Search"}}]
        finally:
            call["quiesced"] = True
        return
    if "folder fixture" in text:
        # The work needs a folder the person has: use_code_folder pauses the
        # turn on a folder card (B277) where they pick it.
        from row_bot.threads import append_checkpoint_messages
        call = predecessor._record("submit", config, "folder-card")
        thread = call["conversation_id"]
        identity = f"folder:{call['generation_id']}"
        tool_id = fixture_id(identity + ":tool")
        try:
            append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":tool-call"), content="",
                tool_calls=[{"id": tool_id, "name": "use_code_folder", "args": {}}])])
            yield "tool_call", {"tool_call_id": tool_id, "name": "use_code_folder", "args": {}}
            yield "interrupt", [{"__interrupt_id": fixture_id(identity + ":approval"),
                                 "tool": "use_code_folder", "label": "Use an existing folder",
                                 "description": "Choose the folder on this computer; Row-Bot adds it and "
                                                "works only inside it. Its files and Git history stay as they are.",
                                 "args": {}, "setup": {"kind": "folder", "label": "Use an existing folder",
                                                       "folders": []}}]
        finally:
            call["quiesced"] = True
        return
    if "app view fixture" in text:
        # An app with an interactive view (MCP Apps): the step calls the fixture counter, whose view then
        # shows under it in its own sandboxed frame.
        from row_bot.threads import append_checkpoint_messages
        call = predecessor._record("submit", config, "app-view")
        thread = call["conversation_id"]
        identity = f"app-view:{call['generation_id']}"
        tool_id, result_id = fixture_id(identity + ":tool"), fixture_id(identity + ":result")
        result = '3\n\nSTRUCTURED_CONTENT:\n{"count": 3}'
        try:
            append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":tool-call"), content="",
                tool_calls=[{"id": tool_id, "name": "mcp_counter_counter", "args": {"start": 3}}])])
            yield "tool_call", {"tool_call_id": tool_id, "message_id": result_id, "name": "MCP: counter (Counter)",
                                "raw_name": "mcp_counter_counter", "runtime_name": "mcp_counter_counter", "args": {"start": 3}}
            append_checkpoint_messages(thread, [ToolMessage(id=result_id, tool_call_id=tool_id, name="mcp_counter_counter",
                                                          content=result)])
            yield "tool_done", {"tool_call_id": tool_id, "message_id": result_id, "name": "MCP: counter (Counter)",
                                "raw_name": "mcp_counter_counter", "args": {"start": 3}, "content": result}
            yield from _natural_final(call, thread, "Here is your counter.", "app-view")
        finally:
            call["quiesced"] = True
        return
    if "app tool fixture" in text or "connected now" in text:
        # Apps in chat: a step through the connected synthetic app (its logo and name on the step), then
        # one change that asks first (its approval names the app). What the turn left out is recorded.
        from row_bot.mcp_client import runtime
        from row_bot.threads import append_checkpoint_messages
        call = predecessor._record("submit", config, "app-tool")
        call["app_scope"] = config["configurable"].get("app_scope")
        thread = call["conversation_id"]
        try:
            if "connected now" in text:
                yield from _natural_final(call, thread, "Continuing with what you asked.", "app-continue")
                return
            wanted = text.split("app tool fixture", 1)[1].strip().casefold()
            with runtime._runtime_lock:
                found = next(((info.prefixed_name, info.server_name) for tools in runtime._catalog.values()
                              for info in tools.values() if info.name == "search_pages"
                              and wanted in info.server_name.casefold()), None)
            assert found, "app tool fixture needs a connected synthetic app"
            identity = f"app-tool:{call['generation_id']}"
            search_id, delete_id = fixture_id(identity + ":search"), fixture_id(identity + ":delete")
            result_id = fixture_id(identity + ":result")
            delete_name = found[0].removesuffix("search_pages") + "delete_page"
            append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":tool-call"), content="",
                tool_calls=[{"id": search_id, "name": found[0], "args": {"query": "roadmap"}}])])
            yield "tool_call", {"tool_call_id": search_id, "message_id": result_id, "name": f"MCP: search_pages ({found[1]})",
                                "raw_name": found[0], "runtime_name": found[0], "args": {"query": "roadmap"}}
            append_checkpoint_messages(thread, [ToolMessage(id=result_id, tool_call_id=search_id, name=found[0],
                                                          content="Found the Roadmap page.")])
            yield "tool_done", {"tool_call_id": search_id, "message_id": result_id, "name": f"MCP: search_pages ({found[1]})",
                                "raw_name": found[0], "args": {"query": "roadmap"}, "content": "Found the Roadmap page."}
            append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":delete-call"), content="",
                tool_calls=[{"id": delete_id, "name": delete_name, "args": {"page": "Old notes"}}])])
            yield "tool_call", {"tool_call_id": delete_id, "name": f"MCP: delete_page ({found[1]})", "raw_name": delete_name,
                                "runtime_name": delete_name, "args": {"page": "Old notes"}}
            yield "interrupt", [{"__interrupt_id": fixture_id(identity + ":approval"), "tool": delete_name,
                                 "label": "Delete a page", "description": "Delete a page for good.",
                                 "args": {"page": "Old notes"}}]
        finally:
            call["quiesced"] = True
        return
    if "connect fixture" in text:
        # The work needs an app: suggest_apps leaves a Connect card with apps from the local catalog.
        from row_bot.threads import append_checkpoint_messages
        from row_bot.tools.conversation_setup_tool import suggest_apps
        call = predecessor._record("submit", config, "connect-card")
        thread = call["conversation_id"]
        identity = f"connect:{call['generation_id']}"
        tool_id, tool_message = fixture_id(identity + ":tool"), fixture_id(identity + ":result")
        args = {"need": text.split("connect fixture", 1)[1].strip(" :") or "Notion"}
        try:
            append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":tool-call"), content="",
                tool_calls=[{"id": tool_id, "name": "suggest_apps", "args": args}])])
            yield "tool_call", {"tool_call_id": tool_id, "message_id": tool_message,
                                "name": "suggest_apps", "args": args}
            result = suggest_apps(**args)
            append_checkpoint_messages(thread, [ToolMessage(id=tool_message, tool_call_id=tool_id,
                                                          name="suggest_apps", content=result)])
            yield "tool_done", {"tool_call_id": tool_id, "message_id": tool_message,
                                "name": "suggest_apps", "args": args, "content": result}
            yield from _natural_final(call, thread, f"Connect {args['need']} and I'll pick this up.", "connect")
        finally:
            call["quiesced"] = True
        return
    if "natural code fixture" in text or "natural design fixture" in text:
        # The model decides the work needs a code folder or a design and calls
        # the real conversation_setup tool; nothing is created from wording.
        from row_bot import agent
        from row_bot.threads import append_checkpoint_messages
        from row_bot.tools.conversation_setup_tool import create_code_folder, create_design
        call = predecessor._record("submit", config, "natural-resource")
        thread = call["conversation_id"]
        agent._set_active_runtime_context(thread_id=thread, runtime_surface="normal_chat",
            approval_mode=config["configurable"]["approval_mode"],
            agent_run_id=config["configurable"].get("agent_run_id", ""))
        code = "natural code fixture" in text
        name = "create_code_folder" if code else "create_design"
        args = {"name": "Landing page"} if code else {"design_type": "deck", "name": "Fixture deck"}
        identity = f"natural:{call['generation_id']}"
        tool_id, tool_message = fixture_id(identity + ":tool"), fixture_id(identity + ":result")
        try:
            append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":tool-call"), content="",
                tool_calls=[{"id": tool_id, "name": name, "args": args}])])
            yield "tool_call", {"tool_call_id": tool_id, "message_id": tool_message, "name": name, "args": args}
            result = create_code_folder(**args) if code else create_design(**args)
            append_checkpoint_messages(thread, [ToolMessage(id=tool_message, tool_call_id=tool_id,
                                                          name=name, content=result)])
            yield "tool_done", {"tool_call_id": tool_id, "message_id": tool_message, "name": name,
                                "args": args, "content": result}
            final = ("Setting up a code folder for it." if code else "Setting up a deck for it.")
            yield from _natural_final(call, thread, final, "reply")
        finally:
            call["quiesced"] = True
        return
    if any(marker in text for marker in ("rich fixture", "steering fixture", "burst fixture", "cadence fixture", "exhaustion fixture", "target fixture", "fail first draft")):
        from row_bot.threads import append_checkpoint_messages, get_latest_checkpoint_messages
        settings = config["configurable"]
        thread = str(settings["thread_id"])
        identity = str(settings["platform_submission_id"])
        if not any(str(message.id) in {identity, "user:submission:" + identity}
                   for message in get_latest_checkpoint_messages(thread)):
            append_checkpoint_messages(thread, [HumanMessage(id=identity, content=text)])
    if any(marker in text for marker in ("exhaustion fixture", "target fixture", "fail first draft")):
        from row_bot.threads import append_checkpoint_messages, get_latest_checkpoint_revision
        from row_bot.conversation_resources import current_execution_context
        case = "exhaustion" if "exhaustion fixture" in text else "target" if "target fixture" in text else "first-draft-failure"
        call = predecessor._record("submit", config, case)
        context = current_execution_context()
        call["accepted_binding_ids"] = [binding.binding_id for binding in context.bindings] if context else []
        try:
            if case == "first-draft-failure":
                yield "error", "Synthetic first-draft provider failure"
                return
            if case == "exhaustion":
                if "timed exhaustion fixture" in text:
                    start = threading.Event()
                    _expiry_starts[call["barrier_id"]] = start
                    if not start.wait(180):
                        raise TimeoutError("Timed expiry observers were not prepared")
                chunks = [f"Tick {index:04d}. " for index in range(5000)]
                call["emitted_tokens"] = 0
                for chunk in chunks:
                    yield "token", chunk
                    call["emitted_tokens"] += 1
                partial = "".join(chunks)
            else:
                partial = "Captured target is active."
                yield "token", partial
            if not predecessor._barriers[call["barrier_id"]].wait(180):
                raise TimeoutError("Phase 3 bounded execution fixture was not released")
            if stop_event is not None and stop_event.is_set():
                return
            context = current_execution_context()
            call["final_binding_ids"] = [binding.binding_id for binding in context.bindings] if context else []
            suffix = " Exhaustion settled." if case == "exhaustion" else " Captured target settled."
            final = partial + suffix
            native_id = fixture_id("phase3:" + call["generation_id"] + ":final")
            append_checkpoint_messages(call["conversation_id"], [AIMessage(id=native_id, content=final)])
            yield "token", suffix
            yield "output_binding", {"native_message_id": native_id,
                "checkpoint_revision": get_latest_checkpoint_revision(call["conversation_id"])}
            yield "done", final
        finally:
            _expiry_starts.pop(call["barrier_id"], None)
            call["quiesced"] = True
        return
    if "steering fixture" in text:
        yield from steering_stream(config, stop_event=stop_event)
        return
    if "cadence fixture" in text:
        yield from cadence_stream(config, stop_event=stop_event)
        return
    if "burst fixture" in text:
        call = predecessor._record("submit", config, "burst")
        try:
            yield "thinking", None
            for index in range(160):
                yield "token", f"Burst {index:03d}. "
            yield "token", "Burst complete; waiting for Stop."
            if stop_event is None or not stop_event.wait(180):
                raise TimeoutError("Burst producer must be stopped by its owner")
        finally:
            call["quiesced"] = True
        return
    if "rich fixture" not in text:
        yield from predecessor.stream(text, enabled_tools, config, stop_event=stop_event)
        return
    from row_bot.application.attachment_context import current_caches
    from row_bot.application.generated_media import capture_generated_media
    from row_bot.threads import append_checkpoint_messages, get_latest_checkpoint_revision

    call = predecessor._record("submit", config, "tools-media")
    thread_id = call["conversation_id"]
    identity = f"phase3:{thread_id}:{call['sequence']}"
    tool_id, tool_message = fixture_id(identity + ":tool"), fixture_id(identity + ":result")
    try:
        yield "thinking", None
        yield "token", "Synthetic tools and media are ready."
        append_checkpoint_messages(thread_id, [AIMessage(id=fixture_id(identity + ":tool-call"), content="",
            tool_calls=[{"id": tool_id, "name": "fixture_image", "args": {}}])])
        yield "tool_call", {
            "tool_call_id": tool_id,
            "message_id": tool_message,
            "name": "fixture_image",
        }
        if not predecessor._barriers[call["barrier_id"]].wait(180):
            raise TimeoutError("Phase 3 tools/media producer was not released")
        if stop_event is not None and stop_event.is_set():
            return
        caches = current_caches()
        if caches is None or caches.conversation_id != thread_id:
            raise AssertionError("Phase 3 media fixture must use the real execution attachment scope")
        caches.pending_image = _IMAGE
        if "rich fixture video" in text:
            from row_bot.application.generated_media import save_generated_output
            video_data = (Path(__file__).parents[2] / "fixtures/client_synthetic_video.mp4").read_bytes()
            caches.pending_video = {"path": save_generated_output(
                thread_id, video_data, prefix="synthetic-browser", extension="mp4")}
        media = capture_generated_media(thread_id, caches)
        # Saved on the tool message as the real tool wrapper saves it, so a transcript re-read keeps the image.
        append_checkpoint_messages(thread_id, [ToolMessage(id=tool_message, tool_call_id=tool_id,
                                                          content="Synthetic image created.",
                                                          additional_kwargs={"platform_media": media} if media else {})])
        yield "tool_done", {
            "tool_call_id": tool_id,
            "message_id": tool_message,
            "name": "fixture_image",
            "media": media,
        }
        final = "Synthetic tools and media are ready.\n\n```python\nprint('local fixture')\n```"
        yield "token", "\n\n```python\nprint('local fixture')\n```"
        native_id = fixture_id(identity + ":final")
        append_checkpoint_messages(thread_id, [AIMessage(content=final, id=native_id)])
        yield "output_binding", {"native_message_id": native_id,
                                 "checkpoint_revision": get_latest_checkpoint_revision(thread_id)}
        yield "done", final
    finally:
        call["quiesced"] = True


def cadence_stream(config: dict, *, stop_event=None):
    """Advance actual streamed output only on explicit synthetic test input."""
    from row_bot.threads import append_checkpoint_messages, get_latest_checkpoint_revision
    call = predecessor._record("submit", config, "cadence")
    feed: queue.Queue[str] = queue.Queue(maxsize=256)
    _token_feeds[call["barrier_id"]] = feed
    text = "Cadence stream ready."
    try:
        yield "token", text
        while not predecessor._barriers[call["barrier_id"]].is_set():
            if stop_event is not None and stop_event.is_set():
                return
            try:
                token = feed.get(timeout=0.05)
            except queue.Empty:
                continue
            text += token
            yield "token", token
        native = fixture_id("cadence-final:" + call["generation_id"])
        append_checkpoint_messages(call["conversation_id"], [AIMessage(id=native, content=text)])
        yield "output_binding", {"native_message_id": native,
                                 "checkpoint_revision": get_latest_checkpoint_revision(call["conversation_id"])}
        yield "done", text
    finally:
        _token_feeds.pop(call["barrier_id"], None)
        call["quiesced"] = True


@app.post("/__p3_fixture/cadence/{barrier_id}/{index}")
def advance_cadence(barrier_id: str, index: int, x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    feed = _token_feeds.get(barrier_id)
    if feed is None or index < 0 or index > 1000:
        raise HTTPException(status_code=404)
    try:
        feed.put_nowait(f" Token {index:03d}.")
    except queue.Full:
        raise HTTPException(status_code=429) from None
    return {"accepted": index}


def steering_stream(config: dict, *, stop_event=None):
    """Real orchestration/run persistence and receipts; parent execution is fake."""
    from row_bot import agent_orchestrator as owner, agent_runs
    from row_bot.threads import append_checkpoint_messages, get_latest_checkpoint_revision, create_thread
    call = predecessor._record("submit", config, "steering")
    thread = call["conversation_id"]
    run_id = fixture_id("phase3-child:" + call["generation_id"])
    row = owner.create_or_get_orchestration(parent_thread_id=thread,
        parent_generation_id=call["generation_id"], root_objective="Synthetic bounded child work",
        model_ref="ollama:fixture", approval_mode="block", runtime_surface="normal_chat",
        orchestration_version=2)
    child_thread = fixture_id("phase3-child-thread:" + run_id)
    create_thread(thread_id=child_thread, name="Synthetic child conversation",
                  name_source="manual", seed_default_skills=False)
    append_checkpoint_messages(child_thread, [HumanMessage(
        id=fixture_id("phase3-child-prompt:" + run_id), content="Synthetic delegated objective")])
    child = agent_runs.create_agent_run(run_id=run_id, status="running", parent_thread_id=thread,
        thread_id=child_thread, prompt="Synthetic child work",
        display_name="Synthetic child", model_override="ollama:fixture")
    owner.register_member(row["id"], child["id"], required=True)
    _orchestrations[thread] = {"orchestration_id": row["id"], "child_id": child["id"],
                             "child_conversation_id": child_thread, "batches": []}
    owner.complete_parent_pass(row["id"], "Synthetic child is working.", foreground=True)
    try:
        yield "thinking", None
        yield "token", "Synthetic child is working."
        if not predecessor._barriers[call["barrier_id"]].wait(180):
            raise TimeoutError("Steering fixture producer was not released")
        if stop_event is not None and stop_event.is_set():
            return
        final = "Synthetic child is working. Synthetic joined work complete."
        native_id = fixture_id("phase3-child-final:" + call["generation_id"])
        append_checkpoint_messages(thread, [AIMessage(id=native_id, content=final)])
        yield "token", " Synthetic joined work complete."
        yield "output_binding", {"native_message_id": native_id,
                                 "checkpoint_revision": get_latest_checkpoint_revision(thread)}
        yield "done", final
    finally:
        call["quiesced"] = True


@app.post("/__p3_fixture/orchestration/{conversation_id}/{action}")
def advance_orchestration(conversation_id: str, action: str, x_fixture_token: str = Header(default="")) -> dict:
    """Release one real parent pass or finish the synthetic execution boundary."""
    predecessor._authorize(x_fixture_token)
    from row_bot import agent_orchestrator as owner, agent_runs
    from dataclasses import asdict
    record = _orchestrations.get(conversation_id)
    if record is None or action not in {"state", "pass", "begin-pass", "release-pass", "finish-child"}:
        raise HTTPException(status_code=404)
    if action == "begin-pass":
        if conversation_id in _parent_threads:
            raise HTTPException(status_code=409)
        _parent_releases[conversation_id] = threading.Event()
        worker = threading.Thread(target=owner._run_parent_thread, args=(record["orchestration_id"],), daemon=True)
        _parent_threads[conversation_id] = worker
        worker.start()
    elif action == "release-pass":
        release = _parent_releases.get(conversation_id)
        if release is not None:
            release.set()
        worker = _parent_threads.get(conversation_id)
        if worker is not None:
            worker.join(timeout=10)
            if worker.is_alive():
                raise HTTPException(status_code=503)
    elif action == "pass":
        owner._run_parent_thread(record["orchestration_id"])
    elif action == "finish-child":
        agent_runs.finish_agent_run(record["child_id"], "completed", summary="Synthetic child result")
        owner._run_parent_thread(record["orchestration_id"])
    row = owner.get_orchestration(record["orchestration_id"])
    return {**record, "parent_state": row["parent_state"],
            "child_status": agent_runs.get_agent_run(record["child_id"])["status"],
            "steering": asdict(owner.read_parent_steering(conversation_id))}


@app.post("/__p3_fixture/large-library")
def large_library(x_fixture_token: str = Header(default="")) -> dict:
    """Seed 1,001 ordinary conversations and 10,001 durable messages once."""
    predecessor._authorize(x_fixture_token)
    global _large_library
    if _large_library is not None:
        return _large_library
    from row_bot import threads

    identifiers = []
    for index in range(1001):
        identifier = fixture_id(f"phase3-library:{index}")
        threads.create_thread(thread_id=identifier, name=f"Library conversation {index:04d}",
                              name_source="manual", seed_default_skills=False)
        identifiers.append(identifier)
    target = identifiers[0]
    def mixed_history(prefix: str, count: int, short: bool = False):
        messages = []
        for index in range(count):
            identity = fixture_id(f"{prefix}:{index}")
            text = (f"Short history row {index:04d}" if short else
                    f"History row {index:05d}" + (" archival needle" if index in {7, 9999} else ""))
            shape = (count - 1 - index) % 20
            if shape == 5:
                messages.append(AIMessage(id=identity, content=text, tool_calls=[{
                    "id": fixture_id(f"{prefix}:tool:{index}"), "name": "fixture_inspect", "args": {}}]))
            elif shape == 4:
                messages.append(ToolMessage(id=identity, content=text,
                    tool_call_id=fixture_id(f"{prefix}:tool:{index - 1}")))
            elif shape == 3:
                messages.append(AIMessage(id=identity, content=[{"type": "text", "text": text},
                    {"type": "text", "text": "[Synthetic media placeholder: bounded image output]"}]))
            else:
                messages.append((HumanMessage if index % 2 == 0 else AIMessage)(id=identity, content=text))
        return messages
    messages = mixed_history("phase3-history", 10001)
    if not threads.append_checkpoint_messages(target, messages):
        raise HTTPException(status_code=500, detail="Synthetic checkpoint seed rejected")
    short = identifiers[1]
    short_messages = mixed_history("phase3-short", 1000, True)
    if not threads.append_checkpoint_messages(short, short_messages):
        raise HTTPException(status_code=500, detail="Synthetic short checkpoint seed rejected")
    oversized = identifiers[2]
    oversized_id = fixture_id("phase3-oversized:0")
    oversized_text = "FIRST synthetic public text | " + "\u00e9\U0001f9ea\u6f22\u5b57|" * 6000 + " | MIDDLE marker | " + "\u03a9\U0001f642\u754c\u00e9|" * 6000 + " | END synthetic public text"
    if not threads.append_checkpoint_messages(oversized, [AIMessage(id=oversized_id, content=oversized_text)]):
        raise HTTPException(status_code=500, detail="Synthetic oversized checkpoint seed rejected")
    _large_library = {"conversation_count": len(identifiers), "message_count": len(messages),
                      "conversation_id": target, "message_id": fixture_id("phase3-history:7"),
                      "short_conversation_id": short, "short_last_message_id": fixture_id("phase3-short:999"),
                      "query": "archival needle",
                      "oversized_conversation_id": oversized, "oversized_message_id": oversized_id,
                      "oversized_public_sha256": hashlib.sha256((oversized_text + "\n").encode()).hexdigest(),
                      "oversized_public_characters": len(oversized_text + "\n")}
    return _large_library


@app.post("/__p3_fixture/expiry/{barrier_id}/start")
def start_expiry(barrier_id: str, x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    start = _expiry_starts.get(barrier_id)
    if start is None:
        raise HTTPException(status_code=404)
    start.set()
    return {"started": True}


_client_security: dict = {}


@app.post("/__p4_fixture/sessions/forget")
def forget_client_sessions(x_fixture_token: str = Header(default="")) -> dict:
    """Lose every client session and cursor key, as a server restart does."""
    predecessor._authorize(x_fixture_token)
    import secrets
    security = _client_security.get("security")
    if security is None:
        raise HTTPException(status_code=503)
    with security._lock:
        forgotten = len(security._sessions)
        security._sessions.clear()
        security._subscriptions.clear()
        security._nonces.clear()
        security._key = secrets.token_bytes(32)
    return {"forgotten": forgotten}


@app.get("/__p3_fixture/conversation/{conversation_id}")
def conversation_state(conversation_id: str, x_fixture_token: str = Header(default="")) -> dict:
    """Observe only test-owned IDs; credentials and private paths never returned."""
    predecessor._authorize(x_fixture_token)
    from row_bot.application.client_platform import client_platform_service
    from row_bot.application.workspace_setup import conversation_workspace
    from row_bot.application.conversation_drafts import read_draft

    return {"conversation": client_platform_service.get_conversation(conversation_id),
            "snapshot": client_platform_service.snapshot(conversation_id),
            "draft": read_draft(client_platform_service, conversation_id),
            "workspace": conversation_workspace(client_platform_service, conversation_id)}


# Phase 4 controls stay inside the already-isolated synthetic data directory.
_p4_workspace_failures: set[str] = set()
_p4_workspace_names: dict[str, str] = {}
_p4_workspace_lock = threading.Lock()
_p4_workspace_save_installed = False


def _p4_workspace_folder(name: str) -> Path:
    import re
    from row_bot.developer.review import scoped_workspace_path
    if not re.fullmatch(r"phase4-[a-z0-9-]{1,80}", name):
        raise HTTPException(status_code=422, detail="Invalid synthetic folder name")
    parent = scoped_workspace_path(predecessor.DATA / "fixture-workspace")
    folder = parent / name
    try:
        folder.lstat()
    except FileNotFoundError:
        return folder
    return scoped_workspace_path(parent, name)


def _p4_registered_folder(resource_id: str, workspace) -> Path:
    """Resolve only disposable fixture workspaces, including local drafts."""
    from row_bot.developer import storage
    from row_bot.developer.review import scoped_workspace_path
    folder = Path(workspace.path).absolute()
    name = folder.name
    drafts = predecessor.DATA / "attachment-workspace" / "Drafts"
    # Drafts are named from the request ("Code folder", "Tiny date app 2"),
    # so a local draft is known by where it lives, not by its name.
    if folder.parent == drafts.absolute():
        expected = scoped_workspace_path(scoped_workspace_path(drafts), name)
    else:
        expected = _p4_workspace_folder(name)
    if (folder != expected.absolute()
            or storage._workspace_id_for_path(expected) != resource_id):
        raise HTTPException(status_code=404, detail="Unknown synthetic workspace")
    return expected


@app.get("/__p4_fixture/workspace-parent")
def p4_workspace_parent(x_fixture_token: str = Header(default="")) -> dict:
    """The synthetic parent a spec's stand-in desktop bridge "chooses"."""
    predecessor._authorize(x_fixture_token)
    from row_bot.developer.review import scoped_workspace_path
    parent = scoped_workspace_path(predecessor.DATA / "fixture-workspace")
    parent.mkdir(parents=True, exist_ok=True)
    return {"path": str(parent)}


@app.post("/__p4_fixture/workspace-save-failure/{folder_name}")
def p4_workspace_save_failure(folder_name: str, x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    from row_bot.developer import storage
    global _p4_workspace_save_installed
    folder = _p4_workspace_folder(folder_name)
    if folder.exists():
        raise HTTPException(status_code=409, detail="Synthetic folder already exists")
    resource_id = storage._workspace_id_for_path(folder)
    with _p4_workspace_lock:
        if not _p4_workspace_save_installed:
            original = storage.save_workspace
            def save(workspace):
                with _p4_workspace_lock:
                    name = Path(workspace.path).name
                    if (name in _p4_workspace_failures
                            and Path(workspace.path).absolute() == _p4_workspace_folder(name).absolute()):
                        _p4_workspace_failures.remove(name)
                        raise OSError("Synthetic one-shot workspace registration failure")
                return original(workspace)
            storage.save_workspace = save
            _p4_workspace_save_installed = True
        if len(_p4_workspace_names) >= 128:
            raise HTTPException(status_code=409, detail="Synthetic control capacity reached")
        _p4_workspace_failures.add(folder_name)
        _p4_workspace_names[resource_id] = folder_name
    return {"resource_id": resource_id, "armed": True}


@app.get("/__p4_fixture/resources/{resource_id}/state")
def p4_workspace_state(resource_id: str, x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    from itertools import islice
    from row_bot.developer import storage
    from row_bot.conversation_resources import _identifier
    _identifier(resource_id)
    workspace = storage.get_workspace(resource_id)
    with _p4_workspace_lock:
        name = _p4_workspace_names.get(resource_id)
    if workspace is not None:
        folder = _p4_registered_folder(resource_id, workspace)
        name = folder.name
    if not name:
        raise HTTPException(status_code=404, detail="Unknown synthetic workspace")
    folder = _p4_registered_folder(resource_id, workspace) if workspace is not None else _p4_workspace_folder(name)
    if (storage._workspace_id_for_path(folder) != resource_id
            or workspace is not None and Path(workspace.path).absolute() != folder.absolute()):
        raise HTTPException(status_code=404, detail="Unknown synthetic workspace")
    children = list(islice(folder.iterdir(), 101)) if folder.is_dir() else []
    info = folder.stat() if folder.is_dir() else None
    return {"resource_id": resource_id, "kind": "workspace", "registered": workspace is not None,
            "origin_id": workspace.origin_conversation_id if workspace else None,
            "exists": info is not None,
            "directory_identity": hashlib.sha256(f"{info.st_dev}:{info.st_ino}".encode()).hexdigest() if info else None,
            "children": sorted(child.name[:120] for child in children[:100]), "has_more": len(children) > 100,
            "git_present": (folder / ".git").exists()}


@app.post("/__p4_fixture/resources/{resource_id}/repository")
def p4_seed_repository(resource_id: str, x_fixture_token: str = Header(default="")) -> dict:
    """Initialize one disposable workspace as a local Git repository."""
    p4_workspace_state(resource_id, x_fixture_token)
    import subprocess
    from row_bot.developer import storage

    workspace = storage.get_workspace(resource_id)
    if workspace is None or workspace.origin_conversation_id is None:
        raise HTTPException(status_code=404, detail="Unknown synthetic workspace")
    folder = _p4_registered_folder(resource_id, workspace)
    marker = folder / "repository-fixture.txt"
    if not (folder / ".git").exists():
        marker.write_text("Synthetic repository fixture\n", encoding="utf-8")
        commands = (
            ["git", "init", "--initial-branch=main", str(folder)],
            ["git", "-C", str(folder), "config", "user.name", "Row-Bot Fixture"],
            ["git", "-C", str(folder), "config", "user.email", "fixture@invalid.local"],
            ["git", "-C", str(folder), "add", "repository-fixture.txt"],
            ["git", "-C", str(folder), "commit", "-m", "Synthetic initial commit"],
        )
        for command in commands:
            try:
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise HTTPException(
                    status_code=409,
                    detail=f"Synthetic Git setup failed: {type(error).__name__}",
                ) from None
    return p4_repository_state(resource_id, x_fixture_token)


@app.get("/__p4_fixture/resources/{resource_id}/repository")
def p4_repository_state(resource_id: str, x_fixture_token: str = Header(default="")) -> dict:
    """Observe bounded repository owner state without returning a host path."""
    p4_workspace_state(resource_id, x_fixture_token)
    from row_bot.application.developer_repository_commands import read_developer_repository
    from row_bot.developer import storage

    workspace = storage.get_workspace(resource_id)
    if workspace is None or workspace.origin_conversation_id is None:
        raise HTTPException(status_code=404, detail="Unknown synthetic workspace")
    snapshot = read_developer_repository(
        resource_id,
        workspace.origin_conversation_id,
        validate=lambda: None,
    )
    return {
        "resource_id": snapshot["resource_id"],
        "conversation_id": snapshot["conversation_id"],
        "repository": snapshot["repository"],
        "worktrees": snapshot["worktrees"],
        "sandbox": snapshot["sandbox"],
        "available": {
            action: value["available"]
            for action, value in snapshot["availability"].items()
        },
    }


@app.post("/__p4_fixture/resources/{resource_id}/edit-file")
def p4_seed_edit_file(resource_id: str, x_fixture_token: str = Header(default="")) -> dict:
    p4_workspace_state(resource_id, x_fixture_token)
    from row_bot.developer import storage
    workspace = storage.get_workspace(resource_id)
    folder = _p4_workspace_folder(Path(workspace.path).name)
    with (folder / "sample.txt").open("xb") as handle:
        handle.write(b"Synthetic original\n")
    return {"seeded": True}


@app.get("/__p4_fixture/resources/{resource_id}/edit-file")
def p4_read_edit_file(resource_id: str, x_fixture_token: str = Header(default="")) -> dict:
    p4_workspace_state(resource_id, x_fixture_token)
    from row_bot.developer import storage
    from row_bot.developer.edits import read_edit_bytes
    workspace = storage.get_workspace(resource_id)
    folder = _p4_workspace_folder(Path(workspace.path).name)
    data, digest, _, _ = read_edit_bytes(folder, "sample.txt")
    if data is None or len(data) > 4096:
        raise HTTPException(status_code=409, detail="Invalid synthetic file")
    return {"content": data.decode("utf-8"), "sha256": digest,
            "retained_original": any(p.name == "previous" and p.read_bytes() == b"Synthetic original\n"
                for p in folder.glob(".row-bot-edit-recovery/*/previous"))}


@app.post("/__p4_fixture/resources/{resource_id}/process-probe")
def p4_process_probe(resource_id: str, x_fixture_token: str = Header(default="")) -> dict:
    p4_workspace_state(resource_id, x_fixture_token)
    from row_bot.developer import storage
    workspace = storage.get_workspace(resource_id)
    folder = _p4_workspace_folder(Path(workspace.path).name)
    # This harmless real child waits for explicit Stop, with a bounded fallback.
    # Its entire workspace and process tree belong to the disposable fixture.
    with (folder / "process_probe.py").open("x", encoding="utf-8") as handle:
        handle.write("import threading\nprint('Synthetic process ready', flush=True)\nthreading.Event().wait(90)\n")
    return {"command": "python -I -S process_probe.py"}


@app.post("/__p4_fixture/resources/{resource_id}/import-probe")
def p4_import_probe(resource_id: str, x_fixture_token: str = Header(default="")) -> dict:
    p4_workspace_state(resource_id, x_fixture_token)
    from row_bot.developer import storage, sandbox_runtime
    workspace = storage.get_workspace(resource_id)
    folder = _p4_workspace_folder(Path(workspace.path).name)
    if not sandbox_runtime.PENDING_CHANGES_PATH.absolute().is_relative_to(predecessor.DATA.absolute()):
        raise HTTPException(status_code=403, detail="Synthetic pending store required")
    before = {'original.txt': 'Synthetic original\n'}
    after = {'original.txt': 'Synthetic imported\n', 'new/nested/empty.txt': ''}
    with (folder / 'original.txt').open('xb') as stream:
        stream.write(before['original.txt'].encode())
    pending = sandbox_runtime._record_pending_change(workspace, workspace.origin_conversation_id, 'Synthetic pending import', before, after)
    return {'pending_change_id': pending.id}


@app.get("/__p4_fixture/resources/{resource_id}/import-probe")
def p4_import_probe_state(resource_id: str, x_fixture_token: str = Header(default="")) -> dict:
    p4_workspace_state(resource_id, x_fixture_token)
    from row_bot.developer import storage, sandbox_runtime, change_ledger
    workspace = storage.get_workspace(resource_id)
    folder = _p4_workspace_folder(Path(workspace.path).name)
    pending = sandbox_runtime.read_pending_import_rows(resource_id, workspace.origin_conversation_id)
    empty = folder / 'new/nested/empty.txt'
    data = (folder / 'original.txt').read_bytes()
    if len(data) > 4096:
        raise HTTPException(status_code=409, detail="Invalid synthetic import size")
    return {'content': data.decode(), 'empty_created': empty.is_file() and empty.stat().st_size == 0,
        'directories_retained': (folder / 'new/nested').is_dir(),
        'reverted': any(change.reverted for change in change_ledger.list_change_sets(workspace_id=resource_id, include_reverted=True)),
        'imported': len(pending) == 1 and pending[0]['imported'],
        'change_sets': len(change_ledger.list_change_sets(workspace_id=resource_id, include_reverted=True)),
        'retained_original': any(file.read_bytes() == b'Synthetic original\n' for file in folder.glob('.row-bot-edit-recovery/*/previous'))}


@app.post("/__p4_fixture/artifacts/{resource_id}/interaction-pages")
def p4_artifact_interaction_pages(resource_id: str, x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    from row_bot.designer import client_service, storage
    from row_bot.designer.state import DesignerPage
    if not storage.PROJECTS_DIR.resolve().is_relative_to(predecessor.DATA.resolve()):
        raise HTTPException(status_code=403, detail="Synthetic data scope required")
    project = client_service.read_artifact(resource_id)
    if not project.name.startswith("phase4-") or project.mode not in {"landing", "app_mockup", "storyboard"}:
        raise HTTPException(status_code=403, detail="Synthetic interactive artifact required")
    project.pages = [DesignerPage(title="Fixture first", route_id="first", html='''<html><body>
        <h1>Fixture first route</h1><button data-row-bot-action="navigate:second">Go to second route</button>
        <button aria-pressed="false" data-row-bot-action="toggle_state:expanded">Toggle details</button>
        <script data-row-bot-runtime="1">window.__QA_AUTHORED_SCRIPT_EXECUTED__=true;</script>
        <svg onload="window.__QA_AUTHORED_SCRIPT_EXECUTED__=true"></svg>
        </body></html>'''), DesignerPage(title="Fixture second", route_id="second", html='''<html><body>
        <h1>Fixture second route</h1><button data-row-bot-action="navigate:first">Return to first route</button>
        </body></html>''')]
    project.active_page = 0
    storage.save_project(project)
    return {"resource_id": project.id, "mode": project.mode, "revision": project.updated_at,
            "origin_id": project.thread_id, "fixture_routes": ["first", "second"]}


_p4_default_model_saved_catalog = None


@app.post("/__p4_fixture/catalog/{state}")
def p4_catalog(state: str, x_fixture_token: str = Header(default="")) -> dict:
    """Seed only saved synthetic metadata; never discover or refresh a provider."""
    predecessor._authorize(x_fixture_token)
    from row_bot.application import provider_default_model
    from row_bot.providers import model_catalog_cache as cache
    if _p4_default_model_saved_catalog is not None:
        provider_default_model._saved_catalog = _p4_default_model_saved_catalog
    if not cache.CATALOG_CACHE_PATH.resolve().is_relative_to(predecessor.DATA.resolve()):
        raise HTTPException(status_code=403, detail="Synthetic data scope required")
    if state not in {"populated", "empty", "changed"}:
        raise HTTPException(status_code=422, detail="Unknown synthetic catalog")
    cloud = {} if state == "empty" else {
        f"model:openai:phase4-{i:03}": {"provider": "openai", "model_id": f"phase4-{i:03}", "label": f"Saved example {i:03}",
            "capabilities_snapshot": {"tasks": ["chat"], "input_modalities": ["text"], "output_modalities": ["text"]}}
        for i in range(105)
    }
    if cloud:
        cloud["model:openai:" + "long-" + "x" * 507] = {"provider": "openai", "label": "Long model identity"}
    cache.write_model_catalog_cache(cache.CatalogCacheSnapshot(
        1, 1001.0 if state == "changed" else 1000.0, cloud, [],
        {"openai": {"status": "ok", "count": len(cloud)}}, (), "synthetic-browser",
    ))
    return {"state": state, "seeded_models": len(cloud)}


@app.post("/__p4_fixture/provider-credentials")
def p4_provider_credentials(x_fixture_token: str = Header(default="")) -> dict:
    """Use an in-memory secret backend only in this disposable fixture server."""
    predecessor._authorize(x_fixture_token)
    from row_bot import secret_store
    from row_bot.providers import auth_store, config
    if not Path(config.CONFIG_PATH).resolve().is_relative_to(predecessor.DATA.resolve()):
        raise HTTPException(status_code=403, detail="Synthetic data scope required")
    class SyntheticSecrets:
        def __init__(self): self.values = {}
        def get_password(self, service, account): return self.values.get((service, account))
        def set_password(self, service, account, value): self.values[service, account] = value
        def delete_password(self, service, account): self.values.pop((service, account), None)
    secret_store._backend_override = SyntheticSecrets()
    # Capture mode normally suppresses all secret reads. This explicit fixture
    # uses only the in-memory backend above, including staged-value readback.
    secret_store._docs_capture_active = lambda: False
    # Provider cards then report the seeded credential through the real status
    # path instead of the display-only capture cards.
    from row_bot.providers import live_settings
    live_settings.docs_capture_fake_provider_status = lambda: False
    for name in auth_store.PROVIDER_API_KEY_ENV.values():
        os.environ.pop(name, None)
    auth_store._session_provider_secrets.clear()
    auth_store.set_provider_secret('openai', 'api_key', 'synthetic-browser-old-credential')
    return {"ready": True}


@app.get("/__p4_fixture/document-upload/{batch_id}")
def p4_document_upload_state(batch_id: str, x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    import re
    from row_bot import document_jobs
    if not re.fullmatch(r'client_[a-f0-9]{32}', batch_id):
        raise HTTPException(status_code=422, detail='Invalid synthetic batch')
    jobs = document_jobs.DocumentJobService(predecessor.DATA)
    batch = jobs.get_batch(batch_id)
    files = []
    for job in jobs.list_jobs(batch_id):
        path = Path(job.staged_path)
        if not path.resolve().is_relative_to(predecessor.DATA.resolve()):
            raise HTTPException(status_code=403, detail='Synthetic document scope required')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        files.append({'name': job.original_name, 'size_bytes': path.stat().st_size,
            'sha256': actual, 'record_matches': actual == job.content_sha256, 'status': job.status})
    return {'paused': batch.pause_requested, 'status': batch.status, 'files': files}


_processing_fixture: dict = {}


@app.post("/__p4_fixture/document-processing")
def p4_document_processing(x_fixture_token: str = Header(default="")) -> dict:
    """Configure captured fakes; admission still uses the real authenticated API."""
    predecessor._authorize(x_fixture_token)
    from contextlib import contextmanager
    from types import SimpleNamespace
    from langchain_core.embeddings import Embeddings
    from row_bot import embedding_config, embedding_providers, documents, document_jobs, wiki_vault, threads, knowledge_graph
    from row_bot.providers import runtime, auth_store
    if document_jobs._supervisor is not None:
        raise HTTPException(status_code=409, detail='Synthetic worker scope required')
    jobs = document_jobs.DocumentJobService(predecessor.DATA)
    if not jobs.root.resolve().is_relative_to(predecessor.DATA.resolve()):
        raise HTTPException(status_code=403, detail='Synthetic data scope required')
    _processing_fixture.clear()
    _processing_fixture.update(embeddings=0, chats=0, starts=0, owner=None, source_embeddings=0)
    config = {**embedding_config.DEFAULT_CONFIG, 'provider': 'cloud',
        'cloud_model': 'openai:text-embedding-3-small', 'dimension': 1536}
    embedding_config.get_embedding_config = lambda: dict(config)
    embedding_providers.get_embedding_config = lambda: dict(config)
    runtime.get_provider_secret = lambda _provider: 'synthetic-processing-credential'
    auth_store.get_provider_secret = lambda provider, credential_name='api_key': (
        'synthetic-processing-credential'
        if provider == 'openai' and credential_name == 'api_key'
        else ''
    )
    documents.DOCUMENT_INDEX_DIR = predecessor.DATA / 'synthetic-processing-index'
    wiki_vault.is_enabled = lambda: False
    document_jobs._notify_batch_complete = lambda *_args: None
    document_jobs._wake_supervisor = lambda: None
    def start(owner):
        _processing_fixture['owner'] = owner
        _processing_fixture['starts'] += 1
    document_jobs.ensure_document_supervisor = start
    class SyntheticEmbeddings(Embeddings):
        def embed_documents(self, texts):
            _processing_fixture['embeddings'] += len(texts)
            _processing_fixture.setdefault('embedding_inputs', []).extend(texts)
            _processing_fixture['source_embeddings'] += sum('Synthetic reviewed source bytes' in text for text in texts)
            return [[1.0] + [0.0] * 1535 for _ in texts]
        def embed_query(self, text):
            return [1.0] + [0.0] * 1535
    @contextmanager
    def embedding(capture, *, validate):
        validate()
        yield embedding_providers._CapturedEmbeddings(SyntheticEmbeddings(), validate)
    @contextmanager
    def chat(capture, *, validate):
        validate()
        def invoke(messages):
            validate()
            _processing_fixture['chats'] += 1
            text = '[]' if _processing_fixture['chats'] % 3 == 0 else 'A detailed synthetic article about the reviewed document source information for ' + _processing_fixture['conversation']
            return SimpleNamespace(content=text)
        yield SimpleNamespace(invoke=invoke)
    embedding_providers.captured_embedding_provider = embedding
    runtime.captured_chat_model = chat
    def forbid(*args, **kwargs):
        raise AssertionError('Uncaptured provider construction is forbidden')
    runtime.create_chat_model = forbid
    # Settings › Documents processes without a conversation, reading with the
    # model picked for documents (as a person would pick it there).
    (predecessor.DATA / 'document_processing.json').write_text(
        json.dumps({'model': 'model:openai:gpt-4o'}), encoding='utf-8')
    identifier = threads.create_thread('Synthetic processing conversation', model_override='model:openai:gpt-4o',
        approval_mode='approve', seed_default_skills=False)
    threads.append_checkpoint_messages(identifier, [HumanMessage(content='Synthetic document processing conversation')])
    _processing_fixture['conversation'] = identifier
    connection = knowledge_graph._get_conn()
    try:
        readiness = knowledge_graph.memory_vector_status()
        if readiness['state'] not in {'ready', 'stale', 'missing'}:
            raise HTTPException(status_code=409, detail='Unexpected synthetic knowledge projection state')
        # The initial fixture generation uses a different embedding fingerprint;
        # its saved entity must be projected under this newly reviewed provider.
        pending = 0 if readiness['ready'] else sum(len(batch) for batch in knowledge_graph._projection_source_batches(connection, 50))
    finally:
        connection.close()
    return {'conversation_id': identifier, 'pending_knowledge_embeddings': pending}


@app.get("/__p4_fixture/document-processing")
def p4_document_processing_state(x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    return {key: _processing_fixture.get(key, 0) for key in ('embeddings', 'source_embeddings', 'chats', 'starts')}


@app.post("/__p4_fixture/document-processing/{batch_id}/run")
def p4_document_processing_run(batch_id: str, x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    from row_bot import document_jobs
    owner = _processing_fixture.get('owner')
    if owner is None or not owner.processing_admission(batch_id):
        raise HTTPException(status_code=409, detail='Reviewed admission required')
    supervisor = document_jobs.DocumentSupervisor(owner)
    # Exercise the actual admitted worker methods with captured fake providers.
    for _ in range(4):
        job = owner.claim_next('synthetic-browser-processing')
        if job is None:
            break
        supervisor._process_job(job)
    supervisor._finalize_ready_batches()
    return {'status': owner.get_batch(batch_id).status,
        'embedding_inputs': _processing_fixture.get('embedding_inputs', []),
        'jobs': [job.status for job in owner.list_jobs(batch_id)],
        **p4_document_processing_state(x_fixture_token)}


_queue_fixture: dict = {}


@app.post("/__p4_fixture/document-queue")
def p4_document_queue(x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    from row_bot import document_jobs
    jobs = document_jobs.DocumentJobService(predecessor.DATA)
    if not jobs.root.resolve().is_relative_to(predecessor.DATA.resolve()):
        raise HTTPException(status_code=403, detail="Synthetic document scope required")
    # Startup is disabled by this fixture. No provider worker may be woken.
    if document_jobs._supervisor is not None:
        raise HTTPException(status_code=409, detail="Synthetic worker scope required")
    document_jobs._wake_supervisor = lambda: None
    batch = jobs.create_batch()
    job = jobs.create_staging_job(batch, 0, 'Synthetic queue.txt')
    path = Path(job.staged_path)
    path.parent.mkdir(parents=True)
    data = b'Synthetic retained queue source'
    path.write_bytes(data)
    jobs.complete_staging(job.id, hashlib.sha256(data).hexdigest(), len(data), path)
    jobs.finish_batch_staging(batch)
    _queue_fixture.update(batch=batch, job=job.id, path=path)
    return {'batch_id': batch, 'job_id': job.id}


@app.get("/__p4_fixture/document-queue")
def p4_document_queue_state(x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    from row_bot import document_jobs
    jobs = document_jobs.DocumentJobService(predecessor.DATA)
    try:
        batch = jobs.get_batch(_queue_fixture['batch'])
        status, paused = batch.status, batch.pause_requested
    except KeyError:
        status, paused = None, None
    return {'status': status, 'paused': paused,
            'source_retained': _queue_fixture['path'].read_bytes() == b'Synthetic retained queue source'}


_installation_fixture: list[str] = []


@app.post("/__p4_fixture/runtime-installation")
def p4_runtime_installation(x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    import io
    import zipfile
    from uuid import uuid4
    from row_bot.mcp_client import requirements
    from row_bot.application.mcp_runtime_installation import _OPERATIONS
    if any(item.thread and item.thread.is_alive() for item in _OPERATIONS.values()):
        raise HTTPException(status_code=409, detail="Synthetic runtime still owned")
    requirements.RUNTIMES_DIR = predecessor.DATA / 'synthetic-runtimes' / str(uuid4())
    _installation_fixture.clear()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('bundle/node.exe', b'Synthetic runtime - never execute')
    data = output.getvalue()
    def resolve(runtime_id, *, validate, cancelled):
        validate()
        _installation_fixture.append('resolve')
        return requirements.make_archive_runtime_plan(runtime_id, version='1.2.3',
            url='https://example.invalid/node.zip', sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data), asset_name='node.zip', executable_candidates=('node.exe',))
    def download(url, destination, progress=None, *, validate=lambda: None):
        validate()
        import tempfile
        if (not destination.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
                or not destination.parent.name.startswith('row-bot-runtime-download-')):
            raise ValueError('Synthetic runtime scope required')
        _installation_fixture.append('download')
        destination.write_bytes(data)
    requirements.resolve_managed_runtime_plan = resolve
    requirements._download = download
    # No system copy, whatever this machine has: the page offers its one Install.
    requirements.system_runtime_available = lambda runtime_id: False
    return {'calls': list(_installation_fixture)}


@app.get("/__p4_fixture/runtime-installation")
def p4_runtime_installation_state(x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    from row_bot.mcp_client import requirements
    root = requirements._managed_bin_dir('node')
    return {'calls': list(_installation_fixture), 'installed': bool(root),
            'synthetic_bytes': bool(root and (root / 'node.exe').read_bytes() == b'Synthetic runtime - never execute')}


@app.post("/__p4_fixture/mcp-runtime")
def p4_mcp_runtime(x_fixture_token: str = Header(default="")) -> dict:
    """Use the canonical lifecycle with fake session transport and discovery."""
    predecessor._authorize(x_fixture_token)
    from types import SimpleNamespace
    from row_bot.mcp_client import config, runtime
    if not config.CONFIG_PATH.resolve().is_relative_to(predecessor.DATA.resolve()):
        raise HTTPException(status_code=403, detail="Synthetic data scope required")
    async def tools():
        return SimpleNamespace(tools=[])
    async def connect(server):
        server.session = SimpleNamespace(list_tools=tools)
    runtime.sdk_available = lambda: True
    runtime.McpServerRuntime._connect = connect
    saved = config.read_saved_configuration().document
    saved['enabled'] = True
    saved.setdefault('servers', {})['Synthetic lifecycle'] = {'enabled':True, 'command':'synthetic-never-launched'}
    config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CONFIG_PATH.write_text(json.dumps(saved), encoding='utf-8')
    config._config_cache = None
    return {'ready':True}


@app.post("/__p4_fixture/subscriptions")
def p4_subscriptions(x_fixture_token: str = Header(default="")) -> dict:
    """Real account publication with synthetic provider handshakes only."""
    p4_provider_credentials(x_fixture_token)
    from types import SimpleNamespace
    from row_bot.application.client_platform import client_platform_service
    from row_bot.application.subscription_controls import SubscriptionFlows
    from row_bot.providers import codex, claude_subscription, xai_oauth
    if not client_platform_service.subscription_flows.dispose():
        raise HTTPException(status_code=409, detail="Previous synthetic sign-in still draining")
    class Client:
        def close(self): pass
    def listener(_flow, *, ready_callback, **_kwargs):
        ready_callback()
        return SimpleNamespace()
    flows = SubscriptionFlows(client_factory=lambda _guard: Client(), listener=listener)
    def module(provider):
        real = {'codex': codex, 'claude_subscription': claude_subscription, 'xai_oauth': xai_oauth}[provider]
        synthetic = SimpleNamespace(**{name: getattr(real, name) for name in dir(real) if not name.startswith('__')})
        flow = SimpleNamespace(verification_uri='https://example.invalid/device' if provider == 'codex' else None,
            authorization_url='https://example.invalid/authorize', user_code='SYNTHETIC' if provider == 'codex' else None,
            expires_at='2030-01-01T00:00:00+00:00', code_verifier='synthetic-private-verifier',
            redirect_uri='http://127.0.0.1:9/callback', token_url='https://example.invalid/token',
            client_id='synthetic-client', state='synthetic-state')
        setattr(synthetic, {'codex':'start_codex_device_flow','claude_subscription':'start_claude_subscription_oauth_flow','xai_oauth':'start_xai_oauth_flow'}[provider], lambda **_kwargs: flow)
        tokens = {'codex':codex.CodexTokenSet, 'claude_subscription':claude_subscription.ClaudeSubscriptionTokenSet, 'xai_oauth':xai_oauth.XAIOAuthTokenSet}[provider]
        setattr(synthetic, {'codex':'exchange_codex_device_authorization','claude_subscription':'exchange_claude_subscription_authorization','xai_oauth':'exchange_xai_oauth_authorization'}[provider],
            lambda *_args, **_kwargs: tokens(access_token='synthetic-browser-access', refresh_token='synthetic-browser-refresh', account_id='synthetic-browser-account'))
        synthetic.poll_codex_device_authorization = lambda *_args, **_kwargs: SimpleNamespace()
        synthetic.authorization_from_xai_oauth_callback = lambda *_args: SimpleNamespace()
        return synthetic
    flows._module = module
    client_platform_service.subscription_flows = flows
    return {'ready': True}


_p4_catalog_calls: list[str] = []


@app.post("/__p4_fixture/mcp-catalog")
def p4_mcp_catalog(x_fixture_token: str = Header(default="")) -> dict:
    p4_mcp_runtime(x_fixture_token)
    from types import SimpleNamespace
    from row_bot.mcp_client import runtime
    _p4_catalog_calls.clear()
    async def tools():
        _p4_catalog_calls.append('list_tools')
        return SimpleNamespace(tools=[
            {'name':'get_record', 'description':'Read synthetic records', 'inputSchema':{'type':'object'}},
            {'name':'delete_record', 'description':'Delete synthetic records', 'inputSchema':{}},
            {'name':'unrecognized', 'description':'Unknown synthetic operation', 'inputSchema':{}}])
    async def connect(server):
        _p4_catalog_calls.append('connect')
        server.session = SimpleNamespace(list_tools=tools)
    runtime.McpServerRuntime._connect = connect
    return {'ready':True}


@app.get("/__p4_fixture/mcp-catalog")
def p4_mcp_catalog_calls(x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    return {'calls': list(_p4_catalog_calls)}


@app.post("/__p4_fixture/mcp-policy")
def p4_mcp_policy(x_fixture_token: str = Header(default="")) -> dict:
    p4_mcp_runtime(x_fixture_token)
    from row_bot.mcp_client import config
    saved = json.loads(config.CONFIG_PATH.read_text(encoding='utf-8'))
    saved['enabled'] = True
    saved['servers']['Synthetic lifecycle']['tools'] = {
        'enabled':{'read_record':True,'delete_record':False},
        'catalog':{'read_record':{'description':'Read synthetic records','destructive':False},
                   'delete_record':{'description':'Delete synthetic records','destructive':True}},
        'resources_enabled':False,'prompts_enabled':False}
    config.CONFIG_PATH.write_text(json.dumps(saved), encoding='utf-8')
    config._config_cache = None
    return {'ready':True}


@app.post("/__p4_fixture/buddy")
def p4_buddy(x_fixture_token: str = Header(default="")) -> dict:
    """Reset only disposable Buddy preferences; use the real bundled artwork."""
    predecessor._authorize(x_fixture_token)
    from row_bot.buddy import config
    if not config._BUDDY_CONFIG_PATH.resolve().is_relative_to(predecessor.DATA.resolve()):
        raise HTTPException(status_code=403, detail='Synthetic Buddy scope required')
    saved, _ = config.read_buddy_config_revision()
    saved.update(visible=True, collapsed=False, display_name='Buddy', pack_id='glyph',
                 personality='warm_mystical', bubble_verbosity='normal', animation_intensity='normal')
    config._BUDDY_CONFIG_PATH.write_text(json.dumps(saved), encoding='utf-8')
    return {'conversation_id': 'p1-browser-a'}


_p4_probe_release = threading.Event()
_p4_probe_entered = threading.Event()
_p4_probe_calls: list[str] = []
_p4_probe_execute = None


@app.post("/__p4_fixture/subscription-probes/{mode}")
def p4_subscription_probes(mode: str, x_fixture_token: str = Header(default="")) -> dict:
    """Exercise the real probe owner with synthetic model responses only."""
    global _p4_default_model_saved_catalog, _p4_probe_execute
    predecessor._authorize(x_fixture_token)
    if mode == 'release':
        _p4_probe_release.set()
        return {'released': True}
    if mode not in {'ready', 'blocked'}:
        raise HTTPException(status_code=422, detail='Unknown synthetic probe mode')
    p4_provider_credentials(x_fixture_token)
    from functools import partial
    from types import SimpleNamespace
    from row_bot.application import provider_default_model, subscription_probes
    from row_bot.application.client_platform import client_platform_service
    from row_bot.application.subscription_controls import SubscriptionFlows
    from row_bot.providers import codex
    if not client_platform_service.subscription_flows.dispose():
        raise HTTPException(status_code=409, detail='Previous synthetic checks still draining')
    client_platform_service.subscription_flows = SubscriptionFlows()
    _p4_probe_release.clear()
    _p4_probe_entered.clear()
    _p4_probe_calls.clear()
    codex.codex_auth_path = lambda *_args, **_kwargs: predecessor.DATA / 'synthetic-missing-codex-auth.json'
    if _p4_default_model_saved_catalog is None:
        _p4_default_model_saved_catalog = provider_default_model._saved_catalog
    provider_default_model._saved_catalog = lambda: SimpleNamespace(cloud_cache={
        'model:xai_oauth:grok-4': {'provider': 'xai_oauth', 'model_id': 'grok-4',
            'capabilities_snapshot': {'tasks': ['chat'], 'input_modalities': ['text', 'image'], 'output_modalities': ['text']}}
    }, ollama_rows=[])
    def factory(_provider, _model, _captured, _guard, _client, _stack):
        class Model:
            def bind_tools(self, *_a, **_kw): return self
            def invoke(self, _messages):
                _p4_probe_calls.append('synthetic-invoke')
                _p4_probe_entered.set()
                if mode == 'blocked' and not _p4_probe_release.wait(30):
                    raise RuntimeError('Synthetic probe release required')
                return SimpleNamespace(content='image row-bot-xai-smoke-ok', tool_calls=[
                    {'name': 'calculate', 'args': {'expression': '1 + 1'}, 'id': 'synthetic-call'}])
        return Model()
    if _p4_probe_execute is None:
        _p4_probe_execute = subscription_probes.execute_probe
    subscription_probes.execute_probe = partial(_p4_probe_execute, model_factory=factory)
    return {'ready': True}


@app.get("/__p4_fixture/subscription-probes")
def p4_subscription_probe_state(x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    return {'entered': _p4_probe_entered.is_set(), 'calls': len(_p4_probe_calls)}


@app.post("/__p4_fixture/subscription-options")
def p4_subscription_options(x_fixture_token: str = Header(default="")) -> dict:
    """Redirect explicit CLI reference reads to synthetic disposable files."""
    p4_provider_credentials(x_fixture_token)
    from row_bot.providers import codex, claude_subscription
    folder = predecessor.DATA / 'synthetic-cli'
    folder.mkdir(exist_ok=True)
    primary = folder / 'auth.json'
    primary.write_text(json.dumps({'access_token':'synthetic-browser-cli-token'}), encoding='utf-8')
    codex.codex_auth_path = lambda *_args, **_kwargs: primary
    claude_subscription.claude_credentials_path = lambda: primary
    claude_subscription.claude_legacy_credentials_path = lambda: folder / 'missing.json'
    return {'ready':True}


@app.post("/__p4_fixture/knowledge/{state}")
def p4_knowledge(state: str, x_fixture_token: str = Header(default="")) -> dict:
    """Seed saved synthetic rows without indexing, extracting or running a job."""
    predecessor._authorize(x_fixture_token)
    import sqlite3
    from row_bot.data_paths import get_memory_db_path, get_row_bot_data_dir
    if state not in {"populated", "empty"}:
        raise HTTPException(status_code=422, detail="Unknown synthetic knowledge state")
    if not get_memory_db_path(create_parent=False).resolve().is_relative_to(predecessor.DATA.resolve()) or get_row_bot_data_dir(create=False).resolve() != predecessor.DATA.resolve():
        raise HTTPException(status_code=403, detail="Synthetic data scope required")
    from row_bot import knowledge_graph as kg
    from row_bot.document_jobs import DocumentJobService
    kg._skip_reindex = True
    count = 105 if state == "populated" else 0
    with sqlite3.connect(kg.DB_PATH) as conn:
        conn.execute("DELETE FROM entities WHERE id LIKE 'p4-entity-%'")
        conn.executemany(
            "INSERT INTO entities (id,entity_type,subject,description,aliases,tags,properties,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(f"p4-entity-{i:03}", "fact", f"Phase 4 knowledge {i:03}",
              "Synthetic description " * 60 + ("tail needle" if i == 104 else ""),
              "Fixture alias" if i == 0 else "", "fixture" if i == 0 else "",
              json.dumps({
                  "status": "needs_review" if i == 0 else "archived" if i == 1 else "active",
                  "memory_tier": "core" if i == 0 else "episodic" if i == 1 else "semantic",
                  "confidence": 0.93 if i == 0 else None,
                  "review_reason": "Synthetic conflict for browser review" if i == 0 else "",
                  "source_context": {"actor": "extraction", "thread_name": "Fixture thread"},
                  "evidence": ["Synthetic browser evidence"],
              }), "extraction" if i == 0 else "manual" if i == 1 else "synthetic",
              "2026-01-01", "2026-01-01") for i in range(count)],
        )
        conn.execute("DELETE FROM relations WHERE id LIKE 'p4-relation-%'")
        if count:
            conn.execute(
                "INSERT INTO relations(id,source_id,target_id,relation_type,confidence,properties,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                ("p4-relation-000", "p4-entity-000", "p4-entity-001", "supports", 0.9, "{}", "synthetic", "2026-01-01", "2026-01-01"),
            )
    (predecessor.DATA / "memory_recall_trace.json").write_text(json.dumps([
        {"ts": "2026-01-02T10:00:00", "allowed": True, "reason": "Synthetic relevant memory",
         "candidates_seen": 2, "selected_ids": ["p4-entity-000"], "selected_count": 1,
         "block_chars": 240, "top_scores": [{"id": "p4-entity-000", "final": 0.93}],
         "rejected": [{"reason": "below threshold"}]}
    ] if count else []), encoding="utf-8")
    (predecessor.DATA / "memory_evolution_journal.json").write_text(json.dumps([
        {"timestamp": "2026-01-02T11:00:00", "action": "mark_needs_review", "actor": "extraction",
         "entity_ids": ["p4-entity-000"], "old_status": "active", "new_status": "needs_review",
         "reason": "Synthetic conflict for browser review"}
    ] if count else []), encoding="utf-8")
    service = DocumentJobService(predecessor.DATA)
    with sqlite3.connect(service.db_path) as conn:
        conn.execute("DELETE FROM document_records WHERE document_id IN (SELECT id FROM document_jobs WHERE original_name LIKE 'Phase 4 document %')")
        conn.execute("DELETE FROM document_jobs WHERE original_name LIKE 'Phase 4 document %'")
    if count:
        batch = service.create_batch()
        for i in range(count):
            service.create_staging_job(batch, i, f"Phase 4 document {i:03}.txt")
        with sqlite3.connect(service.db_path) as conn:
            # Deliberately inconsistent saved completion has no document record.
            conn.execute("UPDATE document_jobs SET status='completed',stage='finalize',completed_at='2026-01-01' WHERE original_name='Phase 4 document 104.txt'")
    return {"state": state, "seeded_entities": count, "seeded_documents": count}


@app.post("/__p4_fixture/document-removal")
def p4_document_removal(x_fixture_token: str = Header(default="")) -> dict:
    """Seed a synthetic searchable source and one controlled retirement failure."""
    predecessor._authorize(x_fixture_token)
    import hashlib
    from row_bot.data_paths import get_row_bot_data_dir
    if get_row_bot_data_dir(create=False).resolve() != predecessor.DATA.resolve():
        raise HTTPException(status_code=403, detail="Synthetic data scope required")
    from row_bot import document_index, document_jobs, documents, knowledge_graph as kg
    kg._skip_reindex = True
    jobs = document_jobs.DocumentJobService(predecessor.DATA)
    batch = jobs.create_batch()
    job = jobs.create_staging_job(batch, 0, 'Synthetic removal document.txt')
    source = Path(job.staged_path)
    source.parent.mkdir(parents=True, exist_ok=True)
    content = b'Synthetic document retained after reviewed cleanup.'
    source.write_bytes(content)
    jobs.complete_staging(job.id, hashlib.sha256(content).hexdigest(), len(content), source)
    jobs.finish_batch_staging(batch)
    jobs.transition_job(job.id, 'indexing')
    jobs.mark_searchable(job.id)
    jobs.mark_completed(job.id)
    document_index.initialize_index(documents.DOCUMENT_INDEX_DIR)
    manifest = document_index.read_corpus_manifest(documents.DOCUMENT_INDEX_DIR)
    manifest['documents'].append({'document_id':job.id,'generation':'synthetic-generation'})
    document_index._atomic_write_json(documents.DOCUMENT_INDEX_DIR / document_index.CORPUS_MANIFEST_NAME, manifest)
    live = documents.DOCUMENT_INDEX_DIR / 'documents' / job.id / 'synthetic-generation'
    live.mkdir(parents=True)
    (live / 'synthetic-vector-data').write_bytes(b'synthetic retained vector bytes')
    kg.save_entity('fact', 'Synthetic removed knowledge', 'Synthetic document fact.', source='document:' + job.id)
    original = document_index.remove_document_shard
    remaining = True
    def fail_once(document_id, *args, **kwargs):
        nonlocal remaining
        if document_id == job.id and remaining:
            if kwargs.get('validate') is not None:
                kwargs['validate']()
            remaining = False
            raise OSError('Synthetic document retirement interruption')
        return original(document_id, *args, **kwargs)
    document_index.remove_document_shard = fail_once
    return {'document_id':job.id, 'name':job.original_name}


@app.get("/__p4_fixture/document-removal/{document_id}")
def p4_document_removal_result(document_id: str, x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    import sqlite3
    from row_bot import document_jobs, document_index, documents, knowledge_graph as kg
    jobs = document_jobs.DocumentJobService(predecessor.DATA)
    result = jobs.latest_removal(document_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No synthetic removal")
    with sqlite3.connect(kg.DB_PATH) as connection:
        derived = connection.execute('SELECT COUNT(*) FROM entities WHERE source=?', ('document:' + document_id,)).fetchone()[0]
    manifest = document_index.read_corpus_manifest(documents.DOCUMENT_INDEX_DIR)
    copies = result['result']['retained_copies']
    return {'status':result['result']['status'], 'derived_count':derived,
        'indexed':any(row['document_id'] == document_id for row in manifest['documents']),
        'recovery_copies_retained':bool(copies) and all(Path(row['path']).exists() for row in copies),
        'historical_job_retained':jobs.get_job(document_id) is not None}


@app.post("/__p4_fixture/tasks/{state}")
def p4_tasks(state: str, x_fixture_token: str = Header(default="")) -> dict:
    """Populate only disposable saved task headers; do not schedule or run them."""
    predecessor._authorize(x_fixture_token)
    from row_bot import tasks
    if not Path(tasks._DB_PATH).resolve().is_relative_to(predecessor.DATA.resolve()):
        raise HTTPException(status_code=403, detail="Synthetic data scope required")
    if state not in {"populated", "empty", "changed"}:
        raise HTTPException(status_code=422, detail="Unknown synthetic tasks")
    conn = tasks._get_conn()
    try:
        conn.execute("DELETE FROM tasks WHERE id LIKE 'p4-task-%'")
        count = 0 if state == "empty" else 105
        conn.executemany(
            "INSERT INTO tasks (id,name,description,icon,prompts,created_at,enabled,notify_only,sort_order,channels) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(f"p4-task-{i:03}", f"Phase 4 saved task {i:03}",
              "Synthetic reminder" + (" changed" if state == "changed" else ""), "", "[]",
              "2026-01-01", 0, 1, i, "[]") for i in range(count)],
        )
        conn.commit()
    finally:
        conn.close()
    return {"state": state, "seeded_tasks": count}


@app.post("/__p4_fixture/tools/{state}")
def p4_tools(state: str, x_fixture_token: str = Header(default="")) -> dict:
    """Register inert synthetic metadata only in this disposable fixture process."""
    predecessor._authorize(x_fixture_token)
    from types import SimpleNamespace
    from row_bot.tools import registry
    if not Path(registry._active_config_path).resolve().is_relative_to(predecessor.DATA.resolve()):
        raise HTTPException(status_code=403, detail="Synthetic data scope required")
    if state not in {"populated", "empty", "changed"}:
        raise HTTPException(status_code=422, detail="Unknown synthetic tools")
    for identity in list(registry._tools):
        if identity.startswith("p4-tool-"):
            registry._tools.pop(identity)
            registry._enabled.pop(identity, None)
    count = 0 if state == "empty" else 105
    for i in range(count):
        identity = f"p4-tool-{i:03}"
        registry._tools[identity] = SimpleNamespace(
            name=identity, display_name=f"Phase 4 saved tool {i:03}",
            destructive_tool_names=frozenset(),
        )
        registry._enabled[identity] = state != "changed"
    return {"state": state, "seeded_tools": count}


_reasoning_fixture: dict = {}


@app.post("/__p4_fixture/reasoning-default")
def p4_reasoning_default(x_fixture_token: str = Header(default="")) -> dict:
    """Restore the one Thinking-capable synthetic model as the saved default.

    Earlier specs may choose another default; this publishes the choice the
    same way a reviewed save does, without creating or unloading clients.
    """
    predecessor._authorize(x_fixture_token)
    from row_bot import models
    from row_bot.providers import saved_model_settings
    if not Path(saved_model_settings.SETTINGS_PATH).resolve().is_relative_to(predecessor.DATA.resolve()):
        raise HTTPException(status_code=403, detail="Synthetic data scope required")
    reference = _reasoning_fixture.get("model")
    if not reference:
        raise HTTPException(status_code=409, detail="Reasoning fixture unavailable")
    saved_model_settings.update_saved_model_settings(lambda raw: {**raw, "model": reference})
    models.adopt_saved_default(reference)
    return {"model_ref": reference}



# ── Phase 10: first run ─────────────────────────────────────────────────────
# Setup detects Ollama, tests the chosen model, checks keys and loads provider
# catalogs. In this disposable server all of that is synthetic: nothing reaches
# a runtime, a provider or the network, and the real machine's Ollama is never
# seen (a fixed "not installed" unless a spec says otherwise).
_p10_state: dict = {"runtime": "not_installed", "models": [], "test": "ok", "backup": None, "active": False}
_P10_SYNTHETIC_CHAT = ("fixture-chat-1", "fixture-chat-2")


def _p10_install_fakes() -> None:
    from row_bot.application import client_first_run
    from row_bot.providers import model_catalog_cache as cache

    client_first_run.ollama_running = lambda: _p10_state["runtime"] == "running"
    client_first_run.ollama_installed = lambda: _p10_state["runtime"] != "not_installed"
    client_first_run.local_models = lambda: list(_p10_state["models"]) if _p10_state["runtime"] == "running" else []

    def invoke(_reference):
        if _p10_state["test"] != "ok":
            raise RuntimeError("synthetic model failed to load")
        return "ready"

    client_first_run.test_invoker = invoke
    client_first_run.key_validators = lambda: {
        # A key the spec marks "refused" is refused; any other synthetic key passes.
        provider: (lambda key: "refused" not in key)
        for provider in ("openai", "anthropic", "google", "openrouter", "xai")
    }

    def refresh(*, reason="manual", force=False, provider_id=None):
        """Write synthetic rows for the provider instead of contacting it."""
        snapshot = cache.read_model_catalog_cache()
        cloud = dict(snapshot.cloud_cache)
        ollama_rows = list(snapshot.ollama_rows)
        if provider_id == "ollama":
            ollama_rows = [
                {"provider_id": "ollama", "model_id": name, "display_name": name, "installed": True,
                 "capabilities_snapshot": {"tasks": ["chat"], "input_modalities": ["text"],
                                           "output_modalities": ["text"], "tool_calling": True}}
                for name in _p10_state["models"]
            ]
        elif provider_id:
            for model_id in _P10_SYNTHETIC_CHAT:
                cloud[f"model:{provider_id}:{model_id}"] = {
                    "provider": provider_id, "model_id": model_id, "label": f"Fixture chat {model_id[-1]}",
                    "capabilities_snapshot": {"tasks": ["chat"], "input_modalities": ["text"],
                                              "output_modalities": ["text"], "tool_calling": True},
                }
        updated = cache.CatalogCacheSnapshot(
            1, snapshot.generated_at + 1, cloud, ollama_rows,
            {provider_id or "all": {"status": "ok", "count": len(_P10_SYNTHETIC_CHAT)}}, (), "synthetic-browser",
        )
        cache.write_model_catalog_cache(updated)
        return updated

    cache.refresh_model_catalog_cache = refresh

    def start(*, reason="manual", provider_id=None, force=False):
        refresh(reason=reason, force=force, provider_id=provider_id)
        return True

    cache.start_model_catalog_refresh_background = start
    cache.model_catalog_refresh_state = lambda: {
        "running": False, "last_result": {"ok": True, "provider_id": "", "provider_status": {}},
    }

    # A real sign-in stores runnable tokens; the synthetic ones are not, so
    # while a first-run spec runs a signed-in subscription counts as runnable.
    from row_bot.providers import model_catalog

    statuses = model_catalog._provider_status_by_id

    def first_run_statuses():
        result = statuses()
        if _p10_state["active"]:
            for provider_id in ("codex", "claude_subscription", "xai_oauth"):
                status = result.get(provider_id)
                if isinstance(status, dict) and status.get("configured"):
                    status["runtime_enabled"] = True
        return result

    model_catalog._provider_status_by_id = first_run_statuses


@app.post("/__p10_fixture/first-run/{action}")
def p10_first_run(action: str, runtime: str = "", models: str = "", test: str = "",
                  x_fixture_token: str = Header(default="")) -> dict:
    """fresh: no default model (and not finished); restore: the seeded profile back."""
    predecessor._authorize(x_fixture_token)
    from row_bot import models as runtime_models
    from row_bot.providers import config as provider_config, model_catalog_cache, saved_model_settings

    settings = predecessor.DATA / "model_settings.json"
    app_config = predecessor.DATA / "app_config.json"
    # A first run pins its pick and loads catalogs; restore puts both back.
    kept = {"providers": Path(provider_config.CONFIG_PATH), "catalog": Path(model_catalog_cache.CATALOG_CACHE_PATH)}
    if not settings.resolve().is_relative_to(predecessor.DATA.resolve()):
        raise HTTPException(status_code=403, detail="Synthetic data scope required")
    if runtime:
        if runtime not in {"not_installed", "installed", "running"}:
            raise HTTPException(status_code=422, detail="Unknown runtime state")
        _p10_state["runtime"] = runtime
        _p10_state["models"] = [name for name in models.split(",") if name]
    if test:
        _p10_state["test"] = test
    if action == "fresh":
        _p10_state["active"] = True
        if _p10_state["backup"] is None:
            _p10_state["backup"] = {
                "settings": settings.read_text(encoding="utf-8") if settings.exists() else None,
                "app_config": app_config.read_text(encoding="utf-8") if app_config.exists() else None,
                **{key: path.read_text(encoding="utf-8") if path.exists() else None for key, path in kept.items()},
            }
        saved_model_settings.update_saved_model_settings(
            lambda raw: {key: value for key, value in raw.items() if key != "model"}, path=settings)
        runtime_models.adopt_saved_default("")
        config = json.loads(app_config.read_text(encoding="utf-8")) if app_config.exists() else {}
        config.update({"setup_complete": False, "onboarding_version": 4,
                       "onboarding_completed_steps": [], "onboarding_skipped_steps": []})
        app_config.write_text(json.dumps(config), encoding="utf-8")
    elif action == "restore":
        backup = _p10_state["backup"] or {}
        for path, key in ((settings, "settings"), (app_config, "app_config"), *((p, k) for k, p in kept.items())):
            if backup.get(key) is not None:
                path.write_text(backup[key], encoding="utf-8")
            elif key in kept and path.exists() and backup:
                path.unlink()
        restored = json.loads(settings.read_text(encoding="utf-8")).get("model", "") if settings.exists() else ""
        runtime_models.adopt_saved_default(restored)
        _p10_state.update(runtime="not_installed", models=[], test="ok", backup=None, active=False)
    elif action != "runtime":
        raise HTTPException(status_code=422, detail="Unknown first-run action")
    return {"action": action, "runtime": _p10_state["runtime"], "models": _p10_state["models"]}


def _p17_install_connection_check_fakes() -> None:
    """Monitor's connection checks (B252) run hourly and on Run diagnosis; in
    this disposable server they answer from the fixture and never reach a
    runtime, an account or the internet."""
    from row_bot import status_checks

    def check_ollama() -> status_checks.CheckResult:
        running = _p10_state["runtime"] == "running"
        return status_checks.CheckResult("Ollama", "ok" if running else "inactive",
                                         "Server reachable" if running else "Server offline", settings_tab="Models")

    def check_github_oauth() -> status_checks.CheckResult:
        return status_checks.CheckResult("GitHub", "inactive", "Not connected", settings_tab="Accounts")

    def check_network() -> status_checks.CheckResult:
        return status_checks.CheckResult("Network", "ok", "Connected", settings_tab="System")

    status_checks.check_ollama = check_ollama
    status_checks.NETWORK_CHECKS = (check_ollama, check_github_oauth, check_network)


def main() -> None:
    # Resolve the fixture's already selected isolated Python for child probes.
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    fixture_asset_root = os.environ.get("ROW_BOT_TEST_CLIENT_ASSET_ROOT")
    if fixture_asset_root:
        if os.environ.get("ROW_BOT_TEST_MODE") != "1":
            raise RuntimeError("Fixture client assets require isolated test mode")
        from row_bot import client_assets

        client_assets.default_client_asset_root = lambda: Path(fixture_asset_root)
    from row_bot import notifications
    from row_bot.providers import readiness
    from row_bot.providers.models import TransportMode
    from types import SimpleNamespace
    from row_bot.voice import get_voice_service
    from row_bot.voice import browser_local

    voice = get_voice_service()
    voice.whisper_model_available = lambda: True
    def deny_host_capture():
        raise AssertionError("Browser fixture must never start host audio capture")
    voice.start = deny_host_capture
    from row_bot.voice.provider_base import SynthesizedSpeech, VoiceProviderStatus
    def transcribe_fixture(session_key, audio, mime, *, validate, mode="dictate"):
        validate()
        _voice_fixture["transcriptions"] += 1
        _voice_fixture["bytes"] += len(audio)
        return "Browser dictated fixture text."
    def synthesize_fixture(session_key, text, *, validate):
        import io
        import wave
        validate()
        _voice_fixture["syntheses"] += 1
        output = io.BytesIO()
        with wave.open(output, 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b'\0\0' * 1600)
        return SynthesizedSpeech(output.getvalue(), "audio/wav")
    def speech_input_status_fixture(mode):
        return VoiceProviderStatus("fixture", "Fixture speech", True)
    synthetic_speech = SimpleNamespace(voice_service=voice, transcribe=transcribe_fixture,
                                       synthesize=synthesize_fixture,
                                       speech_input_status=speech_input_status_fixture)
    browser_local.get_browser_local_voice_service = lambda: synthetic_speech
    from row_bot.application import client_voice
    def fixture_credentials(**kwargs):
        import time
        _voice_fixture['credentials'] += 1
        return {'value': 'synthetic-browser-ephemeral', 'expires_at': time.time() + 60}
    def fixture_exchange(offer, *, client_secret, validate):
        validate()
        assert client_secret == 'synthetic-browser-ephemeral'
        _voice_fixture['exchanges'] += 1
        return b'v=0\r\nsynthetic-browser-answer'
    client_voice.realtime_provider = lambda: SimpleNamespace(create_client_secret=fixture_credentials, exchange_sdp=fixture_exchange)

    # The middleware stack is built on the first request. Wrap the completed
    # stack so the middlewares' own exceptions are observed too.
    build_middleware_stack = app.build_middleware_stack
    app.build_middleware_stack = lambda: SyntheticRequestDiagnostics(build_middleware_stack())

    notifications._desktop_notify = predecessor._suppress_desktop_notification
    notifications._play_sound = predecessor._suppress_notification_sound
    from row_bot.developer import storage
    original_save = storage.save_workspace

    def save_with_real_identity(workspace):
        if workspace.id == "p1-browser-workspace":
            workspace = replace(workspace, id=storage._workspace_id_for_path(Path(workspace.path)))
        return original_save(workspace)

    storage.save_workspace = save_with_real_identity
    try:
        predecessor.seed()
    finally:
        storage.save_workspace = original_save
    state_path = predecessor.DATA / "docs_real_ui_demo_state.json"
    seeded = json.loads(state_path.read_text(encoding="utf-8"))
    seeded["developer"]["workspace_id"] = storage._workspace_id_for_path(predecessor.DATA / "fixture-workspace")
    state_path.write_text(json.dumps(seeded), encoding="utf-8")
    # Test mode omits optional tools. Register the two real workspace tools so
    # natural chat exercises the same capability discovery as an install.
    import row_bot.tools.developer_tool  # noqa: F401
    import row_bot.designer.tool  # noqa: F401
    import row_bot.agent as agent
    from row_bot import models
    from row_bot.providers import reasoning
    from row_bot.providers.selection import model_choice_value, parse_model_ref
    from row_bot.providers.runtime import _reasoning_constructor_kwargs

    # Exactly one isolated synthetic model supplies Thinking controls. These
    # fixture capabilities perform no discovery and never reach a provider.
    reasoning_model = model_choice_value(models.get_current_model())
    _reasoning_fixture["model"] = reasoning_model
    reasoning_provider = (parse_model_ref(reasoning_model) or ("ollama", ""))[0]
    reasoning_caps = reasoning.ReasoningCapabilities(
        supported_efforts=("low", "high"), request_style="ollama" if reasoning_provider == "ollama" else "openai",
        source="isolated_visual_alignment_fixture", revision="visual-alignment-1")
    reasoning.resolve_reasoning_capabilities_for_ref = lambda value: reasoning_caps if value == reasoning_model else None
    original_record = predecessor._record

    def record_reasoning(kind, config, case):
        call = original_record(kind, config, case)
        snapshot = config["configurable"].get("reasoning_snapshot") or {}
        plan = reasoning.ReasoningRequestPlan(
            str(snapshot.get("model_ref") or ""),
            reasoning.ReasoningSelection.from_json(snapshot.get("selection")),
            reasoning.ReasoningCapabilities.from_json(snapshot.get("capabilities")))
        call["reasoning_selection"] = plan.selection.to_json()
        call["effective_reasoning_kwargs"] = _reasoning_constructor_kwargs(plan, provider=reasoning_provider)
        return call

    predecessor._record = record_reasoning

    def fake_agent_readiness(model_ref, **kwargs):
        return readiness.AgentReadinessResult(
            ready=True, provider_id="fixture", model_id="scripted", runtime_model="scripted",
            selection_ref=str(model_ref), transport=TransportMode.OLLAMA_CHAT, context_window=131072,
            tool_calling=True, tool_round_trip=True, streaming=True, credential_status="fixture",
            capability_source="fixture", confidence="high")

    def fake_chat_readiness(model_ref, **kwargs):
        return readiness.ChatReadinessResult(
            ready=True, provider_id="fixture", model_id="scripted", runtime_model="scripted",
            selection_ref=str(model_ref), transport=TransportMode.OLLAMA_CHAT, context_window=131072,
            streaming=True, credential_status="fixture", capability_source="fixture", confidence="high")

    agent.stream_agent = stream
    agent.resume_stream_agent = predecessor.resume
    readiness.evaluate_agent_readiness = fake_agent_readiness
    readiness.evaluate_chat_readiness = fake_chat_readiness
    from row_bot.api.v1 import routes
    from row_bot.application.client_platform import client_platform_service
    from row_bot.application.folder_selections import FolderSelections

    client_platform_service.readiness_factory = lambda _: True
    _p10_install_fakes()
    _p17_install_connection_check_fakes()
    client_platform_service.stream_factory = stream
    client_platform_service.resume_factory = predecessor.resume
    from row_bot import goals as goal_owner
    # Goals verify with a scripted verdict, never a model call.
    goal_owner._invoke_goal_verifier = _goal_verifier
    from row_bot.application import conversation_naming
    # A new conversation keeps its first-words name: its title is never a model call.
    conversation_naming._ask = lambda model_ref, message, reply: ""
    from row_bot import agent_orchestrator as orchestration
    # The explicit fixture control is the scheduler barrier. Every pass still
    # uses the real owner lease, batch selection, acknowledgement and projection.
    orchestration._schedule_parent_runner = lambda _: None

    def parent_executor(row, context, enabled_tools, config):
        record = _orchestrations[row["parent_thread_id"]]
        messages = config["configurable"]["thread_event_messages"]
        record["batches"].append([item["content"] for item in messages if item["role"] == "human"])
        release = _parent_releases.get(row["parent_thread_id"])
        if len(record["batches"]) == 1 and release is not None and not release.wait(180):
            raise TimeoutError("Synthetic captured parent invocation was not released")
        return "Synthetic parent applied captured guidance."

    orchestration.set_test_executors(parent=parent_executor, delivery=lambda *_: True)
    install = routes.install_client_platform

    def install_with_synthetic_picker(*args, **kwargs):
        kwargs["folder_selections"] = FolderSelections(picker=lambda: predecessor.DATA / "fixture-workspace")
        security = install(*args, **kwargs)
        _client_security["security"] = security
        return security

    routes.install_client_platform = install_with_synthetic_picker
    problem = routes.problem

    def record_private_failure(error, *args, **kwargs):
        frames = [(Path(frame.filename).name, frame.lineno) for frame in traceback.extract_tb(error.__traceback__)]
        logging.getLogger(__name__).error("Synthetic API failure: %s code=%s frames=%s",
                                         type(error).__name__, getattr(error, "code", ""), frames)
        return problem(error, *args, **kwargs)

    routes.problem = record_private_failure
    runpy.run_module("row_bot.app", run_name="__main__")


@app.post("/__p4_fixture/integration-skills")
def p4_integration_skills(x_fixture_token: str = Header(default="")) -> dict:
    """Exercise full skill publication through a deterministic public source."""
    predecessor._authorize(x_fixture_token)
    from row_bot.application import client_skill_hub as hub
    from row_bot.skills_hub.models import CatalogSearchResult, SkillHubEntry, SkillFile, SourceResult
    from row_bot.skills_hub.sources import bundle_from_files
    entry = SkillHubEntry(id="clawhub:browser-writing", name="Browser writing", description="Synthetic writing skill with resources",
        source="clawhub", source_id="clawhub", install_ref="clawhub:fixture/browser-writing@1.0.0")
    bundle = bundle_from_files(source="clawhub", install_ref=entry.install_ref, root_name="browser-writing", files=[
        SkillFile.from_text("SKILL.md", "---\nname: browser-writing\ndescription: Synthetic writing instructions\n---\nRead references/checklist.txt before writing."),
        SkillFile.from_text("references/checklist.txt", "Use clear sentences.\n")])
    hub.catalog.search_skills = lambda *a, **k: CatalogSearchResult(entries=[entry], mode="cache", query="writing",
        source_statuses=[SourceResult(entries=[entry], source_id="clawhub", status="cached")])
    hub.catalog.inspect_entry = lambda *a, **k: bundle
    hub.installer.fetch_bundle_for_record = lambda *a, **k: bundle
    from row_bot.skills_hub import clawhub_source
    clawhub_source.fetch_json = lambda url, **kwargs: (
        {"version": {"version": "1.0.0"}} if "/versions/" in url else
        {"owner": {"handle": "fixture"}, "skill": {"slug": "browser-writing"}})
    return {"ready": True}


@app.post("/__p5_fixture/apps")
def p5_apps(x_fixture_token: str = Header(default=""), x_fixture_origin: str = Header(default="")) -> dict:
    """Apps end to end on this machine only: synthetic servers, keychain and sign-in page."""
    p4_provider_credentials(x_fixture_token)
    import asyncio
    import secrets
    from types import SimpleNamespace
    from row_bot.application import client_mcp_auth
    from row_bot.mcp_client import auth, runtime
    tools = [{'name': 'search_pages', 'description': 'Find pages by title or text.', 'inputSchema': {'type': 'object'}},
             {'name': 'update_page', 'description': 'Change the text of a page.', 'inputSchema': {'type': 'object'}},
             {'name': 'delete_page', 'description': 'Delete a page for good.', 'inputSchema': {'type': 'object'}}]

    async def list_tools():
        return SimpleNamespace(tools=tools)

    async def connect(server):
        server.session = SimpleNamespace(list_tools=list_tools)
    runtime.sdk_available = lambda: True
    runtime.McpServerRuntime._connect = connect
    # Synthetic hosted apps never ask for a sign-in they don't declare; declared ones register a client.
    auth.discover_sign_in = lambda url: {"required": False, "metadata": True, "dcr": True}

    async def run_oauth(flow, label, client):
        state = secrets.token_urlsafe(24)
        with client_mcp_auth._LOCK:
            flow.authorization_url, flow.oauth_state, flow.state = (
                f"{x_fixture_origin}/__p5_fixture/oauth/approve?state={state}", state, "waiting")
        client_mcp_auth._persist(flow)
        await asyncio.to_thread(flow.event.wait, 120)
        if not flow.code:
            raise auth.McpAuthError("mcp_auth_denied")
        auth.write_credentials(flow.ref, {"tokens": {"access_token": "synthetic-access", "token_type": "Bearer"}})
        client_mcp_auth._publish(flow, {"mode": "oauth", "credential_ref": flow.ref, "binding": auth.binding(flow.name, flow.cfg),
            "callback_uri": flow.callback_uri, "label": label, "operation_id": flow.command_id})
    client_mcp_auth._run_oauth = run_oauth
    return {'ready': True}


_connect_anything = {"program_open": False}


def _synthetic_tarball(files: dict) -> bytes:
    import io
    import tarfile
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as output:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo("package/" + name)
            info.size, info.mode = len(data), 0o644
            output.addfile(info, io.BytesIO(data))
    return stream.getvalue()


def _synthetic_registry() -> dict:
    """npm registry bytes for the packages these journeys install: a package with its own
    shrinkwrap (so the review lists every dependency) and a self-contained one."""
    def integrity(raw: bytes) -> str:
        return "sha512-" + base64.b64encode(hashlib.sha512(raw).digest()).decode()
    served, documents = {}, {}
    deps = {name: _synthetic_tarball({"package.json": json.dumps({"name": name, "version": version}), "index.js": ""})
            for name, version in (("zod", "3.25.76"), ("minimatch", "10.0.3"), ("diff", "8.0.2"))}
    versions = {"zod": "3.25.76", "minimatch": "10.0.3", "diff": "8.0.2"}
    for name, raw in deps.items():
        url = f"https://registry.npmjs.org/{name}/-/{name}-{versions[name]}.tgz"
        served[url] = raw
    shrinkwrap = {"name": "@modelcontextprotocol/server-filesystem", "version": "2026.1.14", "lockfileVersion": 3, "packages": {
        "": {"name": "@modelcontextprotocol/server-filesystem", "version": "2026.1.14"},
        **{f"node_modules/{name}": {"version": versions[name], "resolved": f"https://registry.npmjs.org/{name}/-/{name}-{versions[name]}.tgz",
                                    "integrity": integrity(raw)} for name, raw in deps.items()}}}
    packages_ = {
        "@modelcontextprotocol/server-filesystem": ("2026.1.14", {"package.json": json.dumps({
            "name": "@modelcontextprotocol/server-filesystem", "version": "2026.1.14", "bin": "dist/index.js",
            "dependencies": {name: "^" + version for name, version in versions.items()}}),
            "dist/index.js": "", "npm-shrinkwrap.json": json.dumps(shrinkwrap)}, True),
        "fixture-blender-bridge": ("1.0.0", {"package.json": json.dumps({"name": "fixture-blender-bridge", "version": "1.0.0",
                                                                         "bin": "main.js"}), "main.js": ""}, False)}
    for name, (version, files, shrinkwrapped) in packages_.items():
        raw = _synthetic_tarball(files)
        url = f"https://registry.npmjs.org/{name}/-/{name.rsplit('/', 1)[-1]}-{version}.tgz"
        served[url] = raw
        document = {"name": name, "version": version, "bin": json.loads(files["package.json"])["bin"],
                    "dist": {"tarball": url, "integrity": integrity(raw)}}
        if shrinkwrapped:
            document.update(dependencies={n: "^" + v for n, v in versions.items()}, _hasShrinkwrap=True)
        for tag in (version, "latest"):
            documents[f"https://registry.npmjs.org/{name}/{tag}"] = document
    return {"tarballs": served, "documents": documents}


@app.post("/__p5_fixture/apps/sign-out")
def p5_apps_sign_out(name: str = "", x_fixture_token: str = Header(default="")) -> dict:
    """The synthetic service ended a signed-in app's sign-in (``name`` in its server name): it needs you."""
    p4_provider_credentials(x_fixture_token)
    from row_bot.integrations import facts
    from row_bot.mcp_client import config, runtime
    names = [server for server, cfg in config.read_saved_configuration().document.get("servers", {}).items()
             if (cfg.get("auth") or {}).get("mode") == "oauth" and name.casefold() in server.casefold()]
    for name in names:
        runtime._update_status(name, status="failed",
                               last_error="unhandled errors | OAuthFlowError: No redirect handler provided for authorization code grant")
    facts.invalidate()
    return {"signed_out": names}


@app.post("/__p5_fixture/connect-anything")
def p5_connect_anything(x_fixture_token: str = Header(default=""), x_fixture_origin: str = Header(default="")) -> dict:
    """Phase 4 on this machine only: a server that asks to sign in once tested, a package whose exact
    versions are reviewed before installing (registry bytes are synthetic), a program that must be
    open, and a bundle picked from disk. Nothing leaves this computer."""
    p5_apps(x_fixture_token, x_fixture_origin)
    from urllib.parse import unquote, urlsplit
    from row_bot.integrations import apps, facts, plans
    from row_bot.mcp_client import auth, config, packages, requirements
    auth.discover_sign_in = lambda url: ({"required": True, "metadata": True, "dcr": False, "cimd": True}
        if urlsplit(url).hostname == "signs-in.example.test" else {"required": False, "metadata": True, "dcr": True})
    requirements.check_requirement = lambda requirement, env=None: requirements.RuntimeCheck(requirement=requirement, available=True)
    registry = _synthetic_registry()

    def fetch(url, **_):
        found = registry["tarballs"].get(url)
        if found is None:
            found = registry["documents"].get(unquote(url))
            found = None if found is None else json.dumps(found).encode()
        if found is None:
            raise ValueError("mcp_package_source_invalid")
        return found
    packages._fetch = fetch
    # A synthetic bridge to Blender: an installed app that needs the program open on this computer.
    match = apps.match
    apps.match = lambda refs: apps.catalog()[0]["blender"] if "npm:fixture-blender-bridge" in refs else match(refs)
    plans.local_app_open = lambda check: _connect_anything["program_open"]
    _connect_anything["program_open"] = False
    document = config.load_config()
    document.setdefault("servers", {})["Blender bridge"] = {
        "enabled": False, "transport": "stdio", "command": "npx", "args": ["-y", "fixture-blender-bridge@1.0.0"],
        "environment_mode": "minimal", "source": {"marketplace": "custom"}}
    config.save_config(document)
    facts.invalidate()
    return {"ready": True}


@app.post("/__p5_fixture/connect-anything/program")
def p5_connect_anything_program(x_fixture_token: str = Header(default="")) -> dict:
    """The person opens the program: its check passes from now on."""
    predecessor._authorize(x_fixture_token)
    _connect_anything["program_open"] = True
    return {"open": True}


@app.get("/__p5_fixture/connect-anything/bundle")
def p5_connect_anything_bundle(x_fixture_token: str = Header(default="")) -> dict:
    """A synthetic, unsigned .mcpb for the file picker: a Node server with one key and one folder."""
    predecessor._authorize(x_fixture_token)
    import io
    import zipfile
    manifest = {"manifest_version": "0.3", "name": "field-notes", "display_name": "Field notes", "version": "1.2.0",
        "description": "Search and read the notes in one folder.", "author": {"name": "Fixture Author"}, "license": "MIT",
        "server": {"type": "node", "entry_point": "server/index.js", "mcp_config": {
            "command": "node", "args": ["${__dirname}/server/index.js", "--root", "${user_config.folder}"],
            "env": {"NOTES_TOKEN": "${user_config.api_key}"}}},
        "user_config": {
            "api_key": {"type": "string", "title": "Notes key", "description": "The key from your notes service.",
                        "sensitive": True, "required": True},
            "folder": {"type": "directory", "title": "Notes folder", "description": "Where your notes are.",
                       "default": "notes"}}}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr("manifest.json", json.dumps(manifest))
        output.writestr("server/index.js", "// synthetic server; never run by this fixture")
    return {"name": "field-notes.mcpb", "data": base64.b64encode(stream.getvalue()).decode()}


@app.get("/__p5_fixture/oauth/approve")
def p5_oauth_approve(state: str, allow: str = ""):
    """The synthetic provider's consent page: Allow completes the sign-in; leaving it doesn't."""
    from html import escape
    from urllib.parse import quote
    from fastapi.responses import HTMLResponse
    from row_bot.application import client_mcp_auth
    if not allow:
        return HTMLResponse("<!doctype html><title>Synthetic sign-in</title><p>Allow Row-Bot to use this service?</p>"
                            f'<a href="?state={escape(quote(state))}&allow=1">Allow</a>')
    client_mcp_auth.accept_callback(state=state, code="synthetic-code")
    return HTMLResponse("<!doctype html><title>Signed in</title><p>Signed in to the synthetic service.</p>")



@app.post("/__p6_fixture/views")
def p6_views(x_fixture_token: str = Header(default="")) -> dict:
    """An app with an interactive view (MCP Apps) on this machine only: the fixture counter's own view and
    tools, from a synthetic session; it is saved, accepted (its view included) and connected."""
    p4_provider_credentials(x_fixture_token)
    import runpy
    from types import SimpleNamespace
    from row_bot.application.capability_catalog_controls import capture_tested_catalog
    from row_bot.integrations import facts
    from row_bot.mcp_client import config, runtime
    fixture = runpy.run_path(str(Path(__file__).resolve().parents[3] / "tests/fixtures/mcp_apps/counter_server.py"))
    tools = [{"name": "counter", "description": "Show the counter, starting from a number.", "inputSchema": {"type": "object"},
              "_meta": {"ui": {"resourceUri": fixture["VIEW_URI"]}}},
             {"name": "increment", "description": "Add to the counter (its view's button).", "inputSchema": {"type": "object"},
              "_meta": {"ui": {"resourceUri": fixture["VIEW_URI"], "visibility": ["app"]}}},
             {"name": "reset", "description": "Set the counter back to zero.", "inputSchema": {"type": "object"},
              "_meta": {"ui": {"visibility": ["model"]}}}]
    count = {"value": 3}

    class Session:
        async def list_tools(self):
            return SimpleNamespace(tools=tools)

        async def read_resource(self, uri):
            return SimpleNamespace(contents=[SimpleNamespace(uri=uri, mimeType=fixture["MEDIA_TYPE"], text=fixture["VIEW"],
                                                             blob=None, meta={"ui": {"prefersBorder": True}})])

        async def call_tool(self, name, arguments):
            count["value"] = int(arguments.get("start", 0)) if name == "counter" else count["value"] + int(arguments.get("by", 1))
            dumped = {"content": [{"type": "text", "text": str(count["value"])}], "structuredContent": {"count": count["value"]},
                      "isError": False}
            return SimpleNamespace(**dumped, model_dump=lambda **_: dumped)
    cfg = {"transport": "stdio", "command": "python", "args": ["counter_server.py"], "enabled": True,
           "source": {"marketplace": "custom"}, "tools": {"enabled": {tool["name"]: True for tool in tools}}}
    normalized = runtime._normalize_tools("Counter", cfg, tools)
    captured = capture_tested_catalog({"ok": True, "tools": [info.__dict__ for info in normalized.values()]})
    cfg["tools"].update(catalog={row["name"]: row for row in captured["tools"]},
                        accepted_names=[row["name"] for row in captured["tools"]])
    document = config.read_saved_configuration().document
    document = {**document, "enabled": True, "servers": {**document.get("servers", {}), "Counter": cfg}}
    config.CONFIG_PATH.write_text(json.dumps(document), encoding="utf-8")
    server = runtime.McpServerRuntime("Counter", cfg)
    server.session = Session()
    with runtime._runtime_lock:
        runtime._servers["Counter"] = server
        runtime._catalog["Counter"] = runtime._normalize_tools("Counter", cfg, tools)
        runtime._statuses["Counter"] = runtime.McpServerStatus(name="Counter", enabled=True, status="connected",
                                                                tool_count=3, enabled_tool_count=3)
    runtime.get_langchain_tools(allow_names=["mcp"], refresh=False)  # As a turn binds them: their names are known.
    facts.invalidate()
    return {"ready": True}



@app.post("/__p6_fixture/app-workflow")
def p6_app_workflow(x_fixture_token: str = Header(default="")) -> dict:
    """A switched-off, scheduled workflow whose prompt step uses the fixture Counter app (set up first)."""
    p4_provider_credentials(x_fixture_token)
    from row_bot import tasks
    from row_bot.integrations import scope
    item = next(item for item in scope._mcp_items() if item["server"] == "Counter")
    task_id = tasks.create_task(
        name="Counter morning digest", description="Every morning, the count and what changed.", icon="🔢",
        schedule="daily:09:00", enabled=False, safety_mode="approve", apply_default_skills=False,
        agent_profile_id=tasks.DEFAULT_WORKFLOW_AGENT_PROFILE_ID,
        steps=[{"id": "read", "type": "prompt", "apps": [item["id"]],
                "prompt": "Using Counter, show today's count and what changed since yesterday."}])
    return {"task_id": task_id}


_p6_search_was_on = False


@app.post("/__p6_fixture/web-search-key")
def p6_web_search_key(x_fixture_token: str = Header(default="")) -> dict:
    """Row-Bot's own web search, set up with a synthetic key in the in-memory keychain: a ready built-in way."""
    p4_provider_credentials(x_fixture_token)
    from row_bot import api_keys
    from row_bot.tools import registry
    global _p6_search_was_on
    _p6_search_was_on = registry.is_enabled("web_search")
    registry.set_enabled("web_search", True)
    api_keys.set_key("TAVILY_API_KEY", "synthetic-search-key")
    return {"ready": True}


@app.post("/__p6_fixture/views/remove")
def p6_views_remove(x_fixture_token: str = Header(default="")) -> dict:
    """Take the fixture Counter app, its workflow and the synthetic search key away again, so later journeys
    find Apps as they expect."""
    p4_provider_credentials(x_fixture_token)
    from row_bot import tasks
    from row_bot.integrations import facts
    from row_bot.mcp_client import config, runtime
    document = config.read_saved_configuration().document
    servers = {name: cfg for name, cfg in document.get("servers", {}).items() if name != "Counter"}
    config.CONFIG_PATH.write_text(json.dumps({**document, "servers": servers}), encoding="utf-8")
    with runtime._runtime_lock:
        for registry in (runtime._servers, runtime._catalog, runtime._statuses):
            registry.pop("Counter", None)
    for task in tasks.list_tasks():
        if task.get("name") == "Counter morning digest":
            tasks.delete_task(task["id"])
    from row_bot import api_keys
    if api_keys.get_key("TAVILY_API_KEY") == "synthetic-search-key":
        api_keys.delete_key("TAVILY_API_KEY")
        from row_bot.tools import registry
        registry.set_enabled("web_search", _p6_search_was_on)
    facts.invalidate()
    return {"removed": True}


if __name__ == "__main__":
    main()
