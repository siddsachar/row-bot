"""The settings Row-Bot may change for the person: one list, one path (self system).

Each setting says what it is called in Settings and where its row is, how risky a change is (its tier),
what values it takes, and how a change is checked and saved by the owner that saves it from Settings.
A change is always checked first, so an invalid value never reaches an approval card; then the chat's
approval level decides who approves it (``tools.approval_gate``):

- **preference** and **capability** (turning a tool on): the person under Ask, the reviewer first under
  Auto, nobody under Hands-off (recorded); Block refuses.
- **security** (turning on Shell, file tools, Browser, Computer use or the MCP client): under Auto only the
  person approves it, whatever the reviewer would say.
- **repair** (running the Dream Cycle now): as a preference.

Everything else (approval levels, keys and sign-ins, devices and remote access, install stats, Learning,
data-folder access, updates, the workspace folder, appearance, downloads and installs, anything that
deletes) is not here: Row-Bot shows the row instead.

A saved change's result ends with the call that undoes it, so the person's Undo and Row-Bot itself
restore exactly what was there.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

Tier = Literal["preference", "capability", "security", "repair"]
TOOL = "row_bot_update_setting"
UNDO_PREFIX = f"Undo: {TOOL} "
BLOCKED = "BLOCKED: Changing Row-Bot settings is disabled in Block approval mode."

# What the Auto reviewer is told about each tier; the security one is the person's alone under Auto.
_PREFERENCE_RISK = "changes one of the person's Row-Bot settings, which they can undo"
_CAPABILITY_RISK = "turns on a Row-Bot tool the request needs"
_REPAIR_RISK = "runs Row-Bot maintenance on the person's own data"

# Tools that act on this computer: under Auto only the person turns them on.
SECURITY_TOOLS = frozenset({"shell", "filesystem", "browser", "computer_use", "mcp"})
# Row-Bot never turns these off: without them it can't turn anything back on.
_KEPT_ON = frozenset({"row_bot_status"})
_ON = frozenset({"on", "true", "yes", "enable", "enabled", "1"})
_OFF = frozenset({"off", "false", "no", "disable", "disabled", "0"})
_LOG_LEVELS = {"debug": "DEBUG", "info": "INFO", "warning": "WARNING", "error": "ERROR"}


class SettingRefused(ValueError):
    """A change that can't be made as asked: the message says why, and nobody is asked to approve it."""


@dataclass(frozen=True)
class Value:
    canonical: str  # What row_bot_update_setting takes back (an undo passes this).
    words: str  # How the person reads it on a card and a step.


@dataclass(frozen=True)
class Change:
    """A checked change, ready for its approval."""

    setting: str
    label: str
    where: str  # "Settings › Preferences › Dream Cycle" ("" when it has no row).
    href: str  # The row in the app ("" when it has no row).
    before: Value | None  # None for an action (run the Dream Cycle now).
    after: Value
    tier: Tier
    title: str
    description: str
    apply: Callable[[], str]
    cancelled: str
    setup: dict | None = None


@dataclass(frozen=True)
class Setting:
    id: str
    label: str
    page: str  # The Settings page's id ("" for the chat's own model).
    page_title: str
    anchor: str
    tier: Tier
    takes: str  # What values it takes, in words, for the model.
    prepare: Callable[["Setting", str], Change]
    current: Callable[[], Value] | None = None

    @property
    def href(self) -> str:
        if not self.page:
            return ""
        return f"/settings/{self.page}" + (f"#{self.anchor}" if self.anchor else "")

    @property
    def where(self) -> str:
        return f"Settings › {self.page_title} › {self.label}" if self.page else ""


def _on_off(value: str) -> bool:
    text = value.strip().lower()
    if text in _ON:
        return True
    if text in _OFF:
        return False
    raise SettingRefused(f"Use 'on' or 'off', not '{value}'.")


def _switch(on: bool) -> Value:
    return Value("on" if on else "off", "On" if on else "Off")


def _change(setting: Setting, before: Value | None, after: Value, apply: Callable[[], str], *,
            title: str = "", description: str = "", cancelled: str = "") -> Change:
    words = f"{before.words} → {after.words}" if before is not None else after.words
    return Change(setting.id, setting.label, setting.where, setting.href, before, after, setting.tier,
                  title or f"Change {setting.label}", description or f"{setting.label}: {words}", apply,
                  cancelled or f"{setting.label} wasn't changed.")


# ── Saving through the Settings service (the same validation and write as the Settings page) ────


def _snapshot() -> dict:
    from row_bot.application.settings_snapshot import read_settings_snapshot

    return read_settings_snapshot(validate=lambda: None)


def _save(page: str, field_name: str, value: object) -> None:
    from row_bot.application.settings_commands import apply_settings_change

    apply_settings_change(page, field_name, value)


def _clear_agent_cache() -> None:
    from row_bot.agent import clear_agent_cache

    clear_agent_cache()


# ── Models ────────────────────────────────────────────────────────────────────


def _runtime_context() -> dict:
    try:
        from row_bot.agent import get_active_runtime_context

        context = get_active_runtime_context()
    except Exception:
        return {}
    return dict(context) if isinstance(context, dict) else {}


