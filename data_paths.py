"""Shared filesystem paths for Thoth local data."""

from __future__ import annotations

import os
from pathlib import Path


def get_thoth_data_dir(*, create: bool = True) -> Path:
    """Return the Thoth data directory, honoring ``THOTH_DATA_DIR``."""
    path = Path(os.environ.get("THOTH_DATA_DIR") or Path.home() / ".thoth")
    path = path.expanduser()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def get_tasks_db_path(*, create_parent: bool = True) -> Path:
    """Return the task database path."""
    data_dir = get_thoth_data_dir(create=create_parent)
    return data_dir / "tasks.db"


def get_memory_db_path(*, create_parent: bool = True) -> Path:
    """Return the memory database path."""
    data_dir = get_thoth_data_dir(create=create_parent)
    return data_dir / "memory.db"


def get_threads_db_path(*, create_parent: bool = True) -> Path:
    """Return the thread metadata/checkpoint database path."""
    data_dir = get_thoth_data_dir(create=create_parent)
    return data_dir / "threads.db"


def describe_data_paths() -> dict[str, str]:
    """Return support-friendly local data paths."""
    data_dir = get_thoth_data_dir()
    return {
        "data_dir": str(data_dir),
        "tasks_db": str(data_dir / "tasks.db"),
        "memory_db": str(data_dir / "memory.db"),
        "threads_db": str(data_dir / "threads.db"),
        "logs_dir": str(data_dir / "logs"),
    }
