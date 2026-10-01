from __future__ import annotations

import builtins
import errno
import importlib
import io
import ipaddress
import os
import pathlib
import socket
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
# pytest-xdist workers run side by side: each gets its own data folder (temporary
# files already get unique names per process).
XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")
DEFAULT_TEST_DATA_DIR = PROJECT_ROOT / ".tmp" / "pytest_row_bot" / XDIST_WORKER
DEFAULT_TEST_TMP_DIR = PROJECT_ROOT / ".tmp" / "pytest_tmp"
LEGACY_DATA_DIR_NAME = ".thoth"
LIVE_LEGACY_DATA_DIR = Path.home() / LEGACY_DATA_DIR_NAME
LIVE_ROW_BOT_DATA_DIR = Path.home() / ".row-bot"


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
    return _is_under(resolved, LIVE_LEGACY_DATA_DIR) or _is_under(resolved, LIVE_ROW_BOT_DATA_DIR)


def _set_default_test_data_env() -> None:
    os.environ["ROW_BOT_DATA_DIR"] = str(DEFAULT_TEST_DATA_DIR)


def _is_write_mode(mode: Any) -> bool:
    text = str(mode or "r")
    return any(flag in text for flag in ("w", "a", "x", "+"))


def _live_write_allowed() -> bool:
    return os.environ.get("ROW_BOT_ALLOW_LIVE_USER_STATE_WRITES") == "1"


def _raise_live_write(path: Any, operation: str) -> None:
    if _live_write_allowed():
        return
    raise AssertionError(
        f"pytest attempted to {operation} live app user state: {path}. "
        f"Use ROW_BOT_DATA_DIR under {DEFAULT_TEST_DATA_DIR.parent} for tests."
    )


# Establish a non-live data directory before test modules import app code. If a
# developer shell already points at live user state, override it for test safety.
existing_data_dir = os.environ.get("ROW_BOT_DATA_DIR")
if not existing_data_dir or _is_live_user_state_path(existing_data_dir):
    existing_data_dir = str(DEFAULT_TEST_DATA_DIR)
elif XDIST_WORKER:
    existing_data_dir = str(Path(existing_data_dir) / XDIST_WORKER)
os.environ["ROW_BOT_DATA_DIR"] = existing_data_dir
DEFAULT_TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_TEST_TMP_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("TMP", str(DEFAULT_TEST_TMP_DIR))
os.environ.setdefault("TEMP", str(DEFAULT_TEST_TMP_DIR))
os.environ.setdefault("ROW_BOT_TEST_MODE", "1")
# Test folders live under the checkout's .tmp: git run there by the code under test
# must stop at .tmp instead of finding (and committing to) the checkout (B216).
os.environ["GIT_CEILING_DIRECTORIES"] = os.pathsep.join(
    filter(None, [str(PROJECT_ROOT / ".tmp"), os.environ.get("GIT_CEILING_DIRECTORIES", "")])
)


_ORIGINAL_BUILTINS_OPEN = builtins.open
_ORIGINAL_IO_OPEN = io.open
_ORIGINAL_PATH_OPEN = pathlib.Path.open
_ORIGINAL_PATH_WRITE_TEXT = pathlib.Path.write_text
_ORIGINAL_PATH_WRITE_BYTES = pathlib.Path.write_bytes
_ORIGINAL_PATH_MKDIR = pathlib.Path.mkdir
_ORIGINAL_PATH_REPLACE = pathlib.Path.replace
_ORIGINAL_OS_REPLACE = os.replace
_ORIGINAL_SQLITE_CONNECT = sqlite3.connect
_ORIGINAL_MONKEYPATCH_SETENV = pytest.MonkeyPatch.setenv


def _guarded_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any):
    if _is_write_mode(mode) and _is_live_user_state_path(file):
        _raise_live_write(file, f"open with mode {mode!r}")
    return _ORIGINAL_BUILTINS_OPEN(file, mode, *args, **kwargs)


def _guarded_io_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any):
    if _is_write_mode(mode) and _is_live_user_state_path(file):
        _raise_live_write(file, f"io.open with mode {mode!r}")
    return _ORIGINAL_IO_OPEN(file, mode, *args, **kwargs)


