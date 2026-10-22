"""The live eval's ``learning`` tier (L0c, D31): the four task packs and replay fixtures, per provider.

``ROW_BOT_LIVE_EVAL_TIER=learning`` (``--tier learning``) runs, with the same reports
and caps as the other tiers:

- **pack tasks**: the visible split of each pack (``ROW_BOT_LIVE_EVAL_LEARNING_PER_PACK``
  tasks per pack: default 8, ``all`` for 16) as turns through the real Agent runtime, with
  Row-Bot's own filesystem tool on the task's workspace and the fake apps registered as
  tools in the worker. Each is checked by the pack's own verifier (code tests run in a
  sandbox with the network off).
- **replay fixtures**: synthetic captured decisions re-asked at their prefix through
  ``learning.evaluation``'s replay with the provider's model, checked against the
  reference action.

The hidden splits never run here: only the audit vault reads them. Planning imports
only ``row_bot.learning.packs`` (data, no I/O).
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Sequence

from .tasks import Check, Context, Outcome, Task, Turn

TIER = "learning"
PER_PACK_ENV = "ROW_BOT_LIVE_EVAL_LEARNING_PER_PACK"
# Eight a pack (32 pack tasks and the replay fixtures) fits the ChatGPT lane's 1.5M-token cap by the
# plan's upper-bound estimate; "all" runs the sixteen (a local model is free).
DEFAULT_PER_PACK = 8
APP_TOOLS = {"calendar": "learning_calendar", "mail": "learning_mail", "issues": "learning_issues"}
_EST = {"document_lookups": (3, 900), "structured_transforms": (3, 700), "small_coding": (3, 600),
        "tool_workflows": (5, 700)}


def per_pack(env: Mapping[str, str] | None = None) -> int:
    source = os.environ if env is None else env
    text = str(source.get(PER_PACK_ENV, "") or "").strip().lower()
    if text == "all":
        return 16
    if not text:
        return DEFAULT_PER_PACK
    value = int(text)
    if not 1 <= value <= 16:
        raise ValueError(f"{PER_PACK_ENV} must be 1 to 16 or all")
    return value


# ── Fake apps in the worker ────────────────────────────────────────────────


class _Current:
    """The fake apps of the task that is running (the worker runs one task at a time)."""

    apps: Any = None


CURRENT = _Current()


def register_tools() -> None:
    """Register the fake apps as Row-Bot tools in this (worker) process."""
    from langchain_core.tools import StructuredTool

    from row_bot.learning.packs.fake_apps import FakeApps
    from row_bot.tools import registry
    from row_bot.tools.base import BaseTool

    class FakeAppTool(BaseTool):
        def __init__(self, app: str, name: str) -> None:
            self._app, self._name = app, name

        @property
        def name(self) -> str:
            return self._name

        @property
        def display_name(self) -> str:
            return f"Test {self._app}"

        @property
        def description(self) -> str:
            return f"The {self._app} app of this test."

        @property
        def enabled_by_default(self) -> bool:
            return False

        def execute(self, query: str) -> str:
            return "Use the app's own tools."

        def as_langchain_tools(self) -> list:
            def bind(tool_name: str):
                return lambda **args: (CURRENT.apps or FakeApps()).call(tool_name, args)

            return [StructuredTool.from_function(func=bind(tool.name), name=tool.name, description=tool.description,
                                                 args_schema=tool.parameters())
                    for tool in FakeApps().tools([self._app])]

    for app, name in APP_TOOLS.items():
        if registry.get_tool(name) is None:
            registry.register(FakeAppTool(app, name))


# ── Pack tasks ─────────────────────────────────────────────────────────────


def _pack_task(task: Any) -> Task:
    from row_bot.learning.packs.verify import Outcome as PackOutcome
    from row_bot.learning.packs.verify import verify, workspace_files

    def seed(workspace: Any, _context: Context) -> None:
        from row_bot.learning.packs.fake_apps import FakeApps

        for name, content in task.files.items():
            path = workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))
        CURRENT.apps = FakeApps(task.apps)

    def check(outcome: Outcome, _context: Context) -> Check:
        failed = outcome.failed_turn()
        if failed is not None:
            return Check(False, f"turn did not finish ({failed.status}): {'; '.join(failed.errors)[:200]}")
        files = workspace_files(outcome.workspace) if outcome.workspace is not None else {}
        verdict = verify(task, PackOutcome(outcome.answer, files, outcome.collected.get("fake_apps") or task.apps))
        return Check(verdict.passed, verdict.detail, {"verifier": verdict.kind, "pack": task.pack})

    tools = (("filesystem",) if "files" in task.tools else ()) + tuple(
        APP_TOOLS[app] for app in task.tools if app in APP_TOOLS)
    calls, growth = _EST[task.pack]
    return Task(f"learn_{task.id}", f"learning:{task.pack}", lambda _context: (Turn(task.prompt),), check,
                tools=tools, est_calls=calls, est_growth_tokens=growth, seed=seed, collect=("learning_apps",),
                tier=TIER, note=task.kind_of_work)


# ── Replay fixtures ────────────────────────────────────────────────────────


def _file_tools() -> list[dict[str, Any]]:
    def tool(name: str, description: str, **properties: str) -> dict[str, Any]:
        return {"type": "function", "function": {"name": name, "description": description, "parameters": {
            "type": "object", "properties": {key: {"type": kind} for key, kind in properties.items()},
            "required": list(properties)}}}

    return [tool("list_files", "List the files in a folder of the workspace.", folder="string"),
            tool("read_file", "Read a text file from the workspace.", path="string")]


def replay_cases() -> list[Any]:
    """Synthetic captured decisions: a prefix, the tools that were offered, and what was right next."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage, message_to_dict

    from row_bot.learning.evaluation import Action, ReplayCase
    from row_bot.learning.packs.fake_apps import FakeApps

    system = SystemMessage(content="You are Row-Bot, a personal assistant. Use the tools when the task needs them.")

    def schemas(*apps: str) -> list[dict[str, Any]]:
        return [tool.openai_schema() for tool in FakeApps().tools(apps)]

    def case(case_id: str, messages: Sequence[Any], tools: list[dict[str, Any]], reference: Action, *,
             kind: str, source: str = "captured") -> Any:
        person = next(str(m.content) for m in reversed(messages) if isinstance(m, HumanMessage))
        return ReplayCase(case_id, "live-eval", "live-eval", 0.0, kind, "exact", False,
                          tuple(message_to_dict(m) for m in (system, *messages)), tuple(tools), None, reference,
                          source, "exact_action", person)  # type: ignore[arg-type]

    listing = {"name": "list_files", "args": {"folder": "docs"}, "id": "call_list", "type": "tool_call"}
    reading = {"name": "read_file", "args": {"path": "docs/pol-114-travel.md"}, "id": "call_read", "type": "tool_call"}
    return [
        case("replay_open_named_file", [HumanMessage(content="Read docs/pol-114-travel.md and tell me the client "
                                                             "dinner limit.")], _file_tools(),
             Action((("read_file", {"path": "docs/pol-114-travel.md"}),)), kind="Document lookups"),
        case("replay_answer_from_result", [
            HumanMessage(content="What is the client dinner limit in POL-114, in EUR? Reply with only the number."),
            AIMessage(content="", tool_calls=[reading]),
            ToolMessage(content="ID: POL-114\nTitle: Travel policy\nClient dinners: up to 85 EUR per person.",
                        tool_call_id="call_read")], _file_tools(), Action(text="85"), kind="Document lookups"),
        case("replay_pick_current_policy", [
            HumanMessage(content="Open the current travel policy. POL-097 is superseded; POL-114 replaced it."),
            AIMessage(content="", tool_calls=[listing]),
            ToolMessage(content="docs/pol-097-travel.md\ndocs/pol-114-travel.md", tool_call_id="call_list")],
             _file_tools(), Action((("read_file", {"path": "docs/pol-114-travel.md"}),)), kind="Document lookups",
             source="corrected"),
        case("replay_create_event", [HumanMessage(content="Add a calendar event called Budget review on 2026-03-10 "
                                                          "from 14:00 to 14:30.")], schemas("calendar"),
             Action((("calendar_create_event", {"title": "Budget review", "date": "2026-03-10", "start": "14:00",
                                                "end": "14:30"}),)), kind="Tool workflows"),
        case("replay_close_issue", [HumanMessage(content="Close issue 12 in the tracker.")], schemas("issues"),
             Action((("issues_update", {"issue_id": "12", "state": "closed"}),)), kind="Tool workflows"),
        case("replay_archive_message", [HumanMessage(content="Archive message msg_2, the newsletter.")],
             schemas("mail"), Action((("mail_archive", {"message_id": "msg_2"}),)), kind="Tool workflows"),
    ]


