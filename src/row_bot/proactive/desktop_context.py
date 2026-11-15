"""The desktop's state for timing, read locally: idle, locked, Focus, mic or camera in use, app kind.

Only for picking a good moment. It never reads window titles, contents or pixels, and nothing it reads
leaves the computer or is stored. Every signal may be unknown (``None``): that's a valid answer, and the
timing rules treat it as "can't tell", never as "free".

- **Windows**: idle from ``GetLastInputInfo``; locked when the input desktop isn't the person's
  (``OpenInputDesktop``); Focus from ``SHQueryUserNotificationState`` (busy, full screen or presenting;
  Focus Assist's own state has no public API, so "quiet hours" in Focus Assist reads as unknown); mic and
  camera from the privacy store's "in use now" records (``CapabilityAccessManager``); app kind from the
  foreground process's executable name.
- **macOS**: idle from ``ioreg``'s HID idle time; locked from the console session's screen-lock flag; app
  kind from the frontmost app's bundle id (AppKit). Focus and mic or camera use have no reliable reading
  without extra permissions, so they are unknown.
- Other systems: unknown.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
from collections.abc import Callable

from row_bot.proactive.model import Desktop

logger = logging.getLogger(__name__)

# Executables and bundle ids by kind: only the program's name is read, never what's in it.
_BROWSERS = ("chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "arc", "safari", "iexplore")
_EDITORS = ("code", "cursor", "windsurf", "devenv", "pycharm", "idea", "webstorm", "rider", "clion", "goland",
            "sublime_text", "notepad++", "zed", "xcode", "android studio", "studio64", "nvim", "vim", "emacs")
_MEETINGS = ("zoom", "teams", "ms-teams", "webex", "skype", "facetime", "gotomeeting", "bluejeans", "around")
_BUNDLES = {
    "browser": ("com.google.chrome", "com.microsoft.edgemac", "org.mozilla.firefox", "com.apple.safari",
                "com.brave.browser", "company.thebrowser.browser", "com.operasoftware.opera", "com.vivaldi.vivaldi"),
    "editor": ("com.microsoft.vscode", "com.todesktop.230313mzl4w4u92", "com.apple.dt.xcode", "com.jetbrains.",
               "com.sublimetext.", "dev.zed.zed", "com.exafunction.windsurf"),
    "meeting": ("us.zoom.xos", "com.microsoft.teams", "com.microsoft.teams2", "com.cisco.webexmeetingsapp",
                "com.apple.facetime", "com.webex.meetingmanager"),
}
_COMMAND_SECONDS = 2.0


def app_kind_for_program(name: str) -> str:
    """The kind of a program from its executable name (Windows) or bundle id (macOS)."""
    program = name.strip().lower().removesuffix(".exe")
    for kind, bundles in _BUNDLES.items():
        if any(program == bundle or (bundle.endswith(".") and program.startswith(bundle)) for bundle in bundles):
            return kind
    if program in _MEETINGS:
        return "meeting"
    if program in _BROWSERS:
        return "browser"
    if program in _EDITORS:
        return "editor"
    return "other" if program else ""


# --- Windows -----------------------------------------------------------------------------------------------

def _windows_idle() -> float | None:
    import ctypes
    from ctypes import wintypes

    class LastInput(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LastInput()
    info.cbSize = ctypes.sizeof(LastInput)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    elapsed = (ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
    return elapsed / 1000.0


def _windows_locked() -> bool | None:
    import ctypes

    user32 = ctypes.windll.user32
    desktop = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
    if not desktop:
        return True  # The secure desktop (lock screen, sign-in) can't be opened from the person's session.
    try:
        name = ctypes.create_unicode_buffer(256)
        needed = ctypes.c_ulong(0)
        if not user32.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed)):
            return None
        return name.value.lower() != "default"
    finally:
        user32.CloseDesktop(desktop)


def _windows_focus() -> bool | None:
    import ctypes

    state = ctypes.c_int(0)
    if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
        return None
    # 2 busy (full screen), 3 Direct3D full screen, 4 presentation mode; 5 accepts notifications; 7 an app in
    # quiet mode. Anything else (1 no session, 6 quiet time after first sign-in) can't be told.
    return {2: True, 3: True, 4: True, 7: True, 5: False}.get(state.value)


def _windows_in_use(capability: str) -> bool | None:
    import winreg

    root = rf"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\{capability}"

    def any_open(key, depth: int) -> bool:
        index = 0
        while True:
            try:
                child = winreg.EnumKey(key, index)
            except OSError:
                return False
            index += 1
            with winreg.OpenKey(key, child) as sub:
                try:
                    stop, _type = winreg.QueryValueEx(sub, "LastUsedTimeStop")
                    start, _type = winreg.QueryValueEx(sub, "LastUsedTimeStart")
                    if stop == 0 and start:
                        return True
                except OSError:
                    pass
                if depth and any_open(sub, depth - 1):
                    return True

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root) as key:
            return any_open(key, 1)  # Store apps, and desktop apps under NonPackaged.
    except OSError:
        return None


def _windows_app_kind() -> str | None:
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    window = user32.GetForegroundWindow()
    if not window:
        return None
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
    process = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        return None
    try:
        size = wintypes.DWORD(1024)
        path = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
            return None
        return app_kind_for_program(path.value.replace("\\", "/").rsplit("/", 1)[-1]) or None
    finally:
        kernel32.CloseHandle(process)


def _read_windows() -> Desktop:
    return Desktop(idle_seconds=_safely(_windows_idle), locked=_safely(_windows_locked),
                   focus=_safely(_windows_focus), mic_in_use=_safely(lambda: _windows_in_use("microphone")),
                   camera_in_use=_safely(lambda: _windows_in_use("webcam")), app_kind=_safely(_windows_app_kind),
                   notes=("Focus Assist's own quiet hours can't be read on Windows; full screen and "
                          "presenting can.",))


# --- macOS -------------------------------------------------------------------------------------------------

def _command(arguments: list[str]) -> str:
    done = subprocess.run(arguments, capture_output=True, text=True, timeout=_COMMAND_SECONDS,  # noqa: S603
                          check=False)
    return done.stdout if done.returncode == 0 else ""


def _mac_idle() -> float | None:
    found = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', _command(["/usr/sbin/ioreg", "-c", "IOHIDSystem", "-d", "4"]))
    return int(found.group(1)) / 1e9 if found else None


def _mac_locked() -> bool | None:
    text = _command(["/usr/sbin/ioreg", "-n", "Root", "-d1"])
    if not text:
        return None
    return '"CGSSessionScreenIsLocked"=Yes' in text.replace(" ", "")


def _mac_app_kind() -> str | None:
    from AppKit import NSWorkspace  # pyobjc-framework-Cocoa: a declared macOS dependency

    application = NSWorkspace.sharedWorkspace().frontmostApplication()
    bundle = str(application.bundleIdentifier() or "") if application is not None else ""
    return app_kind_for_program(bundle) or None


def _read_mac() -> Desktop:
    return Desktop(idle_seconds=_safely(_mac_idle), locked=_safely(_mac_locked), app_kind=_safely(_mac_app_kind),
                   notes=("Focus and microphone or camera use can't be read on macOS without extra permissions.",))


# --- Reading -----------------------------------------------------------------------------------------------

def _safely(read: Callable[[], object]):
    try:
        return read()
    except Exception:
        logger.debug("A desktop signal couldn't be read", exc_info=True)
        return None


def read() -> Desktop:
    """The desktop's state now, as far as this computer can tell."""
    if sys.platform == "win32":
        return _read_windows()
    if sys.platform == "darwin":
        return _read_mac()
    return Desktop(notes=("Desktop context isn't read on this system.",))