def _chat_model_words(ref: str) -> str:
    if not ref:
        return "not chosen"
    from row_bot.providers.selection import pinned_model_choice_summaries

    for choice in pinned_model_choice_summaries("chat", include_inactive=True):
        if choice.get("canonical_ref") == ref and choice.get("display_name"):
            return f"{choice['display_name']} ({ref})"
    return ref


def _refuse_child_model_switch() -> None:
    if str(_runtime_context().get("runtime_surface") or "") in {"agent_child", "agent_child_resume"}:
        raise SettingRefused(
            "Child Agents cannot switch their own runtime model. Ask the parent to spawn the child with "
            "delegate_work(model=...) or use /agent --model=model:provider:model-id for explicit command spawns."
        )


def _resolve_chat_model(value: str) -> str:
    from row_bot.providers.selection import resolve_catalog_model_selection

    if not value.strip():
        raise SettingRefused("No model was given. Choose one of the pinned Brain models (row_bot_status 'model').")
    try:
        return resolve_catalog_model_selection(value, surface="chat", require_agent_ready=True,
                                               require_pinned=True).ref
    except Exception as exc:
        raise SettingRefused(str(exc)) from None


def _main_model_now() -> Value:
    from row_bot.models import get_current_model

    ref = get_current_model() or ""
    return Value(ref, _chat_model_words(ref))


def _prepare_default_model(setting: Setting, value: str, *, legacy: bool = False) -> Change:
    _refuse_child_model_switch()
    ref = _resolve_chat_model(value)

    def apply() -> str:
        from row_bot.models import set_model

        set_model(ref)
        _clear_agent_cache()
        return f"{'Active model' if legacy else 'Global default model'} changed to: {ref}"

    return _change(setting, _main_model_now(), Value(ref, _chat_model_words(ref)), apply,
                   title="Change the main model",
                   cancelled="Model change cancelled." if legacy else "Default model change cancelled.")


def _thread_id() -> str:
    return str(_runtime_context().get("thread_id") or "").strip()


def _chat_model_now() -> Value:
    from row_bot.threads import _get_thread_model_override

    thread_id = _thread_id()
    ref = _get_thread_model_override(thread_id) if thread_id else ""
    if not ref:
        return Value("default", f"the main model, {_main_model_now().words}")
    return Value(ref, _chat_model_words(ref))


def _prepare_thread_model(setting: Setting, value: str) -> Change:
    _refuse_child_model_switch()
    thread_id = _thread_id()
    if not thread_id:
        raise SettingRefused("Cannot change the conversation model because no active thread is in context. "
                             "Use setting='default_model' only if you want to change the global default.")
    clear = value.strip().lower() in {"", "default", "inherit", "global"}
    ref = "" if clear else _resolve_chat_model(value)

    def apply() -> str:
        from row_bot.models import get_current_model
        from row_bot.threads import _set_thread_model_override

        _set_thread_model_override(thread_id, ref)
        _clear_agent_cache()
        if clear:
            return f"Thread model override cleared; using global default: {get_current_model()}"
        return f"Thread model override changed to: {ref}"

    after = Value("default", "the main model") if clear else Value(ref, _chat_model_words(ref))
    return _change(setting, _chat_model_now(), after, apply, title="Change this chat's model",
                   cancelled="Thread model change cancelled.")


def _vision_now() -> Value:
    from row_bot.tools.vision_tool import _get_vision_service

    model = str(_get_vision_service().model or "")
    return Value(model or "same as chat", model or "Same as the chat model")


def _prepare_vision_model(setting: Setting, value: str) -> Change:
    if value.strip().lower() in {"same as chat", "same", "default", "chat"}:
        model = ""
    else:
        model, error = resolve_model_update_value(value, surface="vision")
        if error or not model:
            raise SettingRefused(error or f"Vision model '{value}' not found. Pin a Vision model in Settings › Models first.")

    def apply() -> str:
        from row_bot.tools.vision_tool import _get_vision_service

        _get_vision_service().model = model
        _clear_agent_cache()
        return f"Vision model changed to: {model or 'the chat model'}"

    after = Value(model or "same as chat", model or "Same as the chat model")
    return _change(setting, _vision_now(), after, apply)


def _media_options(setting_id: str) -> dict[str, str]:
    if setting_id == "image_gen_model":
        from row_bot.tools.image_gen_tool import get_available_image_models

        return get_available_image_models()
    from row_bot.tools.video_gen_tool import get_available_video_models

    return get_available_video_models()


def _media_now(setting_id: str) -> Value:
    if setting_id == "image_gen_model":
        from row_bot.tools.image_gen_tool import _get_configured_selection
    else:
        from row_bot.tools.video_gen_tool import _get_configured_selection
    current = str(_get_configured_selection() or "")
    return Value(current, _media_options(setting_id).get(current, current) or "not chosen")


