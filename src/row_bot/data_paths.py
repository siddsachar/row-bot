"""Shared filesystem paths for local app data during the Row-Bot rebrand."""

from __future__ import annotations

import os
from pathlib import Path

from row_bot.brand import APP_DATA_DIR_ENV, default_data_dir


def get_row_bot_target_data_dir(*, create: bool = True) -> Path:
    """Return the canonical Row-Bot target data directory."""
    path = Path(os.environ.get(APP_DATA_DIR_ENV) or default_data_dir())
    path = path.expanduser()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def get_row_bot_data_dir(*, create: bool = True) -> Path:
    """Return the currently active Row-Bot data directory."""
    return get_row_bot_target_data_dir(create=create)


def default_workspace_root() -> Path:
    """The workspace folder until the person chooses one: ``ROW_BOT_WORKSPACE`` (a sandbox's own), else
    Documents/Row-Bot in their home."""
    from row_bot.brand import APP_WORKSPACE_ENV, DEFAULT_WORKSPACE_DIR_NAME

    configured = str(os.environ.get(APP_WORKSPACE_ENV) or "").strip()
    return Path(configured).expanduser() if configured else Path.home() / "Documents" / DEFAULT_WORKSPACE_DIR_NAME


def private_data_dirs() -> list[Path]:
    """Row-Bot's data folder, and the server secrets folder when one is in use (a self-hosted install's keys);
    in a sandbox (5.3.1 B4), the real data folder it was started from too."""
    from row_bot.secret_store import server_secrets_dir

    folders = [get_row_bot_data_dir(create=False)]
    secrets_dir = server_secrets_dir()
    if secrets_dir is not None:
        folders.append(secrets_dir)
    parent = str(os.environ.get("ROW_BOT_SANDBOX_OF") or "").strip()
    if parent:
        folders.append(Path(parent))
    return folders


def sandbox_workspace() -> Path | None:
    """In a sandbox (5.3.1 B4), its own workspace: it lies inside the data folder the sandbox was started
    from, which is otherwise private to it."""
    from row_bot.brand import APP_WORKSPACE_ENV

    configured = str(os.environ.get(APP_WORKSPACE_ENV) or "").strip()
    if not configured or not str(os.environ.get("ROW_BOT_SANDBOX_OF") or "").strip():
        return None
    return Path(configured).expanduser().resolve()


def own_workspaces() -> list[Path]:
    """Workspaces that lie inside a private folder on purpose and stay the agent's to use: a sandbox's own
    (5.3.1 B4); otherwise the default one, Documents/Row-Bot in the home folder. On a computer the home and the
    data folder are apart, but in the container image the home is the data folder (HOME=/data), so the default
    workspace lies inside it. A sandbox never gets the real Row-Bot's workspace."""
    from row_bot.brand import DEFAULT_WORKSPACE_DIR_NAME

    sandbox = sandbox_workspace()
    if sandbox is not None:
        return [sandbox]
    return [(Path.home() / "Documents" / DEFAULT_WORKSPACE_DIR_NAME).expanduser().resolve()]


def is_private_data_path(path: str | os.PathLike[str]) -> bool:
    """True when *path* is Row-Bot's data folder or the server secrets folder, or inside one of them; an own
    workspace (``own_workspaces``) inside one of them is not."""
    try:
        resolved = Path(path).expanduser().resolve()
        folders = [folder.expanduser().resolve() for folder in private_data_dirs()]
        workspaces = own_workspaces()
    except (OSError, ValueError):
        return False

    def inside(child: Path, parent: Path) -> bool:
        return child == parent or parent in child.parents

    return any(inside(resolved, folder)
               and not any(inside(resolved, workspace) and folder in workspace.parents for workspace in workspaces)
               for folder in folders)


# Parts of the data folder that hold tools Row-Bot downloaded and results it made, not conversations,
# settings, approvals or keys. A command may use them like any other folder, by the approval level
# (5.3.1 A1): the managed ffmpeg, recordings and exported media, logs.
SHARED_DATA_FOLDERS = frozenset({
    "ffmpeg", "runtimes", "node", "media", "recordings", "exports", "downloads", "channel_outputs",
    "vision-frames", "logs", "font_cache", "published",
})


def shared_data_folder(relative: str) -> bool:
    """True when *relative* (a path inside the data folder, with / separators) lies in a shared part of it."""
    first = relative.strip("/").split("/", 1)[0]
    return first in SHARED_DATA_FOLDERS


def get_tasks_db_path(*, create_parent: bool = True) -> Path:
    """Return the task database path."""
    data_dir = get_row_bot_data_dir(create=create_parent)
    return data_dir / "tasks.db"


def get_memory_db_path(*, create_parent: bool = True) -> Path:
    """Return the memory database path."""
    data_dir = get_row_bot_data_dir(create=create_parent)
    return data_dir / "memory.db"


def get_threads_db_path(*, create_parent: bool = True) -> Path:
    """Return the thread metadata/checkpoint database path."""
    data_dir = get_row_bot_data_dir(create=create_parent)
    return data_dir / "threads.db"


def get_usage_db_path(*, create_parent: bool = True) -> Path:
    """Return the local model-usage ledger path (token counts only)."""
    data_dir = get_row_bot_data_dir(create=create_parent)
    return data_dir / "usage.db"


def get_access_db_path(*, create_parent: bool = True) -> Path:
    """Return the access database path.

    The physical filename remains ``mobile.db`` for a safe in-place migration
    of existing companion devices.
    """
    data_dir = get_row_bot_data_dir(create=create_parent)
    return data_dir / "mobile.db"


def get_mobile_db_path(*, create_parent: bool = True) -> Path:
    """Compatibility alias for :func:`get_access_db_path`."""
    return get_access_db_path(create_parent=create_parent)


def describe_data_paths() -> dict[str, str]:
    """Return support-friendly local data paths."""
    data_dir = get_row_bot_data_dir(create=False)
    return {
        "data_dir": str(data_dir),
        "tasks_db": str(data_dir / "tasks.db"),
        "memory_db": str(data_dir / "memory.db"),
        "threads_db": str(data_dir / "threads.db"),
        "access_db": str(data_dir / "mobile.db"),
        "mobile_db": str(data_dir / "mobile.db"),
        "logs_dir": str(data_dir / "logs"),
    }
