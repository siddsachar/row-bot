"""Training on Hugging Face in the Learning API (C1): the snapshot's ``hugging_face`` part and its commands.

Reads never reach the network but for Hugging Face's public price list (cached for hours); the sign-in's
token never leaves the keychain (``learning/remote/auth.py``). Every paid run asks on the device it starts
from: ``hf_start`` carries the quote's cost the person saw, and is refused if the quote changed since
(``learning_quote_changed``); runs after the first start by themselves in quiet hours within the monthly cap
(O132).
"""

from __future__ import annotations

import logging
import math
from typing import Any

from row_bot import learning
from row_bot.application.client_platform import ClientPlatformError

logger = logging.getLogger(__name__)

ACTIONS = frozenset({"hf_sign_in", "hf_cancel_sign_in", "hf_sign_out", "hf_choose_model", "hf_choose_flavor",
                     "hf_set_cap", "hf_start",
                     "hf_cancel", "hf_check_now"})
# The monthly cap's choices in dollars (the quote sheet's menu; any whole number up to the maximum is accepted).
CAP_CHOICES = (10, 20, 50, 100)
STAGES = frozenset({"setting_up", "downloading", "training", "exporting", "uploading", "done"})
REMOTE_FAILURES = frozenset({"upload_failed", "not_started", "remote_error", "no_credit", "signed_out",
                             "model_mismatch", "export_failed", "out_of_gpu_memory", "engine_error"})


def _trainer() -> Any:
    from row_bot.learning.remote import training as remote_training

    return remote_training.get()


def _sign_in() -> Any:
    return _trainer().sign_in()


def available() -> bool:
    """Whether this install can talk to the Hub (Row-Bot's full install carries Hugging Face's own library)."""

    import importlib.util

    return importlib.util.find_spec("huggingface_hub") is not None


def _sign_in_view() -> dict[str, Any]:
    try:
        state = _sign_in().status()
    except Exception:  # noqa: BLE001
        logger.debug("Learning couldn't read its Hugging Face sign-in", exc_info=True)
        return {"state": "signed_out", "user": "", "code": None, "url": None, "failure": None}
    return {"state": state.state, "user": state.user[:96], "code": state.code.user_code if state.code else None,
            "url": state.code.verification_uri_complete if state.code else None,
            "failure": state.failure if state.failure in ("expired", "denied", "refused") else None}


def _model_view(model: Any, card_mib: int | None, recommended: str) -> dict[str, Any]:
    option = model.serve_for(card_mib)
    return {"id": model.id, "name": model.name, "licence": model.licence, "params_b": model.params_b,
            "certified": bool(model.certified), "recommended": model.id == recommended,
            "runs_here_gb": round(option.need_mib / 1024, 1) if option is not None else None,
            "download_bytes": int(option.gguf_bytes) if option is not None else 0}


def _quote_view(quote: Any) -> dict[str, Any] | None:
    if quote is None:
        return None
    data = quote.to_dict()
    keep = ("model_id", "model_name", "flavor", "flavor_label", "gpu_memory_gb", "price_per_hour", "seconds",
            "cost_usd", "timeout_seconds", "max_cost_usd", "examples", "upload_bytes", "download_bytes",
            "dataset_repo", "measured", "month_spent_usd", "cap_usd", "fits_cap")
    found = {key: data[key] for key in keep}
    found["choices"] = [dict(item) for item in data.get("choices") or ()][:4]
    return found


def _current_job() -> Any:
    from row_bot.learning import runner as runner_module

    if not learning.store_exists():
        return None
    job = runner_module.get().supervisor.jobs.current()
    return job if job is not None and job.backend == "hugging_face" else None


def _planned_quote(trainer: Any, job: Any) -> Any:
    examples, size = trainer._upload_size(job)  # noqa: SLF001 - the rows leased for this run
    return trainer.quote(examples=examples, upload_bytes=size, steps=job.steps_total)


def _typical_quote(trainer: Any) -> Any:
    """A run of the recipe's smallest size (90 rows, about 40 KB each in the serving shape): what a typical
    first run costs, for the guided start before any run is planned."""

    recipe = trainer.recipe()
    return trainer.quote(examples=recipe.min_examples, upload_bytes=recipe.min_examples * 40_000)


