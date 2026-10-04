"""Monitor's system checks: run by themselves, on request, and kept (B252).

Local checks read this computer's state only; they run shortly after start and
then every 15 minutes. Checks that contact a provider, an account or the
internet run hourly while "Check connections every hour" is on, whenever the
person runs diagnosis, and for the local model right after one of its turns
failed. The last result of every check is kept on disk with its time, without
credential values or private paths, for Monitor, Overview and the attention
indicator. Scheduled runs use the scheduler's worker threads, never a request.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import row_bot.status_checks as status_checks
from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

LOCAL_JOB = "system_health_local"
NETWORK_JOB = "system_health_network"
MODEL_JOB = "system_health_model"
CHANNELS_JOB = "system_health_channels"
# The first runs wait for start-up to settle.
_LOCAL_AFTER_START = timedelta(seconds=30)
_NETWORK_AFTER_START = timedelta(minutes=5)
_NETWORK_EVERY = timedelta(hours=1)
# A result is stale once its schedule has missed about two runs.
_LOCAL_STALE_S = 45 * 60
_NETWORK_STALE_S = 2 * 3600
_FILE = "system_health.json"
_MAX_CHECKS = 64
_STATUSES = {"ok", "warn", "error", "inactive"}
_ID = re.compile(r"[a-z0-9][a-z0-9:-]{0,63}")
# The attention indicator reads these areas from their own, finer readers.
_ATTENTION_ELSEWHERE = ("tunnel", "mcp", "plugins", "channel:")
# What a check says stays readable ("Token expired"); a credential's value, a
# long opaque string and a private path are never kept.
_CREDENTIAL = re.compile(
    r"(?i)\b(?:api[_ -]?key|authorization|password|secret|token)\s*[:=]\s*\S+|\bbearer\s+\S+"
)
_OPAQUE = re.compile(r"\b[A-Za-z0-9_-]{32,}\b")
_PRIVATE_PATH = re.compile(
    r"""(?i)(['"])(?:[A-Z]:\\|/(?:Users|home|root|var|private|tmp|Volumes)/).*?\1"""
    r"""|\b[A-Z]:\\\S*|(?<![\w:/])/(?:Users|home|root|var|private|tmp|Volumes)/\S*"""
)

# Each problem's one fix (Phase 18): where a kept check that warns or fails
# is put right by hand, and what it is called there.
_PLACES: dict[str, tuple[str, str]] = {
    "cloud-api": ("/settings/providers", "Providers"),
    "tunnel": ("/settings/access#tunnel", "Public link"),
    "github": ("/settings/accounts#github", "GitHub"),
    "workflows": ("/?tab=workflows", "Workflows"),
    "knowledge": ("/settings/knowledge#memory-graph", "Memory"),
    "faiss-index": ("/settings/knowledge#memory-graph", "Memory"),
    "dream-cycle": ("/settings/preferences#dream-cycle", "Dream Cycle"),
    "tts": ("/settings/voice#read-aloud", "Read aloud"),
    "wiki-vault": ("/settings/knowledge#wiki-vault", "Wiki vault"),
    "logging": ("/settings/system#logging.level", "Logging"),
    "documents": ("/settings/documents#embedding", "Documents"),
    "search": ("/settings/tools#search-tools", "Search tools"),
    "skills": ("/settings/skills", "Skills"),
    "tracker": ("/settings/tracker#tracker.enabled", "Habit tracker"),
    "buddy": ("/settings/buddy", "Buddy"),
    "mcp": ("/settings/apps", "Apps"),
    "plugins": ("/settings/apps", "Apps"),
    "tools": ("/settings/tools#built-in-tools", "Tools"),
}
# A sign-in Row-Bot can renew from its own row on Settings › Accounts.
_ACCOUNTS = {"gmail-oauth": ("google", "Google"), "calendar-oauth": ("google", "Google"), "x-oauth": ("x", "X")}
# Nothing in Row-Bot fixes these; once they are fixed outside it, check again.
_CHECK_AGAIN = {"ollama": "/settings/providers", "network": None, "disk": None, "threads-db": None}

_lock = threading.Lock()
# The app's scheduler, once start-up has scheduled the checks.
_scheduler: Any = None

Checks = tuple[Callable[[], Any], ...]


def _path() -> Path:
    return get_row_bot_data_dir(create=False) / _FILE


def _safe_detail(value: object) -> str:
    text = _CREDENTIAL.sub("[credential hidden]", str(value or ""))
    text = _OPAQUE.sub("[hidden]", text)
    return _PRIVATE_PATH.sub("[private path]", text)[:512]


def _kept_entry(check_id: object, value: object) -> dict[str, Any] | None:
    if not (isinstance(check_id, str) and _ID.fullmatch(check_id) and isinstance(value, dict)):
        return None
    checked_at = value.get("checked_at")
    if not (
        isinstance(value.get("name"), str)
        and value.get("status") in _STATUSES
        and isinstance(value.get("detail"), str)
        and isinstance(checked_at, (int, float)) and not isinstance(checked_at, bool)
        and isinstance(value.get("settings_tab"), str)
        and isinstance(value.get("network"), bool)
    ):
        return None
    return {
        "name": value["name"][:128],
        "status": value["status"],
        "detail": value["detail"][:512],
        "checked_at": float(checked_at),
        "settings_tab": value["settings_tab"][:64],
        "network": value["network"],
    }


def _load() -> dict[str, Any]:
    state: dict[str, Any] = {"hourly_network_checks": True, "checks": {}}
    try:
        saved = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return state
    except (OSError, UnicodeError, ValueError):
        logger.warning("Monitor's kept check results could not be read", exc_info=True)
        return state
    if not isinstance(saved, dict):
        return state
    state["hourly_network_checks"] = saved.get("hourly_network_checks") is not False
    checks = saved.get("checks")
    if isinstance(checks, dict):
        for check_id, value in list(checks.items())[:_MAX_CHECKS]:
            entry = _kept_entry(check_id, value)
            if entry is not None:
                state["checks"][check_id] = entry
    return state


def _save(state: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{_FILE}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(state, output)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _results(checks: Checks, *, network: bool) -> dict[str, dict[str, Any]]:
    return {
        check_id: {
            "name": str(result.name)[:128],
            "status": result.status if result.status in _STATUSES else "error",
            "detail": _safe_detail(result.detail),
            "checked_at": float(result.checked_at),
            "settings_tab": str(result.settings_tab)[:64],
            "network": network,
        }
        for check_id, result in status_checks.run_checks(checks)
    }


def _record(results: dict[str, dict[str, Any]], *, replace: bool = False) -> None:
    """Keep each result unless a newer one for that check is already kept.
    ``replace`` (a full diagnosis) also drops checks that no longer exist."""
    with _lock:
        state = _load()
        checks = {} if replace else state["checks"]
        for check_id, entry in results.items():
            kept = checks.get(check_id)
            if kept is None or kept["checked_at"] <= entry["checked_at"]:
                checks[check_id] = entry
        state["checks"] = dict(list(checks.items())[-_MAX_CHECKS:])
        _save(state)


def _fix(kind: str, href: str | None, name: str, target: str | None = None) -> dict[str, Any]:
    return {"kind": kind, "href": href, "target": target, "name": name[:128]}


def channel_id(slug: str) -> str:
    """The channel a kept check names by its display name's slug, as the
    channel registry has it; the slug itself when the registry isn't loaded."""
    registry = sys.modules.get("row_bot.channels.registry")
    try:
        for channel in registry.all_channels() if registry is not None else ():
            label = str(getattr(channel, "display_name", "") or channel.name)
            if re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:48] == slug:
                return str(channel.name)
    except Exception:
        logger.debug("Channel registry unreadable for a Monitor fix", exc_info=True)
    return slug


def fix_for_check(check_id: str, entry: dict[str, Any]) -> dict[str, Any] | None:
    """The one fix for a kept check that warns or fails (Phase 18), or None."""
    if entry["status"] not in {"warn", "error"}:
        return None
    name = entry["name"]
    if check_id.startswith("channel:"):
        channel = channel_id(check_id.removeprefix("channel:"))
        href = f"/settings/channels#{channel}"
        if entry["status"] == "warn" and entry["detail"] == "Stopped":
            return _fix("restart_channel", href, name, channel)
        return _fix("open", href, name)
    if check_id == "model":
        return _fix("choose_model", "/settings/models#default-model", "Default model")
    if check_id in _ACCOUNTS:
        account, label = _ACCOUNTS[check_id]
        return _fix("reconnect_account", f"/settings/accounts#{account}", label, account)
    if check_id in _PLACES:
        href, label = _PLACES[check_id]
        return _fix("open", href, label)
    if check_id in _CHECK_AGAIN:
        return _fix("check_again", _CHECK_AGAIN[check_id], name)
    # A check this table doesn't know yet: its settings page, else check again.
    tab = entry["settings_tab"].lower()
    if re.fullmatch(r"[a-z]{2,32}", tab):
        return _fix("open", f"/settings/{tab}", entry["settings_tab"])
    return _fix("check_again", None, name)


def read_system_health(*, now: float | None = None) -> dict[str, Any]:
    """The last result of every check, as Monitor and Overview show it."""
    now = time.time() if now is None else now
    state = _load()
    ordered = sorted(
        state["checks"].items(),
        key=lambda item: status_checks.status_result_order_key(
            status_checks.CheckResult(item[1]["name"], item[1]["status"])
        ),
    )
    return {
        "schema_version": 1,
        "hourly_network_checks": state["hourly_network_checks"],
        "checks": [
            {
                "id": check_id,
                **entry,
                "stale": now - entry["checked_at"]
                > (_NETWORK_STALE_S if entry["network"] else _LOCAL_STALE_S),
                "fix": fix_for_check(check_id, entry),
            }
            for check_id, entry in ordered
        ],
    }


def run_system_diagnosis() -> dict[str, Any]:
    """Run every check now, on the person's request, and keep the results."""
    _record(
        {
            **_results(status_checks.LOCAL_CHECKS, network=False),
            **_results(status_checks.NETWORK_CHECKS, network=True),
        },
        replace=True,
    )
    return read_system_health()


def run_local_checks() -> None:
    """The scheduled local run; the slow checks wait while Row-Bot is busy."""
    from row_bot.memory_extraction import is_app_idle

    idle = is_app_idle(0)
    checks = tuple(
        fn for fn in status_checks.LOCAL_CHECKS if idle or fn not in status_checks.SLOW_CHECKS
    )
    _record(_results(checks, network=False))


def run_connection_checks() -> None:
    """The hourly run; nothing while "Check connections every hour" is off."""
    if not _load()["hourly_network_checks"]:
        return
    _record(_results(status_checks.NETWORK_CHECKS, network=True))


def _sync_connection_job(now: datetime) -> None:
    if _scheduler is None:
        return
    state = _load()
    if not state["hourly_network_checks"]:
        if _scheduler.get_job(NETWORK_JOB) is not None:
            _scheduler.remove_job(NETWORK_JOB)
        return
    newest = max(
        (entry["checked_at"] for entry in state["checks"].values() if entry["network"]),
        default=None,
    )
    first = now + _NETWORK_AFTER_START
    if newest is not None:
        first = max(first, datetime.fromtimestamp(newest) + _NETWORK_EVERY)
    _scheduler.add_job(
        run_connection_checks, trigger="interval", hours=1, id=NETWORK_JOB,
        replace_existing=True, coalesce=True, max_instances=1, next_run_time=first,
    )


def schedule_health_checks(scheduler: Any, *, now: datetime | None = None) -> None:
    """At start-up: the local checks soon and then every 15 minutes, and the
    connection checks hourly while their switch is on."""
    global _scheduler
    _scheduler = scheduler
    now = now or datetime.now()
    scheduler.add_job(
        run_local_checks, trigger="interval", minutes=15, id=LOCAL_JOB,
        replace_existing=True, coalesce=True, max_instances=1,
        next_run_time=now + _LOCAL_AFTER_START,
    )
    _sync_connection_job(now)


def connection_checks_enabled() -> bool:
    """Whether background checks may contact providers, accounts and the internet."""
    return bool(_load()["hourly_network_checks"])


def set_hourly_network_checks(enabled: bool) -> dict[str, Any]:
    """Save "Check connections every hour" and schedule accordingly."""
    with _lock:
        state = _load()
        state["hourly_network_checks"] = enabled
        _save(state)
    _sync_connection_job(datetime.now())
    return read_system_health()


def recheck_after_failed_turn(model_ref: str) -> None:
    """A turn on the local model failed: check its server again now. (Only
    the local model has a reachability check; cloud turns explain their own
    errors.)"""
    from row_bot.providers.selection import parse_model_ref

    if _scheduler is None or (parse_model_ref(model_ref) or ("",))[0] != "ollama":
        return
    _scheduler.add_job(_recheck_local_model, id=MODEL_JOB, replace_existing=True)


def _recheck_local_model() -> None:
    _record(_results((status_checks.check_ollama,), network=True))


def recheck_channels() -> None:
    """A channel was started or stopped (from Monitor's fix or Settings):
    check the channels again now, so their kept result and its fix follow."""
    if _scheduler is not None:
        _scheduler.add_job(_recheck_channels, id=CHANNELS_JOB, replace_existing=True)


def _recheck_channels() -> None:
    _record(_results((status_checks.check_channels,), network=False))


def attention_problems() -> list[dict[str, str]]:
    """A kept check whose last result is an error needs the person."""
    return [
        {
            "id": f"health:{check['id']}",
            "title": f"{check['name']} needs attention",
            "detail": check["detail"],
            "place": "health",
            "fix": check["fix"],
        }
        for check in read_system_health()["checks"]
        if check["status"] == "error" and not check["id"].startswith(_ATTENTION_ELSEWHERE)
    ]
