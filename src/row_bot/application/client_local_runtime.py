"""Run on this computer (E1) for the client: what is installed, the download, Stop, Remove, Repair.

The download is an action the person presses (Setup or Settings › Models), not an agent action: it
starts only from :func:`install`, whose request carries the size the button showed; a different size
now is refused, so the person always agreed to what is downloaded. Reading the state reads files and
this computer's hardware only; it never touches the network.
"""

from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path
from typing import Any

from row_bot import local_runtime
from row_bot.local_runtime import builds, hardware, install, models

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_installers: dict[str, install.Installer] = {}
# The last refusal or failure the person should see, until the next action.
_failure: dict[str, Any] | None = None


def _root() -> Path:
    return install.data_dir()


def installer() -> install.Installer:
    """The one installer for this data folder (made on first use; nothing runs until Download)."""

    key = str(_root().resolve())
    with _lock:
        found = _installers.get(key)
        if found is None:
            found = _installers[key] = install.Installer(on_ready=_ready)
        return found


def _ready(model: models.Model) -> None:
    """A model finished installing: the runtime's endpoint lists it, and the model is checked once on
    this computer with the probe Row-Bot runs for any endpoint (a reply, a tool call and a tool-result
    round trip), so its readiness is evidence, not a guess. That loads it once, now."""

    from row_bot.local_runtime import server

    server.save_endpoint()
    try:
        from row_bot.providers.custom import probe_custom_endpoint

        if server.get().connection(start=True) is not None:
            probe_custom_endpoint(local_runtime.PROVIDER_ID, model.id)
    except Exception:  # noqa: BLE001 - the model is installed; the probe can run again from Settings
        logger.warning("The first check of the model on this computer didn't finish", exc_info=True)


def _server_state() -> str | None:
    from row_bot.local_runtime import server

    try:
        return server.state()
    except Exception:  # noqa: BLE001
        return None


def _free_bytes() -> int:
    try:
        root = _root()
        while not root.exists() and root.parent != root:
            root = root.parent
        return int(shutil.disk_usage(root).free)
    except OSError:
        return 0


def snapshot() -> dict[str, Any]:
    """Everything Setup and Settings › Models show about running models on this computer."""

    found = hardware.peek()
    build = builds.select_build(found.platform, found.acceleration)
    current = install.installed_build()
    worker = installer()
    progress = worker.progress
    downloading = worker.running() and progress is not None
    pick = models.recommend(found)
    installed_ids = {model.id for model in install.installed_models()}
    rows = []
    for model in models.CATALOG:
        rows.append({
            "id": model.id,
            "name": model.name,
            "model_ref": local_runtime.model_ref(model.id),
            "size_bytes": model.size,
            "fit": models.fit(model, found),
            # The window it is served at here (what it reads).
            "context_tokens": models.context_for(model, found),
            "everyday": model.everyday,
            "quantization": model.quantization,
            "older": model.older,
            "installed": model.id in installed_ids,
            "download_bytes": install.remaining_bytes(build, model) if build is not None else 0,
        })
    if build is None:
        state = "unavailable"
    elif downloading:
        state = "downloading"
    elif current is not None and installed_ids:
        state = "running" if _server_state() == "running" else "installed"
    elif _failure is not None or (progress is not None and progress.phase == "failed"):
        state = "failed"
    else:
        state = "not_installed"
    failure = _failure
    if failure is None and progress is not None and progress.phase == "failed" and not downloading:
        failure = {"reason": progress.reason or "network", **progress.facts}
    outdated = current is not None and not current.current
    return {
        "schema_version": 1,
        "state": state,
        "computer": found.describe(),
        "acceleration": found.acceleration,
        "acceleration_label": builds.ACCELERATION_LABELS.get(found.acceleration, ""),
        "note": found.note,
        "release": builds.RELEASE,
        "build_installed": current is not None,
        "update_bytes": build.size if outdated and build is not None else 0,
        "recommended": pick.id if pick is not None and build is not None else None,
        "models": rows,
        "download": progress.view() if progress is not None and (downloading or progress.phase in
                                                                   ("stopped", "finished")) else None,
        "failure": failure,
        "disk_bytes": install.disk_bytes(),
        "free_bytes": _free_bytes(),
    }


def model_ready(model_id: str) -> bool:
    """Whether ``model_id`` is downloaded with a build to run it (Setup's pick may become the default)."""

    view = snapshot()
    return view["state"] in ("installed", "running") and any(
        row["id"] == model_id and row["installed"] for row in view["models"])


def _fail(reason: str, **facts: Any) -> None:
    global _failure
    _failure = {"reason": reason, **facts}


def install_model(model_id: str, download_bytes: int) -> dict[str, Any]:
    """The person pressed Download for ``model_id``: start it when the size they saw still holds."""

    global _failure
    from row_bot.application.client_platform import ClientPlatformError

    model = models.get(model_id)
    if model is None:
        raise ClientPlatformError("invalid_command")
    found = hardware.peek()
    build = builds.select_build(found.platform, found.acceleration)
    _failure = None
    if build is None:
        _fail("unavailable")
        return snapshot()
    try:
        installer().start(build, model, expected_bytes=int(download_bytes))
    except install.InstallError as error:
        _fail(error.code, **error.facts)
    return snapshot()


def stop() -> dict[str, Any]:
    """Stop a download (it resumes later) and the running model server."""

    installer().stop()
    from row_bot.local_runtime import server

    server.stop("you")
    return snapshot()


def remove(model_id: str | None = None) -> dict[str, Any]:
    """Remove one model, or the runtime and every model; the view says how much was freed."""

    global _failure
    from row_bot.application.client_platform import ClientPlatformError

    if model_id is not None and models.get(model_id) is None:
        raise ClientPlatformError("invalid_command")
    installer().stop()
    from row_bot.local_runtime import server

    server.stop("removed")
    freed = install.remove(model_id=model_id)
    try:
        server.save_endpoint()
    except Exception:  # noqa: BLE001
        logger.warning("The local endpoint wasn't updated after a removal", exc_info=True)
    _failure = None
    installer().progress = None
    return {**snapshot(), "freed_bytes": int(freed)}


def _windows() -> dict[str, int]:
    found = hardware.peek()
    return {model.id: models.context_for(model, found) for model in install.installed_models()}


def repair() -> dict[str, Any]:
    """Read this computer's hardware again (a new card, a newer driver) and forget the last failure."""

    global _failure
    _failure = None
    before = _windows()
    hardware.peek(refresh=True)
    if _windows() != before:
        # A new card or driver changes the windows the models are served at; the server takes them when it starts.
        from row_bot.local_runtime import server

        server.stop("repair")
        server.save_endpoint()
    worker = installer()
    if not worker.running():
        worker.progress = None
    return snapshot()


__all__ = ["install_model", "installer", "model_ready", "remove", "repair", "snapshot", "stop"]