def view(settings: learning.LearningSettings) -> dict[str, Any] | None:
    """The snapshot's ``personal_model.hugging_face``: always present while Learning is on (setup's guided start
    reads it too), never a token."""

    try:
        trainer = _trainer()
        card = trainer.card_mib()
        models = trainer.models()
        chosen = trainer.choice()
        recommended = models[0].id if models else ""
        job = _current_job()
        quote = None
        if chosen is not None:
            try:
                quote = _planned_quote(trainer, job) if job is not None and job.state in ("planned", "waiting") \
                    else _typical_quote(trainer)
            except Exception:  # noqa: BLE001 - a quote that can't be made yet is left out
                logger.debug("Learning couldn't make a Hugging Face quote", exc_info=True)
        from row_bot.learning.remote import training as remote_training

        now = trainer.clock()
        runs = [item for item in remote_training.read_spend() if item.get("month") == remote_training._month(now)]  # noqa: SLF001
        return {
            "available": available(),
            "sign_in": _sign_in_view(),
            "models": [_model_view(model, card, recommended) for model in models][:8],
            "model": chosen.id if chosen is not None else "",
            "card_gb": round(card / 1024) if card else None,
            "quote": _quote_view(quote),
            "planned": job is not None and job.state in ("planned", "waiting"),
            "cap_usd": int(settings.hf_monthly_cap_usd),
            "cap_choices": list(CAP_CHOICES),
            "month_spent_usd": round(sum(float(item.get("cost_usd") or 0) for item in runs), 2),
            "runs_confirmed": bool(settings.hf_runs_confirmed),
            "recent_runs": [{"job_id": item["job_id"], "cost_usd": round(float(item.get("cost_usd") or 0), 2),
                             "minutes": int(math.ceil(int(item.get("seconds") or 0) / 60)),
                             "final": bool(item.get("final"))} for item in runs[-6:]],
            "last_run": _last_run(),
        }
    except Exception:  # noqa: BLE001
        logger.debug("Learning couldn't read Hugging Face training", exc_info=True)
        return None


def _last_run() -> dict[str, Any] | None:
    """The newest finished Hugging Face run: its version, what it cost, and whether its uploads were deleted
    there (O133: Learning says so)."""

    from row_bot.learning import runner as runner_module
    from row_bot.learning.remote import training as remote_training

    if not learning.store_exists():
        return None
    for job in runner_module.get().supervisor.jobs.all()[:50]:
        if job.backend != "hugging_face" or job.state in ("planned", "waiting", "preparing", "running",
                                                          "unknown_remote"):
            continue
        record = remote_training.read_record(job.id)
        if record is None or record.submit_started_at is None:
            continue
        return {"version": job.version, "cost_usd": round(float(record.cost_usd), 2),
                "minutes": int(math.ceil(record.running_seconds / 60)) if record.running_seconds else 0,
                "outcome": record.outcome if record.outcome in ("completed", "error", "canceled", "not_started")
                else None,
                "cleaned": record.cleaned_at is not None, "cleanup_failed": bool(record.cleanup_failed)}
    return None


def run_view(job: Any) -> dict[str, Any] | None:
    """A Hugging Face run's own facts for the training card and Monitor: its stage, cost so far and whether
    contact is lost (never a token, an address with one, or a log line)."""

    from row_bot.learning.remote import training as remote_training

    record = remote_training.read_record(job.id)
    if record is None:
        return None
    return {
        "phase": record.phase if record.phase in ("uploading", "submitting", "running", "downloading", "downloaded",
                                                   "cleaned") else "running",
        "stage": record.stage if record.stage in STAGES else None,
        "cost_usd": round(float(record.cost_usd), 2),
        "minutes": int(math.ceil(record.running_seconds / 60)) if record.running_seconds else 0,
        "lost": job.state == "unknown_remote",
        "flavor": record.flavor[:32],
        # Hugging Face hasn't a free card of this kind yet: the run waits in its queue, unbilled.
        "waiting_for_gpu": record.phase == "running" and record.remote_stage == "SCHEDULING",
        "gpu_label": _gpu_label(record.flavor),
        "cleaned": record.cleaned_at is not None,
        "cleanup_failed": bool(record.cleanup_failed),
        "url": (f"https://huggingface.co/jobs/{record.namespace}/{record.remote_id}"
                if record.remote_id and record.namespace else None),
    }


def failure(job: Any) -> str | None:
    code = str(job.error_code or "")
    return code if code in REMOTE_FAILURES else ("remote_error" if job.backend == "hugging_face" else None)


def _gpu_label(flavor: str) -> str:
    """Hugging Face's own name for a card (``1x Nvidia L40S``), from its price list; the flavour if unread."""

    try:
        found = _trainer().prices().get(flavor)
    except Exception:  # noqa: BLE001
        found = None
    return str(found.label if found is not None else flavor)[:64]


def _money(value: float) -> str:
    return f"${value:,.2f}"


