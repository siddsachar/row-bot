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


def _mount_static() -> None:
    """Serve /static as the desktop app does (app.py): the Design panel's font
    picker shows bundled fonts in their own face from /static/fonts."""
    from starlette.staticfiles import StaticFiles
    from row_bot.runtime_paths import static_dir

    app.mount("/static", StaticFiles(directory=static_dir()), name="static")


_mount_static()


# Claude (your Claude plan) runs the person's own Claude Code. Here Claude Code's answers are synthetic,
# so no spec ever starts or asks the Claude Code installed on the machine running the fixture; a spec
# sets the state it shows with /__s9_fixture/claude-plan/{state}.
from row_bot.providers import claude_subscription as _claude_plan  # noqa: E402

_NO_CLAUDE_CODE = "synthetic-claude-code-never-started"
_CLAUDE_PLAN_STATES = {
    "not_installed": _claude_plan.ClaudePlanStatus("not_installed"),
    "signed_out": _claude_plan.ClaudePlanStatus("signed_out", version="2.1.282", program=_NO_CLAUDE_CODE),
    "not_plan": _claude_plan.ClaudePlanStatus(
        "signed_out", version="2.1.282", program=_NO_CLAUDE_CODE,
        detail="Claude Code is signed in without a Claude plan, so it would bill another way."),
    "too_old": _claude_plan.ClaudePlanStatus("too_old", version="2.1.100", program=_NO_CLAUDE_CODE),
    "error": _claude_plan.ClaudePlanStatus(
        "error", version="2.1.282", program=_NO_CLAUDE_CODE,
        detail="Claude Code didn't say whether it is signed in."),
    "ready": _claude_plan.ClaudePlanStatus("ready", version="2.1.282", plan="Claude Max", program=_NO_CLAUDE_CODE),
}
_claude_plan_fixture = {"status": _CLAUDE_PLAN_STATES["not_installed"]}
_claude_plan.check_claude_code = lambda: _claude_plan_fixture["status"]
_claude_plan.discover_claude_plan_models = lambda: _claude_plan.model_infos_from_initialize([
    {"resolvedModel": "claude-opus-5-5[1m]", "description": "Opus 5.5 with 1M context · Best for complex tasks",
     "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"], "supportsEffort": True,
     "supportsAdaptiveThinking": True},
    {"resolvedModel": "claude-sonnet-5", "description": "Sonnet 5 · Efficient for routine tasks",
     "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"], "supportsEffort": True,
     "supportsAdaptiveThinking": True},
    {"resolvedModel": "claude-haiku-4-5-20251001", "description": "Haiku 4.5 · Fastest for quick answers"},
])


# Connecting or disconnecting a provider refreshes its model catalog (B411). Specs connect synthetic accounts
# and keys, so that refresh would ask the real provider for its models: here it never starts.
from row_bot.providers import model_catalog_cache as _catalog_cache  # noqa: E402

_catalog_cache.provider_connection_changed = lambda _provider_id, **_how: None


_capture_cards: list = []


@app.post("/__s9_fixture/claude-plan/{state}")
def s9_claude_plan(state: str, set_up_again: bool = False, x_fixture_token: str = Header(default="")) -> dict:
    """What Claude Code says, as Settings reads it; ``set_up_again``: the old Claude sign-in had this profile.

    Settings then reads provider cards through the real status path, not the display-only capture cards;
    ``restore`` puts those back.
    """
    predecessor._authorize(x_fixture_token)
    from row_bot.providers import live_settings
    from row_bot.providers.config import update_provider_config

    if not _capture_cards:
        _capture_cards.append(live_settings.docs_capture_fake_provider_status)
    if state == "restore":
        live_settings.docs_capture_fake_provider_status = _capture_cards[0]
        state = "not_installed"
    elif state not in _CLAUDE_PLAN_STATES:
        raise HTTPException(status_code=404, detail="Unknown Claude Code state")
    else:
        live_settings.docs_capture_fake_provider_status = lambda: False

    def _update(cfg: dict) -> None:
        entry = cfg.setdefault("providers", {}).setdefault("claude_subscription", {"provider_id": "claude_subscription"})
        if set_up_again:
            entry["set_up_again"] = True
        else:
            entry.pop("set_up_again", None)

    update_provider_config(_update)
    _claude_plan_fixture["status"] = _CLAUDE_PLAN_STATES[state]
    _claude_plan.forget_claude_plan_status()
    return {"ready": True}


# ── N12-A: Row-Bot's browser in the app ──
# The real Browser pane routes, live stream, input and controls run against a synthetic engine:
# no Chromium starts. A spec sets what the pane shows (/__n12_fixture/browser) and the picture,
# which it renders itself from the task suite's fixture pages (/__n12_fixture/site/{page}).
from row_bot.browser.service import BrowserEngine as _BrowserEngine, BrowserSessionManager as _Manager  # noqa: E402
from row_bot.browser.service import Frame as _Frame, _Live  # noqa: E402
from row_bot.tools import browser_tool as _browser_tool  # noqa: E402


class _N12Browser(_BrowserEngine):
    """The engine's own grants, control, viewers and pane state; the page itself is a picture."""

    def __init__(self) -> None:
        super().__init__()
        self.shown: dict[str, dict] = {}
        self.signed_in = [{"site": "homecover.example", "cookies": 14}, {"site": "github.com", "cookies": 6},
                          {"site": "google.com", "cookies": 21}]

    def pane_state(self, chat_id: str) -> dict:
        state = super().pane_state(chat_id)
        chat = self._chats.get(str(chat_id))
        if chat is not None:
            state.update(control=chat.control)
        state.update(self.shown.get(str(chat_id), {}))
        return state

    def ensure_started(self) -> None:
        self._status = "running"

    def restart(self) -> None:
        self._status = "running"
        for state in self.shown.values():
            state["status"] = "running"
        for chat in list(self.shown):
            self._publish(chat)

    def person_action(self, chat_id, action, *, url="", tab=""):
        state = self.shown.setdefault(str(chat_id), {})
        if action == "navigate":
            state.update(url=url, site=url.split("//", 1)[-1].split("/", 1)[0], loading=True)
        if action == "restart":
            self.restart()
        self._publish(str(chat_id))

    def person_input(self, chat_id, events):
        self.take_control(chat_id)
        return "ok"

    def sites(self):
        return list(self.signed_in)

    def clear_sites(self, site):
        before = len(self.signed_in)
        self.signed_in = [item for item in self.signed_in if site is not None and item["site"] != site]
        return before - len(self.signed_in)

    def show(self, chat_id: str, state: dict, frame: str | None, width: float, height: float) -> None:
        self._status = "running" if state.get("status", "running") == "running" else self._status
        self.shown[chat_id] = dict(state)
        if frame:
            live = self._live.setdefault(chat_id, _Live())
            live.seq += 1
            live.frame = _Frame(live.seq, frame, width, height)
        self._publish(chat_id)


class _N12Manager(_Manager):
    def __init__(self) -> None:
        super().__init__()
        self._engine = _N12Browser()

    def get_session(self, thread_id: str = "") -> _BrowserEngine:
        return self._engine

    def kill_all(self) -> None:
        self._engine = _N12Browser()


_browser_tool._session_manager = _N12Manager()
_original_tool_enabled = _tool_registry.is_enabled
_tool_registry.is_enabled = lambda name: True if name == "browser" else _original_tool_enabled(name)
_N12_RELEASE: dict[str, threading.Event] = {}
# The payment step each held turn asked about: its tool call id and arguments.
_N12_PENDING: dict[str, tuple[str, dict]] = {}


@app.post("/__n12_fixture/browser")
def n12_browser(body: dict, x_fixture_token: str = Header(default="")) -> dict:
    """What the conversation's Browser pane shows: its state and, optionally, a new picture."""
    predecessor._authorize(x_fixture_token)
    engine = _browser_tool.get_session_manager().get_session()
    chat = str(body["conversation_id"])
    state = dict(body.get("state") or {})
    record = engine._chat(chat)
    # Who drives is the engine's own (Take control and Give back in the pane change it).
    record.control = str(state.pop("control", record.control) or "agent")
    engine.show(chat, state, body.get("frame"), float(body.get("width") or 1280), float(body.get("height") or 800))
    return {"ready": True}


@app.get("/__n12_fixture/browser/size")
def n12_browser_size(conversation_id: str, x_fixture_token: str = Header(default="")) -> dict:
    """The page's size in the engine (the pane's while one shows it), for the spec to picture the page at."""
    predecessor._authorize(x_fixture_token)
    engine = _browser_tool.get_session_manager().get_session()
    width, height, scale = engine.page_metrics(conversation_id)
    return {"width": width, "height": height, "scale": scale}


@app.get("/__n12_fixture/site/{page}")
def n12_site(page: str):
    """A still page of the task suite's fixture service, for the spec to picture."""
    from fastapi.responses import HTMLResponse

    from tests.fixtures.browser_sites import still_page

    return HTMLResponse(still_page(page).decode("utf-8"))


@app.post("/__n12_fixture/release")
def n12_release(body: dict, x_fixture_token: str = Header(default="")) -> dict:
    """Let a held browser turn go on to its payment step."""
    predecessor._authorize(x_fixture_token)
    _N12_RELEASE.setdefault(str(body["conversation_id"]), threading.Event()).set()
    return {"released": True}


def _n12_stream(text: str, config: dict):
    """A turn that uses the browser: it opens the site and waits (the spec pictures the pane), then asks to pay."""
    from row_bot.browser.policy import Consequence, approval_request
    from row_bot.threads import append_checkpoint_messages

    call = predecessor._record("submit", config, "n12-browser")
    thread = call["conversation_id"]
    identity = f"n12:{call['generation_id']}"
    release = _N12_RELEASE.setdefault(thread, threading.Event())
    try:
        args = {"url": "https://homecover.example/account"}
        tool_id = fixture_id(identity + ":open")
        result_id = fixture_id(identity + ":open-result")
        append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":open-call"), content="",
            tool_calls=[{"id": tool_id, "name": "browser_navigate", "args": args}])])
        # As the agent sends them: the tool's display name, and its own name (U16).
        yield "tool_call", {"tool_call_id": tool_id, "message_id": result_id, "name": "🌐 Browser",
                            "raw_name": "browser_navigate", "runtime_name": "browser_navigate", "args": args}
        while not release.wait(0.1):
            pass
        content = "Opened https://homecover.example/account.\nPage: Your cover (https://homecover.example/account)"
        append_checkpoint_messages(thread, [ToolMessage(id=result_id, tool_call_id=tool_id, name="browser_navigate",
                                                        content=content)])
        yield "tool_done", {"tool_call_id": tool_id, "message_id": result_id, "name": "🌐 Browser",
                            "raw_name": "browser_navigate", "args": args, "content": content}
        pay = {"ref": "e31"}
        pay_id = fixture_id(identity + ":pay")
        append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":pay-call"), content="",
            tool_calls=[{"id": pay_id, "name": "browser_click", "args": pay}])])
        yield "tool_call", {"tool_call_id": pay_id, "name": "🌐 Browser", "raw_name": "browser_click",
                            "runtime_name": "browser_click", "args": pay}
        _N12_PENDING[thread] = (pay_id, pay)
        card = approval_request("browser_click", Consequence("pay", "homecover.example", "Confirm and pay £60"),
                                url="https://homecover.example/claim/5")
        yield "interrupt", [{"__interrupt_id": fixture_id(identity + ":approval"), **card}]
    finally:
        call["quiesced"] = True


def _n12_resume(enabled_tools: list[str], config: dict, approved: bool, *, interrupt_ids=None, resume_values=None,
                stop_event=None):
    """After the payment card: the claim is booked, or the payment didn't happen."""
    from row_bot.threads import append_checkpoint_messages

    thread = str(config["configurable"]["thread_id"])
    pending = _N12_PENDING.pop(thread, None)
    if pending is None:
        yield from _n4_resume(enabled_tools, config, approved, interrupt_ids=interrupt_ids,
                              resume_values=resume_values, stop_event=stop_event)
        return
    call = predecessor._record("resume", config, "n12-pay")
    identity = f"n12-resume:{call['generation_id']}:{call['sequence']}"
    try:
        result_id = fixture_id(identity + ":pay")
        content = ('Approval: asked; approved by you\nClicked button "Confirm and pay £60".\nPage: Claim booked'
                   if approved else "The person said no; Row-Bot didn't click it.")
        call_id, args = pending
        append_checkpoint_messages(thread, [ToolMessage(id=result_id, tool_call_id=call_id, name="browser_click",
                                                        content=content)])
        yield "tool_done", {"tool_call_id": call_id, "message_id": result_id, "name": "🌐 Browser",
                            "raw_name": "browser_click", "args": args, "content": content}
        final = ("Booked: a plumber comes tomorrow between 8am and 12pm (claim HC-1042). The £60 excess is paid."
                 if approved else "I didn't pay. The claim is ready at the payment step whenever you want to finish it.")
        yield from _natural_final(call, thread, final, "n12")
    finally:
        call["quiesced"] = True


# ── N12-B: computer use, rebuilt ──
# The real computer tool, service, routes and approval cards run against a fake desktop behind a fake Cua
# Driver (tests.fixtures.fake_desktop): nothing touches this computer's own screen, and the Stop bar is a
# fake one. A turn's computer calls run the real tool; its approval and question cards are LangGraph's
# interrupts, captured here and given back when the person answers.
import contextlib as _contextlib  # noqa: E402
import io as _io  # noqa: E402
from langgraph.errors import GraphBubbleUp as _GraphBubbleUp  # noqa: E402

from row_bot.computer_use import readiness as _cu_readiness  # noqa: E402
from tests.fixtures.fake_desktop import (  # noqa: E402
    FakeBar as _FakeBar,
    FakeControl as _FakeControl,
    FakeDesktop as _FakeDesktop,
    FakeWindow as _FakeWindow,
)

_N12B: dict = {"ready": None, "desktop": None, "before": None}
_N12B_PENDING: dict[str, dict] = {}
_N12B_RELEASE: dict[str, threading.Event] = {}
_original_cu_readiness = _cu_readiness.readiness


def _n12b_readiness(*, enabled=None):
    """Computer use's readiness: as set by the spec while it holds one, else the real one."""
    code = _N12B["ready"]
    if code is None:
        return _original_cu_readiness(enabled=enabled)
    messages = {"ready": "Cua Driver is ready.", "not_installed": "Cua Driver is not installed."}
    return _cu_readiness.CuaReadiness(_cu_readiness.ReadinessCode(code), messages.get(code, code),
                                      version="0.20.0", executable="cua-driver")


_cu_readiness.readiness = _n12b_readiness