def _guarded_path_open(self: pathlib.Path, mode: str = "r", *args: Any, **kwargs: Any):
    if _is_write_mode(mode) and _is_live_user_state_path(self):
        _raise_live_write(self, f"Path.open with mode {mode!r}")
    return _ORIGINAL_PATH_OPEN(self, mode, *args, **kwargs)


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


def _guarded_sqlite_connect(database: Any, *args: Any, **kwargs: Any):
    if _is_live_user_state_path(database):
        _raise_live_write(database, "sqlite3.connect")
    return _ORIGINAL_SQLITE_CONNECT(database, *args, **kwargs)


def _synced_setenv(self: pytest.MonkeyPatch, name: str, value: str, *args: Any, **kwargs: Any) -> None:
    _ORIGINAL_MONKEYPATCH_SETENV(self, name, value, *args, **kwargs)


builtins.open = _guarded_open
io.open = _guarded_io_open
pathlib.Path.open = _guarded_path_open
pathlib.Path.write_text = _guarded_write_text
pathlib.Path.write_bytes = _guarded_write_bytes
pathlib.Path.mkdir = _guarded_mkdir
pathlib.Path.replace = _guarded_path_replace
os.replace = _guarded_os_replace
sqlite3.connect = _guarded_sqlite_connect
pytest.MonkeyPatch.setenv = _synced_setenv


# Deterministic tests never reach the network: a connection to anything but
# loopback, or a DNS lookup of a real name, is refused (as if offline) and fails
# the test that made it. Tests marked live_provider or e2e may use the network.
_ORIGINAL_SOCKET_CONNECT = socket.socket.connect
_ORIGINAL_SOCKET_CONNECT_EX = socket.socket.connect_ex
_ORIGINAL_GETADDRINFO = socket.getaddrinfo
_OUTBOUND_ATTEMPTS: list[str] = []
_NETWORK_ALLOWED = False
_LOCAL_NAMES = {"", "localhost", socket.gethostname().lower()}


def _is_local_host(host: Any) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    name = str(host or "").strip("[]").split("%", 1)[0].lower()
    if name in _LOCAL_NAMES or name.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def _outbound(self: socket.socket, address: Any) -> str | None:
    if _NETWORK_ALLOWED or self.family not in (socket.AF_INET, socket.AF_INET6) or _is_local_host(address[0]):
        return None
    attempt = f"connect to {address[0]}:{address[1]}"
    _OUTBOUND_ATTEMPTS.append(attempt)
    return attempt


def _guarded_socket_connect(self: socket.socket, address: Any) -> None:
    if attempt := _outbound(self, address):
        raise ConnectionRefusedError(errno.ECONNREFUSED, f"deterministic tests have no network ({attempt})")
    return _ORIGINAL_SOCKET_CONNECT(self, address)


def _guarded_socket_connect_ex(self: socket.socket, address: Any) -> int:
    if _outbound(self, address):
        return errno.ECONNREFUSED
    return _ORIGINAL_SOCKET_CONNECT_EX(self, address)


def _guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any):
    if not _NETWORK_ALLOWED and not _is_local_host(host):
        try:
            ipaddress.ip_address(str(host).strip("[]").split("%", 1)[0])
        except ValueError:
            _OUTBOUND_ATTEMPTS.append(f"DNS lookup of {host}")
            raise socket.gaierror(socket.EAI_NONAME, "deterministic tests have no network") from None
    return _ORIGINAL_GETADDRINFO(host, *args, **kwargs)


socket.socket.connect = _guarded_socket_connect
socket.socket.connect_ex = _guarded_socket_connect_ex
socket.getaddrinfo = _guarded_getaddrinfo


@pytest.fixture(autouse=True)
def _no_outbound_network(request: pytest.FixtureRequest):
    global _NETWORK_ALLOWED
    live = any(request.node.get_closest_marker(name) for name in ("live_provider", "e2e"))
    _NETWORK_ALLOWED = live
    before = len(_OUTBOUND_ATTEMPTS)
    yield
    _NETWORK_ALLOWED = False
    attempts = sorted(set(_OUTBOUND_ATTEMPTS[before:]))
    if attempts and not live:
        pytest.fail("A deterministic test tried to reach the network: " + "; ".join(attempts), pytrace=False)


