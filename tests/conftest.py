from __future__ import annotations

import builtins
import importlib
import importlib.abc
import importlib.util
import io
import os
import pathlib
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEST_DATA_DIR = PROJECT_ROOT / ".tmp" / "pytest_thoth"
DEFAULT_TEST_TMP_DIR = PROJECT_ROOT / ".tmp" / "pytest_tmp"
LIVE_USER_DATA_DIR = Path.home() / ".thoth"
LIVE_ROW_BOT_DATA_DIR = Path.home() / ".row-bot"
SRC_ROOT = PROJECT_ROOT / "src" / "row_bot"
MOVED_SOURCE_DIRS = frozenset(
    {
        "buddy",
        "channels",
        "designer",
        "developer",
        "mcp_client",
        "migration",
        "plugins",
        "providers",
        "skills_hub",
        "tools",
        "ui",
        "utils",
        "voice",
    }
)
MOVED_SOURCE_FILES = frozenset(
    {
        "agent.py",
        "api_keys.py",
        "app.py",
        "app_port.py",
        "approval_policy.py",
        "brand.py",
        "data_paths.py",
        "data_reader.py",
        "document_extraction.py",
        "documents.py",
        "dream_cycle.py",
        "embedding_config.py",
        "embedding_providers.py",
        "github_account.py",
        "identity.py",
        "insights.py",
        "knowledge_graph.py",
        "launcher.py",
        "logging_config.py",
        "memory.py",
        "memory_evolution.py",
        "memory_extraction.py",
        "memory_policy.py",
        "models.py",
        "notifications.py",
        "prompts.py",
        "secret_store.py",
        "self_knowledge.py",
        "skills.py",
        "skills_activation.py",
        "slash_commands.py",
        "stability.py",
        "startup_diagnostics.py",
        "tasks.py",
        "terminal_bridge.py",
        "terminal_pty.py",
        "threads.py",
        "tts.py",
        "tunnel.py",
        "updater.py",
        "version.py",
        "vision.py",
        "wiki_vault.py",
    }
)
LEGACY_IMPORT_ROOTS = frozenset(
    path.removesuffix(".py")
    for path in MOVED_SOURCE_FILES
) | MOVED_SOURCE_DIRS


def _resolve_for_guard(path: Any) -> Path | None:
    try:
        return Path(path).expanduser().resolve(strict=False)
    except (TypeError, OSError, RuntimeError):
        return None


def _is_under(path: Path | None, root: Path) -> bool:
    if path is None:
        return False
    try:
        path.relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _is_live_user_state_path(path: Any) -> bool:
    resolved = _resolve_for_guard(path)
    return _is_under(resolved, LIVE_USER_DATA_DIR) or _is_under(resolved, LIVE_ROW_BOT_DATA_DIR)


def _set_default_test_data_env() -> None:
    os.environ["ROW_BOT_DATA_DIR"] = str(DEFAULT_TEST_DATA_DIR)
    os.environ["THOTH_DATA_DIR"] = str(DEFAULT_TEST_DATA_DIR)


def _is_write_mode(mode: Any) -> bool:
    text = str(mode or "r")
    return any(flag in text for flag in ("w", "a", "x", "+"))


def _live_write_allowed() -> bool:
    return (
        os.environ.get("ROW_BOT_ALLOW_LIVE_USER_STATE_WRITES") == "1"
        or os.environ.get("THOTH_ALLOW_LIVE_USER_STATE_WRITES") == "1"
    )


def _raise_live_write(path: Any, operation: str) -> None:
    if _live_write_allowed():
        return
    raise AssertionError(
        f"pytest attempted to {operation} live app user state: {path}. "
        f"Use ROW_BOT_DATA_DIR under {DEFAULT_TEST_DATA_DIR.parent} for tests."
    )


# Establish a non-live data directory before test modules import app code. If a
# developer shell already points at live user state, override it for test safety.
existing_data_dir = os.environ.get("ROW_BOT_DATA_DIR") or os.environ.get("THOTH_DATA_DIR")
if not existing_data_dir or _is_live_user_state_path(existing_data_dir):
    existing_data_dir = str(DEFAULT_TEST_DATA_DIR)
