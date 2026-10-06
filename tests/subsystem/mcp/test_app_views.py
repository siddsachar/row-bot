"""App views in chat (MCP Apps): a view is read from its own app, served once under a strict policy to a
frame with no Row-Bot origin, may call only its own app's tools that allow it, and every call it makes
goes through the same access and approvals as any other. Fakes only: no server runs, nothing connects."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from row_bot.integrations import views
from row_bot.mcp_client import runtime

pytestmark = [pytest.mark.subsystem, pytest.mark.mcp_transport]

HTML = "<!doctype html><html><body><button>+1</button><script>/* view */</script></body></html>"
CSP = {"resourceDomains": ["https://cdn.counter-app.com", "http://cdn.counter-app.com", "https://*.com"],
       "connectDomains": ["https://api.counter-app.com", "https://127.0.0.1", "https://localhost", "https://intranet",
                          "https://x.github.io/ok", "https://counter-app.com;script-src *", "https://printer.lan",
                          "https://127.0.0.1.nip.io", "https://*.sslip.io", "https://nas.home.arpa",
                          "https://*.compute-1.amazonaws.com", "https://router.localdomain", "https://api.example"]}


class FakeSession:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def read_resource(self, uri):
        item = SimpleNamespace(uri=uri, mimeType="text/html;profile=mcp-app", text=HTML, blob=None,
                               meta={"ui": {"csp": CSP, "prefersBorder": False}})
        return SimpleNamespace(contents=[item])

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(model_dump=lambda **_: {"content": [{"type": "text", "text": "4"}],
                                                     "structuredContent": {"count": 4}, "isError": False})


@pytest.fixture
def app(monkeypatch, tmp_path):
    """A connected Counter app (as a turn would have bound its tools) and a chat that called it once."""
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    session = FakeSession()
    server = runtime.McpServerRuntime("Counter", {"transport": "stdio"})
    server.session = session
    tools = [{"name": "counter", "description": "Show a counter.", "inputSchema": {"type": "object"},
              "_meta": {"ui": {"resourceUri": "ui://counter/view.html"}}},
             {"name": "update_counter", "description": "Change the counter by an amount.", "inputSchema": {"type": "object"},
              "_meta": {"ui": {"resourceUri": "ui://counter/view.html", "visibility": ["app"]}}},
             {"name": "reset", "description": "Reset the counter.", "inputSchema": {"type": "object"},
              "_meta": {"ui": {"visibility": ["model"]}}},
             {"name": "delete_counter", "description": "Delete the counter for good.", "inputSchema": {"type": "object"},
              "_meta": {"ui": {"visibility": ["app"]}}}]
    cfg = {"enabled": True, "tools": {"enabled": {tool["name"]: True for tool in tools}}}
    with runtime._runtime_lock:
        runtime._servers["Counter"] = server
        runtime._catalog["Counter"] = runtime._normalize_tools("Counter", cfg, tools)
        runtime._servers["Other"] = runtime.McpServerRuntime("Other", {"transport": "stdio"})
        runtime._catalog["Other"] = runtime._normalize_tools("Other", cfg, [
            {"name": "steal", "description": "Read something.", "inputSchema": {"type": "object"}, "_meta": {"ui": {"visibility": ["app"]}}}])
    monkeypatch.setattr(runtime, "_get_effective_config", lambda: {"enabled": True, "servers": {"Counter": cfg, "Other": cfg}})
    monkeypatch.setattr(runtime, "_schedule", lambda coroutine: _Done(asyncio.run(coroutine)))
    monkeypatch.setattr(runtime, "_future_result_with_generation_cancellation", lambda future, **_: future.value)
    item = {"id": "mcp:counter", "kind": "mcp", "server": "Counter", "name": "Counter", "icon": "letter:C", "app": None,
            "owner_ref": "counter", "lifecycle": "installed", "target": None}
    monkeypatch.setattr(views, "_app_of", lambda server_name: item if server_name == "Counter" else None)
    monkeypatch.setattr("row_bot.integrations.scope._mcp_items", lambda strict=False: [item])
    monkeypatch.setattr(views, "offered", lambda row: True)  # The person agreed to an app with a view.
    monkeypatch.setattr(views, "_step", lambda conversation, call_id: (
        {"id": call_id, "name": "mcp_counter_counter", "args": {"start": 3}},
        "Approval: not needed\n3\n\nSTRUCTURED_CONTENT:\n{\"count\": 3}"))
    monkeypatch.setattr(views, "_approval_mode", lambda conversation: mode["value"])
    mode, off = {"value": "approve"}, set()
    monkeypatch.setattr("row_bot.threads.get_thread_apps_off", lambda conversation: sorted(off))
    monkeypatch.setattr("row_bot.integrations.scope._profile_allow", lambda conversation: None)
    with runtime._runtime_lock:
        runtime._issued["mcp_counter_counter"] = ("Counter", "counter")
    yield SimpleNamespace(session=session, item=item, mode=mode, cfg=cfg, off=off)
    with runtime._runtime_lock:
        for name in ("Counter", "Other"):
            runtime._servers.pop(name, None)
            runtime._catalog.pop(name, None)
        runtime._issued.pop("mcp_counter_counter", None)


class _Done:
    def __init__(self, value):
        self.value = value


def call(render_id, name, arguments):
    """One call from a view, as the route awaits it."""
    return asyncio.run(views.call(render_id, name, arguments))


def test_a_view_is_read_from_its_own_app_and_served_once_under_a_strict_policy(app):
    shown = views.render("chat-1", "call-1")
    assert shown["input"] == {"start": 3} and shown["tool"]["name"] == "counter"
    assert shown["result"] == {"content": [{"type": "text", "text": "3"}], "structuredContent": {"count": 3}}
    assert shown["frame_url"] == "/app-views/" + shown["render_id"] and shown["prefers_border"] is False
    html, headers = views.frame(shown["render_id"])
    assert html == HTML and headers["Cache-Control"] == "no-store"
    policy = headers["Content-Security-Policy"]
    directives = {part.split()[0]: part.split()[1:] for part in policy.split("; ")}
    assert directives["default-src"] == ["'none'"] and directives["sandbox"] == ["allow-scripts"]
    # Never loopback, a private network name, a wildcard-DNS service or a public suffix.
    assert directives["connect-src"] == ["https://api.counter-app.com"]
    assert directives["script-src"] == ["'unsafe-inline'", "https://cdn.counter-app.com"]
    assert directives["frame-src"] == ["'none'"] and directives["object-src"] == ["'none'"]
    assert directives["form-action"] == ["'none'"] and directives["frame-ancestors"] == ["'self'"]
    assert "'self'" not in policy.replace("frame-ancestors 'self'", "")  # Never Row-Bot's own origin.
    assert "*" not in directives["connect-src"] and "http:" not in policy
    with pytest.raises(views.ViewError, match="not_found"):
        views.frame(shown["render_id"])  # Once: a view navigating, or anyone else, gets nothing.
    assert shown["domains"] == ["https://api.counter-app.com", "https://cdn.counter-app.com"]


def test_views_show_only_where_the_person_lets_them(app, monkeypatch):
    assert views.app_on(app.item) is True
    monkeypatch.setattr("row_bot.integrations.facts.read", lambda item_id, *_: app.item)
    views.set_app("mcp:counter", False)
    with pytest.raises(views.ViewError, match="views_off"):
        views.render("chat-1", "call-1")
    views.set_app("mcp:counter", True)
    views.set_enabled(False)
    assert views.app_on(app.item) is False and not views.has_view("mcp_counter_counter")
    views.set_enabled(True)
    monkeypatch.setattr(views, "offered", lambda row: False)  # Agreed to before views existed.
    assert views.settings()["apps"] == {"mcp:counter": True}
    views.set_app("mcp:counter", False)
    assert views.app_on(app.item) is False


def test_a_view_calls_only_its_own_apps_tools_that_allow_it(app):
    render_id = views.render("chat-1", "call-1")["render_id"]
    with pytest.raises(views.ViewError, match="view_tool_refused"):
        call(render_id, "reset", {})  # Only the agent may call it.
    with pytest.raises(views.ViewError, match="view_tool_refused"):
        call(render_id, "steal", {})  # Another app's tool, whatever it allows.
    with pytest.raises(views.ViewError, match="not_found"):
        call("0" * 32, "update_counter", {})
    app.mode["value"] = "allow_all"  # A routine change the person let run.
    assert call(render_id, "update_counter", {"by": 1}) == {"content": [{"type": "text", "text": "4"}],
                                                                 "structuredContent": {"count": 4}, "isError": False}
    assert app.session.calls == [("update_counter", {"by": 1})]


def test_a_tool_only_its_view_may_call_is_never_given_to_the_agent(app, monkeypatch):
    monkeypatch.setattr(runtime, "_get_effective_config", lambda: {"enabled": True, "servers": {"Counter": app.cfg}})
    monkeypatch.setattr(runtime, "discover_enabled_servers", lambda: None)
    names = {tool.name for tool in runtime.get_langchain_tools(allow_names=["mcp"])}
    assert "mcp_counter_counter" in names and "mcp_counter_reset" in names
    assert "mcp_counter_update_counter" not in names and "mcp_counter_delete_counter" not in names


@pytest.mark.parametrize(("mode", "tool", "expected"), [
    ("approve", "update_counter", "ask"), ("allow_all", "update_counter", "run"), ("block", "update_counter", "refuse"),
    ("allow_all", "delete_counter", "ask"), ("approve", "delete_counter", "ask"), ("block", "delete_counter", "refuse")])
def test_a_call_from_a_view_is_gated_as_any_call_is(app, mode, tool, expected):
    """Destructive, high-impact and unknown tools ask in every mode but Block; a routine change asks unless the
    chat chose Allow all."""
    assert views.gate(runtime.tool_info("Counter", tool), mode) == expected


def test_a_tool_recorded_as_always_asking_asks_from_a_view_even_under_allow_all(app):
    recorded = runtime.tool_info("Counter", "update_counter")
    recorded.locked = True  # A broker's acting tool, say: locked when accepted, whatever its effect reads as.
    assert views.gate(recorded, "allow_all") == "ask" and views.gate(recorded, "block") == "refuse"


def test_a_view_cannot_use_an_app_this_chat_has_switched_off(app):
    render_id = views.render("chat-1", "call-1")["render_id"]
    app.off.add("mcp:counter")
    app.mode["value"] = "allow_all"
    with pytest.raises(views.ViewError, match="view_tool_refused"):
        call(render_id, "update_counter", {})
    with pytest.raises(views.ViewError, match="views_off"):
        views.render("chat-1", "call-1")
    assert app.session.calls == []


def test_a_view_waits_on_one_answer_at_a_time(app, monkeypatch):
    """A view can't pile up calls that wait for the person (each would hold the server while it waits)."""
    import threading
    started, release = threading.Event(), threading.Event()

    async def ask(*args):
        started.set()
        return await asyncio.to_thread(release.wait, 10)
    monkeypatch.setattr(views, "_ask", ask)
    render_id = views.render("chat-1", "call-1")["render_id"]
    first = threading.Thread(target=lambda: call(render_id, "delete_counter", {}))
    first.start()
    assert started.wait(10)
    with pytest.raises(views.ViewError, match="view_busy"):
        call(render_id, "delete_counter", {})
    release.set()
    first.join(10)
    assert app.session.calls == [("delete_counter", {})]


