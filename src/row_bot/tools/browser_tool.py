"""Thin tool schemas and registry for Row-Bot's managed Python Playwright backend."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.browser import BrowserSession, BrowserSessionManager
from row_bot.browser.history import (
    append_browser_history as _append_history,
    clear_browser_history as _clear_history,
    get_browser_history as _get_history,
)
from row_bot.browser.history import HISTORY_PATH as _DEFAULT_HISTORY_PATH
from row_bot.browser import local_access as _local
from row_bot.browser.policy import (
    consequential_browser_target as _consequential_browser_target,
    history_url as _history_url,
    navigation_policy as _navigation_policy,
)
from row_bot.browser.service import BrowserWorkItem as _BrowserWorkItem
from row_bot.browser.service import browser_runs_headless as _browser_runs_headless
from row_bot.tools import registry
from row_bot.tools.base import BaseTool


DATA_DIR = _DEFAULT_HISTORY_PATH.parent
_HISTORY_PATH = _DEFAULT_HISTORY_PATH


def _build_snapshot_js(max_elements: int) -> str:
    """Compatibility source contract: values stay hidden and no DOM refs are written."""

    return f"""
() => ({{
  max_elements: {max(0, int(max_elements))},
  value_length: true,
  collector: 'exact ephemeral element handles; no mutable DOM reference attributes'
}})
"""


def get_browser_history(thread_id: str) -> list[dict]:
    return _get_history(thread_id, path=_HISTORY_PATH)


def append_browser_history(thread_id: str, entry: dict) -> None:
    _append_history(thread_id, entry, path=_HISTORY_PATH)


def clear_browser_history(thread_id: str) -> None:
    _clear_history(thread_id, path=_HISTORY_PATH)


def _get_thread_id() -> str:
    try:
        from row_bot.agent import _current_thread_id_var

        return _current_thread_id_var.get() or "default"
    except ImportError:
        return "default"


class _NavigateInput(BaseModel):
    url: str = Field(description="The HTTP(S) URL to open in Row-Bot's managed browser")


class _ClickInput(BaseModel):
    ref: str = Field(description="Opaque target token from the current managed-browser observation")


class _TypeInput(BaseModel):
    ref: str = Field(description="Opaque input token from the current managed-browser observation")
    text: str = Field(description="Text to enter; the value is never returned or persisted")
    submit: bool = Field(default=False, description="Press Enter after typing")


class _ScrollInput(BaseModel):
    direction: str = Field(default="down", description="Scroll direction: up or down")
    amount: int = Field(default=3, ge=1, le=20, description="Number of approximately 400-pixel steps")


class _TabInput(BaseModel):
    action: str = Field(default="list", description="Tab action: list, switch, new, or close")
    tab_id: Optional[int] = Field(default=None, description="Owned tab index for switch/close")
    url: Optional[str] = Field(default=None, description="Optional HTTP(S) URL for a new owned tab")


class _ScreenshotInput(BaseModel):
    full_page: bool = Field(default=False, description="Capture the whole scrollable page instead of what's on screen")


class _ViewportInput(BaseModel):
    preset: Literal["phone", "tablet", "desktop"] = Field(description="Page size: phone, tablet or desktop")
    device_scale: Optional[float] = Field(default=None, ge=1, le=3, description="Optional device pixel ratio, 1 to 3")


class _RecordInput(BaseModel):
    action: Literal["start", "stop"] = Field(description="start or stop recording this chat's page")
    max_minutes: int = Field(default=5, ge=1, le=15, description="For start: stops by itself after this many minutes")


_session_manager = BrowserSessionManager()


def get_session_manager() -> BrowserSessionManager:
    return _session_manager


def _normalise_url(url: str) -> tuple[str | None, str]:
    selected = str(url or "").strip()
    if selected.casefold().startswith("javascript:"):
        return None, "Error: javascript: URLs are not allowed."
    if not selected.casefold().startswith(("http://", "https://")):
        # A local development server is plain http unless the person says otherwise.
        bare = "http://" + selected
        selected = bare if _local.local_development_origin(bare) else "https://" + selected
    return selected, ""


def _local_address_refusal(session: BrowserSession, thread_id: str, url: str, tool: str) -> tuple[str | None, str]:
    """Whether a local or private-network address may open: (refusal, note).

    An address on this computer opens once allowed for this conversation: by
    the person through the approval gate (or the level: the reviewer under
    Auto, Hands-off), or without asking when a server Row-Bot started here
    listens on it. Other private-network addresses never open.
    """

    origin = _local.local_development_origin(url)
    if origin is None:
        unspecified = _local.unspecified_refusal(url)
        if unspecified:
            return unspecified, ""
        return (_local.PRIVATE_NETWORK_REFUSAL, "") if _local.private_network_target(url) else (None, "")
    if _local.is_row_bot_origin(origin):
        return _local.row_bot_refusal(origin), ""
    if session.local_address_grant(thread_id, origin) is not None:
        return None, ""
    label = _local.address_label(origin)
    if _local.row_bot_server(origin, thread_id):
        session.allow_local_address(thread_id, origin, source="row_bot_server")
        return None, f"Opened without asking: Row-Bot started the server at {label} in this chat."
    from row_bot.tools.approval_gate import approval_check

    session.mark_waiting_approval(thread_id, "Approve a local address")
    refusal, line = approval_check(
        _local.approval_request(origin, tool=tool),
        risks=(_local.LOCAL_ADDRESS_RISK,),
        blocked_message=(
            f"BLOCKED: Opening {label} needs approval, and this chat is on Block. "
            "Ask the person to allow it or change the approval level."
        ),
        cancelled_message=f"The person didn't allow {label}; it wasn't opened.",
    )
    if refusal:
        session.end_activity(thread_id)
        return refusal, ""
    session.allow_local_address(thread_id, origin, source=_local.source_of(line))
    return None, ""


def _show_image(image: bytes) -> bool:
    """Show a picture in the chat and save it to the conversation's files, as a generated image is."""

    import base64

    try:
        from row_bot.tools.image_gen_tool import _set_pending_image

        _set_pending_image(base64.b64encode(image).decode("ascii"))
        return True
    except Exception:
        return False