def _prepare_media_model(setting: Setting, value: str) -> Change:
    options = _media_options(setting.id)
    chosen = normalize_media_model(value, options)
    if chosen not in options:
        names = ", ".join(sorted(options.values())[:6]) or "none yet: connect a provider in Settings › Models"
        raise SettingRefused(f"'{value}' isn't one of the {setting.label.lower()}s Row-Bot can use. Choose one of: {names}.")
    tool = "image_gen" if setting.id == "image_gen_model" else "video_gen"

    def apply() -> str:
        from row_bot.tools import registry

        registry.set_tool_config(tool, "model", chosen)
        kind = "Image" if tool == "image_gen" else "Video"
        return f"{kind} generation model set to: {chosen}"

    return _change(setting, _media_now(setting.id), Value(chosen, options[chosen]), apply)


def _tokens_words(value: int | None) -> str:
    return "Auto" if value is None else f"{value:,} tokens"


def _context(value: str) -> int | None:
    if value.strip().lower() == "auto":
        return None
    from row_bot.models import validate_context_size

    try:
        return validate_context_size(value)
    except ValueError as exc:
        raise SettingRefused(f"{exc} Or use 'auto'.") from None


def _local_context_now() -> Value:
    from row_bot.models import get_local_context_mode, get_user_context_size

    size = None if get_local_context_mode() == "auto" else get_user_context_size()
    return Value("auto" if size is None else str(size), _tokens_words(size))


def _prepare_local_context(setting: Setting, value: str) -> Change:
    size = _context(value)

    def apply() -> str:
        from row_bot.models import set_context_size, set_context_size_auto

        set_context_size_auto() if size is None else set_context_size(size)
        return f"Local context size set to {_tokens_words(size)}."

    return _change(setting, _local_context_now(), Value("auto" if size is None else str(size), _tokens_words(size)),
                   apply, title="Change the reading limit for models on this computer")


def _provider_context_now() -> Value:
    from row_bot.models import get_cloud_context_override

    size = get_cloud_context_override()
    return Value("auto" if size is None else str(size), _tokens_words(size))


def _prepare_provider_context(setting: Setting, value: str) -> Change:
    size = _context(value)

    def apply() -> str:
        from row_bot.models import clear_cloud_context_override, set_cloud_context_size

        clear_cloud_context_override() if size is None else set_cloud_context_size(size)
        return f"Provider context cap set to {_tokens_words(size)}."

    return _change(setting, _provider_context_now(), Value("auto" if size is None else str(size), _tokens_words(size)),
                   apply, title="Change the reading limit for providers' models")


# ── Preferences, Voice and System rows (saved through the Settings service) ───


def _field_now(page: str, *path: str) -> object:
    value: object = _snapshot()[page]
    for key in path:
        value = value[key]  # type: ignore[index]
    return value


def _prepare_name(setting: Setting, value: str) -> Change:
    name = " ".join(value.split())
    if not name or len(name) > 128:
        raise SettingRefused("The assistant's name needs 1 to 128 characters.")
    current = str(_field_now("preferences", "identity", "name"))

    def apply() -> str:
        _save("preferences", "identity.name", name)
        _clear_agent_cache()
        return f"Assistant name changed to: {name}"

    return _change(setting, Value(current, current), Value(name, name), apply)


def _prepare_personality(setting: Setting, value: str) -> Change:
    from row_bot.identity import sanitize_personality

    text = sanitize_personality(value)
    if len(value.strip()) > 200:
        raise SettingRefused("A personality can be up to 200 characters.")
    current = str(_field_now("preferences", "identity", "personality"))

    def apply() -> str:
        _save("preferences", "identity.personality", text)
        _clear_agent_cache()
        removed = " (Some text was removed due to disallowed patterns.)" if text != value.strip() else ""
        return "Personality updated." + removed

    return _change(setting, Value(current, current or "(none)"), Value(text, text or "(none)"), apply)


def _switch_setting(page: str, field_name: str, path: tuple[str, ...], result: Callable[[bool], str], *,
                    after_save: Callable[[bool], None] | None = None,
                    check: Callable[[bool], None] | None = None) -> Callable[[Setting, str], Change]:
    def prepare(setting: Setting, value: str) -> Change:
        on = _on_off(value)
        if check is not None:
            check(on)

        def apply() -> str:
            _save(page, field_name, on)
            if after_save is not None:
                after_save(on)
            return result(on)

        return _change(setting, _switch(bool(_field_now(page, *path))), _switch(on), apply,
                       title=f"Turn {'on' if on else 'off'} {setting.label}")

    return prepare


def _switch_now(page: str, *path: str) -> Callable[[], Value]:
    return lambda: _switch(bool(_field_now(page, *path)))


def _dream_window_now() -> Value:
    dream = _field_now("preferences", "dream_cycle")
    start, end = int(dream["window_start"]), int(dream["window_end"])  # type: ignore[index]
    return Value(f"{start}-{end}", f"{start}:00–{end}:00")


def _prepare_dream_window(setting: Setting, value: str) -> Change:
    match = re.fullmatch(r"\s*(\d{1,2})\s*[-–]\s*(\d{1,2})\s*", value)
    start, end = (int(match.group(1)), int(match.group(2))) if match else (-1, -1)
    if not (0 <= start <= 23 and 0 <= end <= 23) or start == end:
        raise SettingRefused(f"Invalid dream window '{value}'. Use 'START-END' with two different hours 0–23 "
                             "(e.g. '1-5').")

    def apply() -> str:
        _save("preferences", "dream_cycle.window_start", start)
        _save("preferences", "dream_cycle.window_end", end)
        return f"Dream cycle window set to {start}:00–{end}:00."

    return _change(setting, _dream_window_now(), Value(f"{start}-{end}", f"{start}:00–{end}:00"), apply,
                   title="Change the Dream Cycle hours")


