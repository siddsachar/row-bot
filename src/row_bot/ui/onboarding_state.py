"""Onboarding progress helpers for the first-run wizard and Setup Center."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from row_bot.onboarding_catalog import INTENT_OPTIONS, ONBOARDING_VERSION, SETUP_STEPS
from row_bot.ui.helpers import load_app_config


def _clean_list(value: Any, allowed: set[str] | None = None) -> list[str]:
    if not isinstance(value, list):
        return []
    clean: list[str] = []
    for item in value:
        key = str(item or "").strip()
        if not key or key in clean:
            continue
        if allowed is not None and key not in allowed:
            continue
        clean.append(key)
    return clean


def get_onboarding_state() -> dict[str, Any]:
    cfg = load_app_config()
    completed = _clean_list(
        cfg.get("onboarding_completed_steps"),
        set(SETUP_STEPS),
    )
    skipped = _clean_list(
        cfg.get("onboarding_skipped_steps"),
        set(SETUP_STEPS),
    )
    return {
        "version": int(cfg.get("onboarding_version") or 0),
        "profile": _clean_list(cfg.get("onboarding_profile"), set(INTENT_OPTIONS)),
        "completed_steps": completed,
        "skipped_steps": skipped,
        "dismissed_home_card": bool(cfg.get("onboarding_dismissed_home_card")),
        "last_seen": str(cfg.get("onboarding_last_seen") or ""),
        "setup_complete": bool(cfg.get("setup_complete")),
    }


def save_onboarding_profile(profile: list[str]) -> None:
    from row_bot.application.client_onboarding import update_onboarding_config

    def change(cfg: dict[str, Any]) -> None:
        cfg["onboarding_version"] = ONBOARDING_VERSION
        cfg["onboarding_profile"] = _clean_list(profile, set(INTENT_OPTIONS))
        cfg["onboarding_last_seen"] = datetime.now().isoformat()

    update_onboarding_config(change)


def request_setup_center_on_next_load() -> None:
    from row_bot.application.client_onboarding import update_onboarding_config

    def change(cfg: dict[str, Any]) -> None:
        cfg["onboarding_open_setup_center_on_next_load"] = True
        cfg["onboarding_last_seen"] = datetime.now().isoformat()

    update_onboarding_config(change)


def consume_setup_center_on_next_load() -> bool:
    from row_bot.application.client_onboarding import update_onboarding_config

    should_open = False

    def change(cfg: dict[str, Any]) -> bool | None:
        nonlocal should_open
        should_open = bool(cfg.get("onboarding_open_setup_center_on_next_load"))
        if should_open:
            cfg["onboarding_open_setup_center_on_next_load"] = False
            cfg["onboarding_last_seen"] = datetime.now().isoformat()
        return None if should_open else False

    update_onboarding_config(change)
    return should_open


def mark_onboarding_step(step: str, *, skipped: bool = False) -> None:
    if step not in SETUP_STEPS:
        raise ValueError(f"Unknown onboarding step: {step}")
    from row_bot.application.client_onboarding import update_onboarding_config

    def change(cfg: dict[str, Any]) -> None:
        completed = _clean_list(cfg.get("onboarding_completed_steps"), set(SETUP_STEPS))
        skipped_steps = _clean_list(cfg.get("onboarding_skipped_steps"), set(SETUP_STEPS))
        if skipped:
            if step not in skipped_steps:
                skipped_steps.append(step)
            if step in completed:
                completed.remove(step)
        else:
            if step not in completed:
                completed.append(step)
            if step in skipped_steps:
                skipped_steps.remove(step)
        cfg["onboarding_version"] = ONBOARDING_VERSION
        cfg["onboarding_completed_steps"] = completed
        cfg["onboarding_skipped_steps"] = skipped_steps
        cfg["onboarding_last_seen"] = datetime.now().isoformat()

    update_onboarding_config(change)


def dismiss_onboarding_home_card() -> None:
    from row_bot.application.client_onboarding import update_onboarding_config

    def change(cfg: dict[str, Any]) -> None:
        cfg["onboarding_dismissed_home_card"] = True
        cfg["onboarding_last_seen"] = datetime.now().isoformat()

    update_onboarding_config(change)


def reset_onboarding_home_card() -> None:
    from row_bot.application.client_onboarding import update_onboarding_config

    update_onboarding_config(lambda cfg: cfg.update(onboarding_dismissed_home_card=False))


def onboarding_progress() -> dict[str, Any]:
    state = get_onboarding_state()
    actionable = set(state["completed_steps"]) | set(state["skipped_steps"])
    total = len(SETUP_STEPS)
    done = len(actionable)
    return {
        **state,
        "done": done,
        "total": total,
        "percent": int((done / total) * 100) if total else 100,
        "complete": done >= total,
        "remaining_steps": [
            step for step in SETUP_STEPS
            if step not in actionable
        ],
    }
