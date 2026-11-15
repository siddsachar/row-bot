"""
Row-Bot – Shared Media Pipeline
================================
Common helpers for processing inbound media from any channel:

* **Voice notes** → transcribe via faster-whisper
* **Photos / images** → analyse via Vision service
* **Documents / files** → save to inbox directory

Every channel's inbound handler calls these instead of re-implementing
the same logic.
"""

from __future__ import annotations

import logging
import os
import pathlib
import re
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Generic, TypeVar

from row_bot.data_paths import default_workspace_root
from row_bot.data_paths import get_row_bot_data_dir

log = logging.getLogger("row_bot.channels.media")

_DATA_DIR = get_row_bot_data_dir()
_INBOX_DIR = _DATA_DIR / "inbox"


def _safe_filename(filename: str) -> str:
    """Return a basename safe to place under Row-Bot-managed folders."""
    name = pathlib.Path(str(filename or "attachment")).name.strip()
    if not name:
        name = "attachment"
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", name)
    name = name.strip(" .")
    return name or "attachment"


# ── Voice → Text ─────────────────────────────────────────────────────

def transcribe_audio(data: bytes, file_ext: str = ".ogg") -> str:
    """Transcribe audio bytes to text via faster-whisper.

    Parameters
    ----------
    data : bytes
        Raw audio file contents (OGG/Opus, WebM, MP3, WAV, …).
    file_ext : str
        File extension hint so faster-whisper picks the right decoder.

    Returns
    -------
    str
        Transcribed text (empty string on failure).
    """
    from row_bot.voice import get_voice_service

    svc = get_voice_service()
    svc._ensure_whisper()

    # Write to temp file — faster-whisper.transcribe() accepts paths
    # and internally uses ffmpeg to decode any format.
    with tempfile.NamedTemporaryFile(suffix=file_ext, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        segs, _ = svc._whisper_model.transcribe(
            tmp_path, beam_size=5, language="en", vad_filter=True,
        )
        text = " ".join(s.text.strip() for s in segs).strip()
        log.info("Transcribed audio (%d bytes) → %d chars", len(data), len(text))
        return text
    except Exception as exc:
        log.error("Audio transcription failed: %s", exc)
        return ""
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── Photo → Analysis ─────────────────────────────────────────────────

def analyze_image(data: bytes,
                  question: str = "Describe this image in detail.") -> str:
    """Analyse image bytes via the Vision service.

    Returns the model's text description (empty on failure).
    """
    try:
        from row_bot.vision import VisionService
        svc = VisionService()
        result = svc.analyze(data, question)
        log.info("Image analysis (%d bytes) → %d chars", len(data), len(result))
        return result
    except Exception as exc:
        log.error("Image analysis failed: %s", exc)
        return ""


# ── Document / File → Save & Extract ─────────────────────────────────

def save_inbound_file(data: bytes, filename: str) -> pathlib.Path:
    """Persist an inbound file to ``~/.row-bot/inbox/``.

    Returns the absolute ``Path`` to the saved file.
    """
    from uuid import uuid4
    from row_bot.application.attachments import _managed_root, _safe_path, _write

    inbox = _managed_root(_INBOX_DIR)
    inbox.mkdir(parents=True, exist_ok=True)
    _safe_path(inbox, inbox)
    # Unique ownership begins at exclusive creation, including same-name files
    # received by different conversations during the same clock tick.
    while True:
        dest = inbox / f"{int(time.time())}_{uuid4().hex}_{_safe_filename(filename)}"
        try:
            _write(inbox, dest, data)
            break
        except FileExistsError:
            continue
    log.info("Saved inbound file: %s (%d bytes)", dest, len(data))
    return dest


# File-type sets (mirrors ui/constants.py — kept here to avoid UI dependency)
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_DATA_EXTENSIONS = {".csv", ".tsv", ".xlsx", ".xls", ".json", ".jsonl"}
_TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".html", ".css", ".xml", ".yaml",
    ".yml", ".toml", ".ini", ".cfg", ".log", ".sh", ".bat", ".ps1", ".sql",
    ".r", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".rb", ".php",
    ".swift", ".kt", ".lua", ".pl",
}


