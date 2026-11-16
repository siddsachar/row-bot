"""The Linux bin/row-bot wrapper: the shared launch cases, relaunch, the lock, systemd.

Runs the real installer/linux/row-bot against fake releases whose
python/bin/python3 hands install_layout.py to this interpreter and otherwise
records the launch, runs app/on_exit.sh if there is one, and exits with a
scripted code.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from row_bot import install_layout as layout


pytestmark = [
    pytest.mark.subsystem,
    pytest.mark.installer,
    pytest.mark.platform,
    # Never on Windows: bash there can be WSL's, which ignores this environment.
    pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="runs the POSIX wrapper"),
]

WRAPPER = Path("installer/linux/row-bot")
CASES = json.loads(Path("contracts/install-layout/launch-cases.json").read_text(encoding="utf-8"))["cases"]
NAMES = {"A": "1.0.0", "B": "2.0.0"}
FAKE_PYTHON = """#!/usr/bin/env bash
if [ "${1:-}" = "-I" ]; then exec "$REAL_PYTHON" "$@"; fi
release="$(basename "$(dirname "$PWD")")"
codes="$PWD/exit_codes"
code=0
if [ -s "$codes" ]; then
    code="$(head -n 1 "$codes")"
    sed -i.bak '1d' "$codes"
fi
# A sleeping app can be stopped from the moment its start is recorded.
if [ "$code" = "sleep" ]; then
    trap 'echo stopped >> "$FAKE_RUNS"; exit 0' TERM
fi
printf '%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n' "$release" "$$" "${PYTHONDONTWRITEBYTECODE:-}" \\
    "${PYTHONNOUSERSITE:-}" "${ROW_BOT_INSTALL_ROOT:-}" "$PWD" "${*:2}" >> "$FAKE_RUNS"
if [ "$code" = "sleep" ]; then
    while :; do sleep 0.1; done
fi
if [ -f "$PWD/on_exit.sh" ]; then
    . "$PWD/on_exit.sh"
fi
exit "$code"
"""
# What an applied update does: the next release becomes active and unverified.
SWITCH_TO = """"$REAL_PYTHON" - "$ROW_BOT_INSTALL_ROOT" {version} {remove} <<'PY'
import json, os, shutil, sys
from pathlib import Path
root, version, remove = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
state = json.loads((root / "state.json").read_text())
state.update(active=version, previous=state["active"], pending_verification=True, unverified_launches=0)
(root / "state.json").write_text(json.dumps(state))
os.symlink(f"releases/{{version}}", root / ".next")
os.replace(root / ".next", root / "current")
if remove != "-":
    shutil.rmtree(root / "releases" / remove)  # an update's cleanup removed an older release
