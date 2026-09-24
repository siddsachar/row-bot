"""Explicit retained copy of a conversation output in the configured workspace."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def save_output(conversation_id: str, media_ref: str) -> str:
    from row_bot.application.attachments import read_attachment
    from row_bot.application.conversation_creation import configured_workspace_root
    from row_bot.developer.storage import list_workspaces

    if not media_ref.startswith(conversation_id + ":"):
        raise ValueError("media_scope_conflict")
    metadata, data = read_attachment(media_ref)
    extension = {"image/png": "png", "image/jpeg": "jpg", "video/mp4": "mp4"}.get(
        metadata["mime_type"]
    )
    if (extension is None or not str(metadata.get("name") or "").startswith("generated-")
            or not data or len(data) > 25 * 1024 * 1024):
        raise ValueError("media_type_conflict")
    root = configured_workspace_root()
    folder = root / "Saved outputs"
    for workspace in list_workspaces():
        workspace_path = Path(workspace.path).expanduser().resolve()
        if folder.is_relative_to(workspace_path):
            raise ValueError("media_destination_is_code_folder")
    if folder.is_symlink():
        raise ValueError("media_destination_unavailable")
    folder.mkdir(exist_ok=True)
    if not folder.is_dir() or folder.resolve(strict=True) != folder:
        raise ValueError("media_destination_unavailable")
    name = f"output-{hashlib.sha256(media_ref.encode()).hexdigest()[:20]}.{extension}"
    target = folder / name
    digest = hashlib.sha256(data).hexdigest()
    if os.path.lexists(target):
        if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError("media_destination_conflict")
        return name
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return name
