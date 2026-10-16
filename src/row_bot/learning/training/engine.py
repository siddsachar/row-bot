"""The training engine: installed, checked and removed by Row-Bot (L2b, E1, O25, O43).

The engine is its own locked environment (``row_bot/learning_engine/``: CUDA PyTorch, Unsloth,
TRL, PEFT), never part of Row-Bot's own. It is installed only after the person agrees to the
download, into the data folder's ``runtimes/learning/engine/``:

1. **Download.** Every wheel the lock names for this computer and variant goes into
   ``engine/downloads/`` through :mod:`fetch` (pinned SHA-256, resumable after a Stop, a dropped
   connection or a restart), and so does the lock's own pinned ``uv``.
2. **Build.** In a fresh ``engine/.staging-<id>/``: ``uv`` is unpacked from its verified wheel;
   ``uv python install`` puts the engine's own CPython (3.12.15) inside the build as ``py/`` (no
   ``bin`` links, no registry entry, nothing outside the folder); ``uv pip install --offline
   --no-index --require-hashes --no-deps --no-build`` installs exactly the downloaded wheels into
   that private interpreter's own site-packages (uv checks every hash again). Its own, because
   libraries find their bundled tools there: triton-windows looks for its TinyCC and CUDA files
   in the interpreter's ``platlib``.
3. **Check.** Every installed package and version must be the plan's, every file the wheels'
   ``RECORD`` lists must be there with its size, and the engine's own check runs (it imports the
   packages, under the network guard, and asks for the graphics card).
4. **Publish.** The build is renamed to ``engine/<id>/`` and ``engine/installed.json`` is
   written last (atomically). Until then nothing calls the engine installed, so a build cut
   short (a Stop, Row-Bot closing, a full disk) is never used; the next attempt reuses the
   downloaded wheels and builds again.

The worker runs the engine's **base interpreter** (never a virtual environment's launcher,
O43) with ``-I -S`` and ``learning_engine/entry.py``, which adds the engine's site-packages and
switches every library's usage reporting off before anything is imported (E14, O27).

**Remove engine** deletes the whole folder (and its caches) and reports the space it freed.
An engine whose files are missing reads as *damaged*; one built from another version of the
lock (Row-Bot was updated) as *outdated*. Either way the next download builds it again.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from row_bot.learning.training import fetch, lockfile
from row_bot.learning.training.lockfile import EnginePlan, LockedWheel

logger = logging.getLogger(__name__)

FORMAT = 1
ENTRY = lockfile.ENGINE_DIR / "entry.py"
INSTALLED_FILE = "installed.json"
RECORD_FILE = "engine.json"
DOWNLOADS = "downloads"
CHECK_TIMEOUT_SECONDS = 600
UV_TIMEOUT_SECONDS = 3600
# Unpacked, the engine is larger than its download (measured 3.4 GB from 2.2 GB on Windows, L0d).
INSTALLED_FACTOR = 1.8
INSTALLED_EXTRA_BYTES = 200_000_000  # the interpreter and the bytecode uv compiles

EngineState = Literal["not_installed", "installed", "damaged", "outdated"]


class EngineError(RuntimeError):
    """The engine couldn't be installed or removed; ``code`` is stable: ``stopped``, ``network``,
    ``refused``, ``mismatch``, ``out_of_disk``, ``install_failed``, ``check_failed``, ``no_cuda``,
    ``busy``."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# ── Where it lives ──────────────────────────────────────────────────────────


def learning_runtimes(root: Path | None = None) -> Path:
    """``<data>/runtimes/learning``: the engine, the starting models and their caches."""

    if root is None:
        from row_bot.data_paths import get_row_bot_data_dir

        root = get_row_bot_data_dir(create=False)
    return Path(root) / "runtimes" / "learning"


def engine_dir(root: Path | None = None) -> Path:
    return learning_runtimes(root) / "engine"


def cache_dir(root: Path | None = None) -> Path:
    return learning_runtimes(root) / "cache"


def downloads_dir(root: Path | None = None) -> Path:
    return engine_dir(root) / DOWNLOADS


def _plain_dir(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & 0x400)


def folder_bytes(path: Path) -> int:
    """Bytes of the files under ``path`` (links are not followed)."""

    total = 0
    if not _plain_dir(path):
        return 0
    for current, folders, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(current, name)).st_size
            except OSError:
                continue
    return total