def _prepare_run_dream(setting: Setting, value: str) -> Change:
    if value.strip().lower() not in {"now", "", "run"}:
        raise SettingRefused("Use 'now' to run the Dream Cycle.")
    if not bool(_field_now("preferences", "dream_cycle", "enabled")):
        raise SettingRefused("The Dream Cycle is off. Turn it on first (setting 'dream_cycle').")

    def apply() -> str:
        import threading

        from row_bot.dream_cycle import run_dream_cycle

        def run() -> None:
            try:
                run_dream_cycle()
            except Exception:
                logger.error("Manual dream cycle failed", exc_info=True)

        threading.Thread(target=run, name="manual-dream-cycle", daemon=True).start()
        return "Dream cycle started in background. It may take a few minutes to complete."

    return _change(setting, None, Value("now", "Run now"), apply, title="Run the Dream Cycle now",
                   description="Tidy up memory now instead of waiting for its hours.",
                   cancelled="Dream cycle run cancelled.")


def _read_aloud_ready(on: bool) -> None:
    if on and not bool(_field_now("voice", "tts", "installed")):
        raise SettingRefused("Read aloud needs its voice downloaded first, which only you can start: "
                             "[Read aloud](/settings/voice#read-aloud).")


def _log_now() -> Value:
    level = str(_field_now("system", "logging", "level")).upper()
    return Value(level.lower(), level.capitalize())


def _prepare_log_detail(setting: Setting, value: str) -> Change:
    level = _LOG_LEVELS.get(value.strip().lower())
    if level is None:
        raise SettingRefused("Use debug, info, warning or error.")

    def apply() -> str:
        _save("system", "logging.level", level)
        return f"Log detail set to {level.capitalize()}. It applies from Row-Bot's next start."

    return _change(setting, _log_now(), Value(level.lower(), level.capitalize()), apply)


# ── Skills and tools ──────────────────────────────────────────────────────────


def normalize_lookup_token(value: str) -> str:
    """A human or tool label as a stable lookup token ("Web Search Tool" → "web_search")."""
    token = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower())
    token = re.sub(r"_+", "_", token).strip("_")
    return token[:-5] if token.endswith("_tool") else token


def tool_display_label(tool) -> str:
    """A tool's name as Settings shows it, without its leading emoji."""
    label = getattr(tool, "display_name", "") or getattr(tool, "name", "")
    label = re.sub(r"^[^A-Za-z0-9]+", "", str(label)).strip()
    return label or getattr(tool, "name", "")


def _name_and_switch(value: str, kind: str) -> tuple[str, bool]:
    name, sep, toggle = value.rpartition(":")
    if not sep or not name.strip():
        raise SettingRefused(f"Invalid value '{value}'. Use '{kind}_name:on' or '{kind}_name:off'.")
    return name.strip(), _on_off(toggle)


def resolve_tool_name(value: str) -> tuple[str | None, str | None, list[str]]:
    """(tool name, label, suggestions) for a tool named by its id or label ("video generation")."""
    from row_bot.tools import registry

    raw = (value or "").strip()
    if not raw:
        return None, None, []
    direct = registry.get_tool(raw)
    if direct is not None:
        return direct.name, tool_display_label(direct), []
    wanted = normalize_lookup_token(raw)
    for tool in registry.get_all_tools():
        if wanted in {tool.name, normalize_lookup_token(tool.name), normalize_lookup_token(tool_display_label(tool))}:
            return tool.name, tool_display_label(tool), []
    suggestions = [tool_display_label(tool) for tool in registry.get_all_tools()
                   if wanted and (wanted in normalize_lookup_token(tool_display_label(tool))
                                  or wanted in normalize_lookup_token(tool.name))]
    return None, None, suggestions[:3]


# Where each tool is switched on and off in Settings: (page id, page title, row anchor).
_TOOL_ROWS = {
    "image_gen": ("models", "Models", "image-model"),
    "video_gen": ("models", "Models", "video-model"),
    "shell": ("system", "System", "shell.enabled"),
    "filesystem": ("system", "System", "file-operations"),
    "browser": ("system", "System", "browser-computer-use"),
    "computer_use": ("system", "System", "browser-computer-use"),
    "mcp": ("apps", "Apps", ""),
    **{name: ("tools", "Tools", "search-tools")
       for name in ("web_search", "duckduckgo", "wolfram_alpha", "arxiv", "wikipedia", "youtube")},
}


def _tool_row(name: str) -> tuple[str, str, str]:
    return _TOOL_ROWS.get(name, ("tools", "Tools", "built-in-tools"))


def _tool_is_on(name: str) -> bool:
    from row_bot.tools import registry

    if name == "mcp":
        from row_bot.mcp_client import config as mcp_config

        return bool(mcp_config.is_globally_enabled())
    return registry.is_enabled(name)


