"""Sending files from a chat to the person's own devices or to a chat app (F7).

A delivery sends one or more files the conversation can name (``resolve``): a file in this
conversation's files (by its name), a recording (``rec-…``) or video edit (``edit-…``) of it, or a
file in the workspace (the code folder bound to the conversation, else the workspace folder), through
the same guards as video editing (``media_sources``): never another path.

- **To your devices.** A copy is kept for ``DEVICE_KEEP_DAYS`` days. The conversation shows it, on
  every device signed in to Row-Bot, as a download that needs the person's session and ends when the
  copy expires. Every open Row-Bot window also shows "Row-Bot sent you …" through the in-app notice
  journal (``app_notices``). There is no push channel: a device with Row-Bot closed sees the file in
  the conversation the next time it opens it. It never asks: the files go only to the person's own
  sessions, which can already open the conversation's files.
- **To a chat app** (Telegram, Discord, Slack, WhatsApp, or another channel that sends files) through
  the channel's own ``send_document``: this conversation's own chat on that app when the conversation
  came from there, otherwise the person's own chat there (the user set up for the channel). No other
  chat can be named. Each app's size limit is checked before anything is sent.

Every delivery is a small record (``send-xxxxxxxx``) the chat's card follows. One that couldn't go
keeps where its files came from, so the person can send them to their devices instead in one click.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import shutil
import stat
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Literal

logger = logging.getLogger(__name__)

DELIVERY_ID = re.compile(r"send-[0-9a-f]{8}")
Target = Literal["devices", "channel"]
Status = Literal["sent", "partly_sent", "failed"]
Problem = Literal["", "too_large", "cant_send_files", "not_running", "not_linked", "send_failed", "store_full",
                  "unavailable"]
Approval = Literal["", "not_needed", "you", "reviewer", "hands_off"]

MB = 1024 * 1024
# At most this many files in one send, and in one reply.
MAX_FILES = 10
DEVICE_FILE_LIMIT = 100 * MB
DEVICE_KEEP_DAYS = 7
DEVICE_KEEP_SECONDS = DEVICE_KEEP_DAYS * 24 * 3600
# Copies waiting for the person's devices, in all; expired ones are removed first.
DEVICE_STORE_LIMIT = 2 * 1024 * MB
# What each chat app takes from a bot, at most (the smallest of the app's own limits that apply).
CHANNEL_LIMITS = {"telegram": 50 * MB, "discord": 10 * MB, "slack": 100 * MB, "whatsapp": 16 * MB}
OTHER_CHANNEL_LIMIT = 10 * MB
KEEP_PER_CONVERSATION = 100
CHUNK_BYTES = 1024 * 1024
_FOLDER = "deliveries"
_LOCK = threading.RLock()
_RECORDS: dict[str, "Delivery"] = {}
_LOADED_ROOT: Path | None = None


def _now() -> float:
    return time.time()


class DeliveryError(ValueError):
    """A delivery or its download can't be used; ``code`` is a stable, client-safe word."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class TargetProblem(ValueError):
    """The chat app can't take these files: a problem word, plain words, and the app's limit when it matters."""

    def __init__(self, problem: Problem, detail: str, *, limit: int = 0, channel: str = "", label: str = "") -> None:
        self.problem = problem
        self.detail = detail
        self.limit = limit
        self.channel = channel
        self.label = label
        super().__init__(detail)


# ── Where files come from ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Source:
    """One file to send: a file of this conversation (its reference) or of the workspace (its path)."""

    name: str
    size_bytes: int
    mime_type: str
    ref: str = ""
    relative: str = ""
    path: Path | None = None

    def record(self) -> dict[str, Any]:
        """What a delivery keeps to find the file again (never an absolute path)."""

        return {"name": self.name, "size_bytes": self.size_bytes, "mime_type": self.mime_type, "ref": self.ref,
                "relative": self.relative}


def size_words(size: int) -> str:
    """2.1 MB, 640 KB."""

    if size >= MB:
        return f"{size / MB:.1f} MB"
    return f"{max(1, round(size / 1024))} KB"


def limit_words(size: int) -> str:
    """50 MB."""

    return f"{size // MB} MB" if size >= MB else size_words(size)


