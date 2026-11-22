"""The fixed lines Row-Bot Voice says on its own while it works, and a spoken yes or no.

Every line here is a template: no model writes it, it is never saved in the
chat and never sent to the model. It is heard (and captioned) only.
"""
from __future__ import annotations

import re
from typing import Literal

# A progress line no more often than this, counted from the last thing said.
PROGRESS_GAP_SECONDS = 4.0
# A tool still running this long, with nothing said since it started, gets one "still working" line.
STILL_WORKING_SECONDS = 2.0

STILL_WORKING = "Still working on it…"
NEEDS_A_TAP = "This one needs a tap."

# What a tool is doing, as "I'm …" would end: "checking your calendar". Own tool names, first match wins.
_DOING: tuple[tuple[re.Pattern[str], str | None], ...] = tuple((re.compile(pattern), words) for pattern, words in (
    # Quick or conversational steps say nothing of their own.
    (r"^(get_current_datetime|calculate|tool_search|skill_search|skill_load|ask_person|present|row_bot_status|"
     r"goal_status|read_tool_output|check_process|request_connection|suggest_apps|think)$", None),
    (r"(^|_)web_search$|^(duckduckgo|tavily|brave|serp|google)(_|$)|search_web", "searching the web"),
    (r"calendar|(^|_)events?$|_event$|find_free_times", "checking your calendar"),
    (r"gmail|mail", "checking your email"),
    (r"weather|forecast", "checking the weather"),
    (r"memor|explore_connections", "checking what I remember"),
    (r"conversations", "looking through your chats"),
    (r"^browser_|read_url", "looking at the page"),
    (r"(^|_)read_file$|page_read", "reading that file"),
    (r"list_directory|file_search|^find$|developer_(list_files|search)", "looking through your files"),
    (r"^(generate|edit|animate)_image$", "making the picture"),
    (r"analyze_image", "looking at the picture"),
    (r"video", "working on the video"),
    (r"wikipedia|arxiv|youtube|wolfram", "looking that up"),
    (r"^delegate_work$", "getting helpers on it"),
    (r"^agent_wait$", "waiting for the helpers"),
    (r"run_command|^shell$|start_process|run_detected_test", "running that"),
    (r"^computer", "using the computer"),
))
_WORKING = "working on it"


def _doing(tool: str) -> str | None:
    name = str(tool or "").strip().lower()
    for pattern, words in _DOING:
        if pattern.search(name):
            return words
    return _WORKING


def progress_line(tool: str) -> str | None:
    """The line said when *tool* starts ("Checking your calendar…"); None for a quick step."""
    words = _doing(tool)
    return f"{words[0].upper()}{words[1:]}…" if words else None


def status_line(tool: str | None) -> str:
    """The answer to "how's it going?" while a run works: what it is doing now."""
    words = _doing(tool) if tool else None
    return f"I'm still {words or _WORKING}."


# ── A spoken yes or no ──────────────────────────────────────────────────────

_WORDS = re.compile(r"[a-z0-9']+")
_YES = frozenset({"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "approve", "approved", "alright", "fine",
                  "confirm", "confirmed"})
_NO = frozenset({"no", "nope", "nah", "cancel", "deny", "reject", "don't", "dont"})
_PHRASES: dict[tuple[str, ...], str] = {
    ("go", "ahead"): "yes", ("do", "it"): "yes", ("go", "for", "it"): "yes", ("sounds", "good"): "yes",
    ("all", "right"): "yes", ("that's", "fine"): "yes", ("that's", "ok"): "yes", ("that's", "okay"): "yes",
    ("do", "not"): "no", ("don't", "do", "it"): "no", ("never", "mind"): "no", ("nevermind",): "no",
    ("not", "now"): "no", ("cancel", "it"): "no", ("cancel", "that"): "no", ("stop", "it"): "no",
}
# Words that keep an answer short and plain ("yes please", "okay thanks").
_FILLER = frozenset({"please", "thanks", "thank", "you", "hey", "row", "bot", "rowbot", "um", "uh", "oh", "well",
                     "then", "just", "it", "that", "this"})

SpokenAnswer = Literal["yes", "no", "unclear"]


def spoken_answer(text: str) -> SpokenAnswer | None:
    """A short, plain yes or no; "unclear" for a mixed or hedged one ("yes no", "yes but…").

    None when the words are not an answer at all: a sentence goes on as the person's next message.
    """
    words = _WORDS.findall(str(text or "").lower().replace("’", "'"))
    if not words or len(words) > 8:
        return None
    kinds: list[str] = []
    index = 0
    while index < len(words):
        for size in (3, 2, 1):
            phrase = tuple(words[index:index + size])
            if len(phrase) == size and phrase in _PHRASES:
                kinds.append(_PHRASES[phrase])
                index += size
                break
        else:
            word = words[index]
            kinds.append("yes" if word in _YES else "no" if word in _NO else "filler" if word in _FILLER
                         else "other")
            index += 1
    said = {kind for kind in kinds if kind in {"yes", "no"}}
    if not said:
        return None
    others = kinds.count("other")
    if others >= 2:
        return None
    if others or len(said) > 1:
        return "unclear"
    return "yes" if said == {"yes"} else "no"
