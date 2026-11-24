"""The one way a tool names a file (W4): every media and file tool finds what it reads through ``find``.

A tool names a file in one of these ways, tried in this order, and nothing else is ever opened:

1. ``rec-…`` (a recording), ``edit-…`` (a video edit; ``edit-…/frame-03.jpg`` one of its frames) or ``vid-…``
   (a video project's newest made video; ``vid-…@v2`` that version's): this conversation's own;
2. ``name@v2``: that version of one of this conversation's outputs;
3. a full path: never Row-Bot's own data folder; inside the workspace, that file; elsewhere on this computer
   (only where the tool takes one, ``outside``), read in place once the person allows it by the approval level
   (5.3.1 A1). A video project asks when a file becomes one of its scenes' footage, and then reads that exact
   file in place for that project only (``allowed``); any other file elsewhere asks again;
4. a path with a folder: a file in the workspace;
5. a bare name: one of this conversation's files, by its own name, by that name without "generated-", or by
   its output's name (the newest version); then a file in the workspace.

Each file a tool makes in a chat is a version of an output (``attachments.register_attachment``): its versions
share one file name, and its output's name is that name without "generated-" and its type. A result names it
on its first line (``identity``), which history masking keeps, and ``present`` with no files lists them
(``outputs_text``).

The workspace is the person's workspace folder, the one the file tools and the shell use, in every chat; a
code folder bound to a conversation is Developer Studio's. Relative paths, or absolute ones inside it; ``..``,
``.git``, links below the workspace (symbolic links and junctions) and Row-Bot's own data folder are refused,
and the file must resolve inside the workspace. This is the Developer tools' guard
(``developer.review.scoped_workspace_path``) with two differences: folders above the workspace aren't checked
(the person chose it), and cloud-file placeholders (OneDrive's "online only" files and folders, also reparse
points) aren't taken for links.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# Windows reparse tags that redirect a path elsewhere: a symbolic link and a junction (mount point).
_LINK_TAGS = frozenset({0xA000000C, 0xA0000003})
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"})
AUDIO_SUFFIXES = frozenset({".wav", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac"})
MAX_IMAGE_BYTES = 25 * 1024 * 1024
# What a file is; a tool says which kinds it reads (``find``), and None takes any file.
Kind = Literal["video", "image", "audio", "file"]
MEDIA: frozenset[str] = frozenset({"video", "image"})
_KIND_WORDS = {"video": "a video", "image": "a picture", "audio": "a sound file", "file": "a file"}
# ``name@v2``: one version of an output.
_AT_VERSION = re.compile(r"(.+?)@v(\d{1,6})")
# The newest outputs ``outputs_text`` lists and a miss names.
LISTED_OUTPUTS = 8


class SourceError(ValueError):
    """A source can't be used; the message says why in plain words."""


@dataclass(frozen=True)
class Media:
    """One video or picture a tool may read."""

    label: str
    kind: Kind
    path: Path | None = None
    ref: str = ""
    # A file outside the workspace, read in place (only once the person allowed it).
    outside: bool = False

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


def workspace() -> Workspace | None:
    """The person's workspace folder, where the file tools work, the same in every chat (a code folder bound to
    a chat is Developer Studio's alone); None while it doesn't exist."""
    from row_bot.data_paths import workspace_folder

    try:
        root = workspace_folder(create=False)
    except Exception:
        logger.debug("Could not read the workspace folder", exc_info=True)
        return None
    return Workspace(root.absolute()) if root.is_dir() else None


def _relative(root: Path, value: str) -> str:
    """The path inside the workspace that *value* names; refuses one outside it."""
    text = str(value or "").strip().strip('"')
    given = Path(text)
    if given.is_absolute() or (len(text) > 1 and text[1] == ":"):
        try:
            relative = os.path.relpath(given, root)
        except ValueError:  # Another drive.
            raise SourceError(f"{value} is outside the workspace; this uses files in it. Copy the file with workspace_copy_file, "
                              "which asks first.") from None
        if relative == os.pardir or relative.startswith(os.pardir + os.sep):
            raise SourceError(f"{value} is outside the workspace; this uses files in it. Copy the file with workspace_copy_file, "
                              "which asks first.")
        text = relative
    text = text.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def _leaves(root: Path, path: Path) -> bool:
    try:
        relative = os.path.relpath(path, root)
    except ValueError:  # Another drive.
        return True
    return relative == os.pardir or relative.startswith(os.pardir + os.sep)


