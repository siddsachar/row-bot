"""Row-Bot.exe against a fake per-user install: environment, relaunch, rollback.

Builds installer/windows/row_bot_launcher.c with MSVC (or uses the executable
named by ROW_BOT_LAUNCHER_EXE) and runs it against version folders whose
python\\ is this test's own Python and whose launcher.py records each start and
exits with a scripted code. The installed package gets the same checks against
the real app in installer-verify (scripts/smoke_install_layout.py).
"""
from __future__ import annotations

import json
import os
import shutil
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
    pytest.mark.slow,
    pytest.mark.skipif(os.name != "nt", reason="Row-Bot.exe is the Windows launcher"),
]

SOURCE = Path("installer/windows/row_bot_launcher.c")
CASES = json.loads(Path("contracts/install-layout/launch-cases.json").read_text(encoding="utf-8"))["cases"]
NAMES = {"A": "5.2.0", "B": "5.2.1"}
FAKE_LAUNCHER = """\
import json, os, sys
from pathlib import Path
here = Path(__file__).resolve().parent
codes = here / "exit_codes.json"
queue = json.loads(codes.read_text()) if codes.exists() else []
code = queue.pop(0) if queue else 0
codes.write_text(json.dumps(queue))
with open(os.environ["FAKE_RUNS"], "a", encoding="utf-8") as runs:
    runs.write(json.dumps({
        "version": here.parent.name,
        "args": sys.argv[1:],
        "cwd": os.getcwd(),
        "env": {name: os.environ.get(name) for name in (
            "ROW_BOT_INSTALL_ROOT", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE", "PYTHONIOENCODING",
            "TCL_LIBRARY", "PLAYWRIGHT_BROWSERS_PATH")},
    }) + "\\n")
sys.exit(code)
"""


def _msvc_build(out_dir: Path) -> Path | None:
    vswhere = Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        return None
    found = subprocess.run(
        [str(vswhere), "-latest", "-products", "*", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
         "-property", "installationPath"], capture_output=True, text=True, check=False,
    ).stdout.strip()
    if not found:
        return None
    vcvars = Path(found) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    exe = out_dir / "Row-Bot.exe"
    command = (f'"{vcvars}" >nul && cl /nologo /utf-8 /W4 /WX /O2 /MT /Fo"{out_dir / "launcher.obj"}" '
               f'/Fe"{exe}" "{SOURCE.resolve()}" /link /SUBSYSTEM:WINDOWS')
    # One string, not a list: a list escapes the quotes as \", which cmd.exe reads as part of the path.
    result = subprocess.run(f'cmd.exe /d /s /c "{command}"', capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return exe


@pytest.fixture(scope="module")
def launcher_exe(tmp_path_factory) -> Path:
    configured = os.environ.get("ROW_BOT_LAUNCHER_EXE")
    if configured:
        return Path(configured)
    exe = _msvc_build(tmp_path_factory.mktemp("launcher-build"))
    if exe is None:
        pytest.skip("MSVC is not installed; set ROW_BOT_LAUNCHER_EXE to a built Row-Bot.exe")
    return exe


class FakeInstall:
    def __init__(self, root: Path, exe: Path) -> None:
        self.root = root
        self.runs = root.parent / "runs.jsonl"
        root.mkdir(parents=True)
        shutil.copy2(exe, root / "Row-Bot.exe")

    @property
    def data(self) -> Path:
        return self.root.parent / "data"

    def version(self, name: str, *codes: int, runnable: bool = True, bundled_browsers: bool = False) -> Path:
        folder = self.root / "versions" / name
        (folder / "app").mkdir(parents=True)
        if bundled_browsers:
            # A folder of its own (a junction would point into the real Python): a
            # virtual environment's launcher next to its pyvenv.cfg runs the base Python.
            venv = self.root.parent / "venv"
            if not venv.exists():
                subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True,
                               capture_output=True)
            (folder / "python" / "playwright-browsers").mkdir(parents=True)
            shutil.copy2(venv / "Scripts" / "python.exe", folder / "python" / "python.exe")
            shutil.copy2(venv / "pyvenv.cfg", folder / "python" / "pyvenv.cfg")
        elif runnable:
            subprocess.run(["cmd", "/c", "mklink", "/J", str(folder / "python"), str(Path(sys.base_prefix))],
                           check=True, capture_output=True)
        (folder / "app" / "launcher.py").write_text(FAKE_LAUNCHER, encoding="utf-8")
        (folder / "app" / "exit_codes.json").write_text(json.dumps(list(codes)), encoding="utf-8")
        return folder

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "FAKE_RUNS": str(self.runs), "ROW_BOT_DATA_DIR": str(self.data)}
        return subprocess.run([str(self.root / "Row-Bot.exe"), *args], env=env, capture_output=True, text=True,
                              timeout=120, check=False)

    def write_raw_state(self, state: dict) -> None:
        (self.root / "state.json").write_text(json.dumps(state), encoding="utf-8")

    def launches(self) -> list[dict]:
        if not self.runs.exists():
            return []
        return [json.loads(line) for line in self.runs.read_text(encoding="utf-8").splitlines()]

    def close(self) -> None:
        for folder in (self.root / "versions").glob("*"):
            link = folder / "python"
            if link.is_junction():
                os.rmdir(link)  # the junction, never the Python it points at


@pytest.fixture
def install(tmp_path, launcher_exe):
    fake = FakeInstall(tmp_path / "Row-Bot", launcher_exe)
    yield fake
    fake.close()


