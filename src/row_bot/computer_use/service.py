"""Computer use: Row-Bot works in an app on this computer with the real mouse and keyboard, in view.

One session at a time (there is one mouse): it belongs to the chat whose turn
is using the computer and ends with that turn. The session:

- brings the chosen window to the front and acts on it with real input
  (Cua Driver's foreground delivery), one action at a time;
- shows the Stop bar ("Row-Bot is using Microsoft Edge · Stop (Esc)"); Esc or
  Stop end the turn at once, as the chat's own Stop does from any device;
- notices the person's own mouse and keyboard between actions, and the next
  action asks "Carry on?" in the chat instead of going on;
- takes a picture of the window after every action: the model checks it
  itself. There are no verdicts: an action says what it did.

Asking first (D19): the first use of each app in a chat, and paying, buying,
booking with a charge, sending, posting, deleting and system settings
(``policy``), through the shared approval levels. Row-Bot never types into a
password, code or card field. Pictures stay in memory and are never saved.
"""

from __future__ import annotations

import concurrent.futures
import logging
import math
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from langgraph.errors import GraphBubbleUp

from row_bot.cancellation import current_cancellation_scope
from row_bot.computer_use import display, policy
from row_bot.computer_use.client import CuaClient, CuaResponse
from row_bot.computer_use.control_bar import ControlBar
from row_bot.computer_use.screen import Shot, Window, take

logger = logging.getLogger(__name__)

ACTIONS = ("screenshot", "click", "double_click", "right_click", "type", "key", "scroll", "drag", "wait",
           "open_app", "list_apps")
SETUP_FIX = "Open Settings › System › Browser and computer use › Set up…"
# Shell windows that aren't apps a person works in.
_SHELL_TITLES = frozenset({
    "program manager", "windows input experience", "microsoft text input application", "task switching",
    "task view", "start", "search", "notification center", "action center", "new notification",
})
_SHELL_PROCESSES = frozenset({
    "textinputhost", "shellexperiencehost", "startmenuexperiencehost", "searchhost", "searchapp", "lockapp",
    "dwm", "systemsettingsbroker", "widgets", "gamebar",
})
# Processes that host other apps' windows (Store apps, Python apps): their windows go by their titles.
_FRAME_HOSTS = frozenset({"applicationframehost", "python", "pythonw"})
_DONE = {"click": "Clicked", "double_click": "Double-clicked", "right_click": "Right-clicked"}
# How far (physical pixels) the pointer may be from where a click aimed: Cua rounds to whole pixels.
_POINTER_SLACK = 3


class ComputerError(RuntimeError):
    """What went wrong, in words the model and the person read, and how to fix it."""

    def __init__(self, message: str, *, fix: str = "", code: str = "failed") -> None:
        super().__init__(message)
        self.message = message
        self.fix = fix
        self.code = code

    def text(self) -> str:
        """The tool's result: "Blocked:" for what Row-Bot won't do, the person's own "no" as the card said it,
        "Error:" for what went wrong, then the fix."""

        lead = ("" if self.code == "denied" else "Blocked: " if self.code in {"secret", "not_allowed"}
                else "Cancelled: " if self.code == "stopped" else "Error: ")
        return lead + self.message + (f"\nFix: {self.fix}" if self.fix else "")


def _key(value: object) -> str:
    text = re.sub(r"\.(?:exe|app)$", "", str(value or "").strip().casefold())
    return "".join(character for character in text if character.isalnum())


def _words_of(value: object) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", str(value or "").casefold()) if word}


def _stop_turn(thread_id: str) -> None:
    """Stop a chat's turn as the chat's own Stop does (the bar's Stop and Esc)."""

    from row_bot.application.client_platform import client_platform_service

    client_platform_service.stop_conversation(thread_id)


@dataclass
class _Session:
    thread_id: str
    generation_id: str
    client: CuaClient | None = None
    bar: ControlBar | None = None
    window: Window | None = None
    shot: Shot | None = None
    live: tuple[bytes, float] | None = None
    state: str = "working"  # working | waiting_approval | paused
    doing: str = ""
    mark: tuple[float, float] | None = None
    armed_after: float = math.inf
    person_acted: bool = False
    asking: bool = False
    answered: float = -math.inf
    last_control: policy.Control | None = None