def test_few_views_wait_on_the_person_at_once(app, monkeypatch):
    monkeypatch.setattr(views, "_asking", {"a" * 32, "b" * 32, "c" * 32})  # Three other views are waiting.
    render_id = views.render("chat-1", "call-1")["render_id"]
    with pytest.raises(views.ViewError, match="view_busy"):
        call(render_id, "delete_counter", {})
    assert app.session.calls == []


def test_access_changed_while_asking_stops_the_call(app, monkeypatch):
    """What runs is what was asked about: switching the tool off (or any access change) meanwhile stops it."""
    async def answer_after_a_change(*args):
        app.cfg["tools"]["enabled"]["delete_counter"] = False
        return True
    monkeypatch.setattr(views, "_ask", answer_after_a_change)
    render_id = views.render("chat-1", "call-1")["render_id"]
    with pytest.raises(views.ViewError, match="view_tool_refused"):
        call(render_id, "delete_counter", {})
    assert app.session.calls == []


def test_a_call_that_asks_waits_for_the_persons_answer_on_the_standard_card(app, monkeypatch):
    import threading
    from row_bot.application.client_platform import client_platform_service
    from row_bot.tasks import _get_conn, respond_to_approval
    published = []
    monkeypatch.setattr(client_platform_service.projection, "publish",
                        lambda conversation, kind, payload: published.append((conversation, kind, payload)))
    render_id = views.render("chat-1", "call-1")["render_id"]
    outcome: dict = {}

    def view_call():
        try:
            outcome["result"] = call(render_id, "delete_counter", {"id": "main"})
        except views.ViewError as error:
            outcome["error"] = str(error)
    for answer in (False, True):
        published.clear()
        worker = threading.Thread(target=view_call)
        worker.start()
        for _ in range(200):
            if published:
                break
            threading.Event().wait(0.02)
        conversation, kind, payload = published[0]
        assert (conversation, kind) == ("chat-1", "approval.required")
        assert payload["app"]["name"] == "Counter" and payload["app"]["tool"] == "Delete counter"
        with _get_conn() as conn:
            row = conn.execute("SELECT resume_kind, resume_token, source_thread_id FROM approval_requests WHERE id=?",
                               (payload["approval_id"],)).fetchone()
        assert (row["resume_kind"], row["source_thread_id"]) == ("mcp_app", "chat-1")
        assert app.session.calls == [] or answer
        respond_to_approval(row["resume_token"], answer)
        worker.join(10)
        if answer:
            assert outcome["result"]["structuredContent"] == {"count": 4}
            assert app.session.calls == [("delete_counter", {"id": "main"})]
        else:
            assert outcome.pop("error") == "view_tool_denied" and app.session.calls == []


