"""The Home Board (generative UI, G5): surfaces the person pinned, kept current without the model.

A pin names a chat and a surface id. The Board always shows the latest reply in that chat that drew the
surface, so when Row-Bot (or a workflow running in that chat) draws it again, the Board has the new one.
Its sources are read again on this computer whenever the Board is shown or refreshes: no model call, no
tokens, and nothing the person sees there enters a conversation. Pins live beside Row-Bot's other local
settings (``boards.json`` in the data folder) and hold only references, never the values shown.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

MAX_PINS = 24
_LOCK = threading.Lock()


def _path() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir() / "boards.json"


def _load() -> list[dict[str, Any]]:
    path = _path()
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        value = {}
    pins = value.get("pins") if isinstance(value, dict) else None
    return [pin for pin in (pins or []) if isinstance(pin, dict)
            and all(isinstance(pin.get(key), str) for key in ("pin_id", "conversation_id", "surface_id"))]


def _save(pins: list[dict[str, Any]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps({"pins": pins}, ensure_ascii=False, indent=1), encoding="utf-8",
                             newline="\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _title(service: Any, conversation_id: str) -> str:
    try:
        metadata = service._metadata(conversation_id)  # noqa: SLF001 -- the chat's own name, as the sidebar shows it.
    except Exception:  # noqa: BLE001 -- a chat that went away shows as unavailable.
        return ""
    return str(metadata.get("title") or metadata.get("name") or "")[:160]


def board(service: Any) -> dict[str, Any]:
    """Every pin, with the latest drawing of its surface (or unavailable, when its chat or surface is gone)."""

    from row_bot.application.conversation_search import _readable
    from row_bot.application.surfaces import latest_surface, state_key

    with _LOCK:
        pins = _load()
        # A deleted chat's pins go with it: not even its title stays behind.
        kept = [pin for pin in pins if _readable(service, pin["conversation_id"])]
        if len(kept) != len(pins):
            _save(kept)
    items = []
    for pin in kept:
        found = latest_surface(pin["conversation_id"], pin["surface_id"])
        item: dict[str, Any] = {
            "pin_id": pin["pin_id"], "conversation_id": pin["conversation_id"], "surface_id": pin["surface_id"],
            "conversation_title": _title(service, pin["conversation_id"]),
            "title": str(pin.get("title") or "")[:120] or "Interface",
            "pinned_at": str(pin.get("pinned_at") or ""), "available": found is not None,
        }
        if found is not None:
            message_id, surface = found
            item.update({
                "title": surface["title"], "message_id": message_id,
                "state_key": state_key(message_id, pin["surface_id"]),
                "surface_json": json.dumps(surface, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            })
        items.append(item)
    return {"items": items}


def pin(service: Any, conversation_id: str, message_id: str, surface_id: str) -> dict[str, Any]:
    """Pin a surface a reply of this chat drew. Pinning one already on the Board moves it to the top."""

    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.application.conversation_search import _require_readable
    from row_bot.application.surfaces import reply_surface

    _require_readable(service, conversation_id)
    surface = reply_surface(conversation_id, message_id, surface_id)
    if surface is None:
        raise ClientPlatformError("invalid_command")
    with _LOCK:
        pins = [item for item in _load()
                if (item["conversation_id"], item["surface_id"]) != (conversation_id, surface_id)]
        if len(pins) >= MAX_PINS:
            raise ClientPlatformError("board_full")
        pins.insert(0, {"pin_id": f"pin-{uuid.uuid4().hex[:12]}", "conversation_id": conversation_id,
                        "surface_id": surface_id, "title": surface["title"],
                        "pinned_at": datetime.now().astimezone().isoformat(timespec="seconds")})
        _save(pins)
    return board(service)


def unpin(service: Any, pin_id: str) -> dict[str, Any]:
    with _LOCK:
        pins = _load()
        kept = [item for item in pins if item["pin_id"] != pin_id]
        if len(kept) != len(pins):
            _save(kept)
    return board(service)