def _n12b_font(size: int):
    from PIL import ImageFont

    for name in ("segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


class _N12bDesktop(_FakeDesktop):
    """The fake desktop, its windows pictured as a person sees them: a browser window on a claim form."""

    def picture(self, window):
        from PIL import Image, ImageDraw

        width, height = window.bitmap_size()
        image = Image.new("RGB", (width, height), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, width, 84), fill=(240, 242, 245))
        draw.rounded_rectangle((12, 8, 360, 40), radius=8, fill=(255, 255, 255))
        draw.text((28, 14), window.title.split(" - ")[0][:34], fill=(30, 34, 40), font=_n12b_font(16))
        draw.rounded_rectangle((12, 48, width - 12, 76), radius=14, fill=(255, 255, 255), outline=(220, 224, 230))
        draw.text((32, 52), "homecover.example/claim", fill=(70, 76, 86), font=_n12b_font(16))
        draw.rectangle((0, 84, width, 140), fill=(16, 52, 92))
        draw.text((48, 98), "HomeCover", fill=(255, 255, 255), font=_n12b_font(24))
        draw.text((48, 170), window.title.split(" - ")[0].split("– ")[-1], fill=(20, 24, 30), font=_n12b_font(34))
        for control in window.controls:
            x, y, w, h = control.box
            if control.role == "RadioButton":
                draw.ellipse((x, y + h // 2 - 11, x + 22, y + h // 2 + 11), outline=(60, 66, 76), width=2)
                draw.text((x + 34, y + h // 2 - 12), control.label, fill=(20, 24, 30), font=_n12b_font(20))
            elif control.role == "Hyperlink":
                draw.text((x, y + h // 2 - 12), control.label, fill=(25, 90, 180), font=_n12b_font(20))
                draw.line((x, y + h // 2 + 13, x + 9 * len(control.label), y + h // 2 + 13), fill=(25, 90, 180),
                          width=2)
            elif control.role == "ListItem":
                draw.rounded_rectangle((x, y, x + w, y + h), radius=10, outline=(200, 206, 214), width=2)
                draw.text((x + 18, y + h // 2 - 12), control.label, fill=(20, 24, 30), font=_n12b_font(20))
            else:
                draw.rounded_rectangle((x, y, x + w, y + h), radius=10, fill=(16, 52, 92))
                draw.text((x + 22, y + h // 2 - 12), control.label, fill=(255, 255, 255), font=_n12b_font(20))
        out = _io.BytesIO()
        image.save(out, "PNG")
        return out.getvalue()


def _n12b_page(title: str, controls: list) -> callable:
    def go(_desktop, window):
        window.title, window.controls = f"HomeCover – {title} - Microsoft Edge", controls

    return go


def _n12b_edge():
    pay = [
        _FakeControl("ListItem", "Tuesday 8am to 12pm", (48, 250, 520, 64)),
        _FakeControl("ListItem", "Tuesday 12pm to 4pm", (48, 330, 520, 64)),
        _FakeControl("Button", "Confirm and pay £60", (48, 450, 300, 60)),
    ]
    claim = [
        _FakeControl("RadioButton", "Leak", (48, 260, 300, 44)),
        _FakeControl("RadioButton", "Blocked drain", (48, 320, 300, 44)),
        _FakeControl("RadioButton", "No hot water", (48, 380, 300, 44)),
        _FakeControl("Button", "Next", (48, 470, 160, 60), on_click=_n12b_page("Book an engineer", pay)),
    ]
    cover = [
        _FakeControl("Hyperlink", "What am I covered for?", (48, 260, 260, 40)),
        _FakeControl("Button", "Make a claim", (48, 330, 220, 60), on_click=_n12b_page("Make a claim", claim)),
    ]
    return _FakeWindow(pid=2501, window_id=501, process="msedge.exe", title="HomeCover – Your cover - Microsoft Edge",
                       bounds=(0, 0, 1282, 802), controls=cover)


def _n12b_reset() -> None:
    from row_bot.application.client_platform import client_platform_service
    from row_bot.computer_use import service as module
    from row_bot.computer_use.client import CuaClient

    desktop = _N12bDesktop(windows=[_n12b_edge()], apps={2501: "Microsoft Edge"})
    if _N12B["before"] is None:  # What other specs see, put back by restore.
        _N12B["before"] = (_cu_readiness.disclosure_acknowledged(), _original_tool_enabled("computer_use"))
    _cu_readiness.acknowledge_disclosure()  # Computer use is set up on this (fake) desktop.
    if module._SERVICE is not None:
        module._SERVICE.stop()
    service = module.ComputerUseService(
        client_factory=lambda: CuaClient("cua-driver", transport_factory=lambda *_args: desktop),
        bar_factory=_FakeBar,
        stop_turn=client_platform_service.stop_conversation,
        window_display=desktop.window_display,
        cursor_position=desktop.cursor_position,
    )
    service.SETTLE_SECONDS = 0
    module._SERVICE = service
    _N12B.update(desktop=desktop, ready=None)
    _N12B_PENDING.clear()


@app.post("/__n12b_fixture/reset")
def n12b_reset(x_fixture_token: str = Header(default="")) -> dict:
    """A fresh fake desktop (Edge on HomeCover's site) and computer use session."""
    predecessor._authorize(x_fixture_token)
    _n12b_reset()
    return {"ready": True}


@app.post("/__n12b_fixture/restore")
def n12b_restore(x_fixture_token: str = Header(default="")) -> dict:
    """Computer use as it was before the N12-B spec: its notice, its switch and no session."""
    from row_bot.computer_use import service as module

    predecessor._authorize(x_fixture_token)
    if module._SERVICE is not None:
        module._SERVICE.stop()
        module._SERVICE = None
    if _N12B["before"] is not None:
        acknowledged, enabled = _N12B["before"]
        if not acknowledged:
            _cu_readiness.cancel_disclosure()
        _tool_registry.set_enabled("computer_use", enabled)
    _N12B.update(desktop=None, ready=None, before=None)
    _N12B_PENDING.clear()
    return {"restored": True}


@app.post("/__n12b_fixture/settings")
def n12b_settings(x_fixture_token: str = Header(default="")) -> dict:
    """Computer use set up and on, as Settings › Browser and computer use shows it."""
    predecessor._authorize(x_fixture_token)
    _cu_readiness.acknowledge_disclosure()
    _tool_registry.set_enabled("computer_use", True)
    _N12B["ready"] = "ready"
    return {"ready": True}


@app.post("/__n12b_fixture/release")
def n12b_release(body: dict, x_fixture_token: str = Header(default="")) -> dict:
    """Let a held computer turn go on."""
    predecessor._authorize(x_fixture_token)
    _N12B_RELEASE.setdefault(str(body["conversation_id"]), threading.Event()).set()
    return {"released": True}


class _N12bAsked(_GraphBubbleUp):
    """The turn pauses on a card, as LangGraph's interrupt pauses it."""

    def __init__(self, payload) -> None:
        super().__init__("asked")
        self.payload = payload


@_contextlib.contextmanager
def _n12b_turn(thread: str, generation: str, resumes: list):
    """The tool runs as in a chat turn here: its chat, the Ask level, and LangGraph's pauses."""
    from unittest import mock

    import langgraph.types

    from row_bot import agent
    from row_bot.browser import model_view
    from row_bot.tools import approval_gate

    counter = [0]

    def interrupt(payload):
        index = counter[0]
        counter[0] += 1
        if index < len(resumes):
            return resumes[index]
        raise _N12bAsked(payload)

    held = _N12B["ready"]
    _N12B["ready"] = held or "ready"
    try:
        with mock.patch.object(agent, "get_active_runtime_context", lambda: {
                "thread_id": thread, "generation_id": generation, "runtime_surface": "normal_chat"}), \
                mock.patch.object(approval_gate, "current_approval_mode", lambda: "approve"), \
                mock.patch.object(langgraph.types, "interrupt", interrupt), \
                mock.patch.object(model_view, "model_sees_images", lambda model=None: True):
            yield
    finally:
        _N12B["ready"] = held


def _n12b_at(label: str):
    """Click the control a result lists, at the position it gives (as the model aims)."""
    import re

    def args(previous: str) -> dict:
        line = next(line for line in previous.splitlines() if line.startswith("Controls: "))
        x, y = re.search(r'"' + re.escape(label) + r'" (\d+),(\d+)', line).groups()
        return {"action": "click", "x": int(x), "y": int(y)}

    return args


def _n12b_run(call: dict, thread: str, state: dict):
    from row_bot.threads import append_checkpoint_messages
    from row_bot.tools.computer_use_tool import computer

    steps, identity = state["steps"], state["identity"]
    while state["index"] < len(steps):
        step = steps[state["index"]]
        if step == "hold":
            release = _N12B_RELEASE.setdefault(thread, threading.Event())
            while not release.wait(0.1):
                pass
            release.clear()
            state["index"] += 1
            continue
        if callable(step) and getattr(step, "side_effect", False):
            step(thread)
            state["index"] += 1
            continue
        args = step(state["previous"]) if callable(step) else dict(step)
        tool_id = fixture_id(f"{identity}:{state['index']}")
        result_id = fixture_id(f"{identity}:{state['index']}:result")
        if not state["resumes"]:
            append_checkpoint_messages(thread, [AIMessage(id=fixture_id(f"{identity}:{state['index']}:call"),
                content="", tool_calls=[{"id": tool_id, "name": "computer", "args": args}])])
            yield "tool_call", {"tool_call_id": tool_id, "message_id": result_id, "name": "computer", "args": args}
        try:
            with _n12b_turn(thread, call["generation_id"], state["resumes"]):
                content = computer(**args)
        except _N12bAsked as asked:
            _N12B_PENDING[thread] = state
            yield "interrupt", [{"__interrupt_id": fixture_id(f"{identity}:{state['index']}:{len(state['resumes'])}"),
                                 **asked.payload}]
            return
        append_checkpoint_messages(thread, [ToolMessage(id=result_id, tool_call_id=tool_id, name="computer",
                                                        content=content)])
        yield "tool_done", {"tool_call_id": tool_id, "message_id": result_id, "name": "computer", "args": args,
                            "content": content}
        state.update(previous=content, resumes=[], index=state["index"] + 1)
    yield from _natural_final(call, thread, state["final"], "n12b")


def _n12b_person_moves_the_mouse(_thread: str) -> None:
    _FakeBar.instances[-1].person_moves_the_mouse()


_n12b_person_moves_the_mouse.side_effect = True


def _n12b_allowed(thread: str) -> None:
    from row_bot.computer_use.service import get_computer_use_service

    get_computer_use_service().allow(thread, "Microsoft Edge")


_n12b_allowed.side_effect = True


def _n12b_stream(text: str, config: dict):
    """A turn that uses Edge on this computer (the fake desktop) through the real computer tool."""
    call = predecessor._record("submit", config, "n12b-computer")
    thread = call["conversation_id"]
    shot = {"action": "screenshot", "app": "Microsoft Edge"}
    if "carry on" in text:
        steps = [_n12b_allowed, shot, _n12b_person_moves_the_mouse, _n12b_at("Make a claim"),
                 _n12b_at("Make a claim")]
        final = "Your claim form is open: I picked Leak and I'm at the engineer slots."
    elif "not set up" in text:
        _N12B["ready"] = "not_installed"
        steps = [{"action": "list_apps"}]
        final = ("I can't use your computer yet: Cua Driver isn't installed. Open Settings › System › Browser and "
                 "computer use › Set up… and install it, then ask me again.")
    else:
        steps = [{"action": "list_apps"}, shot, _n12b_at("Make a claim"), _n12b_at("Leak"), "hold",
                 _n12b_at("Next"), _n12b_at("Tuesday 8am to 12pm"), _n12b_at("Confirm and pay £60")]
        final = "Booked: a plumber comes on Tuesday between 8am and 12pm (claim HC-1042). The £60 excess is paid."
    state = {"steps": steps, "index": 0, "resumes": [], "previous": "", "final": final,
             "identity": f"n12b:{call['generation_id']}"}
    try:
        yield from _n12b_run(call, thread, state)
    finally:
        if "not set up" in text:
            _N12B["ready"] = None
        call["quiesced"] = True


def _n12b_resume(enabled_tools: list[str], config: dict, approved: bool, *, interrupt_ids=None, resume_values=None,
                 stop_event=None):
    """The person answered a computer card: the same call runs again with the answer, and the turn goes on."""
    thread = str(config["configurable"]["thread_id"])
    state = _N12B_PENDING.pop(thread, None)
    if state is None:
        yield from _n7_resume(enabled_tools, config, approved, interrupt_ids=interrupt_ids,
                              resume_values=resume_values, stop_event=stop_event)
        return
    call = predecessor._record("resume", config, "n12b-answer")
    answer = next(iter(resume_values.values())) if resume_values else bool(approved)
    state["resumes"] = [*state["resumes"], answer]
    try:
        yield from _n12b_run(call, thread, state)
    finally:
        call["quiesced"] = True


_n12b_reset()


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


@app.post("/__a0_fixture/full-autonomy/reset")
def reset_full_autonomy(x_fixture_token: str = Header(default="")) -> dict:
    """Forget that Hands-off was turned on, so its one-time dialog shows again (A0)."""
    predecessor._authorize(x_fixture_token)
    from row_bot.full_autonomy import _settings_path

    _settings_path().unlink(missing_ok=True)
    return {"reset": True}


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


_S2_PROGRAMS = {
    "dev_server.py": (
        "import time\n"
        "print('Starting the development server...', flush=True)\n"
        "time.sleep(0.4)\n"
        "print('  > Local:   http://localhost:5199/', flush=True)\n"
        "print('  > Network: use --host to expose', flush=True)\n"
        "tick = 0\n"
        "while True:\n"
        "    time.sleep(1.5)\n"
        "    tick += 1\n"
        "    print(f'GET /assets/app.js 200 in {12 + tick % 7}ms', flush=True)\n"
    ),
    "run_tests.py": (
        "import sys, time\n"
        "print('collected 24 items', flush=True)\n"
        "time.sleep(0.6)\n"
        "print('tests/test_login.py ........F', flush=True)\n"
        "print('FAILED tests/test_login.py::test_redirect - AssertionError: expected /home', flush=True)\n"
        "print('1 failed, 23 passed in 0.61s', flush=True)\n"
        "sys.exit(1)\n"
    ),
    "build.py": (
        "import time\n"
        "for step in ('Compiling 128 modules', 'Bundling', 'Minifying', 'Writing dist/'):\n"
        "    print(step + '...', flush=True)\n"
        # Long enough to be seen running on a busy machine before it finishes (B420).
        "    time.sleep(3)\n"
        "print('Built in 12s', flush=True)\n"
    ),
}
_S2_LOCK = threading.Lock()
_s2_folder: Path | None = None


def _s2_site() -> Path:
    """A synthetic project folder outside Row-Bot's data folder (which processes may not use)."""
    global _s2_folder
    if _s2_folder is None:
        import tempfile

        _s2_folder = Path(tempfile.mkdtemp(prefix="row-bot-s2-")) / "site"
        _s2_folder.mkdir()
        for name, source in _S2_PROGRAMS.items():
            (_s2_folder / name).write_text(source, encoding="utf-8", newline="\n")
    return _s2_folder


def _s2_approved(call):
    """Run a real process tool as if the person approved it on its card (the card itself is the
    "s2 process approval fixture")."""
    import langgraph.types as graph_types
    from row_bot.tools import approval_gate

    with _S2_LOCK:
        mode, ask = approval_gate.current_approval_mode, graph_types.interrupt
        approval_gate.current_approval_mode = lambda: "approve"
        graph_types.interrupt = lambda _payload: True
        try:
            return call()
        finally:
            approval_gate.current_approval_mode, graph_types.interrupt = mode, ask


def _s2_stream(text: str, config: dict):
    from row_bot.threads import append_checkpoint_messages
    from row_bot.tools.shell_tool import ShellTool, start_process_approval

    call = predecessor._record("submit", config, "s2-process")
    thread = call["conversation_id"]
    identity = f"s2:{call['generation_id']}"
    site = str(_s2_site())
    tool = ShellTool()
    runtime_config = {"configurable": {"thread_id": thread}}
    try:
        if "approval" in text:
            args = {"command": "python dev_server.py", "folder": site, "name": "Dev server",
                    "env": {"PORT": "5199"}}
            tool_id = fixture_id(identity + ":tool")
            append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":tool-call"), content="",
                tool_calls=[{"id": tool_id, "name": "start_process", "args": args}])])
            yield "tool_call", {"tool_call_id": tool_id, "name": "start_process", "args": args}
            payload = start_process_approval("python dev_server.py", site, {"PORT": "5199"}, "Dev server",
                                             "To preview the site while we work on it.")
            yield "interrupt", [{"__interrupt_id": fixture_id(identity + ":approval"), **payload}]
            return
        if "failed" in text:
            steps = [("start_process", {"command": "python run_tests.py", "name": "Unit tests"},
                      lambda: tool.start_process("python run_tests.py", folder=site, name="Unit tests",
                                                 runtime_config=runtime_config))]
            final = "The tests failed: one assertion in test_login.py."
        elif "long command" in text:
            steps = [("run_command", {"command": "python build.py", "background": True},
                      lambda: tool.execute("cd '" + site.replace("'", "''") + "'", runtime_config=runtime_config)
                      and tool.execute("python build.py", runtime_config=runtime_config, background=True))]
            final = "The build runs in the background; I'll check on it."
        else:
            steps = [("start_process", {"command": "python dev_server.py", "name": "Dev server",
                                        "env": {"PORT": "5199"}},
                      lambda: tool.start_process("python dev_server.py", folder=site, env={"PORT": "5199"},
                                                 name="Dev server", runtime_config=runtime_config))]
            final = "The dev server is running at http://localhost:5199."
        for index, (name, args, run) in enumerate(steps):
            tool_id = fixture_id(f"{identity}:{index}:tool")
            result_id = fixture_id(f"{identity}:{index}:result")
            append_checkpoint_messages(thread, [AIMessage(id=fixture_id(f"{identity}:{index}:call"), content="",
                tool_calls=[{"id": tool_id, "name": name, "args": args}])])
            yield "tool_call", {"tool_call_id": tool_id, "message_id": result_id, "name": name, "args": args}
            result = _s2_approved(run)
            append_checkpoint_messages(thread, [ToolMessage(id=result_id, tool_call_id=tool_id, name=name,
                                                          content=result)])
            yield "tool_done", {"tool_call_id": tool_id, "message_id": result_id, "name": name, "args": args,
                                "content": result}
        yield from _natural_final(call, thread, final, "s2")
    finally:
        call["quiesced"] = True


@app.post("/__s2_fixture/processes/stop-all")
def s2_stop_processes(x_fixture_token: str = Header(default="")) -> dict:
    """Stop every background process the fixture started (a spec's clean-up)."""
    predecessor._authorize(x_fixture_token)
    from row_bot import background_processes

    return {"stopped": background_processes.stop_all(by="you", timeout=20)}


# ── S3: Row-Bot's browser on local apps, screenshots, page sizes, recordings (F4, F2) ──
# The scripted model asks for the real local-address approval; approving grants the address
# through the real browser session (no browser starts) and shows a synthetic screenshot.
# Recordings run through the real recording manager with a fake capture that "writes" a
# synthetic video; the screen-recording step uses the real "needs Computer use" words.
_S3_ORIGIN = "http://localhost:5173"


def _s3_page_picture(width: int, height: int, scale: int = 1, *, phone: bool = False) -> bytes:
    """A synthetic picture of a small local web app's sign-up page."""
    from io import BytesIO

    from PIL import Image, ImageDraw, ImageFont

    w, h = width * scale, height * scale
    image = Image.new("RGB", (w, h), (248, 249, 251))
    draw = ImageDraw.Draw(image)

    def font(size: int):
        try:
            return ImageFont.load_default(size=size * scale)
        except TypeError:
            return ImageFont.load_default()

    draw.rectangle([0, 0, w, 56 * scale], fill=(30, 41, 59))
    draw.text((20 * scale, 16 * scale), "Fixture Shop", fill=(255, 255, 255), font=font(20))
    left = 24 * scale if phone else w // 2 - 220 * scale
    right = w - 24 * scale if phone else w // 2 + 220 * scale
    top = 96 * scale
    draw.text((left, top), "Create your account", fill=(15, 23, 42), font=font(26))
    for index, label in enumerate(("Name", "Email", "Password")):
        y = top + (64 + index * 84) * scale
        draw.text((left, y), label, fill=(71, 85, 105), font=font(14))
        box = [left, y + 22 * scale, right, y + 62 * scale]
        draw.rounded_rectangle(box, radius=8 * scale, outline=(203, 213, 225), width=max(1, scale),
                               fill=(38, 40, 46) if label == "Password" else (255, 255, 255))
    y = top + 324 * scale
    draw.rounded_rectangle([left, y, right, y + 48 * scale], radius=10 * scale, fill=(37, 99, 235))
    draw.text((left + 16 * scale, y + 13 * scale), "Sign up", fill=(255, 255, 255), font=font(16))
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _s3_video() -> bytes:
    """A short synthetic recording of the sign-up page (H.264), or the stock fixture video."""
    try:
        from io import BytesIO

        import av
        from PIL import Image, ImageDraw

        page = Image.open(BytesIO(_s3_page_picture(640, 400))).convert("RGB")
        out = BytesIO()
        with av.open(out, mode="w", format="mp4") as container:
            stream = container.add_stream("libx264", rate=12)
            stream.width, stream.height, stream.pix_fmt = 640, 400, "yuv420p"
            for index in range(36):
                frame = page.copy()
                x, y = 180 + index * 6, 150 + (index % 12) * 8
                ImageDraw.Draw(frame).ellipse([x, y, x + 14, y + 14], fill=(239, 68, 68))
                video_frame = av.VideoFrame.from_image(frame)
                video_frame.pts = index
                for packet in stream.encode(video_frame):
                    container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
        return out.getvalue()
    except Exception:
        return (Path(__file__).parents[2] / "fixtures/client_synthetic_video.mp4").read_bytes()


def _s3_media(thread: str, data: bytes) -> list[dict]:
    from row_bot.application.attachments import register_attachment

    saved = register_attachment(thread, "generated-image.png", data)
    return [{"type": "media.available", "payload": {"media_ref": saved["attachment_ref"],
                                                    "mime_type": saved["mime_type"]}}]


class _S3Capture:
    """A recording backend that finishes with the synthetic video when it's stopped."""

    def __init__(self, record, path: Path) -> None:
        self.record, self.path = record, path

    def request_stop(self, reason: str) -> None:
        from row_bot import recordings

        self.path.write_bytes(_s3_video())
        recordings.finished(self.record, reason, self.path)


def _s3_steps(thread: str, identity: str, steps):
    """Each scripted step: its call, then its result (with any media), as the real tool wrapper saves them."""
    from row_bot.threads import append_checkpoint_messages

    for index, (name, args, run) in enumerate(steps):
        tool_id = fixture_id(f"{identity}:{index}:tool")
        result_id = fixture_id(f"{identity}:{index}:result")
        append_checkpoint_messages(thread, [AIMessage(id=fixture_id(f"{identity}:{index}:call"), content="",
            tool_calls=[{"id": tool_id, "name": name, "args": args}])])
        yield "tool_call", {"tool_call_id": tool_id, "message_id": result_id, "name": name, "args": args}
        content, media = run()
        append_checkpoint_messages(thread, [ToolMessage(id=result_id, tool_call_id=tool_id, name=name,
            content=content, additional_kwargs={"platform_media": media} if media else {})])
        yield "tool_done", {"tool_call_id": tool_id, "message_id": result_id, "name": name, "args": args,
                            "content": content, **({"media": media} if media else {})}


def _s3_stream(text: str, config: dict):
    from row_bot import recordings
    from row_bot.browser.local_access import approval_request
    from row_bot.computer_use.recording import setup_problem
    from row_bot.threads import append_checkpoint_messages

    call = predecessor._record("submit", config, "s3-browser")
    thread = call["conversation_id"]
    identity = f"s3:{call['generation_id']}"
    try:
        if "local address" in text:
            args = {"url": _S3_ORIGIN + "/signup"}
            tool_id = fixture_id(identity + ":tool")
            append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":tool-call"), content="",
                tool_calls=[{"id": tool_id, "name": "browser_navigate", "args": args}])])
            yield "tool_call", {"tool_call_id": tool_id, "name": "browser_navigate", "args": args}
            yield "interrupt", [{"__interrupt_id": fixture_id(identity + ":approval"), **approval_request(_S3_ORIGIN)}]
            return
        if "viewport" in text:
            steps = [
                ("browser_viewport", {"preset": "phone"}, lambda: (
                    "The page now shows at phone size: 390×844 at 3× scale, as a touch screen. "
                    "Observe the page again before acting on it.", [])),
                ("browser_screenshot", {"full_page": False}, lambda: (
                    "Screenshot of http://localhost:5173/signup (what's on screen, phone size at 2× scale). "
                    "Password and card fields are covered. Saved to this chat's files and shown to the person.",
                    _s3_media(thread, _s3_page_picture(390, 844, 2, phone=True)))),
            ]
            final = "Here's the sign-up page at phone size: the form stacks into one column and the button fits."
        elif "screen recording" in text:
            problem = setup_problem()
            assert problem.startswith("Recording your screen needs Computer use set up."), problem
            steps = [("computer_record", {"action": "start"}, lambda: (problem, []))]
            final = ("I can't record your screen yet: that needs Computer use set up. Open **Settings › System › "
                     "Browser and computer use**, turn on Computer use and finish its setup, then ask me again. "
                     "I can record Row-Bot's own browser without it.")
        else:  # Recording Row-Bot's browser.
            def start():
                record = recordings.start(thread, "browser", max_minutes=5,
                                          begin=lambda record, path: _S3Capture(record, path))
                return ("Approval: asked; approved by you\n" + recordings.summary(record)
                        + " Do the steps to record, then call browser_record with action stop.", [])

            steps = [("browser_record", {"action": "start", "max_minutes": 5}, start)]
            final = "Recording Row-Bot's browser now. I'll stop it once the sign-up flow is done."
        yield from _s3_steps(thread, identity, steps)
        yield from _natural_final(call, thread, final, "s3")
    finally:
        call["quiesced"] = True


def _s3_resume(enabled_tools: list[str], config: dict, approved: bool, *, interrupt_ids=None, stop_event=None):
    """After the local-address card: open the page (granted for real) and show a screenshot."""
    from row_bot.threads import get_latest_checkpoint_messages

    thread = str(config["configurable"]["thread_id"])
    last = next((message for message in reversed(get_latest_checkpoint_messages(thread))
                 if getattr(message, "tool_calls", None)), None)
    asked = last.tool_calls[0] if last is not None else {}
    if asked.get("name") != "browser_navigate" or not str((asked.get("args") or {}).get("url", "")).startswith(
            _S3_ORIGIN):
        yield from predecessor.resume(enabled_tools, config, approved, interrupt_ids=interrupt_ids,
                                      stop_event=stop_event)
        return
    from row_bot.threads import append_checkpoint_messages
    from row_bot.tools.browser_tool import get_session_manager

    call = predecessor._record("resume", config, "s3-local-address")
    identity = f"s3-resume:{call['generation_id']}:{call['sequence']}"
    try:
        result_id = fixture_id(identity + ":navigate")
        if approved:
            get_session_manager().get_session(thread).allow_local_address(thread, _S3_ORIGIN, source="you")
            content = ("Approval: asked; approved by you\nURL: http://localhost:5173/signup\n"
                       "Title: Fixture Shop · Sign up\nInteractive elements (4):")
        else:
            content = ("Approval: asked; denied by you — did not run\n"
                       "The person didn't allow localhost:5173; it wasn't opened.")
        append_checkpoint_messages(thread, [ToolMessage(id=result_id, tool_call_id=asked["id"],
                                                        name="browser_navigate", content=content)])
        yield "tool_done", {"tool_call_id": asked["id"], "message_id": result_id, "name": "browser_navigate",
                            "args": asked.get("args") or {}, "content": content}
        if not approved:
            yield from _natural_final(call, thread, "OK, I won't open localhost:5173.", "s3-denied")
            return
        steps = [("browser_screenshot", {"full_page": False}, lambda: (
            "Screenshot of http://localhost:5173/signup (what's on screen, 1280×800 at 1× scale). "
            "Password and card fields are covered. Saved to this chat's files and shown to the person.",
            _s3_media(thread, _s3_page_picture(1280, 800))))]
        yield from _s3_steps(thread, identity, steps)
        yield from _natural_final(call, thread,
                                  "localhost:5173 is open in Row-Bot's browser. Here's the sign-up page as it is now.",
                                  "s3-local")
    finally:
        call["quiesced"] = True


@app.post("/__s3_fixture/reset")
def s3_reset(x_fixture_token: str = Header(default="")) -> dict:
    """Stop every recording and forget local-address grants (a spec's clean-up)."""
    predecessor._authorize(x_fixture_token)
    from row_bot import recordings
    from row_bot.tools.browser_tool import get_session_manager

    stopped = recordings.stop_all(by="you", timeout=10)
    get_session_manager().kill_all()
    return {"stopped": stopped}


# ── S4: video editing and many images at once (F3, F5) ──
# The real edit_video and analyze_image tools, video edit jobs, process manager, file owner and
# cards. ffmpeg is a small synthetic program the fixture writes (it reports progress and "makes"
# the video by copying a synthetic H.264 clip), and the ffmpeg download is a fake that reports
# progress; nothing is downloaded. The vision model is scripted.
_S4_FAKE_FFMPEG = (
    "import json, shutil, sys, time\n"
    "from pathlib import Path\n"
    "settings = json.loads(Path(__file__).with_suffix('.json').read_text())\n"
    "args = sys.argv[1:]\n"
    "outputs = [a[5:] for i, a in enumerate(args) if a.startswith('file:') and args[i - 1] != '-i']\n"
    "if not outputs:\n"
    "    length = '00:00:03.00' if args[-1].endswith('output.mp4') else '00:00:12.00'\n"
    "    sys.stderr.write(f'  Duration: {length}, start: 0.000000\\n'\n"
    "                     '  Stream #0:0(und): Video: h264 (High), yuv420p, 640x400, 12 fps, 12 tbr\\n')\n"
    "    sys.exit(1)\n"
    "seconds = float(settings['seconds'])\n"
    "steps = max(1, int(seconds / 0.25))\n"
    "for step in range(steps):\n"
    "    print(f'out_time_us={int(step * 3_000_000 / steps)}', flush=True)\n"
    "    print('progress=continue', flush=True)\n"
    "    time.sleep(0.25)\n"
    "for name in outputs:\n"
    "    shutil.copyfile(settings['video'], name)\n"
    "print('progress=end', flush=True)\n"
)
_S4_STATE: dict = {}
_S4_RELEASE = threading.Event()


def _s4_program(seconds: float):
    """The synthetic ffmpeg, set to take this long; as the resolver hands it out."""
    import tempfile

    from row_bot import ffmpeg_runtime

    if "folder" not in _S4_STATE:
        folder = Path(tempfile.mkdtemp(prefix="row-bot-s4-"))
        (folder / "fake_ffmpeg.py").write_text(_S4_FAKE_FFMPEG, encoding="utf-8", newline="\n")
        (folder / "sample.mp4").write_bytes(_s3_video())
        _S4_STATE["folder"] = folder
    folder = _S4_STATE["folder"]
    (folder / "fake_ffmpeg.json").write_text(json.dumps({"seconds": seconds, "video": str(folder / "sample.mp4")}),
                                             encoding="utf-8")
    return ffmpeg_runtime.Ffmpeg(executable=str(folder / "fake_ffmpeg.py"), source="row_bot", version="7.1",
                                 licence="GPL version 3 or later", prefix=(sys.executable,))


def _s4_patch(*, missing: bool, seconds: float = 1.0) -> None:
    """ffmpeg present (the synthetic program) or missing (the download is a fake that waits)."""
    from row_bot import ffmpeg_runtime
    from row_bot.tools import video_editing_tool as video_tool

    if "originals" not in _S4_STATE:
        _S4_STATE["originals"] = (ffmpeg_runtime.find, ffmpeg_runtime.install, ffmpeg_runtime.current_wheel,
                                  video_tool.INLINE_SECONDS)
    program = _s4_program(seconds)
    wheel = ffmpeg_runtime.WHEELS["windows-x86_64"]
    _S4_RELEASE.clear()

    def install(*, check=lambda: None):
        ffmpeg_runtime._set(status="downloading", received=0, total=wheel.size, error="")
        received = 0
        while not _S4_RELEASE.wait(0.1):
            check()
            received = min(int(wheel.size * 0.42), received + wheel.size // 40)
            ffmpeg_runtime._set(received=received)
        ffmpeg_runtime._set(status="installed", received=wheel.size)
        return program

    ffmpeg_runtime.find = (lambda: None) if missing else (lambda: program)
    ffmpeg_runtime.install = install
    ffmpeg_runtime.current_wheel = lambda *_a, **_k: wheel
    video_tool.INLINE_SECONDS = 1.5


def _s4_restore() -> None:
    from row_bot import ffmpeg_runtime
    from row_bot.tools import video_editing_tool as video_tool

    originals = _S4_STATE.pop("originals", None)
    if originals:
        ffmpeg_runtime.find, ffmpeg_runtime.install, ffmpeg_runtime.current_wheel, video_tool.INLINE_SECONDS = originals


def _s4_steps_picture(index: int) -> bytes:
    """One step of a sign-up flow, numbered, for the contact sheet."""
    from io import BytesIO

    from PIL import Image, ImageDraw, ImageFont

    page = Image.open(BytesIO(_s3_page_picture(640, 400))).convert("RGB")
    draw = ImageDraw.Draw(page)
    try:
        font = ImageFont.load_default(size=22)
    except TypeError:
        font = ImageFont.load_default()
    draw.rounded_rectangle([440, 70, 620, 108], radius=10, fill=(234, 88, 12) if index == 9 else (22, 163, 74))
    draw.text((452, 76), "Error" if index == 9 else f"Step {index}", fill=(255, 255, 255), font=font)
    out = BytesIO()
    page.save(out, format="PNG")
    return out.getvalue()


def _s4_in_thread(thread: str, run):
    """Run a real tool as part of this conversation's turn, as if the person approved any card."""
    from row_bot.agent import _current_thread_id_var

    token = _current_thread_id_var.set(thread)
    try:
        return _s2_approved(run)
    finally:
        _current_thread_id_var.reset(token)


# 6 s of the 12 s clip at twice the speed: 3 s, the length of the synthetic clip the fake ffmpeg "makes".
_S4_STEPS = [{"op": "trim", "start": 2, "end": 8}, {"op": "speed", "factor": 2},
             {"op": "captions", "text": "1\n00:00:00,500 --> 00:00:02,000\nCreate your account\n"}]


def _s4_stream(text: str, config: dict):
    from row_bot import ffmpeg_runtime
    from row_bot.application.attachments import register_attachment
    from row_bot.threads import append_checkpoint_messages
    from row_bot.tools import video_editing_tool as video_tool, vision_tool

    call = predecessor._record("submit", config, "s4-media")
    thread = call["conversation_id"]
    identity = f"s4:{call['generation_id']}"
    try:
        register_attachment(thread, "signup.mp4", _s3_video())
        args = {"inputs": ["signup.mp4"], "steps": _S4_STEPS, "name": "signup-demo"}
        if "consent" in text:
            _s4_patch(missing=True)
            tool_id = fixture_id(identity + ":tool")
            append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":tool-call"), content="",
                tool_calls=[{"id": tool_id, "name": "edit_video", "args": args}])])
            yield "tool_call", {"tool_call_id": tool_id, "name": "edit_video", "args": args}
            payload = ffmpeg_runtime.consent_request("edit", "edit_video",
                                                     wheel=ffmpeg_runtime.WHEELS["windows-x86_64"])
            yield "interrupt", [{"__interrupt_id": fixture_id(identity + ":approval"), **payload}]
            return
        if "contact sheet" in text or "no vision" in text:
            for index in range(1, 13):
                register_attachment(thread, f"step-{index:02d}.png", _s4_steps_picture(index))
            files = [f"step-{index:02d}.png" for index in range(1, 13)]
            shown: list[str] = []

            class _Vision:
                def cannot_see(self, chat_model=None):
                    if "no vision" not in text:
                        return ""
                    import row_bot.vision as vision

                    real = vision.VisionService()
                    real._model, real._enabled = "", True
                    saved = vision.vision_model_compatibility
                    vision.vision_model_compatibility = lambda _model: {"usable": False, "explicit": True}
                    try:
                        real.effective_model = lambda chat_model=None: "model:ollama:qwen3:8b"
                        return real.cannot_see()
                    finally:
                        vision.vision_model_compatibility = saved

                def analyze(self, image, question):
                    return ("Picture 9 shows the error: the password field says “Too short” after the person "
                            "typed 6 characters, while the hint above it says 6 is enough.")

            def look():
                originals = (vision_tool._get_vision_service, vision_tool._show_sheet)
                vision_tool._get_vision_service = lambda: _Vision()
                vision_tool._show_sheet = lambda sheet: shown.append(sheet) or True
                try:
                    content = vision_tool._analyze_image("Which step of the sign-up goes wrong?", files=files,
                                                          contact_sheet=True)
                finally:
                    vision_tool._get_vision_service, vision_tool._show_sheet = originals
                return content, (_s3_media(thread, shown[0]) if shown else [])

            steps = [("analyze_image", {"question": "Which step of the sign-up goes wrong?", "files": files,
                                        "contact_sheet": True}, lambda: _s4_in_thread(thread, look))]
            final = ("Step 9 goes wrong: the password field rejects a 6-character password although the hint says "
                     "6 is enough." if "no vision" not in text else
                     "I can't look at these screenshots with this chat's model: it can't see images. Choose a model "
                     "that can see images for Vision in **Settings › Models** (or switch this chat to one), and ask "
                     "me again.")
        else:
            long_edit = "long" in text
            _s4_patch(missing=False, seconds=20.0 if long_edit else 0.6)
            steps = [("edit_video", args, lambda: (_s4_in_thread(thread, lambda: video_tool._edit_video(**args)), []))]
            final = ("The edit is running: you can follow it below and stop it at any time." if long_edit else
                     "Here's the sign-up demo: trimmed, the form filling sped up 2× with a label, and a caption.")
        yield from _s3_steps(thread, identity, steps)
        yield from _natural_final(call, thread, final, "s4")
    finally:
        call["quiesced"] = True


