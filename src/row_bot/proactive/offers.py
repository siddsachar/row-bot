"""Make it a workflow: what happens when the person answers Row-Bot's one offer about an ask they repeat.

- **Make it a workflow** (``make_workflow``): a draft workflow made through the normal workflow creation
  path, from the person's own words. It is off and unscheduled, at the approval level new workflows get,
  so nothing runs until the person has reviewed it, chosen when it runs and turned it on. It is only ever
  made when they choose it; nothing here is created silently.
- **Not this** (dismissing the offer, or "less like this"): remembered for good. No ask like it is offered
  again, ever (``declined``; the chats watcher checks it).
"""
from __future__ import annotations

from typing import Any

from row_bot.proactive import store

NAME_CHARS = 60
PROMPT_CHARS = 2000


def _ask(moment: dict[str, Any]) -> tuple[str, list[str]]:
    """The ask the offer is about, in the person's words, and its words as the watcher compares them."""
    for item in moment["evidence"]:
        if item["type"] == "ask" and item.get("label"):
            return str(item["label"]), str(item.get("seen") or "").split()
    return "", str(moment.get("topic") or "").split()


def declined() -> list[list[str]]:
    """Every ask the person said no to making a workflow of (kept for good)."""
    return list(store.get_state("declined_offers") or [])


def decline(moment: dict[str, Any], *, now: float) -> None:
    """The person said no to the offer: never offered again for an ask like it."""
    _text, words = _ask(moment)
    if not words:
        return
    store.set_state("declined_offers", [*declined(), sorted(words)])
    store.log("rule", "Won't offer to make that ask a workflow again.", now=now, moment_id=moment["id"])


def _name(ask: str) -> str:
    words = " ".join(ask.split()).rstrip(" ?.!")
    if len(words) > NAME_CHARS:
        words = words[:NAME_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:")
    return (words[:1].upper() + words[1:]) or "From your chats"


def make_workflow(moment: dict[str, Any], *, now: float) -> str:
    """A draft workflow of the repeated ask (off, unscheduled) for the person to review; its id."""
    from row_bot import tasks

    ask, _words = _ask(moment)
    ask = ask or moment["summary"]
    chats = sum(item["type"] == "ask" for item in moment["evidence"])
    task_id = tasks.create_task(
        name=_name(ask), prompts=[ask[:PROMPT_CHARS]], icon="⚡",
        description=(f"Drafted by Row-Bot from something you asked in {chats} chats. Review the message, choose "
                     "when it runs, then turn it on."),
        schedule=None, enabled=False, agent_profile_id=tasks.DEFAULT_WORKFLOW_AGENT_PROFILE_ID,
        apply_default_skills=False)
    store.log("moment", f"Drafted the workflow “{_name(ask)}” from your repeated ask. It stays off until you "
              "review it and choose when it runs.", now=now, moment_id=moment["id"], detail={"task_id": task_id})
    return task_id
