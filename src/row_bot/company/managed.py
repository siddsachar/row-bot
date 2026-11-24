"""The company server an IT department set for this computer (Row-Bot for Teams, C5).

One managed setting per OS, written by device management (Intune, Jamf) and never by the person:

- Windows: ``HKLM\\SOFTWARE\\Policies\\Row-Bot``, value ``CompanyServer`` (REG_SZ). Machine-wide only, never
  HKCU, which a person can write.
- macOS: the managed preferences of the app's domain (``ai.row-bot.assistant``), key ``CompanyServer``, from
  ``/Library/Managed Preferences/ai.row-bot.assistant.plist`` (installed by a configuration profile). Never the
  person's own defaults.
- Linux: ``/etc/row-bot/company.json`` ``{"server": "..."}``, used only while it is owned by root and not
  world-writable.

The value is normalised and checked as a typed address is (https; plain http only on this computer's loopback).
A value that isn't valid is ignored, logged once, and Settings › Company says so. With a server set, Row-Bot
joins only there and doesn't offer Leave (``client_company``). Joining still needs the person's own sign-in.

Reading is cheap and contacts nothing: nothing here is cached, so a setting IT adds or removes applies at the
next read.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

VALUE = "CompanyServer"
WINDOWS_KEY = r"SOFTWARE\Policies\Row-Bot"
# The macOS app's bundle identifier (installer/build_mac_app.sh), so a profile's payload type is the same.
MACOS_DOMAIN = "ai.row-bot.assistant"
MACOS_PLIST = Path("/Library/Managed Preferences") / f"{MACOS_DOMAIN}.plist"
LINUX_FILE = Path("/etc/row-bot/company.json")
_MAX_BYTES = 64 * 1024

# What has been logged in this process: each problem with the setting once.
_logged: set[str] = set()


class CompanyPinned(Exception):
    """Refused: this computer's company server is set by its IT department (409 ``company_pinned``)."""

    code = "company_pinned"
    status = 409


class _Invalid(Exception):
    """The setting is there, but what it holds isn't an address (its reason is logged)."""


@dataclass(frozen=True)
class Pin:
    # The server's origin (``https://host[:port]``) when IT set a valid one.
    server: str | None = None
    # IT set something that isn't a valid address: ignored, and Settings says so.
    invalid: bool = False


def _log_once(reason: str) -> None:
    if reason not in _logged:
        _logged.add(reason)
        logger.warning("The company server set by this computer's device management was ignored (%s)", reason)


def pin() -> Pin:
    """The company server set for this computer, read now."""
    try:
        raw = _read()
    except _Invalid as invalid:
        _log_once(str(invalid))
        return Pin(invalid=True)
    if raw is None or not raw.strip():
        return Pin()
    from row_bot.company import client

    try:
        return Pin(server=client.normalise_origin(raw))
    except client.InvalidAddress as invalid:
        _log_once(f"address {invalid.reason}")
        return Pin(invalid=True)


def pinned_server() -> str | None:
    """The origin of the company server IT set for this computer, or None."""
    return pin().server


def _read() -> str | None:
    """The setting's text from this OS's managed source; None when there is none."""
    if sys.platform == "win32":
        return _windows()
    if sys.platform == "darwin":
        return _macos()
    if sys.platform.startswith("linux"):
        return _linux()
    return None


def _windows() -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, WINDOWS_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            value, kind = winreg.QueryValueEx(key, VALUE)
    except FileNotFoundError:
        return None
    except OSError as error:
        _log_once(f"registry unreadable: {type(error).__name__}")
        return None
    if kind != winreg.REG_SZ or not isinstance(value, str):
        raise _Invalid("registry value is not REG_SZ")
    return value


def _macos() -> str | None:
    import plistlib

    try:
        with MACOS_PLIST.open("rb") as handle:
            data = handle.read(_MAX_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as error:
        _log_once(f"managed preferences unreadable: {type(error).__name__}")
        return None
    if len(data) > _MAX_BYTES:
        raise _Invalid("managed preferences too large")
    try:
        preferences = plistlib.loads(data)
    except Exception:  # plistlib raises several kinds (InvalidFileException, ExpatError, ValueError).
        raise _Invalid("managed preferences not a property list") from None
    value = preferences.get(VALUE) if isinstance(preferences, dict) else None
    if value is None:
        return None
    if not isinstance(value, str):
        raise _Invalid("managed preference is not a string")
    return value


def _fstat(fd: int) -> os.stat_result:
    return os.fstat(fd)


def _linux() -> str | None:
    try:
        fd = os.open(LINUX_FILE, os.O_RDONLY)
    except FileNotFoundError:
        return None
    except OSError as error:
        _log_once(f"{LINUX_FILE} unreadable: {type(error).__name__}")
        return None
    with os.fdopen(fd, "rb") as handle:
        # The file's own owner and mode, from the file opened (not its name, which could change meanwhile).
        info = _fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & stat.S_IWOTH:
            _log_once(f"{LINUX_FILE} is not a file owned by root and closed to other users")
            return None
        data = handle.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        raise _Invalid(f"{LINUX_FILE} too large")
    try:
        document = json.loads(data)
    except ValueError:
        raise _Invalid(f"{LINUX_FILE} is not JSON") from None
    value = document.get("server") if isinstance(document, dict) else None
    if not isinstance(value, str):
        raise _Invalid(f"{LINUX_FILE} has no server address")
    return value