def _s4_resume(enabled_tools: list[str], config: dict, approved: bool, *, interrupt_ids=None, stop_event=None):
    """After the ffmpeg card: start the edit, which downloads ffmpeg first (a fake that waits)."""
    from row_bot.threads import append_checkpoint_messages, get_latest_checkpoint_messages
    from row_bot.tools import video_editing_tool as video_tool

    thread = str(config["configurable"]["thread_id"])
    last = next((message for message in reversed(get_latest_checkpoint_messages(thread))
                 if getattr(message, "tool_calls", None)), None)
    asked = last.tool_calls[0] if last is not None else {}
    if asked.get("name") != "edit_video":
        yield from _s3_resume(enabled_tools, config, approved, interrupt_ids=interrupt_ids, stop_event=stop_event)
        return
    call = predecessor._record("resume", config, "s4-ffmpeg")
    identity = f"s4-resume:{call['generation_id']}:{call['sequence']}"
    try:
        result_id = fixture_id(identity + ":edit")
        if approved:
            content = _s4_in_thread(thread, lambda: video_tool._edit_video(**(asked.get("args") or {})))
        else:
            content = ("Approval: asked; denied by you — did not run\n"
                       "The person didn't allow downloading ffmpeg; nothing was downloaded or edited.")
        append_checkpoint_messages(thread, [ToolMessage(id=result_id, tool_call_id=asked["id"], name="edit_video",
                                                        content=content)])
        yield "tool_done", {"tool_call_id": asked["id"], "message_id": result_id, "name": "edit_video",
                            "args": asked.get("args") or {}, "content": content}
        yield from _natural_final(call, thread, "Getting ffmpeg first; the edit starts as soon as it's ready."
                                  if approved else "OK, I won't download ffmpeg.", "s4-ffmpeg")
    finally:
        call["quiesced"] = True


@app.post("/__s4_fixture/reset")
def s4_reset(x_fixture_token: str = Header(default="")) -> dict:
    """Stop every video edit and put ffmpeg back (a spec's clean-up)."""
    predecessor._authorize(x_fixture_token)
    from row_bot import video_edits

    _S4_RELEASE.set()
    stopped = video_edits.stop_all(by="you", timeout=10)
    _s4_restore()
    return {"stopped": stopped}


# ── S5: send a file to your devices or a chat app (F7) ──
# The real send_file tool, deliveries, notices, downloads and cards. Telegram is the in-memory fake
# adapter (tests/fixtures/channels.FakeChannel): it records what it would send; nothing reaches
# Telegram. A big video for the "too big" case is written into a temporary workspace folder.
_S5_STATE: dict = {}
_S5_VIDEO_BYTES = 63 * 1024 * 1024


def _s5_report() -> bytes:
    """A small one-page PDF: the quarterly report the fixture sends."""
    stream = b"BT /F1 24 Tf 72 720 Td (Q3 report) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\nstartxref\n"
    out += str(start).encode() + b"\n%%EOF\n"
    # Padded so it reads as a real report's size on the card.
    return bytes(out) + b"%" + b" " * (182 * 1024)


def _s5_telegram():
    """The fake Telegram adapter, running and linked to the person, in place of the real one."""
    from row_bot.channels import registry as channels
    from tests.fixtures.channels import FakeChannel

    if "telegram" not in _S5_STATE:
        _S5_STATE["original"] = channels._channels.get("telegram")
        fake = FakeChannel(name="telegram", display_name="Telegram", default_target=4242)
        fake._running = True
        channels._channels["telegram"] = fake
        _S5_STATE["telegram"] = fake
    return _S5_STATE["telegram"]


def _s5_workspace() -> None:
    """A workspace folder with a 63 MB screen recording in videos/ (too big for Telegram)."""
    import tempfile

    from row_bot.tools import registry

    if "workspace" not in _S5_STATE:
        _S5_STATE["workspace_root"] = registry.get_tool_config("filesystem", "workspace_root", "")
        folder = Path(tempfile.mkdtemp(prefix="row-bot-s5-")) / "Projects"
        (folder / "videos").mkdir(parents=True)
        video = _s3_video()
        (folder / "videos" / "demo-recording.mp4").write_bytes(video + b"\x00" * (_S5_VIDEO_BYTES - len(video)))
        _S5_STATE["workspace"] = folder
    registry.set_tool_config("filesystem", "workspace_root", str(_S5_STATE["workspace"]))


def _s5_stream(text: str, config: dict):
    from row_bot import file_deliveries
    from row_bot.application.attachments import register_attachment
    from row_bot.threads import append_checkpoint_messages
    from row_bot.tools import file_delivery_tool

    call = predecessor._record("submit", config, "s5-send")
    thread = call["conversation_id"]
    identity = f"s5:{call['generation_id']}"
    try:
        register_attachment(thread, "q3-report.pdf", _s5_report())
        _s5_telegram()
        if "telegram" in text:
            args = {"files": ["q3-report.pdf"], "to": "telegram"}
            tool_id = fixture_id(identity + ":tool")
            append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":tool-call"), content="",
                tool_calls=[{"id": tool_id, "name": "send_file", "args": args}])])
            yield "tool_call", {"tool_call_id": tool_id, "name": "send_file", "args": args}
            plan = file_deliveries.plan_channel(thread, "telegram")
            sources = file_deliveries.resolve(args["files"], thread)
            yield "interrupt", [{"__interrupt_id": fixture_id(identity + ":approval"),
                                 **file_delivery_tool.channel_approval(plan, sources)}]
            return
        if "too big" in text:
            _s5_workspace()
            args = {"files": ["videos/demo-recording.mp4"], "to": "telegram"}
            final = ("demo-recording.mp4 is 63 MB, too big for Telegram (50 MB limit), so I didn't send it. I can "
                     "send it to your devices instead: use the button below.")
        else:
            args = {"files": ["q3-report.pdf"], "to": "devices"}
            final = ("Sent: q3-report.pdf is on your devices. Open this chat on your phone or laptop and tap "
                     "Download; it's there for 7 days.")
        steps = [("send_file", args,
                  lambda: (_s4_in_thread(thread, lambda: file_delivery_tool._send_file(**args)), []))]
        yield from _s3_steps(thread, identity, steps)
        yield from _natural_final(call, thread, final, "s5")
    finally:
        call["quiesced"] = True


def _s5_resume(enabled_tools: list[str], config: dict, approved: bool, *, interrupt_ids=None, stop_event=None):
    """After the Telegram card: send through the fake adapter, as approved."""
    from row_bot.threads import append_checkpoint_messages, get_latest_checkpoint_messages
    from row_bot.tools import file_delivery_tool

    thread = str(config["configurable"]["thread_id"])
    last = next((message for message in reversed(get_latest_checkpoint_messages(thread))
                 if getattr(message, "tool_calls", None)), None)
    asked = last.tool_calls[0] if last is not None else {}
    if asked.get("name") != "send_file":
        yield from _s4_resume(enabled_tools, config, approved, interrupt_ids=interrupt_ids, stop_event=stop_event)
        return
    call = predecessor._record("resume", config, "s5-telegram")
    identity = f"s5-resume:{call['generation_id']}:{call['sequence']}"
    try:
        args = asked.get("args") or {}
        result_id = fixture_id(identity + ":send")
        if approved:
            content = _s4_in_thread(thread, lambda: file_delivery_tool._send_file(**args))
        else:
            content = ("Approval: asked; denied by you — did not run\nThe person didn't allow sending "
                       "q3-report.pdf to your Telegram chat; nothing was sent.")
        append_checkpoint_messages(thread, [ToolMessage(id=result_id, tool_call_id=asked["id"], name="send_file",
                                                        content=content)])
        yield "tool_done", {"tool_call_id": asked["id"], "message_id": result_id, "name": "send_file",
                            "args": args, "content": content}
        yield from _natural_final(call, thread, "Sent q3-report.pdf to your Telegram chat." if approved
                                  else "OK, I won't send it.", "s5-telegram")
    finally:
        call["quiesced"] = True


@app.get("/__s5_fixture/telegram")
def s5_telegram(x_fixture_token: str = Header(default="")) -> dict:
    """What the fake Telegram adapter was asked to send: file names and the chat."""
    predecessor._authorize(x_fixture_token)
    fake = _S5_STATE.get("telegram")
    return {"sent": [{"target": str(target), "name": Path(path).name} for target, path, _caption
                     in (fake.documents if fake else [])]}


@app.post("/__s5_fixture/reset")
def s5_reset(x_fixture_token: str = Header(default="")) -> dict:
    """Put the real Telegram adapter and the workspace folder back (a spec's clean-up)."""
    predecessor._authorize(x_fixture_token)
    from row_bot.channels import registry as channels
    from row_bot.tools import registry

    fake = _S5_STATE.pop("telegram", None)
    original = _S5_STATE.pop("original", None)
    if fake is not None:
        if original is not None:
            channels._channels["telegram"] = original
        else:
            channels._channels.pop("telegram", None)
    if "workspace_root" in _S5_STATE:
        registry.set_tool_config("filesystem", "workspace_root", _S5_STATE.pop("workspace_root"))
    return {"sent": len(fake.documents) if fake else 0}


# ── S6: attach a code folder from any device (F19) and Prepare sandbox (F18) ──
# The workspace folder is a disposable one with a few projects; a clone is a fake Git that
# makes the folder; Docker is a fake that "installs" over a few seconds (or until released).
_S6_STATE: dict = {}
_S6_IMAGE = "nikolaik/python-nodejs:python3.11-nodejs20"
_S6_INSTALL_LINES = (
    "Obtaining file:///workspace",
    "Collecting pytest",
    "  Downloading pytest-8.3.3-py3-none-any.whl (342 kB)",
    "Collecting pluggy<2,>=1.5",
    "Building editable for tide (pyproject.toml)",
    "Installing collected packages: pluggy, iniconfig, pytest, tide",
    "Successfully installed iniconfig-2.0.0 pluggy-1.5.0 pytest-8.3.3 tide-0.1.0",
)


def _s6_root() -> Path:
    """Row-Bot's workspace folder for these specs: projects, an empty folder and a repository."""
    import tempfile

    from row_bot.tools import registry

    if "root" not in _S6_STATE:
        _S6_STATE["workspace_root"] = registry.get_tool_config("filesystem", "workspace_root", "")
        base = Path(tempfile.mkdtemp(prefix="row-bot-s6-"))
        root = base / "Row-Bot"
        (root / "Projects" / "tide-app" / ".git").mkdir(parents=True)
        (root / "Projects" / "tide-app" / "README.md").write_text("# Tide app\n", encoding="utf-8")
        (root / "Projects" / "harbour-site" / ".git").mkdir(parents=True)
        (root / "Projects" / "notes").mkdir()
        (root / "Archive" / "2025").mkdir(parents=True)
        (base / "outside" / "private").mkdir(parents=True)
        _S6_STATE.update(root=root.resolve(), base=base)
    registry.set_tool_config("filesystem", "workspace_root", str(_S6_STATE["root"]))
    return _S6_STATE["root"]


def _s6_fake_git() -> None:
    """Clones make their folder with a .git and a README; nothing reaches the network."""
    from types import SimpleNamespace

    from row_bot.developer import client_clone

    if "subprocess_run" in _S6_STATE:
        return
    real = client_clone.subprocess.run
    _S6_STATE["subprocess_run"] = real

    def run(args, **kwargs):
        if isinstance(args, list) and args and Path(str(args[0])).stem.lower() == "git":
            if "clone" in args:
                target = Path(args[-1])
                (target / ".git").mkdir(parents=True, exist_ok=True)
                (target / "README.md").write_text("# demo\n", encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout=_S6_STATE.get("source", "") + "\n", stderr="")
        return real(args, **kwargs)

    client_clone.subprocess = SimpleNamespace(**{**vars(client_clone.subprocess), "run": run})


class _S6Docker:
    """Docker as Prepare sandbox sees it, with an install that takes a while."""

    def __init__(self, image_present: bool) -> None:
        import threading

        self.images = {_S6_IMAGE} if image_present else set()
        self.containers: set[str] = set()
        self.release = threading.Event()

    def runtime(self):
        from types import SimpleNamespace

        return SimpleNamespace(available=True, binary="docker", message="", version="27.3.1")

    def docker(self, binary, *args, timeout=30):
        from types import SimpleNamespace

        if args[:2] == ("image", "inspect"):
            return SimpleNamespace(returncode=0 if args[2] in self.images else 1, stdout="", stderr="")
        if args[0] == "inspect":
            return SimpleNamespace(returncode=0 if args[-1] in self.containers else 1, stdout="", stderr="")
        if args[0] == "rm":
            self.containers.discard(args[-1])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def start(self, job, argv, folder, name):
        import threading
        import uuid
        from types import SimpleNamespace

        record = SimpleNamespace(process_id=f"proc-{uuid.uuid4().hex[:8]}", exit_code=None, output="",
                                 done=threading.Event())
        if argv[1] == "pull":
            self.images.add(argv[2])
            record.output = "Status: Downloaded newer image for " + argv[2] + "\n"
            record.exit_code = 0
            record.done.set()
            return record
        self.containers.add(argv[argv.index("--name") + 1])

        def install():
            for line in _S6_INSTALL_LINES:
                if record.done.is_set():
                    return
                record.output += line + "\n"
                if "Downloading pytest" in line and self.release.wait(60):
                    continue
                if self.release.wait(0.6):
                    continue
            if not record.done.is_set():
                record.exit_code = 0
                record.done.set()

        threading.Thread(target=install, daemon=True).start()
        return record

    def stop(self, record, by):
        record.exit_code = 137
        record.done.set()


def _s6_docker(image_present: bool) -> _S6Docker:
    from row_bot.developer import sandbox_prepare

    fake = _S6Docker(image_present)
    if "sandbox_seams" not in _S6_STATE:
        _S6_STATE["sandbox_seams"] = {name: getattr(sandbox_prepare, name) for name in (
            "_runtime", "_docker", "_start_process", "_process_done", "_stop_process", "_tail")}
    sandbox_prepare._runtime = fake.runtime
    sandbox_prepare._docker = fake.docker
    sandbox_prepare._start_process = fake.start
    sandbox_prepare._process_done = lambda record, timeout: record.done.wait(timeout)
    sandbox_prepare._stop_process = fake.stop
    sandbox_prepare._tail = lambda record: record.output
    _S6_STATE["docker"] = fake
    return fake


@app.post("/__s6_fixture/workspace")
def s6_workspace(x_fixture_token: str = Header(default="")) -> dict:
    """Use the disposable workspace folder, with fake Git clones."""
    predecessor._authorize(x_fixture_token)
    root = _s6_root()
    _s6_fake_git()
    _S6_STATE["source"] = "https://example.test/team/demo.git"
    return {"folders": sorted(item.name for item in root.iterdir())}


@app.get("/__s6_fixture/workspace")
def s6_workspace_state(x_fixture_token: str = Header(default="")) -> dict:
    """Which folders the workspace folder has now (names only)."""
    predecessor._authorize(x_fixture_token)
    root = _S6_STATE.get("root")
    if root is None:
        return {"folders": [], "projects": []}
    return {"folders": sorted(item.name for item in root.iterdir()),
            "projects": sorted(item.name for item in (root / "Projects").iterdir())}


@app.post("/__s6_fixture/link-out")
def s6_link_out(x_fixture_token: str = Header(default="")) -> dict:
    """Swap Projects/notes for a link to a folder outside the workspace folder (a junction on Windows)."""
    predecessor._authorize(x_fixture_token)
    import shutil

    root = _s6_root()
    target = root / "Projects" / "notes"
    outside = _S6_STATE["base"] / "outside"
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    if not target.exists():
        if sys.platform == "win32":
            import _winapi

            _winapi.CreateJunction(str(outside), str(target))
        else:
            target.symlink_to(outside, target_is_directory=True)
    return {"linked": True}


@app.post("/__s6_fixture/sandbox")
def s6_sandbox(body: dict, x_fixture_token: str = Header(default="")) -> dict:
    """The conversation's code folder becomes a Python project in the Docker sandbox, with a fake Docker."""
    predecessor._authorize(x_fixture_token)
    from row_bot.developer import sandbox_prepare, storage

    conversation = str(body.get("conversation_id") or "")
    workspace = sandbox_prepare.conversation_workspace(conversation)
    if workspace is None:
        raise HTTPException(status_code=404, detail="No code folder")
    folder = Path(workspace.path)
    (folder / "tests").mkdir(exist_ok=True)
    (folder / "pyproject.toml").write_text(
        "[build-system]\nrequires = [\"setuptools\"]\nbuild-backend = \"setuptools.build_meta\"\n\n"
        "[project]\nname = \"tide\"\nversion = \"0.1.0\"\n\n[project.optional-dependencies]\n"
        "test = [\"pytest-cov\"]\n", encoding="utf-8")
    (folder / "tests" / "test_tide.py").write_text("def test_high_tide():\n    assert True\n", encoding="utf-8")
    storage.set_workspace_execution_settings(workspace.id, execution_mode="docker", sandbox_network="off",
                                             sandbox_image=_S6_IMAGE)
    _s6_docker(image_present=bool(body.get("image_present", True)))
    return {"workspace": workspace.name}


@app.post("/__s6_fixture/sandbox/release")
def s6_sandbox_release(x_fixture_token: str = Header(default="")) -> dict:
    """Let the fake install finish."""
    predecessor._authorize(x_fixture_token)
    fake = _S6_STATE.get("docker")
    if fake is not None:
        fake.release.set()
    return {"released": fake is not None}


@app.post("/__s6_fixture/reset")
def s6_reset(x_fixture_token: str = Header(default="")) -> dict:
    """Put the workspace folder, Git and Docker back (a spec's clean-up)."""
    predecessor._authorize(x_fixture_token)
    from row_bot.developer import client_clone, sandbox_prepare
    from row_bot.tools import registry

    fake = _S6_STATE.pop("docker", None)
    if fake is not None:
        fake.release.set()
        sandbox_prepare.stop_all(timeout=5)
    for name, value in _S6_STATE.pop("sandbox_seams", {}).items():
        setattr(sandbox_prepare, name, value)
    real = _S6_STATE.pop("subprocess_run", None)
    if real is not None:
        import subprocess

        client_clone.subprocess = subprocess
    if "workspace_root" in _S6_STATE:
        registry.set_tool_config("filesystem", "workspace_root", _S6_STATE.pop("workspace_root"))
    _S6_STATE.pop("root", None)
    return {"ok": True}


def _s6_stream(text: str, config: dict):
    """A sandbox test run that couldn't start: pytest isn't installed there (F18)."""
    from row_bot.developer.runtime import CommandResult
    from row_bot.developer.sandbox import ApprovalDecision
    from row_bot.tools.developer_tool import _command_text

    call = predecessor._record("submit", config, "s6-tests")
    thread = call["conversation_id"]
    identity = f"s6:{call['generation_id']}"
    try:
        result = CommandResult(command="python -m pytest", cwd="/workspace", returncode=1, stdout="",
                               stderr="/usr/local/bin/python: No module named pytest",
                               decision=ApprovalDecision("allow", "The project's detected commands run in the "
                                                         "sandbox without asking."),
                               execution_mode="docker", sandbox_backend="docker")
        content = _command_text(result, tool_name="developer_run_detected_test")
        steps = [("developer_run_detected_test", {"command": "pytest"}, lambda: (content, []))]
        yield from _s3_steps(thread, identity, steps)
        yield from _natural_final(call, thread, (
            "The tests couldn't run: pytest isn't installed in the sandbox, and its network is off. "
            "Prepare the sandbox to install the project's test tools, then I'll run them again."), "s6")
    finally:
        call["quiesced"] = True


def _goal_verifier(goal, _context):
    """A goal's second step completes it; the first asks for one more."""
    if int(goal.get("turns_used") or 0) >= 2:
        return {"verdict": "complete", "reason": "Fixture goal: both steps are done."}
    return {"verdict": "continue", "reason": "Fixture goal: one more step."}


# ── N7: Several X accounts, named on every post ────────────────────────────
# Two X accounts signed in through a fake X API (in-process: nothing reaches X) and the in-memory
# keychain. The model's x_post calls are scripted; the card is the real one for the call, and once
# approved the real sender posts to the fake X API (or loses X's answer and finds the post among the
# account's latest). A call without an account gets the real tool's refusal.
_N7: dict = {}
_N7_LAUNCH = ("Row-Bot 5.3 is out. Every post now names the X account it goes out as, and you approve "
              "the exact words and pictures before anything is sent.\n\nRead what's new: row-bot.ai/5.3")
_N7_REPLY = "Thanks! It keeps everything on your own computer, and it asks before it posts anywhere."


def _n7_picture(path: Path) -> Path:
    """A synthetic launch card (the post's picture)."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1200, 675), (17, 24, 39))
    draw = ImageDraw.Draw(image)

    def font(size: int):
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()
    draw.rounded_rectangle([60, 60, 1140, 615], radius=36, fill=(30, 41, 59))
    draw.ellipse([110, 110, 230, 230], fill=(99, 102, 241))
    draw.text((270, 120), "Row-Bot 5.3", fill=(255, 255, 255), font=font(64))
    draw.text((270, 210), "Several X accounts, named on every post", fill=(203, 213, 225), font=font(34))
    for index, label in enumerate(("@rowbot_ai", "@sidd")):
        top = 330 + index * 110
        draw.rounded_rectangle([110, top, 1090, top + 86], radius=20, fill=(51, 65, 85))
        draw.text((150, top + 22), label, fill=(255, 255, 255), font=font(36))
    image.save(path, format="PNG")
    return path


@app.post("/__n7_fixture/accounts")
def n7_accounts(x_fixture_token: str = Header(default="")) -> dict:
    """@rowbot_ai and @sidd signed in, X on, and a post someone else made to reply to."""
    import tempfile

    p4_provider_credentials(x_fixture_token)
    from row_bot import account_tokens, x_accounts
    from tests.helpers.fake_x_api import PERSONAL, ROWBOT, FakeXApi

    fake = FakeXApi(now="")  # Posts carry the real time, as X's do.
    x_accounts.transport = fake.transport()
    os.environ.update(X_CLIENT_ID="fixture-x-client", X_CLIENT_SECRET="fixture-x-secret")
    _N7.update(api=fake, enabled=_tool_registry.is_enabled("x"),
               folder=Path(tempfile.mkdtemp(prefix="row-bot-n7-")))
    _tool_registry.set_enabled("x", True)
    for user in (ROWBOT, PERSONAL):
        account = x_accounts.add(fake.sign_in(*user))
        # Documentation-capture mode (this fixture) never remembers a check; a real sign-in's passes.
        account_tokens._CHECKS[account.slot] = (account_tokens.digest(account.slot), True)
    _N7["question"] = fake.post("999", "What does Row-Bot actually do with my data?")
    _n7_picture(_N7["folder"] / "launch-card.png")
    return {"accounts": [account.handle for account in x_accounts.listed()]}


@app.post("/__n7_fixture/reset")
def n7_reset(x_fixture_token: str = Header(default="")) -> dict:
    """Remove the accounts, the fake X API and the X app's switch."""
    predecessor._authorize(x_fixture_token)
    from row_bot import x_accounts

    for account in x_accounts.listed():
        x_accounts.remove(account.id)
    x_accounts.transport = None
    for name in ("X_CLIENT_ID", "X_CLIENT_SECRET"):
        os.environ.pop(name, None)
    if "enabled" in _N7:
        _tool_registry.set_enabled("x", _N7["enabled"])
    _N7.clear()
    return {"ok": True}


def _n7_stream(text: str, config: dict):
    from row_bot import x_accounts
    from row_bot.threads import append_checkpoint_messages
    from row_bot.tools import x_posting
    from row_bot.tools.x_tool import XTool

    call = predecessor._record("submit", config, "n7-x")
    thread = call["conversation_id"]
    identity = f"n7:{call['generation_id']}"
    try:
        if "which" in text:
            args = {"action": "post", "text": _N7_LAUNCH}
            steps = [("x_post", args, lambda: (XTool()._x_post(**args), []))]
            yield from _s3_steps(thread, identity, steps)
            yield from _natural_final(call, thread, "Which X account should I post this from: @rowbot_ai (Row-Bot) "
                                      "or @sidd (Sidd)? Nothing has been posted.", "n7-which")
            return
        if "reply" in text:
            args = {"action": "reply", "text": _N7_REPLY, "tweet_id": _N7["question"]["id"], "account": "@rowbot_ai"}
        else:
            args = {"action": "post", "text": _N7_LAUNCH, "account": "@rowbot_ai",
                    "media_paths": [str(_N7["folder"] / "launch-card.png")]}
        _N7["lose_answer"] = "unsure" in text
        account = x_accounts.resolve(args["account"], required=True)
        media = x_posting.media_facts(args.get("media_paths"))
        payload = x_posting.approval_payload(account, args["action"], args["text"], args.get("tweet_id", ""), media)
        tool_id = fixture_id(identity + ":tool")
        append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":tool-call"), content="",
            tool_calls=[{"id": tool_id, "name": "x_post", "args": args}])])
        yield "tool_call", {"tool_call_id": tool_id, "name": "x_post", "args": args}
        yield "interrupt", [{"__interrupt_id": fixture_id(identity + ":approval"), **payload}]
    finally:
        call["quiesced"] = True


def _n7_resume(enabled_tools: list[str], config: dict, approved: bool, *, interrupt_ids=None, resume_values=None,
               stop_event=None):
    """After the X card: the real sender posts as approved, to the fake X API."""
    from row_bot import x_accounts
    from row_bot.threads import append_checkpoint_messages, get_latest_checkpoint_messages
    from row_bot.tools import x_posting
    from row_bot.tools.approval_gate import APPROVAL_GIVEN, with_approval

    thread = str(config["configurable"]["thread_id"])
    last = next((message for message in reversed(get_latest_checkpoint_messages(thread))
                 if getattr(message, "tool_calls", None)), None)
    asked = last.tool_calls[0] if last is not None else {}
    if asked.get("name") != "x_post" or not approved:  # A denied card ends the turn without resuming it.
        yield from _n12_resume(enabled_tools, config, approved, interrupt_ids=interrupt_ids,
                               resume_values=resume_values, stop_event=stop_event)
        return
    call = predecessor._record("resume", config, "n7-sent")
    identity = f"n7-resume:{call['generation_id']}:{call['sequence']}"
    try:
        args = asked.get("args") or {}
        result_id = fixture_id(identity + ":sent")
        account = x_accounts.resolve(args["account"], required=True)
        if _N7.pop("lose_answer", False):
            _N7["api"].after_send = "timeout"  # X posts it, and its answer is lost on the way back.
        sent = x_posting.post_as(account, args["action"], args.get("text", ""), args.get("tweet_id", ""),
                                 x_posting.media_facts(args.get("media_paths")))
        content = with_approval(APPROVAL_GIVEN, sent.text)
        final = (f"X didn't confirm it at first, so I checked @rowbot_ai's latest posts: it went out once. {sent.url}"
                 if sent.text.startswith("Not sure") else f"Done. It's live on @rowbot_ai: {sent.url}")
        append_checkpoint_messages(thread, [ToolMessage(id=result_id, tool_call_id=asked["id"], name="x_post",
                                                        content=content)])
        yield "tool_done", {"tool_call_id": asked["id"], "message_id": result_id, "name": "x_post",
                            "args": args, "content": content}
        yield from _natural_final(call, thread, final, "n7-sent")
    finally:
        call["quiesced"] = True


