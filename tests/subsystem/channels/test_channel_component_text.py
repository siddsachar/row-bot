"""A chat app gets an answer component as plain text, never its JSON (F3)."""
from __future__ import annotations

import json

import pytest

from row_bot.channels.agent_output import assemble_agent_answer

pytestmark = pytest.mark.subsystem


def _fence(tag: str, body: dict) -> str:
    return f"```rowbot-{tag}\n{json.dumps(body)}\n```"


def test_each_component_reaches_a_chat_app_as_words():
    answer = assemble_agent_answer("\n\n".join([
        "Here you go.",
        _fence("table", {"columns": [{"key": "a", "label": "Item"}, {"key": "b", "label": "Cost"}],
                         "rows": [{"a": "Rent", "b": "£1,200"}, {"a": "Food", "b": 320}]}),
        _fence("checklist", {"title": "Today", "items": [{"text": "Call Sam", "done": True}, {"text": "Shop"}]}),
        _fence("figures", {"items": [{"label": "Total", "value": 1520, "unit": "£", "delta": "-8%"}]}),
        _fence("progress", {"items": [{"label": "Saved", "value": 300, "target": 1200, "unit": "£"}]}),
        _fence("chart", {"title": "Spend", "kind": "bar", "x": ["Sep", "Oct"],
                         "series": [{"name": "Total", "values": [1650, 1520]}], "unit": "£"}),
        _fence("choices", {"prompt": "Next?", "options": [{"label": "Cut food", "reply": "Help me cut food costs"},
                                                          {"label": "Done", "reply": "That's all"}]}),
    ]), [])

    assert "rowbot-" not in answer and "{" not in answer
    assert "| Item | Cost |\n| --- | --- |\n| Rent | £1,200 |\n| Food | 320 |" in answer
    assert "Today\n- [x] Call Sam\n- [ ] Shop" in answer
    assert "Total: £1,520 (-8%)" in answer
    assert "Saved: £300 of £1,200 (25%)" in answer
    assert "Spend\nSep: Total £1,650\nOct: Total £1,520" in answer
    assert "Next?\n1. Cut food\n2. Done" in answer


def test_a_fence_that_does_not_fit_is_sent_as_written():
    broken = "```rowbot-table\n{not json\n```"
    assert assemble_agent_answer(f"Look:\n\n{broken}", []) == f"Look:\n\n{broken}"


def test_a_component_fenced_as_json_reaches_a_chat_app_as_words_too():
    body = {"title": "Today", "items": [{"text": "Call Sam", "done": True}, {"text": "Shop"}]}
    answer = assemble_agent_answer(f"Your list:\n\n```json\n{json.dumps(body)}\n```", [])
    assert answer == "Your list:\n\nToday\n- [x] Call Sam\n- [ ] Shop"


def test_a_visual_reaches_a_chat_app_as_a_pointer_never_its_code():
    answer = assemble_agent_answer("See:\n\n```rowbot-visual\n<svg><title>Orbit</title><script>x()</script></svg>\n```", [])
    assert answer == "See:\n\n[Visual: Orbit: open the chat in Row-Bot to see it]"