os.environ["ROW_BOT_DATA_DIR"] = existing_data_dir
os.environ["THOTH_DATA_DIR"] = existing_data_dir
DEFAULT_TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_TEST_TMP_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("TMP", str(DEFAULT_TEST_TMP_DIR))
os.environ.setdefault("TEMP", str(DEFAULT_TEST_TMP_DIR))
os.environ.setdefault("ROW_BOT_TEST_MODE", "1")
os.environ.setdefault("THOTH_TEST_MODE", "1")


_ORIGINAL_BUILTINS_OPEN = builtins.open
_ORIGINAL_IO_OPEN = io.open
_ORIGINAL_PATH_OPEN = pathlib.Path.open
_ORIGINAL_PATH_WRITE_TEXT = pathlib.Path.write_text
_ORIGINAL_PATH_WRITE_BYTES = pathlib.Path.write_bytes
_ORIGINAL_PATH_MKDIR = pathlib.Path.mkdir
_ORIGINAL_PATH_REPLACE = pathlib.Path.replace
_ORIGINAL_PATH_EXISTS = pathlib.Path.exists
_ORIGINAL_PATH_IS_FILE = pathlib.Path.is_file
_ORIGINAL_OS_REPLACE = os.replace
_ORIGINAL_SQLITE_CONNECT = sqlite3.connect
_ORIGINAL_MONKEYPATCH_SETENV = pytest.MonkeyPatch.setenv


def _moved_source_candidate(path: Any) -> Path | None:
    try:
        raw = Path(path)
    except (TypeError, OSError, RuntimeError):
        return None
    try:
        absolute = raw if raw.is_absolute() else Path.cwd() / raw
        resolved = absolute.resolve(strict=False)
        rel = resolved.relative_to(PROJECT_ROOT)
    except (OSError, RuntimeError, ValueError):
        return None
    if not rel.parts or rel.parts[0] == "src":
        return None
    if rel.parts[0] in MOVED_SOURCE_DIRS or (len(rel.parts) == 1 and rel.name in MOVED_SOURCE_FILES):
        candidate = SRC_ROOT / rel
        if _ORIGINAL_PATH_EXISTS(candidate):
            return candidate
    return None


def _source_read_path(file: Any, mode: str) -> Any:
    if _is_write_mode(mode):
        return file
    redirected = _moved_source_candidate(file)
    return redirected if redirected is not None else file


def _guarded_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any):
    file = _source_read_path(file, mode)
    if _is_write_mode(mode) and _is_live_user_state_path(file):
        _raise_live_write(file, f"open with mode {mode!r}")
    return _ORIGINAL_BUILTINS_OPEN(file, mode, *args, **kwargs)


def _guarded_io_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any):
    file = _source_read_path(file, mode)
    if _is_write_mode(mode) and _is_live_user_state_path(file):
        _raise_live_write(file, f"io.open with mode {mode!r}")
    return _ORIGINAL_IO_OPEN(file, mode, *args, **kwargs)


def _guarded_path_open(self: pathlib.Path, mode: str = "r", *args: Any, **kwargs: Any):
    source_path = _source_read_path(self, mode)
    if _is_write_mode(mode) and _is_live_user_state_path(self):
        _raise_live_write(self, f"Path.open with mode {mode!r}")
    return _ORIGINAL_PATH_OPEN(source_path, mode, *args, **kwargs)


def _guarded_write_text(self: pathlib.Path, *args: Any, **kwargs: Any):
    if _is_live_user_state_path(self):
        _raise_live_write(self, "Path.write_text")
    return _ORIGINAL_PATH_WRITE_TEXT(self, *args, **kwargs)


def _guarded_write_bytes(self: pathlib.Path, *args: Any, **kwargs: Any):
    if _is_live_user_state_path(self):
        _raise_live_write(self, "Path.write_bytes")
    return _ORIGINAL_PATH_WRITE_BYTES(self, *args, **kwargs)


def _guarded_mkdir(self: pathlib.Path, *args: Any, **kwargs: Any):
    if _is_live_user_state_path(self):
        _raise_live_write(self, "Path.mkdir")
    return _ORIGINAL_PATH_MKDIR(self, *args, **kwargs)


def _guarded_path_replace(self: pathlib.Path, target: Any, *args: Any, **kwargs: Any):
    if _is_live_user_state_path(target):
        _raise_live_write(target, "Path.replace target")
    return _ORIGINAL_PATH_REPLACE(self, target, *args, **kwargs)