def _computer_use_ready() -> None:
    from row_bot.computer_use.readiness import disclosure_acknowledged, resolve_cua_executable

    if not disclosure_acknowledged() or not resolve_cua_executable():
        raise SettingRefused(
            "Computer use needs its setup first, which only you can do: read Cua Driver's notice and install it in "
            "[Browser and computer use](/settings/system#browser-computer-use). Row-Bot can't accept the notice or "
            "install it for you."
        )


def tool_change(name: str, label: str, on: bool, *, why: str = "") -> Change:
    """Turning one tool on or off, checked: the change the "Turn on" card asks about."""
    if not on and name in _KEPT_ON:
        raise SettingRefused(f"Row-Bot can't turn off {label}: it would have no way to turn anything back on. "
                             "Turn it off in [Built-in tools](/settings/tools#built-in-tools) if you want to.")
    if on and name == "computer_use":
        _computer_use_ready()
    page, page_title, anchor = _tool_row(name)
    href = f"/settings/{page}" + (f"#{anchor}" if anchor else "")
    tier: Tier = ("security" if name in SECURITY_TOOLS else "capability") if on else "preference"

    def apply() -> str:
        from row_bot.tools import registry

        if name == "mcp":
            from row_bot.mcp_client import config as mcp_config

            mcp_config.set_global_enabled(on)
            actual = mcp_config.is_globally_enabled() and registry.is_enabled(name)
        else:
            registry.set_enabled(name, on)
            actual = registry.is_enabled(name)
        if actual != on:
            return f"Failed to {'enable' if on else 'disable'} tool '{label}'. The setting did not take effect."
        if on:
            # This turn's tools were fixed when it began: the next turn has it, and starts by itself.
            from row_bot.application.conversation_followups import after_tool_turned_on
            from row_bot.tool_outputs import current_thread_id

            after_tool_turned_on(current_thread_id(), label)
            return (f"{label} is on. It's ready in your next turn, which Row-Bot starts by itself: end this turn "
                    "now with one short line saying you'll do it, without asking the person to send anything.")
        return f"MCP client and tool '{label}' disabled." if name == "mcp" else f"Tool '{label}' disabled."

    if on:
        from row_bot.tools.conversation_setup_tool import declined

        cancelled = declined(label) + " Answer without it."
        description = (why or f"Row-Bot needs {label} for this.") + \
            f" You can turn it off again in Settings › {page_title}."
        title = f"Turn on {label}"
    else:
        cancelled, description, title = "Tool toggle cancelled.", f"{label}: On → Off", f"Turn off {label}"
    was = _tool_is_on(name)
    return Change("tool_toggle", label, f"Settings › {page_title}", href,
                  Value(f"{name}:{'on' if was else 'off'}", "On" if was else "Off"),
                  Value(f"{name}:{'on' if on else 'off'}", "On" if on else "Off"), tier, title, description, apply,
                  cancelled, setup={"kind": "tool", "label": label} if on else None)


def _prepare_tool_toggle(setting: Setting, value: str) -> Change:
    name_part, on = _name_and_switch(value, "tool")
    name, label, suggestions = resolve_tool_name(name_part)
    if not name or not label:
        hint = f" Try one of: {', '.join(suggestions)}." if suggestions else ""
        raise SettingRefused(f"Unknown tool '{name_part}'.{hint}")
    return tool_change(name, label, on)


def resolve_skill_name(value: str) -> tuple[str | None, str | None, list[str]]:
    """(skill name, label, suggestions) for a Skill Library item named by its id or label."""
    from row_bot.skills import get_manual_skill_statuses

    raw = (value or "").strip()
    if not raw:
        return None, None, []
    statuses = get_manual_skill_statuses()
    wanted = normalize_lookup_token(raw)
    for skill, _available in statuses:
        if raw == skill.name or wanted in {skill.name, normalize_lookup_token(skill.name),
                                           normalize_lookup_token(skill.display_name)}:
            return skill.name, skill.display_name, []
    suggestions = [skill.display_name for skill, _available in statuses
                   if wanted and (wanted in normalize_lookup_token(skill.display_name)
                                  or wanted in normalize_lookup_token(skill.name))]
    return None, None, suggestions[:3]


def _prepare_skill(setting: Setting, value: str) -> Change:
    from row_bot import skills

    name_part, on = _name_and_switch(value, "skill")
    try:
        name, label, suggestions = resolve_skill_name(name_part)
    except Exception as exc:
        raise SettingRefused(f"Failed to inspect Skill Library: {exc}") from None
    if not name or not label:
        hint = f" Try one of: {', '.join(suggestions)}." if suggestions else ""
        raise SettingRefused(f"Unknown Skill Library item '{name_part}'.{hint}")
    pin = setting.id == "skill_pin"
    was = skills.is_pinned(name) if pin else skills.is_enabled(name)
    words = ("Pinned", "Not pinned") if pin else ("Available", "Off")

    def apply() -> str:
        if pin:
            skills.set_pinned(name, on)
            if on:
                return (f"Skill '{label}' is now pinned and Available. It will start active in new chats, tasks, "
                        "designer threads, and developer threads.")
            if skills.is_enabled(name):
                return f"Skill '{label}' is no longer pinned. It remains Available unless you turn it off separately."
            return f"Skill '{label}' is no longer pinned. It is Off and will stay Off unless you make it Available."
        was_pinned = skills.is_pinned(name)
        skills.set_enabled(name, on)
        if not on and was_pinned:
            return f"Skill '{label}' is now Off and no longer pinned."
        return f"Skill '{label}' is now {'Available' if on else 'Off'}."

    verb = ("Pin" if on else "Unpin") if pin else ("Turn on" if on else "Turn off")
    return Change(setting.id, label, f"Settings › Skills › {label}", f"/settings/skills/{name}",
                  Value(f"{name}:{'on' if was else 'off'}", words[0] if was else words[1]),
                  Value(f"{name}:{'on' if on else 'off'}", words[0] if on else words[1]), setting.tier,
                  f"{verb} skill '{label}'", f"{label}: {words[0] if was else words[1]} → {words[0] if on else words[1]}",
                  apply, "Skill pin change cancelled." if pin else "Skill toggle cancelled.")


