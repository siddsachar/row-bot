"""The app's side of Row-Bot Self: Undo on a change Row-Bot made, a guide page, and /status for people.

- ``undo_setting_change``: the person pressed Undo on a setting step. The step must be a result of
  ``row_bot_update_setting`` in this conversation; its last line says what to put back, and
  ``self_settings.undo`` puts it back only if the setting still holds what Row-Bot set. Pressing Undo is
  the person's own action, so nothing is asked.
- ``read_guide_page``: one of Row-Bot's guides, read from the copy this install ships (offline).
- ``person_status``: what ``/status`` shows the person: version and update, the model and whether it can
  use tools, the chat's approval level, and what needs them, each with its fix.
"""

from __future__ import annotations

import re
from typing import Any

from row_bot.application.client_platform import ClientPlatformError

SETTING_TOOL = "row_bot_update_setting"
DOCS_SITE = "https://row-bot.ai"
# Settings pages by id, in the page names Settings shows (frontend/src/features/settings/model.ts).
SETTINGS_PAGES = {
    "preferences": "Preferences", "appearance": "Appearance", "buddy": "Buddy", "proactive": "Proactive",
    "models": "Models",
    "voice": "Voice", "knowledge": "Memory", "documents": "Documents", "tracker": "Tracker", "tools": "Tools",
    "skills": "Skills", "apps": "Apps", "system": "System", "access": "Devices & remote access",
    "updates": "Updates", "data": "Data",
}
_IN_APP_LINK = re.compile(r"\[([^\]\n]{1,200})\]\((/(?:app-v2/)?settings/[^\s)]*|/docs/[^\s)]*|/\?tab=[^\s)]*)\)")


def undo_setting_change(conversation_id: str, message_id: str) -> dict[str, str]:
    """Undo one setting change from its step; {status, message}."""
    from row_bot import self_settings
    from row_bot.runtime.checkpoint_reader import open_checkpoint

    with open_checkpoint(conversation_id) as reader:
        if reader is None:
            raise ClientPlatformError("not_found")
        records = [record for _position, record in reader.records()]
        result = next((record for record in records
                       if record["message_id"] == message_id and record["role"] == "tool"), None)
        if result is None or not result.get("tool_call_id"):
            raise ClientPlatformError("not_found")
        call = next((item for record in records for item in record.get("tool_calls") or []
                     if item.get("id") == result["tool_call_id"]), None)
        # Only a change Row-Bot's own setting tool made: never a line copied into another message.
        if call is None or call.get("name") != SETTING_TOOL:
            raise ClientPlatformError("not_found")
        text = "".join(reader.public_text_chunks(result))
    found = self_settings.parse_undo(text)
    if found is None:
        return {"status": "unavailable", "message": "This change can't be undone."}
    status, message = self_settings.undo(found["setting"], found["value"], found["now"])
    return {"status": status, "message": message[:512]}


def read_guide_page(route: str) -> dict[str, Any]:
    """One guide page as the guide sheet shows it."""
    from row_bot import guides

    try:
        page = guides.read(route.partition("#")[0])
    except guides.GuidesUnavailable:
        raise ClientPlatformError("not_found") from None
    if not isinstance(page, guides.GuidePage):
        raise ClientPlatformError("not_found")
    return {
        "schema_version": 1,
        "route": page.route,
        "title": page.title[:200],
        "description": page.description[:1024],
        "markdown": page.text[:200_000],
        "web_url": f"{DOCS_SITE}{page.route}",
        "sections": [{"anchor": section.anchor, "heading": section.heading[:200]}
                     for section in page.sections if section.anchor][:200],
    }


def _model_line(conversation_id: str) -> str:
    from row_bot.models import get_current_model
    from row_bot.providers.readiness import evaluate_runtime_readiness
    from row_bot.threads import _get_thread_model_override

    model = _get_thread_model_override(conversation_id) or get_current_model()
    if not model:
        return "- **Model:** none chosen yet: [Main model](/settings/models#default-model)"
    from row_bot.self_settings import chat_model_words

    readiness = evaluate_runtime_readiness(model, probe_ollama_tools=False, refresh_provider_status=False)
    words = {"agent": "ready, can use tools", "chat_only": "can only chat: tools and actions are off"}.get(
        readiness.selected_mode, f"can't run: {readiness.selection_reason}")
    return f"- **Model:** {chat_model_words(model)}, {words}"


def _update_line() -> str:
    import row_bot.updater as updater

    summary = updater.summary_for_status()
    if summary.get("ready_version"):
        return f"v{summary['ready_version']} installs when Row-Bot restarts"
    if summary.get("update_available"):
        return f"[v{summary['available_version']} available](/settings/updates)"
    return "up to date"


def person_status(conversation_id: str) -> str:
    """What /status shows: never the model's own overview, ids or paths."""
    from row_bot.approval_policy import approval_label
    from row_bot.self_health import fix_words, problems
    from row_bot.threads import _get_thread_approval_mode
    from row_bot.version import __version__

    lines = [f"**Row-Bot {__version__}** · {_update_line()}", "", _model_line(conversation_id),
             f"- **Approvals in this chat:** {approval_label(_get_thread_approval_mode(conversation_id))}"]
    found = problems()
    if not found:
        lines.append("- **Needs you:** nothing")
    else:
        lines.append(f"- **Needs you:** {len(found)}")
        for problem in found:
            fix = fix_words(problem.fix)
            lines.append(f"  - {problem.title.rstrip('.')}" + (f": {fix}" if fix else ""))
    return "\n".join(lines)


def channel_words(text: str) -> str:
    """In-app links as words for a chat app, where nothing in Row-Bot can open them.

    ``[Dream Cycle](/settings/preferences#dream-cycle)`` → "Dream Cycle (Settings › Preferences)";
    a guide becomes its public page; a Home tab keeps its name.
    """

    def words(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        if target.startswith("/docs/"):
            return f"{label} ({DOCS_SITE}{target})"
        if target.startswith("/?tab="):
            return label
        page = target.removeprefix("/app-v2").removeprefix("/settings/").split("/", 1)[0].split("?", 1)[0]
        page = page.split("#", 1)[0]
        title = SETTINGS_PAGES.get(page)
        if not title or "Settings" in label:
            return label
        return f"{label} (Settings › {title})"

    return _IN_APP_LINK.sub(words, text)
