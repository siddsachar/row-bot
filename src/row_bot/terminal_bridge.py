"""Terminal bridge — connects the PTY backend to the NiceGUI UI.

Singleton ``TerminalBridge`` manages a single PTY session exclusively
for the **user's interactive terminal** (xterm.js).  The agent never
writes commands into this PTY — it uses subprocess execution instead
(see ``tools/shell_tool.py``).

The bridge provides:

* **UI interface** — ``on_input()`` / ``on_resize()`` for user keystrokes
  from xterm.js, and output callbacks that stream PTY output to the UI.
* **Replay** — ``replay(text)`` pushes display-only text into the terminal
  output stream so agent command results are visible without touching the
  PTY's stdin.
* **Scrollback** — rolling buffer of ANSI-stripped output for the
  ``read_terminal`` agent tool.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable

from row_bot.terminal_pty import PtySession

logger = logging.getLogger(__name__)

_MAX_INPUT_BYTES = 16 * 1024
_MIN_COLS = 20
_MAX_COLS = 500
_MIN_ROWS = 5
_MAX_ROWS = 200
_OUTPUT_FRAME_BYTES = 4096
_OUTPUT_BUFFER_BYTES = 256 * 1024


class TerminalAccessError(RuntimeError):
    """Stable error for rejected or revoked native terminal authority."""


@dataclass(frozen=True)
class TerminalClientAuthority:
    """Authenticated local-native authority captured for one client lease."""

    instance_id: str
    session_id: str
    window_id: str
    window_epoch: int
    policy_revision: str
    authority_grant: str
    conversation_id: str | None = None


@dataclass(frozen=True)
class _OutputFrame:
    sequence: int
    data: str
    size: int


def _valid_terminal_authority(authority: object) -> bool:
    if not isinstance(authority, TerminalClientAuthority):
        return False
    values = (
        authority.instance_id,
        authority.session_id,
        authority.window_id,
        authority.policy_revision,
        authority.authority_grant,
    )
    return (
        type(authority.window_epoch) is int
        and 0 <= authority.window_epoch <= 2**63 - 1
        and all(isinstance(value, str) and 0 < len(value) <= 256 for value in values)
        and (authority.conversation_id is None
             or isinstance(authority.conversation_id, str)
             and 0 < len(authority.conversation_id) <= 256)
    )


def _bounded_text_chunks(data: str) -> list[tuple[str, int]]:
    """Split text without breaking Unicode while enforcing byte bounds."""
    chunks: list[tuple[str, int]] = []
    current: list[str] = []
    current_size = 0
    for character in data:
        encoded_size = len(character.encode("utf-8", errors="replace"))
        if current and current_size + encoded_size > _OUTPUT_FRAME_BYTES:
            chunks.append(("".join(current), current_size))
            current, current_size = [], 0
        current.append(character)
        current_size += encoded_size
    if current:
        chunks.append(("".join(current), current_size))
    return chunks

# ANSI escape sequence stripper for clean scrollback
_ANSI_RE = re.compile(r"""
    \x1b        # ESC
    (?:
        \[[\d;]*[a-zA-Z]           # CSI sequences  [0;31m
      | \][^\x07]*\x07             # OSC sequences  ]633;C\x07
      | \][^\x1b]*\x1b\\           # OSC sequences  ]0;title ESC\
      | [()][AB012]                # Charset selection
      | [>=<]                      # Numeric keypad mode
      | \[\?[\d;]*[hl]            # DEC private mode set/reset
      | \[[\d;]*[ABCDJKHGP]      # Cursor movement
      | \[[\d;]*m                 # SGR (colors)
      | [78]                       # Save/restore cursor
      | M                         # Reverse index
      | (?:\[|\])[\x20-\x7e]*     # Other bracket sequences
    )
""", re.VERBOSE)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


class TerminalBridge:
    """Singleton bridge between the PTY and multiple UI clients / the agent."""

    _instance: TerminalBridge | None = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> TerminalBridge:
        """Return the global singleton, creating it lazily."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def has_instance(cls) -> bool:
        """Return True if a bridge has been created."""
        return cls._instance is not None

    @classmethod
    def destroy(cls) -> None:
        """Destroy the singleton and kill the PTY."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance._shutdown()
                cls._instance = None

    def __init__(self, *, pty_factory: Callable[..., PtySession] = PtySession,
                 output_buffer_bytes: int = _OUTPUT_BUFFER_BYTES) -> None:
        if not _OUTPUT_FRAME_BYTES <= output_buffer_bytes <= 4 * 1024 * 1024:
            raise ValueError("invalid_output_buffer_size")
        self._pty_factory = pty_factory
        self._pty: PtySession | None = None
        self._reader_task: asyncio.Task | None = None
        self._running = False

        # Output distribution
        self._output_callbacks: list[Callable[[str], None]] = []
        self._output_lock = threading.Lock()
        self._output_frames: deque[_OutputFrame] = deque()
        self._output_bytes = 0
        self._output_limit = output_buffer_bytes
        self._output_sequence = 0
        self._dropped_through = 0

        # Rolling output buffer for read_terminal tool
        self._scrollback_lines: deque[str] = deque(maxlen=500)
        self._scrollback_partial: str = ""  # incomplete line accumulator
        self._scrollback_lock = threading.Lock()

        # Health status
        self._status: str = "stopped"  # "running" | "stopped" | "restarting"

    @property
    def is_running(self) -> bool:
        return self._running and self._pty is not None and self._pty.is_alive()

    @property
    def status(self) -> str:
        """Return current PTY status: 'running', 'stopped', or 'restarting'."""
        if self.is_running:
            return "running"
        return self._status

    def read_output(self, lines: int = 50) -> str:
        """Return the last *lines* of cleaned terminal output.

        Used by the ``read_terminal`` agent tool.  The buffer stores
        ANSI-stripped text so the LLM sees plain content.
        """
        lines = max(1, min(lines, 500))
        with self._scrollback_lock:
            buf = list(self._scrollback_lines)
        tail = buf[-lines:] if len(buf) > lines else buf
        return "\n".join(tail)

    def start(self, cols: int = 120, rows: int = 30, cwd: str | None = None) -> None:
        """Start the PTY session and the reader loop."""
        if self._pty is not None and self._pty.is_alive():
            return  # already running

        self._validate_size(cols, rows)
        self._pty = self._pty_factory(cols=cols, rows=rows, cwd=cwd)
        self._running = True
        self._status = "running"

        # Start the async reader
        try:
            loop = asyncio.get_running_loop()
            self._reader_task = loop.create_task(self._reader_loop())
        except RuntimeError:
            # No running loop — will be started when the first client connects
            pass

    def register_output_callback(self, callback: Callable[[str], None]) -> None:
        """Register a callback that receives PTY output chunks."""
        with self._output_lock:
            self._output_callbacks.append(callback)

    def unregister_output_callback(self, callback: Callable[[str], None]) -> None:
        """Remove a previously registered output callback."""
        with self._output_lock:
            try:
                self._output_callbacks.remove(callback)
            except ValueError:
                pass

    def _publish_output(self, data: str) -> None:
        """Record bounded output and notify a snapshot of legacy callbacks."""
        if not isinstance(data, str) or not data:
            return
        self._feed_scrollback(data)
        with self._output_lock:
            for chunk, size in _bounded_text_chunks(data):
                self._output_sequence += 1
                self._output_frames.append(_OutputFrame(self._output_sequence, chunk, size))
                self._output_bytes += size
                while self._output_bytes > self._output_limit and self._output_frames:
                    removed = self._output_frames.popleft()
                    self._output_bytes -= removed.size
                    self._dropped_through = removed.sequence
            callbacks = tuple(self._output_callbacks)
        for callback in callbacks:
            try:
                callback(data)
            except Exception:
                logger.debug("Output callback error", exc_info=True)

    async def _reader_loop(self) -> None:
        """Continuously read PTY output and distribute to callbacks."""
        while self._running and self._pty is not None:
            try:
                data = await asyncio.get_event_loop().run_in_executor(
                    None, self._pty.read, 65536
                )

                if not data:
                    await asyncio.sleep(0.02)
                    continue

                self._publish_output(data)

            except asyncio.CancelledError:
                break
            except Exception:
                if self._running:
                    logger.debug("Reader loop error", exc_info=True)
                    await asyncio.sleep(0.1)

    def _feed_scrollback(self, data: str) -> None:
        """Append ANSI-stripped output to the rolling scrollback buffer."""
        cleaned = strip_ansi(data)
        with self._scrollback_lock:
            text = self._scrollback_partial + cleaned
            lines = text.split("\n")
            # Last element is the incomplete line (or "" if data ended with \n)
            self._scrollback_partial = lines.pop()
            for line in lines:
                self._scrollback_lines.append(line)

    # ── User input (from xterm.js) ─────────────────────────────────────

    def on_input(self, data: str) -> None:
        """Handle user keystrokes from xterm.js."""
        if not isinstance(data, str) or len(data.encode("utf-8")) > _MAX_INPUT_BYTES:
            raise ValueError("invalid_terminal_input")
        if self._pty is not None and self._pty.is_alive():
            self._pty.write(data)

    def on_resize(self, cols: int, rows: int) -> None:
        """Handle terminal resize from xterm.js."""
        self._validate_size(cols, rows)
        if self._pty is not None:
            self._pty.resize(cols, rows)

    @staticmethod
    def _validate_size(cols: int, rows: int) -> None:
        if (type(cols) is not int or type(rows) is not int
                or not _MIN_COLS <= cols <= _MAX_COLS
                or not _MIN_ROWS <= rows <= _MAX_ROWS):
            raise ValueError("invalid_terminal_size")

    def _read_frames(self, cursor: int, max_bytes: int) -> dict:
        if (type(cursor) is not int or cursor < 0
                or type(max_bytes) is not int
                or not _OUTPUT_FRAME_BYTES <= max_bytes <= 64 * 1024):
            raise ValueError("invalid_terminal_cursor")
        with self._output_lock:
            if cursor > self._output_sequence:
                raise ValueError("invalid_terminal_cursor")
            truncated = cursor < self._dropped_through
            effective = max(cursor, self._dropped_through)
            frames: list[dict[str, object]] = []
            used = 0
            next_cursor = effective
            for frame in self._output_frames:
                if frame.sequence <= effective:
                    continue
                if frames and used + frame.size > max_bytes:
                    break
                frames.append({"sequence": frame.sequence, "data": frame.data})
                used += frame.size
                next_cursor = frame.sequence
            return {
                "cursor": next_cursor,
                "latest": self._output_sequence,
                "truncated": truncated,
                "frames": frames,
                "status": self.status,
            }

    def open_native_client(
        self,
        authority: TerminalClientAuthority,
        *,
        authorize: Callable[[TerminalClientAuthority], bool],
        local_owner: bool,
        direct_loopback: bool,
    ) -> NativeTerminalClient:
        """Open a revocable view onto this PTY for an authenticated local host."""
        if (not local_owner or not direct_loopback
                or not _valid_terminal_authority(authority)):
            raise TerminalAccessError("native_terminal_denied")
        try:
            allowed = authorize(authority)
        except Exception:
            allowed = False
        if not allowed:
            raise TerminalAccessError("capability_revoked")
        return NativeTerminalClient(self, authority, authorize)

    # ── Lifecycle ──────────────────────────────────────────────────────

    def _shutdown(self) -> None:
        """Internal cleanup."""
        self._running = False
        self._status = "stopped"
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None
        if self._pty is not None:
            self._pty.close()
            self._pty = None
        self._output_callbacks.clear()

    async def restart(self, cols: int = 120, rows: int = 30,
                      cwd: str | None = None) -> None:
        """Restart the PTY after a crash.  Preserves output callbacks."""
        self._status = "restarting"
        logger.info("Restarting PTY…")
        # Kill old PTY but keep callbacks
        self._running = False
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None
        if self._pty is not None:
            try:
                self._pty.close()
            except Exception:
                pass
            self._pty = None

        try:
            self._validate_size(cols, rows)
            self._pty = self._pty_factory(cols=cols, rows=rows, cwd=cwd)
            self._running = True
            self._status = "running"
            loop = asyncio.get_running_loop()
            self._reader_task = loop.create_task(self._reader_loop())
            logger.info("PTY restarted successfully")
        except Exception:
            self._status = "stopped"
            logger.error("PTY restart failed", exc_info=True)


class NativeTerminalClient:
    """Small revocable client lease over the singleton terminal owner.

    Disconnecting a client never destroys the global PTY.  Navigation, policy
    changes, and native-window closure are represented by ``authorize``
    returning false and revoke only this lease on its next operation.
    """

    def __init__(self, bridge: TerminalBridge, authority: TerminalClientAuthority,
                 authorize: Callable[[TerminalClientAuthority], bool]) -> None:
        self._bridge = bridge
        self.authority = authority
        self._authorize = authorize
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def _require_authority(self) -> None:
        if self._closed:
            raise TerminalAccessError("terminal_disconnected")
        try:
            allowed = self._authorize(self.authority)
        except Exception:
            allowed = False
        if not allowed:
            self._closed = True
            raise TerminalAccessError("capability_revoked")

    def read(self, cursor: int = 0, max_bytes: int = 64 * 1024) -> dict:
        self._require_authority()
        result = self._bridge._read_frames(cursor, max_bytes)
        self._require_authority()
        return result

    def input(self, data: str) -> None:
        self._require_authority()
        self._bridge.on_input(data)
        self._require_authority()

    def resize(self, cols: int, rows: int) -> None:
        self._require_authority()
        self._bridge.on_resize(cols, rows)
        self._require_authority()

    def disconnect(self) -> None:
        self._closed = True