def extract_document_text(data: bytes, filename: str,
                          max_chars: int = 80_000) -> str:
    """Extract readable text from a file's raw bytes.

    Supports PDF, plain-text / code, and tabular data files.
    Returns the extracted text, or an empty string if the file type is
    unsupported or extraction fails.
    """
    import io

    suffix = pathlib.Path(filename).suffix.lower()

    # ── PDF ───────────────────────────────────────────────────────────
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            pages: list[str] = []
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(f"--- Page {i + 1} ---\n{text}")
                if sum(len(p) for p in pages) > max_chars:
                    pages.append(
                        f"[Truncated — {len(reader.pages)} pages total, "
                        f"showing first {i + 1}]"
                    )
                    break
            content = "\n".join(pages) if pages else ""
            if content:
                log.info("Extracted PDF text: %s (%d chars from %d pages)",
                         filename, len(content), len(reader.pages))
            return content
        except Exception as exc:
            log.error("PDF text extraction failed for %s: %s", filename, exc)
            return ""

    # ── Tabular data (CSV, Excel, JSON) ──────────────────────────────
    if suffix in _DATA_EXTENSIONS:
        try:
            from row_bot.data_reader import read_data_file
            buf = io.BytesIO(data)
            summary = read_data_file(buf, name=filename, max_chars=max_chars)
            log.info("Extracted data file: %s (%d chars)", filename, len(summary))
            return summary
        except Exception as exc:
            log.error("Data file extraction failed for %s: %s", filename, exc)
            return ""

    # ── Plain text / code ────────────────────────────────────────────
    if suffix in _TEXT_EXTENSIONS:
        try:
            text = data.decode("utf-8", errors="replace")
            if len(text) > max_chars:
                text = text[:max_chars] + f"\n[Truncated — {len(data):,} bytes total]"
            log.info("Read text file: %s (%d chars)", filename, len(text))
            return text
        except Exception as exc:
            log.error("Text file read failed for %s: %s", filename, exc)
            return ""

    return ""


# ── Copy to workspace ────────────────────────────────────────────────

_RECEIVED_FOLDER = "Received Files"