def _guarded_os_replace(src: Any, dst: Any, *args: Any, **kwargs: Any):
    if _is_live_user_state_path(dst):
        _raise_live_write(dst, "os.replace target")
    return _ORIGINAL_OS_REPLACE(src, dst, *args, **kwargs)


def _source_aware_exists(self: pathlib.Path):
    if _ORIGINAL_PATH_EXISTS(self):
        return True
    return _moved_source_candidate(self) is not None


def _source_aware_is_file(self: pathlib.Path):
    if _ORIGINAL_PATH_IS_FILE(self):
        return True
    redirected = _moved_source_candidate(self)
    return bool(redirected and _ORIGINAL_PATH_IS_FILE(redirected))


def _guarded_sqlite_connect(database: Any, *args: Any, **kwargs: Any):
    if _is_live_user_state_path(database):
        _raise_live_write(database, "sqlite3.connect")
    return _ORIGINAL_SQLITE_CONNECT(database, *args, **kwargs)


def _synced_setenv(self: pytest.MonkeyPatch, name: str, value: str, *args: Any, **kwargs: Any) -> None:
    previous_row_bot = os.environ.get("ROW_BOT_DATA_DIR")
    _ORIGINAL_MONKEYPATCH_SETENV(self, name, value, *args, **kwargs)
    if name == "THOTH_DATA_DIR" and (
        previous_row_bot is None
        or Path(previous_row_bot).resolve(strict=False) == DEFAULT_TEST_DATA_DIR.resolve(strict=False)
    ):
        _ORIGINAL_MONKEYPATCH_SETENV(self, "ROW_BOT_DATA_DIR", value, *args, **kwargs)
    elif name == "ROW_BOT_DATA_DIR":
        previous_thoth = os.environ.get("THOTH_DATA_DIR")
        if previous_thoth is None or Path(previous_thoth).resolve(strict=False) == DEFAULT_TEST_DATA_DIR.resolve(strict=False):
            _ORIGINAL_MONKEYPATCH_SETENV(self, "THOTH_DATA_DIR", value, *args, **kwargs)


class _LegacyRowBotImportFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname: str, path: Any = None, target: Any = None):
        root = fullname.split(".", 1)[0]
        if root not in LEGACY_IMPORT_ROOTS or fullname.startswith("row_bot."):
            return None
        if fullname in sys.modules:
            return None
        target_name = f"row_bot.{fullname}"
        target_spec = importlib.util.find_spec(target_name)
        if target_spec is None:
            return None
        spec = importlib.util.spec_from_loader(
            fullname,
            self,
            origin=target_spec.origin,
            is_package=target_spec.submodule_search_locations is not None,
        )
        spec.loader_state = {"target_name": target_name}
        return spec

    def create_module(self, spec):
        target_name = spec.loader_state["target_name"]
        module = importlib.import_module(target_name)
        spec.loader_state["target_attrs"] = {
            "__loader__": getattr(module, "__loader__", None),
            "__name__": getattr(module, "__name__", None),
            "__package__": getattr(module, "__package__", None),
            "__spec__": getattr(module, "__spec__", None),
        }
        sys.modules[spec.name] = module
        return module

    def exec_module(self, module):
        state = getattr(module, "__spec__", None)
        loader_state = getattr(state, "loader_state", None) or {}
        for name, value in (loader_state.get("target_attrs") or {}).items():
            setattr(module, name, value)
        return None


sys.meta_path.insert(0, _LegacyRowBotImportFinder())
builtins.open = _guarded_open
io.open = _guarded_io_open
pathlib.Path.open = _guarded_path_open
pathlib.Path.write_text = _guarded_write_text
pathlib.Path.write_bytes = _guarded_write_bytes
pathlib.Path.mkdir = _guarded_mkdir
pathlib.Path.replace = _guarded_path_replace
pathlib.Path.exists = _source_aware_exists
pathlib.Path.is_file = _source_aware_is_file
os.replace = _guarded_os_replace
sqlite3.connect = _guarded_sqlite_connect
pytest.MonkeyPatch.setenv = _synced_setenv


@pytest.fixture(autouse=True)
def _reset_test_data_env_between_tests():
    _set_default_test_data_env()
    yield
    _set_default_test_data_env()