# ── N4: Row-Bot asks questions with options, in cards ──────────────────────
# The real ask_person card and answers: the model's call and the turn's final
# are scripted; the question's result is the real tool's for the answer given.
_N4_BETA = {"header": "Beta timing", "question": "When should the beta go out?",
            "options": [{"label": "Beta.1 now (Recommended)",
                         "description": "Ship the current build to testers today."},
                        {"label": "After the fixes", "description": "Wait for the two open fixes, about two days."},
                        {"label": "Next week", "description": "Ship with the new onboarding."}]}
_N4_PAGES = {"header": "Pages", "question": "Which pages go in the beta?", "multi_select": True,
             "options": [{"label": "Plans", "description": "The plan list and its filters."},
                         {"label": "Ledger", "description": "Spending by month."},
                         {"label": "Docs", "description": "The help pages."}]}
_N4_APPROVALS: list[str] = []
_N4_WORKFLOWS: list[str] = []  # The conversations of workflow runs waiting on a question.


def _n4_stream(text: str, config: dict):
    from row_bot.questions import card, normalize
    from row_bot.threads import append_checkpoint_messages

    asked = [_N4_BETA, _N4_PAGES] if "steps" in text else [_N4_BETA]
    call = predecessor._record("submit", config, "n4-question")
    thread = call["conversation_id"]
    identity = f"n4:{call['generation_id']}"
    tool_id = fixture_id(identity + ":tool")
    try:
        append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":tool-call"), content="",
            tool_calls=[{"id": tool_id, "name": "ask_person", "args": {"questions": asked}}])])
        yield "tool_call", {"tool_call_id": tool_id, "name": "ask_person", "args": {"questions": asked}}
        yield "interrupt", [{"__interrupt_id": fixture_id(identity + ":question"), **card(normalize(asked))}]
    finally:
        call["quiesced"] = True


def _n4_resume(enabled_tools: list[str], config: dict, approved: bool, *, interrupt_ids=None, resume_values=None,
               stop_event=None):
    """The question's answer goes back to its call; the turn ends with a short reply."""
    from row_bot.questions import card, normalize, result
    from row_bot.threads import append_checkpoint_messages, get_latest_checkpoint_messages

    thread = str(config["configurable"]["thread_id"])
    last = next((message for message in reversed(get_latest_checkpoint_messages(thread))
                 if getattr(message, "tool_calls", None)), None)
    asked = last.tool_calls[0] if last is not None else {}
    if asked.get("name") != "ask_person" or not resume_values:
        yield from _s5_resume(enabled_tools, config, approved, interrupt_ids=interrupt_ids, stop_event=stop_event)
        return
    call = predecessor._record("resume", config, "n4-answer")
    identity = f"n4-resume:{call['generation_id']}:{call['sequence']}"
    try:
        content = result(card(normalize(asked["args"]["questions"])), next(iter(resume_values.values())))
        result_id = fixture_id(identity + ":result")
        append_checkpoint_messages(thread, [ToolMessage(id=result_id, tool_call_id=asked["id"], name="ask_person",
                                                        content=content)])
        yield "tool_done", {"tool_call_id": asked["id"], "message_id": result_id, "name": "ask_person",
                            "args": asked["args"], "content": content}
        yield from _natural_final(call, thread, "Thanks. I'll plan the beta that way.", "n4")
    finally:
        call["quiesced"] = True


# ── N5: pages Row-Bot makes for the person ─────────────────────────────────
# The real page tools on the Making a page skill's own templates; the model's
# calls and finals are scripted. The person's Send arrives as a real message.
_N5_SKILL = Path(__file__).resolve().parents[3] / "bundled_skills" / "making_pages" / "SKILL.md"
_N5_PLAN = {
    "title": "Beta launch plan",
    "summary": "Six steps to open the beta to 200 testers on 14 November.",
    "steps": [
        {"id": "freeze", "title": "Freeze the release branch",
         "detail": "Cut beta.1 from main once the two open fixes land.", "status": "done"},
        {"id": "announce", "title": "Draft the announcement",
         "detail": "A short blog post and an email to the waitlist.", "status": "doing"},
        {"id": "invites", "title": "Send invites to the waitlist",
         "detail": "200 people, in two batches a day apart.", "status": "todo"},
        {"id": "call", "title": "Book the launch call",
         "detail": "30 minutes with the first testers on Thursday at 5pm.", "status": "todo"},
        {"id": "feedback", "title": "Set up the feedback form",
         "detail": "Five questions, linked from the app's Help menu.", "status": "todo"},
        {"id": "stickers", "title": "Order stickers for testers",
         "detail": "Optional: 250 stickers, about £120.", "status": "todo"},
    ],
}
_N5_DASHBOARD = {
    "title": "Sales by week",
    "updated": "today at 9:00",
    "metrics": [{"label": "Revenue, last 8 weeks", "value": "£48,210"}, {"label": "Orders", "value": "1,284"},
                {"label": "Average order", "value": "£37.55"}, {"label": "Refunds", "value": "1.8%"}],
    "chart": {"labels": ["4 Sep", "11 Sep", "18 Sep", "25 Sep", "2 Oct", "9 Oct", "16 Oct", "23 Oct"],
              # £48,210 in all, about £6k a week, split between online and the shops.
              "series": [{"label": "Online", "values": [4060, 4190, 4290, 4150, 4400, 4560, 4640, 4750]},
                         {"label": "Shops", "values": [1580, 1630, 1620, 1630, 1650, 1680, 1690, 1690]}],
              "stacked": True},
    "columns": [{"key": "product", "label": "Product"}, {"key": "units", "label": "Units", "number": True},
                {"key": "revenue", "label": "Revenue (£)", "number": True},
                {"key": "change", "label": "vs last week", "number": True}],
    "rows": [{"product": "Trail runner", "units": 182, "revenue": 14560, "change": "+9%"},
             {"product": "City sneaker", "units": 241, "revenue": 13255, "change": "+4%"},
             {"product": "Rain shell", "units": 96, "revenue": 8640, "change": "+18%"},
             {"product": "Merino socks", "units": 410, "revenue": 4920, "change": "−2%"},
             {"product": "Day pack", "units": 74, "revenue": 4440, "change": "+6%"},
             {"product": "Water bottle", "units": 281, "revenue": 2395, "change": "+1%"}],
}
_N5_CHECKLIST = {
    "title": "Launch day checklist",
    "sections": [
        {"title": "The day before", "items": [
            {"id": "notes", "text": "Write the release notes"},
            {"id": "status", "text": "Check the status page is green"},
            {"id": "support", "text": "Brief support on the known issues"}]},
        {"title": "On the day", "items": [
            {"id": "publish", "text": "Publish the beta build"},
            {"id": "email", "text": "Send the waitlist email"},
            {"id": "social", "text": "Post the announcement"},
            {"id": "watch", "text": "Watch sign-ups for the first hour"}]},
    ],
}
# Each way a page might try to leave its sandbox, and what it found.
_N5_PROBE = """<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
</head><body><h1>Sandbox check</h1><ul id="results" class="rb-list"></ul><script>
const said = (name, value) => { const li = document.createElement('li'); li.textContent = name + ': ' + value;
  results.append(li); };
async function probe() {
  try { await fetch('/api/v1/handshake'); said('fetch', 'reached'); } catch { said('fetch', 'blocked'); }
  try { said('parent', window.parent.document.title ? 'reached' : 'reached'); } catch { said('parent', 'blocked'); }
  try { said('cookies', document.cookie === undefined ? 'blocked' : 'reached'); } catch { said('cookies', 'blocked'); }
  try { const opened = window.open('https://example.com/'); said('popup', opened ? 'reached' : 'blocked'); }
  catch { said('popup', 'blocked'); }
  try { window.top.location.href = '/app-v2/'; said('top', 'asked'); } catch { said('top', 'blocked'); }
  said('rowbot', typeof rowbot.decide === 'function' && typeof rowbot.send === 'undefined' ? 'bridge only' : 'more');
  document.body.dataset.done = 'yes';
}
probe();
</script></body></html>"""


# What the person asks for, as they'd write it: each makes one kind of page.
_N5_PROMPTS = {
    "Make me a beta launch plan I can approve step by step": "plan",
    "Show me sales by week as a dashboard": "dashboard",
    "Make a launch day checklist for the beta": "checklist",
    "Make a page that checks its own sandbox": "probe",
}


@app.post("/__n5_fixture/reset")
def n5_reset(x_fixture_token: str = Header(default="")) -> dict:
    """Delete the chats with pages (and so their pages): each journey starts with only its own."""
    predecessor._authorize(x_fixture_token)
    from row_bot import pages
    from row_bot.thread_cleanup import delete_thread

    chats = {page.conversation_id for page in pages.list_pages()}
    for chat in chats:
        delete_thread(chat)
    return {"deleted": len(chats)}


def _n5_template(heading: str) -> str:
    """One template from the Making a page skill, exactly as the model reads it."""
    text = _N5_SKILL.read_text(encoding="utf-8")
    start = text.index("```html\n", text.index(heading)) + len("```html\n")
    return text[start:text.index("\n```", start)]


def _n5_page(thread: str, identity: str, name: str, args: dict):
    from row_bot.tools import pages_tool

    run = pages_tool._page_create if name == "page_create" else pages_tool._page_update
    yield from _s3_steps(thread, identity, [(name, args, lambda: (_s4_in_thread(thread, lambda: run(**args)), []))])


def _n5_stream(text: str, config: dict):
    call = predecessor._record("submit", config, "n5-page")
    thread = call["conversation_id"]
    identity = f"n5:{call['generation_id']}"
    try:
        kind = _N5_PROMPTS[text.strip()]
        if kind == "dashboard":
            args = {"title": "Sales by week", "html": _n5_template("### A dashboard"), "data": _N5_DASHBOARD,
                    "libraries": ["charts", "rowbot-style"]}
            final = "Here's the sales dashboard: revenue by week for online and shops, and every product below."
        elif kind == "checklist":
            args = {"title": "Launch day checklist", "html": _n5_template("### A checklist"),
                    "data": _N5_CHECKLIST, "libraries": ["rowbot-style"]}
            final = "Here's the launch day checklist. Tick things off as you go; it remembers on every device."
        elif kind == "probe":
            args = {"title": "Sandbox check", "html": _N5_PROBE, "libraries": ["rowbot-style"]}
            final = "Here's the sandbox check."
        else:
            args = {"title": "Beta launch plan", "html": _n5_template("### A plan with approvals"),
                    "data": _N5_PLAN, "libraries": ["rowbot-style"]}
            final = ("Here's the beta launch plan. Approve, change or skip each step, then send me your "
                     "decisions and I'll get going.")
        yield from _n5_page(thread, identity, "page_create", args)
        yield from _natural_final(call, thread, final, "n5")
    finally:
        call["quiesced"] = True


# ── N11: incognito chats ───────────────────────────────────────────────────
# The model's save_memory call is scripted; its card is the real tool's own in
# an incognito chat (run in a small graph, so it asks as it does in a turn).
# The answer's result is scripted: nothing is written to memory here.
_N11_PENDING: dict[str, tuple[str, dict]] = {}
_N11_COFFEE = {"category": "preference", "subject": "Coffee", "content": "I take it black, no sugar.", "tags": ""}
_N11_REMEMBER = "Remember that I take my coffee black, no sugar"
# The person's requests these chats script, and Row-Bot's replies (the save request asks first).
_N11_REPLIES = {
    "Find me somewhere quiet for a weekend away":
        "Here are three quiet places for a weekend away: Hay-on-Wye, Whitby and the Lake District.",
    "Plan a weekend in Leeds":
        "Here's a weekend in Leeds: Saturday at the Royal Armouries and Kirkgate Market, "
        "Sunday in Roundhay Park.",
    _N11_REMEMBER: "",
}


def _n11_card(thread: str) -> dict | None:
    """The real save_memory card for *thread*, or None when the chat is not incognito (it would save)."""
    from typing import TypedDict

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from row_bot.threads import incognito_state
    from row_bot.tools import memory_tool

    if not incognito_state(thread).incognito:
        return None

    class State(TypedDict):
        result: str

    builder = StateGraph(State)
    builder.add_node("save", lambda _state: {"result": memory_tool._save_memory(**_N11_COFFEE)})
    builder.add_edge(START, "save")
    builder.add_edge("save", END)
    paused = builder.compile(checkpointer=InMemorySaver()).invoke(
        {"result": ""}, {"configurable": {"thread_id": thread}})
    [asked] = paused["__interrupt__"]
    return dict(asked.value)


def _n11_stream(text: str, config: dict):
    from row_bot.threads import append_checkpoint_messages

    call = predecessor._record("submit", config, "n11")
    thread = call["conversation_id"]
    identity = f"n11:{call['generation_id']}"
    try:
        append_checkpoint_messages(thread, [HumanMessage(
            content=text, id=call["submission_id"] or fixture_id(identity + ":user"))])
        card = _n11_card(thread) if text.strip() == _N11_REMEMBER else None
        if card is None:
            reply = _N11_REPLIES.get(text.strip()) or "This request saves to memory only in an incognito chat."
            yield from _natural_final(call, thread, reply, "n11")
            return
        tool_id = fixture_id(identity + ":tool")
        append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":tool-call"), content="",
            tool_calls=[{"id": tool_id, "name": "save_memory", "args": _N11_COFFEE}])])
        yield "tool_call", {"tool_call_id": tool_id, "name": "save_memory", "args": _N11_COFFEE}
        _N11_PENDING[thread] = (tool_id, _N11_COFFEE)
        yield "interrupt", [{"__interrupt_id": fixture_id(identity + ":approval"), **card}]
    finally:
        call["quiesced"] = True


def _n5_sent(text: str, config: dict):
    """The person's Send under the plan: Row-Bot answers and moves the plan on with page_update."""
    from row_bot import pages

    call = predecessor._record("submit", config, "n5-sent")
    thread = call["conversation_id"]
    identity = f"n5-sent:{call['generation_id']}"
    try:
        page = next((item for item in pages.list_pages(thread) if item.page_id in text.split("\n", 1)[0]), None)
        if page is not None and page.title == _N5_PLAN["title"]:
            moved = {"announce": "done", "invites": "doing"}
            data = {**_N5_PLAN, "steps": [{**step, "status": moved.get(step["id"], step["status"])}
                                          for step in _N5_PLAN["steps"]]}
            yield from _n5_page(thread, identity, "page_update", {"page": page.page_id, "data": data})
        final = ("Thanks. The announcement is ready and the first batch of invites is going out now. I'll move the "
                 "launch call to Friday at 4pm and update the plan as I go.")
        yield from _natural_final(call, thread, final, "n5-sent")
    finally:
        call["quiesced"] = True


def _n11_resume(enabled_tools: list[str], config: dict, approved: bool, *, interrupt_ids=None, resume_values=None,
                stop_event=None):
    """Save or Don't save on the incognito card: the call's result, and a short reply."""
    from row_bot.threads import append_checkpoint_messages
    from row_bot.tools.memory_tool import INCOGNITO_NOT_SAVED

    thread = str(config["configurable"]["thread_id"])
    pending = _N11_PENDING.pop(thread, None)
    if pending is None:
        yield from _n12b_resume(enabled_tools, config, approved, interrupt_ids=interrupt_ids,
                                resume_values=resume_values, stop_event=stop_event)
        return
    call = predecessor._record("resume", config, "n11-answer")
    identity = f"n11-resume:{call['generation_id']}:{call['sequence']}"
    try:
        tool_id, args = pending
        content = ("Approval: asked; approved by you\nMemory saved successfully.\nSubject: Coffee\n"
                   "Content: I take it black, no sugar." if approved
                   else f"Approval: asked; denied by you — did not run\n{INCOGNITO_NOT_SAVED}")
        result_id = fixture_id(identity + ":result")
        append_checkpoint_messages(thread, [ToolMessage(id=result_id, tool_call_id=tool_id, name="save_memory",
                                                        content=content)])
        yield "tool_done", {"tool_call_id": tool_id, "message_id": result_id, "name": "save_memory",
                            "args": args, "content": content}
        final = "Saved: you take your coffee black, no sugar." if approved else "Okay, I won't save that."
        yield from _natural_final(call, thread, final, "n11-answer")
    finally:
        call["quiesced"] = True


def stream(text: str, enabled_tools: list[str], config: dict, *, stop_event=None):
    """Script real tools/media projection and a durable final behind a barrier."""
    if text.startswith("l1a ") and text.rstrip().endswith("fixture"):
        # Learning (L1a): a reply to Teach on, after a step that failed.
        yield from _l1a_stream(text, config)
        return
    if text.strip() in _N11_REPLIES:
        # Incognito chats (N11): a plain reply, or the real save_memory card in an incognito chat.
        yield from _n11_stream(text, config)
        return
    if text.startswith("n7 ") and text.rstrip().endswith("fixture"):
        # Several X accounts, named on every post (N7): the real card and sender, a fake X API.
        yield from _n7_stream(text, config)
        return
    if text.strip() in _N5_PROMPTS:
        # Pages (N5): the real page tools on the skill's templates.
        yield from _n5_stream(text, config)
        return
    if text.startswith("[") and "from the page" in text.split("\n", 1)[0]:
        # The person's Send under a page (N5), as the real message Row-Bot wrote from it.
        yield from _n5_sent(text, config)
        return
    if text.startswith("n12b ") and text.rstrip().endswith("fixture"):
        # Computer use, rebuilt (N12-B): the real computer tool on a fake desktop.
        yield from _n12b_stream(text, config)
        return
    if text.startswith("n12 ") and text.rstrip().endswith("fixture"):
        # Row-Bot's browser in the app (N12-A): a turn that uses the browser, then asks to pay.
        yield from _n12_stream(text, config)
        return
    if text.startswith("n10 ") and text.rstrip().endswith("fixture"):
        # Outlook's own cards (N10): an email to send, or an event that invites people.
        yield from _n10_stream(text, config)
        return
    if text.startswith("n4 ") and text.rstrip().endswith("fixture"):
        # Row-Bot asks questions with options (N4): one question, or two as steps.
        yield from _n4_stream(text, config)
        return
    if text.startswith("s6 ") and text.rstrip().endswith("fixture"):
        # Prepare sandbox (F18): a sandbox test run that lacked pytest; the offer, the job and
        # its card are real, Docker is a fake.
        yield from _s6_stream(text, config)
        return
    if text.startswith("s5 ") and text.rstrip().endswith("fixture"):
        # Send a file to your devices or a chat app (F7): the real tool, deliveries and cards, with
        # the fake Telegram adapter.
        yield from _s5_stream(text, config)
        return
    if text.startswith("s4 ") and text.rstrip().endswith("fixture"):
        # Video editing and many images at once (F3, F5): the real tools, jobs and cards, with a
        # synthetic ffmpeg program, a fake ffmpeg download and a scripted vision model.
        yield from _s4_stream(text, config)
        return
    if text.startswith("s3 ") and text.rstrip().endswith("fixture"):
        # Row-Bot's browser on local apps, screenshots, page sizes and recordings (F4, F2).
        yield from _s3_stream(text, config)
        return
    if text.startswith("s2 ") and text.rstrip().endswith("fixture"):
        # Background processes (F1, F6): the real start_process / run_command and process manager,
        # with small synthetic programs instead of a real server or build.
        yield from _s2_stream(text, config)
        return
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
    if "reviewer asked fixture" in text:
        # Auto (A0): the reviewer asked the person rather than approving; its reason is on the card.
        from row_bot.threads import append_checkpoint_messages
        call = predecessor._record("submit", config, "reviewer-asked")
        thread = call["conversation_id"]
        identity = f"reviewer-asked:{call['generation_id']}"
        tool_id = fixture_id(identity + ":tool")
        args = {"file_path": "notes/old-draft.md"}
        try:
            append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":tool-call"), content="",
                tool_calls=[{"id": tool_id, "name": "workspace_file_delete", "args": args}])])
            yield "tool_call", {"tool_call_id": tool_id, "name": "workspace_file_delete", "args": args}
            yield "interrupt", [{"__interrupt_id": fixture_id(identity + ":approval"),
                                 "tool": "workspace_file_delete", "label": "Delete file",
                                 "description": "Delete file: notes/old-draft.md", "args": args,
                                 "review": {"decision": "ask",
                                            "reason": "You asked to tidy the notes, not to delete any."}}]
        finally:
            call["quiesced"] = True
        return
    if "outside files fixture" in text:
        # 5.3.1 B1/B2: a change outside the workspace asks, and may be allowed for the rest of the chat.
        from row_bot.threads import append_checkpoint_messages
        call = predecessor._record("submit", config, "outside-files")
        thread = call["conversation_id"]
        identity = f"outside-files:{call['generation_id']}"
        tool_id = fixture_id(identity + ":tool")
        args = {"file_path": "D:/Videos/trip/cut-list.txt", "text": "00:00 intro"}
        try:
            append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":tool-call"), content="",
                tool_calls=[{"id": tool_id, "name": "workspace_write_file", "args": args}])])
            yield "tool_call", {"tool_call_id": tool_id, "name": "workspace_write_file", "args": args}
            yield "interrupt", [{"__interrupt_id": fixture_id(identity + ":approval"),
                                 "tool": "workspace_write_file", "label": "Change files outside the workspace?",
                                 "description": "Outside the workspace: D:/Videos/trip/cut-list.txt",
                                 "args": args}]
        finally:
            call["quiesced"] = True
        return
    if "decided without asking fixture" in text:
        # Auto and Hands-off (A0): steps that ran without asking say who let them run.
        from row_bot.threads import append_checkpoint_messages
        call = predecessor._record("submit", config, "decided-without-asking")
        thread = call["conversation_id"]
        identity = f"decided:{call['generation_id']}"
        steps = (
            ("workspace_write_file", {"file_path": "notes/summary.md"},
             "Approval: approved by the reviewer: You asked to save the summary to your notes\n"
             "Wrote notes/summary.md"),
            ("run_command", {"command": "git commit -am \"Tidy notes\""},
             "Approval: allowed by Hands-off\n$ git commit -am \"Tidy notes\"\n\n"
             "[Exit code: 0 | Duration: 0.21s]"),
        )
        try:
            for index, (name, args, result) in enumerate(steps):
                tool_id = fixture_id(f"{identity}:{index}:tool")
                result_id = fixture_id(f"{identity}:{index}:result")
                append_checkpoint_messages(thread, [AIMessage(id=fixture_id(f"{identity}:{index}:call"), content="",
                    tool_calls=[{"id": tool_id, "name": name, "args": args}])])
                yield "tool_call", {"tool_call_id": tool_id, "message_id": result_id, "name": name, "args": args}
                append_checkpoint_messages(thread, [ToolMessage(id=result_id, tool_call_id=tool_id, name=name,
                                                              content=result)])
                yield "tool_done", {"tool_call_id": tool_id, "message_id": result_id, "name": name, "args": args,
                                    "content": result}
            yield from _natural_final(call, thread, "Saved the summary and committed the tidy-up.", "decided")
        finally:
            call["quiesced"] = True
        return
    if "instant reply fixture" in text:
        # A turn that answers at once, before the app has refreshed after sending.
        from langchain_core.messages import HumanMessage
        from row_bot.threads import append_checkpoint_messages
        call = predecessor._record("submit", config, "instant")
        thread = call["conversation_id"]
        try:
            append_checkpoint_messages(thread, [HumanMessage(
                content=text, id=call["submission_id"] or fixture_id(f"instant:{call['generation_id']}:user"))])
            yield from _natural_final(call, thread, "Here it is, straight away.", "instant")
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
                                 "description": "Choose the folder; Row-Bot adds it and "
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
    if "maths fixture" in text:
        # S12: a reply with maths, inline and display, beside a price that stays text.
        call = predecessor._record("submit", config, "maths")
        try:
            yield from _natural_final(call, call["conversation_id"], _S12_MATHS, "maths")
        finally:
            call["quiesced"] = True
        return
    if text.strip() == "b407 digest fixture":
        # B407: a workflow's background turn that asks before it sends, as a scheduled run's would.
        from row_bot.threads import append_checkpoint_messages
        call = predecessor._record("submit", config, "b407-digest")
        thread = call["conversation_id"]
        identity = f"b407:{call['generation_id']}"
        name, call_id = "send_digest", fixture_id(identity + ":call")
        try:
            append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":tool-call"), content="",
                tool_calls=[{"id": call_id, "name": name, "args": {"to": "team"}}])])
            yield "tool_call", {"tool_call_id": call_id, "name": name, "args": {"to": "team"}}
            yield "interrupt", [{"__interrupt_id": fixture_id(identity + ":approval"), "tool": name,
                                 "question": "Send this morning's digest to the team?",
                                 "description": "Send this morning's digest to the team?", "args": {"to": "team"}}]
        finally:
            call["quiesced"] = True
        return
    if "unnamed app tool fixture" in text:
        # S12 (B388): an approval for a Counter tool this runtime never issued (as one left waiting from before a
        # restart): the card names the app from its one set-up connection, in plain words.
        from row_bot.threads import append_checkpoint_messages
        call = predecessor._record("submit", config, "unnamed-app-tool")
        thread = call["conversation_id"]
        identity = f"unnamed-app-tool:{call['generation_id']}"
        name, call_id = "mcp_counter_create_tally", fixture_id(identity + ":call")
        try:
            append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":tool-call"), content="",
                tool_calls=[{"id": call_id, "name": name, "args": {"label": "Visitors"}}])])
            yield "tool_call", {"tool_call_id": call_id, "name": name, "raw_name": name, "runtime_name": name,
                                "args": {"label": "Visitors"}}
            yield "interrupt", [{"__interrupt_id": fixture_id(identity + ":approval"), "tool": name,
                                 "description": "Create a tally named Visitors.", "args": {"label": "Visitors"}}]
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


