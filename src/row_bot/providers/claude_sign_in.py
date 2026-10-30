"""Claude's own sign-in, in a small terminal panel (H15).

The panel runs one program: the person's Claude Code with ``auth login``, in a pseudo-terminal on the
computer Row-Bot runs on, with Claude Code's own environment (``claude_code_environment``: none of Row-Bot's
secrets) in its own empty folder. The person signs in to Claude Code, in Claude Code: they see Claude's own
prompts, open Claude's link and paste Claude's code into Claude's prompt.

Row-Bot carries keystrokes in and output out, as SSH does. It never parses, logs, records or stores what
passes: output waits in memory, bounded, until the panel reads it, and is dropped when the sign-in closes.
Whether it worked is read from ``claude auth status`` after Claude Code exits; Row-Bot never opens Claude's
credential file or keychain entries.

Only the signed-in owner opens one (the routes' access check), and only from the device session that
opened it can it be read, typed into or closed. At most one runs at a time: a newer one replaces it. It
closes when Claude Code exits, when the panel closes, and after ten minutes with nothing passing either way.
It is never a shell, and is separate from the desktop terminal.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from row_bot.providers.claude_subscription import (
    ClaudePlanStatus,
    claude_code_environment,
    claude_code_workdir,
    claude_plan_status,
    find_claude_code,
)

logger = logging.getLogger(__name__)

#: The only arguments the panel ever starts Claude Code with.
SIGN_IN_ARGUMENTS = ("auth", "login")
#: The names Claude Code's installers give the program (the native binary, npm's shims).
_PROGRAM_NAMES = frozenset({"claude", "claude.exe", "claude.cmd"})
IDLE_SECONDS = 600.0
#: An ended sign-in's last output and result stay readable this long, then go.
ENDED_SECONDS = 120.0
SWEEP_SECONDS = 2.0
OUTPUT_LIMIT = 256 * 1024
_FRAME_BYTES = 4096
_FRAMES_PER_READ = 16
MAX_INPUT_BYTES = 16 * 1024

Ended = Literal["exited", "closed", "idle", "replaced"]


class SignInRefused(RuntimeError):
    """A sign-in request Row-Bot won't carry out; ``code`` is the public reason."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SignInPty(Protocol):
    """What the panel needs from a pseudo-terminal (``terminal_pty.CommandPty``; tests pass a fake)."""

    def read(self) -> str: ...
    def write(self, data: str) -> None: ...
    def resize(self, cols: int, rows: int) -> None: ...
    def is_alive(self) -> bool: ...
    def close(self) -> None: ...


PtyFactory = Callable[..., SignInPty]


def _command_pty(argv: list[str], *, env: dict[str, str], cwd: str, cols: int, rows: int) -> SignInPty:
    from row_bot.terminal_pty import CommandPty

    return CommandPty(argv, env=env, cwd=cwd, cols=cols, rows=rows)


def sign_in_command(program: str) -> list[str]:
    """``[program, "auth", "login"]`` for a program Claude Code's installers name ``claude``; else refused."""
    if not program or Path(program).name.lower() not in _PROGRAM_NAMES:
        raise SignInRefused("claude_code_missing")
    return [program, *SIGN_IN_ARGUMENTS]


def _size(cols: Any, rows: Any) -> tuple[int, int]:
    if type(cols) is not int or type(rows) is not int or not 20 <= cols <= 500 or not 5 <= rows <= 200:
        raise ValueError("invalid_terminal_size")
    return cols, rows


def _chunks(data: str) -> list[tuple[str, int]]:
    """Split text into frames of at most ``_FRAME_BYTES`` UTF-8 bytes without splitting a character."""
    chunks: list[tuple[str, int]] = []
    current: list[str] = []
    size = 0
    for character in data:
        width = len(character.encode("utf-8", errors="replace"))
        if current and size + width > _FRAME_BYTES:
            chunks.append(("".join(current), size))
            current, size = [], 0
        current.append(character)
        size += width
    if current:
        chunks.append(("".join(current), size))
    return chunks


@dataclass
class ClaudeSignIn:
    """One sign-in: its terminal, the output not yet dropped, and how it ended."""

    id: str
    owner: str
    pty: SignInPty | None
    active_at: float
    frames: deque[tuple[int, str, int]] = field(default_factory=deque)
    output_bytes: int = 0
    sequence: int = 0
    dropped_through: int = 0
    ended: Ended | None = None
    ended_at: float = 0.0
    plan: ClaudePlanStatus | None = None


