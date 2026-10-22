"""The personal model's download: the training engine, then the starting model (L2b).

The training engine, as the person sees it, includes the server Row-Bot runs personal models in
(``server_files.py``, O75): its pinned files download right after the engine's, inside the same
download and its size. An engine installed before the server existed reads as **Ready to download**
for the server alone (one tap, O36), never downloading by itself.

Nothing here starts by itself (O36). The person's **Download** (the ask, with the sizes, the free
space and where it comes from), **Resume**, **Try again**, **Repair** or **Update** starts it;
**Stop** (Monitor's row) pauses it, keeping what arrived; **Cancel** deletes what arrived. An old
"Download" answer from before the engine could be installed reads as **Ready to download**: one
tap starts it, the sizes and free space shown again.

States the person sees (``DownloadView.state``):

- ``ready``: they said Download before; nothing is downloading (O36);
- ``downloading``: bytes and percent of the whole download (a real denominator: every file's size
  is pinned), then installing and checking the engine;
- ``paused``: stopped by the person, by Learning pausing or turning off, by a dropped connection
  that didn't come back, or by Row-Bot closing ("Download paused at 3.1 of 6.8 GB.");
- ``not_enough_disk``: "Needs 9 GB free; 6 GB free.";
- ``failed``: with a reason (``network``, ``mismatch``, ``install_failed``, ``check_failed``,
  ``no_cuda``, ``refused``) and Try again;
- ``none``: nothing to download, or nothing asked.

Only one download runs at a time, on its own thread, only while Learning is on and not paused in
a build with the preview flag; Row-Bot quitting stops it (it shows as paused next time).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from row_bot import learning as gate
from row_bot.learning.training import engine as engine_module
from row_bot.learning.training import fetch, hardware, lockfile, server_files, weights

logger = logging.getLogger(__name__)

STATE_FILE = "download.json"
# Room left over after a download (logs, checkpoints, the system's own needs).
MARGIN_BYTES = 1_000_000_000
# Unpacked, the model server is larger than its download (its CUDA libraries are compressed).
SERVER_INSTALLED_FACTOR = 1.4
FAILURES = frozenset({"network", "mismatch", "install_failed", "check_failed", "no_cuda", "refused"})

State = Literal["none", "ready", "downloading", "paused", "not_enough_disk", "failed"]
Phase = Literal["engine", "installing", "checking", "model"]


class DownloadError(RuntimeError):
    """A refused download action; ``code`` is stable: ``learning_off``, ``learning_paused``,
    ``unavailable`` (this computer can't train here), ``not_enough_disk``, ``busy``, ``changed``."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DownloadView:
    """The download as the surfaces show it."""

    state: State
    phase: Phase | None = None
    done_bytes: int = 0
    total_bytes: int = 0
    needs_bytes: int | None = None
    free_bytes: int | None = None
    failure: str | None = None
    engine_state: str = "not_installed"
    engine_bytes: int = 0
    engine_download_bytes: int = 0
    model_state: str = "not_downloaded"
    model_bytes: int = 0


@dataclass
class _Live:
    phase: Phase = "engine"
    done: int = 0
    total: int = 0
    stop: threading.Event = field(default_factory=threading.Event)
    reason: str = "you"
    thread: threading.Thread | None = None


def free_bytes(path: Path) -> int | None:
    """Free space on the drive that holds ``path`` (or its nearest existing parent)."""

    current = Path(path)
    while not current.exists() and current.parent != current:
        current = current.parent
    try:
        return int(shutil.disk_usage(current).free)
    except OSError:
        return None


class Downloads:
    """The download of the engine and the starting model, one at a time."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        fetcher: Callable[[], fetch.Fetcher] = fetch.Fetcher,
        installer_options: dict[str, Any] | None = None,
        server_options: dict[str, Any] | None = None,
        check_hardware: Callable[[], hardware.HardwareCheck] = hardware.check,
        peek_hardware: Callable[[], hardware.HardwareCheck | None] = hardware.peek,
        disk_free: Callable[[Path], int | None] = free_bytes,
        start_threads: bool = True,
    ) -> None:
        self._root = root
        self._fetcher = fetcher
        self._installer_options = dict(installer_options or {})
        self._server_options = dict(server_options or {})
        self._hardware = check_hardware
        self._peek = peek_hardware
        self._disk_free = disk_free
        self._start_threads = start_threads
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._live: _Live | None = None

    # ── Where things are ────────────────────────────────────────────────

    @property
    def state_path(self) -> Path:
        return engine_module.learning_runtimes(self._root) / STATE_FILE

    def _saved(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _save(self, state: str | None, **fields: Any) -> None:
        path = self.state_path
        if state is None:
            path.unlink(missing_ok=True)
            engine_module._tidy(self._root)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps({"state": state, "at": time.time(), **fields}), encoding="utf-8")
        os.replace(temporary, path)

    def _model(self) -> weights.PinnedModel:
        return weights.MODELS.get(gate.read_settings(self._root).starting_model, weights.QWEN35_2B)

    def variant(self) -> str | None:
        """The engine build this computer's driver takes, or None when it can't train here
        (runs the hardware check if it hasn't run: up to 15 s)."""

        found = self._hardware()
        return found.variant if found.can_train else None

    def _variant_now(self) -> str | None:
        """The same without waiting: the kept check, or the usual build when it hasn't run yet."""

        found = self._peek()
        if found is None:
            return "cu130"
        return found.variant if found.can_train else None

    def _installer(self, plan: lockfile.EnginePlan, live: _Live | None = None) -> engine_module.EngineInstaller:
        def moved(progress: engine_module.Progress) -> None:
            if live is not None:
                with self._lock:
                    live.phase = progress.phase if progress.phase in ("engine", "installing", "checking") else "engine"
                    live.done = progress.done
                    self._changed.notify_all()

        return engine_module.EngineInstaller(plan, root=self._root, fetcher=self._fetcher(), on_progress=moved,
                                             **self._installer_options)

    # ── The view ────────────────────────────────────────────────────────

    def _server(self, variant: str | None) -> server_files.ServerInstaller | None:
        """The model server's install for this computer's build (None where Row-Bot has none)."""

        if variant is None or not server_files.plan(variant):
            return None
        return server_files.ServerInstaller(variant, root=self._root, fetcher=self._fetcher(),
                                            **self._server_options)

    def _sizes(self, variant: str | None) -> dict[str, Any]:
        """What is installed, what is left to download, and how much room that needs."""

        model = self._model()
        engine_found = engine_module.status(self._root)
        model_found = weights.status(model, self._root)
        engine_total = engine_done = installed = 0
        engine_needed = not engine_found.installed
        engine_download = 0
        if variant is not None:
            try:
                plan = lockfile.cached_plan(variant)
                installer = self._installer(plan)
                engine_download = plan.download_bytes()
                if engine_needed:
                    engine_total = sum(item.known_size or lockfile.UNKNOWN_WHEEL_BYTES for item in installer.files())
                    engine_done = min(engine_total, installer.downloaded())
                    installed = installer.installed_estimate()
            except Exception:
                logger.debug("Learning could not read the engine's lock", exc_info=True)
        server = self._server(variant)
        server_found = server_files.status(self._root, variant=variant) if server is not None else None
        server_needed = server is not None and server_found is not None and not server_found.installed
        server_total = server.total() if server is not None and server_needed else 0
        server_done = min(server_total, server.downloaded()) if server is not None and server_needed else 0
        if server_needed:
            installed += int(server_total * SERVER_INSTALLED_FACTOR)
        engine_download += server.total() if server is not None else 0
        model_needed = model_found.state != "downloaded"
        model_total = model.download_bytes if model_needed else 0
        model_done = weights.downloaded_bytes(model, self._root) if model_needed else 0
        remaining = (engine_total - engine_done) + (server_total - server_done) + (model_total - model_done)
        needed = engine_needed or server_needed or model_needed
        return {
            "variant": variant, "engine": engine_found, "model": model_found, "engine_needed": engine_needed,
            "server": server_found, "server_needed": server_needed, "server_total": server_total,
            "model_needed": model_needed, "total": engine_total + server_total + model_total,
            "done": engine_done + server_done + model_done,
            "needs": remaining + installed + MARGIN_BYTES if needed else 0,
            "engine_download": engine_download,
        }

    def view(self) -> DownloadView:
        """The download now (reads only: nothing starts)."""

        sizes = self._sizes(self._variant_now())
        free = self._disk_free(engine_module.learning_runtimes(self._root))
        engine_found, model_found = sizes["engine"], sizes["model"]
        server_found = sizes["server"]
        server_bytes = server_found.bytes if server_found is not None and server_found.installed else 0
        common = {
            "engine_state": engine_found.state, "engine_bytes": engine_found.bytes + server_bytes,
            "engine_download_bytes": sizes["engine_download"],
            "model_state": model_found.state if model_found.state != "partial" else "not_downloaded",
            "model_bytes": model_found.bytes if model_found.state == "downloaded" else 0,
            "free_bytes": free,
        }
        with self._lock:
            live = self._live
            if live is not None:
                return DownloadView("downloading", live.phase, live.done, live.total,
                                    needs_bytes=sizes["needs"], **common)
        if not (sizes["engine_needed"] or sizes["server_needed"] or sizes["model_needed"]):
            return DownloadView("none", **common)
        saved = self._saved()
        state = saved.get("state")
        if state in ("paused", "downloading"):  # "downloading": Row-Bot closed while it ran
            return DownloadView("paused", None, sizes["done"], sizes["total"], needs_bytes=sizes["needs"], **common)
        if state == "not_enough_disk":
            return DownloadView("not_enough_disk", None, sizes["done"], sizes["total"], needs_bytes=sizes["needs"],
                                **common)
        if state == "failed":
            failure = saved.get("failure") if saved.get("failure") in FAILURES else "network"
            return DownloadView("failed", None, sizes["done"], sizes["total"], needs_bytes=sizes["needs"],
                                failure=failure, **common)
        settings = gate.read_settings(self._root)
        only_server = sizes["server_needed"] and not (sizes["engine_needed"] or sizes["model_needed"])
        if settings.model_download == "waiting" or only_server:
            # An engine from before the model server: the server alone is ready to download (O36).
            return DownloadView("ready", None, sizes["done"], sizes["total"], needs_bytes=sizes["needs"], **common)
        return DownloadView("none", None, sizes["done"], sizes["total"], needs_bytes=sizes["needs"], **common)

    def running(self) -> bool:
        with self._lock:
            return self._live is not None

    # ── The person's actions ────────────────────────────────────────────

    def _require_on(self) -> None:
        if not gate.preview_enabled():
            raise DownloadError("learning_off")
        settings = gate.read_settings(self._root)
        if not settings.enabled:
            raise DownloadError("learning_off")
        if settings.paused:
            raise DownloadError("learning_paused")
        if settings.train_where != "this_computer":
            raise DownloadError("unavailable")

    def start(self) -> DownloadView:
        """Download, Resume, Try again, Repair or Update: whatever is missing downloads now."""

        with self._lock:
            self._require_on()
            if self._live is not None:
                return self.view()
            variant = self.variant()
            sizes = self._sizes(variant)
            if not (sizes["engine_needed"] or sizes["server_needed"] or sizes["model_needed"]):
                self._save(None)
                return self.view()
            if sizes["engine_needed"] and variant is None:
                raise DownloadError("unavailable")
            try:
                plan = lockfile.cached_plan(variant) if sizes["engine_needed"] and variant else None
            except lockfile.LockError:
                # This copy of Row-Bot has no engine lock (a bare package install): nothing to install.
                logger.warning("Learning's training engine lock is missing or unreadable")
                raise DownloadError("unavailable") from None
            free = self._disk_free(engine_module.learning_runtimes(self._root))
            if free is not None and free < sizes["needs"]:
                self._save("not_enough_disk", needs=sizes["needs"])
                raise DownloadError("not_enough_disk")
            live = _Live(phase="engine" if sizes["engine_needed"] or sizes["server_needed"] else "model",
                         done=sizes["done"], total=sizes["total"])
            self._live = live
            self._save("downloading")
            model = self._model() if sizes["model_needed"] else None
            server = self._server(variant) if sizes["server_needed"] else None
            if self._start_threads:
                live.thread = threading.Thread(target=self._run, args=(live, plan, model, sizes, server),
                                               name="learning-download", daemon=True)
                live.thread.start()
            else:
                self._run(live, plan, model, sizes, server)
            return self.view()

    def stop(self, reason: str = "you") -> DownloadView:
        """Stop now, keeping what arrived (Monitor's Stop, Learning pausing or turning off)."""

        with self._lock:
            live = self._live
            if live is None:
                saved = self._saved().get("state")
                if saved not in ("paused", "downloading"):
                    raise DownloadError("changed")
                return self.view()
            live.reason = reason
            live.stop.set()
            thread = live.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(30)
        return self.view()

    def cancel(self) -> DownloadView:
        """Cancel: stop, and delete what arrived (an installed engine or model stays)."""

        self.stop_quietly("you")
        with self._lock:
            if self._live is not None:
                raise DownloadError("busy")
            _remove_downloads(self._root, self._model())
            self._save(None)
        return self.view()

    def stop_quietly(self, reason: str) -> None:
        try:
            self.stop(reason)
        except DownloadError:
            pass

    def forget(self) -> None:
        """Forget a paused, failed or full-disk download (the engine or model was removed)."""

        with self._lock:
            if self._live is None:
                self._save(None)

    def wait(self, timeout: float = 30.0) -> DownloadView:
        """Wait until no download runs (tests)."""

        deadline = time.monotonic() + timeout
        with self._changed:
            while self._live is not None:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError("the download didn't finish in time")
                self._changed.wait(min(left, 0.25))
        return self.view()

    # ── The thread ──────────────────────────────────────────────────────

    def _run(self, live: _Live, plan: lockfile.EnginePlan | None, model: weights.PinnedModel | None,
             sizes: dict[str, Any], server: server_files.ServerInstaller | None = None) -> None:
        def check() -> None:
            if live.stop.is_set() or not gate.is_enabled(self._root) or not gate.preview_enabled():
                if not live.stop.is_set():
                    live.reason = "learning"
                    live.stop.set()
                raise fetch.Stopped()

        outcome: tuple[str | None, dict[str, Any]] = (None, {})
        engine_total = sizes["total"] - (model.download_bytes if model is not None else 0)
        before_server = engine_total - sizes["server_total"]
        try:
            if plan is not None:
                installer = self._installer(plan, live)
                installer.install(check)
            if server is not None:
                with self._lock:
                    live.phase = "engine"
                    self._changed.notify_all()

                def server_moved(done: int, _total: int) -> None:
                    with self._lock:
                        live.phase = "engine"
                        live.done = before_server + done
                        self._changed.notify_all()

                server.on_progress = server_moved
                server.install(check)
            if model is not None:
                with self._lock:
                    live.phase = "model"
                    self._changed.notify_all()

                def moved(current: int) -> None:
                    with self._lock:
                        live.done = engine_total + current
                        self._changed.notify_all()

                weights.download(model, root=self._root, fetcher=self._fetcher(), progress=moved, check=check)
        except fetch.Stopped:
            outcome = ("paused", {"reason": live.reason})
        except (engine_module.EngineError, fetch.FetchError) as error:
            code = error.code
            if code == "stopped":
                outcome = ("paused", {"reason": live.reason})
            elif code == "out_of_disk":
                outcome = ("not_enough_disk", {})
            else:
                outcome = ("failed", {"failure": code if code in FAILURES else "network"})
                logger.warning("Learning's download failed (%s)", code)
        except Exception:
            logger.exception("Learning's download failed")
            outcome = ("failed", {"failure": "install_failed"})
        finally:
            with self._lock:
                state, fields = outcome
                try:
                    self._save(state, **fields)
                except OSError:
                    logger.warning("Learning could not save its download's state", exc_info=True)
                self._live = None
                self._changed.notify_all()

    def shutdown(self) -> None:
        """Row-Bot is quitting: the download stops (it reads as paused next time)."""

        self.stop_quietly("app_closed")


def _remove_downloads(root: Path | None, model: weights.PinnedModel) -> None:
    from row_bot.learning.training.engine import _remove_tree, downloads_dir, engine_dir

    _remove_tree(downloads_dir(root))
    for leftover in engine_dir(root).glob(".staging-*") if engine_dir(root).is_dir() else ():
        _remove_tree(leftover)
    _remove_tree(server_files.downloads_dir(root))
    for part in (model, model.training) if model.training is not None else (model,):
        _remove_tree(weights.partial_dir(part, root))


# ── The app's downloads ─────────────────────────────────────────────────────

_lock = threading.Lock()
_downloads: Downloads | None = None


def get() -> Downloads:
    global _downloads
    with _lock:
        if _downloads is None:
            _downloads = Downloads()
        return _downloads


def loaded() -> Downloads | None:
    return _downloads


def learning_stopped() -> None:
    """Learning was paused or turned off: a running download stops (it shows as paused)."""

    downloads = _downloads
    if downloads is not None:
        downloads.stop_quietly("learning")


def shutdown(timeout: float = 10.0) -> None:
    """Row-Bot is quitting: a running download stops (``timeout`` is the app's shutdown budget)."""

    downloads = _downloads
    if downloads is not None:
        downloads.shutdown()


__all__ = [
    "DownloadError",
    "DownloadView",
    "Downloads",
    "FAILURES",
    "MARGIN_BYTES",
    "free_bytes",
    "get",
    "learning_stopped",
    "loaded",
    "shutdown",
]
