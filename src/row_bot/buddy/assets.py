"""Buddy animation pack discovery and validation."""

from __future__ import annotations

import json
import logging
import os
import pathlib
import shutil
from dataclasses import dataclass, field
from typing import Any

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.runtime_paths import static_dir

logger = logging.getLogger(__name__)

_STATIC_DIR = static_dir()
_BUILTIN_PACKS_DIR = _STATIC_DIR / "buddy" / "builtins"
_DATA_DIR = get_row_bot_data_dir()
_BUDDY_STATIC_DIR = _DATA_DIR / "buddy"
_USER_PACKS_DIR = _BUDDY_STATIC_DIR / "packs"
REQUIRED_STATE_MACHINE = "ThothBuddy"
REQUIRED_INPUTS = {
    "mood",
    "energy",
    "focus",
    "alert",
    "surface",
    "trigger",
}
REQUIRED_MOTION_CLIPS = {
    "idle",
    "thinking",
    "working",
    "approval",
    "success",
    "error",
}


@dataclass(frozen=True)
class BuddyPack:
    id: str
    name: str
    riv_path: pathlib.Path
    state_machine: str
    inputs: dict[str, str]
    version: str = "1.0.0"
    status: str = "available"
    message: str = ""
    runtime: str = "rive"
    preview_path: pathlib.Path = field(default_factory=pathlib.Path)
    motion_pack_path: pathlib.Path = field(default_factory=pathlib.Path)
    default_clip: str = "idle"
    animation_map: dict[str, str] = field(default_factory=dict)
    motion_clips: dict[str, pathlib.Path] = field(default_factory=dict)

    def to_manifest(self) -> dict[str, Any]:
        manifest = {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "runtime": self.runtime,
            "status": self.status,
            "message": self.message,
        }
        if self.runtime in {"generated_motion_pack", "generated_still"}:
            manifest.update(
                {
                    "preview": str(self.preview_path).replace("\\", "/"),
                }
            )
            if self.runtime == "generated_motion_pack":
                manifest.update(
                    {
                        "motion_pack": str(self.motion_pack_path).replace("\\", "/"),
                        "default_clip": self.default_clip,
                        "animation_map": dict(self.animation_map),
                        "clips": {clip_id: str(path).replace("\\", "/") for clip_id, path in self.motion_clips.items()},
                    }
                )
        else:
            manifest.update(
                {
                    "riv": str(self.riv_path).replace("\\", "/"),
                    "state_machine": self.state_machine,
                    "inputs": dict(self.inputs),
                }
            )
        return manifest


