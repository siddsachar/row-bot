from __future__ import annotations

import builtins
import errno
import importlib
import io
import ipaddress
import json
import os
import pathlib
import re
import socket
import sqlite3
import sys
import types
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
# pytest numbers its temporary folders per run under this root, so runs side by side
# never delete each other's (a fixed --basetemp is wiped by every run that starts).
os.environ.setdefault("PYTEST_DEBUG_TEMPROOT", str(DEFAULT_TEST_TMP_DIR))
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


# ── Each test leaves the row_bot modules as it found them ────────────────────────────────────────
# Test files bind modules when they are collected, and product code keeps references to the module
# objects (and finds some, like row_bot.agent, through sys.modules). A test that popped a module and
# imported it again, or reloaded it in place, left later tests on the same worker patching one copy
# while the code ran another (the stale developer.storage of B215; the live-eval runner of #440; the
# untrusted-results read-back) or reading the previous test's data folder. These are hooks, not a
# fixture: the check runs after every fixture of the test has torn down, monkeypatch's undo included.
_ROW_BOT_MODULES = pytest.StashKey[dict[str, tuple[types.ModuleType, Any]]]()


def _row_bot_modules() -> dict[str, tuple[types.ModuleType, Any]]:
    return {name: (module, getattr(module, "__spec__", None)) for name, module in list(sys.modules.items())
            if (name == "row_bot" or name.startswith("row_bot.")) and module is not None}


def _put_back_row_bot_modules(before: dict[str, tuple[types.ModuleType, Any]]) -> str:
    """Put back the module objects a test replaced; describe what it left behind ("" when nothing)."""
    replaced: list[str] = []
    reloaded: list[str] = []
    for name, (module, spec) in before.items():
        if sys.modules.get(name) is not module:
            replaced.append(name)
            sys.modules[name] = module
        elif getattr(module, "__spec__", None) is not spec:
            reloaded.append(name)  # importlib.reload: same object, the test's namespace
        parent, _, child = name.rpartition(".")
        bound = getattr(sys.modules.get(parent), child, None)
        if type(bound) is types.ModuleType and bound is not module and bound.__name__ == name:
            setattr(sys.modules[parent], child, module)  # a copy imported under monkeypatch.delitem
    if not (replaced or reloaded):
        return ""
    found = [f"{name} (a different module object; the original was put back)" for name in sorted(replaced)]
    found += [f"{name} (reloaded in place; its namespace is still the test's)" for name in sorted(reloaded)]
    return (
        "The test left re-imported row_bot modules behind: " + "; ".join(found) + ". Use the "
        "reload_for_data_dir fixture, which puts each module's namespace back, or monkeypatch.setitem/"
        "delitem on sys.modules; never sys.modules.pop or importlib.reload by hand."
    )


# Credentials in the environment are put back too. Saving a key writes os.environ outside
# monkeypatch, so a test that saved one left it to every later test on the worker: a later
# compaction built a real OpenAI client with it and called the provider (PR #438).
_CREDENTIAL_ENV = pytest.StashKey[dict[str, str]]()
_CREDENTIAL_NAME = re.compile(r"(API_KEY|_TOKEN|_SECRET|_PASSWORD)$")


def _credential_env() -> dict[str, str]:
    return {name: value for name, value in os.environ.items() if _CREDENTIAL_NAME.search(name)}


def _put_back_credential_env(before: dict[str, str]) -> None:
    for name in set(_credential_env()) - set(before):
        del os.environ[name]
    for name, value in before.items():
        if os.environ.get(name) != value:
            os.environ[name] = value


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    item.stash[_ROW_BOT_MODULES] = _row_bot_modules()
    item.stash[_CREDENTIAL_ENV] = _credential_env()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None):
    before = item.stash.get(_ROW_BOT_MODULES, {})
    try:
        result = yield
    except BaseException:
        _put_back_row_bot_modules(before)
        raise
    finally:
        _put_back_credential_env(item.stash.get(_CREDENTIAL_ENV, _credential_env()))
    if message := _put_back_row_bot_modules(before):
        pytest.fail(message, pytrace=False)
    return result


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
def _fresh_builtin_ways():
    # Apps keeps one snapshot of Row-Bot's own ways to connect per data folder; tests share a
    # folder and fake its owners differently, so each one starts from its owners again.
    changed = getattr(sys.modules.get("row_bot.integrations.builtin"), "changed", None)
    if callable(changed):
        changed()
    yield


