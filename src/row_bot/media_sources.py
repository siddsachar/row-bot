"""Where videos and pictures for editing and looking come from, and where results may be written (F3, F5).

A tool names a source in one of these ways, and nothing else is ever opened:

- a recording (``rec-…``) or an earlier video edit (``edit-…``) of this conversation;
- a file in this conversation's files, by its name (the newest with that name);
- a file in the workspace, by its path: the code folder bound to the conversation when it has
  one, otherwise the workspace folder. Relative paths, or absolute ones inside it; ``..``,
  ``.git``, links below the workspace (symbolic links and junctions) and Row-Bot's own data
  folder are refused, and the file must resolve inside the workspace. This is the Developer
  tools' guard (``developer.review.scoped_workspace_path``) with two differences: folders above
  the workspace aren't checked (the person chose it), and cloud-file placeholders (OneDrive's
  "online only" files and folders, also reparse points) aren't taken for links.
"""

from __future__ import annotations

import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# Windows reparse tags that redirect a path elsewhere: a symbolic link and a junction (mount point).
_LINK_TAGS = frozenset({0xA000000C, 0xA0000003})
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"})
MAX_IMAGE_BYTES = 25 * 1024 * 1024
Kind = Literal["video", "image"]


class SourceError(ValueError):
    """A source can't be used; the message says why in plain words."""


@dataclass(frozen=True)
class Media:
    """One video or picture a tool may read."""

    label: str
    kind: Kind
    path: Path | None = None
    ref: str = ""

    def read_bytes(self, limit: int = MAX_IMAGE_BYTES) -> bytes:
        if self.ref:
            from row_bot.application.attachments import AttachmentError, read_attachment

            try:
                return read_attachment(self.ref)[1]
            except AttachmentError:
                raise SourceError(f"{self.label} can't be read any more.") from None
        assert self.path is not None
        try:
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise SourceError(f"{self.label} isn't a file.")
            if info.st_size > limit:
                raise SourceError(f"{self.label} is too large to look at ({info.st_size // (1024 * 1024)} MB).")
            return self.path.read_bytes()
        except OSError:
            raise SourceError(f"{self.label} can't be read.") from None


# ── The workspace ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Workspace:
    root: Path
    # A code folder bound to the conversation (Developer), else the workspace folder.
    code_folder_id: str = ""
    name: str = "the workspace"


def workspace(conversation_id: str) -> Workspace | None:
    """The conversation's code folder when it has one, otherwise the workspace folder (as the shell uses)."""
    try:
        from row_bot.developer import tool_context
        from row_bot.developer.storage import get_workspace

        workspace_id = tool_context.get_workspace_id() or tool_context.infer_workspace_id_from_thread(conversation_id)
        bound = get_workspace(workspace_id) if workspace_id else None
        if bound is not None and bound.path and Path(bound.path).is_dir():
            return Workspace(Path(bound.path).absolute(), code_folder_id=bound.id,
                             name=f"the code folder {bound.name}" if getattr(bound, "name", "") else "the code folder")
    except Exception:
        logger.debug("Could not read the conversation's code folder", exc_info=True)
    try:
        from row_bot.tools import registry

        root = registry.get_tool_config("filesystem", "workspace_root", "")
    except Exception:
        root = ""
    if root and Path(root).is_dir():
        return Workspace(Path(root).absolute())
    return None


def _relative(root: Path, value: str) -> str:
    """The path inside the workspace that *value* names; refuses one outside it."""
    text = str(value or "").strip().strip('"')
    given = Path(text)
    if given.is_absolute() or (len(text) > 1 and text[1] == ":"):
        try:
            relative = os.path.relpath(given, root)
        except ValueError:  # Another drive.
            raise SourceError(f"{value} is outside the workspace; only files in it can be used.") from None
        if relative == os.pardir or relative.startswith(os.pardir + os.sep):
            raise SourceError(f"{value} is outside the workspace; only files in it can be used.")
        text = relative
    text = text.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) in _LINK_TAGS


def _parts(relative: str) -> list[str]:
    """The folders and name of a path inside the workspace; ValueError for one that could leave it."""
    if (not relative or len(relative) > 1024 or relative.startswith(("/", "\\"))
            or any(ord(character) < 32 or character in ':*?"<>|' for character in relative)):
        raise ValueError("workspace_path_denied")
    parts = relative.replace("\\", "/").split("/")
    if any(part in {"", ".", "..", ".git"} for part in parts):
        raise ValueError("workspace_path_denied")
    return parts


