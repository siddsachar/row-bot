"""Streaming speech-to-text models for Row-Bot Voice, picked for this computer (VD3).

A small pinned catalog: each model is one official sherpa-onnx release archive on GitHub with its
exact size and SHA-256 (``docs/VOICE_ENGINE.md`` has the review). :func:`recommend` picks one for
this computer from what ``local_runtime.hardware`` reads; Whisper stays the fallback choice.

**Nothing downloads unless the person presses Download.** :class:`Installer` runs only from the
API command the button sends, with the size it showed; a different size now is refused. The archive
lands in ``<data>/models/speech/downloads/`` (resuming after a Stop), is checked against its pin,
and only the model's listed files are unpacked, traversal-safe, into a staging folder that is
renamed into ``<data>/models/speech/<id>/`` after ``model.json`` is written last. A model folder
without its record is not installed. Remove deletes the folder.

Importing this module reads nothing and creates no file.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tarfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from row_bot.local_runtime.fetch import Fetcher, FetchError, Pinned, Stopped, disk_full, on_disk, rename

logger = logging.getLogger(__name__)

RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
FOLDER = ("models", "speech")
DOWNLOADS = "downloads"
RECORD = "model.json"
FORMAT = 1
# Free space kept on the disk after a download and its unpacking.
DISK_MARGIN_BYTES = 256 * 1024**2
# The Talk engine id that means Whisper (the fallback; Settings' saved talk_model).
WHISPER_ENGINE = "local-whisper"

Family = Literal["transducer", "moonshine"]
Reason = Literal["gpu", "cpu", "mac", "no_gpu_runtime"]


@dataclass(frozen=True)
class SpeechModel:
    """One pinned speech-to-text model: its archive, the files Row-Bot keeps and what it is."""

    id: str
    name: str
    family: Family
    archive: str
    archive_size: int
    archive_sha256: str
    # The archive's top folder, and the files kept from it with their sizes.
    top: str
    files: tuple[tuple[str, int], ...]
    # The model's language (a BCP 47 tag), or None when it detects the language itself.
    language: str | None
    licence: str
    licence_url: str
    source: str
    # Where this model runs best: a graphics card, or the processor (any computer, a Mac included).
    runs_best_on: Literal["gpu", "cpu"]

    @property
    def engine_id(self) -> str:
        """The saved Talk engine id (``voice_runtime_settings.json``'s ``talk_model``)."""

        return f"local-{self.id}"

    @property
    def pinned(self) -> Pinned:
        return Pinned(f"{RELEASES}/{self.archive}", self.archive, self.archive_size, self.archive_sha256)

    @property
    def installed_bytes(self) -> int:
        return sum(size for _name, size in self.files)

    def file(self, name: str) -> str:
        """The name of the kept file that ends with ``name`` (``encoder``, ``tokens.txt``)."""

        return next(item for item, _size in self.files if item.startswith(name))


MOONSHINE_BASE_EN = SpeechModel(
    id="moonshine-base-en",
    name="Moonshine Base",
    family="moonshine",
    archive="sherpa-onnx-moonshine-base-en-quantized-2026-02-27.tar.bz2",
    archive_size=111_266_225,
    archive_sha256="43232c1d13013d37317163baec3135bd771a186a4356f28c889bab453bb0e891",
    top="sherpa-onnx-moonshine-base-en-quantized-2026-02-27",
    files=(("encoder_model.ort", 31_326_816), ("decoder_model_merged.ort", 109_424_400),
           ("tokens.txt", 549_350), ("LICENSE", 13_344)),
    language="en",
    licence="MIT",
    licence_url="https://github.com/moonshine-ai/moonshine/blob/main/LICENSE",
    source="https://github.com/moonshine-ai/moonshine",
    runs_best_on="cpu",
)

NEMOTRON_STREAMING_EN = SpeechModel(
    id="nemotron-streaming-en",
    name="Nemotron Speech Streaming",
    family="transducer",
    archive="sherpa-onnx-nemotron-speech-streaming-en-0.6b-160ms-int8-2026-04-25.tar.bz2",
    archive_size=463_945_198,
    archive_sha256="0ae73a41cd51599dc7cac9ac083d9d35de53d762ca45923505fde47a3751814b",
    top="sherpa-onnx-nemotron-speech-streaming-en-0.6b-160ms-int8-2026-04-25",
    files=(("encoder.int8.onnx", 652_916_849), ("decoder.int8.onnx", 7_257_753),
           ("joiner.int8.onnx", 1_735_862), ("tokens.txt", 8_952), ("README.md", 158)),
    language="en",
    licence="NVIDIA Open Model License",
    licence_url="https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/",
    source="https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b",
    runs_best_on="gpu",
)

# The processor pick first: it is the recommendation wherever no graphics card can run Nemotron.
CATALOG: tuple[SpeechModel, ...] = (MOONSHINE_BASE_EN, NEMOTRON_STREAMING_EN)
_BY_ID = {model.id: model for model in CATALOG}
_BY_ENGINE = {model.engine_id: model for model in CATALOG}


def get(model_id: str) -> SpeechModel | None:
    return _BY_ID.get(str(model_id or ""))


def by_engine(engine_id: str) -> SpeechModel | None:
    """The catalog model a saved Talk engine id names, or None (Whisper, SenseVoice)."""

    return _BY_ENGINE.get(str(engine_id or ""))


def runtime_available() -> bool:
    """Whether the speech runtime (sherpa-onnx, in the ``voice`` extra) is installed. Imports nothing."""

    import importlib.util

    try:
        return importlib.util.find_spec("sherpa_onnx") is not None
    except (ImportError, ValueError):
        return False


def cuda_runtime() -> bool:
    """Whether the installed sherpa-onnx build can use an NVIDIA graphics card (a CUDA build ships
    ``onnxruntime_providers_cuda``; the PyPI build Row-Bot locks is processor-only). Imports nothing."""

    import importlib.util

    try:
        spec = importlib.util.find_spec("sherpa_onnx")
    except (ImportError, ValueError):
        return False
    if spec is None or not spec.submodule_search_locations:
        return False
    for location in spec.submodule_search_locations:
        library = Path(location) / "lib"
        try:
            if any(entry.name.startswith(("onnxruntime_providers_cuda", "libonnxruntime_providers_cuda"))
                   for entry in library.iterdir()):
                return True
        except OSError:
            continue
    return False


def recommend(hardware: Any, *, gpu_runtime: bool) -> tuple[SpeechModel, Reason]:
    """The model for this computer and why: Nemotron on an NVIDIA card the speech runtime can use,
    Moonshine on the processor everywhere else (a Mac included)."""

    from row_bot.local_runtime import builds
    from row_bot.local_runtime.hardware import NVIDIA

    gpu = getattr(hardware, "gpu", None)
    nvidia = (gpu is not None and gpu.vendor == NVIDIA
              and hardware.acceleration in (builds.CUDA_12, builds.CUDA_13))
    if nvidia and gpu_runtime:
        return NEMOTRON_STREAMING_EN, "gpu"
    if getattr(hardware, "apple_silicon", False):
        return MOONSHINE_BASE_EN, "mac"
    return MOONSHINE_BASE_EN, "no_gpu_runtime" if nvidia else "cpu"


# ── Where things live ───────────────────────────────────────────────────────


def models_root(root: Path | None = None) -> Path:
    if root is not None:
        return Path(root) / Path(*FOLDER)
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir(create=False) / Path(*FOLDER)


def model_dir(model: SpeechModel, root: Path | None = None) -> Path:
    return models_root(root) / model.id


def downloads_dir(root: Path | None = None) -> Path:
    return models_root(root) / DOWNLOADS


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def installed(model: SpeechModel, root: Path | None = None) -> bool:
    """The model's record names its pin and every kept file is there at its size."""

    folder = model_dir(model, root)
    record = _read_json(folder / RECORD)
    if not isinstance(record, dict) or record.get("format") != FORMAT or record.get("sha256") != model.archive_sha256:
        return False
    for name, size in model.files:
        path = folder / name
        try:
            if not path.is_file() or path.stat().st_size != size:
                return False
        except OSError:
            return False
    return True


def remaining_bytes(model: SpeechModel, root: Path | None = None) -> int:
    """What pressing Download would fetch now (what arrived before counts as done); 0 when installed."""

    if installed(model, root):
        return 0
    return model.archive_size - on_disk(downloads_dir(root), model.pinned)


def disk_bytes(root: Path | None = None) -> int:
    """Disk the speech models and their partial downloads take now."""

    return disk_bytes_of(models_root(root))


# ── Unpacking ───────────────────────────────────────────────────────────────


class InstallError(RuntimeError):
    """``code``: ``out_of_disk``, ``mismatch``, ``network``, ``refused``, ``unpack``, ``busy``,
    ``size_changed``, ``unavailable``."""

    def __init__(self, code: str, **facts: Any) -> None:
        self.code = code
        self.facts = facts
        super().__init__(code)


def unpack(model: SpeechModel, archive: Path, into: Path, check: Callable[[], None] = lambda: None) -> None:
    """Copy only the model's listed files out of its archive into ``into``: no other entry, no link,
    no path outside it. Each must arrive whole at its pinned size."""

    wanted = {f"{model.top}/{name}": (name, size) for name, size in model.files}
    found: set[str] = set()
    into.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive, "r:bz2") as bundle:
            for member in bundle:
                check()
                entry = wanted.get(member.name)
                if entry is None or not member.isfile():
                    continue
                name, size = entry
                if member.size != size:
                    raise InstallError("unpack")
                source = bundle.extractfile(member)
                if source is None:
                    raise InstallError("unpack")
                with source, open(into / name, "wb") as sink:
                    while block := source.read(1024 * 1024):
                        sink.write(block)
                        check()
                if (into / name).stat().st_size != size:
                    raise InstallError("unpack")
                found.add(member.name)
    except (InstallError, Stopped):
        raise
    except OSError as error:
        raise InstallError("out_of_disk" if disk_full(error) else "unpack") from error
    except (tarfile.TarError, EOFError, ValueError) as error:
        raise InstallError("unpack") from error
    if found != set(wanted):
        raise InstallError("unpack")