def _plain_name(name: str) -> str:
    """The name a file is sent with: its own, without the "generated-" Row-Bot gives results."""

    text = str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    text = "".join(character for character in text if ord(character) >= 32 and character not in ':*?"<>|')
    if text.startswith("generated-") and len(text) > len("generated-"):
        text = text[len("generated-"):]
    return (text or "file")[:120]


def _guess_type(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def _chat_file(conversation_id: str, value: str) -> Source | None:
    from row_bot.application.attachments import AttachmentError, list_conversation_files

    wanted = _plain_name(value).casefold()
    try:
        files = list_conversation_files(conversation_id)
    except AttachmentError:
        return None
    for item in files:  # Newest first.
        if _plain_name(str(item["name"])).casefold() == wanted or str(item["name"]).casefold() == value.casefold():
            return Source(name=_plain_name(str(item["name"])), size_bytes=int(item["size_bytes"]),
                          mime_type=str(item["mime_type"]), ref=str(item["attachment_ref"]))
    return None


def _from_reference(ref: str) -> Source:
    from row_bot.application.attachments import AttachmentError, inspect_attachment
    from row_bot.media_sources import SourceError

    try:
        metadata = inspect_attachment(ref)
    except AttachmentError:
        raise SourceError("That file can't be read any more.") from None
    return Source(name=_plain_name(str(metadata["name"])), size_bytes=int(metadata["size_bytes"]),
                  mime_type=str(metadata["mime_type"]), ref=ref)


def _workspace_file(conversation_id: str, value: str, *, required: bool) -> Source | None:
    from row_bot.media_sources import SourceError, _relative, workspace, workspace_file

    place = workspace(conversation_id)
    if place is None:
        if required:
            raise SourceError(f"{value} isn't in this chat's files, and there's no workspace folder to look in.")
        return None
    try:
        path = workspace_file(place.root, value)
    except SourceError:
        if required:
            raise
        return None
    try:
        info = path.lstat()
    except OSError:
        raise SourceError(f"{value} can't be read.") from None
    if not stat.S_ISREG(info.st_mode):
        raise SourceError(f"{value} isn't a file.")
    relative = _relative(place.root, str(path)).replace("\\", "/")
    return Source(name=_plain_name(path.name), size_bytes=int(info.st_size), mime_type=_guess_type(path.name),
                  relative=relative, path=path)


def resolve(values: list[str], conversation_id: str) -> list[Source]:
    """The files these names point to, in order; ``SourceError`` (plain words) for one that can't be sent.

    A bare name is looked for in this conversation's files first, then in the workspace; a path (with a
    folder) only in the workspace; ``rec-…`` and ``edit-…`` are this conversation's recordings and edits.
    """

    from row_bot.media_sources import SourceError

    found: list[Source] = []
    for raw in values:
        value = str(raw or "").strip().strip('"')
        if not value:
            raise SourceError("A file name is empty.")
        from row_bot import recordings, video_edits

        if recordings.RECORDING_ID.fullmatch(value) or video_edits.EDIT_ID.fullmatch(value):
            from row_bot.media_sources import resolve as resolve_media

            found.extend(_from_reference(media.ref) for media in resolve_media(value, conversation_id) if media.ref)
            continue
        path_like = "/" in value or "\\" in value or Path(value).is_absolute() or (len(value) > 1 and value[1] == ":")
        source = None if path_like else _chat_file(conversation_id, value)
        if source is None:
            source = _workspace_file(conversation_id, value, required=path_like)
        if source is None:
            raise SourceError(f"Couldn't find {value} in this chat's files or the workspace. Name a file in this "
                              "chat, a workspace path, a recording (rec-…) or a video edit (edit-…).")
        found.append(source)
    for source in found:
        if source.size_bytes <= 0:
            raise SourceError(f"{source.name} is empty, so there's nothing to send.")
    return found


def _sources_again(conversation_id: str, kept: list[dict[str, Any]]) -> list[Source]:
    """The files a delivery kept, found again through the same guards (a workspace file may have changed)."""

    from row_bot.media_sources import SourceError

    found: list[Source] = []
    for item in kept:
        ref, relative = str(item.get("ref") or ""), str(item.get("relative") or "")
        if ref:
            found.append(_from_reference(ref))
        elif relative:
            source = _workspace_file(conversation_id, relative, required=True)
            assert source is not None
            found.append(source)
        else:
            raise SourceError("That file can't be found any more.")
    return found


def _copy(source: Source, destination: Path, limit: int) -> int:
    """Copy one file's bytes, never more than *limit*; the copy is new and never through a link."""

    from row_bot.media_sources import SourceError

    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
                         | getattr(os, "O_NOFOLLOW", 0), 0o600)
    written = 0
    try:
        with os.fdopen(descriptor, "wb") as output:
            if source.ref:
                from row_bot.application.attachments import AttachmentError, read_attachment

                try:
                    data = read_attachment(source.ref)[1]
                except AttachmentError:
                    raise SourceError(f"{source.name} can't be read any more.") from None
                if len(data) > limit:
                    raise SourceError(f"{source.name} is larger than {limit_words(limit)}.")
                output.write(data)
                written = len(data)
            else:
                assert source.path is not None
                flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                try:
                    handle = os.open(source.path, flags)
                except OSError:
                    raise SourceError(f"{source.name} can't be read.") from None
                with os.fdopen(handle, "rb") as reading:
                    if not stat.S_ISREG(os.fstat(reading.fileno()).st_mode):
                        raise SourceError(f"{source.name} isn't a file.")
                    while chunk := reading.read(CHUNK_BYTES):
                        written += len(chunk)
                        if written > limit:
                            raise SourceError(f"{source.name} is larger than {limit_words(limit)}.")
                        output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return written