@pytest.fixture(autouse=True)
def _package_attributes_follow_sys_modules():
    # Older tests evict row_bot modules from sys.modules by hand and never
    # re-import them, so the package keeps the evicted module as an attribute.
    # `import row_bot.x as x` and string-path monkeypatches read that attribute
    # while the code under test imports a fresh module: the next test on the
    # worker patched one module and ran another.
    for name, package in list(sys.modules.items()):
        if not (name == "row_bot" or name.startswith("row_bot.")) or not hasattr(package, "__path__"):
            continue
        for attribute, value in list(vars(package).items()):
            if type(value) is not types.ModuleType or value.__name__ != f"{name}.{attribute}":
                continue
            current = sys.modules.get(value.__name__)
            if current is None:
                delattr(package, attribute)
            elif current is not value:
                setattr(package, attribute, current)
    yield


@pytest.fixture(autouse=True)
def _channel_registry_restored():
    # Row-Bot's own channels register themselves when imported. Tests that register fakes left them behind,
    # so a later test on the worker listed the fakes and no Telegram channel at all.
    registry = importlib.import_module("row_bot.channels.registry")
    saved = dict(registry._channels), dict(registry._channel_sources)
    yield
    registry = sys.modules.get("row_bot.channels.registry", registry)
    registry._channels.clear()
    registry._channels.update(saved[0])
    registry._channel_sources.clear()
    registry._channel_sources.update(saved[1])


@pytest.fixture(autouse=True)
def _saved_keys_restored():
    # Saved keys are read into api_keys, not the environment, so a key one test saved stayed loaded for the
    # next test on the worker, which cleared only the environment.
    keys = getattr(sys.modules.get("row_bot.api_keys"), "_loaded_keys", None)
    saved = dict(keys) if keys is not None else {}
    yield
    keys = getattr(sys.modules.get("row_bot.api_keys"), "_loaded_keys", None)
    if keys is not None:
        keys.clear()
        keys.update(saved)


@pytest.fixture(autouse=True)
def _fresh_context_calibration():
    # Request-size calibration is learned from reported usage per model and kept in usage.db.
    # One test's fake usage (or a previous run's, in the reused worker folder) must not move
    # another test's estimates and compaction points.
    importlib.import_module("row_bot.context_calibration").reset_for_tests()
    yield


@pytest.fixture(autouse=True)
def _no_title_model(monkeypatch):
    # A conversation's first finished turn asks its model for a title on a background
    # thread. Tests that run turns script the agent, not that model: unscripted, the thread
    # built a real provider client after the test had ended. The naming tests script it.
    monkeypatch.setattr("row_bot.application.conversation_naming._ask", lambda *_args: "")


@pytest.fixture(autouse=True)
def _no_connection_catalog_refresh(monkeypatch):
    # Connecting or disconnecting a provider refreshes its model catalog on a background thread
    # (B411). Tests that connect one script the account, not its catalog: unscripted, the thread
    # asked the provider for its models after the test. The catalog refresh tests script it.
    monkeypatch.setattr("row_bot.providers.model_catalog_cache.provider_connection_changed",
                        lambda _provider_id, **_how: None)


@pytest.fixture(autouse=True)
def _isolate_desktop_notification_outputs(monkeypatch):
    # Exercise notification state/toasts normally while keeping deterministic
    # tests from showing OS alerts or playing sounds on the developer's desktop.
    from row_bot import notifications

    monkeypatch.setattr(notifications, "_desktop_notify", lambda *args, **kwargs: None)
    monkeypatch.setattr(notifications, "_play_sound", lambda *args, **kwargs: None)


