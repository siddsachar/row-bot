"""The real-runtime comparison measures (L2v, O59): the worker's own profile doesn't tell the model
its test apps are off or hand it a broken library, no task finds another's conversation or memories,
and every task that didn't pass says why (a code, never task text), with a turn trace only for a
developer's look at the visible packs."""

from __future__ import annotations

import dataclasses
import json

import pytest

from row_bot.learning import eval_worker
from row_bot.learning.evaluation import Arm, Budget, compare
from row_bot.learning.packs import visible_tasks

pytestmark = [pytest.mark.subsystem]


def _call(name: str, *, via: str = "", call_id: str = "", args: dict | None = None) -> tuple:
    from row_bot.agent import ToolCallPayload

    return ("tool_call", ToolCallPayload(name, raw_name=via or name, runtime_name=name, args=args or {},
                                         call_id=call_id or name))


def _done(name: str, content: str, *, via: str = "", call_id: str = "") -> tuple:
    return ("tool_done", {"tool_call_id": call_id or name, "raw_name": via or name, "name": name,
                          "content": content})


def _cause(events, *, status="failed", detail="", tools=("files",), check="answer") -> str:
    record = eval_worker.TurnRecord()
    for kind, payload in events:
        record.add(kind, payload)
    return eval_worker.failure_cause(status, detail, record, task_tools=tools, check=check)


WRITE_REFUSED = ("Error invoking tool 'workspace_write_file' with kwargs {'text': [{'name': 'Ari'}]} with error:\n"
                 " text: Input should be a valid string\n Please fix the error and try again.")


def test_each_failure_is_told_apart_by_what_the_turn_did():
    # The model call itself failed (the provider was down): outside the denominator, said so. The
    # runtime refusing the model's own malformed call is a failure instead (O70, test_learning_paired).
    assert _cause([], status="error", detail="the model call failed: the provider had a server error") \
        == "provider_error"
    assert _cause([], status="error", detail="the check could not run (OSError)") == "harness"
    assert _cause([_call("workspace_read_file")], status="budget", detail="ran out of steps") == "budget"
    # Answered without a single tool.
    assert _cause([("done", "")]) == "no_tool"
    # A calendar task whose turn only ever searched for tools: the app was never reached.
    assert _cause([_call("tool_search"), _done("tool_search", json.dumps({"ok": True, "results": []}))],
                  tools=("calendar",), check="app_state") == "tool_not_found"
    # The JSON text arrived as a list and the write was refused (O22).
    assert _cause([_call("workspace_write_file"), _done("workspace_write_file", WRITE_REFUSED)],
                  detail="people.json is not JSON", check="file") == "file_write"
    # A name the bridge doesn't know, never put right.
    assert _cause([_call("write_file", via="tool_invoke", call_id="c1"),
                   _done("tool_invoke", json.dumps({"ok": False, "error": {"code": "unknown_tool"}}),
                         call_id="c1")]) == "bad_call"
    # An app's own refusal counts too.
    assert _cause([_call("calendar_delete_event", via="tool_invoke", call_id="c1"),
                   _done("tool_invoke", json.dumps({"error": "no event 'Dentist'"}), call_id="c1")],
                  tools=("calendar",), check="app_state") == "bad_call"


def test_a_refusal_the_turn_put_right_is_not_the_reason_it_failed():
    events = [_call("workspace_read_file", call_id="a"), _done("workspace_read_file", "Error: file not found: x.md",
                                                               call_id="a"),
              _call("workspace_read_file", call_id="b"), _done("workspace_read_file", "Status: superseded\n60 EUR",
                                                               call_id="b"),
              ("done", "60")]

    assert _cause(events, detail="expected 85") == "wrong_answer"
    assert _cause(events, status="passed") == ""
    assert set(eval_worker.CAUSES) >= {"provider_error", "harness", "budget", "no_tool", "tool_not_found",
                                       "bad_call", "file_write", "no_answer", "wrong_answer"}