# ── Records ─────────────────────────────────────────────────────────────────


@dataclass
class DeliveredFile:
    name: str
    size_bytes: int
    mime_type: str
    sent: bool = False


@dataclass
class Delivery:
    delivery_id: str
    conversation_id: str
    target: Target
    created_at: float
    files: list[DeliveredFile] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    channel: str = ""
    channel_label: str = ""
    place: str = ""
    shared: bool = False
    status: Status = "sent"
    problem: Problem = ""
    detail: str = ""
    limit_bytes: int = 0
    expires_at: float | None = None
    approval: Approval = ""
    sent_instead: str = ""

    def expired(self, now: float | None = None) -> bool:
        return self.target == "devices" and self.expires_at is not None and (now or _now()) >= self.expires_at


def deliveries_root() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir() / _FOLDER


def _conversation_folder(conversation_id: str) -> Path:
    from row_bot.tool_outputs import thread_folder_name

    return deliveries_root() / thread_folder_name(conversation_id)


def _record_path(delivery: Delivery) -> Path:
    return _conversation_folder(delivery.conversation_id) / f"{delivery.delivery_id}.json"


def _files_folder(delivery: Delivery) -> Path:
    return _conversation_folder(delivery.conversation_id) / delivery.delivery_id


def _save(delivery: Delivery) -> None:
    path = _record_path(delivery)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.name}.{os.getpid()}.part")
        partial.write_text(json.dumps(asdict(delivery)), encoding="utf-8", newline="\n")
        os.replace(partial, path)
    except OSError:
        logger.warning("Could not save a delivery's record", exc_info=True)


