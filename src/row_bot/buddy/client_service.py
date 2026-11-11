"""Bounded authenticated Buddy projection over existing configuration and brain.

Application commands own authorization and durable receipts. No provider, native
window, workflow cancellation or file-system path is selected by this facade.
"""
from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from itertools import islice
from typing import Any

from . import assets, config

PERSONALITIES = ("warm_mystical", "calm_focus", "playful_helper", "quiet_guardian", "curious_scholar")
BUBBLES = ("quiet", "normal", "chatty")
INTENSITIES = ("quiet", "normal", "expressive")
_FIELDS = {"visible", "collapsed", "display_name", "personality", "personality_description",
           "bubble_verbosity", "animation_intensity", "pack_id"}


class BuddyClientError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class BuddyPreferences:
    visible: bool
    collapsed: bool
    display_name: str
    personality: str
    personality_description: str
    bubble_verbosity: str
    animation_intensity: str
    pack_id: str


@dataclass(frozen=True)
class BuddyStatus:
    mood: str
    animation: str
    energy: int
    focus: int
    alert: int
    event_id: int
    label: str


@dataclass(frozen=True)
class BuddySnapshot:
    schema_version: int
    revision: str
    preferences: BuddyPreferences
    status: BuddyStatus
    placement: str = "docked"
    native_placement_retained: bool = False


@dataclass(frozen=True)
class BuddyAsset:
    id: str
    content_type: str


@dataclass(frozen=True)
class BuddyPack:
    id: str
    name: str
    revision: str
    runtime: str
    available: bool
    generated: bool
    assets: tuple[BuddyAsset, ...]
    animation_map: dict[str, str]


@dataclass(frozen=True)
class BuddyPackPage:
    revision: str
    total: int
    packs: tuple[BuddyPack, ...]
    next_cursor: str | None


def _preferences(saved: dict) -> BuddyPreferences:
    # Unrecognized legacy values stay saved; the wire exposes only safe controls.
    def option(key, choices):
        value = saved.get(key)
        return value if value in choices else choices[0]
    name = saved.get("display_name", "Buddy")
    description = saved.get("personality_description", "")
    pack = saved.get("pack_id", "glyph")
    if (not isinstance(name, str) or len(name) > 128
            or not isinstance(description, str) or len(description) > 200
            or not isinstance(pack, str) or len(pack) > 128):
        raise BuddyClientError("buddy_config_unavailable")
    return BuddyPreferences(bool(saved.get("visible", True)), bool(saved.get("collapsed", False)), name,
        option("personality", PERSONALITIES), description, option("bubble_verbosity", BUBBLES),
        option("animation_intensity", INTENSITIES), pack)


def read_buddy(*, validate: Callable[[], None]) -> BuddySnapshot:
    validate()
    saved, revision = config.read_buddy_config_revision()
    from .brain import get_buddy_brain, _EVENT_REACTIONS
    from .events import BuddyEventType
    state = get_buddy_brain().tick()
    try:
        reaction = _EVENT_REACTIONS[BuddyEventType(state.details.get("event_type"))]
    except (ValueError, KeyError):
        reaction = _EVENT_REACTIONS[BuddyEventType.IDLE]
    # Labels/details from another conversation or tool may contain private data: only fixed words show.
    label = reaction[5]
    if state.details.get("event_type") == BuddyEventType.APPROVAL_NEEDED.value and state.details.get("question"):
        label = "Row-Bot has a question"  # N4: a question waits, not an approval.
    status = BuddyStatus(str(state.mood), reaction[1], int(state.energy), int(state.focus),
        int(state.alert), int(state.event_id), label if saved.get("visible", True) else "Hidden")
    validate()
    return BuddySnapshot(1, revision, _preferences(saved), status,
                         native_placement_retained=saved.get("placement") != "docked")


def update_buddy(changes: dict[str, Any], *, expected_revision: str, command_id: str,
                 validate: Callable[[], None], checkpoint: Callable[[Any], None]) -> str:
    if not isinstance(changes, dict) or not changes or not changes.keys() <= _FIELDS:
        raise BuddyClientError("invalid_buddy_preferences")
    for key, value in changes.items():
        if key in {"visible", "collapsed"}:
            valid = type(value) is bool
        elif key == "display_name":
            valid = isinstance(value, str) and 1 <= len(value.strip()) <= 128 and "\0" not in value
        elif key == "personality_description":
            from row_bot.identity import sanitize_personality

            valid = (isinstance(value, str) and len(value) <= 200 and "\0" not in value
                     and sanitize_personality(value) == value)
        elif key == "pack_id":
            valid = isinstance(value, str) and 1 <= len(value) <= 128
        else:
            choices = {"personality": PERSONALITIES, "bubble_verbosity": BUBBLES, "animation_intensity": INTENSITIES}
            valid = isinstance(value, str) and value in choices[key]
        if not valid:
            raise BuddyClientError("invalid_buddy_preferences")
    with config._lock:
        validate()
        saved, revision = config.read_buddy_config_revision()
        if revision != expected_revision:
            raise BuddyClientError("buddy_revision_conflict")
        if "pack_id" in changes:
            pack = assets.load_buddy_pack(changes["pack_id"], strict=True)
            if pack.status != "available":
                raise BuddyClientError("buddy_pack_unavailable")
            if changes["pack_id"] != saved.get("pack_id"):
                for key in ("active_hatch_preview", "active_hatch_motion", "active_hatch_motion_pack",
                            "active_hatch_motion_clips", "latest_hatch_preview", "latest_hatch_motion",
                            "latest_hatch_motion_pack"):
                    saved.pop(key, None)
        saved.update(changes)
        return config.publish_buddy_config(saved, expected_revision=revision, command_id=command_id,
                                            validate=validate, checkpoint=checkpoint)


