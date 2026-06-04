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
from typing import Callable

from row_bot.terminal_pty import PtySession, detect_shell, _IS_WINDOWS

logger = logging.getLogger(__name__)

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

    def __init__(self) -> None:
        self._pty: PtySession | None = None
        self._reader_task: asyncio.Task | None = None
        self._running = False

        # Output distribution
        self._output_callbacks: list[Callable[[str], None]] = []
        self._output_lock = threading.Lock()

        # Rolling output buffer for read_terminal tool
        self._scrollback_lines: deque[str] = deque(maxlen=500)
        self._scrollback_partial: str = ""  # incomplete line accumulator

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
        buf = list(self._scrollback_lines)
        tail = buf[-lines:] if len(buf) > lines else buf
        return "\n".join(tail)

    def start(self, cols: int = 120, rows: int = 30, cwd: str | None = None) -> None:
        """Start the PTY session and the reader loop."""
        if self._pty is not None and self._pty.is_alive():
            return  # already running

        self._pty = PtySession(cols=cols, rows=rows, cwd=cwd)
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

                # Feed rolling scrollback buffer (ANSI-stripped)
                self._feed_scrollback(data)

                # Distribute to UI callbacks
                with self._output_lock:
                    for cb in self._output_callbacks:
                        try:
                            cb(data)
                        except Exception:
                            logger.debug("Output callback error", exc_info=True)

            except asyncio.CancelledError:
                break
            except Exception:
                if self._running:
                    logger.debug("Reader loop error", exc_info=True)
                    await asyncio.sleep(0.1)

    def _feed_scrollback(self, data: str) -> None:
        """Append ANSI-stripped output to the rolling scrollback buffer."""
        cleaned = strip_ansi(data)
        text = self._scrollback_partial + cleaned
        lines = text.split("\n")
        # Last element is the incomplete line (or "" if data ended with \n)
        self._scrollback_partial = lines.pop()
        for line in lines:
            self._scrollback_lines.append(line)

    # ── User input (from xterm.js) ─────────────────────────────────────

    def on_input(self, data: str) -> None:
        """Handle user keystrokes from xterm.js."""
        if self._pty is not None and self._pty.is_alive():
            self._pty.write(data)

    def on_resize(self, cols: int, rows: int) -> None:
        """Handle terminal resize from xterm.js."""
        if self._pty is not None:
            self._pty.resize(cols, rows)

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
            self._pty = PtySession(cols=cols, rows=rows, cwd=cwd)
            self._running = True
            self._status = "running"
            loop = asyncio.get_running_loop()
            self._reader_task = loop.create_task(self._reader_loop())
            logger.info("PTY restarted successfully")
        except Exception:
            self._status = "stopped"
            logger.error("PTY restart failed", exc_info=True)
