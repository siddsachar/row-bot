"""The 5.2 install layout: where an install lives, state.json and the loop guard.

The native launchers (Row-Bot.exe, the macOS tray host, the Linux wrapper)
implement the same rule as ``install_layout.begin_launch`` and
``relaunch_after_exit``; the installers' smokes run them against a broken
version (scripts/smoke_install_layout.py).
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from row_bot import environment_secrets
from row_bot import install_layout as layout
from row_bot.install_layout import InstallKind, InstallState


pytestmark = [pytest.mark.subsystem, pytest.mark.installer, pytest.mark.platform]

MODULE = Path(layout.__file__)
CASES = json.loads(Path("contracts/install-layout/launch-cases.json").read_text(encoding="utf-8"))["cases"]
# The `current` link and its atomic swap are Linux behaviour; Windows cannot rename over a directory link.
posix_only = pytest.mark.skipif(os.name == "nt", reason="the current link belongs to the Linux layout")


def _version(root: Path, parent: str, version: str) -> Path:
    app = root / parent / version / "app"
    app.mkdir(parents=True)
    (root / parent / version / "python").mkdir()
    return app


# ── Recognising an install ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("system", "parent", "kind"),
    [("Windows", "versions", InstallKind.WINDOWS), ("Darwin", "versions", InstallKind.MACOS),
     ("Linux", "releases", InstallKind.LINUX)],
)
def test_an_installed_version_is_recognised_by_its_place_in_the_root(tmp_path, system, parent, kind):
    root = tmp_path / "Row-Bot"
    app = _version(root, parent, "5.2.1")

    found = layout.detect(app_root=app, environ={}, system=system, home=tmp_path)

    assert found.kind is kind
    assert found.root == root.resolve()
    assert found.version == "5.2.1"
    assert found.version_root == (root / parent / "5.2.1").resolve()
    assert found.self_updating is True
    assert found.state_path == root.resolve() / "state.json"


def test_the_macos_bundle_is_the_seed_version(tmp_path):
    contents = tmp_path / "Applications" / "Row-Bot.app" / "Contents"
    (contents / "Resources" / "app").mkdir(parents=True)
    (contents / "Resources" / "python").mkdir()
    (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "5.2.0"}))
    home = tmp_path / "home"

    found = layout.detect(app_root=contents / "Resources" / "app", environ={}, system="Darwin", home=home)

    assert found.kind is InstallKind.MACOS
    assert found.seed is True
    assert found.version == "5.2.0"
    assert found.root == home / "Library" / "Application Support" / "Row-Bot"
    assert found.self_updating is True


@pytest.mark.parametrize(
    ("make", "kind"),
    [
        (lambda tmp: (tmp / ".git").mkdir() or tmp, InstallKind.DEV),
        (lambda tmp: tmp / "anywhere", InstallKind.UNKNOWN),
        # The 5.1 Windows layout (R\app beside R\python) is not a 5.2 install.
        (lambda tmp: _old_windows_app(tmp), InstallKind.UNKNOWN),
    ],
    ids=["checkout", "unrecognised", "old-program-files-layout"],
)
def test_checkouts_and_unknown_folders_never_update_themselves(tmp_path, make, kind):
    found = layout.detect(app_root=make(tmp_path), environ={}, system="Windows", home=tmp_path)

    assert found.kind is kind
    assert found.self_updating is False


def _old_windows_app(tmp_path: Path) -> Path:
    (tmp_path / "Row-Bot" / "app").mkdir(parents=True)
    (tmp_path / "Row-Bot" / "python").mkdir()
    (tmp_path / "Row-Bot" / "unins000.exe").write_bytes(b"")
    return tmp_path / "Row-Bot" / "app"


def test_a_container_is_never_self_updating_even_in_an_install_layout(tmp_path):
    app = _version(tmp_path / "R", "releases", "5.2.0")

    found = layout.detect(app_root=app, environ={"ROW_BOT_CONTAINERIZED": "1"}, system="Linux")

    assert found.kind is InstallKind.CONTAINER
    assert found.self_updating is False


def test_updater_counts_only_self_updating_installs_as_installed(monkeypatch):
    import row_bot.updater as updater

    monkeypatch.setattr(layout, "detect", lambda: layout.InstallLayout(InstallKind.CONTAINER))
    assert updater.is_dev_install() is True
    monkeypatch.setattr(
        layout, "detect", lambda: layout.InstallLayout(InstallKind.WINDOWS, Path("R"), "5.2.0", Path("R/versions/5.2.0"))
    )
    assert updater.is_dev_install() is False


@pytest.mark.parametrize(
    ("system", "environ", "expected"),
    [
        ("Windows", {"LOCALAPPDATA": "L"}, Path("L") / "Programs" / "Row-Bot"),
        ("Darwin", {}, Path("H") / "Library" / "Application Support" / "Row-Bot"),
        ("Linux", {}, Path("H") / ".local" / "share" / "row-bot"),
        ("Linux", {"XDG_DATA_HOME": "X"}, Path("X") / "row-bot"),
    ],
)
def test_default_install_roots_are_per_user(system, environ, expected):
    assert layout.default_install_root(system, environ, Path("H")) == expected


# ── state.json ──────────────────────────────────────────────────────────────


def test_state_round_trips_and_keeps_fields_it_does_not_know(tmp_path):
    state = InstallState(
        active="5.2.1",
        previous="5.2.0",
        ready={"version": "5.2.2", "staged_at": "2026-10-11T10:00:00Z"},
        pending_verification=True,
        unverified_launches=1,
        extra={"written_by": "phase-3"},
    )

    layout.write_state(tmp_path, state)

    assert layout.read_state(tmp_path) == state
    on_disk = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert on_disk["schema"] == 1
    assert on_disk["written_by"] == "phase-3"
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", json.dumps({"schema": 2, "active": "5.2.0"}),
     json.dumps({"schema": 1, "active": "../evil"}), json.dumps({"schema": 1})],
    ids=["corrupt", "not-an-object", "newer-schema", "traversal", "no-active"],
)
def test_an_invalid_state_reads_as_missing(tmp_path, content):
    (tmp_path / "state.json").write_text(content, encoding="utf-8")

    assert layout.read_state(tmp_path) is None


def test_a_failed_write_leaves_the_old_state_whole(tmp_path, monkeypatch):
    layout.write_state(tmp_path, InstallState(active="5.2.0"))

    def refuse(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(layout.os, "replace", refuse)
    with pytest.raises(OSError):
        layout.write_state(tmp_path, InstallState(active="5.2.1"))

    assert layout.read_state(tmp_path) == InstallState(active="5.2.0")
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_a_write_waits_out_a_reader_holding_the_file(tmp_path, monkeypatch):
    real_replace = os.replace
    calls = []

    def busy_then_free(source, target):
        calls.append(source)
        if len(calls) < 3:
            raise PermissionError("sharing violation")
        real_replace(source, target)

    monkeypatch.setattr(layout.os, "replace", busy_then_free)
    monkeypatch.setattr(layout.time, "sleep", lambda _seconds: None)

    layout.write_state(tmp_path, InstallState(active="5.2.1"))

    assert len(calls) == 3
    assert layout.read_state(tmp_path).active == "5.2.1"


@posix_only
def test_linux_current_link_is_the_active_version(tmp_path):
    (tmp_path / "releases" / "5.2.0").mkdir(parents=True)
    (tmp_path / "releases" / "5.2.1").mkdir()
    layout.set_linux_current(tmp_path, "5.2.0")
    assert layout.load_state(tmp_path, InstallKind.LINUX) == InstallState(active="5.2.0")

    layout.write_state(tmp_path, InstallState(active="5.2.1", previous="5.2.0"))
    # A 5.1 updater moved `current` without writing state.json.
    assert layout.load_state(tmp_path, InstallKind.LINUX).active == "5.2.0"
    layout.set_linux_current(tmp_path, "5.2.1")
    assert layout.load_state(tmp_path, InstallKind.LINUX).active == "5.2.1"
    assert os.readlink(tmp_path / "current") == "releases/5.2.1"


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [("5.2.10", "5.2.9", 1), ("5.2.0", "5.2.0", 0), ("5.2", "5.2.0", 0), ("5.2.0", "5.2.0-rc.1", 1),
     ("5.2.0-rc.1", "5.2.0-beta.2", 1), ("5.2.0-beta.10", "5.2.0-beta.9", 1), ("5.1.9", "5.2.0-beta.1", -1),
     ("v5.2.0", "5.2.0+local", 0)],
)
def test_versions_compare_as_releases(left, right, expected):
    assert layout.compare_versions(left, right) == expected
    assert layout.compare_versions(right, left) == -expected


# ── The loop guard ──────────────────────────────────────────────────────────


def _pending(active="5.2.1", previous="5.2.0", launches=0) -> InstallState:
    return InstallState(active=active, previous=previous, pending_verification=True,
                        unverified_launches=launches, ready={"version": active, "staged_at": "t"})


def _run_case(case: dict) -> tuple[list[str], InstallState]:
    """What a native launcher does for one case, with the reference rules."""
    raw = case["state"]
    state = InstallState(
        active=raw["active"], previous=raw.get("previous"), ready=raw.get("ready"),
        pending_verification=raw.get("pending_verification", False),
        unverified_launches=raw.get("unverified_launches", 0),
    )
    runnable = set(case["runnable"]).__contains__
    exits = {version: list(codes) for version, codes in case["exits"].items()}
    launched: list[str] = []
    for _launch in range(10):
        state, _failed = layout.begin_launch(state, runnable=runnable)
        launched.append(state.active)
        queue = exits.get(state.active, [])
        code = queue.pop(0) if queue else 0
        if not layout.relaunch_after_exit(state, state.active, code, runnable=runnable):
            break
    return launched, state


def _final_matches(expected: dict, actual: dict) -> bool:
    rollback = expected.get("last_rollback")
    plain = {key: value for key, value in expected.items() if key != "last_rollback"}
    if any(actual.get(key) != value for key, value in plain.items()):
        return False
    if rollback is None:
        return "last_rollback" not in expected or actual.get("last_rollback") is None
    return {key: (actual.get("last_rollback") or {}).get(key) for key in rollback} == rollback


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_the_shared_launch_cases_hold_for_the_reference_rules(case):
    launched, state = _run_case(case)

    assert launched == case["launched"]
    assert _final_matches(case["final"], state.to_dict()), state.to_dict()


def test_an_unverified_version_is_rolled_back_after_two_launches_that_never_got_ready():
    state = _pending()
    launched = []
    for _attempt in range(5):
        state, failed = layout.begin_launch(state, now=lambda: "2026-10-11T12:00:00Z")
        launched.append(state.active)
        if failed:
            break
        # The app crashed before /readyz answered.
        assert layout.relaunch_after_exit(state, state.active, 1) is True

    assert launched == ["5.2.1", "5.2.1", "5.2.0"]
    assert state.active == "5.2.0"
    assert state.previous is None
    assert state.pending_verification is False
    assert state.unverified_launches == 0
    assert state.ready is None
    assert state.last_rollback == {
        "from": "5.2.1", "to": "5.2.0", "at": "2026-10-11T12:00:00Z",
        "reason": "2 launches in a row did not become ready",
    }


def test_a_verified_version_is_launched_as_is():
    state = InstallState(active="5.2.1", previous="5.2.0", unverified_launches=3)

    updated, failed = layout.begin_launch(state)

    assert failed is None
    assert updated == InstallState(active="5.2.1", previous="5.2.0")


def test_without_a_previous_version_the_guard_stops_retrying_instead_of_looping():
    state = _pending(previous=None)
    for _attempt in range(2):
        state, failed = layout.begin_launch(state)
        assert failed is None
    assert state.active == "5.2.1" and state.unverified_launches == 2

    assert layout.relaunch_after_exit(state, "5.2.1", 1) is False


@pytest.mark.parametrize(
    ("state", "launched", "code", "runnable", "expected"),
    [
        (InstallState(active="5.2.0"), "5.2.0", 75, (), True),
        (None, "5.2.0", 75, (), True),
        (InstallState(active="5.2.0"), "5.2.0", 0, (), False),
        (InstallState(active="5.2.0"), "5.2.0", 1, (), False),
        (_pending(launches=1), "5.2.1", 0, ("5.2.0",), False),
        (_pending(launches=1), "5.2.1", 3, (), True),
        (_pending(launches=2), "5.2.1", 3, ("5.2.0",), True),
        (_pending(launches=2), "5.2.1", 3, (), False),
        (_pending(launches=2), "5.2.0", 3, ("5.2.0",), False),
    ],
    ids=["relaunch", "relaunch-no-state", "quit", "crash-verified", "quit-while-starting", "crash-unverified",
         "crash-then-rollback", "crash-previous-cannot-start", "another-version-active"],
)
def test_the_app_starts_again_only_on_the_relaunch_code_or_when_a_failed_start_can_progress(
    state, launched, code, runnable, expected
):
    assert layout.relaunch_after_exit(state, launched, code, runnable=set(runnable).__contains__) is expected


def test_a_previous_version_that_cannot_start_is_never_rolled_back_to():
    state, failed = layout.begin_launch(_pending(launches=2), runnable=lambda _version: False)

    assert failed is None
    assert (state.active, state.unverified_launches) == ("5.2.1", 3)


@pytest.mark.parametrize("kind", [InstallKind.WINDOWS, InstallKind.LINUX], ids=["windows", "linux"])
def test_a_version_folder_can_start_only_with_its_python_and_launcher(tmp_path, kind):
    folder = tmp_path / ("releases" if kind is InstallKind.LINUX else "versions") / "5.2.0"
    python = folder / "python" / ("python.exe" if kind is InstallKind.WINDOWS else "bin/python3")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    assert layout.version_runnable(tmp_path, kind, "5.2.0") is False

    (folder / "app").mkdir()
    (folder / "app" / "launcher.py").write_text("", encoding="utf-8")
    assert layout.version_runnable(tmp_path, kind, "5.2.0") is True
    assert layout.version_runnable(tmp_path, kind, "../5.2.0") is False


def test_the_relaunch_exit_code_is_75():
    # The native launchers hard-code the same value.
    assert layout.RELAUNCH_EXIT_CODE == 75


# ── Clearing on ready ───────────────────────────────────────────────────────


def _installed(tmp_path: Path, version="5.2.1") -> layout.InstallLayout:
    app = _version(tmp_path, "versions", version)
    return layout.detect(app_root=app, environ={}, system="Windows", home=tmp_path)


def test_the_running_version_is_verified_once_readyz_answers(tmp_path):
    installed = _installed(tmp_path)
    layout.write_state(tmp_path, _pending(launches=1))
    probes = []

    verified = layout.verify_when_ready(installed, 8090, probe=lambda port: probes.append(port) or len(probes) >= 3,
                                        sleep=lambda _seconds: None)

    assert verified is True
    assert probes == [8090, 8090, 8090]
    state = layout.read_state(tmp_path)
    assert state.pending_verification is False
    assert state.unverified_launches == 0
    assert state.active == "5.2.1" and state.previous == "5.2.0"
    assert "verified_at" in state.extra


def test_nothing_is_probed_when_nothing_waits_for_verification(tmp_path):
    installed = _installed(tmp_path)
    layout.write_state(tmp_path, InstallState(active="5.2.1"))

    assert layout.verify_when_ready(installed, 8090, probe=lambda _port: pytest.fail("probed")) is False


def test_a_version_that_was_rolled_back_meanwhile_is_not_verified(tmp_path):
    installed = _installed(tmp_path)
    layout.write_state(tmp_path, _pending(launches=1))

    def roll_back_then_answer(_port):
        layout.write_state(tmp_path, InstallState(active="5.2.0"))
        return True

    assert layout.verify_when_ready(installed, 8090, probe=roll_back_then_answer) is False
    assert layout.read_state(tmp_path) == InstallState(active="5.2.0")


def test_a_slow_start_is_still_verified_once_it_is_ready(tmp_path, caplog):
    installed = _installed(tmp_path)
    layout.write_state(tmp_path, _pending(launches=1))
    now = [0.0]

    def probe(_port):
        return now[0] >= 600  # ready ten minutes after the start

    def sleep(seconds):
        now[0] += seconds

    with caplog.at_level("WARNING", logger="row_bot.install_layout"):
        verified = layout.verify_when_ready(installed, 8090, probe=probe, clock=lambda: now[0], sleep=sleep)

    assert verified is True
    assert layout.read_state(tmp_path).pending_verification is False
    assert "not ready 240 s after it started" in caplog.text


def test_a_server_that_stops_before_it_is_ready_stays_unverified(tmp_path):
    installed = _installed(tmp_path)
    layout.write_state(tmp_path, _pending(launches=1))
    alive = iter([True, True, False])

    verified = layout.verify_when_ready(installed, 8090, alive=lambda: next(alive), probe=lambda _port: False,
                                        sleep=lambda _seconds: None)

    assert verified is False
    assert layout.read_state(tmp_path) == _pending(launches=1)


def test_the_launcher_watches_readiness_only_for_an_installed_version(monkeypatch):
    import row_bot.launcher as launcher

    started = []
    server = SimpleNamespace(is_alive=True)
    monkeypatch.setattr(launcher.install_layout, "detect", lambda: layout.InstallLayout(InstallKind.DEV))
    assert launcher._verify_install_when_ready(8090, server) is None

    installed = layout.InstallLayout(InstallKind.WINDOWS, Path("R"), "5.2.1", Path("R/versions/5.2.1"))
    monkeypatch.setattr(launcher.install_layout, "detect", lambda: installed)
    monkeypatch.setattr(launcher.install_layout, "verify_when_ready",
                        lambda found, port, alive: started.append((found, port, alive())))
    thread = launcher._verify_install_when_ready(8091, server)
    thread.join(5)

    assert started == [(installed, 8091, True)]


# ── Exit code 75 from the Python launcher ──────────────────────────────────


def test_direct_mode_stops_gracefully_and_exits_with_the_relaunch_code(monkeypatch):
    import row_bot.launcher as launcher

    stopped = []
    server = SimpleNamespace(is_alive=True, stop=lambda: stopped.append(True))
    monkeypatch.setattr(launcher, "_EXIT_REQUESTED", launcher.threading.Event())
    monkeypatch.setattr(launcher, "_requested_exit_code", 0)
    monkeypatch.setattr(launcher, "_ACTIVE_TRAY", None)
    monkeypatch.setattr(launcher, "_claim_instance", lambda: True)
    monkeypatch.setattr(launcher, "_write_launcher_state", lambda **_kwargs: None)
    monkeypatch.setattr(launcher, "_clear_launcher_state", lambda: None)
    # Server mode makes its process non-dumpable for good; this one is the test worker.
    monkeypatch.setattr(environment_secrets, "keep_environment_private", lambda: None)

    def run_direct(_args):
        launcher.quit_for_relaunch()
        launcher._block_until_interrupted(server, owns_server=True)

    monkeypatch.setattr(launcher, "_run_direct", run_direct)

    with pytest.raises(SystemExit) as stopped_with:
        launcher.main(["--server", "--no-open", "--no-splash", "--no-ollama", "--port", "18101"])

    assert stopped_with.value.code == 75
    assert stopped == [True]


def test_tray_mode_quits_through_the_normal_path_with_the_relaunch_code(monkeypatch):
    import row_bot.launcher as launcher

    exits = []
    stopped = []
    tray = launcher.RowBotTray.__new__(launcher.RowBotTray)
    tray._quitting = False
    tray._stop_event = launcher.threading.Event()
    tray._window_proc = None
    tray._owns_server = True
    tray._server = SimpleNamespace(stop=lambda: stopped.append("server"))
    tray._launcher_control = None
    tray._icon = SimpleNamespace(stop=lambda: None)
    monkeypatch.setattr(launcher, "_clear_launcher_state", lambda: stopped.append("state"))
    monkeypatch.setattr(launcher.os, "_exit", exits.append)
    monkeypatch.setattr(launcher, "_ACTIVE_TRAY", tray)
    monkeypatch.setattr(launcher, "_EXIT_REQUESTED", launcher.threading.Event())
    monkeypatch.setattr(launcher, "_requested_exit_code", 0)
    monkeypatch.setattr(launcher, "_QUIT_WATCHDOG_TIMEOUT", 60.0)

    launcher.quit_for_relaunch()
    for thread in list(launcher.threading.enumerate()):
        if thread.name == "quit-worker":
            thread.join(5)

    assert stopped == ["server", "state"]
    assert exits == [75]


# ── Installers ──────────────────────────────────────────────────────────────


def test_a_full_install_keeps_the_version_it_replaces_as_previous(tmp_path):
    for version in ("5.1.9", "5.2.0", "5.2.1"):
        (tmp_path / "versions" / version).mkdir(parents=True)
    layout.write_state(tmp_path, InstallState(active="5.2.0", previous="5.1.9", pending_verification=True,
                                              unverified_launches=1, ready={"version": "5.2.3", "staged_at": "t"}))

    state = layout.record_install(tmp_path, "5.2.1", kind=InstallKind.WINDOWS)
    removed = layout.prune_versions(tmp_path, InstallKind.WINDOWS, {state.active, state.previous})

    assert state == InstallState(active="5.2.1", previous="5.2.0")
    assert layout.read_state(tmp_path) == state
    assert removed == ["5.1.9"]
    assert sorted(p.name for p in (tmp_path / "versions").iterdir()) == ["5.2.0", "5.2.1"]


def test_reinstalling_the_active_version_keeps_its_previous(tmp_path):
    for version in ("5.2.0", "5.2.1"):
        (tmp_path / "versions" / version).mkdir(parents=True)
    layout.write_state(tmp_path, InstallState(active="5.2.1", previous="5.2.0"))

    assert layout.record_install(tmp_path, "5.2.1", kind=InstallKind.WINDOWS).previous == "5.2.0"


def test_a_first_install_has_no_previous(tmp_path):
    state = layout.record_install(tmp_path, "5.2.0", kind=InstallKind.WINDOWS)

    assert state == InstallState(active="5.2.0")


@posix_only
def test_the_command_line_applies_the_loop_guard_for_the_linux_wrapper(tmp_path, capsys):
    for version in ("5.2.0", "5.2.1"):
        _version(tmp_path, "releases", version)
        (tmp_path / "releases" / version / "python" / "bin").mkdir()
        (tmp_path / "releases" / version / "python" / "bin" / "python3").write_bytes(b"")
        (tmp_path / "releases" / version / "app" / "launcher.py").write_text("", encoding="utf-8")
    layout.set_linux_current(tmp_path, "5.2.1")
    layout.write_state(tmp_path, _pending(launches=2))
    root = str(tmp_path)

    assert layout.main(["begin-launch", "--root", root, "--kind", "linux"]) == 0
    assert capsys.readouterr().out.split() == ["5.2.0", "0"]
    assert os.readlink(tmp_path / "current") == "releases/5.2.0"
    assert layout.read_state(tmp_path).last_rollback["from"] == "5.2.1"
    assert layout.main(["after-exit", "--root", root, "--kind", "linux", "--version", "5.2.0", "--code", "0"]) == 1
    assert layout.main(["after-exit", "--root", root, "--kind", "linux", "--version", "5.2.0", "--code", "75"]) == 0


def test_an_install_records_that_an_old_install_for_all_users_is_still_there(tmp_path, capsys):
    root = str(tmp_path)

    assert layout.main(["record-install", "--root", root, "--kind", "windows", "--version", "5.2.0",
                        "--legacy-install-pending"]) == 0
    assert layout.read_state(tmp_path).extra == {"legacy_install_pending": True}
    # A later install that removed it clears the flag.
    assert layout.main(["record-install", "--root", root, "--kind", "windows", "--version", "5.2.0"]) == 0
    assert layout.read_state(tmp_path).extra == {}


def test_the_command_line_records_an_install_and_writes_version_json(tmp_path, capsys):
    (tmp_path / "versions" / "5.2.0").mkdir(parents=True)
    lock = tmp_path / "uv.lock"
    lock.write_bytes(b"lock")
    out = tmp_path / "versions" / "5.2.0" / "version.json"

    assert layout.main(["record-install", "--root", str(tmp_path), "--kind", "windows", "--version", "5.2.0"]) == 0
    assert layout.main(["version-json", "--version", "5.2.0", "--python", "3.13.2", "--lock", str(lock),
                        "--out", str(out)]) == 0

    assert capsys.readouterr().out.split() == ["5.2.0", "-"]
    assert layout.read_state(tmp_path) == InstallState(active="5.2.0")
    metadata = json.loads(out.read_text(encoding="utf-8"))
    assert metadata["version"] == "5.2.0" and metadata["python"] == "3.13.2"
    assert metadata["lock_hash"] == "sha256:" + hashlib.sha256(b"lock").hexdigest()


@pytest.mark.slow
def test_the_module_runs_standalone_without_site_packages(tmp_path):
    """The Linux wrapper and install.sh run it with `python -I -S` before anything else imports."""
    (tmp_path / "versions" / "5.2.0").mkdir(parents=True)

    result = subprocess.run(
        [sys.executable, "-I", "-S", str(MODULE), "record-install", "--root", str(tmp_path), "--kind", "windows",
         "--version", "5.2.0"],
        capture_output=True, text=True, timeout=60, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["5.2.0", "-"]
