"""Thoth Launcher — system-tray process that manages the NiceGUI server.

Responsibilities:
    • Splash screen while the server starts (tkinter — no extra deps)
    • System-tray icon (green = running, grey = stopped)
    • Launch  ``python app.py``  as a headless server subprocess
    • Open a pywebview native window pointing at the server
    • Closing the window keeps the server (and tasks/channels) alive
    • Detect an already-running instance and just open a window
    • Graceful shutdown on Quit
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

from app_port import DEFAULT_APP_PORT, THOTH_PORT_ENV

if TYPE_CHECKING:
    from PIL import Image as _PILImage

# ── Setup logging ────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
_PORT = DEFAULT_APP_PORT
_OLLAMA_PORT = 11434          # Ollama default API port
_STARTUP_GRACE = 15           # seconds to wait for NiceGUI before opening browser
_ICON_SIZE = 64               # px for generated tray icons


# ── Ollama auto-start ────────────────────────────────────────────────────────

def _is_ollama_running() -> bool:
    """Return True if Ollama's API is reachable on its default port."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", _OLLAMA_PORT)) == 0


def _start_ollama() -> None:
    """Auto-start Ollama if it is installed but not already running."""
    if _is_ollama_running():
        logger.info("Ollama already running on port %s", _OLLAMA_PORT)
        return

    # --- Windows: prefer the GUI app (tray icon) for better UX ---
    if sys.platform == "win32":
        app_exe = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama app.exe"
        if app_exe.exists():
            logger.info("Starting Ollama (Windows app)...")
            subprocess.Popen(
                [str(app_exe)],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            _wait_for_ollama()
            return

    # --- macOS / Linux (and Windows fallback): use `ollama serve` ---
    ollama_bin = shutil.which("ollama")
    if ollama_bin is None:
        # macOS: Homebrew may not be on PATH in a GUI launch context
        for candidate in ("/usr/local/bin/ollama", "/opt/homebrew/bin/ollama"):
            if os.path.isfile(candidate):
                ollama_bin = candidate
                break

    if ollama_bin is None:
        logger.warning(
            "Ollama not found — install it from https://ollama.com/download  "
            "Thoth requires Ollama to run local language models."
        )
        return

    logger.info("Starting Ollama (%s)...", ollama_bin)
    subprocess.Popen(
        [ollama_bin, "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # detach from launcher's process group
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    _wait_for_ollama()


def _wait_for_ollama(timeout: float = 15.0) -> None:
    """Block until Ollama's API port is reachable, or until *timeout*."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _is_ollama_running():
            logger.info("Ollama is ready")
            return
        time.sleep(0.5)
    logger.warning("Ollama did not become reachable within %.0fs", timeout)


# ── Icon generation (Pillow, no external files) ──────────────────────────────

def _make_icon(colour: str) -> _PILImage.Image:
    """Create a solid circle icon with the given colour on a transparent bg."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 4
    draw.ellipse(
        [margin, margin, _ICON_SIZE - margin, _ICON_SIZE - margin],
        fill=colour,
    )
    return img


# Pre-generate the three state icons lazily
_icons: dict[str, _PILImage.Image] = {}


def _get_icon(state: str) -> _PILImage.Image:
    """Return the icon for a launcher state string."""
    colour_map = {
        "running":  "#22c55e",   # green
    }
    colour = colour_map.get(state, "#6b7280")  # grey fallback
    if colour not in _icons:
        _icons[colour] = _make_icon(colour)
    return _icons[colour]


# ── Port check ───────────────────────────────────────────────────────────────

def _is_port_in_use(port: int = _PORT) -> bool:
    """Return True if something is already listening on *port*."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _url_for_port(port: int) -> str:
    return f"http://localhost:{port}"


def _is_thoth_server(port: int, timeout: float = 0.75) -> bool:
    """Return True if *port* is serving this Thoth app."""
    url = f"http://127.0.0.1:{port}/api/launcher-ping"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if getattr(response, "status", 200) != 200:
                return False
            data = response.read(512).decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError, TimeoutError):
        return False
    return '"app":"thoth"' in data.replace(" ", "").lower()


def _find_existing_thoth_port(start: int = _PORT, max_tries: int = 50) -> int | None:
    """Return an already-running Thoth port in the session range, if any."""
    for port in range(start, start + max_tries):
        if _is_port_in_use(port) and _is_thoth_server(port):
            return port
    return None


def _find_free_port(start: int = _PORT, max_tries: int = 50) -> int:
    """Return the first free localhost port at or above *start*."""
    for port in range(start, start + max_tries):
        if not _is_port_in_use(port):
            return port
    raise RuntimeError(f"No free Thoth app port found in {start}-{start + max_tries - 1}")


def _select_app_port(preferred: int = _PORT, max_tries: int = 50) -> tuple[int, bool]:
    """Choose the app port and whether it belongs to an existing Thoth."""
    existing = _find_existing_thoth_port(preferred, max_tries=max_tries)
    if existing is not None:
        return existing, True
    if not _is_port_in_use(preferred):
        return preferred, False
    return _find_free_port(preferred + 1, max_tries=max(1, max_tries - 1)), False


# ── NiceGUI subprocess management ───────────────────────────────────────────

class _ThothProcess:
    """Wraps the NiceGUI app subprocess."""

    def __init__(self, port: int = _PORT) -> None:
        self._proc: subprocess.Popen | None = None
        self._log_file: Path | None = None
        self.port = port

    def start(self, port: int | None = None) -> None:
        """Launch ``python app.py`` as a headless server.

        The server runs without ``--native`` so it stays alive
        independently of any UI window.  The launcher opens a
        separate pywebview window to display the UI.
        """
        app_dir = Path(__file__).resolve().parent
        app_py = app_dir / "app.py"
        if port is not None:
            self.port = port

        # Use the same Python that's running this launcher
        python = sys.executable

        cmd = [python, str(app_py)]

        # Log file for diagnosing startup crashes —
        # lives in  ~/.thoth/thoth_app.log
        log_dir = Path.home() / ".thoth"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = log_dir / "thoth_app.log"

        # Rotate previous log so crash evidence survives a restart
        if self._log_file.exists():
            prev = self._log_file.with_suffix(".log.prev")
            try:
                self._log_file.replace(prev)
                logger.info("Previous log rotated to %s", prev)
            except OSError as exc:
                logger.warning("Could not rotate log: %s", exc)

        log_fh = open(self._log_file, "w", encoding="utf-8")  # noqa: SIM115

        # Isolate from any system-wide Python site-packages
        # Force UTF-8 I/O so emoji in print() never crash on cp1252 consoles
        # THOTH_NATIVE=1 tells the app it's behind a pywebview window
        # (used by _save_export to write to ~/Downloads on macOS WebKit)
        env = {
            **os.environ,
            "PYTHONNOUSERSITE": "1",
            "PYTHONIOENCODING": "utf-8",
            "THOTH_NATIVE": "1",
            THOTH_PORT_ENV: str(self.port),
        }

        self._proc = subprocess.Popen(
            cmd,
            cwd=str(app_dir),
            env=env,
            stdout=log_fh,
            stderr=log_fh,
            # On Windows, CREATE_NO_WINDOW prevents a visible console
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        logger.info("Thoth server started (PID %s, log=%s)",
                     self._proc.pid, self._log_file)

    def stop(self) -> None:
        """Terminate the NiceGUI process."""
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        logger.info("Thoth stopped")
        self._proc = None

    @property
    def is_alive(self) -> bool:
        if self._proc is None:
            return False
        return self._proc.poll() is None


# ── Splash screen (subprocess to avoid Tcl/pystray conflicts) ────────────────

# Tkinter GUI splash — tried first.
_SPLASH_TK = r'''
import os, sys, socket, time

py_dir = os.path.dirname(sys.executable)
os.environ['PATH'] = py_dir + os.pathsep + os.environ.get('PATH', '')
if os.name == 'nt':
    if hasattr(os, 'add_dll_directory'):
        os.add_dll_directory(py_dir)
    for d in ('tcl/tcl8.6', 'tcl/tk8.6'):
        p = os.path.join(py_dir, d)
        if os.path.isdir(p):
            os.environ['TCL_LIBRARY' if 'tcl8' in d else 'TK_LIBRARY'] = p
    import ctypes
    for dll in ('tcl86t.dll', 'tk86t.dll'):
        p = os.path.join(py_dir, dll)
        if os.path.exists(p):
            try: ctypes.CDLL(p, winmode=0)
            except OSError: pass
import tkinter as tk

PORT, TIMEOUT = int(sys.argv[1]), float(sys.argv[2])
def port_ready():
    try:
        s = socket.socket(); s.settimeout(0.3)
        s.connect(("127.0.0.1", PORT)); s.close(); return True
    except OSError: return False

BG, GOLD = "#1e1e1e", "#FFD700"
root = tk.Tk(); root.overrideredirect(True); root.attributes("-topmost", True)
root.configure(bg=BG)
sx, sy = root.winfo_screenwidth(), root.winfo_screenheight()
root.geometry(f"500x300+{(sx-500)//2}+{(sy-300)//2}")
tk.Label(root, text="\U0001305F", font=("Segoe UI Emoji", 64), fg=GOLD, bg=BG).pack(pady=(40,0))
tk.Label(root, text="Thoth", font=("Segoe UI", 28, "bold"), fg=GOLD, bg=BG).pack(pady=(0,10))
lbl = tk.Label(root, text="Loading.", font=("Segoe UI", 12), fg="#aaaaaa", bg=BG); lbl.pack()
_start, _d = time.monotonic(), [0]
def _check():
    _d[0] = (_d[0] % 3) + 1; lbl.configure(text="Loading" + "." * _d[0])
    if time.monotonic() - _start > TIMEOUT or port_ready(): root.destroy(); return
    root.after(500, _check)
root.after(500, _check); root.mainloop()
'''

# Console fallback — used when tkinter is unavailable.
_SPLASH_CONSOLE = r'''
import sys, socket, time, os
PORT, TIMEOUT = int(sys.argv[1]), float(sys.argv[2])
if os.name == 'nt':
    os.system('title Thoth')
def port_ready():
    try:
        s = socket.socket(); s.settimeout(0.3)
        s.connect(("127.0.0.1", PORT)); s.close(); return True
    except OSError: return False
print("\n  Thoth — Starting...\n")
_start, _d = time.monotonic(), 0
while time.monotonic() - _start < TIMEOUT:
    if port_ready(): break
    _d = (_d % 3) + 1
    print(f"\r  Loading{'.' * _d}{'   '}", end="", flush=True)
    time.sleep(0.5)
print("\r  Ready!       ")
time.sleep(0.6)
'''


def _show_splash(port: int = _PORT, timeout: float = 60.0) -> subprocess.Popen | None:
    """Launch a splash screen subprocess.  Tries tkinter first; falls back
    to a simple console window if tkinter is unavailable."""
    log_dir = Path.home() / ".thoth"
    log_dir.mkdir(parents=True, exist_ok=True)
    splash_log = log_dir / "splash.log"

    try:
        # --- Attempt 1: tkinter GUI splash ---
        log_fh = open(splash_log, "w", encoding="utf-8")  # noqa: SIM115
        proc = subprocess.Popen(
            [sys.executable, "-c", _SPLASH_TK, str(port), str(timeout)],
            stdout=log_fh, stderr=log_fh,
        )
        time.sleep(0.5)
        if proc.poll() is None:
            return proc  # tkinter splash is running
        log_fh.close()
        # Tkinter often unavailable on macOS — silently fall back
        logger.debug("Tkinter splash exited, using console fallback")

        # --- Attempt 2: console fallback ---
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_CONSOLE
        proc = subprocess.Popen(
            [sys.executable, "-c", _SPLASH_CONSOLE, str(port), str(timeout)],
            creationflags=flags,
        )
        return proc
    except Exception as exc:
        logger.warning("Could not show splash screen: %s", exc)
        return None


# ── Native window (pywebview subprocess) ─────────────────────────────────────

_WINDOW_SCRIPT = r'''
import sys

# macOS: ensure the subprocess registers as a full GUI app so it can
# receive mouse/keyboard events.  Without this, a window re-opened from
# pystray renders but doesn't respond to input.
if sys.platform == "darwin":
    try:
        from AppKit import NSApplication, NSApp
        NSApplication.sharedApplication()
        NSApp.setActivationPolicy_(0)           # Regular (Dock) app
        NSApp.activateIgnoringOtherApps_(True)  # Bring to front
    except Exception:
        pass

import webview
import webbrowser

_NAMED_WINDOWS = {}

class _JsApi:
    """Expose Python helpers to JavaScript via window.pywebview.api."""
    def open_url(self, url):
        if isinstance(url, str) and url.lower().startswith(("https://", "http://")):
            webbrowser.open(url)

    def open_window(self, name, url, title=None, width=1600, height=900):
        if not isinstance(url, str) or not url.lower().startswith(("https://", "http://")):
            return False

        try:
            width = int(width or 1600)
            height = int(height or 900)
        except Exception:
            width, height = 1600, 900

        key = name if isinstance(name, str) else ""
        if key:
            existing = _NAMED_WINDOWS.get(key)
            if existing is not None:
                try:
                    existing.load_url(url)
                    try:
                        existing.restore()
                    except Exception:
                        pass
                    try:
                        existing.show()
                    except Exception:
                        pass
                    return True
                except Exception:
                    _NAMED_WINDOWS.pop(key, None)

        window = webview.create_window(
            title or "Thoth",
            url,
            width=width,
            height=height,
            js_api=_JS_API,
        )
        if key:
            _NAMED_WINDOWS[key] = window

            def _forget(*_args):
                _NAMED_WINDOWS.pop(key, None)

            try:
                window.events.closed += _forget
            except Exception:
                pass
        return True

    def close_window(self, name):
        key = name if isinstance(name, str) else ""
        if not key:
            return False
        window = _NAMED_WINDOWS.get(key)
        if window is None:
            return False
        try:
            window.destroy()
            _NAMED_WINDOWS.pop(key, None)
            return True
        except Exception:
            return False

    def get_clipboard(self):
        import subprocess as _sp, sys as _sys
        try:
            if _sys.platform == "darwin":
                return _sp.check_output(["pbpaste"], timeout=2).decode("utf-8", errors="replace")
            elif _sys.platform == "win32":
                r = _sp.check_output(
                    ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                    timeout=2,
                )
                return r.decode("utf-8", errors="replace").rstrip("\r\n")
            else:
                return _sp.check_output(["xclip", "-selection", "clipboard", "-o"], timeout=2).decode("utf-8", errors="replace")
        except Exception:
            return None

def _on_loaded(window):
    try:
        window.evaluate_js("""
            (function() {
                var retries = 0;
                function check() {
                    if (retries >= 20) return;
                    var body = document.body;
                    if (!body || body.innerHTML.trim() === ""
                        || body.innerText.indexOf("trying to connect") !== -1) {
                        retries++;
                        setTimeout(function() { location.reload(); }, 1500);
                    }
                }
                setTimeout(check, 2000);
            })();
        """)
    except Exception:
        pass

_JS_API = _JsApi()

url, title = sys.argv[1], sys.argv[2]
w, h = int(sys.argv[3]), int(sys.argv[4])
webview.create_window(title, url, width=w, height=h, js_api=_JS_API)
webview.start(func=_on_loaded)
'''


def _load_window_mode() -> str:
    """Read the preferred window mode from app_config.json.

    Returns ``'ask'`` (default / first launch), ``'native'``, or ``'browser'``.
    """
    config_path = Path(
        os.environ.get("THOTH_DATA_DIR", Path.home() / ".thoth")
    ) / "app_config.json"
    try:
        import json
        cfg = json.loads(config_path.read_text())
        mode = cfg.get("window_mode", "ask")
        if mode in ("ask", "native", "browser"):
            return mode
    except Exception:
        pass
    return "ask"


# Tkinter chooser dialog — shown when window_mode is "ask".
_CHOOSER_TK = r'''
import os, sys

py_dir = os.path.dirname(sys.executable)
os.environ['PATH'] = py_dir + os.pathsep + os.environ.get('PATH', '')
if os.name == 'nt':
    if hasattr(os, 'add_dll_directory'):
        os.add_dll_directory(py_dir)
    for d in ('tcl/tcl8.6', 'tcl/tk8.6'):
        p = os.path.join(py_dir, d)
        if os.path.isdir(p):
            os.environ['TCL_LIBRARY' if 'tcl8' in d else 'TK_LIBRARY'] = p
    import ctypes
    for dll in ('tcl86t.dll', 'tk86t.dll'):
        p = os.path.join(py_dir, dll)
        if os.path.exists(p):
            try: ctypes.CDLL(p, winmode=0)
            except OSError: pass
import tkinter as tk

BG, GOLD, GREY = "#1e1e1e", "#FFD700", "#aaaaaa"
choice = ["native"]

def pick(mode):
    choice[0] = mode
    root.destroy()

root = tk.Tk()
root.title("Thoth")
root.configure(bg=BG)
root.resizable(False, False)
sx, sy = root.winfo_screenwidth(), root.winfo_screenheight()
root.geometry(f"420x280+{(sx-420)//2}+{(sy-280)//2}")
root.attributes("-topmost", True)

tk.Label(root, text="\U0001305F", font=("Segoe UI Emoji", 48), fg=GOLD, bg=BG).pack(pady=(24,0))
tk.Label(root, text="How would you like to open Thoth?",
         font=("Segoe UI", 14), fg="#ffffff", bg=BG).pack(pady=(10,16))

btn_frame = tk.Frame(root, bg=BG)
btn_frame.pack()

btn_cfg = dict(font=("Segoe UI", 12), width=18, cursor="hand2",
               relief="flat", bd=0, pady=8)
tk.Button(btn_frame, text="\U0001f5d4  Native Window", bg="#2a2a3e", fg="#ffffff",
          activebackground="#3a3a5e", activeforeground="#ffffff",
          command=lambda: pick("native"), **btn_cfg).pack(side="left", padx=8)
tk.Button(btn_frame, text="\U0001f310  System Browser", bg="#2a2a3e", fg="#ffffff",
          activebackground="#3a3a5e", activeforeground="#ffffff",
          command=lambda: pick("browser"), **btn_cfg).pack(side="left", padx=8)

tk.Label(root, text="To set a default, go to Settings \u2192 System.",
         font=("Segoe UI", 10), fg=GREY, bg=BG).pack(pady=(16,0))

root.protocol("WM_DELETE_WINDOW", lambda: pick("native"))
root.mainloop()
print(choice[0])
'''

# Console fallback chooser — used when tkinter is unavailable.
_CHOOSER_CONSOLE = r'''
import sys, os
if os.name == 'nt':
    os.system('title Thoth')
print()
print("  \U0001305F Thoth")
print("  " + "-" * 36)
print()
print("  How would you like to open Thoth?")
print()
print("  1) Native Window")
print("  2) System Browser")
print()
print("  To set a default, go to")
print("  Settings \u2192 System.")
print()
while True:
    try:
        c = input("  Enter 1 or 2: ").strip()
    except (EOFError, KeyboardInterrupt):
        c = "1"
        break
    if c in ("1", "2"):
        break
    print("  Please enter 1 or 2.")
print("native" if c == "1" else "browser")
'''


def _ask_window_mode() -> str:
    """Show a tkinter chooser dialog and return ``'native'`` or ``'browser'``.

    Falls back to a console prompt if tkinter is unavailable,
    and to ``'native'`` if both fail.
    """
    # --- Attempt 1: tkinter GUI chooser ---
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _CHOOSER_TK],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            result = proc.stdout.strip().splitlines()[-1]
            if result in ("native", "browser"):
                logger.info("User chose window mode: %s (GUI)", result)
                return result
    except Exception:
        pass
    logger.debug("Tkinter chooser unavailable, using console fallback")

    # --- Attempt 2: console chooser ---
    try:
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_CONSOLE
        proc = subprocess.run(
            [sys.executable, "-c", _CHOOSER_CONSOLE],
            capture_output=True, text=True, timeout=120,
            creationflags=flags,
        )
        if proc.stdout.strip():
            result = proc.stdout.strip().splitlines()[-1]
            if result in ("native", "browser"):
                logger.info("User chose window mode: %s (console)", result)
                return result
    except Exception as exc:
        logger.warning("Window mode chooser failed: %s — defaulting to native", exc)
    return "native"


def _open_in_browser(port: int = _PORT) -> None:
    """Open the Thoth UI in the default system browser."""
    webbrowser.open(_url_for_port(port))
    logger.info("Opened Thoth in system browser on port %s", port)


def _open_window(port: int = _PORT) -> subprocess.Popen | None:
    """Open a pywebview native window pointing at the running server.

    Returns the subprocess handle, or None on failure.
    The window is a standalone process — closing it does NOT
    affect the server.
    """
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", _WINDOW_SCRIPT,
             _url_for_port(port), "Thoth", "1280", "900"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        logger.info("Native window opened (PID %s, port %s)", proc.pid, port)
        return proc
    except Exception as exc:
        logger.warning("Could not open native window: %s — falling back to browser", exc)
        webbrowser.open(_url_for_port(port))
        return None


def _wait_for_server(port: int = _PORT, timeout: float = 60.0) -> bool:
    """Block until the NiceGUI server is reachable, or *timeout* expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _is_thoth_server(port):
            return True
        time.sleep(0.3)
    return False


# ── Tray application ────────────────────────────────────────────────────────

class ThothTray:
    """System-tray icon that manages the NiceGUI server and native window."""

    def __init__(self) -> None:
        import pystray

        self._port = _PORT
        self._server = _ThothProcess(self._port)
        self._owns_server = False          # True if *we* started it
        self._window_proc: subprocess.Popen | None = None
        self._stop_event = threading.Event()

        menu = pystray.Menu(
            pystray.MenuItem("Open Thoth", self._on_open, default=True),
            pystray.MenuItem("Open in Browser", self._on_open_browser),
            pystray.MenuItem("Quit", self._on_quit),
        )
        self._icon = pystray.Icon(
            name="Thoth",
            icon=_get_icon("stopped"),
            title="Thoth — stopped",
            menu=menu,
        )

    # ── Helpers ──────────────────────────────────────────────────────────

    def _is_window_alive(self) -> bool:
        return self._window_proc is not None and self._window_proc.poll() is None

    # ── Menu callbacks ───────────────────────────────────────────────────

    def _on_open(self, icon=None, item=None) -> None:   # noqa: ARG002
        """Open (or re-open) the native window."""
        if self._is_window_alive():
            # Window is running but may be behind other apps.  Try to bring
            # it to the foreground WITHOUT killing it — killing drops the
            # WebSocket connection and makes in-flight streams appear failed.
            _brought = False
            if sys.platform == "darwin":
                try:
                    subprocess.run(
                        ["osascript", "-e",
                         "tell application \"System Events\" to set "
                         "frontmost of every process whose unix id is "
                         f"{self._window_proc.pid} to true"],
                        timeout=3, capture_output=True,
                    )
                    _brought = True
                except Exception:
                    pass
            elif sys.platform == "win32":
                try:
                    import ctypes
                    hwnd = ctypes.windll.user32.FindWindowW(None, "Thoth")
                    if hwnd:
                        SW_RESTORE = 9
                        ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
                        _brought = True
                except Exception:
                    pass
            else:
                # Linux / other — try wmctrl
                try:
                    subprocess.run(
                        ["wmctrl", "-a", "Thoth"],
                        timeout=3, capture_output=True,
                    )
                    _brought = True
                except Exception:
                    pass

            if _brought:
                logger.info("Brought existing window to front (pid %s)",
                            self._window_proc.pid)
                return  # keep existing WebSocket connection alive

            # Platform trick failed — kill and spawn fresh window.
            try:
                self._window_proc.terminate()
                self._window_proc.wait(timeout=3)
            except Exception:
                pass
            self._window_proc = None

        if self._owns_server and not self._server.is_alive:
            # Server crashed — restart it first
            logger.info("Server not running — restarting before opening window")
            self._server.stop()
            for _ in range(10):
                if not _is_port_in_use(self._port):
                    break
                time.sleep(0.5)
            self._server.start(self._port)
            _wait_for_server(self._port)

        if not self._owns_server and not _is_thoth_server(self._port):
            # External server died — just open browser and hope
            webbrowser.open(_url_for_port(self._port))
            return

        logger.info("Opening Thoth window")
        self._window_proc = _open_window(self._port)

    def _on_open_browser(self, icon=None, item=None) -> None:   # noqa: ARG002
        """Open the Thoth UI in the default system browser."""
        if _is_thoth_server(self._port):
            _open_in_browser(self._port)
        elif self._owns_server:
            logger.info("Server not running — restarting before opening browser")
            self._server.stop()
            for _ in range(10):
                if not _is_port_in_use(self._port):
                    break
                time.sleep(0.5)
            self._server.start(self._port)
            if _wait_for_server(self._port):
                _open_in_browser(self._port)
            else:
                logger.warning("Server did not restart — cannot open browser")
        else:
            _open_in_browser(self._port)

    def _on_quit(self, icon=None, item=None) -> None:    # noqa: ARG002
        logger.info("Quit requested")
        self._stop_event.set()
        # Close the window first
        if self._is_window_alive():
            try:
                self._window_proc.terminate()
                self._window_proc.wait(timeout=3)
            except Exception:
                pass
        # Then stop the server
        if self._owns_server:
            self._server.stop()
        self._icon.stop()

    # ── Background poller ────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        """Periodically check if the server is alive and update icon."""
        _POLL_INTERVAL = 3.0  # seconds
        _crash_logged = False
        while not self._stop_event.is_set():
            if self._owns_server and self._server.is_alive:
                self._icon.icon = _get_icon("running")
                self._icon.title = "Thoth — running"
                _crash_logged = False
            elif not self._owns_server and _is_thoth_server(self._port):
                self._icon.icon = _get_icon("running")
                self._icon.title = "Thoth — running"
            else:
                self._icon.icon = _get_icon("stopped")
                self._icon.title = "Thoth — stopped"
                # Log once when the server process dies unexpectedly
                if self._owns_server and not _crash_logged:
                    _crash_logged = True
                    rc = (self._server._proc.returncode
                          if self._server._proc else "?")
                    log_path = self._server._log_file or "?"
                    logger.error(
                        "Thoth server exited (code %s). "
                        "Check %s for details.", rc, log_path)
                    if self._server._log_file and self._server._log_file.exists():
                        try:
                            tail = self._server._log_file.read_text(
                                encoding="utf-8", errors="replace"
                            ).strip().splitlines()[-10:]
                            if tail:
                                logger.error("--- last lines of log ---")
                                for line in tail:
                                    logger.error("  %s", line)
                                logger.error("--- end of log ---")
                        except Exception:
                            pass

            self._stop_event.wait(_POLL_INTERVAL)

    # ── Entry point ──────────────────────────────────────────────────────

    def run(self) -> None:
        """Start the tray icon, the NiceGUI server, and a native window."""
        # Ensure Ollama is running before we start the app
        _start_ollama()

        self._port, already_running = _select_app_port()
        self._server.port = self._port

        if already_running:
            logger.info("Thoth already running on port %s", self._port)
        else:
            self._server.start(self._port)
            self._owns_server = True
            # Register cleanup in case launcher crashes
            atexit.register(self._server.stop)

            # Show splash screen while the server starts up
            _show_splash(self._port)

        # Start the status-polling thread
        poller = threading.Thread(target=self._poll_loop, daemon=True, name="tray-poll")
        poller.start()

        # Wait for server to be ready, then open UI in the preferred mode
        if _wait_for_server(self._port):
            mode = _load_window_mode()
            if mode == "ask":
                mode = _ask_window_mode()
            if mode == "browser":
                _open_in_browser(self._port)
            else:
                self._window_proc = _open_window(self._port)
        else:
            logger.warning("Server did not start in time — opening browser as fallback")
            webbrowser.open(_url_for_port(self._port))

        # Blocking — runs the tray icon's event loop on the main thread
        logger.info("Thoth tray running  (Ctrl+C or Quit menu to exit)")
        self._icon.run()


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    try:
        tray = ThothTray()
        tray.run()
    except KeyboardInterrupt:
        logger.info("Interrupted — shutting down")


if __name__ == "__main__":
    main()