def _checked_outside(value: str, place: Workspace | None) -> Path | None:
    """A video or picture outside the workspace named by its full path, checked but not yet allowed; else None.
    Row-Bot's own data folder is refused (a chat's files are named by their name, never by where they're kept)."""
    from row_bot.data_paths import is_private_data_path

    given = Path(value.strip().strip('"')).expanduser()
    if not given.is_absolute() or (place is not None and not _leaves(place.root, given)):
        return None
    path = given.resolve()
    if is_private_data_path(path):
        raise SourceError("That path is inside Row-Bot's private data folder, which tools can't read.")
    if not path.is_file():
        raise SourceError(f"{value} isn't a file.")
    if _kind_of(path.name) is None:
        raise SourceError(f"{value} isn't a video or a picture.")
    return path


def outside_path(value: str, conversation_id: str) -> Path | None:
    """The file *value* names outside the workspace, checked, without asking to read it; None for any other
    source (the same workspace folder in every chat, ``workspace``)."""
    del conversation_id
    return _checked_outside(str(value or "").strip(), workspace())


def _outside_file(value: str, place: Workspace | None, *, allowed: frozenset[Path] | None = None) -> Path | None:
    """A file outside the workspace named by its full path, once the person allows reading it by the approval
    level; ``allowed``: only these files, which the person allowed already, and nothing is asked. Else None."""
    path = _checked_outside(value, place)
    if path is None:
        return None
    if allowed is not None:
        if path not in allowed:
            raise SourceError(f"{value} is outside the workspace and wasn't allowed for this video.")
        return path
    from row_bot.tools.filesystem_tool import outside_workspace_refusal

    refusal, _line = outside_workspace_refusal("read_file", [path])
    if refusal:
        raise SourceError(refusal)
    return path


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


# ── Outputs: the files made in this chat ────────────────────────────────────


def _kind_of(name: str, mime: str = "") -> Literal["video", "image"] | None:
    if mime.startswith("video/") or Path(name).suffix.casefold() in VIDEO_SUFFIXES:
        return "video"
    if mime.startswith("image/") or Path(name).suffix.casefold() in IMAGE_SUFFIXES:
        return "image"
    return None


def _file_kind(name: str, mime: str = "") -> Kind:
    kind = _kind_of(name, mime)
    if kind is not None:
        return kind
    if mime.startswith("audio/") or Path(name).suffix.casefold() in AUDIO_SUFFIXES:
        return "audio"
    return "file"


def output_name(file_name: str) -> str:
    """An output's name: its file's name without "generated-" and its type ("Sam's birthday.mp4" is "Sam's
    birthday"; "Row-Bot 5.3.1 demo" keeps its whole name)."""
    name = str(file_name or "")
    if name.startswith("generated-") and len(name) > len("generated-"):
        name = name[len("generated-"):]
    suffix = Path(name).suffix
    known = suffix.casefold() in IMAGE_SUFFIXES | VIDEO_SUFFIXES | AUDIO_SUFFIXES | {".gif", ".srt", ".vtt"}
    if suffix and suffix != name and (known or mimetypes.guess_type("x" + suffix)[0]):
        return name[:-len(suffix)]
    return name


def identity(item: dict) -> str:
    """A made file as a result's first line names it, so history masking keeps it: ``Output "Sam's birthday" v3
    (draft) · Sam's birthday.mp4``. A file without a role (the person's own) says none."""
    name = str(item.get("name") or "")
    role = str(item.get("role") or "")
    return (f'Output "{output_name(name)}" v{int(item.get("version") or 1)}' + (f" ({role})" if role else "")
            + f" · {name}")


def identity_of(ref: str) -> str:
    """``identity`` of one of a chat's files, by its reference; empty when it can't be read."""
    from row_bot.application.attachments import AttachmentError, attachment_lineage, inspect_attachment

    try:
        return identity({**inspect_attachment(ref), **attachment_lineage(ref)})
    except AttachmentError:
        return ""


def _files(conversation_id: str) -> list[dict]:
    from row_bot.application.attachments import AttachmentError, list_conversation_files

    try:
        return list_conversation_files(conversation_id, limit=100_000)
    except AttachmentError:
        return []


def _output_of(item: dict) -> str:
    return str(item.get("output_id") or item["attachment_ref"])


def _outputs(files: list[dict]) -> list[dict]:
    """Each output's newest version, newest first. A picture that is a working file (a frame, a sheet, a
    screenshot) is left out: it was looked at on the way, never made for the person."""
    seen: set[tuple[str, str]] = set()
    outputs = []
    for item in files:
        if item.get("role") == "working" and str(item.get("mime_type") or "").startswith("image/"):
            continue
        key = (_output_of(item), str(item["name"]).casefold())
        if key not in seen:
            seen.add(key)
            outputs.append(item)
    return outputs


def chat_outputs(conversation_id: str) -> list[dict]:
    """This conversation's outputs, newest first: each one's newest version (name, role, version, reference)."""
    return _outputs(_files(conversation_id))