def test_the_check_reads_the_replys_last_message_not_the_progress_lines_before_its_tools():
    import threading

    events = [("token", "I'll look in the docs folder."), _call("workspace_list_directory"),
              _done("workspace_list_directory", "pol-114-travel.md"), ("token", "Reading the current policy."),
              _call("workspace_read_file"), _done("workspace_read_file", "Client dinners: up to 85 EUR"),
              ("token", "85"), ("done", "I'll look in the docs folder.Reading the current policy.85")]
    record = eval_worker.TurnRecord(keep=True)

    outcome = eval_worker._consume(iter(events), threading.Event(), 24, record)  # noqa: SLF001

    assert outcome["final"] == "85" and outcome["answer"].endswith("policy.85") and outcome["status"] == "done"
    assert {"done": "85"} in record.events
    assert {"reply": "I'll look in the docs folder.Reading the current policy.85"} in record.events
    # A reply with no tool step is its own last message.
    plain = eval_worker._consume(iter([("token", "42"), ("done", "42")]), threading.Event(), 24)  # noqa: SLF001
    assert plain["final"] == plain["answer"] == "42"


def test_a_trace_is_kept_only_when_asked_and_stays_bounded():
    quiet = eval_worker.TurnRecord()
    traced = eval_worker.TurnRecord(keep=True)
    for record in (quiet, traced):
        for _ in range(eval_worker.TRACE_EVENTS):
            record.add(*_call("workspace_list_directory"))
            record.add(*_done("workspace_list_directory", "a.md"))

    assert quiet.events == [] and len(quiet.calls) == eval_worker.TRACE_EVENTS
    assert len(traced.events) == eval_worker.TRACE_EVENTS
    assert traced.events[0] == {"call": "workspace_list_directory", "via": "", "args": ""}


class _Worker:
    """The worker as the runner sees it, remembering whether a trace was asked for."""

    def __init__(self, cause: str = "tool_not_found") -> None:
        self.asked: list[tuple[str, bool]] = []
        self.cause = cause

    def run(self, task, arm, *, budget, timeout=0, trace=False):
        self.asked.append((task["id"], trace))
        frame = {"type": "result", "case": {"task_id": task["id"], "status": "failed", "check": "app_state",
                                            "tokens": 900, "local_tokens": 900, "steps": 2, "seconds": 1.0,
                                            "detail": "calendar.events matching {}: 0, expected 1",
                                            "cause": self.cause}, "usage": []}
        if trace:
            frame["trace"] = [{"call": "tool_search", "via": "", "args": ""}]
        return frame


def _runner(worker, **options):
    return eval_worker.RuntimeRunner(worker, {"new": eval_worker.WorkerArm("new", "model:ollama:p", local=True)},
                                     **options)


def test_only_a_developers_look_at_visible_tasks_gets_the_turns_and_hidden_tasks_never_do():
    seen: list[tuple[str, str, list]] = []
    worker = _Worker()
    runner = _runner(worker, on_trace=lambda task, arm, case, trace: seen.append((task.id, arm, trace)))
    visible = next(task for task in visible_tasks() if task.pack == "tool_workflows")
    hidden = dataclasses.replace(visible, id="hidden_like", split="hidden")

    shown = runner.run(visible, Arm("new", "model:ollama:p"), budget=Budget(), meter=None, arm_left=None)
    unseen = runner.run(hidden, Arm("new", "model:ollama:p"), budget=Budget(), meter=None, arm_left=None)

    assert worker.asked == [(visible.id, True), ("hidden_like", False)]
    assert seen == [(visible.id, "new", [{"call": "tool_search", "via": "", "args": ""}])]
    assert shown.cause == unseen.cause == "tool_not_found"


def test_the_products_comparison_never_asks_for_a_trace_and_keeps_the_cause_with_each_case():
    worker = _Worker(cause="provider_error")
    runner = _runner(worker)
    tasks = [task for task in visible_tasks() if task.pack == "tool_workflows"][:2]

    result = compare([Arm("new", "model:ollama:p")], tasks=tasks, runner=runner, save=False)

    assert [trace for _, trace in worker.asked] == [False, False]
    assert [case.cause for case in result.arms[0].cases] == ["provider_error", "provider_error"]
    assert result.arms[0].causes == {"provider_error": 2}  # counts survive where cases don't (audit runs)


