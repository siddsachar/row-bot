"""Save to workspace (W3): a copy of one of a chat's files in the person's workspace, under its own name."""

from __future__ import annotations

import hashlib
import os
import re

# The folder in the workspace that Save to workspace puts files in; the Outputs pane names it.
SAVED_FOLDER = "Saved outputs"
# Characters a file name in a chat may hold but a workspace path can't ("Q&A: v2?" keeps its words).
_UNSAFE = re.compile(r'[*?"<>|\x00-\x1f]')


def save_output(conversation_id: str, media_ref: str) -> str:
    """Copy one of this chat's files, of any kind, into the workspace's "Saved outputs" folder under the name it
    has in the chat ("Birthday montage.mp4", "Q3 budget £480.pdf"); the path saved to, inside the workspace
    ("Saved outputs/Birthday montage.mp4").

    The same file saved again returns the same path. Another file already under that name is kept, and this one
    is saved as name-2, name-3, …. A workspace that is a code folder is refused: Developer imports there.
    """
    from row_bot.application.attachments import _numbered, read_attachment
    from row_bot.data_paths import workspace_folder
    from row_bot.developer.storage import list_workspaces
    from row_bot.media_sources import SourceError, workspace_target

    if not media_ref.startswith(conversation_id + ":"):
        raise ValueError("media_scope_conflict")
    metadata, data = read_attachment(media_ref)
    root = workspace_folder(create=True).absolute()
    for workspace in list_workspaces():
        if (root / SAVED_FOLDER).resolve().is_relative_to(os.path.realpath(os.path.expanduser(workspace.path))):
            raise ValueError("media_destination_is_code_folder")
    name = _UNSAFE.sub("-", str(metadata["name"])).strip(" .") or "output"
    digest = hashlib.sha256(data).hexdigest()
    taken: set[str] = set()
    candidate = name
    while True:
        try:
            target, relative, exists = workspace_target(root, f"{SAVED_FOLDER}/{candidate}")
        except SourceError:
            raise ValueError("media_destination_unavailable") from None
        if not exists:
            break
        if not target.is_symlink() and target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
            return relative
        taken.add(candidate.casefold())
        candidate = _numbered(name, taken)
    try:
        target.parent.mkdir(exist_ok=True)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:  # Saved by another request a moment ago.
        raise ValueError("media_destination_conflict") from None
    except OSError:
        raise ValueError("media_destination_unavailable") from None
    try:
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return relative