@app.post("/__p3_fixture/named-conversation/{title}")
def named_conversation(title: str, x_fixture_token: str = Header(default="")) -> dict:
    """One ordinary conversation with a test-owned title, found by name in the sidebar."""
    predecessor._authorize(x_fixture_token)
    import re
    from row_bot import threads

    if not re.fullmatch(r"[A-Za-z0-9 -]{1,60}", title):
        raise HTTPException(status_code=422, detail="Invalid synthetic title")
    return {"conversation_id": threads.create_thread(title, name_source="manual", seed_default_skills=False)}


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
    def chat(capture, *, validate, usage_purpose='', usage_thread_id=''):
        # The real owner's signature: the usage-ledger tags (B1) are accepted and unused.
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
    from row_bot.providers import codex, xai_oauth
    if not client_platform_service.subscription_flows.dispose():
        raise HTTPException(status_code=409, detail="Previous synthetic sign-in still draining")
    class Client:
        def close(self): pass
    def listener(_flow, *, ready_callback, **_kwargs):
        ready_callback()
        return SimpleNamespace()
    flows = SubscriptionFlows(client_factory=lambda _guard: Client(), listener=listener)
    def module(provider):
        real = {'codex': codex, 'xai_oauth': xai_oauth}[provider]
        synthetic = SimpleNamespace(**{name: getattr(real, name) for name in dir(real) if not name.startswith('__')})
        flow = SimpleNamespace(verification_uri='https://example.invalid/device' if provider == 'codex' else None,
            authorization_url='https://example.invalid/authorize', user_code='SYNTHETIC' if provider == 'codex' else None,
            expires_at='2030-01-01T00:00:00+00:00', code_verifier='synthetic-private-verifier',
            redirect_uri='http://127.0.0.1:9/callback', token_url='https://example.invalid/token',
            client_id='synthetic-client', state='synthetic-state')
        setattr(synthetic, {'codex':'start_codex_device_flow','xai_oauth':'start_xai_oauth_flow'}[provider], lambda **_kwargs: flow)
        tokens = {'codex':codex.CodexTokenSet, 'xai_oauth':xai_oauth.XAIOAuthTokenSet}[provider]
        setattr(synthetic, {'codex':'exchange_codex_device_authorization','xai_oauth':'exchange_xai_oauth_authorization'}[provider],
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
    from row_bot.providers import codex
    folder = predecessor.DATA / 'synthetic-cli'
    folder.mkdir(exist_ok=True)
    primary = folder / 'auth.json'
    primary.write_text(json.dumps({'access_token':'synthetic-browser-cli-token'}), encoding='utf-8')
    codex.codex_auth_path = lambda *_args, **_kwargs: primary
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
            for provider_id in ("codex", "xai_oauth"):
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


# ── S8 (F10): many local models ─────────────────────────────────────────────
# A person with twenty-odd models in Ollama: each is in the composer's picker
# (a pinned choice with its saved catalog row) and on the synthetic local
# runtime that Setup and Settings › Models read. Favourites and hidden models
# start empty. Nothing reaches a runtime, a provider or the network.
_S8_LOCAL_MODELS = (
    "deepseek-r1:14b", "gemma3:12b", "gemma3:4b", "gpt-oss:20b", "granite3.3:8b", "llama3.1:8b",
    "llama3.2:3b", "llava:13b", "mistral-nemo:12b", "mistral-small3.2:24b", "mistral:7b", "phi4-mini:3.8b",
    "phi4:14b", "qwen2.5-coder:7b", "qwen3-coder:30b", "qwen3:14b", "qwen3:32b", "qwen3:8b", "qwen3.8:27b",
    "smollm2:1.7b",
)
_S8_CHAT = {"tasks": ["chat"], "input_modalities": ["text"], "output_modalities": ["text"], "tool_calling": True}
_s8_backup: dict | None = None


@app.post("/__s8_fixture/models/{action}")
def s8_models(action: str, favourites: str = "", hidden: str = "",
              x_fixture_token: str = Header(default="")) -> dict:
    """seed: twenty local models everywhere models are chosen, with any favourites
    and hidden models given (comma-separated names); restore: the profile back."""
    global _s8_backup
    predecessor._authorize(x_fixture_token)
    from row_bot.providers import config as provider_config, model_catalog_cache as cache, saved_model_settings
    from row_bot.providers.model_visibility import update_model_visibility
    from row_bot.providers.selection import add_quick_choice_for_model

    files = {
        "providers": Path(provider_config.CONFIG_PATH),
        "catalog": Path(cache.CATALOG_CACHE_PATH),
        "settings": predecessor.DATA / "model_settings.json",
    }
    if not all(path.resolve().is_relative_to(predecessor.DATA.resolve()) for path in files.values()):
        raise HTTPException(status_code=403, detail="Synthetic data scope required")
    if action == "seed":
        if _s8_backup is None:
            _s8_backup = {key: path.read_text(encoding="utf-8") if path.exists() else None
                          for key, path in files.items()}
        snapshot = cache.read_model_catalog_cache()
        from row_bot.providers.ollama import is_ollama_tool_capable

        def chat(name: str) -> dict:
            # Tools where the family uses them, as Ollama would report.
            return {**_S8_CHAT, "tool_calling": is_ollama_tool_capable(name)}

        cache.write_model_catalog_cache(cache.CatalogCacheSnapshot(
            1, snapshot.generated_at + 1, snapshot.cloud_cache,
            [{"provider_id": "ollama", "model_id": name, "display_name": name, "installed": True,
              "capabilities_snapshot": chat(name)} for name in _S8_LOCAL_MODELS],
            {"ollama": {"status": "ok", "count": len(_S8_LOCAL_MODELS)}}, (), "synthetic-browser",
        ))
        for name in _S8_LOCAL_MODELS:
            add_quick_choice_for_model(name, provider_id="ollama", display_name=name,
                                       capabilities_snapshot=chat(name), surface="chat")
        saved_model_settings.update_saved_model_settings(
            lambda raw: {key: value for key, value in raw.items() if key != "model_visibility"},
            path=files["settings"])
        for name in filter(None, favourites.split(",")):
            update_model_visibility("favourite", f"model:ollama:{name}", path=files["settings"])
        for name in filter(None, hidden.split(",")):
            update_model_visibility("hide", f"model:ollama:{name}", path=files["settings"])
        # Setup and Settings › Models read the same models from the local runtime.
        _p10_state.update(runtime="running", models=[*_S8_LOCAL_MODELS, "nomic-embed-text:latest"])
    elif action == "restore":
        import time

        for key, path in files.items():
            saved = (_s8_backup or {}).get(key)
            # Windows briefly refuses a file another reader has open: try again.
            for attempt in range(20):
                try:
                    if saved is not None:
                        path.write_text(saved, encoding="utf-8")
                    elif _s8_backup is not None and path.exists():
                        path.unlink()
                    break
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.1)
        _s8_backup = None
        _p10_state.update(runtime="not_installed", models=[])
    else:
        raise HTTPException(status_code=422, detail="Unknown model fixture action")
    return {"action": action, "models": len(_S8_LOCAL_MODELS)}


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
    agent.resume_stream_agent = _n11_resume
    readiness.evaluate_agent_readiness = fake_agent_readiness
    readiness.evaluate_chat_readiness = fake_chat_readiness
    from row_bot.api.v1 import routes
    from row_bot.application.client_platform import client_platform_service
    from row_bot.application.folder_selections import FolderSelections

    client_platform_service.readiness_factory = lambda _: True
    _p10_install_fakes()
    _p17_install_connection_check_fakes()
    client_platform_service.stream_factory = stream
    client_platform_service.resume_factory = _n11_resume
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
    _elsewhere["mode"] = "this_computer"  # Signed in from this computer unless a spec says otherwise (F27).
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
    with client_mcp_auth._LOCK:
        flow = next((f for f in client_mcp_auth._FLOWS.values() if f.oauth_state and f.oauth_state == state), None)
    if flow is not None and flow.delivery == "paste":
        # Another device's browser goes where the sign-in said: an address that doesn't load there (F27). A page
        # that moves on by itself, not a 302: a spec answers that address with a route, which Chromium applies to
        # a navigation but not reliably to a redirect hop.
        from urllib.parse import urlencode
        landing = flow.callback_uri + "?" + urlencode({"code": "synthetic-code", "state": state})
        return HTMLResponse("<!doctype html><title>Signing in</title>"
                            f'<meta http-equiv="refresh" content="0;url={escape(landing)}"><p>Returning to Row-Bot…</p>')
    client_mcp_auth.accept_callback(state=state, code="synthetic-code")
    return HTMLResponse("<!doctype html><title>Signed in</title><p>Signed in to the synthetic service.</p>")


_elsewhere: dict = {"mode": "this_computer"}
_ELSEWHERE_PUBLIC = "https://rowbot.example.test/api/v1/settings/mcp/auth/callback"


@app.post("/__s7_fixture/sign-in-elsewhere")
def s7_sign_in_elsewhere(mode: str = "paste", x_fixture_token: str = Header(default=""),
                         x_fixture_origin: str = Header(default="")) -> dict:
    """F27 on this machine only: apps sign in to the synthetic service of /__p5_fixture/apps as if this browser
    were another device. "paste": Row-Bot has no public address, so the browser ends on an address that doesn't
    load, to paste back; "public": Row-Bot's public address; "device": the service offers a code."""
    if mode not in {"paste", "public", "device"}:
        raise HTTPException(status_code=422, detail="mode")
    p5_apps(x_fixture_token, x_fixture_origin)
    import asyncio
    from row_bot.application import client_mcp_auth
    from row_bot.integrations import plans
    from row_bot.mcp_client import auth
    _elsewhere["mode"] = mode
    original = _elsewhere.setdefault("handler", plans._HANDLERS[("mcp", "sign_in")])

    def elsewhere(ctx, record, step):
        if _elsewhere["mode"] != "this_computer":
            ctx = replace(ctx, remote=True, redirect_uri=_ELSEWHERE_PUBLIC if _elsewhere["mode"] == "public" else "")
        return original(ctx, record, step)
    plans._HANDLERS[("mcp", "sign_in")] = plans._HANDLERS[("mcp", "allow_changes")] = elsewhere
    auth.discover_sign_in = lambda url: {"required": False, "metadata": True, "dcr": True, "device": mode == "device"}

    async def run_device(flow, label, client):
        with client_mcp_auth._LOCK:
            flow.device = {"user_code": "WDJB-MJHT", "verification_uri": "https://auth.example.test/device",
                           "verification_uri_complete": ""}
            flow.state = "waiting"
        client_mcp_auth._persist(flow)
        await asyncio.to_thread(flow.event.wait, 120)
        flow.authority()
        if not flow.code:
            raise auth.McpAuthError("mcp_auth_denied")
        auth.write_credentials(flow.ref, {"tokens": {"access_token": "synthetic-access", "token_type": "Bearer"}})
        client_mcp_auth._publish(flow, {"mode": "oauth", "credential_ref": flow.ref, "binding": auth.binding(flow.name, flow.cfg),
            "callback_uri": flow.callback_uri, "label": label, "operation_id": flow.command_id})
        return True
    client_mcp_auth._run_device = run_device
    return {"ready": True}


@app.post("/__s7_fixture/sign-in-elsewhere/allow-code")
def s7_allow_code(x_fixture_token: str = Header(default="")) -> dict:
    """The person entered the code on the synthetic service's page and allowed access."""
    predecessor._authorize(x_fixture_token)
    from row_bot.application import client_mcp_auth
    with client_mcp_auth._LOCK:
        flow = next(f for f in client_mcp_auth._FLOWS.values() if f.delivery == "device" and f.state == "waiting")
        flow.code, flow.callback_used = "synthetic-code", True
        flow.event.set()
    return {"allowed": True}


@app.post("/__s7_fixture/sign-in-elsewhere/expire")
def s7_expire(x_fixture_token: str = Header(default="")) -> dict:
    """Five minutes pass: every waiting sign-in is past its time (B338)."""
    predecessor._authorize(x_fixture_token)
    import time
    from row_bot.application import client_mcp_auth
    with client_mcp_auth._LOCK:
        flows = [f for f in client_mcp_auth._FLOWS.values() if f.state == "waiting"]
        for flow in flows:
            flow.created -= client_mcp_auth._TTL + 1
            flow.expires_at = time.time() - 1
    return {"expired": len(flows)}



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


_S12_MATHS = "\n".join([
    "The area under \\(y = x^2\\) from 0 to 1 is $\\tfrac{1}{3}$, and the plan costs $5 a month.",
    "",
    "$$",
    "\\int_0^1 x^2 \\, dx = \\left[ \\frac{x^3}{3} \\right]_0^1 = \\frac{1}{3}",
    "$$",
    "",
    "A wide one scrolls in its own box:",
    "",
    "\\[ \\sum_{n=1}^{\\infty} \\frac{1}{n^2} = 1 + \\frac{1}{4} + \\frac{1}{9} + \\frac{1}{16} + \\frac{1}{25}"
    " + \\frac{1}{36} + \\frac{1}{49} + \\frac{1}{64} + \\cdots = \\frac{\\pi^2}{6} \\]",
    "",
    "Written as code it stays code: `$x^2$`.",
])


@app.post("/__s12_fixture/monthly-workflow")
def s12_monthly_workflow(x_fixture_token: str = Header(default="")) -> dict:
    """B345: a switched-off workflow made from "at 08:00 on the 1st of every month"."""
    p4_provider_credentials(x_fixture_token)
    from row_bot import tasks
    task_id = tasks.create_task(
        name="Month-end close", description="The month's count, on the 1st.", icon="🗓️",
        schedule="cron:0 8 1 * *", enabled=False, safety_mode="approve", apply_default_skills=False,
        agent_profile_id=tasks.DEFAULT_WORKFLOW_AGENT_PROFILE_ID,
        prompts=["Using @Counter, total last month's count and note what changed."])
    return {"task_id": task_id}


_s12_google: dict = {}


@app.post("/__s12_fixture/google/{action}")
def s12_google(action: str, x_fixture_token: str = Header(default="")) -> dict:
    """F29: Google as a first sign-in leaves it (a synthetic client and token in the in-memory keychain, saved
    through the real sign-in path); ``remove`` puts the account and its switches back."""
    from row_bot import account_tokens
    from row_bot.application import client_account_oauth
    if action == "signed-in":
        p4_provider_credentials(x_fixture_token)
        _s12_google.update(gmail=_tool_registry.is_enabled("gmail"), calendar=_tool_registry.is_enabled("calendar"))
        _tool_registry.set_enabled("gmail", False)
        _tool_registry.set_enabled("calendar", False)
        account_tokens.write("google_client", {"installed": {
            "client_id": "synthetic-client", "client_secret": "synthetic-secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"]}})
        client_account_oauth._save_sign_in("google", {"token": "synthetic-token", "refresh_token": "synthetic-refresh",
                                                      "scopes": list(account_tokens.GOOGLE_SCOPES)})
        # Documentation-capture mode (this fixture) never remembers a check; a real sign-in's passes.
        account_tokens._CHECKS["google"] = (account_tokens.digest("google"), True)
        return {"gmail": _tool_registry.is_enabled("gmail"), "calendar": _tool_registry.is_enabled("calendar")}
    if action == "remove":
        predecessor._authorize(x_fixture_token)
        account_tokens.delete("google")
        account_tokens.delete("google_client")
        for tool in ("gmail", "calendar"):
            _tool_registry.set_enabled(tool, _s12_google.get(tool, False))
        return {"removed": True}
    raise HTTPException(status_code=404)


@app.post("/__s12_fixture/remove")
def s12_remove(x_fixture_token: str = Header(default="")) -> dict:
    """Take the S12 workflow away again (the Counter app goes with /__p6_fixture/views/remove)."""
    predecessor._authorize(x_fixture_token)
    from row_bot import tasks
    for task in tasks.list_tasks():
        if task.get("name") == "Month-end close":
            tasks.delete_task(task["id"])
    return {"removed": True}


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


# ── Learning (preview), L1a: the flag, the hardware check and the Overview's states ──

_l1a_saved: dict[str, object] = {}


def _l1a_stream(text: str, config: dict):
    from row_bot.threads import append_checkpoint_messages
    call = predecessor._record("submit", config, "l1a")
    thread = call["conversation_id"]
    identity = f"l1a:{call['generation_id']}"
    try:
        tool_id = fixture_id(identity + ":tool")
        result_id = fixture_id(identity + ":result")
        args = {"path": "budgets/2026-draft.md"}
        append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":call"), content="",
            tool_calls=[{"id": tool_id, "name": "workspace_read_file", "args": args}])])
        yield "tool_call", {"tool_call_id": tool_id, "message_id": result_id, "name": "workspace_read_file",
                            "args": args}
        result = "Error: budgets/2026-draft.md was not found."
        append_checkpoint_messages(thread, [ToolMessage(id=result_id, tool_call_id=tool_id,
                                                      name="workspace_read_file", content=result, status="error")])
        yield "tool_done", {"tool_call_id": tool_id, "message_id": result_id, "name": "workspace_read_file",
                            "args": args, "content": result, "status": "error"}
        yield from _natural_final(call, thread, "The 2026 budget for travel is 1,200 (from the draft).", "l1a")
    finally:
        call["quiesced"] = True


@app.post("/__l1a_fixture/learning/{action}")
def l1a_learning(action: str, x_fixture_token: str = Header(default="")) -> dict:
    """Learning (preview) for the L1a spec: the start-up flag, a faked hardware check and the
    Overview's states. Nothing reaches a GPU, a download or a provider."""
    predecessor._authorize(x_fixture_token)
    import time as _time

    from row_bot import learning
    from row_bot.learning import spending
    from row_bot.learning.training import hardware

    if "check" not in _l1a_saved:
        _l1a_saved["check"] = hardware.check
        _l1a_saved["peek"] = hardware.peek
        _l1a_saved["used"] = spending.used_this_month
        _l1a_saved["gpu_memory"] = hardware.gpu_memory
        _l1a_saved["memory_free"] = hardware.memory_free_for_training
    if action == "preview":
        learning._PREVIEW = True  # the start-up flag, as if launched with ROW_BOT_LEARNING_PREVIEW=1
        learning.invalidate()
        # The personal model's download (L2b): a stand-in whose states the specs set; never a real one.
        from row_bot.learning.training import downloads as _downloads
        _downloads._downloads = _FixtureDownloads()
    elif action in {"gpu", "small-gpu", "no-gpu"}:
        # A 16 GB laptop card (the 9B's smallest tier, O124) on a computer with memory to spare: the fake
        # card's memory and this computer's, never the real ones (a busy card or a computer short of
        # memory would make training wait in the spec).
        # small-gpu: an 8 GB card, which runs a personal model trained on Hugging Face but can't train one.
        memory = hardware.SystemMemory(limit_mib=65_536, free_mib=40_960)
        card = {"gpu": hardware.Gpu("NVIDIA GeForce RTX 5080 Laptop GPU", 16_303, "596.08"),
                "small-gpu": hardware.Gpu("NVIDIA GeForce RTX 5070 Laptop GPU", 8_151, "596.08")}.get(action)
        found = hardware.classify([card], memory) if card else hardware.HardwareCheck("no_gpu")
        hardware.check = lambda *, refresh=False, detector=None: found
        hardware.peek = lambda: found  # what reads that mustn't wait see (Runs in, the recipe's need)
        hardware.gpu_memory = lambda **kwargs: (600, card.memory_mib) if card else None
        memory_free = _l1a_saved["memory_free"]
        hardware.memory_free_for_training = lambda *, reader=None: memory_free(reader=reader or (lambda: memory))
    elif action == "paused":
        from row_bot.application.settings_commands import write_learning_settings
        write_learning_settings({"paused": True})
    elif action == "limit":
        limit = learning.read_settings().monthly_token_limit
        spending.used_this_month = lambda **_kwargs: limit
    elif action == "reading":
        from row_bot.learning import past_chats
        past_chats.stop_background(10)  # the real import of the fixture's chats, if still running
        from row_bot.learning.contracts import Job
        from row_bot.learning.store import LearningStore
        now = _time.time()
        LearningStore().put(Job(id=past_chats.JOB_ID, kind="past_chat_import", created_at=now, updated_at=now,
                                state="running", steps_total=214, steps_done=61))
    elif action == "pictures":
        # Browser and computer pictures kept in Learning data (P2): the real store, as capture writes a
        # browser turn's request (its pictures as their own blobs, named in the request).
        import hashlib as _hashlib
        import json as _json

        from row_bot.learning.contracts import Decision
        from row_bot.learning.store import REQUEST_FORMAT, LearningStore
        store = LearningStore()
        now = _time.time()
        for number in range(3):
            pictures = [b"\xff\xd8\xff\xe0" + _hashlib.sha256(f"p2-{number}-{part}".encode()).digest() * 5_000
                        for part in range(2)]
            shas = store.put_blobs(pictures)
            request = store.put_blob(_json.dumps({"format": REQUEST_FORMAT, "version": 1, "purpose": "agent",
                                                  "messages": [], "tools": None, "pictures": sorted(shas)},
                                                 sort_keys=True).encode())
            store.put(Decision(id=f"dec_p2_pictures_{number}", episode_id=f"ep_p2_pictures_{number}",
                               thread_id="p2-pictures", sequence=0, kind="model_call", created_at=now + number,
                               conversation_id="p2-pictures", fidelity="exact", status="ok", request_blob=request))
    elif action == "restore":
        from row_bot.learning.store import LearningStore
        hardware.check = _l1a_saved["check"]
        hardware.peek = _l1a_saved["peek"]
        spending.used_this_month = _l1a_saved["used"]
        hardware.gpu_memory = _l1a_saved["gpu_memory"]
        hardware.memory_free_for_training = _l1a_saved["memory_free"]
        past = sys.modules.get("row_bot.learning.past_chats")
        if past is not None:
            past.stop_background(10)
        capture = sys.modules.get("row_bot.learning.capture")
        if capture is not None:
            capture.learning_turned_off()
        store = LearningStore()
        if store.exists():
            for thread in store.opted_out_chats():
                store.set_chat_opt_out(thread, False)
            store.delete_all()
        for leftover in (learning.store_path(), learning.settings_path()):
            leftover.unlink(missing_ok=True)
        from row_bot.learning.training import downloads as _downloads
        _downloads._downloads = None
        learning._PREVIEW = False
        learning.invalidate()
    else:
        raise HTTPException(404, "Unknown Learning fixture action")
    return {"action": action}


# ── Learning (preview), L3a: last night's look and the changes it drafted ──


def _l3a_drafts() -> None:
    """Two drafted changes (a lesson and a way of working) and a kind that needs more examples."""

    import time as _time

    from row_bot import evolution
    from row_bot.learning.contracts import Attempt
    from row_bot.learning.store import LearningStore

    drafts = [
        ("lesson", "Use the current policy", "document_lookups",
         "When two versions of a policy exist, answer from the newest approved one and say which you used.",
         {"chats": 3, "corrections": 2, "stops": 1, "retries": 0, "denials": 0, "failures": 0, "imported": 1},
         False, {}),
        ("behaviour", "Check the calendar before booking", "tool_workflows",
         "Before adding an event, look at that day's calendar and say if it overlaps another event.",
         {"chats": 2, "corrections": 0, "stops": 2, "retries": 1, "denials": 0, "failures": 0, "imported": 0},
         True, {"slot": "tool_guidance"}),
    ]
    for number, (kind, title, work, text, why, never, extra) in enumerate(drafts):
        evolution.create_proposal(
            insight_ids=[], proposal_type=kind, title=title, rationale="Fixture draft.", status="draft",
            payload={"text": text, "kind_of_work": work, **extra,
                     "learning": {"issue": f"{work}:fixture{number}", "attempt_id": f"att_fixture{number}",
                                  "kind_of_work": work, "state": "waiting_to_test", "never_automatic": never,
                                  "why": why}})
    LearningStore().put(Attempt(id="att_fixture_examples", change_kind="wait", hypothesis="Not enough yet.",
                                created_at=_time.time(), kind_of_work="small_coding", reason="needs_examples",
                                issue="small_coding:corrected"))


def _l3a_forget_drafts() -> None:
    from row_bot import evolution

    store = evolution._load_store()
    store["proposals"] = [item for item in store["proposals"]
                          if not isinstance((item.get("payload") or {}).get("learning"), dict)]
    evolution._save_store(store)


@app.post("/__l3a_fixture/learning/{action}")
def l3a_learning(action: str, x_fixture_token: str = Header(default="")) -> dict:
    """Learning (preview) for the L3a spec: last night's look in each of its states, and the changes it
    drafted. Nothing reaches a model, a GPU or a download: the reports and drafts are written directly."""
    predecessor._authorize(x_fixture_token)
    from datetime import date

    from row_bot.learning import diagnosis
    from row_bot.learning.store import LearningStore

    night = date.today().isoformat()
    if action == "night":
        diagnosis._save(LearningStore(), diagnosis.NightReport(night, found=3, drafted=2, refused=1,
                                                               needs_examples=1, bugs=1))
        _l3a_drafts()
    elif action in {"claude_plan", "stopped", "nothing", "unavailable"}:
        diagnosis._save(LearningStore(), diagnosis.NightReport(
            night, state=action, found=2 if action == "stopped" else 0, drafted=1 if action == "stopped" else 0,
            diagnosed=1 if action == "stopped" else 0))
    elif action == "restore":
        _l3a_forget_drafts()
    else:
        raise HTTPException(404, "Unknown Learning fixture action")
    return {"action": action}


# ── Learning (preview), L3b: changes tested, ready, in use, not used; lessons; Used a lesson ──

_L3B_CHAT = "l3b-policies"


def _l3b_change(kind: str, title: str, work: str, text: str, *, state: str, test: dict | None = None,
                status: str = "draft", slot: str = "", why: dict | None = None, never: bool = False) -> str:
    from row_bot import evolution

    learning = {"issue": f"{work}:l3b-{title[:12]}", "attempt_id": None, "kind_of_work": work, "state": state,
                "never_automatic": never,
                "why": why or {"chats": 4, "corrections": 2, "stops": 1, "retries": 0, "denials": 0, "failures": 0,
                               "imported": 0}}
    if test is not None:
        learning["test"] = test
    payload = {"text": text, "kind_of_work": work, "learning": learning, **({"slot": slot} if slot else {})}
    return str(evolution.create_proposal(insight_ids=[], proposal_type=kind, title=title, rationale="Fixture.",
                                         status=status, payload=payload, dedupe=False)["id"])


def _l3b_test(label: str, total: int, without: int, with_change: int, verdict: str = "better",
              difference: float = 0.25) -> dict:
    import time as _time

    return {"verdict": verdict, "rows": [{"label": label, "total": total, "without": without, "with": with_change},
                                         {"label": "Other tasks", "total": 18, "without": 17, "with": 17}],
            "note": "Testing used 1.1M tokens · Small test, so expect some variation.", "small": True,
            "tested_at": _time.time() - 5 * 3600,
            "own": {"problems": total, "difference": difference, "low": 0.05, "high": 0.45}}