def copy_to_workspace(saved_path: pathlib.Path, workspace_filename: str | None = None) -> str | None:
    """Copy an inbox file into the filesystem-tool workspace so the agent
    can re-read it without escaping the sandbox.

    Returns the *workspace-relative* path (e.g. ``Received Files/doc.pdf``)
    on success, or ``None`` if the workspace is not configured or the copy
    fails.
    """
    import stat

    from row_bot.application.attachments import AttachmentError, _managed_root, _open, _safe_path

    from row_bot.conversation_resources import current_execution_context, ResourceError
    context = current_execution_context()
    binding = context.resolve("workspace") if context is not None else None
    if binding is not None:
        from row_bot.developer.storage import get_workspace
        workspace = get_workspace(binding.resource_id)
        if workspace is None:
            raise ResourceError("resource_unavailable")
        root = workspace.path
    else:
        try:
            from row_bot.tools.registry import get_tool_config
            root = get_tool_config("filesystem", "workspace_root", "")
            if not root:
                root = str(default_workspace_root())
        except Exception:
            root = str(default_workspace_root())

    descriptor = -1
    dest: pathlib.Path | None = None
    try:
        workspace_root = _managed_root(pathlib.Path(root))
        workspace_root.mkdir(parents=True, exist_ok=True)
        _safe_path(workspace_root, workspace_root)
        root_identity = workspace_root.stat()
        dest_dir = workspace_root / _RECEIVED_FOLDER
        _safe_path(workspace_root, dest_dir)
        dest_dir.mkdir(exist_ok=True)
        _safe_path(workspace_root, dest_dir)
        directory_identity = dest_dir.stat()

        def validate_destination() -> None:
            _safe_path(workspace_root, dest_dir)
            if (not os.path.samestat(root_identity, workspace_root.stat())
                    or not os.path.samestat(directory_identity, dest_dir.stat())):
                raise AttachmentError("action_denied")
            if descriptor >= 0 and dest is not None:
                _safe_path(workspace_root, dest)
                if not os.path.samestat(os.fstat(descriptor), dest.stat()):
                    raise AttachmentError("action_denied")

        source_path = saved_path.absolute()
        with _open(source_path.parent, source_path) as source:
            source_identity = os.fstat(source.fileno())
            if not stat.S_ISREG(source_identity.st_mode):
                raise AttachmentError("action_denied")
            name = _safe_filename(workspace_filename or saved_path.name)
            stem, suffix = pathlib.Path(name).stem, pathlib.Path(name).suffix
            counter = 0
            while True:
                validate_destination()
                dest = dest_dir / (name if counter == 0 else f"{stem}_{counter}{suffix}")
                _safe_path(workspace_root, dest)
                try:
                    descriptor = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                         | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600)
                    break
                except FileExistsError:
                    counter += 1
            # Check the opened identity before any bytes and each bounded write.
            # A renamed directory or reparse point must not redirect a copy.
            validate_destination()
            remaining = source_identity.st_size
            while remaining:
                data = source.read(min(65536, remaining))
                if not data:
                    raise AttachmentError("revision_conflict")
                view = memoryview(data)
                while view:
                    validate_destination()
                    written = os.write(descriptor, view)
                    if not written:
                        raise OSError("workspace_copy_failed")
                    view = view[written:]
                remaining -= len(data)
            current_source = os.fstat(source.fileno())
            if (source.read(1) or current_source.st_size != source_identity.st_size
                    or current_source.st_mtime_ns != source_identity.st_mtime_ns):
                raise AttachmentError("revision_conflict")
            validate_destination()
            os.fsync(descriptor)
        rel = f"{_RECEIVED_FOLDER}/{dest.name}"
        log.info("Copied to workspace: %s", rel)
        return rel
    except Exception as exc:
        # Remove only this attempt's unchanged, still-contained partial file.
        if descriptor >= 0 and dest is not None:
            try:
                validate_destination()
                os.close(descriptor)
                descriptor = -1
                dest.unlink()
            except (OSError, AttachmentError):
                pass
        log.warning("Could not copy file to workspace: %s", exc)
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


# ── Several attachments → one agent turn ─────────────────────────────

_AUDIO_EXTENSIONS = {
    ".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".webm", ".aac", ".flac", ".amr",
}
DEFAULT_IMAGE_QUESTION = "Describe this image in detail."


@dataclass(frozen=True)
class InboundAttachment:
    """One file a person sent on a channel, already downloaded."""

    filename: str
    data: bytes
    content_type: str = ""


@dataclass
class AttachmentTurn:
    """The single user message one or more attachments become."""

    text: str
    transcripts: list[str] = field(default_factory=list)
    usable: bool = False
    """False when nothing could be read and the person wrote nothing either."""


def attachment_kind(attachment: InboundAttachment) -> str:
    """``"image"``, ``"audio"`` or ``"document"``, from the type, else the extension."""
    content_type = str(attachment.content_type or "").lower()
    suffix = pathlib.Path(str(attachment.filename or "")).suffix.lower()
    if content_type.startswith("image/") or (not content_type and suffix in _IMAGE_EXTENSIONS):
        return "image"
    if content_type.startswith("audio/") or (not content_type and suffix in _AUDIO_EXTENSIONS):
        return "audio"
    return "document"