class ComputerUseService:
    """The one computer use session, the chats' app approvals, and what went wrong last."""

    SETTLE_SECONDS = 0.5
    LAUNCH_WAIT_SECONDS = 10.0
    LIVE_PICTURE_SECONDS = 1.5
    # After the person answered Row-Bot (an OK, "Carry on"), their hand on the mouse doesn't count yet.
    PERSON_GRACE_SECONDS = 1.0

    def __init__(
        self,
        *,
        client_factory: Callable[[], CuaClient] | None = None,
        bar_factory: Callable[..., Any] | None = None,
        stop_turn: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        window_display: Callable[[int], display.WindowDisplay | None] = display.window_display,
        cursor_position: Callable[[], tuple[int, int] | None] = display.cursor_position,
    ) -> None:
        from row_bot.computer_use.client import default_client

        self._client_factory = client_factory or default_client
        self._bar_factory = bar_factory or ControlBar
        self._stop_turn = stop_turn or _stop_turn
        self._clock = clock
        self._window_display = window_display
        self._cursor_position = cursor_position
        self._lock = threading.RLock()
        self._acting = threading.Lock()
        self._driver = threading.RLock()
        self._cancel = threading.Event()
        self._session: _Session | None = None
        self._revision = 0
        self._names: dict[int, str] = {}
        # Per chat: the apps it may use ({app key: "person" | "reviewer"}), what went wrong last,
        # and the turns the person stopped from the "Carry on?" card.
        self._allowed: dict[str, dict[str, str]] = {}
        self._problems: dict[str, tuple[ComputerError, str]] = {}
        self._stopped_turns: set[tuple[str, str]] = set()

    # ── The session ──────────────────────────────────────────────────────────

    def begin(self, thread_id: str, generation_id: str) -> None:
        """This chat's turn uses the computer (the session starts, or carries on)."""

        with self._lock:
            stopped = (thread_id, generation_id) in self._stopped_turns
        if stopped:
            raise ComputerError("The person stopped computer use in this turn.",
                                fix="Don't use the computer again unless they ask.", code="stopped")
        with self._lock:
            session = self._session
            if session is not None and session.thread_id != thread_id:
                raise ComputerError("Row-Bot is using the computer in another chat.",
                                    fix="Wait for it to finish there, or stop it with Esc.", code="busy")
            if session is None:
                self._cancel.clear()
                self._session = _Session(thread_id, generation_id)
                self._bump()
            else:
                session.generation_id = generation_id

    def _current(self) -> _Session:
        with self._lock:
            session = self._session
        if session is None:
            raise concurrent.futures.CancelledError("Computer use stopped")
        return session

    def _bump(self) -> None:
        with self._lock:
            self._revision += 1

    def stop(self) -> None:
        """End the session: input stops, the bar goes, the driver closes. The chats' approvals stay."""

        with self._lock:
            session, self._session = self._session, None
            self._cancel.set()
            self._names.clear()
            self._revision += 1
        if session is None:
            return
        if session.client is not None:
            session.client.close(graceful=False)
        if session.bar is not None:
            session.bar.close()

    def close_for_thread(self, thread_id: str) -> None:
        with self._lock:
            belongs = self._session is not None and self._session.thread_id == thread_id
        if belongs:
            self.stop()

    def forget(self, thread_id: str) -> None:
        """A deleted chat: its session, approvals and problems go."""

        self.close_for_thread(thread_id)
        with self._lock:
            self._allowed.pop(thread_id, None)
            self._problems.pop(thread_id, None)
            self._stopped_turns = {turn for turn in self._stopped_turns if turn[0] != thread_id}

    def allow(self, thread_id: str, app: str) -> None:
        """The person allowed *app* in this chat themselves (Settings' Test)."""

        with self._lock:
            self._allowed.setdefault(thread_id, {})[_key(app)] = "person"

    def stopped_by_person(self, thread_id: str, generation_id: str) -> None:
        with self._lock:
            self._stopped_turns.add((thread_id, generation_id))
        self.stop()

    def _check_cancelled(self) -> None:
        scope = current_cancellation_scope()
        if self._cancel.is_set() or (scope is not None and scope.is_cancelled()):
            raise concurrent.futures.CancelledError("Computer use stopped")

    def _pause(self, seconds: float) -> None:
        if seconds > 0 and self._cancel.wait(seconds):
            raise concurrent.futures.CancelledError("Computer use stopped")
        self._check_cancelled()

    # ── The Stop bar and the person's input ──────────────────────────────────

    def _bar(self, session: _Session) -> ControlBar:
        if session.bar is None:
            thread_id = session.thread_id
            session.bar = self._bar_factory(on_stop=lambda by: self._bar_stop(thread_id, by),
                                            on_input=lambda kind: self._person_input(thread_id, kind))
        return session.bar

    def _bar_stop(self, thread_id: str, by: str) -> None:
        logger.info("Computer use stopped from the Stop bar (%s)", "Esc" if by == "esc" else "Stop")
        with self._lock:
            if self._session is None or self._session.thread_id != thread_id:
                return
            self._cancel.set()
        try:
            self._stop_turn(thread_id)
        except Exception:
            logger.warning("The Stop bar couldn't stop the turn", exc_info=True)
        self.stop()

    def _person_input(self, thread_id: str, _kind: str) -> None:
        with self._lock:
            session = self._session
            if (session is None or session.thread_id != thread_id or session.state != "working"
                    or self._clock() < session.armed_after):
                return
            if not session.person_acted:
                session.person_acted = True
                self._revision += 1

    def _arm(self, session: _Session) -> None:
        """Count the person's input from now (not just after they answered Row-Bot in the chat)."""

        with self._lock:
            session.armed_after = max(self._clock(), session.answered + self.PERSON_GRACE_SECONDS)
            session.person_acted = False

    def _set_state(self, session: _Session, state: str, words: str = "") -> None:
        with self._lock:
            session.state = state
            if state != "working":
                session.armed_after = math.inf  # The person answers in the chat: that isn't using the computer.
            self._revision += 1
        if session.window is not None and session.bar is not None:
            session.bar.show(words or f"Row-Bot is using {session.window.app}", session.window.bounds)

    def must_ask(self) -> bool:
        """The person used the computer since Row-Bot's last action (or the question is waiting)."""

        with self._lock:
            session = self._session
            return bool(session and (session.asking or session.person_acted))

    def asking(self) -> str:
        """The "Carry on?" question is up: the app Row-Bot was using."""

        session = self._current()
        with self._lock:
            session.asking = True
        app = session.window.app if session.window else "the app"
        self._set_state(session, "paused", "Row-Bot paused · Carry on in the chat")
        return app

    def carry_on(self) -> Shot:
        """The person said to carry on: a fresh picture, nothing replayed."""

        session = self._current()
        with self._lock:
            session.asking = False
            session.person_acted = False
            session.answered = self._clock()
        self._set_state(session, "working")
        window = self._require_window(session)
        with self._acting:
            self._front(session, window, strict=False)
            shot = self._capture(session)
        self._arm(session)
        return shot

    # ── The driver ───────────────────────────────────────────────────────────

    def _call(self, session: _Session, tool: str, arguments: dict[str, Any]) -> CuaResponse:
        self._check_cancelled()
        if session.client is None:
            try:
                client = self._client_factory()
                client.start()
            except PermissionError as exc:
                raise ComputerError("Computer use needs its notice accepted first.", fix=SETUP_FIX,
                                    code="setup") from exc
            except Exception as exc:
                logger.info("Cua Driver didn't start", exc_info=True)
                raise ComputerError("Cua Driver didn't start.", code="setup",
                                    fix=f"Run Check setup in {SETUP_FIX[5:]}") from exc
            with self._lock:
                if self._session is not session:
                    client.close(graceful=False)
                    raise concurrent.futures.CancelledError("Computer use stopped")
                session.client = client
        try:
            with self._driver:
                response = session.client.call(tool, arguments)
        except ConnectionError as exc:
            self._check_cancelled()  # Stopped on purpose (Esc, Stop): not a driver failure.
            self.stop()
            raise ComputerError("Cua Driver stopped while Row-Bot was using the computer.", code="driver",
                                fix=f"Try again. If it keeps happening, run Check setup in {SETUP_FIX[5:]}") from exc
        except PermissionError:
            raise
        except Exception as exc:
            self._check_cancelled()
            logger.info("Cua Driver call %s failed", tool, exc_info=True)
            raise ComputerError("Cua Driver couldn't do that.", code="driver",
                                fix="Take a screenshot and try again.") from exc
        self._check_cancelled()
        if response.is_error and response.error_code == "permission_denied":
            raise ComputerError("Row-Bot doesn't have permission to see or control this computer.", code="permission",
                                fix=f"{SETUP_FIX} › Check setup, and allow Row-Bot in Privacy & Security on a Mac.")
        return response

    # ── Windows ──────────────────────────────────────────────────────────────

    def _app_names(self, session: _Session, pids: set[int]) -> None:
        """The names people know apps by (Cua's list of apps is slow: asked only for processes not seen yet)."""

        if not pids - set(self._names):
            return
        self._names.update({pid: "" for pid in pids})
        response = self._call(session, "list_apps", {})
        for row in response.structured.get("apps") or []:
            if isinstance(row, dict) and row.get("running") and row.get("name"):
                try:
                    self._names[int(row.get("pid") or 0)] = str(row["name"])[:80]
                except (TypeError, ValueError):
                    continue

    def windows(self, session: _Session | None = None) -> list[Window]:
        """The windows a person works in, front first (never Row-Bot's own, nor the shell's)."""

        session = session or self._current()
        response = self._call(session, "list_windows", {})
        if response.is_error:
            raise ComputerError("Row-Bot couldn't list the open windows.", code="driver",
                                fix="Try again in a moment.")
        rows = [row for row in response.structured.get("windows") or [] if isinstance(row, dict)]
        own = {session.bar.pid} if session.bar is not None and session.bar.pid else set()
        candidates: list[tuple[dict[str, Any], int]] = []
        for row in rows:
            try:
                pid = int(row.get("pid") or 0)
            except (TypeError, ValueError):
                continue
            if pid in own:
                continue
            candidates.append((row, pid))
        self._app_names(session, {pid for _row, pid in candidates})
        found: list[Window] = []
        for row, pid in candidates:
            title = " ".join(str(row.get("title") or "").split())[:200]
            process = str(row.get("app_name") or row.get("name") or "")
            bounds = row.get("bounds") if isinstance(row.get("bounds"), dict) else {}
            try:
                box = (float(bounds.get("x") or 0), float(bounds.get("y") or 0),
                       float(bounds.get("width") or 0), float(bounds.get("height") or 0))
                window_id = int(row.get("window_id") or 0)
                front = int(row.get("z_index") or 0)
            except (TypeError, ValueError):
                continue
            minimized = bool(row.get("minimized"))
            if not title or not window_id or title.casefold() in _SHELL_TITLES or _key(process) in _SHELL_PROCESSES:
                continue
            if not minimized and (box[2] < 8 or box[3] < 8):
                continue
            app = self._names.get(pid) or re.sub(r"\.(?:exe|app)$", "", process, flags=re.IGNORECASE) or title
            if _key(app) in _FRAME_HOSTS or _key(process) in _FRAME_HOSTS:
                app = title  # A Store or Python app's window (Calculator, Settings) goes by its title.
            if policy.own_window(app, title) or policy.own_window(process, title):
                continue
            found.append(Window(pid, window_id, app[:80], title, box, minimized, front))
        found.sort(key=lambda window: (window.minimized, -window.front))
        return found

    def list_apps(self) -> str:
        session = self._current()
        windows = self.windows(session)
        if not windows:
            return "No app windows are open. Open one with open_app."
        apps: dict[str, list[str]] = {}
        for window in windows:
            apps.setdefault(window.app, []).append(window.title + (" (minimized)" if window.minimized else ""))
        lines = ["Open windows:"]
        for app, titles in list(apps.items())[:25]:
            shown = "; ".join(f'"{title[:70]}"' for title in titles[:4])
            more = f" and {len(titles) - 4} more" if len(titles) > 4 else ""
            lines.append(f"- {app}: {shown}{more}")
        lines.append('Use one with app="…" (the app\'s name, or words from its window\'s title).')
        return "\n".join(lines)[:2000]

    def find(self, app: str, session: _Session | None = None) -> Window:
        """The window *app* names: an app's name, or words from a window's title (the front one wins)."""

        session = session or self._current()
        wanted, words = _key(app), _words_of(app)
        if not wanted:
            raise ComputerError("Say which app: app=\"Microsoft Edge\", for example.", code="input",
                                fix="Call list_apps to see what's open.")
        best: tuple[int, Window] | None = None
        for window in self.windows(session):
            name, title = _key(window.app), _words_of(window.title)
            score = (4 if name == wanted else 3 if words <= _words_of(window.app) or wanted in name
                     else 2 if words and words <= title else 1 if wanted in _key(window.title) else 0)
            if score and (best is None or score > best[0]):
                best = (score, window)
        if best is None:
            raise ComputerError(f'No window of "{app}" is open.', code="no_window",
                                fix="Call list_apps to see what's open, or open_app to open it.")
        return best[1]

    def _require_window(self, session: _Session) -> Window:
        if session.window is None:
            raise ComputerError("No app is chosen yet.", code="input",
                                fix='Call screenshot or open_app with app="…" first.')
        return session.window

    # ── Asking first ─────────────────────────────────────────────────────────

    def _allow(self, session: _Session, app: str, *, opening: bool = False) -> None:
        """The first use of an app in a chat asks once (by the chat's approval level)."""

        from row_bot.approval_policy import normalize_approval_mode
        from row_bot.tools.approval_gate import APPROVAL_FULL_AUTONOMY, current_approval_mode

        key = _key(app)
        with self._lock:
            given = self._allowed.get(session.thread_id, {}).get(key)
        if given == "person" or (given == "reviewer" and normalize_approval_mode(current_approval_mode()) == "auto"):
            return
        question = f"Let Row-Bot {'open and use' if opening else 'use'} {app} in this chat?"
        line = self._approve(
            session,
            {
                "tool": "computer",
                "label": "Use an app on this computer",
                "action": "use_app",
                "question": question,
                "description": (f"Row-Bot will use {app} with your mouse and keyboard while you watch. "
                                "A bar on the screen shows it; press Esc or Stop to stop it at any time."),
                "risk_class": "medium",
                "args": {"app": app},
            },
            risks=(f"controls {app} on this computer with the real mouse and keyboard",),
            blocked_message=(f"BLOCKED: Using {app} needs approval, and this chat is on Block. "
                             "Ask the person to change the approval level."),
            cancelled_message=f"The person said no: Row-Bot won't use {app}.",
        )
        if line != APPROVAL_FULL_AUTONOMY:
            with self._lock:
                self._allowed.setdefault(session.thread_id, {})[key] = (
                    "reviewer" if line.startswith("Approval: approved by the reviewer") else "person")

    def _ask_first(self, session: _Session, consequence: policy.Consequence,
                   point: tuple[float, float] | None) -> None:
        self._approve(
            session,
            policy.approval_request(consequence),
            risks=(consequence.risk,),
            blocked_message=(f"BLOCKED: {consequence.question()[:-1]} needs approval, and this chat is on "
                             "Block. Ask the person to do it themselves, or to change the approval level."),
            cancelled_message=f"The person said no; Row-Bot didn't {consequence.doing}.",
            mark=point,
        )

    def _approve(self, session: _Session, payload: dict[str, Any], *, risks: tuple[str, ...], blocked_message: str,
                 cancelled_message: str, mark: tuple[float, float] | None = None) -> str:
        """Get an approval by the chat's level: its approval line, or a ``ComputerError`` with the refusal.

        While the person may be asked, the session waits (the bar says so, and their mouse and keyboard
        don't count as using the computer); once they've answered, they get a moment to let go of the mouse.
        """

        from row_bot.approval_policy import normalize_approval_mode
        from row_bot.tools.approval_gate import approval_check, current_approval_mode

        waits = normalize_approval_mode(current_approval_mode()) != "full"
        with self._lock:
            session.mark = mark
        if waits:
            self._set_state(session, "waiting_approval", "Row-Bot is waiting for your OK in the chat")
        try:
            refusal, line = approval_check(payload, risks=risks, blocked_message=blocked_message,
                                           cancelled_message=cancelled_message)
        except GraphBubbleUp:
            raise  # The turn waits on the card: the session waits with it, ring and all.
        except BaseException:
            self._done_waiting(session, waits)
            raise
        self._done_waiting(session, waits)
        if line.startswith("Approval: asked"):  # The person answered the card.
            with self._lock:
                session.answered = self._clock()
        if refusal:
            raise ComputerError(refusal, code="denied")
        return line

    def _check_command(self, session: _Session, window: Window, text: str) -> None:
        """What's typed into a terminal runs as a command: checked as the shell tool checks one.

        Blocked commands and Row-Bot's private data never run; anything that isn't plainly read-only asks
        by the approval level, on the same card as run_command (so Allow in this chat counts commands by
        what they do).
        """

        from row_bot.tools import registry
        from row_bot.tools.shell_tool import PRIVATE_DATA_REFUSAL, classify_command

        shell = registry.get_tool("shell")
        command = text.strip()
        kind = classify_command(command, shell._get_extra_blocked() if shell is not None else None)
        if kind == "blocked":
            raise ComputerError(f"BLOCKED: that command matches a blocked pattern, in a terminal as with the "
                                f"shell tool: {command[:200]}", code="not_allowed")
        if kind == "private_data":
            raise ComputerError(PRIVATE_DATA_REFUSAL, code="not_allowed")
        if kind == "needs_approval":
            self._approve(
                session,
                {"tool": "run_command", "label": "Run shell command",
                 "description": f"Type into {window.app} and run: {command[:2000]}",
                 "args": {"command": command[:2000]}},
                risks=("runs a command in a terminal on this computer",),
                blocked_message=(f"BLOCKED: This command requires approval and cannot run in Block approval "
                                 f"mode: {command[:200]}"),
                cancelled_message="The person said no; Row-Bot didn't type that command.",
            )

    def _done_waiting(self, session: _Session, waited: bool) -> None:
        with self._lock:
            session.mark = None
        if waited and self._session is session:
            self._set_state(session, "working")

    # ── Choosing a window ────────────────────────────────────────────────────

    def use(self, app: str) -> Shot:
        """Work in *app*'s window from now on (it asks first the first time in this chat)."""

        session = self._current()
        window = self.find(app, session)
        reason = policy.unusable_app(window.app, window.title)
        if reason:
            raise ComputerError(reason, code="not_allowed")
        self._allow(session, window.app)
        return self._select(session, window)

    def open_app(self, app: str) -> Shot:
        """Open *app* (or use its window when it's open already)."""

        session = self._current()
        name = " ".join(str(app or "").split())
        if not name or len(name) > 80 or re.search(r"[\\/:]|://|\s--?\w|[\x00-\x1f]", name):
            raise ComputerError("open_app takes an app's name, not a path, an address or arguments.", code="input",
                                fix='Use the name people know it by: app="Calculator".')
        try:
            return self.use(name)
        except ComputerError as error:
            if error.code != "no_window":
                raise
        reason = policy.unusable_app(name)
        if reason:
            raise ComputerError(reason, code="not_allowed")
        self._allow(session, name, opening=True)
        response = self._call(session, "launch_app", {"name": name})
        if response.is_error:
            raise ComputerError(f"Row-Bot couldn't open {name}.", code="no_app",
                                fix="Check the app's name as the Start menu or Applications shows it, or open it "
                                    "yourself and call list_apps.")
        try:
            launched = int(response.structured.get("pid") or 0)
        except (TypeError, ValueError):
            launched = 0
        deadline = self._clock() + self.LAUNCH_WAIT_SECONDS
        while True:
            for window in self.windows(session):
                if (launched and window.pid == launched) or _key(window.app) == _key(name) or (
                        _words_of(name) <= _words_of(window.title)):
                    if policy.unusable_app(window.app, window.title):
                        raise ComputerError(policy.unusable_app(window.app, window.title), code="not_allowed")
                    with self._lock:
                        self._allowed.setdefault(session.thread_id, {}).setdefault(
                            _key(window.app), self._allowed[session.thread_id].get(_key(name), "person"))
                    return self._select(session, window)
            if self._clock() >= deadline:
                raise ComputerError(f"{name} started, but no window of it showed up.", code="no_window",
                                    fix="Wait a moment and call list_apps.")
            self._pause(0.25)

    def _select(self, session: _Session, window: Window) -> Shot:
        with self._lock:
            session.window = window
            session.last_control = None
            self._revision += 1
        self._bar(session).show(f"Row-Bot is using {window.app}", window.bounds)
        with self._acting:
            self._front(session, window, strict=False)
            shot = self._capture(session)
        self._arm(session)
        return shot

    def _front(self, session: _Session, window: Window, *, strict: bool = True) -> None:
        """Bring the window to the front. Not *strict* (a picture): Windows keeping another window in front
        is fine, the picture is of the window itself; input needs it in front."""

        response = self._call(session, "bring_to_front", {"pid": window.pid, "window_id": window.window_id})
        if response.is_error and _refused_foreground(response):
            if strict:
                raise _no_foreground(window)
            return
        if response.is_error:
            self._window_gone(session, window)

    def _window_gone(self, session: _Session, window: Window) -> None:
        if any(item.window_id == window.window_id for item in self.windows(session)):
            raise ComputerError(f"Row-Bot couldn't bring {window.app} to the front.", code="driver",
                                fix="Try again in a moment.")
        with self._lock:
            session.window = session.shot = None
        raise ComputerError(f'The {window.app} window ("{window.title[:60]}") closed.', code="no_window",
                            fix="Call list_apps and choose a window, or open_app.")

    # ── Pictures ─────────────────────────────────────────────────────────────

    def _capture(self, session: _Session, *, controls: bool = True) -> Shot:
        window = self._require_window(session)
        current = next((item for item in self.windows(session) if item.window_id == window.window_id), None)
        if current is None:
            self._window_gone(session, window)
        assert current is not None
        response = self._call(session, "get_window_state", {
            "pid": current.pid, "window_id": current.window_id, "include_screenshot": True,
            "max_elements": 1500 if controls else 1, "max_depth": 25 if controls else 1,
        })
        if response.is_error or response.image_bytes is None:
            raise ComputerError(f"Row-Bot couldn't take a picture of {current.app}.", code="no_picture",
                                fix="Make sure its window is on the screen and not minimized, then try again.")
        # Windows: how the window draws on its display, so a point maps to where it really is on screen.
        facts = self._window_display(current.window_id)
        shot = take(current, response.image_bytes, (response.image_width, response.image_height),
                    response.elements if controls else (), facts.scaling if facts else None)
        with self._lock:
            if controls:
                session.window, session.shot = current, shot
            session.live = (shot.thumbnail(), self._clock())
            self._revision += 1
        return shot

    def screenshot(self, app: str = "") -> Shot:
        if app:
            return self.use(app)
        session = self._current()
        self._require_window(session)
        return self._capture(session)

    def latest(self) -> Shot | None:
        with self._lock:
            return self._session.shot if self._session else None

    # ── Actions ──────────────────────────────────────────────────────────────

    def act(
        self,
        action: str,
        *,
        x: float | None = None,
        y: float | None = None,
        to_x: float | None = None,
        to_y: float | None = None,
        text: str = "",
        keys: str = "",
        scroll: str = "",
        seconds: float | None = None,
        before_input: Callable[[Shot, tuple[float, float] | None], None] | None = None,
    ) -> tuple[str, Shot]:
        """Do one action with real input on the chosen window; what was done, and the window after it.
        ``before_input`` hears the moment the input is sent, with the picture and point it aims at (a screen
        recording logs it, C9)."""

        session = self._current()
        window = self._require_window(session)
        shot = session.shot
        if shot is None:
            raise ComputerError("Take a screenshot first.", code="input", fix="Call screenshot.")
        if action == "wait":
            waited = max(0.5, min(float(seconds or 2), 10.0))
            self._pause(waited)
            return f"Waited {waited:g} seconds.", self._capture(session)

        point: tuple[float, float] | None = None
        if action in {"click", "double_click", "right_click", "drag"} or (action == "scroll" and x is not None):
            point = self._point(shot, x, y)
        end = self._point(shot, to_x, to_y) if action == "drag" else None
        control = shot.control_at(*point) if point and action != "drag" else None
        parsed_keys: tuple[tuple[str, ...], str] | None = None
        if action == "key":
            parsed_keys = policy.parse_keys(keys)
            if parsed_keys is None:
                raise ComputerError(f'"{keys}" isn\'t a key Row-Bot can press.', code="input",
                                    fix="Use one key or chord: enter, tab, escape, ctrl+s, cmd+shift+t.")
            refused = policy.refused_keys(*parsed_keys)
            if refused:
                raise ComputerError(refused, code="not_allowed")
        terminal = policy.is_terminal(window.app)
        if terminal and policy.terminal_paste(action, parsed_keys):
            raise ComputerError(policy.TERMINAL_PASTE_REFUSAL, code="not_allowed", fix="Use action=type.")
        if action == "type":
            if not text:
                raise ComputerError("There's nothing to type.", code="input", fix="Put the words in text.")
            field = session.last_control
            secret = policy.secret_field(field.role, field.label, clicked=True) if field else ""
            if secret:
                raise ComputerError(policy.SECRET_REFUSAL[secret], code="secret")
            if terminal:
                self._check_command(session, window, text)

        consequence = policy.classify(
            action, app=window.app, window=policy.window_facts(_title_of(window), shot.labels()),
            control=control if action != "type" else session.last_control, keys=parsed_keys, text=text,
        )
        if consequence is not None:
            self._ask_first(session, consequence, point)

        arguments: dict[str, Any] = {"pid": window.pid, "window_id": window.window_id, "delivery_mode": "foreground"}
        if action in _DONE:
            tool = action
            arguments.update(zip(("x", "y"), shot.to_cua(*point)))
            name = f' ({control.label[:60]})' if control and control.label else ""
            done = f"{_DONE[action]} at {round(point[0])},{round(point[1])}{name}."
            shown = (f'{_DONE[action]} "{control.label[:60]}"' if control and control.label
                     else f"{_DONE[action]} in the window")
        elif action == "drag":
            tool = "drag"
            (from_x, from_y), (end_x, end_y) = shot.to_cua(*point), shot.to_cua(*end)
            arguments.update(from_x=from_x, from_y=from_y, to_x=end_x, to_y=end_y)
            done = f"Dragged from {round(point[0])},{round(point[1])} to {round(end[0])},{round(end[1])}."
            shown = "Dragged in the window"
        elif action == "type":
            tool = "type_text"
            arguments["text"] = text[:4000]
            done = f"Typed {len(text)} characters."
            shown = done[:-1]  # Never the words themselves.
        elif action == "key":
            modifiers, key = parsed_keys
            tool = "hotkey" if modifiers else "press_key"
            if modifiers:
                arguments["keys"] = [*modifiers, key]
            else:
                arguments["key"] = key
            done = f"Pressed {policy.key_words(modifiers, key)}."
            shown = done[:-1]
        elif action == "scroll":
            direction, amount = _scroll(scroll)
            tool = "scroll"
            arguments.update(direction=direction, amount=amount)
            if point is not None:
                arguments.update(zip(("x", "y"), shot.to_cua(*point)))
            done = f"Scrolled {direction} {amount}."
            shown = f"Scrolled {direction}"
        else:
            raise ComputerError(f'"{action}" isn\'t an action.', code="input", fix=f"Use one of: {', '.join(ACTIONS)}.")

        with self._acting:
            self._front(session, window)
            bar = self._bar(session)
            if point is not None:
                bar.avoid(shot.to_screen(*point))
            with self._lock:
                session.doing = shown
                self._revision += 1
            bar.busy(True)
            try:
                if before_input is not None:
                    before_input(shot, point)
                response = self._call(session, tool, arguments)
            finally:
                bar.busy(False)
            if response.is_error and _refused_foreground(response):
                raise _no_foreground(window)
            if response.is_error:
                raise ComputerError(f"That didn't go through: {response.text[:200] or 'Cua Driver refused it'}.",
                                    code="driver", fix="Take a screenshot and try again.")
            if action in _DONE and shot.scaling is not None:
                self._check_pointer(window, shot, point, arguments, response)
            if action in {"click", "double_click"}:
                with self._lock:
                    session.last_control = control
            self._pause(self.SETTLE_SECONDS)
            after = self._capture(session)
        self._arm(session)
        return done, after

    def _check_pointer(self, window: Window, shot: Shot, point: tuple[float, float], arguments: dict[str, Any],
                       response: CuaResponse) -> None:
        """A click went where it aimed (Windows): the pointer is on the screen point the model's point is.

        Cua says it clicked wherever its own mapping put the pointer; a click that landed elsewhere must
        not read as done.
        """

        aimed = shot.to_screen(*point)
        pointer = self._cursor_position()
        if logger.isEnabledFor(logging.DEBUG):
            facts = self._window_display(window.window_id)
            logger.debug("Click in %s: model %s → screen (%.1f, %.1f) → Cua (%s, %s); pointer %s; Cua: %s; %s",
                         window.app, point, *aimed, arguments.get("x"), arguments.get("y"), pointer,
                         response.text[:160], facts.words() if facts else "no display facts")
        if pointer is None or math.dist(pointer, aimed) <= _POINTER_SLACK:
            return
        raise ComputerError(
            f"The pointer went to {pointer[0]},{pointer[1]} on the screen, not to the point Row-Bot aimed at in "
            f"{window.app} ({round(aimed[0])},{round(aimed[1])}), so the click may have landed somewhere else.",
            code="driver", fix="Take a screenshot to see what happened before going on.")

    @staticmethod
    def _point(shot: Shot, x: float | None, y: float | None) -> tuple[float, float]:
        if x is None or y is None:
            raise ComputerError("This action needs x and y.", code="input",
                                fix=f"Use a point in the latest screenshot ({shot.size[0]}×{shot.size[1]}).")
        if not shot.contains(float(x), float(y)):
            raise ComputerError(f"{x},{y} is outside the screenshot ({shot.size[0]}×{shot.size[1]}).", code="input",
                                fix="Use x and y from the latest screenshot.")
        return float(x), float(y)

    # ── What the chat shows ──────────────────────────────────────────────────

    def status_snapshot(self) -> dict[str, Any]:
        with self._lock:
            session = self._session
            window = session.window if session else None
            return {
                "engine": "computer",
                "active": session is not None,
                "thread_id": session.thread_id if session else "",
                "generation_id": session.generation_id if session else "",
                "state": session.state if session else "ready",
                "app": window.app if window else "",
                "window": _title_of(window) if window else "",
                "doing": session.doing if session else "",
                "person_acted": bool(session and session.person_acted),
                "has_thumbnail": bool(session and session.live),
                "revision": self._revision,
            }

    def ephemeral_picture(self) -> tuple[bytes, str, int] | None:
        """The latest small picture of the window (memory only): a ring marks where an action waits for an OK.

        Hidden while the person has the computer ("Carry on?"). While nothing
        else is going on, the picture is taken again every ``LIVE_PICTURE_SECONDS``.
        """

        with self._lock:
            session = self._session
            if session is None or session.state == "paused" or session.live is None:
                return None
            if session.state == "waiting_approval" and session.mark is not None and session.shot is not None:
                return session.shot.thumbnail(session.mark), "image/jpeg", self._revision
            stale = self._clock() - session.live[1] >= self.LIVE_PICTURE_SECONDS and session.state == "working"
        if stale and self._acting.acquire(blocking=False):
            try:
                self._capture(session, controls=False)
            except Exception:
                logger.debug("The live picture wasn't taken", exc_info=True)
            finally:
                self._acting.release()
        with self._lock:
            live = session.live
            return (live[0], "image/jpeg", self._revision) if live and self._session is session else None

    def note_problem(self, thread_id: str, error: ComputerError | None, generation_id: str = "") -> None:
        """What kept computer use from starting in a chat's turn (None: it worked), for its step to show."""

        with self._lock:
            if error is None:
                self._problems.pop(thread_id, None)
            else:
                self._problems[thread_id] = (error, generation_id)
            self._revision += 1

    def problem(self, thread_id: str) -> tuple[ComputerError, str] | None:
        """The chat's last problem and the turn that hit it."""

        with self._lock:
            return self._problems.get(thread_id)


