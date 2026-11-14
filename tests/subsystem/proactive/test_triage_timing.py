"""Triage judges a change for when it matters, not only for right now (found in the 5.3.4 real-user test).

A promise due tomorrow at 5pm was dropped because triage was asked whether it was worth interrupting
for "now"; Row-Bot's timing already holds a moment until it matters, so triage is told when that is.
"""

from __future__ import annotations

import json
from datetime import datetime

from row_bot.proactive import triage
from row_bot.proactive.model import Candidate, Evidence


def _at(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%d %H:%M").timestamp()


def _change(due_at: float | None) -> Candidate:
    return Candidate(kind="follow_up", key="promise:c:1", title="You said you'd send Sam the shortlist",
                     summary="The person said they'd send Sam the venue shortlist by tomorrow at 5pm.",
                     evidence=(Evidence("conversation", "c", "c", "chats", seen="1"),), due_at=due_at)


def _sent(candidate: Candidate, now: float) -> dict:
    prompt = triage.messages([candidate], level="quiet", rules=[], recent=[], now=now)
    return json.loads(prompt[1]["content"])["changes"][0]


def test_triage_is_told_when_each_change_matters() -> None:
    now = _at("2026-11-14 17:27")
    assert _sent(_change(_at("2026-11-15 09:00")), now)["matters_at"] == "tomorrow 09:00"
    assert _sent(_change(_at("2026-11-14 18:00")), now)["matters_at"] == "today 18:00"
    assert _sent(_change(_at("2026-11-18 09:00")), now)["matters_at"] == "Wed 18 Nov 09:00"
    assert _sent(_change(None), now)["matters_at"] == "now"
    assert _sent(_change(_at("2026-11-14 09:00")), now)["matters_at"] == "now"  # Already due.


def test_a_kept_future_change_reaches_the_engine_with_its_time() -> None:
    now = _at("2026-11-14 17:27")
    seen: list[list[dict[str, str]]] = []

    def model(prompt: list[dict[str, str]]) -> str:
        seen.append(prompt)
        return json.dumps({"decisions": [{"id": "c1", "keep": True, "title": "Send Sam the shortlist", "body": ""}]})

    kept, words = triage.decide([_change(_at("2026-11-15 09:00"))], level="quiet", rules=[], recent=[],
                                call=model, now=now)
    assert [title for _candidate, title, _body in kept] == ["Send Sam the shortlist"]
    assert json.loads(seen[0][1]["content"])["changes"][0]["matters_at"] == "tomorrow 09:00"
    assert words.endswith("1 worth a mention, 0 silent.")