def shard_files(files: set[str], shard: str) -> set[str]:
    """The files one shard runs for ROW_BOT_TEST_SHARD=k/N.

    Files are dealt round-robin in path order, so neighbouring files of similar
    cost land on different shards and a file's tests (and module fixtures) stay
    together. Every file lands in exactly one shard.
    """
    index, _, total = shard.partition("/")
    if not (index.isdigit() and total.isdigit() and 1 <= int(index) <= int(total)):
        raise ValueError(f"ROW_BOT_TEST_SHARD must look like 1/3, not {shard!r}")
    return set(sorted(files)[int(index) - 1::int(total)])


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """CI splits the PR pass across jobs with ROW_BOT_TEST_SHARD=k/N."""
    shard = os.environ.get("ROW_BOT_TEST_SHARD", "").strip()
    if not shard:
        return
    paths = {item.path: item.path.resolve().relative_to(PROJECT_ROOT).as_posix() for item in items}
    try:
        kept_files = shard_files(set(paths.values()), shard)
    except ValueError as error:
        raise pytest.UsageError(str(error)) from None
    config.hook.pytest_deselected(items=[item for item in items if paths[item.path] not in kept_files])
    items[:] = [item for item in items if paths[item.path] in kept_files]


@pytest.fixture(autouse=True)
def _keep_module_identity():
    """Put back the row_bot module objects a test popped and imported again.

    Some tests re-import modules for a fresh data folder. Product modules keep
    references to the original objects, so a later test that patched the new copy
    would patch nothing (the stale developer.storage of B215).
    """
    before = {name: module for name, module in sys.modules.items()
              if name == "row_bot" or name.startswith("row_bot.")}
    yield
    for name, module in before.items():
        if sys.modules.get(name) is not module:
            sys.modules[name] = module
            parent, _, child = name.rpartition(".")
            if parent in sys.modules:
                setattr(sys.modules[parent], child, module)


@pytest.fixture(autouse=True)
def _reset_test_data_env_between_tests():
    _set_default_test_data_env()
    yield
    _set_default_test_data_env()


@pytest.fixture(autouse=True)
def _reset_agent_runtime_context():
    # stream_agent sets the run's context (runtime surface, thread, tools) on
    # the calling thread: a worker thread per run in the app, the one shared
    # thread in tests. A test that streamed as a workflow left Computer Use
    # refusing every later test on its worker.
    yield
    reset = getattr(sys.modules.get("row_bot.agent"), "_set_active_runtime_context", None)
    if callable(reset):  # some tests stand a stub in for the module
        reset()


@pytest.fixture(autouse=True)
def _isolate_desktop_notification_outputs(monkeypatch):
    # Exercise notification state/toasts normally while keeping deterministic
    # tests from showing OS alerts or playing sounds on the developer's desktop.
    from row_bot import notifications

    monkeypatch.setattr(notifications, "_desktop_notify", lambda *args, **kwargs: None)
    monkeypatch.setattr(notifications, "_play_sound", lambda *args, **kwargs: None)


@pytest.fixture
def reload_for_data_dir(monkeypatch: pytest.MonkeyPatch):
    """Reload modules that bind ROW_BOT_DATA_DIR paths at import, for a test's folder.

    Afterwards each module gets back exactly the namespace it had (its classes,
    singletons and paths), because other modules keep references to those:
    reloading them back is not enough (a reloaded dataclass no longer compares
    equal to the one other modules imported; B215). A module first imported by
    the test is reloaded under the session's data folder instead.
    """
    saved: dict[str, dict[str, Any]] = {}
    first_imported: list[str] = []

    def reload(data_dir: Path, *names: str) -> list[Any]:
        monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data_dir))
        for name in names:
            module = sys.modules.get(name)
            if module is None:
                if name not in first_imported:
                    first_imported.append(name)
            elif name not in saved and name not in first_imported:
                saved[name] = dict(module.__dict__)
            importlib.reload(importlib.import_module(name))
        return [sys.modules[name] for name in names]

    yield reload
    threads = sys.modules.get("row_bot.threads")
    if threads is not None and ("row_bot.threads" in saved or "row_bot.threads" in first_imported):
        try:
            threads.conn.close()  # the test's connection; the restored one stays open
        except Exception:
            pass
    for name, namespace in saved.items():
        module = sys.modules[name]
        module.__dict__.clear()
        module.__dict__.update(namespace)
    monkeypatch.undo()
    for name in first_imported:
        importlib.reload(sys.modules[name])