@pytest.fixture(scope="session")
def _registry_fixture_snapshot(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from row_bot.mcp_client import marketplace, registry_snapshot

    envelope = json.loads((Path(__file__).parent / "fixtures/integrations/registry-v01.json").read_text(encoding="utf-8"))
    path = tmp_path_factory.mktemp("registry") / "registry_snapshot.jsonl.xz"
    path.write_bytes(registry_snapshot.build_snapshot(marketplace.registry_entries(envelope), captured_at=1790000000.0,
                                                      watermark="2026-09-21T00:00:00Z"))
    return path


@pytest.fixture(autouse=True)
def _small_registry_snapshot(_registry_fixture_snapshot: Path, monkeypatch: pytest.MonkeyPatch):
    # The shipped Registry mirror holds tens of thousands of records. Deterministic tests
    # index a one-record fixture instead; a test of the shipped snapshot sets SHIPPED itself.
    monkeypatch.setattr("row_bot.mcp_client.registry_snapshot.SHIPPED", _registry_fixture_snapshot)
    # And no shipped quality signals: a test that ranks with signals ships its own (tests/helpers/registry.py).
    monkeypatch.setattr("row_bot.mcp_client.registry_signals.SHIPPED", _registry_fixture_snapshot.with_name("no-signals.json.xz"))


@pytest.fixture
def reload_for_data_dir(monkeypatch: pytest.MonkeyPatch):
    """Reload modules that bind ROW_BOT_DATA_DIR paths at import, for a test's folder.

    ``reload(data_dir, *names)`` returns the reloaded modules; a data_dir of None
    reloads them under the folder the test already uses (a fresh module state only).
    Afterwards each module gets back exactly the namespace it had (its classes,
    singletons and paths), because other modules keep references to those:
    reloading them back is not enough (a reloaded dataclass no longer compares
    equal to the one other modules imported; B215). A module first imported by
    the test is reloaded under the session's data folder instead.

    The namespace is recorded through monkeypatch just before the first reload, so
    the undo puts it back in order: after undoing the patches the test made to the
    reloaded module (which would otherwise write the test's values back over it) and
    before undoing any it made earlier. It is recorded with ``setattr``, the stack
    the test's own attribute patches are on: pytest undoes every ``setattr`` before
    any ``setitem``, so a namespace kept with ``setitem`` put back a value the test
    had patched before the reload (``threads.DB_PATH`` stayed on the test's folder
    while ``threads.checkpointer`` was back on the session's).
    """
    kept: dict[str, tuple[Any, set[str]]] = {}
    first_imported: list[str] = []

    def reload(data_dir: Path | None, *names: str) -> list[Any]:
        if data_dir is not None:
            monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data_dir))
        for name in names:
            module = sys.modules.get(name)
            if module is None:
                if name not in first_imported:
                    first_imported.append(name)
            elif name not in kept and name not in first_imported:
                namespace = vars(module)
                kept[name] = (module, set(namespace))
                for key, value in list(namespace.items()):
                    monkeypatch.setattr(module, key, value)
            importlib.reload(importlib.import_module(name))
        return [sys.modules[name] for name in names]

    yield reload
    threads = sys.modules.get("row_bot.threads")
    if threads is not None and ("row_bot.threads" in kept or "row_bot.threads" in first_imported):
        # A background page refresh may still be reading the connection closed below.
        settle = getattr(sys.modules.get("row_bot.application.client_platform"), "settle_background", None)
        if callable(settle):
            assert settle(10), "Test left background work running"
        try:
            threads.conn.close()  # the test's connection; the restored one stays open
        except Exception:
            pass
    monkeypatch.undo()
    for module, keys in kept.values():
        namespace = vars(module)
        for key in set(namespace) - keys:  # names only the reloaded copy defined
            del namespace[key]
    for name in first_imported:
        importlib.reload(sys.modules[name])
