from __future__ import annotations

import builtins
import importlib
import importlib.abc
import importlib.util
import io
import os
import pathlib
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PARENT = PROJECT_ROOT / "src"
SRC_ROOT = SRC_PARENT / "row_bot"
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
LEGACY_IMPORT_ROOTS = frozenset(path.removesuffix(".py") for path in MOVED_SOURCE_FILES) | MOVED_SOURCE_DIRS


_ORIGINAL_BUILTINS_OPEN = builtins.open
_ORIGINAL_IO_OPEN = io.open
_ORIGINAL_PATH_OPEN = pathlib.Path.open
_ORIGINAL_PATH_EXISTS = pathlib.Path.exists
_ORIGINAL_PATH_IS_FILE = pathlib.Path.is_file
_ORIGINAL_PATH_IS_DIR = pathlib.Path.is_dir
_ORIGINAL_PATH_GLOB = pathlib.Path.glob
_ORIGINAL_PATH_ITERDIR = pathlib.Path.iterdir
_ORIGINAL_OS_PATH_EXISTS = os.path.exists
_ORIGINAL_OS_PATH_ISFILE = os.path.isfile
_ORIGINAL_OS_PATH_ISDIR = os.path.isdir
_INSTALLED = False


def _is_write_mode(mode: Any) -> bool:
    text = str(mode or "r")
    return any(flag in text for flag in ("w", "a", "x", "+"))


def moved_source_candidate(path: Any, project_root: Path | None = None) -> Path | None:
    root = (project_root or PROJECT_ROOT).resolve(strict=False)
    src_root = root / "src" / "row_bot"
    try:
        raw = Path(path)
    except (TypeError, OSError, RuntimeError):
        return None
    try:
        absolute = raw if raw.is_absolute() else Path.cwd() / raw
        resolved = absolute.resolve(strict=False)
        rel = resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    if not rel.parts or rel.parts[0] == "src":
        return None
    if rel.parts[0] in MOVED_SOURCE_DIRS or (len(rel.parts) == 1 and rel.name in MOVED_SOURCE_FILES):
        candidate = src_root / rel
        if _ORIGINAL_PATH_EXISTS(candidate):
            return candidate
    return None


def _source_read_path(file: Any, mode: str) -> Any:
    if _is_write_mode(mode):
        return file
    redirected = moved_source_candidate(file)
    return redirected if redirected is not None else file


def _source_path(path: Any) -> Any:
    redirected = moved_source_candidate(path)
    return redirected if redirected is not None else path


def _layout_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any):
    return _ORIGINAL_BUILTINS_OPEN(_source_read_path(file, mode), mode, *args, **kwargs)


def _layout_io_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any):
    return _ORIGINAL_IO_OPEN(_source_read_path(file, mode), mode, *args, **kwargs)


def _layout_path_open(self: pathlib.Path, mode: str = "r", *args: Any, **kwargs: Any):
    return _ORIGINAL_PATH_OPEN(_source_read_path(self, mode), mode, *args, **kwargs)


def _layout_exists(self: pathlib.Path) -> bool:
    if _ORIGINAL_PATH_EXISTS(self):
        return True
    return moved_source_candidate(self) is not None


def _layout_is_file(self: pathlib.Path) -> bool:
    if _ORIGINAL_PATH_IS_FILE(self):
        return True
    redirected = moved_source_candidate(self)
    return bool(redirected and _ORIGINAL_PATH_IS_FILE(redirected))


def _layout_is_dir(self: pathlib.Path) -> bool:
    if _ORIGINAL_PATH_IS_DIR(self):
        return True
    redirected = moved_source_candidate(self)
    return bool(redirected and _ORIGINAL_PATH_IS_DIR(redirected))


def _layout_glob(self: pathlib.Path, pattern: str, *args: Any, **kwargs: Any):
    redirected = moved_source_candidate(self)
    return _ORIGINAL_PATH_GLOB(redirected or self, pattern, *args, **kwargs)


def _layout_iterdir(self: pathlib.Path):
    redirected = moved_source_candidate(self)
    return _ORIGINAL_PATH_ITERDIR(redirected or self)


def _layout_os_path_exists(path: Any) -> bool:
    return _ORIGINAL_OS_PATH_EXISTS(path) or moved_source_candidate(path) is not None


def _layout_os_path_isfile(path: Any) -> bool:
    if _ORIGINAL_OS_PATH_ISFILE(path):
        return True
    redirected = moved_source_candidate(path)
    return bool(redirected and _ORIGINAL_PATH_IS_FILE(redirected))


def _layout_os_path_isdir(path: Any) -> bool:
    if _ORIGINAL_OS_PATH_ISDIR(path):
        return True
    redirected = moved_source_candidate(path)
    return bool(redirected and _ORIGINAL_PATH_IS_DIR(redirected))


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


def install_source_layout_compat(project_root: Path | str | None = None) -> None:
    global _INSTALLED
    root = Path(project_root or PROJECT_ROOT).resolve(strict=False)
    src_parent = root / "src"
    for path in (src_parent, root):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)
    if _INSTALLED:
        return
    sys.meta_path.insert(0, _LegacyRowBotImportFinder())
    builtins.open = _layout_open
    io.open = _layout_io_open
    pathlib.Path.open = _layout_path_open
    pathlib.Path.exists = _layout_exists
    pathlib.Path.is_file = _layout_is_file
    pathlib.Path.is_dir = _layout_is_dir
    pathlib.Path.glob = _layout_glob
    pathlib.Path.iterdir = _layout_iterdir
    os.path.exists = _layout_os_path_exists
    os.path.isfile = _layout_os_path_isfile
    os.path.isdir = _layout_os_path_isdir
    _INSTALLED = True