def outputs_text(conversation_id: str) -> str:
    """This chat's newest outputs (``LISTED_OUTPUTS``), one identity a line: what ``present`` with no files says."""
    outputs = chat_outputs(conversation_id)
    if not outputs:
        return "This chat has no outputs yet."
    lines = ["This chat's outputs, newest first (name one in any media tool, present or send_file; name@v2 for "
             "an older version):", *(identity(item) for item in outputs[:LISTED_OUTPUTS])]
    if len(outputs) > LISTED_OUTPUTS:
        lines.append(f"…and {len(outputs) - LISTED_OUTPUTS} older.")
    return "\n".join(lines)


def _named(files: list[dict], text: str) -> list[dict]:
    """The files a bare name names, newest first: by their own name, by it without "generated-", or by their
    output's name."""
    wanted = text.casefold()
    for test in (lambda item: str(item["name"]).casefold() == wanted,
                 lambda item: str(item["name"]).casefold() == f"generated-{wanted}",
                 lambda item: output_name(str(item["name"])).casefold() == wanted):
        found = [item for item in files if test(item)]
        if found:
            return found
    return []


def _of_kinds(found: list[dict], kinds: frozenset[str] | None) -> list[dict]:
    """*found* with the files of a kind the tool reads first (a voice "scene-1.wav" and a picture "scene-1.png")."""
    right = [item for item in found
             if kinds is None or _file_kind(str(item["name"]), str(item["mime_type"])) in kinds]
    return right + [item for item in found if item not in right]


def _chat_media(item: dict) -> Media:
    return Media(label=str(item["name"]), kind=_file_kind(str(item["name"]), str(item["mime_type"])),
                 ref=str(item["attachment_ref"]))


def chat_file(conversation_id: str, name: str) -> Media | None:
    """A video or picture in this conversation's files, by its name or its output's name (the newest)."""
    found = _of_kinds(_named(_files(conversation_id), name.strip()), MEDIA)
    media = _chat_media(found[0]) if found else None
    return media if media is not None and media.kind in MEDIA else None


def _version(files: list[dict], name: str, number: int, kinds: frozenset[str] | None) -> Media:
    """``name@v2``: that version of the output a name names."""
    found = _of_kinds(_named(files, name), kinds)
    if not found:
        raise SourceError(_missing(f"{name}@v{number}", files))
    newest = found[0]
    versions = [item for item in files if _output_of(item) == _output_of(newest)]
    numbered = [item for item in versions if int(item.get("version") or 1) == number]
    # The output's own file first: a video project's export or review sheet has the version's number too.
    match = next((item for item in numbered if str(item["name"]).casefold() == str(newest["name"]).casefold()),
                 numbered[0] if numbered else None)
    if match is None:
        have = sorted({int(item.get("version") or 1) for item in versions})
        raise SourceError(f"{name} has no version {number} in this chat; it has "
                          + ", ".join(f"v{value}" for value in have) + ".")
    return _chat_media(match)


def _missing(text: str, files: list[dict]) -> str:
    where = "this chat's files or the workspace" if workspace() is not None else "this chat's files"
    names = [f"“{output_name(str(item['name']))}”" for item in _outputs(files)[:LISTED_OUTPUTS]]
    listed = f" This chat's outputs: {', '.join(names)}." if names else ""
    return (f"Couldn't find {text} in {where}. Name a file in this chat, a workspace path, a recording (rec-…), a "
            f"video edit (edit-…) or a video project (vid-…).{listed}")


# ── Finding a file ──────────────────────────────────────────────────────────


def _edit_frame(edit_id: str, name: str, conversation_id: str) -> Media:
    """``edit-…/frame-08.jpg``: that edit's still, by the name it was saved under or its number (S3). A bare
    ``frame-08.jpg`` named another edit's frame once two edits had saved stills."""
    from row_bot import video_edits

    edit = video_edits.get(edit_id)
    if edit is None or edit.conversation_id != conversation_id or edit.kind != "frames" or edit.status != "saved":
        raise SourceError(f"{edit_id} isn't a video edit with saved frames in this chat.")
    names = [item.casefold() for item in edit.frame_names]
    number = re.search(r"(\d+)(?:\.\w+)?$", name)
    index = (names.index(name.casefold()) if name.casefold() in names
             else int(number.group(1)) - 1 if number else -1)
    if not 0 <= index < len(edit.frame_refs):
        raise SourceError(f"{edit_id} has {len(edit.frame_refs)} frames: name one by its number, 1 to "
                          f"{len(edit.frame_refs)}.")
    moment = edit.frame_times[index] if index < len(edit.frame_times) else 0.0
    return Media(label=f"Frame at {video_edits.clock(moment)}", kind="image", ref=edit.frame_refs[index])


