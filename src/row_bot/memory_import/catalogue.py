"""What each assistant offers and how to get it, plus the prompt to paste into it."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


UNIVERSAL_PROMPT = """\
I'm moving to another assistant and need a complete export of what you know about me.
List every saved memory, custom instruction and piece of context you have about me,
including what you have learned from our past conversations.

Output a single code block containing only the list. One fact per line, exactly:
category | date | fact

category is exactly one of:
  identity     name, age range, location, job, education, languages, long-term situation
  person       people in my life and how they relate to me (partner, child, manager, pet)
  preference   things I like, dislike or prefer
  instruction  how you should answer me: tone, format, length, units, "always…", "never…"
  project      ongoing work, goals, plans, recurring topics
  skill        tools, languages, frameworks, equipment and skills I use
  event        dated things that happened or are planned
  place        places that matter to me (home town, office, favourite places)
  other        anything stored that fits none of the above

date: YYYY-MM-DD when you know when this was saved or happened, otherwise unknown.
Never invent a date.

fact: one short standalone sentence about me in the third person, keeping my own
words where possible (for example: Works night shifts as a nurse in Porto).

Rules:
- One fact per line. No headings, numbering, bullets, bold or blank lines.
- Split combined facts into separate lines. Do not summarise, merge or leave anything out.
- Do not add anything you are unsure about, and do not include passwords or account numbers.
- Keep the language I used; do not translate.
After the code block, say on one line whether this is the complete set or whether more remain.
"""

CONTINUE_PROMPT = (
    "Please continue the list from where you stopped, in the same format "
    "(category | date | fact), in a single code block. Do not repeat lines you already gave."
)

Export = Literal["memories", "history", "none"]


@dataclass(frozen=True)
class SourceInfo:
    """One assistant a person may be moving from."""

    id: str
    name: str
    export: Export  # what its own file export holds about the person
    prompt_route: bool  # whether pasting UNIVERSAL_PROMPT into it is the way to bring memories
    how_to_get: str


SOURCES: tuple[SourceInfo, ...] = (
    SourceInfo(
        "claude", "Claude", "memories", False,
        "In Claude on the web or desktop, open Settings › Privacy › Export data; the emailed link lasts 24 hours. "
        "Add the zip, or the conversations and memories zips the link offers.",
    ),
    SourceInfo(
        "chatgpt", "ChatGPT", "history", True,
        "In ChatGPT open Settings › Data controls › Export data; the emailed link lasts 24 hours. "
        "The zip holds your chats and custom instructions but not saved memories, so also paste the prompt.",
    ),
    SourceInfo(
        "gemini", "Gemini", "history", True,
        "At takeout.google.com choose only My Activity › Gemini Apps, switch its format from HTML to JSON, "
        "and add the zip. Saved info is not in it, so also paste the prompt.",
    ),
    SourceInfo(
        "copilot", "Microsoft Copilot", "history", True,
        "At account.microsoft.com/privacy open Copilot › Copilot apps › Export all activity history; "
        "a CSV file downloads straight away.",
    ),
    SourceInfo(
        "perplexity", "Perplexity", "history", True,
        "In Perplexity open All settings › Account › Export my data; the zip arrives by email.",
    ),
    SourceInfo(
        "grok", "Grok", "history", True,
        "In Grok open Settings › Data Controls › Download account data (accounts.x.ai/data); "
        "the zip arrives by email.",
    ),
    SourceInfo(
        "deepseek", "DeepSeek", "history", False,
        "In DeepSeek open Settings › Data › Export data; the download link lasts 7 days.",
    ),
    SourceInfo(
        "mem0", "mem0", "memories", False,
        "Save the JSON from mem0's get_all or GET /v1/memories/, or an OpenMemory backup zip, and add it.",
    ),
    SourceInfo(
        "letta", "Letta", "memories", False,
        "Export the agent as an Agent File (.af) from Letta and add it; its core memory about you is imported.",
    ),
    SourceInfo(
        "other", "Another assistant", "none", True,
        "Paste the prompt into the assistant, then paste its whole answer back here.",
    ),
)

PROMPT_CATEGORIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "identity": ("fact", ()),
    "person": ("person", ()),
    "preference": ("preference", ()),
    "instruction": ("preference", ("instruction",)),
    "project": ("project", ()),
    "skill": ("skill", ()),
    "event": ("event", ()),
    "place": ("place", ()),
    "other": ("fact", ()),
}
