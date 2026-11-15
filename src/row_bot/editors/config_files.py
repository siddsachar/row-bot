"""Each editor's config: the exact snippet, and writing it into the editor's own file.

Formats and places, from each editor's own documentation (November 2026):

- Claude Code: ``~/.claude.json`` (under ``CLAUDE_CONFIG_DIR`` when set), top-level ``mcpServers``.
  The file is Claude Code's own: Row-Bot adds or replaces only its ``row-bot`` entry and keeps
  every other key as it was. It never reads or changes Claude Code's sign-in.
- Cursor: ``~/.cursor/mcp.json``, top-level ``mcpServers`` (``"type": "stdio"``).
- Codex: ``config.toml`` in ``CODEX_HOME`` (``~/.codex``), a ``[mcp_servers.row-bot]`` table with
  ``tool_timeout_sec`` raised from Codex's 60 seconds, so Row-Bot has time to answer.
- Zed: ``settings.json`` (``~/.config/zed``; ``%APPDATA%\\Zed`` on Windows), ``context_servers``.
  It allows comments, so Row-Bot only shows Zed's snippet; the person adds it.

No token goes into any of them: ``row-bot mcp`` reads its editor's token from the keychain. Before
a write the person sees the file and the entry it gets (``preview``); the write checks the file is
still what they saw, keeps a copy beside it first and replaces the file in one step (``write``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

from row_bot.editors.catalog import SERVER_NAME, Editor
from row_bot.editors.store import EditorError

logger = logging.getLogger(__name__)

CODEX_TOOL_TIMEOUT_SECONDS = 600
CODEX_STARTUP_TIMEOUT_SECONDS = 30
MAX_CONFIG_BYTES = 8 * 1024 * 1024
BACKUP_SUFFIX = ".row-bot-backup-"


# --- The command an editor runs ------------------------------------------------------------------------

def launch_command() -> tuple[str, list[str]]:
    """The program that runs ``row-bot mcp`` on this install, and the arguments before ``mcp``.

    An installed Row-Bot's own launcher, whose path stays the same across updates; otherwise the
    ``row-bot`` command beside this Python, or this Python itself.
    """
    from row_bot import install_layout

    layout = install_layout.detect()
    if layout.kind is install_layout.InstallKind.WINDOWS and layout.root is not None:
        launcher = layout.root / "Row-Bot.exe"
        if launcher.is_file():
            return str(launcher), []
    if layout.kind is install_layout.InstallKind.MACOS:
        host = str(os.environ.get("ROW_BOT_HOST_EXECUTABLE") or "").strip()
        if host and Path(host).is_file():
            return host, []
        if layout.seed and layout.version_root is not None:
            bundled = layout.version_root.parent / "MacOS" / "row-bot"
            if bundled.is_file():
                return str(bundled), []
    if layout.kind is install_layout.InstallKind.LINUX and layout.root is not None:
        wrapper = layout.root / "current" / "bin" / "row-bot"
        if wrapper.is_file():
            return str(wrapper), []
    script = Path(sys.executable).parent / ("row-bot.exe" if os.name == "nt" else "row-bot")
    if script.is_file():
        return str(script), []
    return sys.executable, ["-m", "row_bot.launcher"]


def _environment() -> dict[str, str]:
    """The data folder, when Row-Bot runs on one of its own: ``row-bot mcp`` must find the same one."""
    from row_bot.brand import default_data_dir
    from row_bot.data_paths import get_row_bot_data_dir

    current = get_row_bot_data_dir(create=False)
    try:
        if current.resolve() == default_data_dir().resolve():
            return {}
    except OSError:
        pass
    return {"ROW_BOT_DATA_DIR": str(current)}


def server_entry(editor: Editor) -> dict[str, Any]:
    """The entry an editor's config gets for Row-Bot."""
    command, before = launch_command()
    entry: dict[str, Any] = {"command": command, "args": [*before, "mcp", "--editor", editor.id]}
    environment = _environment()
    if environment:
        entry["env"] = environment
    if editor.id in {"claude-code", "cursor"}:
        entry = {"type": "stdio", **entry}
    return entry