def _remove_tree(path: Path) -> None:
    """Delete a folder Row-Bot made (read-only files too); a link is removed, never followed."""

    if not os.path.lexists(path):
        return
    if not _plain_dir(path):
        Path(path).unlink(missing_ok=True)
        return

    def retry(function: Callable[..., Any], target: str, _error: Any) -> None:
        try:
            os.chmod(target, stat.S_IWRITE)
            function(target)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:  # pragma: no cover - the app runs 3.12+
        shutil.rmtree(path, onerror=retry)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _read_json(path: Path, limit: int = 1024 * 1024) -> dict[str, Any] | None:
    try:
        if path.stat().st_size > limit:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


# ── What is installed ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class EngineStatus:
    """The installed engine, if any, and whether it can be used."""

    state: EngineState
    variant: str | None = None
    folder: Path | None = None
    python: Path | None = None
    site: Path | None = None
    bytes: int = 0
    reason: str | None = None

    @property
    def installed(self) -> bool:
        return self.state == "installed"


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _dist_versions(site: Path) -> dict[str, str]:
    """``{name: version}`` from the ``*.dist-info`` folders (nothing is imported)."""

    from packaging.utils import canonicalize_name

    found: dict[str, str] = {}
    try:
        entries = list(site.iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.name.endswith(".dist-info") or not _plain_dir(entry):
            continue
        name = version = ""
        try:
            with open(entry / "METADATA", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if line.startswith("Name:"):
                        name = line.split(":", 1)[1].strip()
                    elif line.startswith("Version:"):
                        version = line.split(":", 1)[1].strip()
                    elif not line.strip():
                        break
        except OSError:
            continue
        if name and version:
            found[canonicalize_name(name)] = version
    return found


def _versions_match(found: dict[str, str], expected: dict[str, str]) -> bool:
    from packaging.version import InvalidVersion, Version

    if set(found) != set(expected):
        return False
    for name, version in expected.items():
        try:
            if Version(found[name]) != Version(version):
                return False
        except InvalidVersion:
            return False
    return True


_status_lock = threading.Lock()
_status_cache: dict[str, tuple[tuple[Any, ...], EngineStatus, float]] = {}
# A few file checks, read again at most this often (a damaged engine shows within a minute).
STATUS_SECONDS = 60.0


def _identity(path: Path) -> tuple[Any, ...]:
    try:
        info = path.stat()
        return (str(path), info.st_mtime_ns, info.st_size)
    except OSError:
        return (str(path), None, None)


def current_plan_identity(variant: str) -> str | None:
    """The identity of what this version of Row-Bot would install for ``variant`` here."""

    try:
        return lockfile.cached_plan(variant).identity
    except Exception:
        logger.debug("Learning could not read the engine's lock", exc_info=True)
        return None


def status(root: Path | None = None, *, plan_identity: Callable[[str], str | None] = current_plan_identity,
           refresh: bool = False) -> EngineStatus:
    """The installed engine's state: a few file checks, cached until ``installed.json`` changes."""

    base = engine_dir(root)
    marker = base / INSTALLED_FILE
    key = _identity(marker)
    now = time.monotonic()
    with _status_lock:
        cached = _status_cache.get(str(base))
        if cached is not None and cached[0] == key and not refresh and now - cached[2] < STATUS_SECONDS:
            return cached[1]
    found = _read_status(base, plan_identity)
    with _status_lock:
        _status_cache[str(base)] = (key, found, now)
    return found


def forget_status() -> None:
    with _status_lock:
        _status_cache.clear()


def _read_status(base: Path, plan_identity: Callable[[str], str | None]) -> EngineStatus:
    installed = _read_json(base / INSTALLED_FILE)
    if installed is None:
        return EngineStatus("not_installed")
    name = str(installed.get("folder") or "")
    folder = base / name
    variant = installed.get("variant") if installed.get("variant") in lockfile.VARIANTS else None
    if not name or "/" in name or "\\" in name or name.startswith(".") or not _plain_dir(folder):
        return EngineStatus("damaged", variant, reason="folder")
    record = _read_json(folder / RECORD_FILE)
    if record is None or record.get("format") != FORMAT:
        return EngineStatus("damaged", variant, folder, reason="record")
    python = folder / str(record.get("python") or "")
    site = folder / str(record.get("site") or "")
    if not record.get("python") or not _inside(python, folder) or not python.is_file():
        return EngineStatus("damaged", variant, folder, reason="python")
    if not record.get("site") or not _inside(site, folder) or not _plain_dir(site):
        return EngineStatus("damaged", variant, folder, python, reason="site")
    expected = record.get("packages")
    if not isinstance(expected, dict):
        return EngineStatus("damaged", variant, folder, python, site, reason="record")
    found = _dist_versions(site)
    core = {name: expected[name] for name in lockfile.CORE if name in expected}
    if any(found.get(name) != version for name, version in core.items()):
        return EngineStatus("damaged", variant, folder, python, site, reason="packages")
    size = int(record.get("bytes") or 0)
    if variant is None or plan_identity(variant) != record.get("plan"):
        return EngineStatus("outdated", variant, folder, python, site, size, reason="plan")
    return EngineStatus("installed", variant, folder, python, site, size)


def check_files(site: Path, check: Callable[[], None] = lambda: None) -> list[str]:
    """Files the installed wheels' ``RECORD`` lists that are missing or the wrong size."""

    problems: list[str] = []
    for entry in sorted(site.glob("*.dist-info")):
        record = entry / "RECORD"
        try:
            lines = record.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            problems.append(entry.name)
            continue
        for line in lines:
            check()
            parts = line.rsplit(",", 2)
            if len(parts) != 3 or not parts[0]:
                continue
            relative, _digest, size = parts
            path = (site / relative).resolve() if ".." in relative else site / relative
            if ".." in relative and not _inside(path, site.parent):
                continue  # scripts written outside site-packages (none with --target)
            try:
                actual = path.stat().st_size
            except OSError:
                problems.append(relative)
                continue
            if size.isdigit() and int(size) != actual:
                problems.append(relative)
    return problems


# ── Running uv and the engine's check ───────────────────────────────────────


def _base_environment(home: Path) -> dict[str, str]:
    """A process environment with nothing of the person's: no keys, no pip or uv configuration."""

    keep = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "SYSTEMDRIVE", "NUMBER_OF_PROCESSORS",
            "PROCESSOR_ARCHITECTURE", "LANG", "LC_ALL")
    environment = {key: value for key, value in os.environ.items() if key.upper() in keep}
    temp = home / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    environment.update({
        "HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home), "LOCALAPPDATA": str(home),
        "XDG_CACHE_HOME": str(temp), "XDG_DATA_HOME": str(home), "XDG_CONFIG_HOME": str(home),
        "TEMP": str(temp), "TMP": str(temp), "TMPDIR": str(temp),
        "PYTHONNOUSERSITE": "1", "PIP_CONFIG_FILE": os.devnull, "NETRC": str(home / ".disabled-netrc"),
        "USERNAME": "row-bot", "USER": "row-bot", "LOGNAME": "row-bot",  # as the worker's (worker.py)
        "UV_NO_CONFIG": "1", "UV_NO_PROGRESS": "1", "UV_PYTHON_PREFERENCE": "only-managed",
        "UV_PYTHON_INSTALL_REGISTRY": "0", "UV_NO_MODIFY_PATH": "1",
    })
    from row_bot.learning.training.worker import TELEMETRY_ENV

    environment.update(TELEMETRY_ENV)
    return environment


@dataclass
class Run:
    """One finished command: its exit code and the end of what it printed."""

    code: int
    stdout: str
    stderr: str


def run_owned(argv: Sequence[str], *, cwd: Path, env: dict[str, str], timeout: float,
              check: Callable[[], None]) -> Run:
    """Run a helper (uv, the engine's check) as an owned process tree; ``check`` raising stops it."""

    cwd.mkdir(parents=True, exist_ok=True)
    options: dict[str, Any] = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(list(argv), cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=out,
                                   stderr=err, shell=False, **options)
        job = None
        if os.name == "nt":
            try:
                from row_bot.plugins.worker_ownership import WindowsJob

                job = WindowsJob(process)
            except Exception:
                logger.debug("Learning could not own a helper process with a job object", exc_info=True)
        deadline = time.monotonic() + timeout
        stopped: BaseException | None = None
        try:
            while True:
                try:
                    process.wait(0.25)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if time.monotonic() > deadline:
                    stopped = EngineError("install_failed")
                    break
                try:
                    check()
                except BaseException as error:  # noqa: BLE001 - a Stop: end the tree, then re-raise
                    stopped = error
                    break
        finally:
            if process.poll() is None or job is not None:
                if job is not None:
                    job.close()
                elif os.name != "nt":
                    try:
                        os.killpg(process.pid, 9)
                    except OSError:
                        pass
                else:  # pragma: no cover - a job object is always available on Windows
                    process.kill()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                pass
        if stopped is not None:
            raise stopped
        out.seek(0)
        err.seek(0)
        stdout = out.read()[-65536:].decode("utf-8", "replace")
        stderr = err.read()[-65536:].decode("utf-8", "replace")
    return Run(int(process.returncode or 0), stdout, stderr)


def engine_check(python: Path, site: Path, *, cwd: Path, check: Callable[[], None],
                 runner: Callable[..., Run] = run_owned, timeout: float = CHECK_TIMEOUT_SECONDS) -> dict[str, Any]:
    """The engine's own check: import every package, under the network guard, and find the GPU."""

    env = _base_environment(cwd)
    env.update(engine_variables())
    argv = [str(python), "-I", "-S", str(ENTRY), "check", "--site", str(site)]
    result = runner(argv, cwd=cwd, env=env, timeout=timeout, check=check)
    report: dict[str, Any] | None = None
    for line in reversed(result.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                report = json.loads(line)
                break
            except ValueError:
                continue
    if result.code != 0 or not isinstance(report, dict) or report.get("ok") is not True:
        logger.warning("The training engine's check failed (exit %s, %s): %s", result.code,
                       (report or {}).get("error"), result.stderr[-1500:])
        raise EngineError("check_failed")
    if report.get("cuda") is not True:
        raise EngineError("no_cuda")
    return report


def engine_variables(root: Path | None = None) -> dict[str, str]:
    """The engine's own settings: its caches in Row-Bot's folder, L0d's workarounds (W2, W3)."""

    caches = cache_dir(root)
    return {
        "TRITON_CACHE_DIR": str(caches / "triton"),
        "TORCHINDUCTOR_CACHE_DIR": str(caches / "inductor"),
        "UNSLOTH_COMPILE_LOCATION": str(caches / "unsloth"),
        "HF_HOME": str(caches / "hf"),
        "HF_HUB_ENABLE_HF_TRANSFER": "0",
        "TOKENIZERS_PARALLELISM": "false",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        # Qwen3.5's hybrid layers: the padded path (no varlen kernels on Windows); a fixed
        # cross-entropy budget so a resumed run doesn't stop on its first step.
        "UNSLOTH_EXPERIMENTAL_HYBRID_PACKING": "0",
        "UNSLOTH_CE_LOSS_TARGET_GB": "1",
    }


# ── Installing ──────────────────────────────────────────────────────────────


_PYTHON_NAME = "python.exe" if os.name == "nt" else "bin/python3"


def site_packages(home: Path) -> Path:
    """The engine interpreter's own site-packages (its ``platlib``)."""

    return home / ("Lib/site-packages" if os.name == "nt" else "lib/python3.12/site-packages")


def extract_uv(wheel: Path, target: Path) -> Path:
    """The ``uv`` program from its verified wheel (nothing else from it)."""

    executable = "uv.exe" if os.name == "nt" else "uv"
    with zipfile.ZipFile(wheel) as archive:
        members = [item for item in archive.infolist()
                   if not item.is_dir() and item.filename.endswith(f".data/scripts/{executable}")
                   and item.filename.count("/") == 2]
        if len(members) != 1:
            raise EngineError("install_failed")
        target.mkdir(parents=True, exist_ok=True)
        path = target / executable
        with archive.open(members[0]) as source, open(path, "wb") as output:
            shutil.copyfileobj(source, output, 1024 * 1024)
    if os.name != "nt":
        os.chmod(path, 0o755)
    return path


@dataclass
class Progress:
    """Where an install is: ``phase`` and bytes downloaded of the whole download."""

    phase: str = "engine"
    done: int = 0
    total: int = 0


@dataclass
class EngineInstaller:
    """Installs one engine (the person agreed to the download).

    ``uv``: a command prefix to use instead of the lock's pinned uv (tests run a fake one).
    ``python_source``: an existing standalone CPython folder to copy instead of downloading
    one (the opt-in end-to-end test and the GPU check reuse one already on the computer).
    ``uv_cache``: a uv cache that already holds every wheel (the end-to-end test reuses L0d's),
    so the engine is built offline from it and nothing is downloaded.
    """

    plan: EnginePlan
    root: Path | None = None
    fetcher: fetch.Fetcher = field(default_factory=fetch.Fetcher)
    uv: Sequence[str] | None = None
    python_source: Path | None = None
    uv_cache: Path | None = None
    runner: Callable[..., Run] = run_owned
    run_check: bool = True
    on_progress: Callable[[Progress], None] = lambda _progress: None

    # ── Downloads ───────────────────────────────────────────────────────

    def files(self) -> list[LockedWheel]:
        """What downloads: uv first (unless one is given), then every wheel."""

        if self.uv_cache is not None:
            return []
        return ([] if self.uv is not None else [self.plan.uv]) + list(self.plan.wheels)

    def pinned(self, wheel: LockedWheel, check: Callable[[], None] = lambda: None) -> fetch.Pinned:
        size = wheel.known_size
        item = fetch.Pinned(wheel.url, wheel.filename, wheel.sha256, size)
        if size is None:
            item = fetch.Pinned(wheel.url, wheel.filename, wheel.sha256, self.fetcher.size(item, check))
        return item

    def download_total(self, check: Callable[[], None] = lambda: None) -> int:
        return sum(self.pinned(wheel, check).size or 0 for wheel in self.files())

    def downloaded(self) -> int:
        folder = downloads_dir(self.root)
        return sum(fetch.on_disk(folder, fetch.Pinned(w.url, w.filename, w.sha256, w.known_size))
                   for w in self.files())

    def installed_estimate(self) -> int:
        return int(self.plan.download_bytes() * INSTALLED_FACTOR) + INSTALLED_EXTRA_BYTES

    def _download(self, progress: Progress, check: Callable[[], None]) -> dict[str, Path]:
        folder = downloads_dir(self.root)
        folder.mkdir(parents=True, exist_ok=True)
        items = [self.pinned(wheel, check) for wheel in self.files()]
        progress.total = progress.total or sum(item.size or 0 for item in items)
        before = {item.name: fetch.on_disk(folder, item) for item in items}
        progress.done = sum(before.values())
        self.on_progress(progress)
        paths: dict[str, Path] = {}
        for item in items:
            base = progress.done - before[item.name]

            def moved(current: int, base: int = base) -> None:
                progress.done = base + current
                self.on_progress(progress)

            try:
                paths[item.name] = self.fetcher.fetch(item, folder, progress=moved, check=check)
            except fetch.Stopped:
                raise
            except fetch.FetchError as error:
                raise EngineError(error.code) from error
            before[item.name] = item.size or paths[item.name].stat().st_size
            progress.done = base + before[item.name]
        return paths

    # ── Build ───────────────────────────────────────────────────────────

    def _uv_command(self, staging: Path, paths: dict[str, Path]) -> list[str]:
        if self.uv is not None:
            return list(self.uv)
        wheel = paths.get(self.plan.uv.filename)
        if wheel is None:
            raise EngineError("install_failed")
        return [str(extract_uv(wheel, staging / "tools"))]

    def _python(self, uv: list[str], staging: Path, env: dict[str, str], check: Callable[[], None]) -> Path:
        """The engine's own interpreter, at ``<build>/py`` (a short path: Windows' 260 characters)."""

        target = staging / "python"
        if self.python_source is not None:
            source = Path(self.python_source)
            shutil.copytree(source, target / source.name, symlinks=False)
        else:
            result = self.runner([*uv, "python", "install", self.plan.python_version, "--install-dir", str(target),
                                  "--no-bin", "--no-registry"],
                                 cwd=staging / "work", env=env, timeout=UV_TIMEOUT_SECONDS, check=check)
            if result.code != 0:
                logger.warning("uv couldn't install the engine's Python: %s", result.stderr[-400:])
                raise EngineError("install_failed")
        found = sorted(path for path in target.glob(f"*/{_PYTHON_NAME}") if path.is_file())
        if len(found) != 1:
            raise EngineError("install_failed")
        home = staging / "py"
        fetch.rename(found[0].parents[len(Path(_PYTHON_NAME).parts) - 1], home)
        _remove_tree(target)
        return home / _PYTHON_NAME

    def _packages(self, uv: list[str], python: Path, staging: Path, env: dict[str, str],
                  check: Callable[[], None]) -> Path:
        work = staging / "work"
        site = site_packages(staging / "py")
        site.mkdir(parents=True, exist_ok=True)
        if self.uv_cache is not None:
            # Built offline from a uv cache that already holds every wheel (the end-to-end test),
            # into a scratch environment whose packages then move into the interpreter's own.
            project = work / "project"
            project.mkdir(parents=True, exist_ok=True)
            shutil.copy2(lockfile.ENGINE_DIR / "pyproject.toml", project / "pyproject.toml")
            shutil.copy2(lockfile.LOCK_FILE, project / "uv.lock")
            scratch = work / "env"
            env = {**env, "UV_CACHE_DIR": str(self.uv_cache), "UV_PROJECT_ENVIRONMENT": str(scratch)}
            argv = [*uv, "sync", "--locked", "--offline", "--no-build", "--no-install-project", "--no-default-groups",
                    "--extra", self.plan.variant, "--python", str(python), "--link-mode", "copy",
                    "--compile-bytecode", "--project", str(project)]
            result = self.runner(argv, cwd=work, env=env, timeout=UV_TIMEOUT_SECONDS, check=check)
            if result.code == 0:
                built = scratch / ("Lib/site-packages" if os.name == "nt" else "lib/python3.12/site-packages")
                for entry in built.iterdir() if _plain_dir(built) else ():
                    if entry.name not in ("_virtualenv.pth", "_virtualenv.py"):
                        fetch.rename(entry, site / entry.name)
        else:
            requirements = work / "requirements.txt"
            requirements.parent.mkdir(parents=True, exist_ok=True)
            requirements.write_text(self.plan.requirements(), encoding="utf-8", newline="\n")
            env = {**env, "UV_CACHE_DIR": str(work / "uv-cache")}
            argv = [*uv, "pip", "install", "--python", str(python), "--target", str(site), "--offline", "--no-index",
                    "--find-links", str(downloads_dir(self.root)), "--require-hashes", "--no-deps", "--no-build",
                    "--compile-bytecode", "--requirement", str(requirements)]
            result = self.runner(argv, cwd=work, env=env, timeout=UV_TIMEOUT_SECONDS, check=check)
        if result.code != 0:
            logger.warning("uv couldn't install the training engine: %s", result.stderr[-600:])
            raise EngineError("install_failed")
        if not _plain_dir(site):
            raise EngineError("install_failed")
        return site

    def _verify(self, site: Path, check: Callable[[], None]) -> dict[str, str]:
        found = _dist_versions(site)
        for bundled in ("pip", "uv"):  # an interpreter's own pip, never run; never the engine's
            found.pop(bundled, None)
        expected = {wheel.name: wheel.version for wheel in self.plan.wheels}
        if not _versions_match(found, expected):
            missing = sorted(set(expected) - set(found))[:5]
            extra = sorted(set(found) - set(expected))[:5]
            logger.warning("The training engine's packages aren't the plan's (missing %s, extra %s)", missing, extra)
            raise EngineError("install_failed")
        problems = check_files(site, check)
        if problems:
            logger.warning("The training engine is missing %d files (%s)", len(problems), problems[:3])
            raise EngineError("install_failed")
        return expected

    def install(self, check: Callable[[], None] = lambda: None) -> EngineStatus:
        """Download what is missing, build, check and publish. Raises :class:`EngineError`."""

        with _install_lock:
            base = engine_dir(self.root)
            base.mkdir(parents=True, exist_ok=True)
            progress = Progress("engine")
            paths = self._download(progress, check)
            for leftover in base.glob(".staging-*"):
                _remove_tree(leftover)
            identifier = uuid.uuid4().hex[:8]
            staging = base / f".staging-{identifier}"
            staging.mkdir()
            try:
                progress.phase = "installing"
                self.on_progress(progress)
                env = _base_environment(staging / "work")
                uv = self._uv_command(staging, paths)
                python = self._python(uv, staging, env, check)
                site = self._packages(uv, python, staging, env, check)
                packages = self._verify(site, check)
                report: dict[str, Any] = {}
                if self.run_check:
                    progress.phase = "checking"
                    self.on_progress(progress)
                    report = engine_check(python, site, cwd=staging / "work" / "check", check=check,
                                          runner=self.runner)
                _remove_tree(staging / "work")
                _remove_tree(staging / "tools")
                record = {
                    "format": FORMAT,
                    "variant": self.plan.variant,
                    "plan": self.plan.identity,
                    "lock_sha256": lockfile.lock_sha256(),
                    "python": python.relative_to(staging).as_posix(),
                    "python_version": self.plan.python_version,
                    "site": site.relative_to(staging).as_posix(),
                    "packages": packages,
                    "torch_cuda": report.get("torch_cuda"),
                    "installed_at": time.time(),
                }
                record["bytes"] = folder_bytes(staging)
                _write_json(staging / RECORD_FILE, record)
                check()
                final = base / identifier
                fetch.rename(staging, final)
            except BaseException:
                _remove_tree(staging)
                raise
            previous = _read_json(base / INSTALLED_FILE)
            _write_json(base / INSTALLED_FILE, {"format": FORMAT, "folder": identifier, "variant": self.plan.variant})
            forget_status()
            if previous and previous.get("folder") and previous.get("folder") != identifier:
                old = base / str(previous["folder"])
                if _plain_dir(old) and old.parent == base:
                    _remove_tree(old)
            _remove_tree(downloads_dir(self.root))
            logger.info("Learning installed its training engine (%s)", self.plan.variant)
            return status(self.root, refresh=True)


_install_lock = threading.RLock()


def remove(root: Path | None = None) -> int:
    """Remove the engine (and its downloads and caches). Returns the bytes freed."""

    with _install_lock:
        base = engine_dir(root)
        caches = cache_dir(root)
        freed = folder_bytes(base) + folder_bytes(caches)
        (base / INSTALLED_FILE).unlink(missing_ok=True)
        forget_status()
        for folder in (base, caches):
            if os.path.lexists(folder):
                doomed = folder.with_name(f".removing-{uuid.uuid4().hex[:8]}")
                try:
                    fetch.rename(folder, doomed, seconds=5.0)
                except OSError:
                    doomed = folder
                _remove_tree(doomed)
        _tidy(root)
        logger.info("Learning removed its training engine")
        return freed


def _tidy(root: Path | None) -> None:
    """Remove ``runtimes/learning`` once nothing is left in it."""

    base = learning_runtimes(root)
    try:
        for leftover in base.glob(".removing-*"):
            _remove_tree(leftover)
        if base.is_dir() and not any(base.iterdir()):
            base.rmdir()
    except OSError:
        pass


def plan_for(variant: str) -> EnginePlan:
    return lockfile.engine_plan(variant)


# ── The engine the training worker runs (L2a's Engine seam) ─────────────────


class LocalEngine:
    """The installed engine and starting model, as the training worker's ``Engine``."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root

    def ready(self) -> bool:
        from row_bot import learning
        from row_bot.learning.training import weights

        if not status(self._root).installed:
            return False
        model = weights.MODELS.get(learning.read_settings(self._root).starting_model)
        return model is not None and weights.status(model, self._root).state == "downloaded"

    def command(self, job: Any) -> Any:
        from row_bot import learning
        from row_bot.learning.training import weights
        from row_bot.learning.training.worker import PROTOCOL_PATH, EngineCommand, WorkerError

        found = status(self._root)
        model = weights.MODELS.get(learning.read_settings(self._root).starting_model)
        if not found.installed or found.python is None or found.site is None or model is None:
            raise WorkerError("engine_missing")
        weights_status = weights.status(model, self._root)
        if weights_status.state != "downloaded" or weights_status.folder is None:
            raise WorkerError("engine_missing")
        for folder in ("triton", "inductor", "unsloth", "hf"):
            (cache_dir(self._root) / folder).mkdir(parents=True, exist_ok=True)
        return EngineCommand(
            argv=(str(found.python), "-I", "-S", str(ENTRY), "train", "--site", str(found.site),
                  "--protocol", str(PROTOCOL_PATH), "--model", str(weights_status.folder)),
            env=engine_variables(self._root),
            name="unsloth",
        )


def describe_size(path_bytes: int) -> str:
    return f"{path_bytes / 1e9:.1f} GB"


def iter_generations(root: Path | None = None) -> Iterable[Path]:
    base = engine_dir(root)
    return (item for item in base.iterdir() if _plain_dir(item) and not item.name.startswith(".")
            and item.name != DOWNLOADS) if base.is_dir() else iter(())


__all__ = [
    "ENTRY",
    "EngineError",
    "EngineInstaller",
    "EngineStatus",
    "LocalEngine",
    "Progress",
    "Run",
    "cache_dir",
    "check_files",
    "downloads_dir",
    "engine_check",
    "engine_dir",
    "engine_variables",
    "extract_uv",
    "folder_bytes",
    "forget_status",
    "learning_runtimes",
    "plan_for",
    "remove",
    "run_owned",
    "status",
]