def publish(model: SpeechModel, staging: Path, root: Path | None = None) -> None:
    """Write the record last, then move the folder into place in one step."""

    from row_bot.integrations.safe import write_atomic

    write_atomic(staging / RECORD, json.dumps({
        "format": FORMAT, "id": model.id, "name": model.name, "archive": model.archive,
        "sha256": model.archive_sha256, "licence": model.licence, "source": model.source,
        "files": [name for name, _size in model.files],
    }, indent=1, sort_keys=True))
    target = model_dir(model, root)
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    rename(staging, target)


# ── The download job ────────────────────────────────────────────────────────


@dataclass
class Progress:
    model_id: str
    total: int
    done: int = 0
    phase: str = "downloading"  # downloading | checking | finished | stopped | failed
    reason: str | None = None
    facts: dict[str, int] = field(default_factory=dict)

    def view(self) -> dict[str, Any]:
        return {"model_id": self.model_id, "total_bytes": self.total, "done_bytes": min(self.done, self.total),
                "phase": self.phase, "reason": self.reason, **({"facts": dict(self.facts)} if self.facts else {})}


class Installer:
    """One speech-model download at a time for this data folder (the API's Download and Stop)."""

    def __init__(self, root: Path | None = None, *, fetcher: Fetcher | None = None,
                 free_bytes: Callable[[Path], int] | None = None,
                 on_ready: Callable[[SpeechModel], None] | None = None) -> None:
        self.root = root
        self.fetcher = fetcher
        self._free_bytes = free_bytes or (lambda path: shutil.disk_usage(path).free)
        self.on_ready = on_ready
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.progress: Progress | None = None

    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self, model: SpeechModel, *, expected_bytes: int) -> Progress:
        """Start downloading ``model``. ``expected_bytes`` is the size the person agreed to; a
        different size now is refused (``size_changed``)."""

        with self._lock:
            if self.running():
                if self.progress is not None and self.progress.model_id == model.id:
                    return self.progress
                raise InstallError("busy")
            total = remaining_bytes(model, self.root)
            if int(expected_bytes) != total:
                raise InstallError("size_changed", download_bytes=total)
            base = models_root(self.root)
            probe = base
            while not probe.exists() and probe.parent != probe:
                probe = probe.parent
            need = total + model.installed_bytes + DISK_MARGIN_BYTES
            free = int(self._free_bytes(probe))
            if free < need:
                raise InstallError("out_of_disk", needed_bytes=need, free_bytes=free)
            self._stop.clear()
            self.progress = Progress(model.id, total)
            self._thread = threading.Thread(target=self._run, args=(model, self.progress),
                                            name="row-bot-speech-model-download", daemon=True)
            self._thread.start()
            return self.progress

    def stop(self) -> bool:
        """Stop the download; what arrived is kept for next time. True when one was running."""

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

    def _run(self, model: SpeechModel, progress: Progress) -> None:
        fetcher = self.fetcher or Fetcher()
        downloads = downloads_dir(self.root)
        staging = models_root(self.root) / f".staging-{model.id}"
        try:
            if not installed(model, self.root):
                offset = on_disk(downloads, model.pinned)

                def advance(size: int) -> None:
                    progress.done = max(0, size - offset)

                archive = fetcher.fetch(model.pinned, downloads, progress=advance, check=self._check)
                progress.done = progress.total
                progress.phase = "checking"
                shutil.rmtree(staging, ignore_errors=True)
                unpack(model, archive, staging, self._check)
                publish(model, staging, self.root)
                archive.unlink(missing_ok=True)
            if self.on_ready is not None:
                try:
                    self.on_ready(model)
                except Exception:  # noqa: BLE001 - the model is installed; choosing it can be done in Settings
                    logger.warning("The speech model was installed but wasn't chosen for Talk", exc_info=True)
            progress.phase = "finished"
            logger.info("Row-Bot installed the %s speech model", model.id)
        except Stopped:
            progress.phase = "stopped"
        except FetchError as error:
            progress.phase, progress.reason = "failed", error.code
            logger.warning("A speech-model download failed (%s)", error.code)
        except InstallError as error:
            progress.phase, progress.reason, progress.facts = "failed", error.code, dict(error.facts)
            logger.warning("Installing a speech model failed (%s)", error.code)
        except OSError as error:
            progress.phase, progress.reason = "failed", "out_of_disk" if disk_full(error) else "unpack"
            logger.warning("Installing a speech model failed", exc_info=True)
        finally:
            shutil.rmtree(staging, ignore_errors=True)