# --- Where each editor keeps it ---------------------------------------------------------------------------

def config_path(editor: Editor) -> Path:
    home = Path.home()
    if editor.id == "claude-code":
        configured = str(os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
        return (Path(configured).expanduser() if configured else home) / ".claude.json"
    if editor.id == "cursor":
        return home / ".cursor" / "mcp.json"
    if editor.id == "codex":
        configured = str(os.environ.get("CODEX_HOME") or "").strip()
        return (Path(configured).expanduser() if configured else home / ".codex") / "config.toml"
    if os.name == "nt":
        appdata = str(os.environ.get("APPDATA") or "").strip()
        return (Path(appdata) if appdata else home / "AppData" / "Roaming") / "Zed" / "settings.json"
    configured = str(os.environ.get("XDG_CONFIG_HOME") or "").strip()
    return (Path(configured).expanduser() if configured else home / ".config") / "zed" / "settings.json"


def _toml_string(value: str) -> str:
    # A JSON string is a TOML basic string: quotes, backslashes and control characters escaped.
    return json.dumps(value, ensure_ascii=False)


def _toml_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else _toml_string(key)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_toml_key(str(k))} = {_toml_value(v)}" for k, v in value.items()) + " }"
    return _toml_string(str(value))


def _toml_block(table: dict[str, Any]) -> str:
    lines = [f"[mcp_servers.{SERVER_NAME}]",
             *(f"{_toml_key(key)} = {_toml_value(value)}" for key, value in table.items())]
    return "\n".join(lines) + "\n"


def _codex_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {**entry, "startup_timeout_sec": CODEX_STARTUP_TIMEOUT_SECONDS,
            "tool_timeout_sec": CODEX_TOOL_TIMEOUT_SECONDS}


def snippet(editor: Editor) -> dict[str, str]:
    """The exact text to add, where it goes, and its format."""
    entry = server_entry(editor)
    path = config_path(editor)
    if editor.id == "codex":
        return {"path": str(path), "format": "toml", "text": _toml_block(_codex_entry(entry))}
    key = "context_servers" if editor.id == "zed" else "mcpServers"
    return {"path": str(path), "format": "json",
            "text": json.dumps({key: {SERVER_NAME: entry}}, indent=2, ensure_ascii=False) + "\n"}


# --- Writing it, after the person has seen the change -----------------------------------------------------

def _fingerprint(data: bytes | None) -> str:
    return hashlib.sha256(data if data is not None else b"\0missing").hexdigest()


def _read(path: Path) -> bytes | None:
    try:
        if path.stat().st_size > MAX_CONFIG_BYTES:
            raise EditorError("config_too_large")
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise EditorError("config_unreadable") from None


_CODEX_HEADER = re.compile(r"""^\s*\[\s*mcp_servers\s*\.\s*(?:"row-bot"|'row-bot'|row-bot)\s*(?:\.[^\]]*)?\]\s*(?:#.*)?$""")
_ANY_HEADER = re.compile(r"^\s*\[")


def _without_codex_entry(text: str) -> str:
    kept, skipping = [], False
    for line in text.splitlines(keepends=True):
        if _CODEX_HEADER.match(line):
            skipping = True
            continue
        if skipping and _ANY_HEADER.match(line):
            skipping = False
        if not skipping:
            kept.append(line)
    return "".join(kept)


