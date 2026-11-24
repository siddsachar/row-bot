"""Any open model in the client (C1 Part 4, O138): search Hugging Face, the models the person chose and their checks.

``search`` answers ``GET /api/v1/learning/models`` (Hugging Face's free public search, no sign-in); ``view`` is the
snapshot's ``personal_model.other_models``; ``perform`` runs ``check_model`` (the licence agreed in the same ask
where it needs that), ``stop_model_check`` and ``forget_model``. A model is offered for training only once its check
passed (``open_models.as_choice``); a gated or unsupported one is never checked.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Any

from row_bot.application.client_platform import ClientPlatformError

logger = logging.getLogger(__name__)

ACTIONS = frozenset({"check_model", "stop_model_check", "forget_model"})


def _card_mib() -> int | None:
    from row_bot.learning.training import hardware

    found = hardware.peek()
    return int(found.gpu.memory_mib) if found is not None and found.gpu is not None else None


def _model_view(model: Any, card_mib: int | None) -> dict[str, Any]:
    from row_bot.learning.training import open_models

    serve = open_models.serve_estimate(model.params_b)
    return {
        "id": model.id, "repo": model.repo, "name": model.name[:128], "family": model.family,
        "params_b": model.params_b, "licence": model.licence[:96], "licence_kind": model.licence_kind,
        "licence_url": model.licence_url[:512], "terms": [item[:300] for item in model.terms][:6],
        "downloads": int(model.downloads), "unsupported": (model.unsupported or None) and model.unsupported[:300],
        # O131: a model this card can't run afterwards is never offered, wherever it would train.
        "runs_here": bool(serve is not None and card_mib and serve.need_mib <= card_mib),
        "runs_here_gb": round(serve.need_mib / 1024, 1) if serve is not None else None,
    }


def search(query: str) -> dict[str, Any]:
    from row_bot.learning.training import open_models

    text = " ".join(str(query or "").split())[:80]
    try:
        found = open_models.search(text)
    except open_models.HubError as error:
        return {"query": text, "models": [], "failure": error.code}
    card = _card_mib()
    return {"query": text, "models": [_model_view(model, card) for model in found], "failure": None}


def _record_view(record: Any) -> dict[str, Any]:
    from row_bot.learning.training import open_models

    return {
        "id": record.id, "repo": record.repo, "name": record.name[:128], "family": record.family or None,
        "params_b": record.params_b, "licence": record.licence[:96], "licence_kind": record.licence_kind,
        "licence_url": record.licence_url[:512], "terms": [item[:300] for item in record.terms][:6],
        "licence_agreed": record.licence_kind != "terms" or record.licence_ok_at is not None,
        "state": record.state, "stage": record.stage, "failure": record.failure,
        "items": [{"id": name, "state": record.item(name).state, "words": record.item(name).words[:300]}
                  for name in open_models.CHECK_ITEMS],
        "sample": [{"prompt": str(item.get("prompt") or "")[:300], "passed": bool(item.get("passed"))}
                   for item in record.sample][:6],
        "download_bytes": int(record.download_bytes), "downloaded_bytes": int(record.downloaded_bytes),
        "trains_here": record.trains_here, "hugging_face_flavor": record.hugging_face_flavor,
    }


def view() -> list[dict[str, Any]]:
    """The models the person chose to check, newest first (never more than a short list)."""

    from row_bot.learning.training import open_models

    try:
        return [_record_view(item) for item in open_models.records()[:12]]
    except Exception:  # noqa: BLE001 - the rest of the snapshot still comes back
        logger.debug("Learning couldn't read the checked models", exc_info=True)
        return []


def _engine_installed() -> bool:
    """Whether this computer has the training engine: the check trains, converts and serves with it, so a check
    without it would download the whole model only to fail."""

    from row_bot.learning.training import downloads

    try:
        return downloads.get().view().engine_state == "installed"
    except Exception:  # noqa: BLE001 - unknown: the person is asked to download it
        logger.debug("Learning couldn't read the training engine's state", exc_info=True)
        return False


def _in_use(model_id: str) -> bool:
    """Whether training uses this model: the starting model here, the model Hugging Face trains (while Learning
    trains there), or the model of a Hugging Face run under way. Its record stays while it does."""

    from row_bot import learning
    from row_bot.application import learning_hugging_face

    settings = learning.read_settings()
    if model_id == settings.starting_model or (settings.train_where == "hugging_face"
                                               and model_id == settings.hf_model):
        return True
    try:
        job = learning_hugging_face._current_job()  # noqa: SLF001 - the Hugging Face run, if any
    except Exception:  # noqa: BLE001
        logger.debug("Learning couldn't read the current run", exc_info=True)
        return False
    if job is None or job.state not in ("preparing", "running", "unknown_remote"):
        return False
    from row_bot.learning.remote import training as remote_training

    record = remote_training.read_record(job.id)
    return record is not None and record.model_id == model_id


def perform(action: str, command: dict[str, Any]) -> str:
    from row_bot.learning.training import open_models

    repo = open_models.repo_of(str(command.get("model") or ""))
    if repo is None:
        raise ClientPlatformError("invalid_learning_command")
    checker = open_models.checker()
    if action == "stop_model_check":
        if checker.running != repo:
            raise ClientPlatformError("learning_model_check_changed")
        checker.stop()
        return "Stopping the check. What it downloaded stays for the next try."
    if _in_use(open_models.model_id(repo)):
        raise ClientPlatformError("learning_model_in_use")  # choose another model first
    if action == "forget_model":
        if checker.running == repo:
            raise ClientPlatformError("learning_model_check_busy")
        open_models.forget(repo)
        return "Removed from Other models."
    # check_model: read the card now (free), then ask nothing more: the client asked first (download, the card).
    if checker.running is not None:
        raise ClientPlatformError("learning_model_check_busy")  # one check at a time (it uses the card)
    if not _engine_installed():
        raise ClientPlatformError("learning_engine_needed")
    try:
        model, files = open_models.inspect(repo)
    except open_models.HubError as error:
        raise ClientPlatformError("learning_hugging_face_unreachable" if error.code == "unreachable"
                                  else "learning_model_unavailable") from None
    serve = open_models.serve_estimate(model.params_b)
    card = _card_mib()
    if model.unsupported or model.gated or serve is None or not card or serve.need_mib > card:
        # Never attempted blindly; and a model this card can't run afterwards is never offered (O131).
        raise ClientPlatformError("learning_model_unavailable")
    saved = open_models.read(repo)
    record = open_models.new_record(model, files)
    if saved is not None and saved.revision == record.revision:
        record = replace(record, licence_ok_at=saved.licence_ok_at, pinned=saved.pinned)
    if record.licence_kind == "terms" and record.licence_ok_at is None:
        if command.get("licence_ok") is not True:
            raise ClientPlatformError("learning_licence_needed")
        record = replace(record, licence_ok_at=time.time())
    record = open_models.write(record)
    try:
        checker.start(record)
    except RuntimeError:
        raise ClientPlatformError("learning_model_check_busy") from None
    return f"Checking {model.name} on this computer. It downloads first ({_size(model.download_bytes)})."


def _size(value: int) -> str:
    return f"{value / 1e9:.1f} GB"


__all__ = ["ACTIONS", "perform", "search", "view"]
