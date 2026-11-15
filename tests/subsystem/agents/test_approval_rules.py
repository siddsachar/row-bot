"""D1 Always allow: the person's standing rules, decided in the approval gate.

Each action is shaped as its tool's gate shapes it (the payload and risks ``run_command``,
``developer_run_command``, the wrapped ``workspace_move_file`` and an app's tool pass) and decided by the
real gate; the person is a callback, the Auto reviewer a fake. An app's tools are classified by the MCP
runtime's own classifier. Approvals are security-sensitive: these stay in the pull request lane.
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from pathlib import Path

import pytest
from langchain_core.tools import tool
from langgraph.types import interrupt

from row_bot import activity_log, approval_review, approval_rules
from row_bot.approval_review import ReviewVerdict
from row_bot.approval_rules import APP_ASKS_RISK, CHANGE_RISK, COMMAND_RISK, DEVELOPER_COMMAND_RISK
from row_bot.mcp_client import runtime
from row_bot.tools import approval_gate
from tests.helpers.learning_turns import AgentTurns, ScriptedModel, asks, call, says

pytestmark = pytest.mark.subsystem


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    yield tmp_path / "data"
    activity_log.flush()


@pytest.fixture
def project(tmp_path) -> Path:
    folder = tmp_path / "row-bot"
    folder.mkdir()
    return folder


@pytest.fixture
def workspace(tmp_path, monkeypatch) -> Path:
    folder = tmp_path / "Documents"
    (folder / "Taxes").mkdir(parents=True)
    from row_bot.tools import registry

    real = registry.get_tool_config
    monkeypatch.setattr(registry, "get_tool_config", lambda tool, key, default=None: str(folder) if (
        tool, key) == ("filesystem", "workspace_root") else real(tool, key, default))
    return folder


@pytest.fixture
def notion(monkeypatch):
    """A connected Notion app: one routine change, one high-impact and one the classifier can't place."""

    tools = [{"name": "create_page", "description": "Create a page.", "inputSchema": {"type": "object"}},
             {"name": "update_page", "description": "Update a page's title.", "inputSchema": {"type": "object"}},
             {"name": "delete_page", "description": "Delete a page for good.", "inputSchema": {"type": "object"}},
             {"name": "frobnicate", "description": "Frobnicate.", "inputSchema": {"type": "object"}}]
    cfg = {"enabled": True, "tools": {"enabled": {item["name"]: True for item in tools}}}
    item = {"id": "mcp:notion", "kind": "mcp", "server": "Notion", "name": "Notion", "icon": "letter:N",
            "app": None, "owner_ref": "notion", "lifecycle": "installed", "target": None}
    monkeypatch.setattr("row_bot.integrations.scope._mcp_items", lambda strict=False: [item])
    with runtime._runtime_lock:
        runtime._catalog["Notion"] = runtime._normalize_tools("Notion", cfg, tools)
        for name in ("create_page", "update_page", "delete_page", "frobnicate"):
            runtime._issued[f"mcp_notion_{name}"] = ("Notion", name)
    yield
    with runtime._runtime_lock:
        runtime._catalog.pop("Notion", None)
        for name in ("create_page", "update_page", "delete_page", "frobnicate"):
            runtime._issued.pop(f"mcp_notion_{name}", None)


@pytest.fixture
def reviewer(monkeypatch):
    """The Auto reviewer, faked: every action it was asked about."""

    seen: list[dict] = []

    def review(payload, *, risks=(), **_kwargs):
        seen.append(dict(payload))
        return ReviewVerdict("ask", "You didn't ask for this.")

    monkeypatch.setattr(approval_review, "review", review)
    return seen


# ── Actions as their tools' gates shape them ─────────────────────────────────


def command(text: str, folder: Path, *risks: str) -> tuple[dict, tuple]:
    return ({"tool": "run_command", "label": "Run shell command", "description": f"Run shell command: {text}",
             "args": {"command": text}, "folder": folder.name, "folder_path": str(folder)},
            (COMMAND_RISK, *risks))