PY
"""


def _release(root: Path, version: str, *codes: str, runnable: bool = True) -> Path:
    release = root / "releases" / version
    (release / "bin").mkdir(parents=True)
    module = release / "app" / "src" / "row_bot" / "install_layout.py"
    module.parent.mkdir(parents=True)
    shutil.copy2(layout.__file__, module)
    (release / "app" / "launcher.py").write_text("# fake\n", encoding="utf-8")
    shutil.copy2(WRAPPER, release / "bin" / "row-bot")
    (release / "bin" / "row-bot").chmod(0o755)
    if runnable:
        python = release / "python" / "bin" / "python3"
        python.parent.mkdir(parents=True)
        python.write_text(FAKE_PYTHON, encoding="utf-8")
        python.chmod(0o755)
    (release / "app" / "exit_codes").write_text("".join(f"{code}\n" for code in codes), encoding="utf-8")
    return release


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "ROW_BOT_DATA_DIR": str(tmp_path / "data"),
        "REAL_PYTHON": sys.executable,
        "FAKE_RUNS": str(tmp_path / "runs.tsv"),
    }
    env.update(extra)
    return env


def _runs(tmp_path: Path) -> list[list[str]]:
    path = tmp_path / "runs.tsv"
    if not path.exists():
        return []
    return [line.split("\t") for line in path.read_text(encoding="utf-8").splitlines() if "\t" in line]


def _run(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, env=env, capture_output=True, text=True, timeout=60, check=False)


def _named(value):
    if isinstance(value, dict):
        return {key: _named(item) for key, item in value.items()}
    return NAMES.get(value, value) if isinstance(value, str) else value


# One case stays in the PR lane (under a second): the rollback guards against a broken update.
@pytest.mark.parametrize(
    "case",
    [pytest.param(case, id=case["id"], marks=() if case["id"] == "two-failed-starts-roll-back-to-previous"
                  else pytest.mark.slow) for case in CASES],
)
def test_the_wrapper_follows_the_shared_launch_cases(tmp_path, case):
    root = tmp_path / "row-bot"
    for letter, name in NAMES.items():
        _release(root, name, *map(str, case["exits"].get(letter, [])), runnable=letter in case["runnable"])
    state = {"schema": 1, "ready": None, "last_rollback": None, **_named(case["state"])}
    (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
    layout.set_linux_current(root, state["active"])

    result = _run([str(root / "current" / "bin" / "row-bot"), "serve", "--port", "8123"], _env(tmp_path))

    assert [run[0] for run in _runs(tmp_path)] == [NAMES[letter] for letter in case["launched"]], result.stderr
    assert all(run[6] == "serve --port 8123" for run in _runs(tmp_path))
    final = json.loads((root / "state.json").read_text(encoding="utf-8"))
    for key, expected in _named(case["final"]).items():
        if key == "last_rollback":
            assert {field: final[key][field] for field in expected} == expected
        else:
            assert final[key] == expected, (key, final)
    assert os.readlink(root / "current") == f"releases/{final['active']}"


@pytest.mark.slow
def test_each_relaunch_runs_the_current_release_even_after_its_own_was_removed(tmp_path):
    root = tmp_path / "row-bot"
    _release(root, "1.0.0", "75")
    (root / "releases" / "1.0.0" / "app" / "on_exit.sh").write_text(SWITCH_TO.format(version="2.0.0", remove="-"),
                                                                    encoding="utf-8")
    _release(root, "2.0.0", "75")
    (root / "releases" / "2.0.0" / "app" / "on_exit.sh").write_text(
        SWITCH_TO.format(version="3.0.0", remove="1.0.0"), encoding="utf-8")
    _release(root, "3.0.0", "0")
    layout.set_linux_current(root, "1.0.0")
    bin_home = tmp_path / "home" / ".local" / "bin"
    bin_home.mkdir(parents=True)
    (bin_home / "row-bot").symlink_to(root / "current" / "bin" / "row-bot")

    result = _run([str(bin_home / "row-bot"), "serve", "--port", "8123"], _env(tmp_path))

    assert result.returncode == 0, result.stderr
    runs = _runs(tmp_path)
    assert [run[0] for run in runs] == ["1.0.0", "2.0.0", "3.0.0"]
    assert len({run[1] for run in runs}) == 3
    for run, version in zip(runs, ("1.0.0", "2.0.0", "3.0.0")):
        # The launch environment, and the arguments kept across every relaunch.
        assert run[2:5] == ["1", "1", str(root.resolve())]
        assert run[5].endswith(f"/releases/{version}/app")
        assert run[6] == "serve --port 8123"
    assert not (root / "releases" / "1.0.0").exists()


@pytest.mark.slow
@pytest.mark.skipif(not shutil.which("flock"), reason="the single-launch lock uses flock")
def test_a_second_start_while_row_bot_runs_neither_counts_nor_supervises(tmp_path):
    root = tmp_path / "row-bot"
    _release(root, "1.0.0", "0")
    _release(root, "2.0.0", "sleep", "0")
    layout.set_linux_current(root, "2.0.0")
    layout.write_state(root, layout.InstallState(active="2.0.0", previous="1.0.0", pending_verification=True))
    command = [str(root / "current" / "bin" / "row-bot"), "serve"]
    first = subprocess.Popen(command, env=_env(tmp_path))
    deadline = time.monotonic() + 30
    while not _runs(tmp_path) and time.monotonic() < deadline:
        time.sleep(0.05)

    for _start in range(3):
        assert _run(command, _env(tmp_path)).returncode == 0

    state = layout.read_state(root)
    assert (state.active, state.unverified_launches) == ("2.0.0", 1)
    assert len(_runs(tmp_path)) == 4  # each second start ran launcher.py once, to hand over
    first.send_signal(signal.SIGTERM)
    assert first.wait(timeout=30) == 0


def test_an_editors_mcp_bridge_runs_the_current_release_once_and_never_supervises(tmp_path):
    root = tmp_path / "row-bot"
    _release(root, "1.0.0", "0")
    _release(root, "2.0.0", "3", "3")
    layout.set_linux_current(root, "2.0.0")
    layout.write_state(root, layout.InstallState(active="2.0.0", previous="1.0.0", pending_verification=True))

    result = _run([str(root / "current" / "bin" / "row-bot"), "mcp", "--editor", "codex"], _env(tmp_path))

    # Its exit is the bridge's own: never retried, never counted toward a rollback.
    assert result.returncode == 3
    assert [(run[0], run[6]) for run in _runs(tmp_path)] == [("2.0.0", "mcp --editor codex")]
    state = layout.read_state(root)
    assert (state.active, state.pending_verification, state.unverified_launches) == ("2.0.0", True, 0)


@pytest.mark.slow
def test_a_current_release_that_cannot_start_is_reported_not_entered(tmp_path):
    root = tmp_path / "row-bot"
    _release(root, "2.0.0", runnable=False)
    layout.set_linux_current(root, "2.0.0")

    result = _run([str(root / "current" / "bin" / "row-bot"), "serve"], _env(tmp_path))

    assert result.returncode == 1
    assert "no installed release can start" in result.stderr
    assert _runs(tmp_path) == []


@pytest.mark.slow
def test_without_arguments_the_wrapper_opens_the_browser_client(tmp_path):
    root = tmp_path / "row-bot"
    _release(root, "1.0.0", "0")
    layout.set_linux_current(root, "1.0.0")

    assert _run([str(root / "current" / "bin" / "row-bot")], _env(tmp_path)).returncode == 0
    assert _runs(tmp_path)[0][6] == "--browser --no-tray"


@pytest.mark.slow
def test_run_by_systemd_the_unit_restarts_the_app(tmp_path):
    root = tmp_path / "row-bot"
    _release(root, "1.0.0", "75", "0")
    layout.set_linux_current(root, "1.0.0")
    # What systemd 248+ does for ExecStart=: SYSTEMD_EXEC_PID is the started process.
    command = ["bash", "-c", 'export SYSTEMD_EXEC_PID=$$; exec "$0" "$@"', str(root / "current" / "bin" / "row-bot"),
               "serve"]

    process = subprocess.Popen(command, env=_env(tmp_path, INVOCATION_ID="unit"), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    process.communicate(timeout=60)

    assert process.returncode == 75
    runs = _runs(tmp_path)
    assert len(runs) == 1
    assert runs[0][1] == str(process.pid)  # exec'd: systemd sees the app itself


@pytest.mark.slow
def test_a_shell_inside_a_systemd_service_still_relaunches(tmp_path):
    # Terminals and CI runners that run as services pass their unit's variables on.
    root = tmp_path / "row-bot"
    _release(root, "1.0.0", "75", "0")
    layout.set_linux_current(root, "1.0.0")

    result = _run([str(root / "current" / "bin" / "row-bot"), "serve"],
                  _env(tmp_path, INVOCATION_ID="terminal", SYSTEMD_EXEC_PID="1"))

    assert result.returncode == 0, result.stderr
    assert len(_runs(tmp_path)) == 2


@pytest.mark.slow
def test_an_unpacked_tarball_runs_itself_without_state(tmp_path):
    package = tmp_path / "Row-Bot-1.0.0-Linux-x86_64"
    _release(tmp_path / "unpacked", "x", "0").rename(package)

    result = _run([str(package / "bin" / "row-bot"), "--server"], _env(tmp_path))

    assert result.returncode == 0, result.stderr
    assert _runs(tmp_path)[0][4] == str(package.resolve())
    assert not (tmp_path / "state.json").exists()


@pytest.mark.slow
def test_stopping_the_wrapper_stops_the_app_and_never_relaunches(tmp_path):
    root = tmp_path / "row-bot"
    _release(root, "1.0.0", "sleep", "0")
    layout.set_linux_current(root, "1.0.0")
    process = subprocess.Popen([str(root / "current" / "bin" / "row-bot"), "serve"], env=_env(tmp_path))
    deadline = time.monotonic() + 30
    while not _runs(tmp_path) and time.monotonic() < deadline:
        time.sleep(0.05)

    process.send_signal(signal.SIGTERM)

    assert process.wait(timeout=30) == 0
    assert (tmp_path / "runs.tsv").read_text(encoding="utf-8").splitlines()[-1] == "stopped"
    assert len(_runs(tmp_path)) == 1


@pytest.mark.slow
def test_a_stop_while_the_app_starts_never_leaves_it_running(tmp_path):
    """A stop can reach the wrapper at any moment of its start, before or after the app is running:
    the wrapper always ends and never leaves the app running on its own."""
    for attempt, delay in enumerate((0, 0.005, 0.01, 0.02, 0.05, 0.1)):
        here = tmp_path / str(attempt)
        root = here / "row-bot"
        _release(root, "1.0.0", "sleep")
        layout.set_linux_current(root, "1.0.0")
        process = subprocess.Popen([str(root / "current" / "bin" / "row-bot"), "serve"], env=_env(here))
        time.sleep(delay)
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=30)
        left = []
        for run in _runs(here):
            pid, deadline = int(run[1]), time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                left.append(pid)
                os.kill(pid, signal.SIGKILL)
        assert not left, f"stopped after {delay}s: the app was left running"


@pytest.mark.slow
@pytest.mark.skipif(sys.platform != "linux", reason="install.sh uses GNU sed")
def test_install_sh_records_the_release_and_keeps_the_one_it_replaces(tmp_path):
    xdg = tmp_path / "xdg"
    home = tmp_path / "home"
    home.mkdir()
    env = {**_env(tmp_path), "XDG_DATA_HOME": str(xdg), "ROW_BOT_SUPPRESS_INSTALL_PATH_HINT": "1"}
    for version in ("1.0.0", "1.1.0", "1.2.0"):
        package = tmp_path / f"package-{version}"
        _release(tmp_path / "packages", version).rename(package)
        shutil.copy2("installer/linux/install.sh", package / "install.sh")
        (package / "install.sh").chmod(0o755)
        (package / "share" / "applications").mkdir(parents=True)
        (package / "share" / "applications" / "ai.row-bot.RowBot.desktop").write_text("Exec=row-bot\n", encoding="utf-8")
        (package / "install_info.json").write_text(json.dumps({"version": version}), encoding="utf-8")
        python = package / "python" / "bin" / "python3"
        python.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n', encoding="utf-8")

        result = _run(["bash", str(package / "install.sh")], env)
        assert result.returncode == 0, result.stderr

    root = xdg / "row-bot"
    assert layout.read_state(root) == layout.InstallState(active="1.2.0", previous="1.1.0")
    assert os.readlink(root / "current") == "releases/1.2.0"
    assert sorted(path.name for path in (root / "releases").iterdir()) == ["1.1.0", "1.2.0"]
    assert os.readlink(home / ".local" / "bin" / "row-bot") == str(root / "current" / "bin" / "row-bot")
