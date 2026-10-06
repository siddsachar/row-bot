"""Windows connectors: the agent connectors Windows and installed apps register in the On-device
Agent Registry (Windows Insider builds with Experimental agentic features on).

Detecting them starts no process: Windows, a new enough build, and ``odr.exe`` in the Windows
system folder (never one found on PATH). Listing runs ``odr.exe list`` only when the person
updates this catalog, and keeps the result here; searching reads that copy. Connecting is the
normal plan for the stdio command ``odr.exe mcp run --proxy <identifier>``, so consent, the
connection test, access presets and approvals apply as for any app, and Windows asks too.
Row-Bot never adds, removes or configures a connector, or provisions Windows' agent account.
"""
from __future__ import annotations

from collections.abc import Callable
import json
import logging
from pathlib import Path
import re
import subprocess
import sys

from row_bot.integrations.safe import write_atomic

logger = logging.getLogger(__name__)

MIN_BUILD = 26220  # Insider Dev/Beta; the On-device Agent Registry shipped in 26220.7344.
LIST_SECONDS = 20
MAX_OUTPUT = 4 * 1024 * 1024
MAX_CONNECTORS = 200
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@:/+-]{0,199}")


def _windows_folder() -> Path | None:
    """The Windows folder from the system itself, not an environment variable."""
    import ctypes
    buffer = ctypes.create_unicode_buffer(260)
    size = ctypes.windll.kernel32.GetSystemWindowsDirectoryW(buffer, 260)
    return Path(buffer.value) if 0 < size < 260 else None


def locate() -> Path | None:
    """``odr.exe`` when this Windows has the On-device Agent Registry; otherwise None."""
    if sys.platform != "win32" or sys.getwindowsversion().build < MIN_BUILD:
        return None
    folder = _windows_folder()
    path = folder / "System32" / "odr.exe" if folder else None
    return path if path is not None and path.is_file() else None


def _cache() -> Path:
    from row_bot.integrations import index
    return index.folder() / "windows_connectors.json"


def saved() -> list[dict]:
    """The connectors from the last update, while this computer still has the registry."""
    if locate() is None:
        return []
    try:
        value = json.loads(_cache().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = value.get("connectors") if isinstance(value, dict) else None
    return [row for row in rows if _valid(row)] if isinstance(rows, list) else []


def _valid(row: object) -> bool:
    return (isinstance(row, dict) and isinstance(row.get("identifier"), str) and bool(_IDENTIFIER.fullmatch(row["identifier"]))
            and all(isinstance(row.get(key), str) for key in ("name", "title", "description", "version")))


def parse(output: str) -> list[dict] | None:
    """Local connectors from ``odr.exe list`` (the MCP Registry schema) or the two earlier shapes:
    ``{"servers": [<server>]}`` and a bare list; None when the output is not a listing.
    Remote-only entries are skipped."""
    try:
        value = json.loads(output)
    except ValueError:
        return None
    servers = value.get("servers") if isinstance(value, dict) else value
    if not isinstance(servers, list):
        return None
    found: dict[str, dict] = {}
    for item in servers:
        server = item.get("server", item) if isinstance(item, dict) else None
        if not isinstance(server, dict):
            continue
        packages = server.get("packages") if isinstance(server.get("packages"), list) else []
        local = [p for p in packages if isinstance(p, dict) and p.get("registryType") in {"on_device", "odr"}]
        identifier = str((local[0].get("identifier") if local else "") or server.get("identifier") or "")
        if not (local or server.get("identifier")) or not _IDENTIFIER.fullmatch(identifier):
            continue
        name = str(server.get("name") or identifier)[:200]
        found.setdefault(identifier, {"identifier": identifier, "name": name,
                                      "title": str(server.get("title") or name.rpartition("/")[2] or identifier)[:160],
                                      "description": str(server.get("description") or "")[:2048],
                                      "version": str(server.get("version") or "")[:64]})
        if len(found) >= MAX_CONNECTORS:
            break
    return list(found.values())


def _run(odr: Path, *args: str) -> str:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    done = subprocess.run([str(odr), *args], capture_output=True, timeout=LIST_SECONDS, shell=False, check=False,
                          stdin=subprocess.DEVNULL, creationflags=flags)
    return done.stdout[:MAX_OUTPUT].decode("utf-8", "replace") if done.returncode == 0 else ""


def update(cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Ask Windows which connectors this computer has (read-only) and keep the list."""
    odr = locate()
    if odr is None:
        raise ValueError("source_unavailable")
    connectors = None
    for args in (("list",), ("mcp", "list")):  # Earlier builds list under ``mcp``.
        if cancelled():
            raise ValueError("cancelled")
        try:
            listed = parse(_run(odr, *args))
        except (OSError, subprocess.SubprocessError):
            logger.warning("Windows connectors could not be listed", exc_info=True)
            continue
        if listed is not None:
            connectors = listed if connectors is None or listed else connectors
        if connectors:
            break
    if connectors is None:
        raise ValueError("source_unavailable")  # The list kept from before stays.
    from row_bot.integrations import index
    index.folder(create=True)
    write_atomic(_cache(), json.dumps({"schema": 1, "connectors": connectors}, indent=2, sort_keys=True))
    return {"entries": len(connectors)}


def install(odr: Path, identifier: str) -> dict:
    """The launch recipe: Windows' own proxy for this one connector, run without a shell."""
    if not _IDENTIFIER.fullmatch(identifier):
        raise ValueError("invalid_connector")
    return {"transport": "stdio", "command": str(odr), "args": ["mcp", "run", "--proxy", identifier]}


def entries() -> list:
    from row_bot.mcp_client.marketplace import MarketplaceEntry
    odr = locate()
    if odr is None:
        return []
    return [MarketplaceEntry(id=row["identifier"], name=row["title"], description=row["description"], source="windows",
                             publisher="Windows", transport="stdio", install=install(odr, row["identifier"]),
                             metadata={"version": row["version"], "auth_mode": "none",
                                       "evidence": "Listed by Windows on this computer; Windows asks before it is used."},
                             notes=["Windows asks you to allow this connector the first time Row-Bot uses it."])
            for row in saved()]


def environment_note() -> str:
    """Why the source is hidden, for logs and the handoff; never shown as a catalog."""
    if sys.platform != "win32":
        return "not Windows"
    build = sys.getwindowsversion().build
    if build < MIN_BUILD:
        return f"Windows build {build}; needs {MIN_BUILD}.7344 or later (Insider) with Experimental agentic features on"
    return "odr.exe not found in the Windows system folder" if locate() is None else "available"

