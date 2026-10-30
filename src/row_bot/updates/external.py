"""Updates applied by the host (``ROW_BOT_UPDATES=external``, decision D11).

Docker today, and the cloud appliance's on-VM updater later, replace Row-Bot
from outside. Row-Bot then runs no checks, downloads or staging and its
agent's update tool is off. Settings shows what the host reports and turns
Update now / Skip / Pause into requests the host picks up.

A known hosting company (:mod:`row_bot.hosting`, H3) always applies the
updates itself, so naming one means this mode whatever else is set: a
hosted Row-Bot never checks, downloads or switches versions on its own.

Both sides share one folder, ``ROW_BOT_UPDATES_DIR`` or, by default,
``<data dir>/host-updates/`` (the data volume a container already mounts):

``status.json`` — written by the host, read by Row-Bot::

    {
      "schema": 1,
      "state": "idle" | "checking" | "downloading" | "ready" | "applying" | "failed" | "paused",
      "current_version": "5.2.1",
      "available_version": "5.2.2",          # or null
      "message": "One sentence for people",  # optional
      "notes_url": "https://github.com/...", # optional, https only
      "updated_at": "2026-10-11T12:00:00Z",
      "handled_request": "<id of the last request.json it acted on>"  # optional
    }

``request.json`` — written by Row-Bot (atomically; the newest request
replaces an unhandled older one), read and then deleted by the host::

    {
      "schema": 1,
      "id": "<uuid>",
      "action": "check" | "update_now" | "skip" | "pause" | "resume",
      "version": "5.2.2",                     # or null
      "requested_at": "2026-10-11T12:00:00Z"
    }

Row-Bot never executes anything on the host's behalf; the folder is the
whole interface.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Mapping

from row_bot import install_layout
from row_bot.hosting import hosted_by

UPDATES_MODE_ENV = "ROW_BOT_UPDATES"
UPDATES_DIR_ENV = "ROW_BOT_UPDATES_DIR"
STATUS_FILE = "status.json"
REQUEST_FILE = "request.json"
MAX_STATUS_BYTES = 16 * 1024
HOST_STATES = ("idle", "checking", "downloading", "ready", "applying", "failed", "paused")
HostAction = Literal["check", "update_now", "skip", "pause", "resume"]


def external_mode(environ: Mapping[str, str] | None = None) -> bool:
    """Whether the host applies updates: ``ROW_BOT_UPDATES=external`` or a known host (H3)."""
    env = os.environ if environ is None else environ
    return str(env.get(UPDATES_MODE_ENV) or "").strip().lower() == "external" or hosted_by(env) is not None


def exchange_dir(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    configured = str(env.get(UPDATES_DIR_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser()
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir(create=False) / "host-updates"


def _text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.replace("\0", "").strip()
    return cleaned[:limit] or None


def read_status(folder: Path | None = None) -> dict[str, Any]:
    """What the host last reported, bounded and validated; ``unknown`` if nothing."""
    path = (folder or exchange_dir()) / STATUS_FILE
    status: dict[str, Any] = {
        "state": "unknown",
        "current_version": None,
        "available_version": None,
        "message": None,
        "notes_url": None,
        "updated_at": None,
        "handled_request": None,
    }
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_STATUS_BYTES + 1)
    except OSError:
        return status
    if len(raw) > MAX_STATUS_BYTES:
        return status
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return status
    if not isinstance(data, dict) or data.get("schema") != 1:
        return status
    state = data.get("state")
    status["state"] = state if state in HOST_STATES else "unknown"
    for key in ("current_version", "available_version"):
        value = data.get(key)
        status[key] = value if install_layout.is_valid_version_name(value) else None
    status["message"] = _text(data.get("message"), 300)
    notes = _text(data.get("notes_url"), 2048)
    status["notes_url"] = notes if notes and notes.startswith("https://") else None
    status["updated_at"] = _text(data.get("updated_at"), 64)
    status["handled_request"] = _text(data.get("handled_request"), 64)
    return status


def read_request(folder: Path | None = None) -> dict[str, Any] | None:
    """The request still waiting for the host, if any."""
    path = (folder or exchange_dir()) / REQUEST_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != 1:
        return None
    return {
        "id": _text(data.get("id"), 64),
        "action": data.get("action") if data.get("action") in {"check", "update_now", "skip", "pause", "resume"} else None,
        "version": data.get("version") if install_layout.is_valid_version_name(data.get("version")) else None,
        "requested_at": _text(data.get("requested_at"), 64),
    }


def write_request(action: HostAction, *, version: str | None = None, folder: Path | None = None) -> dict[str, Any]:
    """Leave a request for the host (atomic replace). Returns what was written."""
    if action not in {"check", "update_now", "skip", "pause", "resume"}:
        raise ValueError(f"unknown host request {action!r}")
    if version is not None and not install_layout.is_valid_version_name(version):
        raise ValueError("not a version")
    target = folder or exchange_dir()
    target.mkdir(parents=True, exist_ok=True)
    request = {
        "schema": 1,
        "id": str(uuid.uuid4()),
        "action": action,
        "version": version,
        "requested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    handle, temporary = tempfile.mkstemp(prefix=".request.", suffix=".tmp", dir=target)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(request, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, target / REQUEST_FILE)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return request


__all__ = [
    "HOST_STATES",
    "REQUEST_FILE",
    "STATUS_FILE",
    "UPDATES_DIR_ENV",
    "UPDATES_MODE_ENV",
    "exchange_dir",
    "external_mode",
    "read_request",
    "read_status",
    "write_request",
]