def _record_approval(minutes: int) -> dict:
    return {
        "tool": "browser_record",
        "label": "Record Row-Bot's browser",
        "question": "Record Row-Bot's browser in this chat?",
        "description": (
            f"Row-Bot will record what its browser shows in this chat, for up to {minutes} minutes, and save "
            "the video to this chat's files. Password and card fields are hidden. You can stop it at any time."
        ),
        "args": {"max_minutes": minutes},
    }


class BrowserTool(BaseTool):
    @property
    def name(self) -> str:
        return "browser"

    @property
    def display_name(self) -> str:
        return "🌐 Browser"

    @property
    def description(self) -> str:
        return (
            "Navigate websites and automate ordinary web pages in Row-Bot's owned browser profile using direct Python Playwright. "
            "Use Computer Use instead for an already-open browser, browser chrome, native dialogs, or OS windows."
        )

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def config_schema(self) -> dict[str, dict]:
        return {}

    @property
    def destructive_tool_names(self) -> set[str]:
        return set()

    @property
    def untrusted_tool_names(self) -> set[str]:
        return {
            "browser_navigate", "browser_click", "browser_type", "browser_scroll",
            "browser_snapshot", "browser_back", "browser_tab", "browser_screenshot",
        }

    def as_langchain_tools(self) -> list:
        def browser_navigate(url: str) -> str:
            """Open an ordinary website in Row-Bot's managed browser and return one bounded observation."""

            selected, error = _normalise_url(url)
            if selected is None:
                return error
            thread_id = _get_thread_id()
            session = _session_manager.get_session(thread_id)
            policy, reason = _navigation_policy(selected, session.current_url(thread_id))
            if policy == "block":
                return f"BLOCKED: {reason}"
            local = _local.local_development_origin(selected) is not None
            refusal, note = _local_address_refusal(session, thread_id, selected, "browser_navigate")
            if refusal:
                return refusal
            if policy == "ask" and not (local and reason.startswith("Navigation targets a local")):
                from row_bot.tools.approval_gate import gate_action

                session.mark_waiting_approval(thread_id, "Approve website navigation")
                blocked = gate_action({
                    "tool": "browser_navigate", "label": "Managed Browser navigation",
                    "action": "navigate", "origin_and_path": _history_url(selected), "reason": reason,
                })
                if blocked:
                    session.end_activity(thread_id)
                    return blocked
            result = session.navigate(selected, thread_id)
            append_browser_history(thread_id, {
                "action": "navigate", "url": _history_url(selected), "timestamp": datetime.now().isoformat(),
            })
            return f"{note}\n{result}" if note else result

        def browser_click(ref: str) -> str:
            """Click one exact opaque target from the current managed-browser observation."""

            thread_id = _get_thread_id()
            session = _session_manager.get_session(thread_id)
            metadata = session.pending_approval_proof(thread_id, "click", ref)
            replaying_approval = metadata is not None
            if metadata is None:
                metadata = session.describe_ref(ref, thread_id)
            if not metadata:
                return "ERROR [stale_observation]: Observe the same managed page again before retrying."
            consequence = _consequential_browser_target(metadata)
            if consequence:
                from row_bot.tools.approval_gate import gate_action

                if not replaying_approval:
                    session.stage_approval_proof(thread_id, "click", ref, metadata)
                session.mark_waiting_approval(thread_id, "Approve page action")
                blocked = gate_action({
                    "tool": "browser_click", "label": "Managed Browser consequential action",
                    "action": "click", "target": str(metadata.get("label") or "control")[:160],
                    "reason": consequence,
                })
                if blocked:
                    session.clear_approval_proof(thread_id)
                    session.end_activity(thread_id)
                    return blocked
                try:
                    result = session.click_after_approval(metadata, thread_id)
                finally:
                    session.clear_approval_proof(thread_id)
            else:
                result = session.click(ref, thread_id)
            append_browser_history(thread_id, {
                "action": "click", "target_token_revision": str(ref).split("_", 1)[0],
                "timestamp": datetime.now().isoformat(),
            })
            return result

        def browser_type(ref: str, text: str, submit: bool = False) -> str:
            """Enter hidden text into one exact opaque input; optionally submit it."""

            thread_id = _get_thread_id()
            session = _session_manager.get_session(thread_id)
            metadata = session.pending_approval_proof(thread_id, "type", ref)
            replaying_approval = metadata is not None
            if metadata is None:
                metadata = session.describe_ref(ref, thread_id)
            if not metadata:
                return "ERROR [stale_observation]: Observe the same managed page again before retrying."
            consequence = _consequential_browser_target(metadata, submit=submit)
            if consequence:
                from row_bot.tools.approval_gate import gate_action

                if not replaying_approval:
                    session.stage_approval_proof(thread_id, "type", ref, metadata)
                session.mark_waiting_approval(thread_id, "Approve form action")
                blocked = gate_action({
                    "tool": "browser_type", "label": "Managed Browser form action",
                    "action": "type_and_submit" if submit else "type",
                    "target": str(metadata.get("label") or "field")[:160], "reason": consequence,
                    "data_summary": f"Text entry ({len(text)} characters; value hidden)",
                })
                if blocked:
                    session.clear_approval_proof(thread_id)
                    session.end_activity(thread_id)
                    return blocked
                try:
                    result = session.type_text_after_approval(
                        metadata,
                        text,
                        submit,
                        thread_id,
                    )
                finally:
                    session.clear_approval_proof(thread_id)
            else:
                result = session.type_text(ref, text, submit, thread_id)
            append_browser_history(thread_id, {
                "action": "type", "target_token_revision": str(ref).split("_", 1)[0],
                "text_length": len(text), "submit": submit, "timestamp": datetime.now().isoformat(),
            })
            return result

        def browser_scroll(direction: str = "down", amount: int = 3) -> str:
            """Scroll the current owned page and return one fresh semantic observation."""

            thread_id = _get_thread_id()
            result = _session_manager.get_session(thread_id).scroll(direction, amount, thread_id)
            append_browser_history(thread_id, {
                "action": "scroll", "direction": direction, "amount": amount,
                "timestamp": datetime.now().isoformat(),
            })
            return result

        def browser_snapshot() -> str:
            """Replace the current ephemeral observation and return new opaque target tokens."""

            thread_id = _get_thread_id()
            return _session_manager.get_session(thread_id).snapshot(thread_id)

        def browser_back() -> str:
            """Navigate the owned page back and return one fresh observation."""

            thread_id = _get_thread_id()
            result = _session_manager.get_session(thread_id).go_back(thread_id)
            append_browser_history(thread_id, {"action": "back", "timestamp": datetime.now().isoformat()})
            return result

        def browser_tab(action: str = "list", tab_id: int | None = None, url: str | None = None) -> str:
            """List, switch, open, or close only tabs owned by this task."""

            if action == "new" and url:
                selected, error = _normalise_url(url)
                if selected is None:
                    return error
                url = selected
            thread_id = _get_thread_id()
            session = _session_manager.get_session(thread_id)
            note = ""
            if action == "new" and url:
                policy, reason = _navigation_policy(url, session.current_url(thread_id))
                if policy == "block":
                    return f"BLOCKED: {reason}"
                local = _local.local_development_origin(url) is not None
                refusal, note = _local_address_refusal(session, thread_id, url, "browser_tab")
                if refusal:
                    return refusal
                if policy == "ask" and not (local and reason.startswith("Navigation targets a local")):
                    from row_bot.tools.approval_gate import gate_action

                    session.mark_waiting_approval(thread_id, "Approve new task tab")
                    blocked = gate_action({
                        "tool": "browser_tab", "label": "Managed Browser new-tab navigation",
                        "action": "new_tab", "origin_and_path": _history_url(url), "reason": reason,
                    })
                    if blocked:
                        session.end_activity(thread_id)
                        return blocked
            result = session.tab_action(action, tab_id, url, thread_id)
            append_browser_history(thread_id, {
                "action": f"tab_{action}", "tab_id": tab_id,
                "url": _history_url(url) if url else None, "timestamp": datetime.now().isoformat(),
            })
            return f"{note}\n{result}" if note else result

        def browser_screenshot(full_page: bool = False) -> str:
            """Save a picture of this chat's page to its files and show it to the person."""

            thread_id = _get_thread_id()
            image, words = _session_manager.get_session(thread_id).capture_page(thread_id, full_page=full_page)
            if image is None:
                return words
            if not _show_image(image):
                return "The screenshot was taken but couldn't be shown in the chat."
            return f"{words} Saved to this chat's files and shown to the person."

        def browser_viewport(preset: str, device_scale: float | None = None) -> str:
            """Show this chat's page at a phone, tablet or desktop size."""

            thread_id = _get_thread_id()
            return _session_manager.get_session(thread_id).set_viewport(preset, device_scale, thread_id)

        def browser_record(action: str, max_minutes: int = 5) -> str:
            """Start or stop recording this chat's page in Row-Bot's browser to a video."""

            from row_bot import recordings
            from row_bot.browser.recording import encoder_problem

            thread_id = _get_thread_id()
            if action == "stop":
                record = recordings.active_for(thread_id, "browser")
                if record is None:
                    latest = recordings.latest_for(thread_id, "browser")
                    return recordings.summary(latest) if latest else "Row-Bot's browser isn't being recorded in this chat."
                recordings.stop(record.recording_id, by="agent")
                recordings.wait(record, 30)
                return recordings.summary(record)
            if action != "start":
                return "Use action start or stop."
            running = recordings.active_for(thread_id, "browser")
            if running is not None:
                return recordings.summary(running)
            problem = encoder_problem()
            if problem:
                return problem
            session = _session_manager.get_session(thread_id)
            if not session.current_url(thread_id) or session.current_url(thread_id) in session._BLANK_URLS:
                return "Open a page with browser_navigate first; the recording follows this chat's page."
            minutes = recordings.minutes(max_minutes, "browser")
            from row_bot.tools.approval_gate import approval_check

            refusal, _line = approval_check(
                _record_approval(minutes),
                risks=("records Row-Bot's browser window to a video saved in the chat",),
                blocked_message="BLOCKED: Recording needs approval, and this chat is on Block.",
                cancelled_message="The person didn't allow recording; nothing was recorded.",
            )
            if refusal:
                return refusal

            def begin(record, path):
                try:
                    return session.start_recording(
                        path,
                        max_seconds=record.max_seconds,
                        size_limit=recordings.SIZE_LIMIT,
                        on_end=lambda reason, video: recordings.finished(record, reason, video),
                        thread_id=thread_id,
                    )
                except LookupError:
                    raise recordings.RecordingError(
                        "Open a page with browser_navigate first; the recording follows this chat's page."
                    ) from None

            try:
                record = recordings.start(thread_id, "browser", max_minutes=minutes, begin=begin)
            except recordings.RecordingError as exc:
                return str(exc)
            except Exception:
                return "Row-Bot's browser couldn't start recording. Try again in a moment."
            return (
                recordings.summary(record)
                + " Do the steps to record, then call browser_record with action stop; the video is saved "
                "to this chat's files."
            )

        return [
            StructuredTool.from_function(
                func=browser_navigate, name="browser_navigate",
                description=(
                    "Open a website in Row-Bot's owned managed browser profile; returns opaque exact target tokens. "
                    "Addresses on this computer (localhost:5173, 127.0.0.1:8000) open once the person allows them "
                    "for this chat (one ask per address); servers you started here with start_process open "
                    "without asking. Other local-network addresses never open."
                ),
                args_schema=_NavigateInput,
            ),
            StructuredTool.from_function(
                func=browser_click, name="browser_click",
                description="Click an exact opaque target from the current managed-browser observation.",
                args_schema=_ClickInput,
            ),
            StructuredTool.from_function(
                func=browser_type, name="browser_type",
                description=(
                    "Enter hidden text into an exact current managed-browser input target. "
                    "For a search field, use submit=true so this call returns the post-submit observation; "
                    "do not add a separate snapshot before opening a result."
                ),
                args_schema=_TypeInput,
            ),
            StructuredTool.from_function(
                func=browser_scroll, name="browser_scroll",
                description="Scroll the current managed page and return one semantic observation.",
                args_schema=_ScrollInput,
            ),
            StructuredTool.from_function(
                func=browser_snapshot, name="browser_snapshot",
                description="Refresh the managed page observation; old opaque target tokens become stale.",
            ),
            StructuredTool.from_function(
                func=browser_back, name="browser_back",
                description="Navigate the current owned managed-browser page back.",
            ),
            StructuredTool.from_function(
                func=browser_tab, name="browser_tab",
                description="Manage only this task's tabs in Row-Bot's owned browser profile.",
                args_schema=_TabInput,
            ),
            StructuredTool.from_function(
                func=browser_screenshot, name="browser_screenshot",
                description=(
                    "Save a picture of this chat's current page to the chat's files and show it to the person "
                    "(what's on screen, or the whole page). Use it when they want to see the page; use "
                    "browser_snapshot to read it yourself."
                ),
                args_schema=_ScreenshotInput,
            ),
            StructuredTool.from_function(
                func=browser_viewport, name="browser_viewport",
                description=(
                    "Show this chat's page at a phone, tablet or desktop size (with an optional device scale), "
                    "to check a responsive layout. It stays until changed; observe the page again afterwards."
                ),
                args_schema=_ViewportInput,
            ),
            StructuredTool.from_function(
                func=browser_record, name="browser_record",
                description=(
                    "Record this chat's page in Row-Bot's browser to a video saved in the chat's files (asks first). "
                    "Start, do the steps, then stop. It records only Row-Bot's browser: to record the whole "
                    "screen use computer_use start_recording; without Computer use, tell the person that "
                    "recording the screen needs it (Settings › System › Browser and computer use)."
                ),
                args_schema=_RecordInput,
            ),
        ]


registry.register(BrowserTool())
