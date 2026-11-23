"""Settings › Voice's streaming speech models for the client: the pick, Download, Stop, Remove (VD3).

A download is an action the person presses, never an agent action: it starts only from
:func:`install`, whose request carries the size the button showed; a different size now is refused.
Reading the state reads files and this computer's hardware only (``local_runtime.hardware``, read
once per run); it never touches the network.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from row_bot.voice import speech_models

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_installers: dict[str, speech_models.Installer] = {}
# The last refusal or failure the person should see, until the next action.
_failure: dict[str, Any] | None = None


def _root() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir(create=False)


def installer() -> speech_models.Installer:
    """The one installer for this data folder (made on first use; nothing runs until Download)."""

    key = str(_root().resolve())
    with _lock:
        found = _installers.get(key)
        if found is None:
            found = _installers[key] = speech_models.Installer(on_ready=_ready)
        return found


def _ready(model: speech_models.SpeechModel) -> None:
    """A model finished installing: Talk listens with it (the Download button said so)."""

    from row_bot.voice.runtime import save_talk_model

    save_talk_model(model.engine_id)


def snapshot() -> dict[str, Any]:
    from row_bot.local_runtime import hardware
    from row_bot.voice.runtime import load_voice_runtime_settings

    found = hardware.peek()
    pick, reason = speech_models.recommend(found, gpu_runtime=speech_models.cuda_runtime())
    worker = installer()
    progress = worker.progress
    downloading = worker.running() and progress is not None
    rows = []
    for model in speech_models.CATALOG:
        installed = speech_models.installed(model)
        rows.append({
            "id": model.id, "name": model.name, "engine_id": model.engine_id,
            "size_bytes": model.archive_size, "installed_bytes": model.installed_bytes,
            "download_bytes": 0 if installed else speech_models.remaining_bytes(model),
            "installed": installed, "language": model.language, "licence": model.licence,
            "source": model.source, "runs_best_on": model.runs_best_on,
        })
    failure = _failure
    if failure is None and progress is not None and progress.phase == "failed" and not downloading:
        failure = {"reason": progress.reason or "network", **progress.facts}
    if not speech_models.runtime_available():
        state = "unavailable"
    elif downloading:
        state = "downloading"
    elif any(row["installed"] for row in rows):
        state = "installed"
    elif failure is not None:
        state = "failed"
    else:
        state = "not_installed"
    return {
        "schema_version": 1,
        "state": state,
        "computer": found.describe(),
        "recommended": pick.id,
        "reason": reason,
        "models": rows,
        "download": progress.view() if progress is not None and (downloading or progress.phase in
                                                                   ("stopped", "finished")) else None,
        "failure": failure,
        "talk_engine": load_voice_runtime_settings().talk_model,
        "disk_bytes": speech_models.disk_bytes(),
    }


def _model(model_id: str) -> speech_models.SpeechModel:
    from row_bot.application.client_platform import ClientPlatformError

    model = speech_models.get(model_id)
    if model is None:
        raise ClientPlatformError("invalid_command")
    return model


def install(model_id: str, download_bytes: int) -> dict[str, Any]:
    """The person pressed Download for ``model_id``: start it when the size they saw still holds."""

    global _failure
    model = _model(model_id)
    _failure = None
    if not speech_models.runtime_available():
        _failure = {"reason": "unavailable"}
        return snapshot()
    try:
        installer().start(model, expected_bytes=int(download_bytes))
    except speech_models.InstallError as error:
        _failure = {"reason": error.code, **error.facts}
    return snapshot()


def stop() -> dict[str, Any]:
    """Stop the download; what arrived is kept and it resumes on the next Download."""

    installer().stop()
    return snapshot()


def remove(model_id: str) -> dict[str, Any]:
    """Delete one model. Talk goes back to Whisper when it was listening with it."""

    global _failure
    from row_bot.voice.engine.sherpa_speech import unload
    from row_bot.voice.runtime import load_voice_runtime_settings, save_talk_model

    model = _model(model_id)
    worker = installer()
    if worker.progress is not None and worker.progress.model_id == model.id:
        worker.stop()
        worker.progress = None
    unload(model)
    freed = speech_models.remove(model)
    if load_voice_runtime_settings().talk_model == model.engine_id:
        save_talk_model(speech_models.WHISPER_ENGINE)
    _failure = None
    return {**snapshot(), "freed_bytes": int(freed)}


__all__ = ["install", "installer", "remove", "snapshot", "stop"]