def _contained(root: Path, parts: list[str]) -> Path:
    """The path below *root*, refusing any part that is a link; it must resolve inside *root*."""
    current = root
    for part in parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            break
        if _is_link(info):
            raise ValueError("workspace_path_denied")
    target = root.joinpath(*parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("workspace_path_denied")
    return target


def workspace_file(root: Path, value: str) -> Path:
    """An existing file in the workspace, reached without links; never Row-Bot's own data."""
    from row_bot.data_paths import is_private_data_path

    relative = _relative(root, value)
    try:
        target = _contained(root, _parts(relative))
    except (OSError, ValueError):
        raise SourceError(f"{value} isn't a file in the workspace (paths can't leave it or go through links).") \
            from None
    if is_private_data_path(target):
        raise SourceError("That path is inside Row-Bot's private data folder, which tools can't read.")
    if not target.is_file():
        raise SourceError(f"{value} isn't a file in the workspace.")
    return target


def workspace_target(root: Path, value: str) -> tuple[Path, str, bool]:
    """Where to write a new file in the workspace: (path, its relative name, whether one is there already).

    Missing folders on the way are made inside the workspace; no part may be a link.
    """
    from row_bot.data_paths import is_private_data_path

    relative = _relative(root, value)
    try:
        parts = _parts(relative)
    except ValueError:
        raise SourceError(f"{value} isn't a path Row-Bot can write in the workspace (paths can't leave it).") \
            from None
    try:
        target = _contained(root, parts)
    except ValueError:
        raise SourceError(f"{value} goes through a link, so Row-Bot won't write there.") from None
    except OSError:
        raise SourceError(f"{value} can't be written.") from None
    if is_private_data_path(target):
        raise SourceError("That path is inside Row-Bot's private data folder, which tools can't change.")
    if target.exists() and not target.is_file():
        raise SourceError(f"{value} is a folder.")
    return target, "/".join(parts), target.exists()


# ── Naming a source ─────────────────────────────────────────────────────────


def _kind_of(name: str, mime: str = "") -> Kind | None:
    if mime.startswith("video/") or Path(name).suffix.casefold() in VIDEO_SUFFIXES:
        return "video"
    if mime.startswith("image/") or Path(name).suffix.casefold() in IMAGE_SUFFIXES:
        return "image"
    return None


def _chat_file(conversation_id: str, name: str) -> Media | None:
    from row_bot.application.attachments import AttachmentError, list_conversation_files

    wanted = name.strip().casefold()
    try:
        files = list_conversation_files(conversation_id)
    except AttachmentError:
        return None
    for item in files:
        candidate = str(item["name"]).casefold()
        if candidate == wanted or candidate == f"generated-{wanted}":
            kind = _kind_of(item["name"], item["mime_type"])
            if kind is not None:
                return Media(label=str(item["name"]), kind=kind, ref=str(item["attachment_ref"]))
    return None


def resolve(value: str, conversation_id: str) -> list[Media]:
    """The videos or pictures a source names (an edit that saved frames names several)."""
    text = str(value or "").strip()
    if not text:
        raise SourceError("A source is empty.")
    from row_bot import recordings, video_edits

    if recordings.RECORDING_ID.fullmatch(text):
        record = recordings.get(text)
        if record is None or record.conversation_id != conversation_id:
            raise SourceError(f"{text} isn't a recording in this chat.")
        if record.status != "saved" or not record.media_ref:
            raise SourceError(f"{text} has no saved video yet.")
        label = "Screen recording" if record.kind == "screen" else "Browser recording"
        return [Media(label=label, kind="video", ref=record.media_ref)]
    if video_edits.EDIT_ID.fullmatch(text):
        edit = video_edits.get(text)
        if edit is None or edit.conversation_id != conversation_id:
            raise SourceError(f"{text} isn't a video edit in this chat.")
        if edit.status != "saved":
            raise SourceError(f"{text} isn't finished yet ({video_edits.status_words(edit)}).")
        if edit.kind == "frames":
            return [Media(label=f"Frame at {video_edits.clock(moment)}", kind="image", ref=ref)
                    for ref, moment in zip(edit.frame_refs, edit.frame_times)]
        return [Media(label=edit.output_name or "Edited video", kind="video", ref=edit.media_ref)]
    place = workspace(conversation_id)
    refused: SourceError | None = None
    if place is not None:
        try:
            path = workspace_file(place.root, text)
        except SourceError as exc:
            path, refused = None, exc
        if path is not None:
            kind = _kind_of(path.name)
            if kind is None:
                raise SourceError(f"{text} isn't a video or a picture.")
            return [Media(label=_relative(place.root, str(path)), kind=kind, path=path)]
    found = _chat_file(conversation_id, text)
    if found is not None:
        return [found]
    looks_like_path = "/" in text or "\\" in text or Path(text).is_absolute()
    if refused is not None and looks_like_path and "isn't a file in the workspace." not in str(refused):
        raise refused  # Outside the workspace, through a link, or Row-Bot's own data: say so.
    where = "this chat's files or the workspace" if place is not None else "this chat's files"
    raise SourceError(f"Couldn't find {text} in {where}. Name a file there, a recording (rec-…) or a video "
                      "edit (edit-…).")