# Bump when the cut-out or the derived rig changes, so clients fetch them again.
_DERIVATION = b"living-derivation-1"
_DERIVED: dict[tuple[str, str], tuple[bytes, bytes]] = {}
PNG_SIGNATURE = bytes.fromhex("89504e470d0a1a0a")
_CONTENT_TYPES = {"art": "image/png", "patches": "image/png", "preview": "image/png", "rig": "application/json"}


def _pack(pack_id: str):
    pack = assets.load_buddy_pack(pack_id, strict=True)
    root = assets._pack_dir_for(pack_id)
    paths = assets.pack_files(pack)
    identities = {key: assets._buddy_asset_identity(path, root=root) for key, path in paths.items()}
    manifest = assets.read_buddy_asset(root / "manifest.json", root=root, limit=65536)
    revision = hashlib.sha256(manifest + json.dumps(identities, sort_keys=True).encode()
                              + (_DERIVATION if pack.derived else b"")).hexdigest()
    ids = sorted({*paths, "art", "rig"})
    descriptors = tuple(BuddyAsset(key, _CONTENT_TYPES[key]) for key in ids)
    # Every pack is shown as a living Buddy; a legacy pack's art and rig are derived from its still.
    return BuddyPack(pack.id, pack.name, revision, assets.LIVING_RUNTIME, pack.status == "available",
        pack.id.startswith("hatch-"), descriptors, {}), paths, root


def _derived(pack_id: str, revision: str, still: bytes) -> tuple[bytes, bytes]:
    """A legacy pack's cut-out and rig, made once per revision (its files are never written)."""
    key = (pack_id, revision)
    if key not in _DERIVED:
        from .living_art import CutOutError, derive_living

        try:
            art, rig = derive_living(still)
        except CutOutError:
            raise BuddyClientError("buddy_asset_unavailable") from None
        _DERIVED[key] = (art, json.dumps(rig, separators=(",", ":")).encode())
        while len(_DERIVED) > 8:
            _DERIVED.pop(next(iter(_DERIVED)))
    return _DERIVED[key]


def list_buddy_packs(*, cursor: str | None = None, validate: Callable[[], None]) -> BuddyPackPage:
    validate()
    ids = set()
    for root in (assets._BUILTIN_PACKS_DIR, assets._USER_PACKS_DIR):
        if root.exists():
            entries = list(islice(root.iterdir(), 4097))
            if len(entries) > 4096:
                raise BuddyClientError("buddy_pack_limit")
            ids.update(path.name for path in entries if path.is_dir())
    packs = []
    catalog_bytes = 0
    for pack_id in sorted(ids):
        if len(pack_id) > 128:
            raise BuddyClientError("buddy_pack_limit")
        try:
            packs.append(_pack(pack_id)[0])
        except (ValueError, OSError):
            packs.append(BuddyPack(pack_id, pack_id, "unavailable", "unknown", False,
                                   pack_id.startswith("hatch-"), (), {}))
        catalog_bytes += len(json.dumps(asdict(packs[-1]), ensure_ascii=False).encode("utf-8"))
        if catalog_bytes > 8 * 1024 * 1024:
            raise BuddyClientError("buddy_pack_limit")
    revision = hashlib.sha256(json.dumps([asdict(pack) for pack in packs], sort_keys=True).encode()).hexdigest()
    offset = 0
    if cursor is not None:
        try:
            if len(cursor) > 2048:
                raise ValueError
            saved_revision, offset = json.loads(base64.urlsafe_b64decode(cursor).decode("ascii"))
            if saved_revision != revision or type(offset) is not int or not 0 <= offset < len(packs):
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            raise BuddyClientError("buddy_cursor_changed") from None
    next_offset = offset
    response_bytes = 0
    while next_offset < len(packs) and next_offset - offset < 50:
        size = len(json.dumps(asdict(packs[next_offset]), ensure_ascii=False).encode("utf-8"))
        if response_bytes + size > 240 * 1024:
            if next_offset == offset:
                raise BuddyClientError("buddy_pack_limit")
            break
        response_bytes += size
        next_offset += 1
    next_cursor = (base64.urlsafe_b64encode(json.dumps([revision, next_offset]).encode()).decode()
                   if next_offset < len(packs) else None)
    validate()
    return BuddyPackPage(revision, len(packs), tuple(packs[offset:next_offset]), next_cursor)


def read_buddy_media(pack_id: str, asset_id: str, *, expected_revision: str,
                     validate: Callable[[], None]) -> tuple[bytes, str]:
    validate()
    descriptor, paths, root = _pack(pack_id)
    if descriptor.revision != expected_revision or asset_id not in {asset.id for asset in descriptor.assets}:
        raise BuddyClientError("buddy_revision_conflict")
    if asset_id in paths:
        data = assets.read_buddy_asset(paths[asset_id], root=root,
                                       limit=65536 if asset_id == "rig" else 64 * 1024 * 1024)
    else:
        art, rig = _derived(pack_id, descriptor.revision,
                            assets.read_buddy_asset(paths["preview"], root=root, limit=16 * 1024 * 1024))
        data = art if asset_id == "art" else rig
    if _pack(pack_id)[0].revision != expected_revision:
        raise BuddyClientError("buddy_revision_conflict")
    content_type = _CONTENT_TYPES[asset_id]
    if content_type == "image/png" and not data.startswith(PNG_SIGNATURE):
        raise BuddyClientError("buddy_asset_unavailable")
    if content_type == "application/json":
        try:
            if not isinstance(json.loads(data), dict):
                raise ValueError
        except ValueError:
            raise BuddyClientError("buddy_asset_unavailable") from None
    validate()
    return data, content_type