def test_a_cause_the_worker_doesnt_know_is_dropped():
    case = _runner(_Worker(cause="the task text")).run(
        visible_tasks()[0], Arm("new", "model:ollama:p"), budget=Budget(), meter=None, arm_left=None)

    assert case.cause == ""


def test_a_runtime_that_reports_no_usage_still_spends_the_tasks_token_budget():
    # llama.cpp's server streams no usage to Row-Bot here: without this its arm never ran out of tokens (E12).
    gate = eval_worker._TaskGate()  # noqa: SLF001 - the worker's budget gate
    gate.reset(30_000, None)
    allowance_for_output = 4_096

    gate.admit("call-1", 12_000 + allowance_for_output, local=True)
    gate.charge("call-1", 0, local=True)  # reported nothing: charged what it sent
    gate.admit("call-2", 12_000 + allowance_for_output, local=True)
    gate.charge("call-2", 0, local=True)

    assert gate.spent == 24_000 and gate.estimated == 2
    with pytest.raises(RuntimeError):
        gate.admit("call-3", 12_000 + allowance_for_output, local=True)
    assert gate.refused == "task"


def test_a_reported_usage_is_charged_as_reported_and_cloud_calls_count_toward_the_month():
    gate = eval_worker._TaskGate()  # noqa: SLF001
    gate.reset(150_000, 20_000)

    gate.admit("a", 9_000, local=False)
    gate.charge("a", 15_500, local=False)

    assert (gate.spent, gate.cloud_spent, gate.estimated) == (15_500, 15_500, 0)
    with pytest.raises(RuntimeError):
        gate.admit("b", 9_000, local=False)  # 15.5K + 9K would pass the month's 20K
    assert gate.refused == "limit"


# ── The worker's own profile ────────────────────────────────────────────────


@pytest.fixture
def registry_copy(data_dir, monkeypatch):
    """The tool registry, with whatever a test registers or switches undone afterwards."""

    from row_bot.tools import registry

    monkeypatch.setattr(registry, "_tools", dict(registry._tools))  # noqa: SLF001
    monkeypatch.setattr(registry, "_enabled", dict(registry._enabled))  # noqa: SLF001
    return registry


def test_the_test_apps_are_on_so_no_turn_lists_them_as_off(registry_copy):
    from row_bot import agent

    names = eval_worker._register_fake_apps()  # noqa: SLF001 - the worker's own setup step
    fields = {field.key: field.text for field in agent._runtime_turn_fields(  # noqa: SLF001
        ["filesystem", "learning_calendar", "row_bot_status"])}

    assert names == ["learning_calendar", "learning_mail", "learning_issues"]
    assert all(registry_copy.is_enabled(name) for name in names)
    assert "learning_calendar" in fields["tools"]
    assert not any(name in fields["tools_off"] for name in names), fields["tools_off"]


def test_the_documents_tool_answers_as_the_empty_library_it_is(registry_copy, monkeypatch):
    tool = registry_copy.get_tool("documents")
    assert tool is not None
    monkeypatch.setattr(tool, "execute", tool.execute)  # undone after the test

    assert eval_worker._empty_libraries() == ["documents"]  # noqa: SLF001
    answer = tool.as_langchain_tool().invoke({"query": "travel policy"})

    assert answer == "No results found for: travel policy"


def test_what_a_task_left_behind_goes_before_the_next_task(monkeypatch):
    from row_bot import knowledge_graph, thread_cleanup

    removed: list[str] = []
    memories = {"count": 2}
    monkeypatch.setattr(thread_cleanup, "delete_thread", lambda thread_id: removed.append(thread_id))
    monkeypatch.setattr(knowledge_graph, "count_entities", lambda: memories["count"])
    monkeypatch.setattr(knowledge_graph, "delete_all_entities", lambda: memories.update(count=0) or 2)

    eval_worker._forget_task("eval-1234")  # noqa: SLF001

    assert removed == ["eval-1234"] and memories["count"] == 0