# ── Model resolution kept from the status tool ────────────────────────────────


def normalize_media_model(value: str, options: dict[str, str]) -> str:
    """The catalog value an image or video model is named by: its value, id or label, when only one fits."""
    normalized = (value or "").strip()
    if not normalized or normalized in options:
        return normalized

    def tokens(text: str) -> set[str]:
        raw = re.sub(r"^[^A-Za-z0-9]+", "", str(text or "")).strip()
        found = {normalize_lookup_token(raw)}
        without_icon = re.sub(r"^[A-Za-z]{1,3}\s{2,}", "", raw).strip()
        if without_icon and without_icon != raw:
            found.add(normalize_lookup_token(without_icon))
        stripped = re.sub(r"\s*\([^)]*\)\s*$", "", raw).strip()
        found.add(normalize_lookup_token(stripped))
        without_prefix = re.sub(r"^[A-Za-z]{1,3}\s{2,}", "", stripped).strip()
        if without_prefix and without_prefix != stripped:
            found.add(normalize_lookup_token(without_prefix))
        if "/" in stripped:
            found.add(normalize_lookup_token(stripped.split("/", 1)[1]))
        return {token for token in found if token}

    desired = normalize_lookup_token(normalized)
    desired_tokens = tokens(normalized)
    exact: list[str] = []
    close: list[str] = []
    for config_value, label in options.items():
        provider, _, model_id = config_value.partition("/")
        candidates = {config_value, model_id or config_value, label, f"{provider} {model_id}", f"{provider} {label}"}
        found = set().union(*(tokens(candidate) for candidate in candidates))
        if desired in found:
            exact.append(config_value)
        elif desired_tokens & found:
            close.append(config_value)
    if len(exact) == 1:
        return exact[0]
    if not exact and len(close) == 1:
        return close[0]
    return normalized


def _surface_label(surface: str) -> str:
    return {"chat": "Brain", "vision": "Vision"}.get(surface, surface.replace("_", " ").title())


def resolve_model_update_value(value: str, *, surface: str) -> tuple[str | None, str | None]:
    """A Vision model named by its ref, label or installed name: (model, None) or (None, why not)."""
    from row_bot.models import is_model_local, list_cloud_models, list_cloud_vision_models
    from row_bot.providers.capabilities import snapshot_supports_surface
    from row_bot.providers.selection import list_model_choice_options, model_ref, resolve_selection, star_cannot_run

    raw = (value or "").strip()
    if not raw:
        return None, f"No {_surface_label(surface)} model was provided."
    resolved = resolve_selection(raw)
    if not resolved:
        return None, f"Model '{raw}' not found. Pin it in Settings › Models first."
    provider_id = resolved.provider_id or "local"
    model_id = resolved.model_id
    starred = {str(option["value"]): option for option in list_model_choice_options(surface, include_inactive=True)}
    star = starred.get(resolved.ref)
    if star:
        if star_cannot_run(star):
            reason = str(star.get("reason") or "This Quick Choice is inactive.")
            return None, f"Model '{raw}' is not active for {_surface_label(surface)}. {reason}"
        return model_id, None
    if provider_id in {"local", "ollama"}:
        if not is_model_local(model_id):
            return None, (f"Local model '{raw}' is not installed. Install it with Ollama or pin an installed model "
                          "in Settings › Models.")
        if surface == "vision":
            from row_bot.providers.capability_resolution import resolve_capability_snapshot

            snapshot = resolve_capability_snapshot("ollama", model_id)
            if not snapshot or not snapshot_supports_surface(snapshot, "vision"):
                return None, (f"Local model '{raw}' is installed, but Row-Bot does not have Vision capability "
                              "metadata for it. Choose a Vision model from Settings › Models.")
        return model_id, None
    if model_id not in set(list_cloud_models(provider_id)):
        return None, (f"Provider model '{raw}' is not in the current catalog. Refresh Providers or pin it from "
                      "Settings › Models first.")
    if surface == "vision" and model_id not in set(list_cloud_vision_models()):
        from row_bot.models import is_cloud_vision_model

        if not is_cloud_vision_model(model_ref(provider_id, model_id)):
            return None, f"Provider model '{raw}' is not marked as Vision-capable in the catalog."
    return model_id, None


# ── The list ──────────────────────────────────────────────────────────────────