def _title_of(window: Window) -> str:
    """A window's title without the app's name it ends with ("Inbox - Microsoft Edge" → "Inbox")."""

    for dash in (" - ", " – ", " — "):
        if window.title.endswith(dash + window.app):
            return window.title[: -len(dash + window.app)]
    return window.title


def _refused_foreground(response: CuaResponse) -> bool:
    """Windows kept another window in front (a locked screen, or the focus held elsewhere)."""

    return response.error_code == "foreground_unavailable" or "foreground_unavailable" in response.text


def _no_foreground(window: Window) -> ComputerError:
    return ComputerError(f"Windows didn't let Row-Bot bring {window.app} to the front, so nothing was done.",
                         code="foreground",
                         fix=f"If the screen is locked, unlock it. Otherwise click {window.app}'s window once, "
                             "then try again.")


def _scroll(value: str) -> tuple[str, int]:
    parts = str(value or "down").strip().casefold().split()
    direction = parts[0] if parts and parts[0] in {"up", "down", "left", "right"} else ""
    if not direction or len(parts) > 2:
        raise ComputerError(f'"{value}" isn\'t a scroll.', code="input", fix='Use "down", "up 3", "left" or "right".')
    try:
        amount = int(parts[1]) if len(parts) == 2 else 5
    except ValueError:
        raise ComputerError(f'"{value}" isn\'t a scroll.', code="input", fix='Use "down", "up 3", "left" or "right".') from None
    return direction, max(1, min(amount, 30))


_SERVICE: ComputerUseService | None = None
_SERVICE_LOCK = threading.Lock()


def get_computer_use_service() -> ComputerUseService:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = ComputerUseService()
        return _SERVICE


def shutdown_computer_use() -> None:
    if _SERVICE is not None:
        _SERVICE.stop()
