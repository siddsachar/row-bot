"""Publish each tool's generated media through the existing opaque file owner."""
from __future__ import annotations

import base64
import binascii
import logging
import os
from pathlib import Path
import re
from uuid import uuid4

from row_bot.application.attachment_context import AttachmentExecutionCaches
from row_bot.application.attachments import (
    AttachmentError, MAX_ATTACHMENT_BYTES, _conversation, _managed_root, _open, _safe_path, _write, register_attachment,
)

logger = logging.getLogger(__name__)


def save_generated_output(conversation_id: str, data: bytes, *, prefix: str, extension: str) -> str:
    """Reserve an original generated file; transport limits apply only at capture.

    The existing media owner retains large outputs. UUID names plus exclusive
    creation keep simultaneous tools from overwriting one another's originals.
    """
    from row_bot import threads
    if (not isinstance(data, bytes) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", prefix)
            or not re.fullmatch(r"[A-Za-z0-9]{1,12}", extension)):
        raise AttachmentError("invalid_command")
    _conversation(conversation_id)
    root = _managed_root(threads._MEDIA_DIR)
    folder = root / conversation_id
    _safe_path(root, folder)
    folder.mkdir(parents=True, exist_ok=True)
    _safe_path(root, folder)
    while True:
        _conversation(conversation_id)
        path = folder / f"{prefix}_{uuid4().hex}.{extension}"
        try:
            _write(root, path, data)
            return str(path)
        except FileExistsError:
            continue


def name_saved_files(text: str, saved: list[dict]) -> str:
    """A tool result that names the files it made in the chat, never their private paths (R2): each made
    output's identity leads it (``media_sources.identity``; history masking keeps a result's first line, W4),
    and a "Saved to: <path>" line goes. A working file the call kept on the way (a screenshot, a contact sheet)
    is named by its own tool's words."""
    from row_bot.media_sources import identity

    kept = [line for line in text.splitlines() if not line.startswith("Saved to: ")]
    made = [identity(item) for item in saved if item.get("role") != "working"]
    if not made and len(kept) == len(text.splitlines()):
        return text
    return "\n".join(made + kept)


def _image(conversation_id: str, encoded: str, name: str = "", role: str = "final") -> dict:
    if not isinstance(encoded, str):
        raise AttachmentError("media_unavailable")
    if len(encoded) > 4 * ((MAX_ATTACHMENT_BYTES + 2) // 3):
        raise AttachmentError("payload_too_large")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise AttachmentError("media_unavailable") from None
    if not data:
        raise AttachmentError("media_unavailable")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentError("payload_too_large")
    suffix = ".png" if data.startswith(b"\x89PNG\r\n\x1a\n") else ".jpg" if data.startswith(b"\xff\xd8\xff") else ".bin"
    return register_attachment(conversation_id, (Path(name).stem or "generated-image") + suffix, data, role=role)


def _video(conversation_id: str, metadata: dict, label: str = "") -> dict:
    from row_bot import threads
    if not isinstance(metadata, dict) or not isinstance(metadata.get("path"), str) or not metadata["path"]:
        raise AttachmentError("media_unavailable")
    root = _managed_root(threads._MEDIA_DIR)
    folder = root / conversation_id
    _safe_path(root, folder)
    path = Path(metadata["path"]).absolute()
    _safe_path(path.parent, path)
    path = path.parent.resolve() / path.name
    _safe_path(folder, path)
    with _open(folder, path) as source:
        before = os.fstat(source.fileno())
        if before.st_size > MAX_ATTACHMENT_BYTES:
            raise AttachmentError("payload_too_large")
        data = source.read(MAX_ATTACHMENT_BYTES + 1)
        after = os.fstat(source.fileno())
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise AttachmentError("payload_too_large")
        if (len(data) != before.st_size or after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns
                or len(data) < 12 or data[4:8] != b"ftyp"):
            raise AttachmentError("media_unavailable")
    saved = register_attachment(conversation_id, f"{label or 'generated-video'}.mp4", data, role="final")
    try:
        path.unlink()  # The chat's file is the clip now: one copy is kept, never two (W4).
    except OSError:
        logger.debug("A generated clip's first copy wasn't removed", exc_info=True)
    return saved


def capture_generated_media(conversation_id: str, caches: AttachmentExecutionCaches, *,
                            names: list[dict] | None = None, label: str = "") -> list[dict]:
    """Drain only this call's private slots; never expose paths. A generated clip's first copy goes once the
    chat holds it; on any failure it stays.

    The ToolNode wrapper adds native issuing message/tool-call identities to each
    outcome. A failed output cannot discard another successfully captured output.
    Each saved file's record (name, role, version) goes into *names*; *label* (from the prompt) names
    a file its tool didn't name. Each outcome says the file's role; files the call
    presented (already chat files) follow as finals, with their names.
    """
    image, name, role = caches.pending_image, caches.pending_image_name, caches.pending_image_role
    video, presented = caches.pending_video, caches.presented
    caches.pending_image, caches.pending_image_name, caches.pending_image_role = None, "", "final"
    caches.pending_video, caches.presented = None, []
    outcomes = []
    for kind, value in (("image", image), ("video", video)):
        if value is None:
            continue
        try:
            if caches.conversation_id != conversation_id:
                raise AttachmentError("action_denied")
            _conversation(conversation_id)
            result = (_image(conversation_id, value, name or label, role) if kind == "image"
                      else _video(conversation_id, value, label))
            if names is not None:
                names.append(result)
            outcomes.append({"type": "media.available", "payload": {
                "media_ref": result["attachment_ref"], "mime_type": result["mime_type"], "role": result["role"],
            }})
        except (OSError, ValueError, TypeError) as error:
            # Only the transport-size failure is actionable in a public client;
            # path/format/authority failures share a redacted unavailable code.
            code = "payload_too_large" if isinstance(error, AttachmentError) and error.code == "payload_too_large" else "media_unavailable"
            outcomes.append({"type": "media.error", "payload": {"code": code}})
    if caches.conversation_id == conversation_id:
        outcomes += [{"type": "media.available", "payload": {
            "media_ref": item["attachment_ref"], "mime_type": item["mime_type"], "role": "final",
            "name": item["name"], **({"prompt": item["caption"]} if item.get("caption") else {}),
        }} for item in presented if str(item.get("attachment_ref") or "").startswith(conversation_id + ":")]
    return outcomes