def _l3b_changes() -> None:
    import json as _json
    import time as _time
    from datetime import date

    from row_bot.learning import changes, diagnosis
    from row_bot.learning.store import LearningStore

    _l3b_change("lesson", "Pick documents by ID, not title", "document_lookups",
                "When two documents share a title, choose by ID and say which one you picked.", state="tested",
                status="ready", test=_l3b_test("Document lookups it hadn’t seen", 12, 7, 10))
    _l3b_change("behaviour", "Check the calendar before booking", "tool_workflows",
                "Before adding an event, read that day's calendar and say if it overlaps another event.",
                state="tested", status="ready", slot="tool_guidance",
                test=_l3b_test("Tool workflows it hadn’t seen", 9, 4, 7))
    _l3b_change("lesson", "Use the current policy", "document_lookups",
                "Answer from the newest approved policy and name its ID.", state="edited",
                test=_l3b_test("Document lookups it hadn’t seen", 12, 8, 10))
    _l3b_change("lesson", "Pick the newest file", "structured_transforms",
                "When a folder holds several exports, use the newest one.", state="not_better", status="rejected",
                test=_l3b_test("Structured transforms it hadn’t seen", 10, 9, 9, verdict="no_difference",
                               difference=0.0))
    _l3b_change("behaviour", "Hand long reports to a helper", "other",
                "When a report needs more than ten steps, hand it to a helper and say so.", state="waiting_to_test",
                slot="delegation", never=True)
    used = _l3b_change("lesson", "Say which document you used", "document_lookups",
                       "After answering from a document, name the document and its ID.", state="tested",
                       status="ready", test=_l3b_test("Document lookups it hadn’t seen", 12, 9, 11))
    changes.use(used, by="automatic")
    night = date.today().isoformat()
    store = LearningStore()
    diagnosis._save(store, diagnosis.NightReport(night, found=4, drafted=3, refused=0))  # noqa: SLF001
    store.set_meta_values({changes.NIGHT_KEY: _json.dumps(changes.NightTests(
        night, tested=3, ready=2, not_better=1, used=1, tokens=2_400_000).to_dict())})
    memory = changes.block(changes.get(used))["memory_id"]
    _l3b_chat(memory)
    del _time


def _l3b_chat(memory_id: str) -> None:
    """A chat whose first turn recalled the lesson in use: its reply says "Used a lesson"."""

    from datetime import datetime, timezone

    from row_bot import threads, turn_context

    if not threads._thread_exists(_L3B_CHAT):  # noqa: SLF001
        threads.create_thread("Policies", thread_id=_L3B_CHAT, seed_default_skills=False)
    values = turn_context.TurnContextValues(
        now=datetime.now(timezone.utc), memories=({"id": memory_id, "subject": "Say which document you used",
                                                   "content": "Name the document and its ID."},),
        recall_block=lambda memories: "Relevant memories:\n- Name the document and its ID.")
    context = turn_context.build_message(turn_id="l3b-t1", values=values, baseline=turn_context.Baseline(),
                                         summary_marker="")
    threads.append_checkpoint_messages(_L3B_CHAT, [
        HumanMessage(id="l3b-ask-1", content="What is the client dinner limit?"), context,
        AIMessage(id="l3b-reply-1", content="85 EUR a person, from POL-114 (Travel policy, updated 2026-02-10)."),
    ])


def _l3b_testing() -> None:
    from row_bot import learning
    from row_bot.learning import changes

    proposal = next(item for item in changes.learning_changes() if item.get("title") == "Hand long reports to a helper")
    path = learning.folder_path() / changes.PROGRESS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"change_id": proposal["id"], "title": proposal["title"], "state": "running",
                                "done": 9, "total": 24}), encoding="utf-8")


def _l3b_saved_lesson() -> None:
    from row_bot import knowledge_graph

    knowledge_graph.save_entity("self_knowledge", "Shell: pip needs --user on macOS",
                                "On macOS, install Python packages with --user when the system Python is used.")


def _l3b_restore() -> None:
    from row_bot import knowledge_graph, learning
    from row_bot.learning import changes

    for proposal in changes.learning_changes():
        if changes.view_state(proposal) == "in_use":
            try:
                changes.undo(str(proposal["id"]))
            except Exception:  # noqa: BLE001
                pass
    _l3a_forget_drafts()
    for row in knowledge_graph.list_entity_summaries("self_knowledge", limit=200):
        knowledge_graph.delete_entity(str(row["id"]))
    for name in (learning.WAYS_OF_WORKING_FILE, learning.LESSONS_FILE):
        (learning.data_dir() / name).unlink(missing_ok=True)
    (learning.folder_path() / changes.PROGRESS_FILE).unlink(missing_ok=True)
    from row_bot import threads
    from row_bot.thread_cleanup import delete_thread

    if threads._thread_exists(_L3B_CHAT):  # noqa: SLF001 - its turn context names this test's lesson
        delete_thread(_L3B_CHAT)


@app.post("/__l3b_fixture/learning/{action}")
def l3b_learning(action: str, x_fixture_token: str = Header(default="")) -> dict:
    """Learning (preview) for the L3b spec: changes in each state (tested, ready, edited, in use by itself, not
    used, waiting), last night's tests, a test running now, a lesson Row-Bot saved for itself, and a chat whose
    reply used a lesson. Nothing reaches a model, a GPU or a download: tests are written as their results."""
    predecessor._authorize(x_fixture_token)
    if action == "changes":
        _l3b_changes()
    elif action == "testing":
        _l3b_testing()
    elif action == "saved_lesson":
        _l3b_saved_lesson()
    elif action == "restore":
        _l3b_restore()
    else:
        raise HTTPException(404, "Unknown Learning fixture action")
    return {"action": action}


# ── Learning (preview), L4a: how Row-Bot learns: its methods, their trials and history, a night's switch ──


def _l4a_methods() -> None:
    """Method v1 (the first), v2 tried and dropped, v3 switched in by a trial (with Undo)."""

    import time as _time
    from dataclasses import replace

    from row_bot.learning.improver import archive
    from row_bot.learning.improver import strategy as strategy_module
    from row_bot.learning.store import LearningStore

    store = LearningStore()
    now = _time.time()
    first = archive.ensure_first(store)
    tried = archive.add(store, replace(first.strategy, budget=replace(first.strategy.budget, tests=4)),
                        parents=(first.id,), at=now - 2 * 86_400)
    archive.set_status(store, tried, "not_used")
    archive.record_trial(store, {"id": "trial_l4a_1", "at": now - 2 * 86_400, "finished_at": now - 2 * 86_400 + 3600,
                                 "problems": 30, "arm_tokens": 2_000_000, "incumbent": first.id, "control": first.id,
                                 "winner": first.id, "switched": False, "arms": [
                                     {"role": "incumbent", "release": first.id, "version": 1, "accepted": 1,
                                      "better": 0, "tokens": 1_400_000, "yield": 0.0},
                                     {"role": "challenger", "release": tried.id, "version": 2, "accepted": 2,
                                      "better": 0, "tokens": 1_900_000, "yield": 0.0}]})
    won = archive.add(store, replace(first.strategy, methods=strategy_module.MethodChoice(
        ("way_of_working", "lesson", "create_skill", "patch_skill", "train"))), parents=(first.id,), at=now - 5 * 3600)
    archive.switch(store, won, by="trial", trial="trial_l4a_2", at=now - 4 * 3600)
    archive.record_trial(store, {"id": "trial_l4a_2", "at": now - 5 * 3600, "finished_at": now - 4 * 3600,
                                 "problems": 31, "arm_tokens": 1_500_000, "incumbent": first.id, "control": first.id,
                                 "winner": won.id, "switched": True, "arms": [
                                     {"role": "incumbent", "release": first.id, "version": 1, "accepted": 1,
                                      "better": 0, "tokens": 1_000_000, "yield": 0.0},
                                     {"role": "challenger", "release": won.id, "version": 3, "accepted": 2,
                                      "better": 1, "tokens": 500_000, "yield": 2.0}]})


def _l4a_night(switched: bool) -> None:
    import json as _json
    from datetime import date

    from row_bot.learning import changes, diagnosis
    from row_bot.learning.improver import meta
    from row_bot.learning.store import LearningStore

    store = LearningStore()
    night = date.today().isoformat()
    diagnosis._save(store, diagnosis.NightReport(night, found=2, drafted=1))
    store.set_meta_values({changes.NIGHT_KEY: _json.dumps(changes.NightTests(night, tested=1, not_better=1).to_dict())})
    report = meta.TrialReport(night, "done", "trial_l4a_2", 31,
                              switched={"from": 1, "to": 3, "words": "tries a way of working first"} if switched else None)
    meta._note(store, report)


def _l4a_restore() -> None:
    from row_bot.learning.improver import archive, meta
    from row_bot.learning.store import LearningStore

    store = LearningStore()
    if not store.exists():
        return
    for entry in archive.entries(store):
        store.remove("release", entry.id)
    store.set_meta_values({archive.CHAIN_KEY: "[]", archive.TRIALS_KEY: "[]", meta.NIGHT_KEY: "null",
                           meta.STATE_KEY: "null"})


@app.post("/__l4a_fixture/learning/{action}")
def l4a_learning(action: str, x_fixture_token: str = Header(default="")) -> dict:
    """Learning (preview) for the L4a spec: learning methods and their trials, and a night that switched to a new
    method or kept the one in use. Nothing reaches a model: methods and trial results are written directly."""
    predecessor._authorize(x_fixture_token)
    if action == "methods":
        _l4a_methods()
    elif action in {"switched", "kept"}:
        _l4a_night(action == "switched")
    elif action == "restore":
        _l4a_restore()
    else:
        raise HTTPException(404, "Unknown Learning fixture action")
    return {"action": action}


# ── Learning (preview), L2b: the personal model's download, its states set by the spec ──

_GB = 1_000_000_000


class _FixtureDownloads:
    """Stands in for ``training.downloads.Downloads``: what the surfaces read and the commands do,
    with states the specs set. Nothing is downloaded or installed."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.state, self.phase, self.failure = "none", None, None
        self.done, self.total, self.needs, self.free = 0, 0, None, 512 * _GB
        self.engine_state, self.engine_bytes = "not_installed", 0
        self.model_state, self.model_bytes = "not_downloaded", 0

    def set(self, name: str) -> None:
        self.reset()
        if name == "installed":
            self.engine_state, self.engine_bytes = "installed", 4_033_251_988
            self.model_state, self.model_bytes = "downloaded", 19_331_993_000
        elif name == "damaged":
            self.engine_state, self.engine_bytes = "damaged", 0
            self.model_state, self.model_bytes = "downloaded", 19_331_993_000
        elif name in ("downloading", "model", "installing"):
            self.state = "downloading"
            self.phase = {"downloading": "engine", "model": "model", "installing": "installing"}[name]
            self.done = {"engine": 1_190_000_000, "model": 12_930_000_000, "installing": 21_549_991_432}[self.phase]
            self.total = 21_549_991_432
        elif name == "paused":
            self.state, self.done, self.total = "paused", 3_120_000_000, 21_549_991_432
        elif name == "disk":
            self.state, self.needs, self.free = "not_enough_disk", 24_180_000_000, 6_030_000_000
            self.total = 21_549_991_432
        elif name == "failed":
            self.state, self.failure, self.done, self.total = "failed", "network", 2_480_000_000, 21_549_991_432
        elif name == "server":  # L2x: an engine from before Row-Bot's own model server, only that missing
            self.engine_state, self.engine_bytes = "installed", 4_033_251_988
            self.model_state, self.model_bytes = "downloaded", 19_331_993_000
            self.state, self.total = "ready", 578_117_583
        elif name != "ready":
            raise HTTPException(404, "Unknown download state")

    def view(self):
        from row_bot import learning
        from row_bot.learning.training.downloads import DownloadView

        state = self.state
        missing = not (self.engine_state == "installed" and self.model_state == "downloaded")
        if state == "none" and missing and learning.read_settings().model_download == "waiting":
            state = "ready"
        return DownloadView(state, self.phase, self.done, self.total, needs_bytes=self.needs, free_bytes=self.free,
                            failure=self.failure, engine_state=self.engine_state, engine_bytes=self.engine_bytes,
                            engine_download_bytes=2_217_998_432, model_state=self.model_state,
                            model_bytes=self.model_bytes)

    def running(self) -> bool:
        return self.state == "downloading"

    def start(self):
        from row_bot import learning
        from row_bot.learning.training.downloads import DownloadError

        settings = learning.read_settings()
        if not settings.enabled:
            raise DownloadError("learning_off")
        if settings.paused:
            raise DownloadError("learning_paused")
        self.set("downloading")
        return self.view()

    def stop(self, reason: str = "you"):
        from row_bot.learning.training.downloads import DownloadError

        if self.state != "downloading":
            raise DownloadError("changed")
        self.state, self.phase = "paused", None
        return self.view()

    def cancel(self):
        self.reset()
        return self.view()

    def forget(self) -> None:
        if self.state != "downloading":
            self.state = "none"

    def stop_quietly(self, reason: str) -> None:
        if self.state == "downloading":
            self.state, self.phase = "paused", None

    def shutdown(self) -> None:
        return None


@app.post("/__l2b_fixture/learning/{action}")
def l2b_learning(action: str, name: str = "", x_fixture_token: str = Header(default="")) -> dict:
    """The personal model's download for the L2b spec: its states, and an NVIDIA driver too old
    to train. Nothing reaches the network, a GPU or a provider."""
    predecessor._authorize(x_fixture_token)
    from row_bot.learning.training import downloads, hardware

    current = downloads.loaded()
    if action == "state":
        if not isinstance(current, _FixtureDownloads):
            raise HTTPException(409, "Turn on the preview first")
        current.set(name)
    elif action == "old-driver":
        found = hardware.classify([hardware.Gpu("NVIDIA GeForce RTX 4090 Laptop GPU", 16_376, "560.94")])
        hardware.check = lambda *, refresh=False, detector=None: found
    else:
        raise HTTPException(404, "Unknown Learning fixture action")
    return {"action": action}


# ── Learning (preview), L2x: Row-Bot's own model server, "Runs in" and the larger tiers ──

_l2x: dict[str, object] = {}
_l2x_models: list[tuple[object, str, object]] = []  # what two-models replaced, put back by restore


_c1: dict[str, object] = {}


@app.post("/__c1_fixture/learning/{action}")
def c1_learning(action: str, name: str = "", x_fixture_token: str = Header(default="")) -> dict:
    """Training on Hugging Face for the C1 spec: a fake Hub and sign-in (in memory) in place of Hugging Face,
    and a Hugging Face run at each of its states. Nothing reaches the network, a GPU or a real account."""
    predecessor._authorize(x_fixture_token)
    import json as _json

    from row_bot.application.settings_commands import write_learning_settings
    from row_bot.learning import runner as runner_module
    from row_bot.learning.remote import hub as hub_module
    from row_bot.learning.remote import training as remote_training
    from row_bot.learning.training import jobs as jobs_module
    from row_bot.learning.training import recipes
    from tests.fixtures.hugging_face import FakeFetcher, FakeHub, FakeSignIn
    from tests.fixtures.learning import FakeDataset

    runner = runner_module.get()
    if action in ("restore", "hugging-face"):
        remote_training.spend_path().unlink(missing_ok=True)  # each spec starts with no spending this month
    if action == "restore":
        if "remote" in _c1:
            runner.remote = _c1.pop("remote")
        remote_training.configure(None)
        _c1.clear()
        return {"action": action}
    if action == "hugging-face":  # name: signed_out, signing_in, signed_in, small (signed in, a 12 GB card)
        hub = FakeHub()
        sign_in = FakeSignIn(signed_in=name in ("signed_in", "small"))
        sign_in.signing_in = name == "signing_in"
        card = 12_288 if name == "small" else 32_607
        trainer = remote_training.RemoteTrainer(
            jobs=runner.supervisor.jobs, hub=hub, sign_in=sign_in, fetcher=FakeFetcher(hub),
            card_mib=lambda: card, start_threads=False,
            flavors=lambda: [hub_module.Flavor("a100-large", "Nvidia A100", 80, 2.5 / 60),
                             hub_module.Flavor("l40sx1", "Nvidia L40S", 48, 0.03)])
        trainer.dataset = runner.remote.dataset if runner.remote is not None else None
        _c1.setdefault("remote", runner.remote)
        _c1.update(hub=hub, sign_in=sign_in, trainer=trainer)
        runner.remote = trainer
        remote_training.configure(trainer)
        write_learning_settings({"train_where": "hugging_face", "hugging_face.model": ""})
        return {"action": action}
    trainer = _c1.get("trainer")
    if trainer is None:
        raise HTTPException(409, "Set up the fake Hugging Face first")
    hub, sign_in = _c1["hub"], _c1["sign_in"]
    if action == "authorize":
        sign_in.authorize()
        return {"action": action}
    if action == "run":
        # name: first (planned, never confirmed), planned, over-cap, uploading, setting-up, training, exporting,
        # downloading, lost, no-credit, done (the version made ready and the uploads deleted)
        jobs = runner.supervisor.jobs
        recipe = recipes.load("hf-27b")
        steps = recipe.steps_for(412)
        job = jobs.plan(steps_total=steps, checkpoint_every=10, recipe=recipe.spec(None), backend="hugging_face")
        data = jobs_module.run_dir(job.id) / "data"
        data.mkdir(parents=True, exist_ok=True)
        FakeDataset(examples=412).prepare(job, data)
        confirmed = name != "first"
        write_learning_settings({"hugging_face.runs_confirmed": confirmed,
                                 "hugging_face.monthly_cap_usd": 20 if confirmed else 0})
        if name == "over-cap":
            remote_training.record_spend("job_earlier", cost_usd=18.4, seconds=36_000, flavor="l40sx1",
                                         at=trainer.clock(), final=True)
        if name in ("first", "planned", "over-cap"):
            return {"action": action, "job_id": job.id}
        trainer.start(job, "train_now", confirmed=True)
        moved = jobs.get(job.id)
        remote = hub.jobs[moved.remote_request_id] if moved.remote_request_id else None
        record = remote_training.read_record(job.id)
        if name == "uploading":
            jobs.move(job.id, "stopped", reason="you")
            new = jobs.plan(steps_total=steps, checkpoint_every=10, recipe=recipe.spec(None), backend="hugging_face")
            jobs.move(new.id, "preparing", reason="train_now")
            folder = jobs_module.run_dir(new.id)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / remote_training.REMOTE_FILE).write_text(_json.dumps(
                {**record.to_dict(), "job_id": new.id, "phase": "uploading", "remote_id": None}), encoding="utf-8")
            return {"action": action}
        stage = {"setting-up": "setting_up", "training": "training", "exporting": "exporting",
                 "lost": "training", "no-credit": "setting_up"}.get(name)
        if stage:
            remote.stage, remote.running_seconds = "RUNNING", {"setting_up": 240, "training": 2_520,
                                                               "exporting": 5_100}.get(stage, 240)
            hub.say(remote.id, "stage", stage=stage, steps_total=steps)
            if stage in ("training", "exporting"):
                hub.say(remote.id, "progress", step=118 if stage == "training" else steps, steps_total=steps,
                        loss=0.31, seconds_per_step=41.0)
            trainer.tick()
        if name == "queued":  # Hugging Face has no free card of the kind yet (SCHEDULING)
            trainer.tick()
        if name == "lost":
            hub.fail["job"] = hub_module.HubError("network")
            trainer.tick()
        if name == "no-credit":
            remote.stage = "ERROR"
            hub.say(remote.id, "error", reason="no_credit", stage="setting_up")
            trainer.tick()
        if name in ("downloading", "done"):
            hub.finish(remote.id, output_repo=record.output_repo, running_seconds=6_120)
            if name == "downloading":
                remote_training.write_record(remote_training.RunRecord(**{
                    **record.to_dict(), "outputs": tuple(record.outputs), "phase": "downloading",
                    "stage": "done", "running_seconds": 6_120, "cost_usd": remote_training.cost(6_120, 1.8)}))
            else:
                trainer.tick()  # downloaded, evaluating
                current = remote_training.read_record(job.id)
                jobs.move(job.id, "ready", reason="made_ready")
                remote_training.write_record(remote_training.RunRecord(**{
                    **current.to_dict(), "outputs": tuple(current.outputs)}))
                trainer.tick()  # settled: the uploads go
        return {"action": action, "job_id": job.id}
    raise HTTPException(404, "Unknown Learning fixture action")


@app.post("/__l2x_fixture/learning/{action}")
def l2x_learning(action: str, name: str = "", x_fixture_token: str = Header(default="")) -> dict:
    """Row-Bot's own model server for the L2x spec: an RTX 5090 (or no NVIDIA card), the server's
    files installed or not, and a version at each of its states there. Nothing starts a server,
    reaches the network or needs a GPU."""
    predecessor._authorize(x_fixture_token)
    from dataclasses import replace as _replace

    from row_bot.learning.training import hardware, lockfile, releases, server_files

    patched = (("hardware", "check", hardware), ("hardware", "peek", hardware),
               ("hardware", "gpu_memory", hardware), ("hardware", "memory_free_for_training", hardware),
               ("lockfile", "tools_platform", lockfile), ("server_files", "status", server_files))
    if action == "restore":
        for module, attribute, owner in patched:
            original = _l2x.pop(f"{module}.{attribute}", None)
            if original is not None:
                setattr(owner, attribute, original)
        _l2x.clear()
        while _l2x_models:
            owner, attribute, original = _l2x_models.pop()
            setattr(owner, attribute, original)
        hardware.forget_memory_reading()
        from row_bot.learning import runner as runner_module
        from row_bot.learning import versions as learning_versions

        runner_module.forget_card_room()
        learning_versions._needs_path(None).unlink(missing_ok=True)  # O124's dismissed offer, among others
        return {"action": action}
    for module, attribute, owner in patched:
        _l2x.setdefault(f"{module}.{attribute}", getattr(owner, attribute))
    if action == "two-models":  # O112, O124: a stand-in smaller model beside the 9B (before "card")
        from row_bot.learning.training import weights
        from tests.fixtures.learning import stand_in_small_model

        def patch(owner: object, attribute: str, value: object) -> None:
            _l2x_models.append((owner, attribute, getattr(owner, attribute)))
            setattr(owner, attribute, value)

        stand_in_small_model(patch, weights.QWEN35_2B.files)  # the download is the fixture's stand-in
        return {"action": action}
    if action == "card":
        # name: 5090 (the 32 GB tier, with 128 GB of RAM and virtual memory), 5090-short (memory that can't
        # hold its largest certified tier, O105; with two-models), 5090-busy (its display and apps hold
        # 22.5 GB: with two-models Row-Bot recommends the smaller model, O112), 4080-low (a 16 GB card on a
        # computer whose memory can't hold even its tier) or none (no NVIDIA card: not on this computer)
        needs = sorted(tier.system_memory_mib for tier in hardware.TIERS if tier.certified and tier.system_memory_mib)
        # 5090-short: room for the smallest tier only (the next one would offer a larger model).
        # Looked up only for the cards that need it: an OS with fewer measured tiers has fewer needs.
        limit = needs[1] - 1024 if name == "5090-short" else needs[0] - 3072 if name == "4080-low" else 131_072
        memory = hardware.SystemMemory(limit_mib=limit, free_mib=min(limit, 90_000) // 2)
        gpus = ([] if name == "none" else [hardware.Gpu("NVIDIA GeForce RTX 4080", 16_376, "596.08", 300)]
                if name == "4080-low" else [hardware.Gpu("NVIDIA GeForce RTX 5090", 32_607, "596.08", 23_000)]
                if name == "5090-busy" else [hardware.Gpu("NVIDIA GeForce RTX 5090", 32_607, "610.60", 2_400)])
        found = hardware.classify(gpus, memory)
        hardware.check = lambda *, refresh=False, detector=None: found
        hardware.peek = lambda: found
        # The fake card's memory now, and this computer's, never the real ones: a busy real card or a
        # computer short of memory would make training wait in the spec.
        card = max(gpus, key=lambda gpu: gpu.memory_mib, default=None)
        reading = (card.used_mib or 0, card.memory_mib) if card else None
        hardware.gpu_memory = lambda **kwargs: reading
        memory_free = _l2x["hardware.memory_free_for_training"]
        hardware.memory_free_for_training = lambda *, reader=None: memory_free(reader=reader or (lambda: memory))
        hardware.forget_memory_reading()
        lockfile.tools_platform = lambda platforms=None: "windows-x64"  # a build for this computer
    elif action == "starting-model":  # O124: a setup made on another starting model (name: its id)
        from row_bot.application.settings_commands import write_learning_settings

        write_learning_settings({"starting_model": name})
    elif action == "room":  # O112: two quiet-hours nights without room on the card for the 9B
        import json as _json

        from row_bot.application.settings_commands import write_learning_settings
        from row_bot.learning import runner as runner_module
        from row_bot.learning.training import jobs as jobs_module
        from row_bot.learning.training import recipes as recipe_files

        write_learning_settings({"starting_model": "qwen3.5-9b"})
        nine = recipe_files.for_model("qwen3.5-9b", hardware.peek())
        path = jobs_module.models_dir() / runner_module.ROOM_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps({"nights": ["2026-10-21", "2026-10-22"], "free_mib": 9_830,
                                     "need_mib": nine.memory_mib if nine else 15_616, "model": "qwen3.5-9b"}),
                        encoding="utf-8")
    elif action == "server":  # name: installed or missing
        state = "installed" if name == "installed" else "not_installed"
        found_files = server_files.ServerFiles(state, "cu130", bytes=747_386_477 if state == "installed" else 0)
        server_files.status = lambda root=None, **kwargs: found_files
    elif action == "version":  # name: starting, ready, missing, memory, unavailable (v1 in Row-Bot's server)
        _l2c_version({"starting": "making-ready"}.get(name, "ready"))
        folder = releases.version_dir(1)
        state = releases.read_state(folder)
        from row_bot.application.settings_commands import write_learning_settings

        write_learning_settings({"serve_runtime": "row_bot"})
        if name == "starting":
            state = _replace(state, runtime="row_bot")
        elif name == "ready":
            state = _replace(state, runtime="row_bot",
                             model_ref="model:custom_openai_row-bot-personal-row-bot:row-bot-personal-v1")
        else:
            failure = {"missing": "server_missing", "memory": "load_failed", "unavailable": "server_unavailable"}
            if name not in failure:
                raise HTTPException(404, "Unknown version state")
            state = _replace(state, phase=releases.RUNTIME_FAILED, runtime="row_bot", failure=failure[name],
                             model_ref=None)
        releases.write_state(folder, state)
    else:
        raise HTTPException(404, "Unknown Learning fixture action")
    return {"action": action}


# ── Learning (preview), L2a: training with the fake trainer, driven step by step ──

_l2a: dict[str, object] = {}


@app.post("/__l2a_fixture/learning/{action}")
def l2a_learning(action: str, steps: int = 0, x_fixture_token: str = Header(default="")) -> dict:
    """The personal model's training for the L2a spec: the fake trainer as the training engine,
    a planned run, steps taken on request, the person using Row-Bot, Row-Bot closing, running out
    of GPU memory. No GPU, no download, no provider."""
    predecessor._authorize(x_fixture_token)
    from row_bot.learning import runner
    from row_bot.learning.training import jobs as training_jobs
    from tests.fixtures.learning import FakeDataset, FakeEngine, FakeLease

    if action == "restore":
        runner.shutdown(10)
        runner._runner = None  # a fresh runner for the next spec
        runner._thread = None
        lease = sys.modules.get("row_bot.learning.lease")
        if lease is not None:
            lease.release_training()
        training_jobs.remove_all_runs()
        _l2a.clear()
        return {"action": action}
    supervisor = runner.get().supervisor
    if action == "engine":
        from datetime import datetime

        from row_bot.learning.runner import QuietHours

        lease = FakeLease(since_activity=3600.0)
        engine = FakeEngine()
        _l2a.update(lease=lease, engine=engine)
        runner.configure(engine=engine, dataset=FakeDataset(), lease=lease)
        # An afternoon, whatever the hour the spec runs: nothing starts by itself in quiet hours.
        runner.get().hours = QuietHours(now=lambda: datetime(2026, 10, 16, 14, 0))
        supervisor.jobs.plan(steps_total=1000, checkpoint_every=10)
        return {"action": action}
    lease = _l2a["lease"]
    engine = _l2a["engine"]
    job = supervisor.jobs.current()
    if action == "advance":
        target = job.steps_done + max(1, steps)
        supervisor.live_worker().advance(max(1, steps))
        supervisor.wait_for(lambda found: found.steps_done >= target or found.state not in ("preparing", "running"))
    elif action == "use":  # the person chats (on a cloud model): pause at the next step
        lease.set(chat_cloud=True, since_activity=0.0)
        supervisor.watch()
        supervisor.wait_for(lambda found: found.state == "pausing")
    elif action == "step":  # the step in progress ends: it saves and pauses
        supervisor.live_worker().advance(1)
        supervisor.wait_for(lambda found: found.state == "paused")
        supervisor.wait_idle(10)
    elif action == "free":  # the chat ended a while ago: Train now's run continues
        lease.set(since_activity=3600.0)
        runner.get().check()
        supervisor.wait_for(lambda found: found.state == "running")
    elif action == "closed":  # Row-Bot closed while it trained (it asks on the next start)
        supervisor.stop_now("app_closed")
    elif action == "oom":  # a run that doesn't fit the graphics card
        if job is not None and job.state not in ("failed", "evaluating"):
            supervisor.discard(job.id)
        engine.options["oom_at_step"] = 4
        planned = supervisor.jobs.plan(steps_total=1000, checkpoint_every=10)
        supervisor.train_now(planned.id)
        supervisor.wait_for(lambda found: found.state == "running")
        supervisor.live_worker().advance(5)
        supervisor.wait_for(lambda found: found.state == "failed")
        supervisor.wait_idle(10)
        engine.options.pop("oom_at_step", None)
    else:
        raise HTTPException(404, "Unknown Learning fixture action")
    return {"action": action}


# ── Learning (preview), L2c: the dataset's count, a version made ready, where it runs ──

_l2c: dict[str, object] = {}
# The 5070's own run (L2c's e2e): 154 examples, 39 steps of about 28 s, a 2.0 GB Q8_0 file.
_L2C_EXAMPLES, _L2C_STEPS, _L2C_SECONDS, _L2C_GGUF = 154, 39, 28.1, 2_012_012_128


class _FixturePlanner:
    """Stands in for ``training.planner.Planner``: the example count the spec sets."""

    def __init__(self) -> None:
        self.engine = None
        self.counts = (37, 100, 37, 4)

    def summary(self, *, compute: bool = True):
        from row_bot.learning.training.dataset import DatasetSummary

        examples, needed, new, left = self.counts
        return DatasetSummary(examples=examples, needed=needed, new_examples=new, left_out=left, at=0.0)

    def due(self) -> bool:
        return False

    def plan(self, *, force: bool = False, retrain: bool = False):
        from row_bot.learning.training.planner import PlanOutcome

        return PlanOutcome(reason="later", summary=self.summary())

    def recipe(self):
        from row_bot.learning.training import recipes

        return recipes.load("8gb")

    def forget(self) -> None:
        return None


class _FixtureReleases:
    """Stands in for ``training.releases.ReleaseMaker``: Try again and Runs in move the version to
    "making ready" there; nothing is exported, registered or checked."""

    def busy(self) -> bool:
        return False

    def start(self, job, *, quiet: bool = False, runtime: str | None = None) -> bool:
        from dataclasses import replace

        from row_bot import learning
        from row_bot.learning.training import releases

        folder = releases.version_dir(int(job.version or 1))
        state = releases.read_state(folder)
        if state is not None:
            releases.write_state(folder, replace(state, phase=releases.MAKING_READY, failure=None, command=None,
                                                 runtime=runtime or learning.read_settings().serve_runtime))
        return True

    def tick(self) -> bool:
        return False

    def shutdown(self) -> None:
        return None

    def wait(self, timeout: float = 30.0) -> bool:
        return True


def _l2c_version(phase: str) -> None:
    """Write the newest version's state (v1, from the finished run) as the spec names it."""

    from row_bot.application.settings_commands import write_learning_settings
    from row_bot.learning import runner
    from row_bot.learning.training import releases

    jobs = runner.get().supervisor.jobs
    job = jobs.current() or next(iter(jobs.all()), None)
    if job is None:
        raise HTTPException(409, "Train v1 first")
    folder = releases.version_dir(1)
    gguf = "row-bot-personal-v1-Q8_0.gguf"
    base = {"version": 1, "job_id": job.id, "examples": _L2C_EXAMPLES, "adapter_bytes": 43_676_816,
            "trained_at": 1_792_180_800.0, "quantization": "Q8_0"}
    runtime = "ollama"
    if phase == "exporting":
        state = releases.VersionState(phase=releases.EXPORTING, step="convert", **base)
    elif phase == "export-failed":
        state = releases.VersionState(phase=releases.EXPORT_FAILED, failure="not_enough_disk",
                                      needs_bytes=9_420_000_000, free_bytes=6_030_000_000, **base)
    elif phase == "making-ready":
        state = releases.VersionState(phase=releases.MAKING_READY, gguf=gguf, gguf_bytes=_L2C_GGUF, **base)
    elif phase == "ready":
        state = releases.VersionState(phase=releases.READY, gguf=gguf, gguf_bytes=_L2C_GGUF,
                                      model_ref="model:ollama:row-bot-personal-v1:fixture0", **base)
    elif phase == "runtime-failed":
        runtime = "lmstudio"
        state = releases.VersionState(phase=releases.RUNTIME_FAILED, failure="probe_failed", runtime=runtime,
                                      gguf=gguf, gguf_bytes=_L2C_GGUF, **base)
    elif phase == "waiting":
        runtime = "llama_cpp"
        command = (f'llama-server -m "{(folder / gguf).absolute()}" -a row-bot-personal-v1 --host 127.0.0.1 '
                   "--port 8181 -c 32768 -ngl 99 --jinja --offline --no-webui --reasoning off")
        state = releases.VersionState(phase=releases.WAITING, failure="not_running", runtime=runtime,
                                      command=command, gguf=gguf, gguf_bytes=_L2C_GGUF, **base)
    else:
        raise HTTPException(404, "Unknown version state")
    write_learning_settings({"serve_runtime": runtime})
    releases.write_state(folder, state)
    if phase == "ready" and job.state == "evaluating":
        jobs.move(job.id, "ready", reason="made_ready")