def _changed(editor: Editor, data: bytes | None) -> tuple[bytes, Any, Any]:
    """The file's new bytes, the entry it had (or None) and the entry it gets."""
    entry = server_entry(editor)
    if editor.id == "codex":
        try:
            text = data.decode("utf-8") if data is not None else ""
            before = tomllib.loads(text)
        except (tomllib.TOMLDecodeError, UnicodeDecodeError):
            raise EditorError("config_not_plain") from None
        servers_before = before.get("mcp_servers")
        if servers_before is not None and not isinstance(servers_before, dict):
            raise EditorError("config_not_plain")
        previous = servers_before.get(SERVER_NAME) if isinstance(servers_before, dict) else None
        stripped = _without_codex_entry(text)
        if stripped and not stripped.endswith("\n"):
            stripped += "\n"
        expected = _codex_entry(entry)
        after_text = stripped + ("\n" if stripped.strip() else "") + _toml_block(expected)
        try:
            after = tomllib.loads(after_text)
        except tomllib.TOMLDecodeError:
            raise EditorError("config_not_plain") from None
        # Only Row-Bot's entry may differ; anything else (an inline entry, a table it can't see) refuses.
        others_before = {**before, "mcp_servers": {k: v for k, v in (before.get("mcp_servers") or {}).items()
                                                   if k != SERVER_NAME}}
        others_after = {**after, "mcp_servers": {k: v for k, v in (after.get("mcp_servers") or {}).items()
                                                 if k != SERVER_NAME}}
        if not others_before["mcp_servers"]:
            others_before.pop("mcp_servers")
        if not others_after["mcp_servers"]:
            others_after.pop("mcp_servers")
        if others_before != others_after or after.get("mcp_servers", {}).get(SERVER_NAME) != expected:
            raise EditorError("config_not_plain")
        return after_text.encode("utf-8"), previous, expected
    try:
        document = json.loads(data.decode("utf-8")) if data is not None and data.strip() else {}
    except (ValueError, UnicodeDecodeError):
        raise EditorError("config_not_plain") from None
    if not isinstance(document, dict) or not isinstance(document.get("mcpServers", {}), dict):
        raise EditorError("config_not_plain")
    servers = dict(document.get("mcpServers") or {})
    previous = servers.get(SERVER_NAME)
    servers[SERVER_NAME] = entry
    document["mcpServers"] = servers
    return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8"), previous, entry


def _shown(editor: Editor, value: Any) -> str | None:
    if value is None:
        return None
    if editor.id == "codex":
        return _toml_block(value) if isinstance(value, dict) else _toml_value(value)
    return json.dumps({SERVER_NAME: value}, indent=2, ensure_ascii=False)


def preview(editor: Editor) -> dict[str, Any]:
    """What a write would change: the file, the entry it has and the one it gets. Changes nothing.

    Only Row-Bot's own entry is shown, never the rest of the file (Claude Code's holds its account).
    """
    if not editor.writes_config:
        raise EditorError("config_not_written")
    path = config_path(editor)
    data = _read(path)
    result: dict[str, Any] = {"path": str(path), "exists": data is not None, "fingerprint": _fingerprint(data),
                              "format": "toml" if editor.id == "codex" else "json"}
    try:
        _, previous, entry = _changed(editor, data)
    except EditorError as exc:
        return {**result, "action": "unavailable", "problem": exc.code, "before": None, "after": None}
    action = "unchanged" if previous == entry else "replace" if previous is not None else "add"
    return {**result, "action": action, "problem": None, "before": _shown(editor, previous),
            "after": _shown(editor, entry)}


def write(editor: Editor, fingerprint: str) -> dict[str, Any]:
    """Write Row-Bot's entry into the file the person reviewed, keeping a copy of it first."""
    if not editor.writes_config:
        raise EditorError("config_not_written")
    path = config_path(editor)
    data = _read(path)
    if _fingerprint(data) != fingerprint:
        raise EditorError("config_changed")
    content, previous, entry = _changed(editor, data)
    if data is not None and previous == entry:
        return {"path": str(path), "backup_path": None, "changed": False}
    backup = None
    path.parent.mkdir(parents=True, exist_ok=True)
    if data is not None:
        backup = path.with_name(path.name + BACKUP_SUFFIX + datetime.now().strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(path, backup)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".row-bot.tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
        if data is not None:
            shutil.copymode(path, temporary)
        if _fingerprint(_read(path)) != fingerprint:
            raise EditorError("config_changed")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    logger.info("Added Row-Bot to %s's config (%s)", editor.name, "a copy was kept" if backup else "a new file")
    return {"path": str(path), "backup_path": str(backup) if backup else None, "changed": True}
