"""Row-Bot's own user in its container, when a host starts the container as root (R1).

The image runs as uid 10001. Some hosts start every container as root: Railway mounts its volumes
owned by root and tells images with a user of their own to set ``RAILWAY_RUN_UID=0``. Started as
root inside its container, the first thing Row-Bot does is make the data folder 10001's, then become
10001 for good (no supplementary groups, then the group, then the user), before it writes the data
folder, reads a secret or starts any program. Started as 10001 nothing changes, and root outside a
container stays as it was. If any step fails, Row-Bot stops: it never goes on as root.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import stat
from typing import Any, Mapping

from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

# The image's user and group (deploy/docker/Dockerfile: USER 10001:10001).
CONTAINER_UID = 10001
CONTAINER_GID = 10001
CONTAINERIZED_ENV = "ROW_BOT_CONTAINERIZED"
DEPLOYMENT_MODE_ENV = "ROW_BOT_DEPLOYMENT_MODE"
# Folders the image's environment keeps in the data folder. The image makes them, but a host's
# volume hides the image's own copies, so they are made again, as Row-Bot's user.
_IMAGE_FOLDERS = ("TMPDIR", "XDG_CACHE_HOME", "HF_HOME", "TORCH_HOME", "SENTENCE_TRANSFORMERS_HOME", "UV_CACHE_DIR")

# The operating system's calls, in one place so a test can stand in for root.
_system: Any = os


class ContainerUserError(RuntimeError):
    """Row-Bot can't run as its own user, so it doesn't start."""


def started_as_root_in_container(environ: Mapping[str, str] | None = None) -> bool:
    """Root, inside Row-Bot's own image (``ROW_BOT_CONTAINERIZED=1``, server mode)."""
    env = os.environ if environ is None else environ
    geteuid = getattr(_system, "geteuid", None)
    return (
        geteuid is not None
        and geteuid() == 0
        and env.get(CONTAINERIZED_ENV) == "1"
        and str(env.get(DEPLOYMENT_MODE_ENV) or "").strip().lower() == "server"
    )


def give_data_folder(data_dir: Path, *, uid: int = CONTAINER_UID, gid: int = CONTAINER_GID) -> bool:
    """Make ``data_dir`` and everything in it ``uid``'s, unless its top already is. True when it changed it.

    A volume a host mounts as root (Railway's, with its ``lost+found``) is handed over once; after that
    its top is Row-Bot's and the next start leaves it alone. The top changes last, so a start stopped
    half-way hands it over again. Symbolic links are changed themselves, never followed, and nothing on
    another file system mounted inside is touched.
    """
    if not data_dir.is_absolute() or data_dir == Path(data_dir.anchor):
        raise ContainerUserError(f"the data folder must be a folder of its own, not {data_dir}")
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    top = _system.lstat(data_dir)
    if not stat.S_ISDIR(top.st_mode):
        raise ContainerUserError(f"the data folder {data_dir} is not a folder")
    if (top.st_uid, top.st_gid) == (uid, gid):
        return False
    for root, folders, files in os.walk(data_dir):
        kept: list[str] = []
        for name in (*folders, *files):
            path = os.path.join(root, name)
            entry = _system.lstat(path)
            if entry.st_dev != top.st_dev:
                continue
            _system.chown(path, uid, gid, follow_symlinks=False)
            if name in folders and stat.S_ISDIR(entry.st_mode):
                kept.append(name)
        folders[:] = kept
    _system.chown(data_dir, uid, gid, follow_symlinks=False)
    _system.chmod(data_dir, 0o700)
    return True


def become_container_user(*, uid: int = CONTAINER_UID, gid: int = CONTAINER_GID) -> None:
    """Drop root for good: no supplementary groups, then the group, then the user (real, effective, saved)."""
    _system.setgroups([])
    _system.setresgid(gid, gid, gid)
    _system.setresuid(uid, uid, uid)
    if (
        tuple(_system.getresuid()) != (uid, uid, uid)
        or tuple(_system.getresgid()) != (gid, gid, gid)
        or list(_system.getgroups())
    ):
        raise ContainerUserError(f"Row-Bot couldn't become uid {uid}")
    try:
        _system.setuid(0)
    except PermissionError:
        return
    raise ContainerUserError("Row-Bot could become root again")


def run_as_container_user(environ: Mapping[str, str] | None = None) -> bool:
    """The launcher's first step: started as root in the container, run as Row-Bot's user. True when it did.

    Raises ``ContainerUserError`` when it can't; the caller stops.
    """
    env = os.environ if environ is None else environ
    if not started_as_root_in_container(env):
        return False
    data_dir = get_row_bot_data_dir(create=False)
    try:
        handed_over = give_data_folder(data_dir)
        become_container_user()
        for name in _IMAGE_FOLDERS:
            folder = Path(str(env.get(name) or "")).expanduser()
            if folder.is_absolute() and folder.is_relative_to(data_dir):
                folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise ContainerUserError(
            f"started as root, Row-Bot couldn't give {data_dir} to uid {CONTAINER_UID} and run as it "
            f"({exc.strerror or type(exc).__name__})"
        ) from None
    if handed_over:
        logger.info(
            "Started as root in its container: Row-Bot gave %s to uid %d and runs as uid %d",
            data_dir, CONTAINER_UID, CONTAINER_UID,
        )
    else:
        logger.info("Started as root in its container: Row-Bot runs as uid %d", CONTAINER_UID)
    return True


__all__ = [
    "CONTAINER_GID", "CONTAINER_UID", "ContainerUserError", "become_container_user", "give_data_folder",
    "run_as_container_user", "started_as_root_in_container",
]