@app.post("/__l2c_fixture/learning/{action}")
def l2c_learning(action: str, name: str = "", x_fixture_token: str = Header(default="")) -> dict:
    """The personal model's dataset, training and versions for the L2c spec: the example count, a
    run with the 5070's numbers (the fake trainer), a version at each phase, Runs in, a graphics
    card another app holds, and the Docker image. No GPU, no download, no runtime, no provider."""
    predecessor._authorize(x_fixture_token)
    from row_bot.learning import runner
    from row_bot.learning.training import downloads, hardware, releases
    from tests.fixtures.learning import FakeDataset, FakeEngine, FakeLease

    if action == "restore":
        import shutil

        runner.shutdown(10)
        runner._runner = None
        runner._thread = None
        lease = sys.modules.get("row_bot.learning.lease")
        if lease is not None:
            lease.release_training()
        from row_bot.learning.training import jobs as training_jobs

        training_jobs.remove_all_runs()
        shutil.rmtree(releases.versions_dir(), ignore_errors=True)
        if "executable" in _l2c:
            from row_bot.learning.training import serving

            serving.OllamaRuntime.executable = _l2c["executable"]
        _l2c.clear()
        return {"action": action}
    if action == "container":
        found = hardware.HardwareCheck("container")
        hardware.check = lambda *, refresh=False, detector=None: found
        return {"action": action}
    if action == "setup":
        from datetime import datetime

        from row_bot.learning.runner import QuietHours

        current = downloads.loaded()
        if not isinstance(current, _FixtureDownloads):
            raise HTTPException(409, "Turn on the preview first")
        current.set("installed")
        planner, makes, engine = _FixturePlanner(), _FixtureReleases(), FakeEngine(seconds_per_step=_L2C_SECONDS)
        lease = FakeLease(since_activity=3600.0)
        runner.configure(engine=engine, dataset=FakeDataset(examples=_L2C_EXAMPLES), lease=lease, planner=planner,
                         releases=makes, comparisons=_FixtureComparisons())
        runner.get().hours = QuietHours(now=lambda: datetime(2026, 10, 16, 14, 0))
        # Ollama reads as installed whatever the machine running the spec has (CI has none).
        from row_bot.learning.training import serving

        _l2c.update(planner=planner, engine=engine, lease=lease, executable=serving.OllamaRuntime.executable)
        serving.OllamaRuntime.executable = lambda self: "ollama"
        return {"action": action}
    planner = _l2c["planner"]
    supervisor = runner.get().supervisor
    if action == "examples":  # name: "37" (needs more) or "154" (enough)
        count = int(name or 37)
        planner.counts = (count, 100, count if count < 100 else 0, 4 if count < 100 else 11)
    elif action == "train":  # v1 planned from 154 examples, started, at step 17 of 39
        job = supervisor.jobs.plan(steps_total=_L2C_STEPS, checkpoint_every=10,
                                   dataset={"id": "ds_" + "e" * 32, "examples": _L2C_EXAMPLES})
        planner.counts = (_L2C_EXAMPLES, 100, _L2C_EXAMPLES, 11)
        supervisor.train_now(job.id)
        supervisor.wait_for(lambda found: found.state == "running")
        supervisor.live_worker().advance(17)
        supervisor.wait_for(lambda found: found.steps_done >= 17)
    elif action == "finish":
        job = supervisor.jobs.current()
        supervisor.live_worker().advance(_L2C_STEPS - job.steps_done)
        supervisor.wait_for(lambda found: found.state == "evaluating")
        supervisor.wait_idle(10)
        planner.counts = (_L2C_EXAMPLES, 100, 0, 11)
    elif action == "version":
        _l2c_version(name)
    elif action in ("gpu-busy", "memory-busy"):
        # A local model still loaded in Ollama holds the card, or other apps hold the computer's memory
        # (O105): Train now waits.
        if action == "gpu-busy":
            supervisor.gpu_free = lambda: False
        else:
            supervisor.memory_free = lambda: False
        planner.counts = (_L2C_EXAMPLES, 100, _L2C_EXAMPLES, 11)
        job = supervisor.jobs.plan(steps_total=_L2C_STEPS, checkpoint_every=10,
                                   dataset={"id": "ds_" + "e" * 32, "examples": _L2C_EXAMPLES})
        supervisor.train_now(job.id)
    else:
        raise HTTPException(404, "Unknown Learning fixture action")
    return {"action": action}


# ── Learning (preview), L2c2: compare, trial and use the personal model ──

_L2C2_CHAT = "l2c2-travel-policy"
_L2C2_REF = "model:ollama:row-bot-personal-v1:fixture0"
_l2c2: dict[str, object] = {}


class _FixtureComparisons:
    """Stands in for ``model_compare.Comparisons``: never runs a model. Compare now starts a
    comparison that stays at its first task until the spec moves it (``progress``) or finishes it
    (``compared``); Stop stops it."""

    def __init__(self) -> None:
        self.version: int | None = None

    def running(self) -> bool:
        return self.version is not None

    def tick(self) -> bool:
        return False

    def _progress(self, version: int, **values) -> None:
        import time

        from row_bot.learning import model_compare

        current = model_compare.read_progress(version) or {}
        model_compare._write_json(model_compare.progress_path(version), {  # noqa: SLF001
            **current, **values, "updated_at": time.time()})

    def start(self, version: int) -> bool:
        self.version = int(version)
        self._progress(self.version, state="running", phase="visible", done=0, total=96)
        return True

    def stop(self, timeout: float = 15.0) -> None:
        if self.version is not None:
            self._progress(self.version, state="stopped", reason="stopped")
        self.version = None

    def wait(self, timeout: float = 30.0) -> bool:
        return True


def _l2c2_files() -> None:
    """v1's files and records as a real release leaves them (the GGUF a sparse file of its real size):
    what Experiment details, Export… and Retrain without it read."""

    import time

    from row_bot.learning.store import LearningStore
    from row_bot.learning.training import releases
    from tests.fixtures.learning import release

    folder = releases.version_dir(1)
    state = releases.read_state(folder)
    if state is None or state.phase != releases.READY:
        raise HTTPException(409, "Make v1 ready first")
    (folder / "adapter").mkdir(parents=True, exist_ok=True)
    (folder / "adapter" / "adapter_model.safetensors").write_bytes(b"\0" * 43_676)
    (folder / "adapter" / "adapter_config.json").write_text('{"r": 16}', encoding="utf-8")
    with open(folder / str(state.gguf), "wb") as handle:
        handle.truncate(_L2C_GGUF)
    (folder / releases.MODEL_CARD_FILE).write_text("# Personal model v1\n", encoding="utf-8")
    now = time.time()
    (folder / releases.MANIFEST_FILE).write_text(json.dumps({"identity": {
        "base": {"name": "Qwen3.5 2B", "version": "a1b2c3d4e5f6a7b8"},
        "recipe": {"name": "8gb-v1"}, "runtime": {"name": "Ollama", "version": "0.40.4"}}}), encoding="utf-8")
    (folder / releases.DATASET_FILE).write_text(json.dumps({
        "examples": _L2C_EXAMPLES, "validation": 17, "conversations": [f"chat-{i:02d}" for i in range(41)],
        "first_at": now - 15 * 86400, "last_at": now - 3600, "providers": ["openai"],
        "left_out": {"too_long": 3, "held_out": 11}, "terms_note": True}), encoding="utf-8")
    store = LearningStore()
    store.create()
    if store.get("release", "rel_l2c2") is None:
        store.put(release("rel_l2c2", version=1, source_thread_ids=("l2c2-deleted-chat",)))
    releases.write_state(folder, replace(state, release_id="rel_l2c2"))


def _l2c2_main() -> str:
    """The main model's name, as Learning shows it (the profile's default model)."""

    from row_bot.application.client_learning import _default_model_name

    return _default_model_name() or "your main model"


def _l2c2_comparison(verdict: str) -> dict:
    """A finished comparison of v1 with the starting model and the main model."""

    import time

    main = _l2c2_main()

    from row_bot.learning import model_compare

    passed = {"document_lookups": {"base": 6, "v1": 10, "main": 11},
              "structured_transforms": {"base": 7, "v1": 7, "main": 9},
              "tool_workflows": {"base": 3, "v1": 4, "main": 7},
              "own_work": {"base": 4, "v1": 5, "main": 8}}
    totals = {"document_lookups": 12, "structured_transforms": 10, "tool_workflows": 8, "own_work": 10}
    labels = {"document_lookups": "Document lookups", "structured_transforms": "Structured transforms",
              "tool_workflows": "Tool workflows", "own_work": "Your own work"}
    if verdict == "no_difference":
        passed["document_lookups"]["v1"] = 7
    rows = []
    for kind, total in totals.items():
        counted = {key: total for key in ("base", "v1", "main")}
        if kind == "structured_transforms":
            counted["v1"] = 9  # one task the personal model's runtime failed: left out of its count (O19)
        better = verdict == "better" and kind == "document_lookups"
        rows.append({"kind": kind, "label": labels[kind], "total": total, "passed": passed[kind],
                     "counted": counted, "verdict": "better" if better else "same"})
    overall = ({"verdict": "better", "recommend": "document_lookups",
                "line": "Better at document lookups than the starting model: 10 of 12 passed, against 6."}
               if verdict == "better" else
               {"verdict": "no_difference", "recommend": None,
                "line": "No clear difference from the starting model. Not used."})
    table = {key: {labels[kind]: [passed[kind][key], totals[kind]] for kind in totals if kind != "own_work"}
             for key in ("base", "v1", "main")}
    return {
        "format": model_compare.FORMAT, "schema_version": 1, "version": 1, "release_id": "rel_l2c2",
        "compared_at": time.time() - 600, "seconds": 2950.0,
        "arms": [
            {"key": "base", "label": "the starting model", "role": "baseline", "ran": 40, "cached": 0,
             "seconds_per_task": 6.2, "cloud_tokens_per_task": 0},
            {"key": "v1", "label": "v1 (new)", "role": "new", "ran": 40, "cached": 0, "seconds_per_task": 4.8,
             "cloud_tokens_per_task": 0},
            {"key": "main", "label": main, "role": "main", "ran": 40, "cached": 40,
             "seconds_per_task": 21.5, "cloud_tokens_per_task": 18_400},
        ],
        "rows": rows, "overall": overall,
        "note": f"Testing used 590K tokens (and 1.1M on this computer); {main}\u2019s results were reused.",
        "stopped": "", "left_out": [model_compare.CODING_NOTE], "baseline_unavailable": None,
        "main_unavailable": None,
        "details": {"visible": table, "hidden": {key: {"Your own work": [passed["own_work"][key], 10]}
                                                 for key in ("base", "v1", "main")}},
        "runs": {}, "budget": {"task_tokens": 150_000}, "visible_per_pack": 16, "hidden": True,
        "worker_notes": [], "write_backs": [], "metric_version": 1, "reviewed_at": None,
    }


def _l2c2_chat() -> None:
    """A chat where v1 answered one turn and got stuck on the next, which the main model finished."""

    from row_bot import threads
    from row_bot.learning import routing

    if not threads._thread_exists(_L2C2_CHAT):  # noqa: SLF001
        threads.create_thread("Travel policy", thread_id=_L2C2_CHAT, seed_default_skills=False)
    note = f"Your personal model got stuck; {_l2c2_main()} finished this."
    personal = {"model": "row-bot-personal-v1:fixture0"}
    threads.append_checkpoint_messages(_L2C2_CHAT, [
        HumanMessage(id="l2c2-ask-1", content="What is the client dinner limit in the travel policy?"),
        AIMessage(id="l2c2-personal", content="The client dinner limit is 85 EUR a person (TRV-014, section 3).",
                  response_metadata=personal),
        HumanMessage(id="l2c2-ask-2", content="And for a dinner with 12 people?"),
        AIMessage(id="l2c2-stuck", content="", response_metadata=personal, tool_calls=[
            {"id": "l2c2-call-1", "name": "calculate", "args": {"expression": "85 * 12"}}]),
        ToolMessage(tool_call_id="l2c2-call-1", content="The personal model stopped here."),
        HumanMessage(id="l2c2-handover", content=routing._HANDOVER.format(  # noqa: SLF001
            why="it repeated the same step", request=routing._quoted("And for a dinner with 12 people?")),  # noqa: SLF001
                     additional_kwargs={"row_bot_ui": {"thread_event": True, "source_event_id": "",
                                                       "learning_fallback": {
                                                           "text": note, "version": 1, "kind": "document_lookups",
                                                           "reason": "tool_loop"}}}),
        AIMessage(id="l2c2-main", content="For 12 people the limit is 1,020 EUR (85 EUR \u00d7 12).",
                  response_metadata={"model_name": "gpt-6.1-sol"}),
    ])


@app.post("/__l2c2_fixture/learning/{action}")
def l2c2_learning(action: str, name: str = "", x_fixture_token: str = Header(default="")) -> dict:
    """The personal model compared, on trial, kept, in a chat and in the picker for the L2c2 spec. Uses
    L2c's fixture (setup, train, finish, version ready) first. No model, runtime or provider runs."""

    predecessor._authorize(x_fixture_token)
    import time

    from row_bot import learning
    from row_bot.learning import model_compare, runner, use, versions

    if action == "restore":
        import shutil

        models = learning.data_dir() / learning.MODELS_FOLDER
        for name_ in (learning.USE_FILE, "needs_you.json"):
            (models / name_).unlink(missing_ok=True)
        (learning.data_dir() / "learning_advanced.json").unlink(missing_ok=True)
        if "suggested" in _l2c2:
            versions.suggested_folder = _l2c2["suggested"]
        try:
            from row_bot.thread_cleanup import delete_thread

            delete_thread(_L2C2_CHAT)
        except Exception:
            logging.getLogger(__name__).debug("L2c2 fixture chat already gone", exc_info=True)
        shutil.rmtree(models / "base", ignore_errors=True)
        _l2c2.clear()
        return {"action": action}
    if "suggested" not in _l2c2:
        # Export… offers a folder that names no real person's home.
        _l2c2["suggested"] = versions.suggested_folder
        versions.suggested_folder = lambda: str(Path("C:/Users/you/Documents/Row-Bot personal models")) \
            if os.name == "nt" else "/home/you/Documents/Row-Bot personal models"
    if action == "files":
        _l2c2_files()
    elif action == "progress":  # name: tasks done of 96
        comparisons = runner.get().comparisons
        if not isinstance(comparisons, _FixtureComparisons) or comparisons.version is None:
            raise HTTPException(409, "Compare now first")
        comparisons._progress(comparisons.version, done=int(name or 0))  # noqa: SLF001
    elif action == "compared":  # name: better | no_difference
        comparisons = runner.get().comparisons
        if isinstance(comparisons, _FixtureComparisons):
            comparisons.version = None
        model_compare._write_json(model_compare.comparison_path(1), _l2c2_comparison(name or "better"))  # noqa: SLF001
        model_compare.progress_path(1).unlink(missing_ok=True)
    elif action == "keep-due":  # v1 on trial for four days, with 18 requests (one finished by the main model)
        kind = name or "document_lookups"
        use.start_trial(1, kind, _L2C2_REF, clock=lambda: time.time() - 4 * 86400)
        for outcome in ["answered"] * 17 + ["fallback"]:
            use.record(kind, 1, outcome, 4.2)
    elif action == "retrain":  # a chat v1 was trained on is deleted
        from row_bot.learning.store import LearningStore

        LearningStore().purge_thread("l2c2-deleted-chat")
    elif action == "chat":
        _l2c2_chat()
    elif action == "picker":  # v1 in the composer's picker, beside the synthetic local models (s8's seed first)
        from row_bot.providers import model_catalog_cache as cache
        from row_bot.providers.selection import add_quick_choice_for_model

        snapshot = cache.read_model_catalog_cache()
        tag = _L2C2_REF.split(":", 2)[2]
        rows = [row for row in snapshot.ollama_rows if row.get("model_id") != tag]
        rows.append({"provider_id": "ollama", "model_id": tag, "display_name": tag, "installed": True,
                     "capabilities_snapshot": _S8_CHAT})
        cache.write_model_catalog_cache(cache.CatalogCacheSnapshot(
            1, snapshot.generated_at + 1, snapshot.cloud_cache, rows,
            {"ollama": {"status": "ok", "count": len(rows)}}, (), "synthetic-browser"))
        add_quick_choice_for_model(tag, provider_id="ollama", display_name=tag, capabilities_snapshot=_S8_CHAT,
                                   surface="chat")
    else:
        raise HTTPException(404, "Unknown Learning fixture action")
    return {"action": action}


# ── S11: helpers ────────────────────────────────────────────────────────────
# A chat's helpers as the runner records them: each with the model and reasoning
# it started on (B386), and one waiting on an approval that no notice put in the
# chat's transcript (B348). Nothing runs: the runs and the approval are records.
_S11_RUNS: list[str] = []