def perform(action: str, command: dict[str, Any]) -> str:
    """Carry out one Hugging Face command (admitted by ``execute_learning``): what happened, in words."""

    from row_bot.learning.remote.auth import SignInError
    from row_bot.learning.remote.training import RemoteError

    settings = learning.read_settings()
    if action == "hf_sign_in":
        if not available():
            raise ClientPlatformError("learning_hugging_face_unavailable")
        try:
            _sign_in().start()
        except SignInError as error:
            raise ClientPlatformError("learning_hugging_face_unreachable" if error.code == "network"
                                      else "learning_hugging_face_refused") from None
        return "Hugging Face is open: sign in there and click Authorize."
    if action == "hf_cancel_sign_in":
        _sign_in().cancel()
        return "Sign-in cancelled."
    if action == "hf_sign_out":
        job = _current_job()
        if job is not None and job.state in ("preparing", "running", "unknown_remote"):
            raise ClientPlatformError("learning_hugging_face_busy")
        try:
            _sign_in().sign_out()
        except SignInError:
            raise ClientPlatformError("learning_save_unconfirmed") from None
        return "Signed out of Hugging Face. Row-Bot removed its sign-in from this computer."
    if action == "hf_choose_model":
        model_id = str(command.get("model") or "")
        offered = {model.id: model for model in _trainer().models()}
        if model_id not in offered:
            raise ClientPlatformError("learning_training_changed")
        _save({"hugging_face.model": model_id})
        _set_aside_planned()
        return f"{offered[model_id].name} will train on Hugging Face."
    if action == "hf_choose_flavor":
        flavor = str(command.get("flavor") or "")
        trainer = _trainer()
        offered = {item["flavor"]: item for item in trainer.flavors()}
        job = _current_job()
        if flavor not in offered:
            raise ClientPlatformError("learning_training_changed")
        if job is not None and job.state in ("preparing", "running", "unknown_remote"):
            raise ClientPlatformError("learning_hugging_face_busy")
        _save({"hugging_face.flavor": flavor})
        return f"Training on Hugging Face uses {_gpu_label(flavor)}."
    if action == "hf_set_cap":
        cap = command.get("cap_usd")
        if type(cap) is not int or not 1 <= cap <= learning.MAX_HUGGING_FACE_CAP_USD:
            raise ClientPlatformError("invalid_learning_command")
        _save({"hugging_face.monthly_cap_usd": cap})
        return f"Hugging Face spending is capped at {_money(cap)} a month."
    trainer = _trainer()
    job = _current_job()
    if action == "hf_start":
        cap = command.get("cap_usd")
        if cap is not None:
            if type(cap) is not int or not 1 <= cap <= learning.MAX_HUGGING_FACE_CAP_USD:
                raise ClientPlatformError("invalid_learning_command")
            _save({"hugging_face.monthly_cap_usd": cap})
        if job is None or job.state not in ("planned", "waiting") or job.id != str(command.get("job_id") or ""):
            raise ClientPlatformError("learning_training_changed")
        seen = command.get("quote_cost_cents")
        quote = _planned_quote(trainer, job)
        if type(seen) is not int or seen != round(quote.cost_usd * 100):
            raise ClientPlatformError("learning_quote_changed")  # the person agreed to another price
        try:
            trainer.start(job, "train_now", confirmed=True)
        except RemoteError as error:
            raise ClientPlatformError({"signed_out": "learning_hugging_face_signed_out",
                                       "over_cap": "learning_hugging_face_over_cap",
                                       "busy": "learning_training_changed",
                                       "no_model": "learning_training_changed"}.get(error.code,
                                                                                    "learning_training_changed")) from None
        if not settings.hf_runs_confirmed:
            _save({"hugging_face.runs_confirmed": True})
        return (f"Uploading {quote.examples} examples to your private Hugging Face dataset, then training "
                f"starts (about {_money(quote.cost_usd)}).")
    found = job if job is not None and job.id == str(command.get("job_id") or "") else None
    if found is None:
        raise ClientPlatformError("learning_training_changed")
    if action == "hf_cancel":
        if found.state not in ("planned", "waiting", "preparing", "running", "unknown_remote"):
            raise ClientPlatformError("learning_training_changed")
        if found.state in ("planned", "waiting"):
            from row_bot.learning import runner as runner_module

            runner_module.get().supervisor.jobs.move(found.id, "stopped", reason="you")
            return "Run cancelled. Nothing was uploaded."
        try:
            trainer.cancel(found.id)
        except RemoteError:
            raise ClientPlatformError("learning_hugging_face_unreachable") from None
        return "Cancelled on Hugging Face. Billing has stopped, and the uploaded data is being deleted."
    if action == "hf_check_now":
        trainer.check_now(found.id)
        return "Checked Hugging Face."
    raise ClientPlatformError("invalid_learning_command")


def _set_aside_planned() -> None:
    """A run planned for another model that never started is set aside (its rows stay for the next plan)."""

    job = _current_job()
    if job is not None and job.state in ("planned", "waiting"):
        from row_bot.learning import runner as runner_module

        runner_module.get().supervisor.jobs.move(job.id, "stopped", reason="model_changed")


def _save(values: dict[str, Any]) -> None:
    from row_bot.application.settings_commands import SettingsCommandError, write_learning_settings

    try:
        write_learning_settings(values)
    except SettingsCommandError as error:
        raise ClientPlatformError(
            "learning_settings_unavailable" if error.code == "settings_unavailable" else "learning_save_unconfirmed"
        ) from None


__all__ = ["ACTIONS", "CAP_CHOICES", "available", "failure", "perform", "run_view", "view"]