def test_it_starts_the_active_version_with_the_launch_environment_and_relaunches_on_75(install):
    good = install.version("5.2.0", 75, 0)
    layout.write_state(install.root, layout.InstallState(active="5.2.0", extra={"kept": {"nested": ["}", 1]}}))

    result = install.run("--server", "--port", "8123", "two words")

    assert result.returncode == 0, result.stderr
    launches = install.launches()
    assert [launch["version"] for launch in launches] == ["5.2.0", "5.2.0"]
    for launch in launches:
        assert launch["args"] == ["--server", "--port", "8123", "two words"]
        assert Path(launch["cwd"]) == good / "app"
        assert launch["env"] == {
            "ROW_BOT_INSTALL_ROOT": str(install.root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONIOENCODING": "utf-8",
            "TCL_LIBRARY": str(good / "python" / "tcl" / "tcl8.6"),
            # No Chromium in the version folder: Playwright's browsers live in the
            # data folder, which updates never replace and Row-Bot.exe creates.
            "PLAYWRIGHT_BROWSERS_PATH": str(install.data / "playwright-browsers"),
        }
    assert (install.data / "playwright-browsers").is_dir()
    assert not (good / "python" / "playwright-browsers").exists()
    assert layout.read_state(install.root).extra == {"kept": {"nested": ["}", 1]}}


def test_a_version_that_bundles_chromium_keeps_using_its_own_copy(install):
    older = install.version("5.2.0", 0, bundled_browsers=True)
    layout.write_state(install.root, layout.InstallState(active="5.2.0"))

    result = install.run("--server")

    assert result.returncode == 0, result.stderr
    [launch] = install.launches()
    assert launch["env"]["PLAYWRIGHT_BROWSERS_PATH"] == str(older / "python" / "playwright-browsers")
    assert not (install.data / "playwright-browsers").exists()


def _named(value):
    if isinstance(value, dict):
        return {key: _named(item) for key, item in value.items()}
    return NAMES.get(value, value) if isinstance(value, str) else value


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_row_bot_exe_follows_the_shared_launch_cases(install, case):
    for letter, name in NAMES.items():
        install.version(name, *case["exits"].get(letter, []), runnable=letter in case["runnable"])
    install.write_raw_state({"schema": 1, "ready": None, "last_rollback": None, **_named(case["state"])})

    result = install.run("--server")

    assert [launch["version"] for launch in install.launches()] == [NAMES[letter] for letter in case["launched"]]
    state = json.loads((install.root / "state.json").read_text(encoding="utf-8"))
    for key, expected in _named(case["final"]).items():
        if key == "last_rollback":
            assert {field: state[key][field] for field in expected} == expected
        else:
            assert state[key] == expected, (key, state)
    assert result.returncode in {0, 3}, result.stderr


def test_two_unverified_launches_that_fail_roll_back_to_previous(install):
    install.version("5.2.0", 0)
    install.version("5.2.1", 3, 3, 3)
    layout.write_state(install.root, layout.InstallState(
        active="5.2.1", previous="5.2.0", pending_verification=True, ready={"version": "5.2.1", "staged_at": "t"},
    ))

    result = install.run("--server")

    assert result.returncode == 0, result.stderr
    assert [launch["version"] for launch in install.launches()] == ["5.2.1", "5.2.1", "5.2.0"]
    state = layout.read_state(install.root)
    assert (state.active, state.previous, state.pending_verification, state.ready) == ("5.2.0", None, False, None)
    assert state.last_rollback["from"] == "5.2.1" and state.last_rollback["to"] == "5.2.0"


def test_a_quit_during_an_unverified_start_is_counted_but_not_retried(install):
    install.version("5.2.0", 0)
    install.version("5.2.1", 0)
    layout.write_state(install.root, layout.InstallState(active="5.2.1", previous="5.2.0", pending_verification=True))

    assert install.run().returncode == 0
    assert [launch["version"] for launch in install.launches()] == ["5.2.1"]
    state = layout.read_state(install.root)
    assert state.pending_verification is True and state.unverified_launches == 1


def test_without_state_the_newest_installed_version_starts(install):
    for name in ("5.2.9", "5.2.10", "5.2.10-rc.1"):
        install.version(name, 0)

    assert install.run().returncode == 0
    assert [launch["version"] for launch in install.launches()] == ["5.2.10"]
    assert layout.read_state(install.root) == layout.InstallState(active="5.2.10")


def test_ending_row_bot_exe_ends_the_launcher_it_started(install):
    psutil = pytest.importorskip("psutil")
    folder = install.version("5.2.0")
    pid_file = install.root.parent / "launcher.pid"
    (folder / "app" / "launcher.py").write_text(
        f"import os, time\nopen({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(120)\n",
        encoding="utf-8",
    )
    layout.write_state(install.root, layout.InstallState(active="5.2.0"))
    env = {**os.environ, "FAKE_RUNS": str(install.runs), "ROW_BOT_DATA_DIR": str(install.data)}
    exe = subprocess.Popen([str(install.root / "Row-Bot.exe")], env=env)
    launched = None
    try:
        deadline = time.monotonic() + 30
        while not (pid_file.exists() and pid_file.read_text()) and time.monotonic() < deadline:
            time.sleep(0.1)
        launched = psutil.Process(int(pid_file.read_text()))
        exe.kill()  # as Task Manager ends it: no clean-up runs
        exe.wait(timeout=10)
        launched.wait(timeout=10)
    finally:
        if exe.poll() is None:
            exe.kill()
        if launched is not None and launched.is_running():
            launched.kill()
            pytest.fail("launcher.py outlived Row-Bot.exe")