def _image_block(
    attachment: InboundAttachment,
    question: str,
    cache_image: Callable[[str, bytes], str] | None,
) -> tuple[str, bool]:
    from row_bot.file_context import _vision_analysis_failed

    name = _safe_filename(attachment.filename or "image")
    if cache_image is not None:
        try:
            name = cache_image(name, attachment.data) or name
        except Exception:
            log.debug("An inbound image could not be cached for image tools", exc_info=True)
    analysis = analyze_image(attachment.data, question)
    if _vision_analysis_failed(analysis):
        detail = str(analysis or "").strip()
        return f"[Attached image: {name} - vision analysis failed]\n{detail}".rstrip(), False
    return f"[Attached image: {name} — ALREADY ANALYZED, do NOT call analyze_image]\n{analysis}", True


def _document_block(attachment: InboundAttachment) -> str:
    filename = _safe_filename(attachment.filename or "file")
    saved = save_inbound_file(attachment.data, filename)
    extracted = extract_document_text(attachment.data, filename)
    workspace_path = copy_to_workspace(saved)
    lines = [f"[Attached file: {filename}]"]
    lines.append(f"Workspace path: {workspace_path}" if workspace_path else f"Saved to: {saved}")
    if extracted:
        lines.append(f"--- File content ---\n{extracted}\n--- End of file ---")
    else:
        lines.append("(Could not extract text — file may be image-based or unsupported format)")
    return "\n".join(lines)


def build_attachment_turn(
    attachments: list[InboundAttachment],
    caption: str = "",
    *,
    cache_image: Callable[[str, bytes], str] | None = None,
) -> AttachmentTurn:
    """Read every attachment of one inbound message and make one user message.

    Each image still gets its own vision pre-call and each voice note its own
    transcription; their text results go into one turn instead of one turn
    each. Raw images never enter the conversation: text descriptions stay
    cheap across a long thread. The person's own words come first, then the
    transcripts, then the files, in the attachment block the app shows as
    badges (the block the desktop client sends).
    """
    from row_bot.file_context import wrap_attachment_context

    caption = str(caption or "").strip()
    question = caption or DEFAULT_IMAGE_QUESTION
    blocks: list[str] = []
    transcripts: list[str] = []
    usable = bool(caption)
    seen_names: dict[str, int] = {}
    for original in attachments:
        # Albums often name every item alike ("image.jpg"); each keeps its own name.
        name = _safe_filename(original.filename or "attachment")
        seen_names[name] = seen_names.get(name, 0) + 1
        if seen_names[name] > 1:
            path = pathlib.PurePath(name)
            name = f"{path.stem}_{seen_names[name]}{path.suffix}"
        attachment = replace(original, filename=name)
        kind = attachment_kind(attachment)
        try:
            if kind == "image":
                block, read = _image_block(attachment, question, cache_image)
                blocks.append(block)
                usable = usable or read
            elif kind == "audio":
                suffix = pathlib.Path(str(attachment.filename or "")).suffix.lower() or ".ogg"
                transcript = transcribe_audio(attachment.data, suffix)
                if transcript:
                    transcripts.append(transcript)
                    usable = True
                else:
                    name = _safe_filename(attachment.filename or "audio")
                    blocks.append(f"[Attached audio: {name} - could not be transcribed]")
            else:
                blocks.append(_document_block(attachment))
                usable = True
        except Exception:
            log.warning("An inbound %s attachment could not be read", kind, exc_info=True)
            name = _safe_filename(attachment.filename or "attachment")
            blocks.append(f"[Attached file: {name} - could not be read]")
    parts: list[str] = [caption] if caption else []
    if len(transcripts) == 1:
        parts.append(f"[Voice message transcription]: {transcripts[0]}")
    else:
        total = len(transcripts)
        parts.extend(
            f"[Voice message {index} of {total} transcription]: {text}"
            for index, text in enumerate(transcripts, start=1)
        )
    context = wrap_attachment_context("\n\n".join(blocks))
    if context:
        parts.append(context)
    return AttachmentTurn(text="\n\n".join(parts), transcripts=transcripts, usable=usable)