class ClaudeSignIns:
    """The one Claude sign-in this Row-Bot may be running."""

    def __init__(self, *, pty_factory: PtyFactory | None = None, clock: Callable[[], float] = time.monotonic,
                 status: Callable[[], ClaudePlanStatus] | None = None, sweep_every: float | None = SWEEP_SECONDS
                 ) -> None:
        self._factory = pty_factory or _command_pty
        self._clock = clock
        self._status = status or (lambda: claude_plan_status(fresh=True))
        self._lock = threading.RLock()
        self._current: ClaudeSignIn | None = None
        self._sweeper: threading.Thread | None = None
        self._sweep_every = sweep_every
        self._stopped = threading.Event()

    # ── Opening and closing ────────────────────────────────────────────

    def open(self, owner: str, *, cols: int = 100, rows: int = 30, busy: Callable[[], bool] | None = None
             ) -> ClaudeSignIn:
        """Start ``claude auth login`` for ``owner`` (an access session), replacing any sign-in still open."""
        if not isinstance(owner, str) or not owner:
            raise SignInRefused("authentication_required")
        cols, rows = _size(cols, rows)
        if busy is not None and busy():
            raise SignInRefused("claude_code_installing")
        command = sign_in_command(find_claude_code())
        with self._lock:
            if self._current is not None:
                self._end(self._current, "replaced")
                self._current = None
            pty = self._factory(command, env=claude_code_environment(), cwd=str(claude_code_workdir()),
                                cols=cols, rows=rows)
            sign_in = ClaudeSignIn(id=secrets.token_urlsafe(24), owner=owner, pty=pty, active_at=self._clock())
            self._current = sign_in
        logger.info("Claude's sign-in started")
        self._start_sweeper()
        return sign_in

    def close(self, sign_in_id: str, owner: str) -> None:
        with self._lock:
            sign_in = self._find(sign_in_id, owner)
            if sign_in.ended is None:
                self._end(sign_in, "closed")
            self._current = None

    def close_all(self) -> None:
        """Close whatever runs (Row-Bot is stopping)."""
        with self._lock:
            if self._current is not None and self._current.ended is None:
                self._end(self._current, "closed")
            self._current = None
        self._stopped.set()

    def running(self) -> bool:
        with self._lock:
            return self._current is not None and self._current.ended is None

    # ── The panel's view ───────────────────────────────────────────────

    def read(self, sign_in_id: str, owner: str, cursor: int = 0) -> dict[str, Any]:
        if type(cursor) is not int or cursor < 0:
            raise ValueError("invalid_terminal_cursor")
        with self._lock:
            sign_in = self._find(sign_in_id, owner)
            exited = self._pump(sign_in)
        if exited:
            self._finish(sign_in)
        with self._lock:
            if cursor > sign_in.sequence:
                raise ValueError("invalid_terminal_cursor")
            truncated = cursor < sign_in.dropped_through
            after = max(cursor, sign_in.dropped_through)
            frames: list[dict[str, Any]] = []
            next_cursor = after
            for sequence, data, _ in sign_in.frames:
                if sequence <= after:
                    continue
                if len(frames) >= _FRAMES_PER_READ:
                    break
                frames.append({"sequence": sequence, "data": data})
                next_cursor = sequence
            ended = sign_in.ended
            plan = sign_in.plan
            return {
                "cursor": next_cursor,
                "latest": sign_in.sequence,
                "truncated": truncated,
                "frames": frames,
                "status": "running" if ended is None else "ended",
                "ended": ended,
                "plan": plan.to_json() if plan is not None and ended is not None else None,
            }

    def input(self, sign_in_id: str, owner: str, data: str) -> None:
        if not isinstance(data, str) or not data or len(data.encode("utf-8")) > MAX_INPUT_BYTES:
            raise ValueError("invalid_terminal_input")
        with self._lock:
            sign_in = self._find(sign_in_id, owner)
            if sign_in.ended is not None or sign_in.pty is None:
                raise SignInRefused("sign_in_ended")
            sign_in.pty.write(data)
            sign_in.active_at = self._clock()

    def resize(self, sign_in_id: str, owner: str, cols: int, rows: int) -> None:
        cols, rows = _size(cols, rows)
        with self._lock:
            sign_in = self._find(sign_in_id, owner)
            if sign_in.ended is None and sign_in.pty is not None:
                sign_in.pty.resize(cols, rows)

    # ── Housekeeping ───────────────────────────────────────────────────

    def sweep(self) -> None:
        """Carry output on, notice Claude Code ending, close an idle sign-in and drop an old ended one."""
        with self._lock:
            sign_in = self._current
            if sign_in is None:
                return
            now = self._clock()
            if sign_in.ended is not None:
                if now - sign_in.ended_at >= ENDED_SECONDS:
                    self._current = None
                return
            exited = self._pump(sign_in)
            if not exited and now - sign_in.active_at >= IDLE_SECONDS:
                self._end(sign_in, "idle")
                return
        if exited:
            self._finish(sign_in)

    def _start_sweeper(self) -> None:
        if self._sweep_every is None or (self._sweeper is not None and self._sweeper.is_alive()):
            return

        def run() -> None:
            while not self._stopped.wait(self._sweep_every):
                try:
                    self.sweep()
                except Exception:
                    logger.debug("Claude sign-in sweep failed", exc_info=True)
                with self._lock:
                    if self._current is None:
                        return

        self._sweeper = threading.Thread(target=run, name="claude-sign-in-sweep", daemon=True)
        self._sweeper.start()

    def _find(self, sign_in_id: str, owner: str) -> ClaudeSignIn:
        sign_in = self._current
        if (sign_in is None or not isinstance(sign_in_id, str)
                or not secrets.compare_digest(sign_in.id, sign_in_id) or sign_in.owner != owner):
            raise SignInRefused("not_found")
        return sign_in

    def _pump(self, sign_in: ClaudeSignIn) -> bool:
        """Move new output into frames; True when Claude Code has just ended. Caller holds the lock."""
        if sign_in.ended is not None or sign_in.pty is None:
            return False
        alive = sign_in.pty.is_alive()
        data = sign_in.pty.read()
        if data:
            sign_in.active_at = self._clock()
            for chunk, size in _chunks(data):
                sign_in.sequence += 1
                sign_in.frames.append((sign_in.sequence, chunk, size))
                sign_in.output_bytes += size
                while sign_in.output_bytes > OUTPUT_LIMIT and sign_in.frames:
                    sequence, _, dropped = sign_in.frames.popleft()
                    sign_in.output_bytes -= dropped
                    sign_in.dropped_through = sequence
        if alive:
            return False
        sign_in.pty.close()
        sign_in.pty = None
        return True

    def _finish(self, sign_in: ClaudeSignIn) -> None:
        """Claude Code ended: ask it whether it is signed in now (never its files)."""
        try:
            plan = self._status()
        except Exception:
            logger.info("Claude Code didn't say whether it is signed in after its sign-in", exc_info=True)
            plan = ClaudePlanStatus("error", detail="Claude Code didn't say whether it is signed in.")
        with self._lock:
            if sign_in.ended is None:
                sign_in.plan = plan
                sign_in.ended = "exited"
                sign_in.ended_at = self._clock()
        logger.info("Claude's sign-in ended (signed in: %s)", plan.ready)

    def _end(self, sign_in: ClaudeSignIn, why: Ended) -> None:
        """Stop Claude Code and drop every frame. Caller holds the lock."""
        if sign_in.pty is not None:
            try:
                sign_in.pty.close()
            except Exception:
                logger.debug("Closing Claude's sign-in failed", exc_info=True)
            sign_in.pty = None
        sign_in.frames.clear()
        sign_in.output_bytes = 0
        sign_in.ended = why
        sign_in.ended_at = self._clock()
        logger.info("Claude's sign-in closed (%s)", why)


_sign_ins: ClaudeSignIns | None = None
_sign_ins_lock = threading.Lock()


def claude_sign_ins() -> ClaudeSignIns:
    """This Row-Bot's sign-in panel owner, created on first use."""
    global _sign_ins
    with _sign_ins_lock:
        if _sign_ins is None:
            _sign_ins = ClaudeSignIns()
        return _sign_ins


def close_claude_sign_ins() -> None:
    global _sign_ins
    with _sign_ins_lock:
        if _sign_ins is not None:
            _sign_ins.close_all()
            _sign_ins = None