def test_a_call_waiting_for_the_person_holds_no_server_thread(app, monkeypatch):
    """While one view waits for an answer, another view's call still runs on a server with one thread."""
    from concurrent.futures import ThreadPoolExecutor
    from row_bot.application.client_platform import client_platform_service
    published = []
    monkeypatch.setattr(client_platform_service.projection, "publish",
                        lambda conversation, kind, payload: published.append(payload))
    asking, other = (views.render("chat-1", "call-1")["render_id"] for _ in range(2))

    async def scenario():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        waiting = asyncio.create_task(views.call(asking, "delete_counter", {}))
        while not published:
            await asyncio.sleep(0.01)
        app.mode["value"] = "allow_all"  # The other call runs without asking.
        result = await asyncio.wait_for(views.call(other, "update_counter", {"by": 1}), 10)
        views.decided(published[0]["approval_id"], False)
        with pytest.raises(views.ViewError, match="view_tool_denied"):
            await asyncio.wait_for(waiting, 10)
        return result
    assert asyncio.run(scenario())["structuredContent"] == {"count": 4}
    assert app.session.calls == [("update_counter", {"by": 1})]


def test_a_view_is_limited_in_how_often_it_calls(app):
    app.mode["value"] = "allow_all"
    render_id = views.render("chat-1", "call-1")["render_id"]
    for _ in range(views.CALLS_PER_MINUTE):
        call(render_id, "update_counter", {})
    with pytest.raises(views.ViewError, match="view_rate_limited"):
        call(render_id, "update_counter", {})