REPLAY_FIXTURES = ("replay_open_named_file", "replay_answer_from_result", "replay_pick_current_policy",
                   "replay_create_event", "replay_close_issue", "replay_archive_message")


def _replay_task(case_id: str) -> Task:
    def check(outcome: Outcome, _context: Context) -> Check:
        replay = outcome.collected.get("replay") or {}
        status = str(replay.get("status") or "not run")
        return Check(status == "passed", "" if status == "passed" else f"{status}: {replay.get('detail', '')}",
                     {"check": replay.get("check")})

    return Task(case_id, "learning:replay", lambda _context: (), check, est_calls=1, est_growth_tokens=0,
                est_output_tokens=300, collect=("learning_replay",), tier=TIER, note="replay at a captured prefix")


def run_replay(case_id: str, model_ref: str) -> dict[str, Any]:
    """In the worker: re-ask one replay fixture with the run's model (a ``learning_test`` call)."""
    from row_bot.learning.evaluation import Arm, LedgerModelClient, run_replay_case

    case = next(item for item in replay_cases() if item.decision_id == case_id)
    result = run_replay_case(case, Arm("live", model_ref=model_ref), client=LedgerModelClient())
    return {"status": result.status, "detail": result.detail, "check": result.check, "tokens": result.tokens}


def tasks(env: Mapping[str, str] | None = None) -> list[Task]:
    """The learning tier: pack tasks per pack, then the replay fixtures."""
    from row_bot.learning.packs import PACKS, visible_tasks

    count = per_pack(env)
    found = [_pack_task(task) for pack in PACKS for task in visible_tasks([pack])[:count]]
    return found + [_replay_task(case_id) for case_id in REPLAY_FIXTURES]


def all_tasks() -> list[Task]:
    """Every learning-tier task (for looking a task up by id in the worker)."""
    return tasks({PER_PACK_ENV: "all"})
