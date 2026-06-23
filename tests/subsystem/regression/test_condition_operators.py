from __future__ import annotations

import sys
import types

import pytest

from tests.fixtures.tasks import fresh_tasks_module


pytestmark = pytest.mark.subsystem


def test_condition_operator_matrix(tmp_path, monkeypatch) -> None:
    tasks = fresh_tasks_module(tmp_path, monkeypatch)
    evaluate = tasks.evaluate_condition

    assert evaluate("true", {"prev_output": ""}) is True
    assert evaluate("false", {"prev_output": ""}) is False
    assert evaluate("empty", {"prev_output": "  "}) is True
    assert evaluate("not_empty", {"prev_output": "hello"}) is True
    assert evaluate("contains:quick", {"prev_output": "The Quick Brown Fox"}) is True
    assert evaluate("not_contains:zebra", {"prev_output": "The Quick Brown Fox"}) is True
    assert evaluate("equals:hello", {"prev_output": "hello"}) is True
    assert evaluate("equals:Hello", {"prev_output": "hello"}) is False
    assert evaluate(r"matches:#\d+", {"prev_output": "Order #12345"}) is True
    assert evaluate(r"matches:[invalid", {"prev_output": "Order #12345"}) is False

    numeric_context = {"prev_output": "Score: 75 points"}
    assert evaluate("gt:50", numeric_context) is True
    assert evaluate("lt:80", numeric_context) is True
    assert evaluate("gte:75", numeric_context) is True
    assert evaluate("lte:75", numeric_context) is True
    assert evaluate("gt:abc", numeric_context) is False
    assert evaluate("gt:50", {"prev_output": "no numbers"}) is False
    assert evaluate("length_gt:3", {"prev_output": "hello"}) is True
    assert evaluate("length_lt:10", {"prev_output": "hello"}) is True
    assert evaluate("unknown_op:val", {"prev_output": ""}) is False


def test_json_and_compound_conditions(tmp_path, monkeypatch) -> None:
    tasks = fresh_tasks_module(tmp_path, monkeypatch)
    evaluate = tasks.evaluate_condition
    context = {
        "prev_output": '{"status": "active", "score": 85, "items": [{"name": "a"}, {"name": "b"}]}',
        "step_outputs": {"step_1": "first"},
        "task_id": "task-1",
    }

    assert evaluate("json:status:equals:active", context) is True
    assert evaluate("json:score:gt:50", context) is True
    assert evaluate("json:items.0.name:equals:a", context) is True
    assert evaluate("json:missing:equals:x", context) is False
    assert evaluate("json:status", context) is False
    assert evaluate("json:key:equals:val", {"prev_output": "not json"}) is False
    assert evaluate("and:[json:status:equals:active,json:score:gt:50]", context) is True
    assert evaluate("and:[json:status:equals:active,json:score:gt:90]", context) is False
    assert evaluate("or:[json:status:equals:inactive,json:score:gt:80]", context) is True
    assert evaluate("and:[]", context) is True
    assert evaluate("or:[]", context) is False
    assert evaluate("and:[not_empty,or:[contains:active,contains:critical]]", context) is True
    assert tasks._split_compound("a,or:[b,c],d") == ["a", "or:[b,c]", "d"]


def test_llm_condition_prompt_and_response_handling(tmp_path, monkeypatch) -> None:
    tasks = fresh_tasks_module(tmp_path, monkeypatch)
    captured: list[tuple[str, list, dict]] = []
    fake_agent = types.ModuleType("row_bot.agent")

    def fake_invoke(prompt, tools, config, **_kwargs):
        captured.append((prompt, tools, config))
        return "Yes, definitely"

    fake_agent.invoke_agent = fake_invoke
    monkeypatch.setitem(sys.modules, "row_bot.agent", fake_agent)

    result = tasks.evaluate_condition(
        "llm:Is it good?",
        {
            "prev_output": "main output here",
            "step_outputs": {"step_1": "first", "step_2": "second"},
            "task_id": "task-1",
        },
    )

    assert result is True
    assert captured[0][1] == []
    assert "main output here" in captured[0][0]
    assert "[step_1]" in captured[0][0]
    assert "second" in captured[0][0]
    assert captured[0][2]["configurable"]["runtime_surface"] == "workflow"

    fake_agent.invoke_agent = lambda *_args, **_kwargs: "no"
    assert tasks.evaluate_condition("llm:test", {"prev_output": ""}) is False
    fake_agent.invoke_agent = lambda *_args, **_kwargs: ""
    assert tasks.evaluate_condition("llm:test", {"prev_output": ""}) is False
    fake_agent.invoke_agent = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline"))
    assert tasks.evaluate_condition("llm:test", {"prev_output": ""}) is False

    long_context = {"prev_output": "x" * 40_000, "step_outputs": {}, "task_id": ""}
    fake_agent.invoke_agent = fake_invoke
    captured.clear()
    tasks.evaluate_condition("llm:test", long_context)
    assert "[... truncated ...]" in captured[0][0]
    assert len(captured[0][0]) < 40_000


def test_condition_parse_step_id_and_mermaid_helpers(tmp_path, monkeypatch) -> None:
    tasks = fresh_tasks_module(tmp_path, monkeypatch)
    from row_bot.ui.task_dialog import _parse_condition_expr

    assert _parse_condition_expr("contains:hello") == ("contains:", "hello")
    assert _parse_condition_expr("empty") == ("empty", "")
    assert _parse_condition_expr("json:status:equals:success") == ("json:", "status:equals:success")
    assert _parse_condition_expr("llm:Is it good?") == ("llm:", "Is it good?")
    assert _parse_condition_expr("and:[a,b]") == (None, "")

    steps = [
        {"id": "old_a", "type": "prompt", "prompt": "a"},
        {"id": "old_b", "type": "condition", "condition": "not_empty", "if_true": "old_c", "if_false": "end"},
        {"id": "old_c", "type": "prompt", "prompt": "b", "next": "old_a"},
        {"type": "notify", "message": "done", "channel": "desktop"},
    ]
    tasks.assign_step_ids(steps)

    assert [step["id"] for step in steps] == ["prompt_1", "condition_1", "prompt_2", "notify_1"]
    assert steps[1]["if_true"] == "prompt_2"
    assert steps[2]["next"] == "prompt_1"
    assert tasks._resolve_step_index(steps, "prompt_2") == 2
    assert tasks._resolve_step_index(steps, "end") is None

    mermaid = tasks.generate_pipeline_mermaid(steps)
    assert mermaid.startswith("graph TD")
    assert "condition_1" in mermaid
    assert '-->|"Yes"|' in mermaid
    assert "desktop" in mermaid
    assert "None" not in mermaid
    assert tasks.generate_pipeline_mermaid([]) == ""

    colon_steps = [
        {"id": "condition_1", "type": "condition", "condition": "llm:Were any results found?", "if_true": "end", "if_false": "end"},
        {"id": "approval_1", "type": "approval", "message": "Approve?"},
    ]
    colon_mermaid = tasks.generate_pipeline_mermaid(colon_steps)
    assert '{"' in colon_mermaid
    assert '"}' in colon_mermaid
    assert "llm" in colon_mermaid
