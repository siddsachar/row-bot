"""The live eval's generative-UI tier (G8): its checks read a reply the way the app does."""
from __future__ import annotations

import json

import pytest

from tests.e2e.live_eval.genui import TASKS
from tests.e2e.live_eval.tasks import Context, Outcome, TurnResult, select_tasks

pytestmark = pytest.mark.subsystem


def _ui(title: str, components: list[dict], data: dict | None = None, **extra) -> str:
    body = {"title": title, "surfaceId": title.lower().replace(" ", "_"), "components": components,
            "dataModel": data or {}, **extra}
    return f"Here it is.\n\n```rowbot-ui\n{json.dumps(body)}\n```"


def _text(key: str, text: str) -> dict:
    return {"id": key, "component": "Text", "text": text}


def _check(task_id: str, *answers: str):
    task = {task.task_id: task for task in TASKS}[f"genui_{task_id}"]
    outcome = Outcome(turns=[TurnResult(prompt="p", answer=answer, status="done") for answer in answers])
    return task.check(outcome, Context())


TRIP = [{"id": "root", "component": "Column", "children": ["day", "budget"]}, _text("day", "Day 1: Alfama"),
        {"id": "budget", "component": "Slider", "label": "Budget", "min": 0, "max": 3000,
         "value": {"@path": "/budget"}}]
SEND = {"id": "send", "component": "Button", "child": "send_words", "action": {"event": {
    "name": "send", "context": {"name": {"@path": "/name"}, "phone": {"@path": "/phone"}}}}}


@pytest.mark.parametrize(("task_id", "answer"), [
    ("trip", _ui("Lisbon trip", TRIP, {"budget": 1500})),
    ("compare", _ui("Laptops", [
        {"id": "root", "component": "Column", "children": ["a", "b", "c", "pick"]},
        _text("a", "MacBook Air 13"), _text("b", "Dell XPS 13"), _text("c", "ThinkPad X1 Carbon"),
        {"id": "pick", "component": "ChoicePicker", "options": [{"label": "Air", "value": "air"}],
         "value": {"@path": "/pick"}}])),
    ("budget", _ui("Budget", [{"id": "root", "component": "Breakdown", "items": {"@path": "/parts"}}],
                   {"parts": [{"label": "Rent", "amount": 1200}, {"label": "Food", "amount": 350}]})),
    ("form", _ui("Dentist", [
        {"id": "root", "component": "Column", "children": ["name", "phone", "when", "send"]},
        {"id": "name", "component": "TextField", "label": "Name", "value": {"@path": "/name"}},
        {"id": "phone", "component": "TextField", "label": "Phone", "value": {"@path": "/phone"}},
        {"id": "when", "component": "DateTimeInput", "value": {"@path": "/when"}}, SEND, _text("send_words", "Send")])),
    ("private_file", _ui("October", [{"id": "root", "component": "Table", "columns": [
        {"key": "what", "label": "What"}, {"key": "amount", "label": "Amount"}], "rows": {"@path": "/spend"}}],
        sources={"/spend": {"@call": "rowbot.file", "args": {"path": "bank/oct.csv"}}})),
    ("plain", "The capital of Portugal is Lisbon."),
    ("checklist", "Pack these.\n\n```rowbot-checklist\n" + json.dumps(
        {"title": "Hike", "items": [{"text": "Boots"}, {"text": "Water"}]}) + "\n```"),
    ("savings", _ui("Bike", [{"id": "root", "component": "ProgressBar", "label": "Saved", "value": 1100, "max": 3000}])),
    ("tasks_source", _ui("Open tasks", [{"id": "root", "component": "List", "children": {
        "path": "/tasks", "componentId": "task"}}, _text("task", {"@path": "title"})],
        sources={"/tasks": {"@call": "rowbot.tasks"}})),
])
def test_a_reply_that_draws_what_was_asked_passes(task_id, answer) -> None:
    check = _check(task_id, answer)
    assert check.passed, check.reason


def test_the_redrawn_interface_must_keep_its_surface_id() -> None:
    four = TRIP[:1] + [_text("day", "Days 1 to 4")] + TRIP[2:]
    assert _check("redraw", _ui("Lisbon trip", TRIP), _ui("Lisbon trip", four)).passed
    moved = _check("redraw", _ui("Lisbon trip", TRIP), _ui("Lisbon trip 4 days", four))
    assert (moved.passed, moved.reason) == (False, "redrawn under another surfaceId")


@pytest.mark.parametrize(("task_id", "answer", "reason"), [
    ("plain", _ui("Capital", [_text("root", "Lisbon")]), "drew an interface for a one-line answer"),
    ("private_file", _ui("October", [{"id": "root", "component": "Table", "columns": [{"key": "a", "label": "A"}],
                                      "rows": [{"a": "487.23"}]}],
                         sources={"/spend": {"@call": "rowbot.file", "args": {"path": "bank/oct.csv"}}}),
     "copied the values ['487.23'] into the conversation"),
    ("trip", "Day 1: Alfama. Budget £1,500.", "no interface Row-Bot can show"),
    ("trip", "```rowbot-ui\n{not json\n```", "no interface Row-Bot can show (a fence that didn't parse)"),
])
def test_a_reply_that_misses_says_why(task_id, answer, reason) -> None:
    check = _check(task_id, answer)
    assert (check.passed, check.reason) == (False, reason)


def test_healing_shows_in_the_metrics() -> None:
    # A 0.8-style {"path"} binding heals into a surface; the fence itself was strict JSON.
    healed = _ui("Lisbon trip", TRIP).replace('{"@path": "/budget"}', '{"path": "/budget"}')
    check = _check("trip", healed)
    assert check.passed and check.metrics["surfaces"] == 1 and check.metrics["strict_json"] == 1


def test_the_genui_tier_runs_its_own_tasks_on_any_provider() -> None:
    chosen = [task.task_id for task in select_tasks("ollama", {"ROW_BOT_LIVE_EVAL_TIER": "genui"})]
    assert chosen == [task.task_id for task in TASKS] and len(chosen) == 10
    assert [task.task_id for task in select_tasks("codex", {"ROW_BOT_LIVE_EVAL_TIER": "genui",
                                                            "ROW_BOT_LIVE_EVAL_TASKS": "genui_plain"})] == ["genui_plain"]