def developer_command(text: str, folder: Path) -> tuple[dict, tuple]:
    return ({"tool": "developer_run_command", "label": "Run Developer shell command",
             "description": f"Run in {folder.name}: {text}", "args": {"workspace": folder.name, "command": text},
             "folder_path": str(folder)}, (DEVELOPER_COMMAND_RISK,))


def move(source: str, destination: str) -> tuple[dict, tuple]:
    return ({"tool": "workspace_move_file", "label": "Move / rename file",
             "description": f"Move / rename file: source_path={source!r}, destination_path={destination!r}",
             "args": {"source_path": source, "destination_path": destination}}, (CHANGE_RISK,))


def app_tool(name: str) -> tuple[dict, tuple]:
    runtime_name = f"mcp_notion_{name}"
    return ({"tool": runtime_name, "label": runtime_name, "description": "Title: Trip notes",
             "args": {"title": "Trip notes"}}, (CHANGE_RISK, APP_ASKS_RISK))


def decide(action: tuple[dict, tuple], mode: str = "approve", answer: bool = True) -> tuple[str, str, list[dict]]:
    """The gate's decision for one action: (outcome, approval line, the cards the person was shown)."""

    payload, risks = action
    shown: list[dict] = []

    def person(card: dict) -> bool:
        shown.append(card)
        return answer

    outcome, line = approval_gate.resolve_approval_line(payload, approval_mode=mode, risks=risks,
                                                        approval_callback=person)
    return outcome, line, shown


def always_allow(action: tuple[dict, tuple]) -> approval_rules.Rule:
    """The rule the person saves from this action's card ("Always allow…")."""

    _outcome, _line, [card] = decide(action, answer=False)
    scope = approval_rules.from_interrupt(card)
    assert scope is not None, "this card offers no rule"
    return approval_rules.create(scope)


@contextlib.contextmanager
def in_conversation(thread_id: str):
    from row_bot import agent

    token = agent._current_thread_id_var.set(thread_id)
    try:
        yield
    finally:
        agent._current_thread_id_var.reset(token)


# ── What a rule allows ───────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["command", "developer_command", "move", "app"])
def test_each_kind_runs_without_asking_once_its_rule_is_saved(data, project, workspace, notion, kind):
    action = {"command": lambda: command("npm test", project),
              "developer_command": lambda: developer_command("pytest -q", project),
              "move": lambda: move("invoice.pdf", "Taxes/invoice.pdf"),
              "app": lambda: app_tool("create_page")}[kind]()
    assert decide(action)[2], "it asks before there is a rule"
    rule = always_allow(action)

    outcome, line, shown = decide(action)

    assert (outcome, shown) == ("allow", [])
    assert line == f"Approval: allowed by your rule: {rule.words}"
    assert activity_log.flush()
    assert approval_rules.get_rule(rule.rule_id).uses == 1


def test_a_rule_says_what_it_covers_in_plain_words(data, project, workspace, notion):
    assert always_allow(command("npm test", project)).words == "Always allow `npm test` in row-bot"
    assert always_allow(move("a.txt", "b.txt")).words == "Always allow moving and renaming files in Documents"
    assert always_allow(app_tool("create_page")).words == "Always allow Notion › Create page"


