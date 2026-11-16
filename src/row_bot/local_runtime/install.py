"""Download, check and publish the llama.cpp build and a model; Remove frees them again.

**Nothing half-installed is ever used.** Each file is checked against its pin (``fetch.py``). A build
is unpacked into a staging folder, traversal-safe; its ``llama-server`` must answer ``--version`` on
this computer (an antivirus or App Control that blocks it stops the install there); then
``installed.json`` is written last and the staging folder is renamed into place in one step. A model
file is renamed into place only after its SHA-256 matched, and its ``model.json`` is written last. A
build or model without its record is not installed, whatever else is on disk.

**Started only by the person.** :func:`start` runs only from the API command the Download button
sends, after the size it showed was checked against what is left to download; it first checks the
free disk space. Stop keeps what arrived (it resumes later).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import subprocess
import tarfile
import threading
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from row_bot.local_runtime import builds, models
from row_bot.local_runtime.fetch import Fetcher, FetchError, Pinned, Stopped, disk_full, on_disk, rename

logger = logging.getLogger(__name__)

RUNTIME_FOLDER = "llamacpp"
MODELS_FOLDER = "local_models"
DOWNLOADS = "downloads"
RECORD = "installed.json"
MODEL_RECORD = "model.json"
FORMAT = 1
VERSION_SECONDS = 60.0
# Unpacked, a build takes about this many times its archives (the libraries are compressed).
UNPACKED_FACTOR = 2.5
# Free space kept on the disk after a download.
DISK_MARGIN_BYTES = 1024**3


class InstallError(RuntimeError):
    """``code``: ``unavailable``, ``out_of_disk``, ``mismatch``, ``network``, ``refused``, ``blocked``
    (the program didn't run here), ``unpack``, ``stopped``, ``busy``, ``unknown_model``, ``size_changed``."""

    def __init__(self, code: str, **facts: Any) -> None:
        self.code = code
        self.facts = facts
        super().__init__(code)


# ── Where things live ───────────────────────────────────────────────────────


def data_dir(root: Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir(create=False)


def runtime_dir(root: Path | None = None) -> Path:
    return data_dir(root) / "runtimes" / RUNTIME_FOLDER


def models_dir(root: Path | None = None) -> Path:
    return data_dir(root) / MODELS_FOLDER


def downloads_dir(root: Path | None = None) -> Path:
    return runtime_dir(root) / DOWNLOADS


def build_dir(build: builds.Build, root: Path | None = None) -> Path:
    return runtime_dir(root) / build.id


def model_dir(model: models.Model, root: Path | None = None) -> Path:
    return models_dir(root) / model.id


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, value: Any) -> None:
    from row_bot.integrations.safe import write_atomic

    write_atomic(path, json.dumps(value, indent=1, sort_keys=True))


# ── What is installed ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class InstalledBuild:
    folder: Path
    release: str
    acceleration: str
    executable: Path

    @property
    def current(self) -> bool:
        return self.release == builds.RELEASE


def installed_builds(root: Path | None = None) -> list[InstalledBuild]:
    """Every build with its record (the current release first)."""

    found = []
    folder = runtime_dir(root)
    try:
        entries = sorted(folder.iterdir()) if folder.is_dir() else []
    except OSError:
        entries = []
    for entry in entries:
        record = _read_json(entry / RECORD)
        if not isinstance(record, dict) or record.get("format") != FORMAT:
            continue
        executable = entry / str(record.get("executable") or "")
        if executable.parent != entry or not executable.is_file():
            continue
        found.append(InstalledBuild(entry, str(record.get("release") or ""), str(record.get("acceleration") or ""),
                                    executable))
    return sorted(found, key=lambda item: (not item.current, item.folder.name))


def installed_build(root: Path | None = None) -> InstalledBuild | None:
    found = installed_builds(root)
    return found[0] if found else None


def model_installed(model: models.Model, root: Path | None = None) -> bool:
    record = _read_json(model_dir(model, root) / MODEL_RECORD)
    file = model_dir(model, root) / model.file
    return (isinstance(record, dict) and record.get("sha256") == model.sha256 and file.is_file()
            and file.stat().st_size == model.size)


def installed_models(root: Path | None = None) -> list[models.Model]:
    return [model for model in models.CATALOG if model_installed(model, root)]


def model_path(model: models.Model, root: Path | None = None) -> Path:
    return model_dir(model, root) / model.file


def _build_items(build: builds.Build) -> list[Pinned]:
    return [Pinned(asset.url, asset.name, asset.size, asset.sha256) for asset in build.assets]


def _model_item(model: models.Model) -> Pinned:
    return Pinned(model.url, model.file, model.size, model.sha256)


def remaining_bytes(build: builds.Build | None, model: models.Model | None, root: Path | None = None) -> int:
    """What is left to download for ``build`` and ``model`` (what arrived before counts as done)."""

    total = 0
    current = installed_build(root)
    if build is not None and not (current is not None and current.current and current.acceleration == build.acceleration):
        total += sum(item.size - on_disk(downloads_dir(root), item) for item in _build_items(build))
    if model is not None and not model_installed(model, root):
        total += model.size - on_disk(model_dir(model, root), _model_item(model))
    return total


def folder_bytes(folder: Path) -> int:
    total = 0
    for base, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += (Path(base) / name).lstat().st_size
            except OSError:
                continue
    return total


def disk_bytes(root: Path | None = None) -> int:
    """Disk the runtime and its models take now."""

    return folder_bytes(runtime_dir(root)) + folder_bytes(models_dir(root))


# ── Unpacking ───────────────────────────────────────────────────────────────


def _inside(base: Path, name: str) -> Path:
    target = (base / name).resolve()
    if target != base.resolve() and base.resolve() not in target.parents:
        raise InstallError("unpack")
    return target


def unpack(archive: Path, into: Path) -> None:
    """Unpack a release archive, refusing any entry that would land outside ``into``."""

    into.mkdir(parents=True, exist_ok=True)
    try:
        if archive.name.endswith(".zip"):
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    target = _inside(into, member.filename)
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(member) as source, open(target, "wb") as sink:
                        shutil.copyfileobj(source, sink)
        else:
            with tarfile.open(archive, "r:gz") as bundle:
                # The "data" filter refuses absolute paths, links leaving the folder and device files.
                bundle.extractall(into, filter="data")
    except InstallError:
        raise
    except (OSError, zipfile.BadZipFile, tarfile.TarError) as error:
        if isinstance(error, OSError) and disk_full(error):
            raise InstallError("out_of_disk") from error
        raise InstallError("unpack") from error


def _contents(folder: Path) -> Path:
    """An archive's files: inside its one top folder when it has one (the macOS and Linux tarballs)."""

    entries = list(folder.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return folder


def _merge(source: Path, target: Path) -> None:
    for entry in source.iterdir():
        destination = target / entry.name
        if destination.exists():
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()
        shutil.move(str(entry), str(destination))


def _executable_bits(folder: Path) -> None:
    if os.name == "nt":
        return
    for entry in folder.iterdir():
        if entry.is_file() and not entry.is_symlink() and (entry.name.startswith("llama-") or entry.suffix in
                                                            {".so", ".dylib"} or ".so." in entry.name):
            entry.chmod(entry.stat().st_mode | stat.S_IXUSR | stat.S_IRUSR)


def _default_command(executable: Path) -> list[str]:
    return [str(executable)]


def server_environment(folder: Path) -> dict[str, str]:
    """A process environment for ``llama-server``: nothing of the person's (no keys, no ``HF_TOKEN``, no
    ``LLAMA_*`` settings that could turn on its tools or downloads), its own home, caches and temporary
    folder inside the build, and on Linux the build's libraries on the loader's path."""

    keep = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "SYSTEMDRIVE", "NUMBER_OF_PROCESSORS",
            "PROCESSOR_ARCHITECTURE", "LANG", "LC_ALL", "CUDA_VISIBLE_DEVICES")
    environment = {key: value for key, value in os.environ.items() if key.upper() in keep}
    home = Path(folder) / "home"
    temp = home / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    environment.update({
        "HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home), "LOCALAPPDATA": str(home),
        "XDG_CACHE_HOME": str(temp), "TEMP": str(temp), "TMP": str(temp), "TMPDIR": str(temp),
        "LLAMA_CACHE": str(home / "cache"),
    })
    if os.name != "nt":
        environment["LD_LIBRARY_PATH"] = str(folder)
    return environment


def version_check(executable: Path, *, command: Callable[[Path], list[str]] = _default_command,
                  seconds: float = VERSION_SECONDS) -> bool:
    """Whether the build runs on this computer: ``llama-server --version`` answers and exits cleanly."""

    options: dict[str, Any] = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        done = subprocess.run([*command(executable), "--version"], capture_output=True, text=True,
                              timeout=seconds, cwd=str(executable.parent), stdin=subprocess.DEVNULL,
                              env=server_environment(executable.parent), check=False, **options)
    except (OSError, subprocess.SubprocessError):
        logger.warning("The downloaded llama.cpp build didn't start here", exc_info=True)
        return False
    text = f"{done.stdout}\n{done.stderr}".lower()
    if done.returncode != 0 or "version" not in text:
        logger.warning("The downloaded llama.cpp build didn't answer its version check (exit %s)", done.returncode)
        return False
    return True


def publish_build(build: builds.Build, archives: Sequence[Path], root: Path | None = None, *,
                  command: Callable[[Path], list[str]] = _default_command) -> InstalledBuild:
    """Unpack ``archives`` (the build, then its CUDA runtime) into a staging folder, check that the
    program runs, write ``installed.json`` last and move the folder into place in one step."""

    base = runtime_dir(root)
    staging = base / f".staging-{build.id}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    files = staging / "build"
    files.mkdir(parents=True)
    try:
        for index, archive in enumerate(archives):
            raw = staging / f"raw-{index}"
            unpack(archive, raw)
            _merge(_contents(raw), files)
        executable = files / build.executable
        if not executable.is_file():
            raise InstallError("unpack")
        _executable_bits(files)
        if not version_check(executable, command=command):
            raise InstallError("blocked")
        _write_json(files / RECORD, {"format": FORMAT, "release": builds.RELEASE, "acceleration": build.acceleration,
                                     "platform": build.platform, "executable": build.executable,
                                     "assets": [asset.name for asset in build.assets], "licence": builds.LICENCE})
        target = build_dir(build, root)
        if target.exists():
            shutil.rmtree(target)
        rename(files, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    for older in installed_builds(root):
        if older.folder != target:
            shutil.rmtree(older.folder, ignore_errors=True)  # an older release or another acceleration
    found = installed_build(root)
    assert found is not None
    return found


def publish_model(model: models.Model, root: Path | None = None) -> None:
    _write_json(model_dir(model, root) / MODEL_RECORD, {
        "format": FORMAT, "id": model.id, "name": model.name, "source": model.source, "repo": model.repo,
        "revision": model.revision, "file": model.file, "sha256": model.sha256, "licence_id": model.licence_id,
    })


# ── The download job ────────────────────────────────────────────────────────


@dataclass
class Progress:
    model_id: str
    acceleration: str
    total: int
    done: int = 0
    phase: str = "downloading"  # downloading | checking | finished | stopped | failed
    reason: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)

    def view(self) -> dict[str, Any]:
        return {"model_id": self.model_id, "acceleration": self.acceleration, "total_bytes": self.total,
                "done_bytes": min(self.done, self.total), "phase": self.phase, "reason": self.reason,
                **({"facts": dict(self.facts)} if self.facts else {})}


class Installer:
    """One download at a time for this data folder (the API's Download and Stop)."""

    def __init__(self, root: Path | None = None, *, fetcher: Fetcher | None = None,
                 command: Callable[[Path], list[str]] = _default_command,
                 free_bytes: Callable[[Path], int] | None = None,
                 on_ready: Callable[[models.Model], None] | None = None) -> None:
        self.root = root
        self.fetcher = fetcher
        self.command = command
        self._free_bytes = free_bytes or (lambda path: shutil.disk_usage(path).free)
        self.on_ready = on_ready
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.progress: Progress | None = None

    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def plan(self, build: builds.Build, model: models.Model) -> int:
        return remaining_bytes(build, model, self.root)

    def start(self, build: builds.Build, model: models.Model, *, expected_bytes: int | None = None) -> Progress:
        """Start downloading ``build`` (unless it is installed) and ``model``. ``expected_bytes`` is the
        size the person agreed to; a different size now is refused (``size_changed``)."""

        with self._lock:
            if self.running():
                if self.progress is not None and self.progress.model_id == model.id:
                    return self.progress
                raise InstallError("busy")
            total = self.plan(build, model)
            if expected_bytes is not None and int(expected_bytes) != total:
                raise InstallError("size_changed", download_bytes=total)
            base = data_dir(self.root)
            base.mkdir(parents=True, exist_ok=True)
            build_needed = self._build_needed(build)
            need = total + (int(build.size * UNPACKED_FACTOR) if build_needed else 0) + DISK_MARGIN_BYTES
            free = int(self._free_bytes(base))
            if free < need:
                raise InstallError("out_of_disk", needed_bytes=need, free_bytes=free)
            self._stop.clear()
            self.progress = Progress(model.id, build.acceleration, total)
            self._thread = threading.Thread(target=self._run, args=(build, model, self.progress),
                                            name="row-bot-local-runtime-download", daemon=True)
            self._thread.start()
            return self.progress

    def stop(self) -> bool:
        """Stop the download; what arrived is kept. True when one was running."""

        with self._lock:
            running = self.running()
            self._stop.set()
            if self.fetcher is not None:
                self.fetcher.close()
        thread = self._thread
        if running and thread is not None:
            thread.join(timeout=30)
        return running

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _check(self) -> None:
        if self._stop.is_set():
            raise Stopped()

    def _build_needed(self, build: builds.Build) -> bool:
        current = installed_build(self.root)
        return not (current is not None and current.current and current.acceleration == build.acceleration)

    def _run(self, build: builds.Build, model: models.Model, progress: Progress) -> None:
        fetcher = self.fetcher or Fetcher()
        downloads = downloads_dir(self.root)
        try:
            base = 0
            if self._build_needed(build):
                archives = []
                for item in _build_items(build):
                    # Progress counts what is left to download: bytes that arrived before count as done.
                    offset = on_disk(downloads, item)

                    def advance(size: int, start: int = base, offset: int = offset) -> None:
                        progress.done = start + max(0, size - offset)

                    archives.append(fetcher.fetch(item, downloads, progress=advance, check=self._check))
                    base += item.size - offset
                    progress.done = base
                progress.phase = "checking"
                self._check()
                publish_build(build, archives, self.root, command=self.command)
                for archive in archives:
                    archive.unlink(missing_ok=True)
            if not model_installed(model, self.root):
                progress.phase = "downloading"
                item = _model_item(model)
                folder = model_dir(model, self.root)
                offset = on_disk(folder, item)

                def advance_model(size: int, start: int = base, offset: int = offset) -> None:
                    progress.done = start + max(0, size - offset)

                fetcher.fetch(item, folder, progress=advance_model, check=self._check)
                publish_model(model, self.root)
            progress.done = progress.total
            progress.phase = "finished"
            if self.on_ready is not None:
                try:
                    self.on_ready(model)
                except Exception:  # noqa: BLE001 - the files are installed; the endpoint is saved again later
                    logger.warning("The local model was installed but its endpoint wasn't saved", exc_info=True)
            logger.info("Row-Bot installed %s to run on this computer", model.id)
        except Stopped:
            progress.phase = "stopped"
        except FetchError as error:
            progress.phase, progress.reason = "failed", error.code
            logger.warning("A local-model download failed (%s)", error.code)
        except InstallError as error:
            progress.phase, progress.reason, progress.facts = "failed", error.code, dict(error.facts)
            logger.warning("Installing the local runtime failed (%s)", error.code)
        except OSError as error:
            progress.phase, progress.reason = "failed", "out_of_disk" if disk_full(error) else "unpack"
            logger.warning("Installing the local runtime failed", exc_info=True)


def remove(root: Path | None = None, *, model_id: str | None = None) -> int:
    """Delete one model, or (without ``model_id``) the build, its partial downloads and every model.
    Returns the bytes freed. The caller stops the server first."""

    if model_id is not None:
        model = models.get(model_id)
        if model is None:
            raise InstallError("unknown_model")
        folder = model_dir(model, root)
        freed = folder_bytes(folder)
        shutil.rmtree(folder, ignore_errors=True)
        return freed
    freed = disk_bytes(root)
    for folder in (runtime_dir(root), models_dir(root)):
        shutil.rmtree(folder, ignore_errors=True)
    return freed


__all__ = [
    "InstallError",
    "InstalledBuild",
    "Installer",
    "Progress",
    "build_dir",
    "disk_bytes",
    "downloads_dir",
    "installed_build",
    "installed_builds",
    "installed_models",
    "model_dir",
    "model_installed",
    "model_path",
    "models_dir",
    "publish_build",
    "remaining_bytes",
    "remove",
    "runtime_dir",
    "server_environment",
    "unpack",
    "version_check",
]
