"""What a Live voice front model is told, and the context block it starts with.

Every string here reaches a cloud speech-to-speech model (OpenAI Realtime,
Gemini Live, Grok Voice). Keep it short and generic: the front talks, the
normal agent does the work through ``row_bot_agent_consult``.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Sequence

from row_bot.brand import APP_DISPLAY_NAME

# Bounds for the context block: the front gets a glimpse of the chat, not the chat.
HISTORY_MESSAGES = 12
HISTORY_MESSAGE_CHARS = 400
HISTORY_CHARS = 3000
MEMORY_ITEMS = 5
MEMORY_ITEM_CHARS = 200

FRONT_INSTRUCTIONS = f"""You are {APP_DISPLAY_NAME}, talking with the person in a live voice conversation.
Speak naturally and briefly, like a person on a call. No markdown, lists or links.
Answer greetings, small talk and simple questions about this conversation yourself.
For anything else (facts, current information, memory, files, the computer or browser, any action) call row_bot_agent_consult with the person's request. While it works you may say a few words such as "Let me check". When it returns, say its answer faithfully, as your own.
Status lines you are sent while it works are for you to pass on in a few words.
Never say you used a tool, changed something or remembered something unless a result says so.
If the audio is silence, noise, an echo of you or speech not meant for you, call wait_for_user and say nothing."""


def _clip(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def context_block(*, profile_name: str = "", history: Sequence[tuple[str, str]] = (),
                  memories: Iterable[str] = ()) -> str:
    """The persona line, the chat's recent turns and a few memories, as plain text.

    ``history`` is (role, text) pairs, oldest first, with role "person" or
    "assistant"; only the newest that fit the bounds are kept.
    """
    parts: list[str] = []
    profile = _clip(profile_name, 80)
    if profile:
        parts.append(f"You speak for the agent profile: {profile}.")
    lines: list[str] = []
    used = 0
    for role, text in reversed(list(history)[-HISTORY_MESSAGES:]):
        clean = _clip(text, HISTORY_MESSAGE_CHARS)
        if not clean:
            continue
        line = f"{'Person' if role == 'person' else APP_DISPLAY_NAME}: {clean}"
        if used + len(line) > HISTORY_CHARS:
            break
        lines.append(line)
        used += len(line)
    if lines:
        parts.append("Recent conversation, oldest first:\n" + "\n".join(reversed(lines)))
    remembered = [f"- {_clip(item, MEMORY_ITEM_CHARS)}" for item in memories if _clip(item, MEMORY_ITEM_CHARS)]
    if remembered:
        parts.append("Things you remember that may help:\n" + "\n".join(remembered[:MEMORY_ITEMS]))
    return "\n\n".join(parts)


def front_instructions(context: str = "") -> str:
    """The front's session instructions: the fixed lines, then the context block."""
    return f"{FRONT_INSTRUCTIONS}\n\n{context}" if context else FRONT_INSTRUCTIONS


def progress_cue(tool_label: str) -> str:
    """A status line from a tool the agent started (no model call)."""
    return f"Working on: {_clip(tool_label, 60)}."


def consult_output(status: str, answer: str = "") -> str:
    """The consult call's result for the front to speak, once the agent's run ends."""
    if status == "completed":
        return json.dumps({"status": "completed", "answer": answer}, ensure_ascii=False)
    if status in {"cancelled", "stopped"}:
        return json.dumps({"status": "cancelled", "answer": "That was stopped."})
    if status == "waiting_approval":
        return json.dumps({"status": "needs_approval", "answer": "It needs your approval in the app."})
    return json.dumps({"status": "failed", "answer": "That didn't work. The details are in the chat."})