T = TypeVar("T")

_PENDING_WAIT_S = 120.0  # a download that never finishes stops holding its batch


@dataclass
class _Batch(Generic[T]):
    items: list[tuple[int, T]] = field(default_factory=list)
    pending: int = 0
    version: int = 0
    arrivals: int = 0


class AttachmentCoalescer(Generic[T]):
    """Join attachments that arrive as separate messages but were sent together.

    A Telegram album or a WhatsApp album arrives as one message per item. The
    first item for a key opens a batch; items for the same key that arrive
    before it closes join it. ``begin`` marks an item as on its way (still
    downloading) and ``add`` delivers it. A batch closes once nothing is on its
    way and nothing new came for ``settle_s``; ``on_ready(key, items)`` then
    runs once, on the batch's own worker thread, with the items in arrival order.
    """

    def __init__(
        self,
        on_ready: Callable[[str, list[T]], None],
        *,
        settle_s: float = 1.0,
        name: str = "attachments",
    ) -> None:
        self._on_ready = on_ready
        self._settle_s = max(0.0, float(settle_s))
        self._name = name
        self._cond = threading.Condition()
        self._batches: dict[str, _Batch[T]] = {}

    def begin(self, key: str) -> int:
        """Mark one item of *key* as on its way; return its arrival order."""
        with self._cond:
            batch = self._batches.get(key)
            if batch is None:
                batch = _Batch()
                self._batches[key] = batch
                threading.Thread(
                    target=self._settle,
                    args=(key, batch),
                    daemon=True,
                    name=f"{self._name}-batch",
                ).start()
            batch.pending += 1
            batch.version += 1
            batch.arrivals += 1
            self._cond.notify_all()
            return batch.arrivals

    def add(self, key: str, order: int, item: T) -> None:
        """Deliver an item that ``begin`` announced."""
        with self._cond:
            batch = self._batches.get(key)
            if batch is not None:
                batch.items.append((order, item))
                batch.pending = max(0, batch.pending - 1)
                batch.version += 1
                self._cond.notify_all()
                return
        # Its batch already closed (it waited too long): it goes on its own,
        # off the caller's thread like any batch.
        threading.Thread(
            target=self._run, args=(key, [item]), daemon=True, name=f"{self._name}-batch",
        ).start()

    def cancel(self, key: str) -> None:
        """An announced item will not come (its download failed)."""
        with self._cond:
            batch = self._batches.get(key)
            if batch is not None:
                batch.pending = max(0, batch.pending - 1)
                batch.version += 1
                self._cond.notify_all()

    def _wait_for_change(self, batch: _Batch[T], version: int, seconds: float) -> bool:
        """Wait until *batch* changes or *seconds* pass; True when it changed.

        The condition is shared by every key, so a wake-up alone proves nothing.
        """
        deadline = time.monotonic() + seconds
        while batch.version == version:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._cond.wait(timeout=remaining)
        return True

    def _settle(self, key: str, batch: _Batch[T]) -> None:
        with self._cond:
            while True:
                version = batch.version
                if batch.pending > 0:
                    if not self._wait_for_change(batch, version, _PENDING_WAIT_S):
                        log.warning("A %s item never arrived; its batch goes on without it", self._name)
                        batch.pending = 0
                    continue
                if self._wait_for_change(batch, version, self._settle_s):
                    continue
                if self._batches.get(key) is batch:
                    del self._batches[key]
                items = [item for _order, item in sorted(batch.items, key=lambda pair: pair[0])]
                break
        if items:
            self._run(key, items)

    def _run(self, key: str, items: list[T]) -> None:
        try:
            self._on_ready(key, items)
        except Exception:
            log.warning("A %s batch could not be handled", self._name, exc_info=True)