def _load_manifest(path: pathlib.Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        logger.warning("Failed to load Buddy pack manifest %s", path, exc_info=True)
        return {}


def buddy_static_dir() -> pathlib.Path:
    """Return the user Buddy static directory served by the app."""

    return _BUDDY_STATIC_DIR


def _pack_dir_for(pack_id: str) -> pathlib.Path:
    user_pack = _USER_PACKS_DIR / pack_id
    if (user_pack / "manifest.json").exists():
        return user_pack
    return _BUILTIN_PACKS_DIR / pack_id


def _resolve_pack_asset_path(value: str, *, base_dir: pathlib.Path, pack_dir: pathlib.Path, pack_id: str) -> pathlib.Path:
    candidate = pathlib.Path(value or "").expanduser()
    if not candidate.is_absolute():
        return (base_dir / candidate).resolve()

    resolved = candidate.resolve(strict=False)
    if resolved.exists():
        return resolved

    parts = list(resolved.parts)
    lowered = [part.lower() for part in parts]
    safe_pack_id = str(pack_id or "")
    for index, part in enumerate(lowered[:-1]):
        if part != "packs":
            continue
        if index + 1 >= len(parts) or parts[index + 1] != safe_pack_id:
            continue
        mapped = pack_dir.joinpath(*parts[index + 2 :]).resolve()
        if mapped.exists():
            return mapped

    if "buddy" in lowered:
        buddy_index = lowered.index("buddy")
        mapped = _BUDDY_STATIC_DIR.joinpath(*parts[buddy_index + 1 :]).resolve()
        if mapped.exists():
            return mapped

    return resolved


def load_buddy_pack(pack_id: str = "glyph") -> BuddyPack:
    pack_dir = _pack_dir_for(pack_id)
    manifest_path = pack_dir / "manifest.json"
    manifest = _load_manifest(manifest_path)
    runtime = str(manifest.get("runtime") or "")
    if not runtime:
        has_motion_manifest = bool(manifest.get("motion_pack_path")) or (pack_dir / "motions" / "manifest.json").exists()
        if isinstance(manifest.get("clips"), dict) or str(manifest.get("status") or "") == "motion_pack_generated" or has_motion_manifest:
            runtime = "generated_motion_pack"
        elif pack_id.startswith("hatch-") and (manifest.get("preview_path") or (pack_dir / "preview.png").exists()):
            runtime = "generated_still"
        else:
            runtime = "rive"
    riv_name = str(manifest.get("riv") or manifest.get("rive_file") or "buddy.riv")
    riv_path = pack_dir / riv_name
    inputs = manifest.get("inputs") if isinstance(manifest.get("inputs"), dict) else {}
    if not inputs:
        inputs = {name: name for name in REQUIRED_INPUTS}
    motion_manifest_value = str(manifest.get("motion_pack") or manifest.get("motion_pack_path") or ("motions/manifest.json" if runtime == "generated_motion_pack" and (pack_dir / "motions" / "manifest.json").exists() else "manifest.json"))
    motion_manifest_path = _resolve_pack_asset_path(
        motion_manifest_value,
        base_dir=pack_dir,
        pack_dir=pack_dir,
        pack_id=pack_id,
    )
    motion_manifest = _load_manifest(motion_manifest_path) if motion_manifest_path != manifest_path.resolve() else manifest
    clips = motion_manifest.get("clips") if isinstance(motion_manifest.get("clips"), dict) else {}
    motion_clips: dict[str, pathlib.Path] = {}
    for clip_id, entry in clips.items():
        if not isinstance(entry, dict):
            continue
        clip_path = _resolve_pack_asset_path(
            str(entry.get("path") or f"{clip_id}.mp4"),
            base_dir=motion_manifest_path.parent,
            pack_dir=pack_dir,
            pack_id=pack_id,
        )
        motion_clips[str(clip_id)] = clip_path
    preview_value = str(manifest.get("preview") or manifest.get("preview_path") or "preview.png")
    preview_path = _resolve_pack_asset_path(
        preview_value,
        base_dir=pack_dir,
        pack_dir=pack_dir,
        pack_id=pack_id,
    )
    animation_map = motion_manifest.get("animation_map") if isinstance(motion_manifest.get("animation_map"), dict) else {}
    pack = BuddyPack(
        id=str(manifest.get("id") or pack_id),
        name=str(manifest.get("name") or "Buddy"),
        version=str(manifest.get("version") or "1.0.0"),
        riv_path=riv_path,
        state_machine=str(manifest.get("state_machine") or REQUIRED_STATE_MACHINE),
        inputs={str(k): str(v) for k, v in inputs.items()},
        runtime=runtime,
        preview_path=preview_path,
        motion_pack_path=motion_manifest_path,
        default_clip=str(motion_manifest.get("default_clip") or manifest.get("default_clip") or "idle"),
        animation_map={str(k): str(v) for k, v in animation_map.items()},
        motion_clips=motion_clips,
    )
    return validate_buddy_pack(pack)


def list_buddy_packs() -> list[BuddyPack]:
    pack_ids: set[str] = set()
    for base in (_BUILTIN_PACKS_DIR, _USER_PACKS_DIR):
        if base.exists():
            pack_ids.update(path.name for path in base.iterdir() if path.is_dir())
    packs: list[BuddyPack] = []
    for pack_id in sorted(pack_ids):
        packs.append(load_buddy_pack(pack_id))
    return packs


def delete_generated_buddy_pack(pack_id: str) -> str:
    """Delete a user-generated Hatch pack from the Buddy picker."""

    safe_pack_id = "".join(ch for ch in str(pack_id or "") if ch.isalnum() or ch in {"-", "_"}).strip("-_")
    if not safe_pack_id.startswith("hatch-"):
        raise ValueError("Only generated Hatch packs can be deleted")

    user_root = _USER_PACKS_DIR.resolve()
    pack_dir = (_USER_PACKS_DIR / safe_pack_id).resolve()
    try:
        pack_dir.relative_to(user_root)
    except ValueError as exc:
        raise ValueError("Generated Buddy pack path is invalid") from exc

    manifest_path = pack_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("Generated Buddy pack was not found")

    runtime = load_buddy_pack(safe_pack_id).runtime
    if runtime not in {"generated_motion_pack", "generated_still"}:
        raise ValueError("Only generated Hatch packs can be deleted")

    shutil.rmtree(pack_dir)
    return safe_pack_id


def install_buddy_rive_asset(source_path: str | pathlib.Path, pack_id: str = "glyph") -> BuddyPack:
    """Install a local Rive export as the active asset for a Buddy pack."""

    source = pathlib.Path(source_path).expanduser().resolve()
    if source.suffix.lower() != ".riv":
        raise ValueError("Buddy animation assets must be .riv files exported from Rive")
    if not source.exists() or not source.is_file() or source.stat().st_size == 0:
        raise ValueError("Buddy Rive asset is missing or empty")

    safe_pack_id = "".join(ch for ch in pack_id if ch.isalnum() or ch in {"-", "_"}).strip("-_") or "glyph"
    pack_dir = _USER_PACKS_DIR / safe_pack_id
    pack_dir.mkdir(parents=True, exist_ok=True)
    target = pack_dir / "buddy.riv"
    if source != target.resolve():
        shutil.copy2(source, target)

    builtin = load_buddy_pack(safe_pack_id) if (_BUILTIN_PACKS_DIR / safe_pack_id / "manifest.json").exists() else None
    manifest = {
        "id": safe_pack_id,
        "name": builtin.name if builtin else "Buddy",
        "version": builtin.version if builtin else "1.0.0",
        "riv": "buddy.riv",
        "state_machine": REQUIRED_STATE_MACHINE,
        "inputs": dict(builtin.inputs if builtin else {name: name for name in REQUIRED_INPUTS}),
    }
    (pack_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return load_buddy_pack(safe_pack_id)


def install_buddy_rive_bytes(data: bytes, filename: str, pack_id: str = "glyph") -> BuddyPack:
    if pathlib.Path(filename or "").suffix.lower() != ".riv":
        raise ValueError("Buddy animation assets must be .riv files exported from Rive")
    if not data:
        raise ValueError("Buddy Rive asset is empty")

    safe_pack_id = "".join(ch for ch in pack_id if ch.isalnum() or ch in {"-", "_"}).strip("-_") or "glyph"
    pack_dir = _USER_PACKS_DIR / safe_pack_id
    pack_dir.mkdir(parents=True, exist_ok=True)
    target = pack_dir / "buddy.riv"
    target.write_bytes(data)
    return install_buddy_rive_asset(target, pack_id=safe_pack_id)


def validate_buddy_pack(pack: BuddyPack) -> BuddyPack:
    problems: list[str] = []
    if pack.runtime in {"generated_motion_pack", "generated_still"}:
        if not pack.preview_path.exists() or not pack.preview_path.is_file() or pack.preview_path.stat().st_size == 0:
            problems.append("preview image is missing or empty")
    if pack.runtime == "generated_motion_pack":
        if not pack.motion_pack_path.exists() or not pack.motion_pack_path.is_file():
            problems.append("motion pack manifest is missing")
        missing_clips = sorted(REQUIRED_MOTION_CLIPS.difference(pack.motion_clips))
        if missing_clips:
            problems.append("missing clips: " + ", ".join(missing_clips))
        empty_clips = sorted(
            clip_id
            for clip_id, clip_path in pack.motion_clips.items()
            if not clip_path.exists() or not clip_path.is_file() or clip_path.stat().st_size == 0
        )
        if empty_clips:
            problems.append("empty or missing clip files: " + ", ".join(empty_clips))
    else:
        if pack.state_machine != REQUIRED_STATE_MACHINE:
            problems.append(f"state machine must be {REQUIRED_STATE_MACHINE}")
        missing = sorted(REQUIRED_INPUTS.difference(pack.inputs))
        if missing:
            problems.append("missing inputs: " + ", ".join(missing))
    if problems:
        return BuddyPack(
            id=pack.id,
            name=pack.name,
            version=pack.version,
            riv_path=pack.riv_path,
            state_machine=pack.state_machine,
            inputs=pack.inputs,
            status="unavailable",
            message="; ".join(problems),
            runtime=pack.runtime,
            preview_path=pack.preview_path,
            motion_pack_path=pack.motion_pack_path,
            default_clip=pack.default_clip,
            animation_map=pack.animation_map,
            motion_clips=pack.motion_clips,
        )
    return BuddyPack(
        id=pack.id,
        name=pack.name,
        version=pack.version,
        riv_path=pack.riv_path,
        state_machine=pack.state_machine,
        inputs=pack.inputs,
        status="available",
        message="Ready",
        runtime=pack.runtime,
        preview_path=pack.preview_path,
        motion_pack_path=pack.motion_pack_path,
        default_clip=pack.default_clip,
        animation_map=pack.animation_map,
        motion_clips=pack.motion_clips,
    )


def pack_static_url(pack: BuddyPack) -> str:
    return static_url_for_path(pack.riv_path, fallback=pathlib.Path("buddy") / "builtins" / pack.id / pack.riv_path.name)


def static_url_for_path(path: str | pathlib.Path, *, fallback: pathlib.Path | None = None) -> str:
    asset_path = pathlib.Path(path).expanduser().resolve()
    try:
        rel = asset_path.relative_to(_STATIC_DIR)
        return "/static/" + str(rel).replace("\\", "/")
    except ValueError:
        pass
    try:
        rel = asset_path.relative_to(_BUDDY_STATIC_DIR.resolve())
        return "/_buddy/" + str(rel).replace("\\", "/")
    except ValueError:
        if fallback is None:
            return ""
        return "/static/" + str(fallback).replace("\\", "/")