def test_row_bot_says_it_can_show_views_only_while_views_are_on(monkeypatch, tmp_path):
    from mcp import types
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    sent = []

    async def capture(self, request, result_type, *args, **kwargs):
        sent.append(request.root.params.capabilities.model_dump(exclude_none=True))
    monkeypatch.setattr(runtime.ClientSession, "send_request", capture)
    request = types.ClientRequest(types.InitializeRequest(params=types.InitializeRequestParams(
        protocolVersion="2025-11-25", capabilities=types.ClientCapabilities(), clientInfo=types.Implementation(
            name="row-bot", version="1"))))
    session = object.__new__(runtime._AppsSession)
    asyncio.run(session.send_request(request, types.InitializeResult))
    views.set_enabled(False)
    asyncio.run(session.send_request(request, types.InitializeResult))
    assert sent[0]["extensions"] == {"io.modelcontextprotocol/ui": {"mimeTypes": ["text/html;profile=mcp-app"]}}
    assert "extensions" not in sent[1]


def test_a_view_gets_the_call_and_result_the_chat_kept(reload_for_data_dir, tmp_path):
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    threads, = reload_for_data_dir(tmp_path / "data", "row_bot.threads")
    thread = threads.create_thread("Counter", thread_id="chat-kept")
    threads.append_checkpoint_messages(thread, [
        HumanMessage(content="Show my counter"),
        AIMessage(content="", tool_calls=[{"id": "call-1", "name": "mcp_counter_counter", "args": {"start": 3}}]),
        ToolMessage(tool_call_id="call-1", name="mcp_counter_counter",
                    content='Approval: not needed\n3\n\nSTRUCTURED_CONTENT:\n{"count": 3}')])
    call, text = views._step(thread, "call-1")
    assert call["name"] == "mcp_counter_counter" and call["args"] == {"start": 3}
    assert views._result(text) == {"content": [{"type": "text", "text": "3"}], "structuredContent": {"count": 3}}
    with pytest.raises(views.ViewError, match="not_found"):
        views._step(thread, "call-2")


