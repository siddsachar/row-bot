"""Start-up's first imports: the shared ones once and first, channel adapters after them (RM4)."""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from row_bot import startup_imports


@pytest.fixture
def package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Write a throwaway package on sys.path; its modules leave sys.modules afterwards."""
    name = f"rm4_{uuid.uuid4().hex[:8]}"
    root = tmp_path / name
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(startup_imports, "SHARED_MODULES", ())
    monkeypatch.setattr(startup_imports, "_warmed", False)

    def write(module: str, source: str) -> str:
        (root / f"{module}.py").write_text(source.replace("PKG", name), encoding="utf-8")
        importlib.invalidate_caches()
        return f"{name}.{module}"

    write.name = name
    yield write
    for module in [key for key in sys.modules if key == name or key.startswith(name + ".")]:
        sys.modules.pop(module, None)


class _SlowMount(importlib.abc.MetaPathFinder):
    """The package as on the Mac's slow, translocated copy (RM4): reading a module takes a while, and a
    lookup made while another thread reads reports a module that is there as missing."""

    def __init__(self, prefix: str):
        self.prefix = prefix
        self._lock = threading.Lock()
        self._reading: set[int] = set()

    def find_spec(self, fullname, path, target=None):
        if not fullname.startswith(self.prefix + "."):
            return None
        with self._lock:
            if self._reading - {threading.get_ident()}:
                raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        spec.loader = _SlowRead(self, spec.loader)
        return spec

    def read(self) -> None:
        me = threading.get_ident()
        with self._lock:
            self._reading.add(me)
        time.sleep(0.03)
        with self._lock:
            self._reading.discard(me)


class _SlowRead(importlib.abc.Loader):
    def __init__(self, mount: _SlowMount, loader):
        self.mount, self.loader = mount, loader

    def create_module(self, spec):
        return None

    def exec_module(self, module) -> None:
        self.mount.read()
        self.loader.exec_module(module)


def _channels(monkeypatch: pytest.MonkeyPatch, names: dict[str, str]):
    import row_bot.app as app_mod

    monkeypatch.setattr(app_mod, "_CHANNEL_MODULES", tuple(names))
    monkeypatch.setattr(app_mod, "_CHANNEL_DISPLAY_NAMES", names)
    return app_mod


def test_startup_workers_importing_at_once_all_load(package, monkeypatch) -> None:
    package("graph", "TOURNAMENT = 'tournament'\n")
    tools = package("tools", "from PKG.graph import TOURNAMENT\n")
    package("store", "SECRET = 'secret'\n")
    telegram = package("telegram", "from PKG.store import SECRET\nREADY = SECRET\n")
    monkeypatch.setattr(startup_imports, "SHARED_MODULES", (tools,))
    app_mod = _channels(monkeypatch, {telegram: "Telegram"})
    monkeypatch.setattr(sys, "meta_path", [_SlowMount(package.name), *sys.meta_path])

    results: dict[str, object] = {}
    start = threading.Barrier(2)

    def channel_import() -> None:  # The channel step, which in a real start may run before the monitor's.
        start.wait()
        results["channels"] = app_mod._load_channel_modules()

    def approval_monitor() -> None:  # Its first tick: the shared imports, then the X posts' tools.
        start.wait()
        startup_imports.warm()
        results["tools"] = importlib.import_module(tools).TOURNAMENT

    workers = [threading.Thread(target=channel_import), threading.Thread(target=approval_monitor)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert not any(worker.is_alive() for worker in workers)
    assert results == {"channels": [], "tools": "tournament"}
    assert sys.modules[telegram].READY == "secret"


def test_install_the_extra_is_said_only_when_the_package_is_missing(package, monkeypatch) -> None:
    absent = package("absent", f"import rm4_absent_{uuid.uuid4().hex[:8]}_sdk\n")
    own_module = package(
        "own_module",
        'raise ModuleNotFoundError("No module named \'row_bot.channels.auth_store\'", '
        'name="row_bot.channels.auth_store")\n',
    )
    installed = package("installed", "from json import not_a_name\n")
    app_mod = _channels(monkeypatch, {absent: "Absent", own_module: "Own", installed: "Installed"})

    warnings = app_mod._load_channel_modules()

    assert len(warnings) == 3
    absent_warning, own_warning, installed_warning = warnings
    assert absent_warning.startswith("Absent ") and "Install the channels extra" in absent_warning
    for warning, display in ((own_warning, "Own"), (installed_warning, "Installed")):
        assert warning.startswith(f"{display} didn't load")
        assert "Restart Row-Bot" in warning
        assert "Install the channels extra" not in warning


def test_a_channel_that_fails_to_import_is_tried_again(package, monkeypatch) -> None:
    attempts = package("attempts", "count = 0\n")
    flaky = package(
        "flaky",
        "import PKG.attempts as attempts\n"
        "attempts.count += 1\n"
        "if attempts.count == 1:\n"
        "    raise ImportError(\"cannot import name 'get_channel_secret' from partially initialized module\", "
        "name='row_bot.channels.auth_store')\n"
        "LOADED = True\n",
    )
    app_mod = _channels(monkeypatch, {flaky: "Flaky"})

    assert app_mod._load_channel_modules() == []
    assert sys.modules[flaky].LOADED is True
    assert sys.modules[attempts].count == 2