def _dream_switch_result(on: bool) -> str:
    return f"Dream cycle {'enabled' if on else 'disabled'}."


SETTINGS: tuple[Setting, ...] = (
    Setting("thread_model", "This chat's model", "", "", "", "preference",
            "a pinned Brain model's canonical ref or exact label; 'default' uses the main model",
            _prepare_thread_model, _chat_model_now),
    Setting("default_model", "Main model", "models", "Models", "default-model", "preference",
            "a pinned Brain model's canonical ref or exact label (row_bot_status 'model')",
            _prepare_default_model, _main_model_now),
    Setting("vision_model", "Vision model", "models", "Models", "vision-model", "preference",
            "a Vision model's ref or label, or 'same as chat'", _prepare_vision_model, _vision_now),
    Setting("image_gen_model", "Image model", "models", "Models", "image-model", "preference",
            "an image model's provider/model-id or exact label", _prepare_media_model,
            lambda: _media_now("image_gen_model")),
    Setting("video_gen_model", "Video model", "models", "Models", "video-model", "preference",
            "a video model's provider/model-id or exact label", _prepare_media_model,
            lambda: _media_now("video_gen_model")),
    Setting("context_size", "Reading limit", "models", "Models", "reading-limit", "preference",
            "'auto' or a token count for models on this computer", _prepare_local_context, _local_context_now),
    Setting("cloud_context_size", "Reading limit", "models", "Models", "reading-limit", "preference",
            "'auto' or a token count for providers' models", _prepare_provider_context, _provider_context_now),
    Setting("name", "Assistant name", "preferences", "Preferences", "identity.name", "preference",
            "a name, 1 to 128 characters", _prepare_name,
            lambda: (lambda name: Value(name, name))(str(_field_now("preferences", "identity", "name")))),
    Setting("personality", "Personality", "preferences", "Preferences", "identity.personality", "preference",
            "up to 200 characters about how to talk", _prepare_personality,
            lambda: (lambda text: Value(text, text or "(none)"))(str(_field_now("preferences", "identity",
                                                                                "personality")))),
    Setting("self_improvement", "Learn new skills", "preferences", "Preferences", "identity.self_improvement_enabled",
            "preference", "'on' or 'off'",
            _switch_setting("preferences", "identity.self_improvement_enabled", ("identity", "self_improvement_enabled"),
                            lambda on: f"Self-improvement {'enabled' if on else 'disabled'}.",
                            after_save=lambda _on: _clear_agent_cache()),
            _switch_now("preferences", "identity", "self_improvement_enabled")),
    Setting("dream_cycle", "Dream Cycle", "preferences", "Preferences", "dream-cycle", "preference", "'on' or 'off'",
            _switch_setting("preferences", "dream_cycle.enabled", ("dream_cycle", "enabled"), _dream_switch_result),
            _switch_now("preferences", "dream_cycle", "enabled")),
    Setting("dream_window", "Dream Cycle", "preferences", "Preferences", "dream-cycle", "preference",
            "'START-END' hours, 0–23 (e.g. '1-5')", _prepare_dream_window, _dream_window_now),
    Setting("run_dream_cycle", "Dream Cycle", "preferences", "Preferences", "dream-cycle", "repair",
            "'now' runs it once", _prepare_run_dream),
    Setting("read_aloud", "Read aloud", "voice", "Voice", "read-aloud", "preference", "'on' or 'off'",
            _switch_setting("voice", "tts.enabled", ("tts", "enabled"),
                            lambda on: f"Read aloud is {'on' if on else 'off'}.", check=_read_aloud_ready),
            _switch_now("voice", "tts", "enabled")),
    Setting("live_captions", "Live captions", "voice", "Voice", "runtime.captions_enabled", "preference",
            "'on' or 'off'",
            _switch_setting("voice", "runtime.captions_enabled", ("runtime", "captions_enabled"),
                            lambda on: f"Live captions are {'on' if on else 'off'}."),
            _switch_now("voice", "runtime", "captions_enabled")),
    Setting("log_detail", "Log detail", "system", "System", "logging.level", "preference",
            "debug, info, warning or error", _prepare_log_detail, _log_now),
    Setting("skill_toggle", "Skills", "skills", "Skills", "", "preference",
            "'skill_name:on' (Available) or 'skill_name:off'", _prepare_skill),
    Setting("skill_pin", "Skills", "skills", "Skills", "", "preference",
            "'skill_name:on' (pinned for new chats) or 'skill_name:off'", _prepare_skill),
    Setting("tool_toggle", "Tools", "tools", "Tools", "built-in-tools", "preference",
            "'tool_name:on' or 'tool_name:off' ('mcp:on' or 'mcp:off' for the MCP client)", _prepare_tool_toggle),
)
BY_ID = {setting.id: setting for setting in SETTINGS}
IDS = tuple(BY_ID)


# ── Asking and saving ─────────────────────────────────────────────────────────


def _risks(tier: Tier) -> tuple[str, ...]:
    from row_bot.tools.approval_gate import SECURITY_CAPABILITY_RISK

    return {"preference": (_PREFERENCE_RISK,), "capability": (_CAPABILITY_RISK,),
            "security": (SECURITY_CAPABILITY_RISK,), "repair": (_REPAIR_RISK,)}[tier]


