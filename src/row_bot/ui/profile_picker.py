"""Reusable thread Agent Profile picker for chat-like surfaces."""

from __future__ import annotations

import logging
from typing import Callable

from nicegui import ui

from row_bot.ui.state import AppState, P

logger = logging.getLogger(__name__)

_DEFAULT_VALUE = ""


def _profile_skills(profile: dict | None) -> list[str]:
    if not profile:
        return []
    skill_policy = profile.get("skill_policy_json") or {}
    if not isinstance(skill_policy, dict):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in skill_policy.get("skills_override") or []:
        name = str(item or "").strip()
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _profile_options() -> tuple[dict[str, str], dict[str, dict]]:
    try:
        from row_bot.agent_profiles import list_agent_profiles

        profiles = list_agent_profiles(enabled_only=True, include_builtins=True)
    except Exception:
        logger.debug("Could not load Agent Profiles for picker", exc_info=True)
        return {_DEFAULT_VALUE: "Default"}, {}
    options = {_DEFAULT_VALUE: "Default"}
    by_value: dict[str, dict] = {}
    for profile in profiles:
        value = str(profile.get("id") or profile.get("slug") or "")
        if not value:
            continue
        display = str(profile.get("display_name") or profile.get("slug") or value)
        slug = str(profile.get("slug") or "")
        label = f"{display} ({slug})" if slug and slug != display else display
        options[value] = label
        by_value[value] = profile
    return options, by_value


def _apply_profile_picker_selection(
    thread_id: str,
    value: str,
    *,
    profiles_by_value: dict[str, dict] | None = None,
) -> dict:
    from row_bot.agent import clear_agent_cache
    from row_bot.agent_profiles import get_agent_profile
    from row_bot.threads import (
        _clear_thread_agent_profile,
        _set_thread_agent_profile,
        set_thread_skills_override,
    )

    thread_id = str(thread_id or "").strip()
    value = str(value or "").strip()
    if not thread_id:
        raise ValueError("A thread id is required to set an Agent Profile.")
    if not value:
        _clear_thread_agent_profile(thread_id)
        set_thread_skills_override(thread_id, None)
        clear_agent_cache()
        return {"cleared": True, "stored": {"id": "", "slug": ""}, "profile": None}

    stored = _set_thread_agent_profile(thread_id, value)
    profile_lookup = profiles_by_value if isinstance(profiles_by_value, dict) else {}
    profile = profile_lookup.get(value) or get_agent_profile(
        str(stored.get("id") or stored.get("slug") or value),
        enabled_only=False,
    )
    profile_skills = _profile_skills(profile)
    set_thread_skills_override(thread_id, profile_skills or None)
    clear_agent_cache()
    return {"cleared": False, "stored": stored, "profile": profile}


def build_profile_picker(
    state: AppState,
    *,
    p: P | None = None,
    rebuild_main: Callable[..., None] | None = None,
    rebuild_thread_list: Callable[[], None] | None = None,
    label: str = "Profile",
    surface: str = "chat",
) -> None:
    """Render a compact picker for the current thread's Agent Profile."""

    thread_id = str(getattr(state, "thread_id", "") or "")
    if not thread_id:
        return

    options, profiles_by_value = _profile_options()

    try:
        from row_bot.agent_profiles import get_agent_profile
        from row_bot.threads import _get_thread_agent_profile

        pointer = _get_thread_agent_profile(thread_id)
        ref = str(pointer.get("id") or pointer.get("slug") or "")
        if ref:
            current_profile = get_agent_profile(ref, enabled_only=False)
            if current_profile is None:
                current_value = ref
                current_state = "missing"
                options.setdefault(ref, f"Missing: {ref}")
            elif not current_profile.get("enabled", True):
                current_value = str(current_profile.get("id") or current_profile.get("slug") or ref)
                current_state = "disabled"
                options.setdefault(
                    current_value,
                    f"Disabled: {current_profile.get('display_name') or current_profile.get('slug')}",
                )
            else:
                current_value = str(current_profile.get("id") or current_profile.get("slug") or ref)
                current_state = "active"
                profiles_by_value.setdefault(current_value, current_profile)
        else:
            current_value = _DEFAULT_VALUE
            current_state = "none"
    except Exception:
        logger.debug("Could not load current Agent Profile for picker", exc_info=True)
        current_value = _DEFAULT_VALUE
        current_state = "error"

    with ui.row().classes("items-center gap-1 no-wrap").style("min-width: 0;"):
        picker = ui.select(
            options=options,
            value=current_value if current_value in options else _DEFAULT_VALUE,
            label=label,
        ).props("dense outlined options-dense hide-bottom-space").classes(
            "text-xs row-bot-profile-picker"
        ).style(
            "min-width: 150px; max-width: 260px;"
        ).tooltip(
            "Choose the Agent Profile for this thread"
            if surface != "developer"
            else "Choose a behavior profile; Developer workspace permissions still apply"
        )

        if current_state in {"missing", "disabled", "error"}:
            badge_label = {
                "missing": "missing",
                "disabled": "disabled",
                "error": "error",
            }.get(current_state, "profile")
            ui.badge(badge_label, color="warning" if current_state == "disabled" else "negative").props(
                "outline dense"
            ).classes("text-xs")

        def _refresh_surfaces() -> None:
            if rebuild_thread_list is not None:
                rebuild_thread_list()
            if rebuild_main is not None:
                try:
                    rebuild_main()
                except TypeError:
                    rebuild_main(reason="profile_picker")

        def _on_pick(e) -> None:
            value = str(e.value or "")
            try:
                result = _apply_profile_picker_selection(
                    thread_id,
                    value,
                    profiles_by_value=profiles_by_value,
                )
                if result.get("cleared"):
                    ui.notify("Agent Profile cleared", type="info")
                else:
                    stored = result.get("stored") or {}
                    profile = result.get("profile")
                    display = (
                        str(profile.get("display_name") or profile.get("slug"))
                        if profile
                        else str(stored.get("slug") or value)
                    )
                    ui.notify(f"Agent Profile: {display}", type="info")
                _refresh_surfaces()
            except Exception as exc:
                ui.notify(f"Could not set Agent Profile: {exc}", type="negative")
                picker.value = current_value if current_value in options else _DEFAULT_VALUE
                picker.update()

        picker.on_value_change(_on_pick)

        selected_profile = profiles_by_value.get(current_value)
        if selected_profile is not None:
            def _view_profile() -> None:
                from row_bot.ui.profile_library import open_profile_view_dialog

                open_profile_view_dialog(
                    selected_profile,
                    state=state,
                    p=p,
                    rebuild_main=rebuild_main,
                    rebuild_thread_list=rebuild_thread_list,
                    on_refresh=_refresh_surfaces,
                )

            ui.button(icon="visibility", on_click=_view_profile).props(
                "flat dense round size=xs"
            ).tooltip("View Profile")