def test_the_frame_route_serves_a_view_once_with_its_own_policy(app):
    from fastapi.testclient import TestClient
    from row_bot.app import _app_view_handler
    from starlette.applications import Starlette
    shown = views.render("chat-1", "call-1")
    client = TestClient(Starlette(routes=[]))
    client.app.add_route("/app-views/{render_id}", _app_view_handler, methods=["GET"])
    first = client.get(shown["frame_url"])
    assert first.status_code == 200 and first.text == HTML
    assert "sandbox allow-scripts" in first.headers["content-security-policy"]
    assert "x-frame-options" not in first.headers and first.headers["cache-control"] == "no-store"
    assert client.get(shown["frame_url"]).status_code == 404
    assert json.loads(json.dumps(views.settings()))["enabled"] is True


@pytest.mark.slow
def test_the_fixture_counter_app_declares_its_view_over_a_real_connection(monkeypatch, tmp_path):
    """The real SDK and the fixture server: Row-Bot's session says it shows views, the server's tools carry
    their view and visibility, and the view is exactly the declared media type."""
    import sys
    from pathlib import Path
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    fixture = Path(__file__).resolve().parents[2] / "fixtures" / "mcp_apps" / "counter_server.py"

    async def run():
        params = StdioServerParameters(command=sys.executable, args=[str(fixture)])
        async with stdio_client(params) as (read, write):
            async with runtime._AppsSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                normalized = runtime._normalize_tools("Counter", {"tools": {}}, tools)
                contents = (await session.read_resource("ui://counter/view.html")).contents
                return normalized, contents
    normalized, contents = asyncio.run(run())
    assert normalized["counter"].ui == "ui://counter/view.html" and normalized["counter"].visibility == ("model", "app")
    assert normalized["increment"].visibility == ("app",) and normalized["reset"].visibility == ("model",)
    assert contents[0].mimeType == "text/html;profile=mcp-app" and "ui/initialize" in contents[0].text