def approval_payload(change: Change) -> dict:
    """The approval card's question: the change in words, where its row is, and what it was."""
    payload = {
        "tool": TOOL,
        "label": change.title,
        "description": change.description,
        "args": {"setting": change.setting, "value": change.after.canonical},
        "change": {"label": change.label, "where": change.where, "href": change.href,
                   "before": change.before.words if change.before else "", "after": change.after.words,
                   "tier": change.tier},
    }
    if change.setup:
        payload["setup"] = change.setup
    return payload


def approve(change: Change) -> str | None:
    """None when the change may be saved; else what the model hears (blocked, or not approved)."""
    from row_bot.tools.approval_gate import approval_check

    refusal, _line = approval_check(approval_payload(change), risks=_risks(change.tier), blocked_message=BLOCKED,
                                    cancelled_message="")
    if refusal is None:
        return None
    return refusal if refusal.startswith("BLOCKED") else change.cancelled


def undo_line(change: Change) -> str:
    """The call that puts the setting back, for the person's Undo and for Row-Bot."""
    if change.before is None:
        return ""
    return UNDO_PREFIX + json.dumps({"setting": change.setting, "value": change.before.canonical,
                                     "now": change.after.canonical}, ensure_ascii=False)


def parse_undo(result: str) -> dict[str, str] | None:
    """The undo a saved change's result ends with: {setting, value, now}, or None."""
    for line in reversed(str(result or "").splitlines()):
        if line.startswith(UNDO_PREFIX):
            try:
                found = json.loads(line[len(UNDO_PREFIX):])
            except ValueError:
                return None
            if isinstance(found, dict) and all(isinstance(found.get(key), str) for key in ("setting", "value", "now")):
                return {key: found[key] for key in ("setting", "value", "now")}
            return None
    return None


def save(change: Change) -> str:
    """Save an approved change; its result ends with how to undo it."""
    try:
        result = change.apply()
    except SettingRefused as exc:
        return str(exc)
    except Exception as exc:
        logger.warning("Saving %s failed", change.setting, exc_info=True)
        return f"Failed to change {change.label}: {exc}"
    line = undo_line(change)
    return f"{result}\n{line}" if line else result


def change(setting_id: str, value: str) -> str:
    """Check, approve and save one change; what the model hears."""
    key = (setting_id or "").strip().lower()
    raw = (value or "").strip()
    try:
        if key == "model":
            # The legacy alias: the global default, unless a conversation makes it ambiguous.
            if _thread_id():
                _refuse_child_model_switch()
                return ("Model scope is ambiguous in this conversation, so I did not change anything. Use "
                        "setting='thread_model' to change this conversation, or setting='default_model' to change "
                        "the global default for new conversations and workflows.")
            proposed = _prepare_default_model(BY_ID["default_model"], raw, legacy=True)
        elif key in BY_ID:
            proposed = BY_ID[key].prepare(BY_ID[key], raw)
        else:
            return f"Unknown setting '{setting_id}'. Supported: {', '.join(IDS)}."
    except SettingRefused as exc:
        return str(exc)
    if proposed.before is not None and proposed.before.canonical == proposed.after.canonical:
        return f"{proposed.label} is already {proposed.after.words}."
    refusal = approve(proposed)
    return refusal if refusal is not None else save(proposed)


def undo(setting_id: str, value: str, now: str) -> str:
    """The person's Undo: put *value* back if the setting still holds *now* (their own action, no card)."""
    setting = BY_ID.get(setting_id)
    if setting is None:
        return "That change can't be undone here."
    try:
        proposed = setting.prepare(setting, value)
    except SettingRefused as exc:
        return f"Couldn't undo: {exc}"
    if proposed.before is None or proposed.before.canonical != now:
        return f"{proposed.label} has changed since, so it wasn't undone. Change it in Settings."
    return save(proposed)


def turn_on(name: str, label: str, why: str) -> bool:
    """Ask to turn a tool on with the "Turn on" card, by the approval level; True once it is on."""
    if _tool_is_on(name):
        return True
    try:
        proposed = tool_change(name, label, True, why=why)
    except SettingRefused:
        return False
    if approve(proposed) is not None:
        return False
    proposed.apply()
    return _tool_is_on(name)


def describe() -> str:
    """What Row-Bot can change, what each is now, and where it is (row_bot_status 'settings')."""
    lines = ["**Settings Row-Bot can change** (row_bot_update_setting; the chat's approval level decides who "
             "approves, and each change can be undone)"]
    for setting in SETTINGS:
        now = ""
        if setting.current is not None:
            try:
                now = f" Now: {setting.current().words}."
            except Exception as exc:
                now = f" Now: unavailable ({exc})."
        where = f" [{setting.where.removeprefix('Settings › ')}]({setting.href})" if setting.href else ""
        lines.append(f"- {setting.id}: {setting.label}{where}.{now} Takes {setting.takes}.")
    lines.append(
        "Row-Bot never changes these, only shows where they are: approval levels, keys and sign-ins, devices and "
        "remote access, install stats, Learning, data-folder access, updates, the workspace folder, appearance, "
        "downloads and installs, and anything that deletes."
    )
    return "\n".join(lines)
