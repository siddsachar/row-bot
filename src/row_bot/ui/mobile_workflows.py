"""Mobile-native workflow list and simple workflow editor."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable

from nicegui import ui

from row_bot.agent_profiles import list_agent_profiles
from row_bot.providers.selection import list_model_choice_options
from row_bot.tasks import (
    DEFAULT_WORKFLOW_AGENT_PROFILE_ID,
    _prepare_task_thread,
    create_task,
    get_global_approval_mode,
    get_running_task_thread,
    list_tasks,
    run_task_background,
    update_task,
)
from row_bot.ui.iconography import icon_select_options, material_icon_for
from row_bot.ui.state import AppState, P

logger = logging.getLogger(__name__)


_SCHEDULE_OPTIONS = ["Manual", "Daily", "Weekly", "Interval (hrs)", "Interval (min)", "Cron"]
_DAY_OPTIONS = {
    "mon": "Monday",
    "tue": "Tuesday",
    "wed": "Wednesday",
    "thu": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
    "sun": "Sunday",
}


def _safe_call(label: str, fn: Callable[[], Any], fallback: Any) -> Any:
    try:
        return fn()
    except Exception as exc:
        logger.warning("Mobile workflow %s unavailable: %s", label, exc)
        return fallback


def _parse_schedule(value: str | None) -> dict[str, str]:
    raw = str(value or "")
    state = {
        "mode": "Manual",
        "time": "08:00",
        "day": "mon",
        "interval": "1",
        "cron": "",
    }
    if raw.startswith("daily:"):
        state["mode"] = "Daily"
        state["time"] = raw.split(":", 1)[1] or "08:00"
    elif raw.startswith("weekly:"):
        state["mode"] = "Weekly"
        parts = raw.split(":")
        if len(parts) >= 3:
            state["day"] = parts[1] or "mon"
            state["time"] = f"{parts[2]}:{parts[3]}" if len(parts) >= 4 else parts[2]
    elif raw.startswith("interval_minutes:"):
        state["mode"] = "Interval (min)"
        state["interval"] = raw.split(":", 1)[1] or "30"
    elif raw.startswith("interval:"):
        state["mode"] = "Interval (hrs)"
        state["interval"] = raw.split(":", 1)[1] or "1"
    elif raw.startswith("cron:"):
        state["mode"] = "Cron"
        state["cron"] = raw.split(":", 1)[1]
    return state


def _schedule_from_inputs(
    mode: str,
    *,
    time_value: str,
    day_value: str,
    interval_value: str,
    cron_value: str,
) -> str | None:
    if mode == "Daily":
        return f"daily:{(time_value or '08:00').strip()}"
    if mode == "Weekly":
        return f"weekly:{day_value or 'mon'}:{(time_value or '08:00').strip()}"
    if mode == "Interval (hrs)":
        return f"interval:{(interval_value or '1').strip()}"
    if mode == "Interval (min)":
        return f"interval_minutes:{(interval_value or '30').strip()}"
    if mode == "Cron":
        cron = str(cron_value or "").strip()
        return f"cron:{cron}" if cron else None
    return None


def _profile_options() -> dict[str, str]:
    options: dict[str, str] = {}
    for profile in _safe_call(
        "agent profiles",
        lambda: list_agent_profiles(enabled_only=True, include_builtins=True),
        [],
    ):
        profile_id = str(profile.get("id") or "")
        if profile_id:
            options[profile_id] = str(profile.get("display_name") or profile.get("slug") or profile_id)
    if DEFAULT_WORKFLOW_AGENT_PROFILE_ID not in options:
        options[DEFAULT_WORKFLOW_AGENT_PROFILE_ID] = "Default"
    return options


def _model_options(current_value: str) -> dict[str, str]:
    options: dict[str, str] = {"__default__": "Default"}
    include_values = [current_value] if current_value else []
    for option in _safe_call(
        "model options",
        lambda: list_model_choice_options("workflow", include_values=include_values, include_inactive=True),
        [],
    ):
        value = str(option.get("value") or "")
        if value:
            options[value] = str(option.get("label") or value)
    if current_value and current_value not in options:
        options[current_value] = current_value
    return options


def _channel_options() -> dict[str, str]:
    from row_bot.channels import registry as channel_registry

    return {
        channel.name: channel.display_name
        for channel in _safe_call("configured channels", channel_registry.configured_channels, [])
    }


def _channel_mode_and_value(task: dict | None, channel_options: dict[str, str]) -> tuple[str, list[str]]:
    raw_channels = task.get("channels") if task else None
    if task and raw_channels is None and task.get("delivery_channel"):
        raw_channels = [task.get("delivery_channel")]
    selected = [str(name) for name in (raw_channels or []) if str(name) in channel_options]
    if raw_channels is None:
        return "Use workflow default", selected
    if selected:
        return "Custom channels", selected
    return "Web app only", []


def _channels_from_inputs(mode: str, selected: list[str]) -> list[str] | None:
    if mode == "Web app only":
        return []
    if mode == "Custom channels":
        return selected
    return None


def _is_advanced_workflow(task: dict | None) -> bool:
    if not task:
        return False
    steps = task.get("steps") or []
    return bool(task.get("advanced_mode")) or any(step.get("type") not in ("prompt", None) for step in steps)


def open_mobile_workflow_editor(
    task: dict | None,
    on_done: Callable[[], None],
    *,
    state: AppState,
    p: P,
) -> None:
    """Open a full-screen mobile workflow editor."""
    is_new = task is None
    title = "New workflow" if is_new else "Edit workflow"
    advanced = _is_advanced_workflow(task)

    name_value = str(task.get("name") or "") if task else ""
    icon_value = material_icon_for(task.get("icon") if task else "bolt")
    desc_value = str(task.get("description") or "") if task else ""
    enabled_value = bool(task.get("enabled", True)) if task else True
    approval_value = str(task.get("safety_mode") or get_global_approval_mode()) if task else "block"
    model_value = str(task.get("model_override") or "") if task else ""
    profile_value = str(task.get("agent_profile_id") or DEFAULT_WORKFLOW_AGENT_PROFILE_ID) if task else DEFAULT_WORKFLOW_AGENT_PROFILE_ID
    persistent_value = bool(task.get("persistent_thread_id")) if task else False
    prompts_value = list(task.get("prompts") or [""]) if task else [""]
    if not prompts_value:
        prompts_value = [""]
    schedule_state = _parse_schedule(str(task.get("schedule") or "") if task else "")
    channel_options = _channel_options()
    channel_mode_value, channels_value = _channel_mode_and_value(task, channel_options)

    p.task_dlg.clear()
    with p.task_dlg:
        with ui.card().classes("row-bot-mobile-workflow-editor w-full h-full no-shadow").props(
            "data-docs-id=mobile-workflow-editor"
        ):
            with ui.row().classes("row-bot-mobile-editor-header w-full items-center gap-2 no-wrap"):
                ui.button(icon="close", on_click=p.task_dlg.close).props("flat dense round").tooltip("Close")
                with ui.column().classes("gap-0").style("min-width: 0; flex: 1;"):
                    ui.label(title).classes("text-subtitle1 ellipsis")
                    ui.label("Simple mobile editor").classes("text-grey-6 text-xs")

                def _save() -> None:
                    cur_name = str(name_input.value or "").strip()
                    if not cur_name:
                        ui.notify("Name is required.", type="warning")
                        return
                    cur_prompts = [str(inp.value or "").strip() for inp in prompt_inputs if str(inp.value or "").strip()]
                    if is_new and not cur_prompts:
                        ui.notify("Add at least one prompt step.", type="warning")
                        return
                    cur_model = str(model_select.value or "")
                    cur_model = None if cur_model == "__default__" else cur_model
                    cur_channels = _channels_from_inputs(str(channel_mode_select.value or ""), list(channel_select.value or []))
                    cur_schedule = _schedule_from_inputs(
                        str(schedule_select.value or "Manual"),
                        time_value=str(time_input.value or ""),
                        day_value=str(day_select.value or "mon"),
                        interval_value=str(interval_input.value or ""),
                        cron_value=str(cron_input.value or ""),
                    )
                    try:
                        if is_new:
                            persistent_thread_id = f"pt_{uuid.uuid4().hex[:10]}" if persistent_switch.value else None
                            create_task(
                                name=cur_name,
                                prompts=cur_prompts,
                                description=str(desc_input.value or "").strip(),
                                icon=str(icon_select.value or "bolt"),
                                schedule=cur_schedule,
                                notify_only=False,
                                delivery_channel=None,
                                delivery_target=None,
                                model_override=cur_model,
                                persistent_thread_id=persistent_thread_id,
                                steps=None,
                                safety_mode=str(approval_select.value or "block"),
                                channels=cur_channels,
                                advanced_mode=False,
                                agent_profile_id=str(profile_select.value or DEFAULT_WORKFLOW_AGENT_PROFILE_ID),
                                enabled=bool(enabled_switch.value),
                                apply_default_skills=False,
                            )
                        else:
                            assert task is not None
                            updates: dict[str, Any] = {}
                            if cur_name != str(task.get("name") or ""):
                                updates["name"] = cur_name
                            if str(icon_select.value or "bolt") != str(task.get("icon") or ""):
                                updates["icon"] = str(icon_select.value or "bolt")
                            if str(desc_input.value or "").strip() != str(task.get("description") or ""):
                                updates["description"] = str(desc_input.value or "").strip()
                            if bool(enabled_switch.value) != bool(task.get("enabled", True)):
                                updates["enabled"] = bool(enabled_switch.value)
                            if str(approval_select.value or "block") != str(task.get("safety_mode") or "block"):
                                updates["safety_mode"] = str(approval_select.value or "block")
                            if cur_model != (task.get("model_override") or None):
                                updates["model_override"] = cur_model
                            if str(profile_select.value or DEFAULT_WORKFLOW_AGENT_PROFILE_ID) != str(
                                task.get("agent_profile_id") or DEFAULT_WORKFLOW_AGENT_PROFILE_ID
                            ):
                                updates["agent_profile_id"] = str(profile_select.value or DEFAULT_WORKFLOW_AGENT_PROFILE_ID)
                            if cur_schedule != task.get("schedule"):
                                updates["schedule"] = cur_schedule
                            if cur_channels != task.get("channels"):
                                updates["channels"] = cur_channels
                            had_persistent = bool(task.get("persistent_thread_id"))
                            if bool(persistent_switch.value) != had_persistent:
                                updates["persistent_thread_id"] = (
                                    f"pt_{uuid.uuid4().hex[:10]}" if persistent_switch.value else None
                                )
                            if not advanced and cur_prompts != list(task.get("prompts") or []):
                                updates["prompts"] = cur_prompts
                                updates["steps"] = []
                                updates["advanced_mode"] = False
                            if updates:
                                update_task(task["id"], **updates)
                        ui.notify("Workflow saved", type="positive")
                    except ValueError as exc:
                        ui.notify(str(exc), type="negative")
                        return
                    p.task_dlg.close()
                    on_done()

                ui.button("Save", icon="check", on_click=_save).props("unelevated dense no-caps color=primary")

            with ui.scroll_area().classes("row-bot-mobile-editor-body w-full"):
                with ui.column().classes("w-full gap-3"):
                    if advanced:
                        with ui.element("div").classes("row-bot-mobile-notice"):
                            ui.icon("schema").classes("text-primary")
                            ui.label(
                                "Advanced graph steps are preserved. Mobile edits only safe workflow metadata and prompts."
                            ).classes("text-sm")

                    with ui.element("div").classes("row-bot-mobile-section"):
                        ui.label("Basics").classes("row-bot-mobile-section-title")
                        icon_select = ui.select(
                            options=icon_select_options(icon_value),
                            value=icon_value,
                            label="Icon",
                        ).classes("w-full").props("outlined dense")
                        name_input = ui.input("Name", value=name_value).classes("w-full").props("outlined dense")
                        desc_input = ui.input("Description", value=desc_value).classes("w-full").props("outlined dense")
                        enabled_switch = ui.switch("Enabled", value=enabled_value)

                    with ui.element("div").classes("row-bot-mobile-section"):
                        ui.label("Run behavior").classes("row-bot-mobile-section-title")
                        approval_select = ui.select(
                            options={
                                "block": "Block",
                                "approve": "Ask",
                                "allow_all": "Auto",
                            },
                            value=approval_value,
                            label="Approval mode",
                        ).classes("w-full").props("outlined dense")
                        model_options = _model_options(model_value)
                        model_select = ui.select(
                            options=model_options,
                            value=model_value if model_value in model_options else "__default__",
                            label="Model",
                        ).classes("w-full").props("outlined dense")
                        profile_options = _profile_options()
                        profile_select = ui.select(
                            options=profile_options,
                            value=profile_value if profile_value in profile_options else DEFAULT_WORKFLOW_AGENT_PROFILE_ID,
                            label="Agent profile",
                        ).classes("w-full").props("outlined dense")
                        persistent_switch = ui.switch("Keep conversation history across runs", value=persistent_value)

                    with ui.element("div").classes("row-bot-mobile-section"):
                        ui.label("Prompt steps").classes("row-bot-mobile-section-title")
                        prompt_inputs: list[Any] = []
                        prompt_container = ui.column().classes("w-full gap-2")

                        def _render_prompts() -> None:
                            prompt_container.clear()
                            prompt_inputs.clear()
                            with prompt_container:
                                for index, prompt in enumerate(prompts_value):
                                    with ui.row().classes("w-full items-start gap-2 no-wrap"):
                                        inp = ui.textarea(
                                            f"Step {index + 1}",
                                            value=prompt,
                                        ).classes("w-full").props("outlined autogrow")
                                        prompt_inputs.append(inp)
                                        if len(prompts_value) > 1 and not advanced:
                                            ui.button(
                                                icon="delete",
                                                on_click=lambda i=index: (
                                                    prompts_value.pop(i),
                                                    _render_prompts(),
                                                ),
                                            ).props("flat dense round color=negative")

                        _render_prompts()
                        add_button = ui.button(
                            "Add prompt step",
                            icon="add",
                            on_click=lambda: (prompts_value.append(""), _render_prompts()),
                        ).props("flat dense no-caps color=primary")
                        if advanced:
                            add_button.disable()

                    with ui.element("div").classes("row-bot-mobile-section"):
                        ui.label("Schedule").classes("row-bot-mobile-section-title")
                        schedule_select = ui.select(
                            options=_SCHEDULE_OPTIONS,
                            value=schedule_state["mode"],
                            label="Type",
                        ).classes("w-full").props("outlined dense")
                        time_input = ui.input("Time", value=schedule_state["time"]).classes("w-full").props(
                            'outlined dense mask="##:##" placeholder="HH:MM"'
                        )
                        day_select = ui.select(options=_DAY_OPTIONS, value=schedule_state["day"], label="Day").classes(
                            "w-full"
                        ).props("outlined dense")
                        interval_input = ui.input("Every", value=schedule_state["interval"]).classes("w-full").props(
                            "outlined dense"
                        )
                        cron_input = ui.input("Cron expression", value=schedule_state["cron"]).classes("w-full").props(
                            "outlined dense"
                        )

                        def _sync_schedule_visibility(value: str | None = None) -> None:
                            mode = str(value or schedule_select.value or "Manual")
                            time_input.visible = mode in {"Daily", "Weekly"}
                            day_select.visible = mode == "Weekly"
                            interval_input.visible = mode in {"Interval (hrs)", "Interval (min)"}
                            cron_input.visible = mode == "Cron"

                        schedule_select.on_value_change(lambda e: _sync_schedule_visibility(str(e.value)))
                        _sync_schedule_visibility(schedule_state["mode"])

                    with ui.element("div").classes("row-bot-mobile-section"):
                        ui.label("Channels").classes("row-bot-mobile-section-title")
                        channel_mode_select = ui.select(
                            options=["Use workflow default", "Custom channels", "Web app only"],
                            value=channel_mode_value,
                            label="Delivery mode",
                        ).classes("w-full").props("outlined dense")
                        channel_select = ui.select(
                            options=channel_options,
                            value=channels_value,
                            multiple=True,
                            label="External channels",
                        ).classes("w-full").props("outlined dense use-chips clearable")
                        if not channel_options:
                            channel_select.disable()
                            ui.label("No external channels configured. Web app workflow status still works.").classes(
                                "text-grey-6 text-xs"
                            )

                        def _sync_channel_visibility(value: str | None = None) -> None:
                            channel_select.visible = str(value or channel_mode_select.value) == "Custom channels"

                        channel_mode_select.on_value_change(lambda e: _sync_channel_visibility(str(e.value)))
                        _sync_channel_visibility(channel_mode_value)
    p.task_dlg.open()


def build_mobile_workflows(
    state: AppState,
    p: P,
    *,
    rebuild_main: Callable[..., None],
    rebuild_thread_list: Callable[[], None],
    load_thread_messages: Callable[[str], list[dict]],
) -> None:
    """Build the mobile workflow list."""
    from row_bot.tools import registry as tool_registry

    tasks = _safe_call("workflow list", list_tasks, [])

    def _refresh() -> None:
        rebuild_main(immediate=True, reason="mobile_workflow_editor")

    def _start_task(task: dict) -> None:
        thread_id = get_running_task_thread(task["id"])
        if thread_id:
            ui.notify(f"{task.get('name') or 'Workflow'} is already running.", type="info")
            rebuild_main(immediate=True, reason="mobile_workflow_already_running")
            return
        enabled_tools = [tool.name for tool in tool_registry.get_enabled_tools()]
        thread_id = _prepare_task_thread(task)
        run_task_background(task["id"], thread_id, enabled_tools, notification=True)
        ui.notify(
            f"{task.get('name') or 'Workflow'} started - you'll be notified when done.",
            type="positive",
        )
        rebuild_thread_list()
        rebuild_main(immediate=True, reason="mobile_workflow_started")

    with ui.row().classes("w-full items-center justify-between no-wrap"):
        with ui.column().classes("gap-0").style("min-width: 0;"):
            ui.label("Workflows").classes("text-h6")
            ui.label("Run, create, or edit mobile-friendly workflows.").classes("text-grey-6 text-xs")
        ui.button(
            icon="add",
            on_click=lambda: open_mobile_workflow_editor(None, _refresh, state=state, p=p),
        ).props("unelevated round color=primary").tooltip("New workflow")

    if not tasks:
        with ui.element("div").classes("row-bot-mobile-empty"):
            ui.icon("bolt").classes("text-grey-6")
            ui.label("No workflows yet.").classes("text-subtitle2")
            ui.button(
                "Create workflow",
                icon="add",
                on_click=lambda: open_mobile_workflow_editor(None, _refresh, state=state, p=p),
            ).props("flat dense no-caps color=primary")
        return

    for task in tasks:
        enabled = bool(task.get("enabled", True))
        advanced = _is_advanced_workflow(task)
        running_thread_id = get_running_task_thread(task["id"])
        with ui.element("div").classes("row-bot-mobile-list-card"):
            with ui.row().classes("w-full items-start gap-2 no-wrap"):
                ui.icon(material_icon_for(task.get("icon") or "bolt")).classes(
                    "text-primary" if enabled else "text-grey-6"
                )
                with ui.column().classes("gap-0").style("min-width: 0; flex: 1;"):
                    ui.label(str(task.get("name") or "Workflow")).classes("text-subtitle2 ellipsis")
                    desc = str(task.get("description") or "")
                    if desc:
                        ui.label(desc).classes("text-grey-6 text-xs").style("white-space: pre-wrap;")
                    chips = []
                    chips.append("Enabled" if enabled else "Disabled")
                    if advanced:
                        chips.append("Advanced")
                    if running_thread_id:
                        chips.append("Running")
                    if task.get("schedule"):
                        chips.append("Scheduled")
                    ui.label(" - ".join(chips)).classes("text-grey-6 text-xs")
            with ui.row().classes("w-full justify-end gap-1"):
                ui.button(
                    icon="edit",
                    on_click=lambda t=task: open_mobile_workflow_editor(t, _refresh, state=state, p=p),
                ).props("flat dense round").tooltip("Edit")
                run_button = ui.button(
                    "Running" if running_thread_id else "Run",
                    icon="autorenew" if running_thread_id else "play_arrow",
                    on_click=lambda t=task: _start_task(t),
                ).props("flat dense no-caps color=primary")
                if not enabled or running_thread_id:
                    run_button.disable()
