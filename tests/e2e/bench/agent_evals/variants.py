"""Bench-only arms for a paired A/B of a candidate Row-Bot change, applied inside the worker before any turn.

A variant changes one thing in this worker process only, records its name in every trajectory (through the
run's record), and is undone when the worker closes. Nothing here changes the app: a variant that wins goes
to Row-Bot as its own pull request, with the Learning chat's review when it touches a model's text.

- ``""``: Row-Bot as it ships.
- ``no-progress-note``: no "say what you found" note after silent tool rounds (``agent.SILENT_ROUNDS``).
- ``progress-note-with-call``: the note asks for that line *with* the next tool call, as the interactive
  progress contract already says ("each sent with its tool call"), so it no longer ends the turn.
"""
from __future__ import annotations

from typing import Callable

VARIANTS = ("", "no-progress-note", "progress-note-with-call")
NOTE_WITH_CALL = ("Row-Bot note, not from the person: you've worked through several tools without a word to the "
                  "person. Say in one short line what you found and what's next, and send it with your next tool "
                  "call; end your turn only when the work is done or you need the person.")


def apply(name: str) -> Callable[[], None]:
    if name not in VARIANTS:
        raise ValueError(f"unknown variant {name!r}; one of {VARIANTS}")
    import row_bot.agent as agent

    saved = (agent.SILENT_ROUNDS, agent.PROGRESS_NOTE)
    if name == "no-progress-note":
        agent.SILENT_ROUNDS = 10 ** 9
    elif name == "progress-note-with-call":
        agent.PROGRESS_NOTE = NOTE_WITH_CALL

    def undo() -> None:
        agent.SILENT_ROUNDS, agent.PROGRESS_NOTE = saved

    return undo