# Windows can hold a just-closed model file for a moment.
_DELETE_TRIES = 20
_DELETE_WAIT_SECONDS = 0.25
_pause = time.sleep


def remove(model: SpeechModel, root: Path | None = None) -> int:
    """Delete the model's folder and its partial download; returns the bytes freed. The caller
    unloads the model first."""

    freed = 0
    for path in (model_dir(model, root), downloads_dir(root) / f"{model.archive}.part"):
        before = disk_bytes_of(path)
        for attempt in range(_DELETE_TRIES):
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                break
            except FileNotFoundError:
                break
            except OSError:
                if attempt == _DELETE_TRIES - 1:
                    logger.warning("Some speech-model files are still in use and weren't removed", exc_info=True)
                else:
                    _pause(_DELETE_WAIT_SECONDS)
        freed += max(0, before - disk_bytes_of(path))
    return freed


def disk_bytes_of(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(base) / name).lstat().st_size
            except OSError:
                continue
    return total


__all__ = [
    "CATALOG",
    "Installer",
    "InstallError",
    "MOONSHINE_BASE_EN",
    "NEMOTRON_STREAMING_EN",
    "Progress",
    "SpeechModel",
    "WHISPER_ENGINE",
    "by_engine",
    "cuda_runtime",
    "disk_bytes",
    "get",
    "installed",
    "model_dir",
    "models_root",
    "recommend",
    "remaining_bytes",
    "remove",
    "runtime_available",
    "unpack",
]