def _own(text: str, conversation_id: str) -> list[Media] | None:
    """A recording, video edit or video project of this conversation, by its id; None for any other name."""
    from row_bot import recordings, video_edits, video_projects

    if recordings.RECORDING_ID.fullmatch(text):
        record = recordings.get(text)
        if record is None or record.conversation_id != conversation_id:
            raise SourceError(f"{text} isn't a recording in this chat.")
        if record.status != "saved" or not record.media_ref:
            raise SourceError(f"{text} has no saved video yet.")
        label = "Screen recording" if record.kind == "screen" else "Browser recording"
        return [Media(label=label, kind="video", ref=record.media_ref)]
    named = re.fullmatch(r"(edit-[0-9a-f]{8})[/\\](.+)", text)
    if named:
        return [_edit_frame(named.group(1), named.group(2).strip(), conversation_id)]
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
    project = re.fullmatch(rf"({video_projects.PROJECT_ID.pattern})(?:@v(\d{{1,6}}))?", text)
    if not project:
        return None
    found = video_projects.get(project.group(1), conversation_id)
    if found is None:
        raise SourceError(f"{project.group(1)} isn't a video project in this chat.")
    number = int(project.group(2)) if project.group(2) else None
    for version in [found.version(number)] if number else list(reversed(found.versions)):
        made = video_edits.get(version.edit_id) if version is not None and version.edit_id else None
        if made is not None and made.status == "saved" and made.media_ref:
            return [Media(label=made.output_name or found.title, kind="video", ref=made.media_ref)]
    if number:
        raise SourceError(f"{text} has no made video: make that version first (video action make).")
    raise SourceError(f"{text} has no made video yet: make it first (video action make).")


def _workspace_media(place: Workspace, text: str) -> Media:
    path = workspace_file(place.root, text)
    return Media(label=_relative(place.root, str(path)), kind=_file_kind(path.name), path=path)


def _file(text: str, conversation_id: str, *, kinds: frozenset[str] | None, outside: bool,
          allowed: frozenset[Path] | None) -> Media:
    """A file by its path or its name (steps 2 to 5 of the order above)."""
    from row_bot.data_paths import is_private_data_path

    given = Path(text).expanduser()
    absolute = given.is_absolute() or (len(text) > 1 and text[1] == ":")
    if absolute and is_private_data_path(given):
        raise SourceError("That path is inside Row-Bot's private data folder, which tools can't read.")
    place = workspace()
    if absolute and outside:
        path = _outside_file(text, place, allowed=allowed)
        if path is not None:
            return Media(label=str(path), kind=_kind_of(path.name) or "video", path=path, outside=True)
    if absolute or "/" in text or "\\" in text:
        if place is None:
            raise SourceError(f"{text} isn't in the workspace: there's no workspace folder (the person can choose "
                              "one in Settings).")
        return _workspace_media(place, text)
    files = _files(conversation_id)
    at = _AT_VERSION.fullmatch(text)
    if at:
        return _version(files, at.group(1).strip(), int(at.group(2)), kinds)
    named = _of_kinds(_named(files, text), kinds)
    if named:
        return _chat_media(named[0])
    if place is not None:
        try:
            return _workspace_media(place, text)
        except SourceError:
            pass
    raise SourceError(_missing(text, files))


def find(value: str, conversation_id: str, *, kinds: frozenset[str] | None = MEDIA,
         outside: bool = True, allowed: frozenset[Path] | None = None) -> list[Media]:
    """The files a name points to, found in the order this module gives (an edit that saved frames names several).

    *kinds*: what the tool reads (None: any file); a file of another kind is refused in plain words. *outside*:
    whether a full path elsewhere on this computer may be read in place once the person allows it (a tool that
    sends or shows files never reads one). *allowed*: the files outside the workspace the person already allowed
    for this use (a video project's footage), read without asking; any other file outside it is refused instead of
    asked about. ``SourceError`` says, in plain words, why a name finds nothing.
    """
    text = str(value or "").strip().strip('"')
    if not text:
        raise SourceError("A source is empty.")
    found = _own(text, conversation_id)
    if found is None:
        found = [_file(text, conversation_id, kinds=kinds, outside=outside, allowed=allowed)]
    for media in found:
        if kinds is not None and media.kind not in kinds:
            raise SourceError(f"{media.label} isn't " + " or ".join(
                words for kind, words in _KIND_WORDS.items() if kind in kinds) + ".")
    return found


def resolve(value: str, conversation_id: str, *, allowed: frozenset[Path] | None = None) -> list[Media]:
    """The videos or pictures a source names: ``find`` for a tool that reads them (``allowed`` as there)."""
    return find(value, conversation_id, allowed=allowed)