@app.post("/__s11_fixture/helpers")
def s11_helpers(payload: dict, x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    from row_bot import agent_runs, tasks
    from row_bot.approval_messages import normalize_interrupts
    from row_bot.threads import create_thread

    parent = str(payload.get("conversation_id") or "")
    model_ref = str(_reasoning_fixture.get("model") or "")
    if not parent or not model_ref:
        raise HTTPException(status_code=409)
    model_id = model_ref.split(":", 2)[-1]
    from row_bot.threads import append_checkpoint_messages

    append_checkpoint_messages(parent, [
        HumanMessage(id=fixture_id("s11-ask:" + parent), content="Set up the shop's catalogue in Stripe and file "
                     "the month-end notes in Notion."),
        AIMessage(id=fixture_id("s11-reply:" + parent), content="I've asked three helpers: one checks the dates, "
                  "one finds the right Notion page, and one adds the products to Stripe."),
    ])
    helpers = [
        ("Date review", "running", "High", "", {"tool": "developer_read_file", "detail": "src/dates.py"}),
        ("Notion destination lookup", "completed", "Provider default", "XHigh", None),
        ("Catalogue helper", "waiting_approval", "Low", "", None),
    ]
    made = {}
    for name, status, reasoning, not_offered, activity in helpers:
        child = create_thread(name, thread_type="agent_child", seed_default_skills=False)
        run = agent_runs.create_agent_run(parent_thread_id=parent, thread_id=child, display_name=name,
                                          status=status, model_override=model_ref, prompt=name,
                                          summary="Found the Finance › Month-end page." if status == "completed" else "")
        agent_runs.append_agent_event(run["id"], "reasoning.chosen", {
            "model_ref": model_ref, "model": model_id, "reasoning": reasoning, "not_offered": not_offered})
        if activity:
            agent_runs.record_agent_run_activity(run["id"], activity["tool"], activity["detail"])
        _S11_RUNS.append(run["id"])
        made[name] = {"run_id": run["id"], "conversation_id": child}
    waiting = made["Catalogue helper"]
    interrupts = [{"id": "s11-interrupt", "tool": "mcp_stripe_create_product", "label": "Create a product in Stripe",
                   "approval_reason": "Add the blue mug to the catalogue.", "args": {"name": "Blue mug"}}]
    approval_payload = normalize_interrupts(interrupts, source_label="Catalogue helper",
                                            agent_run_id=waiting["run_id"], parent_thread_id=parent)
    _token, approval_id = tasks.create_approval_request(
        waiting["run_id"], "", "agent_interrupt", "Catalogue helper needs approval: Create a product in Stripe",
        agent_run_id=waiting["run_id"], resume_kind="agent_run", source_label="Catalogue helper",
        source_thread_id=waiting["conversation_id"], parent_thread_id=parent, approval_payload_json=approval_payload)
    return {"helpers": made, "approval_id": approval_id}


@app.post("/__s11_fixture/reset")
def s11_reset(x_fixture_token: str = Header(default="")) -> dict:
    """Withdraw the helpers' waiting approval, so later journeys find nothing waiting."""
    predecessor._authorize(x_fixture_token)
    from row_bot import tasks

    while _S11_RUNS:
        tasks.cancel_agent_run_approvals(_S11_RUNS.pop())
    return {"ok": True}


# N9: a workflow with its own code folder, a schedule zone, "Run once when Row-Bot starts" and "Always ask me",
# with the runs that show each state. Records only: the workflow is switched off and nothing is scheduled or run.
_N9_TASK = "Landing checks"
_N9_CARD = {
    "action": "Post on X", "destination": "@rowbot", "account": "X account",
    "text": "Landing checks passed for row-bot.ai at 09:00.\n\nAll 12 pages load in under a second.",
    "media": [{"name": "landing-checks.png", "size": 184320, "fingerprint": "3f9a1c07be52", "kind": "image"}],
}


@app.post("/__n9_fixture/workflows")
def n9_workflows(x_fixture_token: str = Header(default="")) -> dict:
    """Seed the N9 workflow, its code folders, a set-up app for "Always ask me" and four runs."""
    from datetime import datetime, timedelta

    p4_provider_credentials(x_fixture_token)
    from row_bot import api_keys, tasks
    from row_bot.developer import storage
    from row_bot.developer.state import DeveloperWorkspace
    from row_bot.integrations import builtin

    api_keys.set_key("TAVILY_API_KEY", "synthetic-n9-key")
    api_keys.set_key("WOLFRAM_ALPHA_APPID", "synthetic-n9-key")
    builtin.changed()
    root = predecessor.DATA / "n9-folders"
    for ident, name in (("dev_n9_landing", "row-bot-landing"), ("dev_n9_docs", "docs-site")):
        (root / name).mkdir(parents=True, exist_ok=True)
        if storage.get_workspace(ident) is None:
            storage.save_workspace(DeveloperWorkspace(id=ident, name=name, path=str(root / name)))
    for task in tasks.list_tasks():
        if task["name"] == _N9_TASK:
            tasks.delete_task(task["id"])
    task_id = tasks.create_task(
        _N9_TASK, description="Checks the landing page every morning and posts the result.", icon="🛬",
        prompts=["Run the landing page checks and post a short summary on X."],
        schedule="daily:09:00", timezone="Europe/London", safety_mode="auto", enabled=False,
        workspace_id="dev_n9_landing", workspace_name="row-bot-landing", missed_runs="run_once",
        always_ask=["builtin:tool:web_search"], apply_default_skills=False,
    )
    now = datetime.now()
    # Every run is a turn of the workflow's one conversation (F31).
    thread_id = tasks._prepare_task_thread(tasks.get_task(task_id))
    runs = [  # Oldest first: the newest, waiting on its card, is the one the run history opens.
        ("n9-late", "completed", now - timedelta(days=3, hours=2), "2026-10-19T09:00:00+01:00",
         "Posted on X as @rowbot: https://x.com/rowbot/status/1849000000000000001"),
        ("n9-folder-gone", "blocked", now - timedelta(days=2),
         "", "row-bot-landing isn't a code folder any more. Choose another in this workflow's settings."),
        ("n9-replaced", "expired", now - timedelta(days=1), "", "A newer run replaced it before it was approved."),
        ("n9-asking", "waiting", now - timedelta(minutes=12), "", "It needs your approval to go on."),
    ]
    actions = {"n9-late": [{"tool": "x_post", "title": "Post on X", "result": "Posted on X as @rowbot",
                            "link": "https://x.com/rowbot/status/1849000000000000001"}]}
    conn = tasks._get_conn()
    try:
        for run_id, status, started, late_for, notice in runs:
            finished = None if status == "waiting" else (started + timedelta(minutes=3)).isoformat()
            conn.execute(
                "INSERT INTO task_runs (id,task_id,thread_id,started_at,finished_at,status,status_message,steps_total,"
                "steps_done,task_name,task_icon,workspace_id,workspace_name,late_for,notice,fix,actions_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, task_id, thread_id, started.isoformat(), finished, status, "", 1,
                 0 if status == "waiting" else 1, _N9_TASK, "🛬", "" if status == "blocked" else "dev_n9_landing",
                 "" if status == "blocked" else "row-bot-landing", late_for, notice,
                 "choose_folder" if status == "blocked" else "", json.dumps(actions.get(run_id, []))),
            )
        # The waiting run's card: its conversation's own X card, which only the person answers (N9, "Always ask me").
        post = {"__interrupt_id": "n9-interrupt", "tool": "x_post", "label": "Post to X as @rowbot",
                "question": "Post to X as @rowbot?", "description": "Post to X as @rowbot", "person_only": True,
                "review": {"decision": "ask", "reason": "You asked to approve this yourself."},
                "args": {"account": "@rowbot", "action": "post", "text": _N9_CARD["text"]},
                "x_post": {"account_id": "2047775631836844032", "username": "rowbot", "name": "Row-Bot",
                           "action": "post", "text": _N9_CARD["text"], "target_id": "", "target_url": "",
                           "media": [{"name": "landing-checks.png", "size": 184320, "sha256": "3f9a1c07be52",
                                      "kind": "image"}]}}
        conn.execute(
            "INSERT INTO approval_requests (id,run_id,task_id,step_id,resume_token,message,status,requested_at,"
            "resume_kind,source_thread_id,parent_thread_id,approval_payload_json) "
            "VALUES (?,?,?,?,?,?,'pending',?,'conversation',?,?,?)",
            ("n9-approval", "n9-asking-pass", "", "conversation", "n9-token", "Post to X as @rowbot?",
             (now - timedelta(minutes=11)).isoformat(), thread_id, thread_id,
             json.dumps({"interrupt_ids": ["n9-interrupt"], "interrupt": [post], "pass_id": "n9-asking-pass",
                         "model_selection": {"provider_id": "", "model_ref": ""}})),
        )
        conn.commit()
    finally:
        conn.close()
    return {"task_id": task_id}


@app.post("/__n9_fixture/reset")
def n9_reset(x_fixture_token: str = Header(default="")) -> dict:
    """Remove the N9 workflow and its runs' waiting card, so later journeys find nothing waiting."""
    predecessor._authorize(x_fixture_token)
    from row_bot import tasks

    for task in tasks.list_tasks():
        if task["name"] == _N9_TASK:
            tasks.delete_task(task["id"])
    conn = tasks._get_conn()
    try:
        conn.execute("DELETE FROM approval_requests WHERE id = 'n9-approval'")
        conn.execute("DELETE FROM task_runs WHERE id LIKE 'n9-%'")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.post("/__n4_fixture/helper")
def n4_helper(payload: dict, x_fixture_token: str = Header(default="")) -> dict:
    """A helper this chat started, waiting on its question (a record: nothing runs)."""
    predecessor._authorize(x_fixture_token)
    from row_bot import agent_runs, tasks
    from row_bot.approval_messages import normalize_interrupts
    from row_bot.questions import card, normalize
    from row_bot.threads import create_thread

    parent = str(payload.get("conversation_id") or "")
    if not parent:
        raise HTTPException(status_code=409)
    child = create_thread("Launch planner", thread_type="agent_child", seed_default_skills=False)
    run = agent_runs.create_agent_run(parent_thread_id=parent, thread_id=child, display_name="Launch planner",
                                      status="waiting_approval", prompt="Plan the launch")
    _S11_RUNS.append(run["id"])
    interrupts = [{"__interrupt_id": fixture_id("n4-helper:" + run["id"]), **card(normalize([_N4_BETA]))}]
    payload_json = normalize_interrupts(interrupts, source_label="Launch planner", agent_run_id=run["id"],
                                        parent_thread_id=parent)
    _token, approval_id = tasks.create_approval_request(
        run["id"], "", "agent_interrupt", "Launch planner has a question: When should the beta go out?",
        agent_run_id=run["id"], resume_kind="agent_run", source_label="Launch planner", source_thread_id=child,
        parent_thread_id=parent, approval_payload_json=payload_json)
    return {"approval_id": approval_id}


@app.post("/__n4_fixture/workflow")
def n4_workflow(x_fixture_token: str = Header(default="")) -> dict:
    """An unattended run waiting on Row-Bot's question, for Needs you: its turn asks in the workflow's
    conversation, as a scheduled run's would (F31)."""
    import time

    predecessor._authorize(x_fixture_token)
    from row_bot import tasks

    task_id = tasks.create_task("Weekly launch check", prompts=["n4 question fixture"], channels=[],
                                enabled=False, safety_mode="approve", apply_default_skills=False)
    thread_id = tasks._prepare_task_thread(tasks.get_task(task_id))
    tasks.run_task_background(task_id, thread_id, [])
    deadline = time.monotonic() + 10
    while (approval_id := tasks.pending_approval_id(thread_id)) is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if approval_id is None:
        raise HTTPException(status_code=500, detail="The run did not ask its question")
    _N4_APPROVALS.append(approval_id)
    _N4_WORKFLOWS.append(thread_id)
    return {"approval_id": approval_id, "task_id": task_id}


@app.post("/__n4_fixture/reset")
def n4_reset(x_fixture_token: str = Header(default="")) -> dict:
    """Withdraw the seeded questions, so later journeys find nothing waiting."""
    predecessor._authorize(x_fixture_token)
    from row_bot import tasks

    while _S11_RUNS:
        tasks.cancel_agent_run_approvals(_S11_RUNS.pop())
    while _N4_WORKFLOWS:  # A run still waiting on its question stops, and its card goes with it.
        tasks.stop_task(_N4_WORKFLOWS.pop())
    conn = tasks._get_conn()
    try:
        for approval_id in _N4_APPROVALS:
            conn.execute("UPDATE approval_requests SET status='cancelled' WHERE id=? AND status='pending'",
                         (approval_id,))
        conn.commit()
    finally:
        conn.close()
    _N4_APPROVALS.clear()
    return {"ok": True}


# ── N6: install stats ──
# Dormant here unless a spec turns it on, so no other spec ever meets it. On, it names a realistic
# service address and a fake send that only records what would have gone: nothing leaves this
# computer.
from row_bot import install_count as _install_count  # noqa: E402

_install_count.COLLECTOR_URL = ""
# The test matrix turns install stats off for every app it starts; the N6 specs turn it back on here.
_N6_ENV = os.environ.get(_install_count.ENV)
_N6_SERVICE = "https://row-bot-count.row-bot.workers.dev"
_n6_sent: list[dict] = []
_n6_real_post = _install_count._post


def _n6_post(url: str, body: dict, _scope) -> bool:
    _n6_sent.append({"path": url.removeprefix(_N6_SERVICE), "body": dict(body)})
    return True


# The Privacy screenshots show the sidebar: realistic conversation names while N6 runs.
_N6_NAMES = {"p1-browser-a": ("Phase 1 conversation A", "Plan the Lisbon offsite"),
             "p1-browser-b": ("Phase 1 conversation B", "Draft the Q4 board update")}


def _n6_name_conversations(realistic: bool) -> None:
    import sqlite3
    from row_bot import threads

    with sqlite3.connect(threads.DB_PATH) as conn:
        for thread_id, names in _N6_NAMES.items():
            conn.execute("UPDATE thread_meta SET name = ? WHERE thread_id = ?", (names[realistic], thread_id))


@app.post("/__n6_fixture/install-count/{state}")
def n6_install_count(state: str, x_fixture_token: str = Header(default="")) -> dict:
    """fresh: on by default, nothing sent yet; on/off: one count already sent, then the switch;
    restore: dormant again."""
    predecessor._authorize(x_fixture_token)
    (predecessor.DATA / _install_count.STATE_FILE).unlink(missing_ok=True)
    _n6_sent.clear()
    if state == "restore":
        _install_count.COLLECTOR_URL = ""
        _install_count._post = _n6_real_post
        if _N6_ENV is not None:
            os.environ[_install_count.ENV] = _N6_ENV
        _n6_name_conversations(False)
        return {"state": state}
    if state not in {"fresh", "on", "off"}:
        raise HTTPException(status_code=404, detail="Unknown install stats state")
    os.environ.pop(_install_count.ENV, None)
    _install_count.COLLECTOR_URL = _N6_SERVICE
    _install_count._post = _n6_post
    _n6_name_conversations(True)
    if state != "fresh":
        _install_count.tick()
        if state == "off":
            _install_count.choose(False)
    return {"state": state, "view": _install_count.view()}


@app.get("/__n6_fixture/install-count/sent")
def n6_install_count_sent(x_fixture_token: str = Header(default="")) -> dict:
    """What the fake service received, in order (path and body)."""
    predecessor._authorize(x_fixture_token)
    return {"sent": list(_n6_sent)}


# --- N10: Outlook through your own Microsoft app, and Microsoft 365's tenant (B398) ----------------------
# The Outlook account's states are seeded in an in-memory keychain (this run's own, restored by reset); a
# sign-in from another device gets a fixed code and never an answer, so its wait stays on screen. The
# approval cards come from the real Outlook card writer. Microsoft 365's Registry check answers with the
# record the catalog lists (or, for the error, as removed); nothing reaches Microsoft or the Registry.
_N10_CLIENT = "11111111-2222-3333-4444-555555555555"
_N10_ADDRESS = "you@yourdomain.com"
_N10_SAVED: dict = {}
_N10_REGISTRY = {"mode": "same"}
_N10_WORK_IQ = {"server": {
    "name": "com.microsoft/workiq-admintools", "title": "Admin MCP Server", "version": "1.0.0",
    "description": "MCP Server containing tools relating to Microsoft Admin Center",
    "remotes": [{"type": "streamable-http",
                 "url": "https://agent365.svc.cloud.microsoft/agents/tenants/{tenant_id}/servers/mcp_AdminTools",
                 "variables": {"tenant_id": {"description": "Microsoft Entra tenant ID", "isRequired": True}}}]},
    "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active"}}}


def _n10_keychain() -> None:
    """This run's keychain in memory, readable during capture; reset puts back what was there."""
    from row_bot import secret_store
    if "backend" not in _N10_SAVED:
        _N10_SAVED.update(backend=secret_store._backend_override, capture=secret_store._docs_capture_active)
    if not isinstance(getattr(secret_store._backend_override, "values", None), dict):
        class SyntheticSecrets:
            def __init__(self): self.values = {}
            def get_password(self, service, account): return self.values.get((service, account))
            def set_password(self, service, account, value): self.values[service, account] = value
            def delete_password(self, service, account): self.values.pop((service, account), None)
        secret_store._backend_override = SyntheticSecrets()
    secret_store._docs_capture_active = lambda: False


def _n10_route(device: bool) -> None:
    """How this browser signs in: here (the desktop app) or from another device, with a code."""
    from row_bot import microsoft_account
    from row_bot.application import client_account_oauth as owner
    if "route" not in _N10_SAVED:
        _N10_SAVED.update(route=owner.sign_in_route, start=microsoft_account.device_start,
                          poll=microsoft_account.device_poll)
    original = _N10_SAVED["route"]

    def route(account, device_info):
        if account == "microsoft" and device:
            return {"sign_in": "device", "sign_in_blocker": None, "callback_url": None}
        return original(account, device_info)
    owner.sign_in_route = route
    microsoft_account.device_start = lambda found, http: microsoft_account.DeviceCode(
        "fixture-device-code", "ABCD-EFGH", "https://microsoft.com/devicelogin", 900, 5)
    microsoft_account.device_poll = lambda found, code, http: "authorization_pending"


@app.post("/__n10_fixture/outlook/{state}")
def n10_outlook(state: str, route: str = "desktop", x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    from row_bot import account_tokens, microsoft_account
    from row_bot.integrations import builtin
    from row_bot.tools import registry
    if state not in {"none", "registered", "signed_in", "signed_out"} or route not in {"desktop", "device"}:
        raise HTTPException(status_code=404, detail="Unknown Outlook state")
    _n10_keychain()
    _n10_route(route == "device")
    for name in ("microsoft", "microsoft_client"):
        account_tokens.delete(name)
    if state != "none":
        microsoft_account.save_client(_N10_CLIENT)
    if state in {"signed_in", "signed_out"}:
        account_tokens.write("microsoft", {"refresh_token": "fixture-refresh", "client_id": _N10_CLIENT,
                                           "tenant": "common", "address": _N10_ADDRESS, "name": "You"})
        # As the last check left it: working, or turned down by Microsoft.
        account_tokens._CHECKS["microsoft"] = (account_tokens.digest("microsoft"), state == "signed_in")
        for tool in ("outlook_mail", "outlook_calendar"):
            registry.set_enabled(tool, True)
    builtin.changed()
    builtin.rows()  # Apps and Needs you read the new state at once.
    return {"state": microsoft_account.state()}


@app.post("/__n10_fixture/registry/{mode}")
def n10_registry(mode: str, x_fixture_token: str = Header(default="")) -> dict:
    """What the MCP Registry answers when Microsoft 365's record is checked again: the same record, or removed."""
    predecessor._authorize(x_fixture_token)
    from row_bot.mcp_client import marketplace
    if mode not in {"same", "removed"}:
        raise HTTPException(status_code=404, detail="Unknown Registry answer")
    if "fetch" not in _N10_SAVED:
        _N10_SAVED["fetch"] = marketplace._fetch_json
        original = marketplace._fetch_json

        def fetch(url, timeout=0):
            if "workiq-admintools" not in url:
                return original(url, timeout=timeout)
            record = json.loads(json.dumps(_N10_WORK_IQ))
            if _N10_REGISTRY["mode"] == "removed":
                record["_meta"]["io.modelcontextprotocol.registry/official"]["status"] = "deleted"
            return record
        marketplace._fetch_json = fetch
    if "index" not in _N10_SAVED:
        # The catalog has no Registry mirror here: one with only this record, removed again by reset.
        import tempfile
        from row_bot.integrations import facts, index
        from row_bot.mcp_client import registry_snapshot
        _N10_SAVED["index"] = (registry_snapshot.SHIPPED, index.pointer() is not None)
        if index.pointer() is None:
            shipped = Path(tempfile.mkdtemp(prefix="row-bot-n10-")) / "registry_snapshot.jsonl.xz"
            shipped.write_bytes(registry_snapshot.build_snapshot(
                marketplace.registry_entries({"servers": [_N10_WORK_IQ]}), captured_at=1790800000.0,
                watermark="2026-10-01T00:00:00Z"))
            registry_snapshot.SHIPPED = shipped
            index.ensure()
            facts.invalidate()
    _N10_REGISTRY["mode"] = mode
    return {"mode": mode}


@app.post("/__n10_fixture/reset")
def n10_reset(x_fixture_token: str = Header(default="")) -> dict:
    """Forget Outlook, any Microsoft 365 connection the spec saved, and put back what the fixture replaced."""
    predecessor._authorize(x_fixture_token)
    from row_bot import account_tokens, microsoft_account, secret_store
    from row_bot.application import client_account_oauth as owner
    from row_bot.integrations import builtin, facts
    from row_bot.mcp_client import config, marketplace
    from row_bot.tools import registry
    with owner._LOCK:
        for key in [key for key, job in owner._JOBS.items() if job["account"] == "microsoft"]:
            owner._JOBS[key]["stop"].set()
            owner._JOBS.pop(key)
        owner._ACTIVE.discard("microsoft")
    if "backend" in _N10_SAVED:
        for name in ("microsoft", "microsoft_client"):
            account_tokens.delete(name)
        account_tokens._CHECKS.pop("microsoft", None)
        for tool in ("outlook_mail", "outlook_calendar"):
            registry.set_enabled(tool, False)
        secret_store._backend_override = _N10_SAVED.pop("backend")
        secret_store._docs_capture_active = _N10_SAVED.pop("capture")
    if "route" in _N10_SAVED:
        owner.sign_in_route = _N10_SAVED.pop("route")
        microsoft_account.device_start = _N10_SAVED.pop("start")
        microsoft_account.device_poll = _N10_SAVED.pop("poll")
    if "fetch" in _N10_SAVED:
        marketplace._fetch_json = _N10_SAVED.pop("fetch")
    if "index" in _N10_SAVED:
        from row_bot.integrations import index
        from row_bot.mcp_client import registry_snapshot
        registry_snapshot.SHIPPED, had_index = _N10_SAVED.pop("index")
        if not had_index:
            (index.folder() / "registry.json").unlink(missing_ok=True)
            index._READY.clear()
    saved = config.load_config()
    servers = saved.get("servers") or {}
    for name in [name for name, cfg in servers.items()
                 if str((cfg.get("source") or {}).get("registry_name") or "").startswith("com.microsoft/workiq")]:
        servers.pop(name)
    config.save_config(saved)
    builtin.changed()
    facts.invalidate()
    return {"ok": True}


_N10_CARDS = {
    "mail": ("outlook_send_mail", "outlook_mail", {
        "to": ["amy@contoso.com"], "cc": ["ben@contoso.com"], "subject": "Q3 numbers",
        "body": "Hi Amy,\n\nThe Q3 numbers are attached. Revenue is up 12% on Q2, mostly from the new plans.\n\n"
                "Shall we go through them on Thursday?\n\nThanks", "attachments": []}),
    "event": ("outlook_create_event", "outlook_calendar", {
        "subject": "Q3 review", "start": "2026-10-29 14:00", "end": "2026-10-29 14:45",
        "timezone_name": "Europe/London", "location": "Room 4", "attendees": ["amy@contoso.com", "ben@contoso.com"],
        "online_meeting": True, "body": "Go through the Q3 numbers before the board pack."}),
}


def _n10_stream(text: str, config: dict):
    """A turn that asks to send an Outlook email or to invite people to an event: the real card's words."""
    import tempfile
    from row_bot.integrations import builtin
    from row_bot.threads import append_checkpoint_messages
    from row_bot.tools import outlook_tool

    name, parent, args = _N10_CARDS["event" if "event" in text else "mail"]
    args = json.loads(json.dumps(args))
    if name == "outlook_send_mail":
        report = Path(tempfile.mkdtemp(prefix="row-bot-n10-")) / "q3-report.pdf"
        report.write_bytes(b"%PDF-1.7 Q3 numbers\n" + b"0" * 245_000)
        args["attachments"] = [str(report)]
    builtin.remember_tools({name: parent})
    call = predecessor._record("submit", config, "n10-outlook")
    thread = call["conversation_id"]
    identity = f"n10:{call['generation_id']}"
    tool_id = fixture_id(identity + ":tool")
    try:
        append_checkpoint_messages(thread, [AIMessage(id=fixture_id(identity + ":tool-call"), content="",
            tool_calls=[{"id": tool_id, "name": name, "args": args}])])
        yield "tool_call", {"tool_call_id": tool_id, "name": name, "args": args}
        question, description = outlook_tool.approval_card(name, args)
        yield "interrupt", [{"__interrupt_id": fixture_id(identity + ":approval"), "tool": name,
                             "label": outlook_tool.TITLES[name], "question": question, "description": description,
                             "args": args}]
    finally:
        call["quiesced"] = True



# B407: a workflow run the scheduler starts in the background, with a step that
# waits for the person's approval, so a spec sees it reach the sidebar, Home and
# Monitor without a reload. Synthetic data only; nothing calls a model.
_B407_TASK = "Morning digest"
# The seeded conversations, titled as a person's own for screenshots, and back.
_B407_TITLES = {"p1-browser-a": ("Phase 1 conversation A", "Plan the Lisbon trip"),
                "p1-browser-b": ("Phase 1 conversation B", "Draft the Q3 board update")}


def _b407_titles(realistic: bool) -> None:
    import sqlite3
    from row_bot import threads
    from row_bot.application.live_updates import publish

    with sqlite3.connect(threads.DB_PATH) as conn:
        for thread_id, (seeded, realistic_title) in _B407_TITLES.items():
            conn.execute("UPDATE thread_meta SET name=? WHERE thread_id=?",
                         (realistic_title if realistic else seeded, thread_id))
    publish("conversations")


def _b407_clear() -> None:
    from row_bot import tasks
    from row_bot.thread_cleanup import delete_thread

    if not Path(tasks._DB_PATH).resolve().is_relative_to(predecessor.DATA.resolve()):
        raise HTTPException(status_code=403, detail="Synthetic data scope required")
    for task in tasks.list_tasks():
        if task["name"] != _B407_TASK:
            continue
        conversations = {run.get("thread_id") for run in tasks.get_run_history(task["id"])}
        tasks.delete_task(task["id"])
        for conversation in conversations - {None, ""}:
            delete_thread(conversation)


@app.post("/__b407_fixture/background-run")
def b407_background_run(x_fixture_token: str = Header(default="")) -> dict:
    """Start the workflow exactly as its schedule would, in the background."""
    predecessor._authorize(x_fixture_token)
    from row_bot import tasks

    _b407_clear()
    task_id = tasks.create_task(
        _B407_TASK, description="Gathers the morning's news and asks before it goes to the team.", icon="📰",
        prompts=["b407 digest fixture"], channels=[], enabled=False, safety_mode="approve",
        apply_default_skills=False,
    )
    task = tasks.get_task(task_id)
    thread_id = tasks._prepare_task_thread(task)
    tasks.run_task_background(task_id, thread_id, [])
    return {"task_id": task_id, "conversation_id": thread_id}


@app.post("/__b407_fixture/titles")
def b407_realistic_titles(x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    _b407_titles(True)
    return {"ok": True}


@app.post("/__b407_fixture/reset")
def b407_reset(x_fixture_token: str = Header(default="")) -> dict:
    predecessor._authorize(x_fixture_token)
    _b407_clear()
    _b407_titles(False)
    return {"ok": True}


if __name__ == "__main__":
    main()