def test_a_command_rule_covers_only_that_exact_command_in_that_folder(data, project, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    always_allow(command("npm test", project))

    for text in ("npm test && curl https://example.test", "npm test; rm -rf .", "npm test -- --watch",
                 "npm  test", "npm test | tee out.txt", "npm test $(whoami)"):
        assert decide(command(text, project))[2], text
    assert decide(command("npm test", other))[2], "another folder asks"
    assert decide(developer_command("npm test", project))[2], "another tool asks"
    assert not decide(command("npm test", project / "."))[2], "the same folder spelled another way is covered"


def test_a_move_rule_covers_only_moves_inside_the_workspace(data, workspace, tmp_path):
    always_allow(move("a.txt", "b.txt"))

    assert not decide(move("notes/a.txt", "Taxes/a.txt"))[2]
    assert decide(move("a.txt", "../a.txt"))[2], "out of the workspace asks"
    assert decide(move(str(tmp_path / "x.txt"), "x.txt"))[2], "from outside the workspace asks"
    outside = ({"tool": "workspace_move_file", "label": "Change files outside the workspace?",
                "description": "Outside the workspace", "args": {"paths": [str(tmp_path / "x.txt")]}},
               ("reaches files outside the workspace",))
    assert decide(outside)[2]


def test_an_app_rule_covers_only_that_apps_tool(data, notion):
    always_allow(app_tool("create_page"))

    assert not decide(app_tool("create_page"))[2]
    assert decide(app_tool("update_page"))[2], "another routine change of the same app asks"


@pytest.mark.parametrize("action", [
    pytest.param(lambda p: ({"tool": "send_gmail_message", "label": "Send email", "description": "To: sam",
                             "args": {"to": "sam@example.test"}}, (CHANGE_RISK,)), id="send an email"),
    pytest.param(lambda p: command("git push origin main", p), id="git push"),
    pytest.param(lambda p: command("git commit -m wip", p), id="git commit"),
    pytest.param(lambda p: command("gh pr create --fill", p), id="pull request"),
    pytest.param(lambda p: command("pip install requests", p), id="install"),
    pytest.param(lambda p: command("rm -rf build", p), id="delete"),
    pytest.param(lambda p: command("curl https://example.test", p), id="network"),
    pytest.param(lambda p: command("npm run dev", p), id="start a server"),
    pytest.param(lambda p: command("npm test", p, "starts it in the background"), id="background"),
    pytest.param(lambda p: command("git config user.name Sam", p, approval_gate.GIT_IDENTITY_RISK), id="git identity"),
    pytest.param(lambda p: command("winget install ffmpeg", p, approval_gate.SYSTEM_INSTALL_RISK), id="system install"),
    pytest.param(lambda p: ({"tool": "start_process", "label": "Start a background process",
                             "description": "npm run dev", "args": {"command": "npm run dev"},
                             "folder_path": str(p)}, (COMMAND_RISK,)), id="start a process"),
    pytest.param(lambda p: app_tool("delete_page"), id="high-impact app tool"),
    pytest.param(lambda p: app_tool("frobnicate"), id="unknown app tool"),
    pytest.param(lambda p: ({"tool": "computer", "label": "Computer activity", "description": "Click Send"},
                            ("acts on this computer",)), id="computer use"),
    pytest.param(lambda p: ({**command("npm test", p)[0], "person_only": True}, (COMMAND_RISK,)), id="person only"),
    pytest.param(lambda p: ({**command("npm test", p)[0], "risk_class": "high"}, (COMMAND_RISK,)), id="high risk"),
    pytest.param(lambda p: ({"tool": "x_post", "label": "Post to X", "description": "hi", "x_post": {"text": "hi"}},
                            (approval_gate.X_POST_RISK,)), id="x post"),
    pytest.param(lambda p: ({"tool": "workspace_file_delete", "label": "Delete file", "description": "a.txt",
                             "args": {"file_path": "a.txt"}}, (CHANGE_RISK,)), id="delete a file"),
])
def test_no_rule_is_ever_offered_for_anything_else(data, project, workspace, notion, action):
    payload, risks = action(project)

    assert approval_rules.candidate(payload, risks) is None
    _outcome, _line, [card] = decide((payload, risks), answer=False)
    assert "always_allow" not in card and approval_rules.from_interrupt(card) is None


def test_a_stored_rule_that_isnt_one_of_the_kinds_is_never_used(data, project):
    rule = always_allow(command("npm test", project))
    with contextlib.closing(sqlite3.connect(data / "activity.db")) as connection, connection:
        connection.execute("UPDATE approval_rules SET command = 'rm -rf build' WHERE id = ?", (rule.rule_id,))

    assert decide(command("rm -rf build", project))[2]
    assert approval_rules.list_rules() == []


def test_editing_a_command_rule_is_checked_like_a_new_one(data, project):
    rule = always_allow(command("npm test", project))

    for refused in ("pip install x", "npm test && curl x", "rm -rf .", "npm run dev"):
        with pytest.raises(approval_rules.RuleError) as error:
            approval_rules.update(rule.rule_id, command=refused)
        assert error.value.code == "rule_command_refused"
    narrowed = approval_rules.update(rule.rule_id, command="npm test -- --run")

    assert narrowed.words == "Always allow `npm test -- --run` in row-bot"
    assert not decide(command("npm test -- --run", project))[2]
    assert decide(command("npm test", project))[2], "the old command asks again"


# ── Where rules never apply ──────────────────────────────────────────────────


def test_a_rule_applies_under_auto_without_asking_the_reviewer(data, project, reviewer):
    always_allow(command("npm test", project))

    outcome, line, shown = decide(command("npm test", project), mode="auto")

    assert (outcome, shown, reviewer) == ("allow", [], [])
    assert line.startswith("Approval: allowed by your rule: ")


def test_block_still_blocks_and_hands_off_needs_no_rule(data, project):
    always_allow(command("npm test", project))

    assert decide(command("npm test", project), mode="block")[0] == "block"
    assert decide(command("npm test", project), mode="full")[1] == approval_gate.APPROVAL_FULL_AUTONOMY


def test_a_rule_never_applies_where_other_people_share_the_conversation(data, project):
    from row_bot import agent_runs, tasks

    always_allow(command("npm test", project))
    tasks.record_thread_channel_ref("slack-thread-d1", channel="slack", target="C1", shared=True)
    agent_runs.create_agent_run(parent_thread_id="slack-thread-d1", thread_id="helper-of-slack-d1",
                                display_name="Helper", status="running", prompt="Synthetic task")

    with in_conversation("slack-thread-d1"):
        assert decide(command("npm test", project))[2]
    with in_conversation("helper-of-slack-d1"):
        assert decide(command("npm test", project))[2], "nor for a helper working for that conversation"
    with in_conversation("own-chat-d1"):
        assert not decide(command("npm test", project))[2]


def test_a_rule_never_answers_for_the_person(data, project):
    always_allow(command("npm test", project))
    payload, _risks = command("npm test", project)
    shown: list[dict] = []
    binding = approval_gate.call_binding("run_command", payload["args"])

    approved, _line = approval_gate.person_approval(payload, binding=binding,
                                                    approval_callback=lambda card: shown.append(card) or False)

    assert (approved, len(shown)) == (False, 1)


def test_an_unreadable_store_means_every_action_asks(data, project):
    always_allow(command("npm test", project))
    assert not decide(command("npm test", project))[2]
    assert activity_log.flush()

    (data / "activity.db").write_bytes(b"this is not a database" * 64)
    for suffix in ("-wal", "-shm"):
        (data / f"activity.db{suffix}").unlink(missing_ok=True)

    outcome, _line, shown = decide(command("npm test", project))
    assert (outcome, len(shown)) == ("allow", 1), "it asked the person, who approved"


def test_a_store_without_rules_asks(data, project):
    always_allow(command("npm test", project))
    with contextlib.closing(sqlite3.connect(data / "activity.db")) as connection, connection:
        connection.execute("DROP TABLE approval_rules")

    assert decide(command("npm test", project))[2]


def test_a_switched_off_rule_asks_and_switching_it_on_again_allows(data, project):
    rule = always_allow(command("npm test", project))
    approval_rules.update(rule.rule_id, enabled=False)
    assert decide(command("npm test", project))[2]

    approval_rules.update(rule.rule_id, enabled=True)
    assert not decide(command("npm test", project))[2]
    approval_rules.delete(rule.rule_id)
    assert decide(command("npm test", project))[2]


def test_at_most_a_hundred_rules(data, project):
    for index in range(approval_rules.MAX_RULES):
        approval_rules.create(approval_rules.Scope("command", "run_command", folder=str(project),
                                                   command=f"npm test -- {index}"))
    with pytest.raises(approval_rules.RuleError) as error:
        always_allow(command("npm test", project))
    assert error.value.code == "rules_full"


# ── A rule switched off while a call waits ───────────────────────────────────


def test_a_rule_switched_off_while_the_call_waits_is_decided_afresh(data, project, monkeypatch):
    done: list[str] = []

    @tool
    def test_then_tidy(target: str) -> str:
        """Run the tests (a rule covers it), then delete the build folder (asks)."""

        payload, risks = command("npm test", project)
        tested, line = approval_gate.resolve_approval_line(payload, approval_mode=approval_gate.current_approval_mode(),
                                                           risks=risks, approval_callback=lambda card: interrupt(card))
        if tested != "allow":
            return f"{line}\nThe tests didn't run."
        done.append("tests")
        refusal = approval_gate.gate_action({"tool": "delete_build", "label": "Delete the build folder",
                                             "description": f"Delete {target}"})
        if refusal:
            return refusal
        done.append("tidy")
        return "Tested and tidied."

    rule = always_allow(command("npm test", project))
    model = ScriptedModel(responses=[asks(call("test_then_tidy", "t1", target="build")), says("Done.")], seen=[])
    turns = AgentTurns(monkeypatch, model, [test_then_tidy])
    events = turns.send("Run the tests, then tidy up")
    assert any(kind == "interrupt" for kind, _ in events), "the delete asks"

    approval_rules.update(rule.rule_id, enabled=False)
    events = turns.answer(True, events)

    [cards] = [payload for kind, payload in events if kind == "interrupt"]
    assert [card["tool"] for card in cards] == ["run_command"], "the tests now ask the person"
    turns.answer(False, events)
    assert activity_log.flush()
    rows, _ = activity_log.query(limit=10)
    assert [row["outcome"] for row in rows] == ["denied"]


# ── Would this have asked? ───────────────────────────────────────────────────


def _row(call_id: str, outcome: str, *, days_ago: float, note: dict, person_only: bool = False,
         thread: str = "chat-dry-run") -> None:
    asked = {"stage": "asking", "level": "approve", "description": f"Run npm test ({call_id})",
             "person_only": person_only, **note}
    decided = {**asked, "stage": "decided", "outcome": {"denied": "deny"}.get(outcome, "allow"),
               "by": {"approved_by_reviewer": "reviewer", "allowed_in_chat": "chat_grant"}.get(outcome, "person")}
    decisions = (asked,) if outcome == "asked" else (asked, decided)
    activity_log.record(activity_log.ToolCall(call_id=call_id, tool="run_command", thread_id=thread,
                                              decisions=decisions, at=time.time() - days_ago * 86_400,
                                              paused=outcome == "asked"))


def test_the_dry_run_counts_what_the_rule_would_have_allowed(data, project, tmp_path):
    scope = approval_rules.Scope("command", "run_command", folder=str(project), command="npm test")
    other_folder = approval_rules.Scope("command", "run_command", folder=str(tmp_path), command="npm test")
    other_command = approval_rules.Scope("command", "run_command", folder=str(project), command="npm run lint")
    for index in range(3):
        _row(f"you-{index}", "approved_by_you", days_ago=index + 1, note=scope.note())
    _row("reviewer", "approved_by_reviewer", days_ago=2.5, note=scope.note())
    _row("no-1", "denied", days_ago=5, note=scope.note())
    _row("no-2", "denied", days_ago=1.5, note=scope.note())
    _row("waiting", "asked", days_ago=0.1, note=scope.note())
    _row("old", "approved_by_you", days_ago=31, note=scope.note())
    _row("elsewhere", "approved_by_you", days_ago=1, note=other_folder.note())
    _row("lint", "approved_by_you", days_ago=1, note=other_command.note())
    _row("mine-only", "approved_by_you", days_ago=1, note=scope.note(), person_only=True)
    assert activity_log.flush()

    result = approval_rules.dry_run(scope)

    assert result["total"] == 7
    assert result["counts"] == {"approved_by_you": 3, "denied": 2, "approved_by_reviewer": 1,
                                "allowed_in_chat": 0, "changed": 0, "asked": 1}
    assert [row["outcome"] for row in result["examples"]] == ["denied", "denied", "asked", "approved_by_you",
                                                             "approved_by_you"]
    assert [row["summary"] for row in result["examples"][:2]] == ["Run npm test (no-2)", "Run npm test (no-1)"]


def test_the_log_keeps_each_actions_scope_and_the_rule_that_allowed_it(data, project):
    from row_bot.tools.approval_gate import collect_activity

    with collect_activity() as asked:
        decide(command("npm test", project), answer=True)
    rule = always_allow(command("npm test", project))
    with collect_activity() as allowed:
        decide(command("npm test", project))
    for call_id, notes in (("first", asked), ("ruled", allowed)):
        activity_log.record(activity_log.ToolCall(call_id=call_id, tool="run_command", thread_id="chat-scope",
                                                  decisions=tuple(notes)))
    assert activity_log.flush()

    rows = {row["call_id"]: row for row in activity_log.query(limit=10)[0]}
    assert (rows["ruled"]["outcome"], rows["ruled"]["decided_by"], rows["ruled"]["rule_id"]) == (
        "allowed_by_rule", "rule", rule.rule_id)
    assert rows["first"]["kind"] == "command:run_command" and rows["first"]["scope_command"] == "npm test"
    assert approval_rules.dry_run(rule.scope)["counts"]["approved_by_you"] == 1


# ── Allow in this chat, in the gate ──────────────────────────────────────────


def test_allow_in_this_chat_answers_in_the_gate_for_the_chat_and_its_helpers(data, reviewer):
    from row_bot import agent_runs
    from row_bot.application import approval_grants

    write = ({"tool": "workspace_write_file", "label": "Write file", "description": "notes.txt"}, (CHANGE_RISK,))
    agent_runs.create_agent_run(parent_thread_id="chat-grants-d1", thread_id="helper-grants-d1",
                                display_name="Helper", status="running", prompt="Synthetic task")
    approval_grants.grant("chat-grants-d1", approval_grants.approval_kinds(write[0]), "chat")
    try:
        for thread in ("chat-grants-d1", "helper-grants-d1"):
            with in_conversation(thread):
                outcome, line, shown = decide(write, mode="auto")
                assert (outcome, line, shown) == ("allow", approval_gate.APPROVAL_ALLOWED_IN_CHAT, [])
        assert reviewer == [], "the reviewer wasn't asked"
        with in_conversation("another-chat-d1"):
            assert decide(write)[2]
        delete = ({"tool": "workspace_file_delete", "label": "Delete file", "description": "a.txt"}, (CHANGE_RISK,))
        with in_conversation("chat-grants-d1"):
            assert decide(delete)[2], "a delete still asks"
            git = ({"tool": "run_command", "label": "Run shell command", "description": "git config user.name",
                    "args": {"command": "git config user.name Sam"}}, (COMMAND_RISK, approval_gate.GIT_IDENTITY_RISK))
            approval_grants.grant("chat-grants-d1", approval_grants.approval_kinds(git[0]), "chat")
            assert decide(git, mode="auto")[2], "an action only the person approves still asks"
    finally:
        approval_grants.end_chat("chat-grants-d1")