def _remove(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()
    except OSError:
        logger.debug("Could not remove %s", path.name, exc_info=True)


def _load(path: Path) -> Delivery | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not DELIVERY_ID.fullmatch(str(data.get("delivery_id") or "")) or data.get("target") not in {
                "devices", "channel"}:
            return None
        files = [DeliveredFile(**item) for item in data.pop("files", [])]
        return Delivery(files=files, **data)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def _ensure_loaded() -> None:
    global _LOADED_ROOT
    root = deliveries_root()
    with _LOCK:
        if _LOADED_ROOT == root:
            return
        _RECORDS.clear()
        _LOADED_ROOT = root
        if not root.is_dir():
            return
        for path in sorted(root.glob("*/send-*.json")):
            delivery = _load(path)
            if delivery is not None:
                _RECORDS[delivery.delivery_id] = delivery


def sweep() -> int:
    """Remove the copies of expired deliveries and anything a send cut short left; how many went."""

    global _LOADED_ROOT
    removed = 0
    with _LOCK:
        _LOADED_ROOT = None
        _ensure_loaded()
        records = list(_RECORDS.values())
    for delivery in records:
        folder = _files_folder(delivery)
        if delivery.target == "channel" or delivery.expired():
            if folder.exists():
                _remove(folder)
                removed += 1
    return removed


def get(delivery_id: str) -> Delivery | None:
    if not DELIVERY_ID.fullmatch(str(delivery_id or "")):
        return None
    _ensure_loaded()
    with _LOCK:
        return _RECORDS.get(delivery_id)


def list_deliveries(conversation_id: str | None = None) -> list[Delivery]:
    """Newest first."""

    _ensure_loaded()
    with _LOCK:
        items = [item for item in _RECORDS.values() if conversation_id is None
                 or item.conversation_id == conversation_id]
    return sorted(items, key=lambda item: item.created_at, reverse=True)


def _forget_old(conversation_id: str) -> None:
    for delivery in list_deliveries(conversation_id)[KEEP_PER_CONVERSATION:]:
        with _LOCK:
            _RECORDS.pop(delivery.delivery_id, None)
        _remove(_files_folder(delivery))
        _remove(_record_path(delivery))


def delete_conversation(conversation_id: str) -> int:
    """Forget a deleted conversation's deliveries and remove their copies."""

    _ensure_loaded()
    with _LOCK:
        for delivery_id in [key for key, item in _RECORDS.items() if item.conversation_id == conversation_id]:
            _RECORDS.pop(delivery_id, None)
    folder = _conversation_folder(conversation_id)
    if not folder.exists():
        return 0
    _remove(folder)
    return 1


def _new(conversation_id: str, target: Target, sources: list[Source], **fields: Any) -> Delivery:
    delivery = Delivery(
        delivery_id=f"send-{uuid.uuid4().hex[:8]}", conversation_id=conversation_id, target=target,
        created_at=_now(), sources=[source.record() for source in sources],
        files=[DeliveredFile(source.name, source.size_bytes, source.mime_type) for source in sources], **fields)
    with _LOCK:
        _ensure_loaded()
        _RECORDS[delivery.delivery_id] = delivery
    return delivery


def _finish(delivery: Delivery) -> Delivery:
    _save(delivery)
    _forget_old(delivery.conversation_id)
    return delivery


# ── To the person's devices ─────────────────────────────────────────────────


def _stored_bytes() -> int:
    total = 0
    for delivery in list_deliveries():
        if delivery.target != "devices" or delivery.expired():
            continue
        folder = _files_folder(delivery)
        for index, item in enumerate(delivery.files):
            if item.sent and (folder / str(index)).exists():
                total += item.size_bytes
    return total


def _notify_devices(delivery: Delivery) -> None:
    """ "Row-Bot sent you report.pdf" in every open Row-Bot window, with a way to the conversation."""

    sent = [item.name for item in delivery.files if item.sent]
    if not sent:
        return
    title = f"Row-Bot sent you {sent[0]}" if len(sent) == 1 else f"Row-Bot sent you {len(sent)} files"
    try:
        from row_bot.application.app_notices import app_notices

        app_notices.post(title=title, message="", level="info", source="deliveries", requested=True,
                         conversation_id=delivery.conversation_id)
    except Exception:
        logger.debug("The delivery notice was not posted", exc_info=True)


def device_problem(sources: list[Source]) -> TargetProblem | None:
    """Why these files can't go to the person's devices, or None."""

    too_big = [source for source in sources if source.size_bytes > DEVICE_FILE_LIMIT]
    if too_big:
        source = too_big[0]
        return TargetProblem("too_large", f"{source.name} ({size_words(source.size_bytes)}) is too big to send to "
                             f"your devices ({limit_words(DEVICE_FILE_LIMIT)} limit).", limit=DEVICE_FILE_LIMIT)
    return None


def send_to_devices(conversation_id: str, sources: list[Source], *, approval: Approval = "not_needed") -> Delivery:
    """Keep a copy of each file for the person's devices, and tell every open Row-Bot window."""

    sweep_expired()
    delivery = _new(conversation_id, "devices", sources, approval=approval,
                    expires_at=_now() + DEVICE_KEEP_SECONDS)
    problem = device_problem(sources)
    if problem is None and _stored_bytes() + sum(source.size_bytes for source in sources) > DEVICE_STORE_LIMIT:
        problem = TargetProblem("store_full", f"Row-Bot is already holding {limit_words(DEVICE_STORE_LIMIT)} of "
                                f"files for your devices. They clear out {DEVICE_KEEP_DAYS} days after they're sent.")
    if problem is not None:
        delivery.status, delivery.problem, delivery.detail = "failed", problem.problem, problem.detail
        delivery.limit_bytes, delivery.expires_at = problem.limit, None
        return _finish(delivery)
    from row_bot.media_sources import SourceError

    folder = _files_folder(delivery)
    for index, source in enumerate(sources):
        try:
            delivery.files[index].size_bytes = _copy(source, folder / str(index), DEVICE_FILE_LIMIT)
            delivery.files[index].sent = True
        except (SourceError, OSError) as exc:
            delivery.detail = str(exc) if isinstance(exc, SourceError) else f"{source.name} couldn't be copied."
            delivery.problem = "unavailable"
    sent = sum(item.sent for item in delivery.files)
    delivery.status = "sent" if sent == len(delivery.files) else "partly_sent" if sent else "failed"
    if not sent:
        delivery.expires_at = None
        _remove(folder)
    _finish(delivery)
    _notify_devices(delivery)
    return delivery


def sweep_expired() -> None:
    for delivery in list_deliveries():
        if delivery.expired() and _files_folder(delivery).exists():
            _remove(_files_folder(delivery))


def open_file(delivery_id: str, index: int) -> tuple[str, int, BinaryIO]:
    """One sent file for a download: its name, size and an open handle. Refused once its copy expired."""

    delivery = get(delivery_id)
    if delivery is None or delivery.target != "devices" or not 0 <= index < len(delivery.files):
        raise DeliveryError("sent_file_unavailable")
    item = delivery.files[index]
    if not item.sent:
        raise DeliveryError("sent_file_unavailable")
    folder = _files_folder(delivery)
    if delivery.expired():
        _remove(folder)
        raise DeliveryError("sent_file_expired")
    path = folder / str(index)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size != item.size_bytes:
            raise DeliveryError("sent_file_unavailable")
        handle = open(path, "rb")  # noqa: SIM115 - the route closes it once streamed.
    except OSError:
        raise DeliveryError("sent_file_unavailable") from None
    return item.name, item.size_bytes, handle


def read_file(delivery_id: str, index: int) -> tuple[str, bytes]:
    """One sent file's bytes (for saving into Exports on this computer)."""

    name, _size, handle = open_file(delivery_id, index)
    with handle:
        return name, handle.read(DEVICE_FILE_LIMIT + 1)


# ── To a chat app ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChannelPlan:
    """Where a chat app would send files: the channel, its chat, and that chat in words."""

    channel: Any
    name: str
    label: str
    target: str | int
    place: str
    shared: bool
    limit: int


_OWN_PLACES = {"telegram": "your Telegram chat", "discord": "your Discord DMs", "slack": "your Slack DMs",
               "whatsapp": "your WhatsApp chat"}


def _here_place(name: str, label: str, target: str, shared: bool) -> str:
    if name == "slack":
        return "this Slack thread" if ":" in target else ("this Slack channel" if shared else "your Slack DMs")
    if name == "discord":
        return "this Discord channel" if shared else "your Discord DMs"
    if name in {"telegram", "whatsapp"}:
        return f"this {label} group" if shared else f"this {label} chat"
    return f"this {label} chat"


def channel_limit(name: str) -> int:
    return CHANNEL_LIMITS.get(name, OTHER_CHANNEL_LIMIT)


def available_channels() -> list[str]:
    """Channels set up here, by name, for a plain refusal."""

    try:
        from row_bot.channels import registry

        return [channel.display_name for channel in registry.all_channels() if channel.is_configured()]
    except Exception:
        return []


def plan_channel(conversation_id: str, name: str) -> ChannelPlan:
    """The chat this conversation would send files to on *name*: its own chat there, else the person's own.

    Raises ``KeyError`` for a channel that isn't set up here at all, and ``TargetProblem`` (with words
    for the person) for one that can't send files now.
    """

    from row_bot.channels import registry

    wanted = str(name or "").strip().casefold()
    channel = registry.get(wanted) or next(
        (item for item in registry.all_channels() if item.display_name.casefold() == wanted), None)
    if channel is None:
        raise KeyError(name)
    label = str(channel.display_name or channel.name)
    limit = channel_limit(channel.name)
    if not channel.capabilities.document_out:
        raise TargetProblem("cant_send_files", f"{label} can't send files from Row-Bot.", channel=channel.name,
                            label=label)
    if not channel.is_running():
        raise TargetProblem("not_running", f"{label} isn't connected right now. Start it in Settings › Channels.",
                            channel=channel.name, label=label)
    ref = None
    try:
        from row_bot.tasks import get_thread_channel_ref

        ref = get_thread_channel_ref(conversation_id)
    except Exception:
        logger.debug("Channel origin unavailable", exc_info=True)
    if ref and str(ref.get("channel") or "") == channel.name and str(ref.get("target") or ""):
        target = str(ref["target"])
        shared = bool(int(ref.get("shared") or 0))
        return ChannelPlan(channel, channel.name, label, target, _here_place(channel.name, label, target, shared),
                           shared, limit)
    try:
        target = channel.get_default_target()
    except Exception:
        target = None
    if target in ("", None):
        raise TargetProblem("not_linked", f"{label} doesn't know which chat is yours yet. Set your user in "
                            "Settings › Channels, or message Row-Bot there once.", channel=channel.name, label=label)
    return ChannelPlan(channel, channel.name, label, target, _OWN_PLACES.get(channel.name, f"your {label} chat"),
                       False, limit)


def channel_problem(plan: ChannelPlan, sources: list[Source]) -> TargetProblem | None:
    """Why the chat app can't take these files (too big), or None."""

    too_big = [source for source in sources if source.size_bytes > plan.limit]
    if too_big:
        source = too_big[0]
        return TargetProblem("too_large", f"{source.name} ({size_words(source.size_bytes)}) is too big for "
                             f"{plan.label} ({limit_words(plan.limit)} limit).", limit=plan.limit)
    return None


def not_sent(conversation_id: str, sources: list[Source], problem: TargetProblem, *, channel: str = "",
             label: str = "", place: str = "", target: Target = "channel") -> Delivery:
    """A delivery that couldn't go, kept so its card says why and offers the person's devices instead."""

    delivery = _new(conversation_id, target, sources, channel=channel, channel_label=label, place=place)
    delivery.status, delivery.problem, delivery.detail = "failed", problem.problem, problem.detail
    delivery.limit_bytes = problem.limit
    return _finish(delivery)


def _sending_name(folder: Path, name: str, used: set[str]) -> Path:
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""
    candidate, number = name, 1
    while candidate.casefold() in used:
        number += 1
        candidate = f"{stem} ({number}){dot}{suffix}"
    used.add(candidate.casefold())
    return folder / candidate


def send_to_channel(conversation_id: str, plan: ChannelPlan, sources: list[Source], *, approval: Approval,
                    caption: str = "") -> Delivery:
    """Send each file through the channel's own ``send_document``, with each file's own name."""

    from row_bot.media_sources import SourceError

    delivery = _new(conversation_id, "channel", sources, channel=plan.name, channel_label=plan.label,
                    place=plan.place, shared=plan.shared, approval=approval, limit_bytes=plan.limit)
    outgoing = _files_folder(delivery) / "outgoing"
    used: set[str] = set()
    failed: list[str] = []
    try:
        for index, source in enumerate(sources):
            path = _sending_name(outgoing, source.name, used)
            try:
                _copy(source, path, plan.limit)
            except (SourceError, OSError) as exc:
                failed.append(str(exc) if isinstance(exc, SourceError) else f"{source.name} couldn't be read.")
                continue
            try:
                plan.channel.send_document(plan.target, str(path), caption=(caption or None) if index == 0 else None)
            except Exception:
                logger.warning("%s didn't take a file", plan.label, exc_info=True)
                failed.append(f"{plan.label} didn't take {source.name}.")
                continue
            finally:
                path.unlink(missing_ok=True)
            delivery.files[index].sent = True
    finally:
        _remove(_files_folder(delivery))
    sent = sum(item.sent for item in delivery.files)
    delivery.status = "sent" if sent == len(delivery.files) else "partly_sent" if sent else "failed"
    if failed:
        delivery.problem = "send_failed"
        delivery.detail = " ".join(failed[:3])
    return _finish(delivery)


def send_instead_to_devices(delivery_id: str) -> Delivery:
    """The person's "Send to your devices instead" on a delivery that couldn't go: no approval."""

    from row_bot.media_sources import SourceError

    original = get(delivery_id)
    if original is None:
        raise DeliveryError("sent_file_unavailable")
    if original.sent_instead and get(original.sent_instead) is not None:
        return get(original.sent_instead)  # type: ignore[return-value]
    if original.status == "sent" or original.target != "channel":
        raise DeliveryError("sent_file_unavailable")
    # What didn't go (all of it, unless some files did).
    kept = [source for source, item in zip(original.sources, original.files) if not item.sent] or original.sources
    try:
        sources = _sources_again(original.conversation_id, kept)
    except SourceError:
        raise DeliveryError("sent_files_gone") from None
    copy = send_to_devices(original.conversation_id, sources, approval="not_needed")
    if copy.status == "failed":
        return copy
    original.sent_instead = copy.delivery_id
    _save(original)
    return copy


# ── Views and words ─────────────────────────────────────────────────────────


def _iso(value: float | None) -> str | None:
    return None if value is None else datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def can_send_instead(delivery: Delivery) -> bool:
    return delivery.target == "channel" and delivery.status != "sent" and bool(delivery.sources)


def view(delivery: Delivery) -> dict[str, Any]:
    """The client's view of one delivery: names and words, never a path."""

    expired = delivery.expired()
    available = delivery.target == "devices" and not expired
    return {
        "delivery_id": delivery.delivery_id,
        "conversation_id": delivery.conversation_id,
        "target": delivery.target,
        "channel": delivery.channel,
        "channel_label": delivery.channel_label,
        "place": delivery.place,
        "shared": delivery.shared,
        "status": "expired" if expired and delivery.status != "failed" else delivery.status,
        "problem": delivery.problem,
        "detail": delivery.detail[:600],
        "limit_bytes": delivery.limit_bytes or None,
        "files": [{"index": index, "name": item.name, "size_bytes": item.size_bytes, "mime_type": item.mime_type,
                   "sent": item.sent, "downloadable": available and item.sent}
                  for index, item in enumerate(delivery.files)],
        "created_at": _iso(delivery.created_at),
        "expires_at": _iso(delivery.expires_at) if delivery.target == "devices" else None,
        "approval": delivery.approval,
        "can_send_to_devices": can_send_instead(delivery) and not delivery.sent_instead,
        "sent_instead": delivery.sent_instead or None,
    }


def _names(delivery: Delivery, *, sent: bool | None = None) -> str:
    items = [item for item in delivery.files if sent is None or item.sent is sent]
    if len(items) == 1:
        return f"{items[0].name} ({size_words(items[0].size_bytes)})"
    shown = ", ".join(item.name for item in items[:4])
    more = f" and {len(items) - 4} more" if len(items) > 4 else ""
    return f"{len(items)} files ({shown}{more})"


def summary(delivery: Delivery) -> str:
    """The tool result's first line: ``Delivery send-…: …`` (the chat's card follows it by that id)."""

    head = f"Delivery {delivery.delivery_id}: "
    where = ("your devices" if delivery.target == "devices"
             else delivery.place or delivery.channel_label or "the chat app")
    if delivery.status == "sent":
        if delivery.target == "devices":
            return (head + f"sent {_names(delivery)} to the person's devices. It shows in this chat with a "
                    f"Download button on each device signed in to Row-Bot, for {DEVICE_KEEP_DAYS} days.")
        return head + f"sent {_names(delivery)} to {where}."
    if delivery.status == "partly_sent":
        advice = (" Suggest sending the rest to their devices instead (the chat's card has a button for that)."
                  if delivery.target == "channel" else "")
        return (head + f"sent {_names(delivery, sent=True)} to {where}; not sent: {_names(delivery, sent=False)}. "
                f"{delivery.detail}{advice}")
    advice = (" Suggest sending it to their devices instead: the chat's card has a button for that, or use "
              "send_file with to=devices." if delivery.target == "channel" else "")
    return head + f"not sent to {where}. {delivery.detail}{advice}"
