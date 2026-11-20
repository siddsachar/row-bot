"""Always allow (D1): the person's standing rules for a few low-risk kinds of action.

A rule lets one kind of action run without asking, in any of the person's own
chats, until they turn it off. The kinds are a closed whitelist, decided by
code from the action itself (its tool, the effect class Row-Bot works out for
an app's tool, the exact command text and the folder), never by any words a
tool or a model wrote:

1. **An app's routine change**: one app tool whose effect class is a routine
   ``mutation`` (never high impact, never unknown), scoped to that app and tool.
2. **One exact command in one folder** (``run_command``, ``developer_run_command``):
   only an ordinary command (never an install, network use, a delete, a commit,
   a push, a pull request or a server), with no shell operators, no patterns and
   no prefixes.
3. **Moving or renaming files inside the workspace folder** (``workspace_move_file``).

A tool that is not named here can never become a rule. Rules apply only under
Ask and Auto, never in a conversation other people share, never to an action
only the person may approve (``approval_gate``). A rule starts only from a real
approval card: the gate attaches the action's scope to the card it raises, and
the server makes the rule from that stored card, so a client never says what a
rule covers.

Rules live in the activity log's file (``activity.db``, table ``approval_rules``),
at most 100. Reading them fails closed: when the store can't be read, nothing
matches and every action asks as it does without rules.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import sqlite3
import time
from collections.abc import Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_RULES = 100
DRY_RUN_DAYS = 30
DRY_RUN_EXAMPLES = 5
COMMAND_MAX = 500

KINDS = ("app", "command", "move")
COMMAND_TOOLS = frozenset({"run_command", "developer_run_command"})
MOVE_TOOL = "workspace_move_file"
# What classify_command_action may call a command a rule can cover: an ordinary one.
_COMMAND_CLASSES = frozenset({"run_command", "run_safe_command"})
# Anything that joins, redirects or substitutes: a rule covers one plain command.
_SHELL_SYNTAX = re.compile(r"[;&|<>`\r\n\x00]|\$\(|\$\{")

# The risks each gate names for an ordinary action of its kind. Any other risk (a background start, Git's
# identity, a system install, private data, files outside the workspace) means the action is not one a rule
# may cover: an unknown risk always counts against it.
COMMAND_RISK = "runs a command that can change things on this computer"
CHANGE_RISK = "changes or deletes something"
APP_ASKS_RISK = "its app's access says to ask first (a high-impact change, or one without Full access)"
DEVELOPER_COMMAND_RISK = "a Developer action in the conversation's code folder: Run Developer shell command"
_ROUTINE_RISKS = {
    "run_command": frozenset({COMMAND_RISK}),
    "developer_run_command": frozenset({DEVELOPER_COMMAND_RISK}),
    MOVE_TOOL: frozenset({CHANGE_RISK}),
    "app": frozenset({CHANGE_RISK, APP_ASKS_RISK}),
}
# The activity log's outcomes the dry run counts: what asked (or would have) before it ran.
DRY_RUN_OUTCOMES = ("approved_by_you", "denied", "approved_by_reviewer", "allowed_in_chat", "changed", "asked")

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS approval_rules ("
    "id TEXT PRIMARY KEY, "
    "kind TEXT NOT NULL, "
    "tool TEXT NOT NULL, "
    "app_id TEXT NOT NULL DEFAULT '', "
    "app_name TEXT NOT NULL DEFAULT '', "
    "tool_title TEXT NOT NULL DEFAULT '', "
    "folder TEXT NOT NULL DEFAULT '', "
    "command TEXT NOT NULL DEFAULT '', "
    "words TEXT NOT NULL DEFAULT '', "
    "enabled INTEGER NOT NULL DEFAULT 1, "
    "created_at REAL NOT NULL, "
    "created_from TEXT NOT NULL DEFAULT 'card', "
    "last_used_at REAL, "
    "uses INTEGER NOT NULL DEFAULT 0)"
)


class RuleError(ValueError):
    """A rule can't be made or changed; ``code`` says why in a stable, client-safe word."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


# ── What a rule covers ───────────────────────────────────────────────────────


def _folder_key(folder: str) -> str:
    return os.path.normcase(os.path.normpath(folder)) if folder else ""


def _clean(value: Any, maximum: int) -> str:
    return " ".join(str(value or "").split())[:maximum]


@dataclass(frozen=True)
class Scope:
    """One action's kind and exact scope: what a rule for it would cover."""

    kind: str
    tool: str
    app_id: str = ""
    folder: str = ""
    command: str = ""
    app_name: str = ""
    tool_title: str = ""

    def key(self) -> tuple[str, str, str, str, str]:
        return (self.kind, self.tool, self.app_id, _folder_key(self.folder), self.command)

    @property
    def folder_name(self) -> str:
        return Path(self.folder).name or self.folder if self.folder else ""

    def words(self) -> str:
        """The rule in plain words, as the person reads it and as its approval line says it."""

        if self.kind == "app":
            return f"Always allow {self.app_name} › {self.tool_title}"
        if self.kind == "command":
            return f"Always allow `{self.command}` in {self.folder_name}"
        return f"Always allow moving and renaming files in {self.folder_name}"

    def card(self) -> dict[str, str]:
        """What the gate keeps on the card it raises, so a rule can be made from that card later."""

        return {"kind": self.kind, "tool": self.tool, "app_id": self.app_id, "folder": self.folder,
                "command": self.command, "app_name": self.app_name, "tool_title": self.tool_title}

    def note(self) -> dict[str, str]:
        """For the activity log's row, so the dry run finds the actions a rule would have covered."""

        return {"rule_kind": f"{self.kind}:{self.tool}", "rule_app": self.app_id,
                "rule_folder": _folder_key(self.folder), "rule_command": self.command}


def command_ok(command: str, folder: str = "") -> bool:
    """Whether one command may be a rule: plain, ordinary, and nothing private."""

    text = str(command or "")
    if not text or text != text.strip() or len(text) > COMMAND_MAX or _SHELL_SYNTAX.search(text):
        return False
    from row_bot.developer.runtime import classify_command_action, has_shell_control_operator

    if has_shell_control_operator(text) or classify_command_action(text) not in _COMMAND_CLASSES:
        return False
    from row_bot.tools.shell_tool import names_private_data

    return not names_private_data(text, folder or None)


def _folder(value: Any) -> str:
    """A folder that exists, resolved; '' otherwise."""

    text = str(value or "").strip()
    if not text:
        return ""
    try:
        path = Path(text).expanduser().resolve()
    except (OSError, ValueError):
        return ""
    if not path.is_dir():
        return ""
    from row_bot.data_paths import is_private_data_path

    return "" if is_private_data_path(path) else str(path)


def _command_scope(tool: str, payload: Mapping[str, Any]) -> Scope | None:
    args = payload.get("args")
    if not isinstance(args, Mapping):
        return None
    command = str(args.get("command") or "")
    folder = _folder(payload.get("folder_path"))
    if not folder or not command_ok(command, folder):
        return None
    return Scope("command", tool, folder=folder, command=command)


def workspace_root() -> str:
    """The workspace folder the file tools work in, resolved; '' when there is none."""

    from row_bot.tools import registry

    root = str(registry.get_tool_config("filesystem", "workspace_root", "") or "")
    if not root:
        from row_bot.data_paths import default_workspace_root

        root = str(default_workspace_root())
    return _folder(root)


def _move_scope(payload: Mapping[str, Any]) -> Scope | None:
    args = payload.get("args")
    if not isinstance(args, Mapping) or set(args) - {"source_path", "destination_path"}:
        return None
    source, destination = args.get("source_path"), args.get("destination_path")
    if not (isinstance(source, str) and isinstance(destination, str) and source.strip() and destination.strip()):
        return None
    root = workspace_root()
    if not root:
        return None
    from row_bot.data_paths import is_private_data_path
    from row_bot.tools.filesystem_tool import _absolute, _within

    for value in (source, destination):
        path = _absolute(value, root)
        if not _within(path, root) or is_private_data_path(path):
            return None
    return Scope("move", MOVE_TOOL, folder=root)


def _app_scope(tool: str) -> Scope | None:
    from row_bot.integrations.scope import app_for_tool
    from row_bot.mcp_client import runtime

    issued = runtime.issued_tool(tool)
    if issued is None:
        return None
    info = runtime.tool_info(*issued)
    if info is None or info.destructive or info.locked or info.effect != "mutation":
        return None
    app = app_for_tool(tool)
    if not app or not app.get("item_id"):
        return None
    return Scope("app", tool, app_id=str(app["item_id"]), app_name=_clean(app.get("name"), 128),
                 tool_title=_clean(app.get("tool") or runtime.tool_title(tool), 96))


def _scope(payload: Mapping[str, Any]) -> Scope | None:
    tool = str(payload.get("tool") or "")
    if tool in COMMAND_TOOLS:
        return _command_scope(tool, payload)
    if tool == MOVE_TOOL:
        return _move_scope(payload)
    if tool.startswith("mcp_"):
        return _app_scope(tool)
    return None


def _routine(tool: str, risks: Iterable[str]) -> bool:
    allowed = _ROUTINE_RISKS.get("app" if tool.startswith("mcp_") else tool)
    return allowed is not None and {str(risk) for risk in risks} <= allowed


def conversations_here() -> list[str]:
    """The conversation this action runs in, then the ones behind it when it is a helper agent's."""

    from row_bot.agent import _current_thread_id_var

    current = str(_current_thread_id_var.get("") or "")
    if not current:
        try:
            from langgraph.config import get_config

            current = str((get_config().get("configurable") or {}).get("thread_id") or "")
        except RuntimeError:
            current = ""  # Not inside a run.
    found: list[str] = []
    if not current:
        return found
    from row_bot.agent_runs import get_agent_run_for_thread

    while current and current not in found:
        found.append(current)
        if len(found) > 4:
            raise RuntimeError("helper chain too deep")
        current = str((get_agent_run_for_thread(current) or {}).get("parent_thread_id") or "")
    return found


def _shared_here() -> bool:
    """Whether this action is in a conversation other people share, or under one (a helper's). Fails closed."""

    try:
        from row_bot.tasks import get_thread_channel_ref

        for conversation in conversations_here():
            ref = get_thread_channel_ref(conversation)
            if ref and int(ref.get("shared") or 0):
                return True
        return False
    except Exception:
        logger.debug("Couldn't tell whether this conversation is shared; no rule applies", exc_info=True)
        return True


def candidate(payload: Mapping[str, Any], risks: Iterable[str] = (), *, person_only: bool = False) -> Scope | None:
    """The scope a rule for this action would cover, or None when no rule may ever cover it.

    None for an action only the person approves, anything high risk, a kind not on the whitelist, an
    action with any risk beyond its kind's ordinary one, and anything in a shared conversation.
    """

    if person_only or payload.get("person_only") or payload.get("binding") or payload.get("x_post"):
        return None
    if str(payload.get("risk_class") or "").casefold() in {"high", "critical"}:
        return None
    tool = str(payload.get("tool") or "")
    if not _routine(tool, risks):
        return None
    try:
        scope = _scope(payload)
    except Exception:
        logger.debug("No rule scope for %s", tool[:80], exc_info=True)
        return None
    if scope is None or _shared_here():
        return None
    return scope


def from_card(card: Any) -> Scope | None:
    """The scope a stored card offers a rule for, checked again now; None when it no longer qualifies."""

    if not isinstance(card, Mapping) or card.get("kind") not in KINDS:
        return None
    tool = str(card.get("tool") or "")
    kind = str(card["kind"])
    try:
        if kind == "command":
            if tool not in COMMAND_TOOLS:
                return None
            return _command_scope(tool, {"args": {"command": card.get("command")}, "folder_path": card.get("folder")})
        if kind == "move":
            folder = _folder(card.get("folder"))
            return Scope("move", MOVE_TOOL, folder=folder) if tool == MOVE_TOOL and folder else None
        scope = _app_scope(tool) if tool.startswith("mcp_") else None
        return scope if scope is not None and scope.app_id == str(card.get("app_id") or "") else None
    except Exception:
        logger.debug("A card's rule scope no longer checks out", exc_info=True)
        return None


def from_interrupt(interrupt: Any) -> Scope | None:
    """The scope a stored approval offers a rule for: one action only, its card's own scope."""

    items = interrupt if isinstance(interrupt, list) else [interrupt]
    if len(items) != 1 or not isinstance(items[0], Mapping):
        return None
    item = items[0]
    if item.get("person_only") or item.get("binding") or item.get("setup") or item.get("question_kind"):
        return None
    return from_card(item.get("always_allow"))


# ── The rules ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Rule:
    rule_id: str
    scope: Scope
    words: str
    enabled: bool
    created_at: float
    last_used_at: float | None
    uses: int


def _path() -> Path:
    from row_bot.data_paths import get_activity_db_path

    return get_activity_db_path(create_parent=False)


def _connect(*, timeout: float = 5) -> sqlite3.Connection:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=timeout)
    connection.row_factory = sqlite3.Row
    connection.execute(_SCHEMA)
    return connection


def _load(row: Mapping[str, Any]) -> Rule | None:
    """A stored rule, or None when its fields don't make one of the whitelisted kinds."""

    kind, tool = str(row["kind"] or ""), str(row["tool"] or "")
    folder, command, app_id = str(row["folder"] or ""), str(row["command"] or ""), str(row["app_id"] or "")
    if kind == "command":
        valid = tool in COMMAND_TOOLS and bool(folder) and not app_id and command_ok(command, folder)
    elif kind == "move":
        valid = tool == MOVE_TOOL and bool(folder) and not command and not app_id
    elif kind == "app":
        valid = tool.startswith("mcp_") and bool(app_id) and not folder and not command
    else:
        valid = False
    if not valid:
        return None
    scope = Scope(kind, tool, app_id=app_id, folder=folder, command=command,
                  app_name=str(row["app_name"] or ""), tool_title=str(row["tool_title"] or ""))
    return Rule(str(row["id"]), scope, scope.words(), bool(row["enabled"]), float(row["created_at"] or 0),
                float(row["last_used_at"]) if row["last_used_at"] is not None else None, int(row["uses"] or 0))


_warned = False


def match(scope: Scope | None) -> Rule | None:
    """The person's rule (switched on) that covers exactly this action, if any.

    Fails closed: an unreadable, locked or broken store matches nothing, so the action asks.
    """

    global _warned
    if scope is None:
        return None
    try:
        path = _path()
        if not path.is_file():
            return None
        with closing(sqlite3.connect(str(path), timeout=1)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT * FROM approval_rules WHERE enabled = 1 AND kind = ? AND tool = ?",
                                      (scope.kind, scope.tool)).fetchall()
        for row in rows:
            rule = _load(row)
            if rule is not None and rule.scope.key() == scope.key():
                return rule
        return None
    except Exception:
        if not _warned:
            _warned = True
            logger.warning("Your Always allow rules couldn't be read; every action asks as usual", exc_info=True)
        return None


def note_used(rule_id: str) -> None:
    """A rule just allowed an action: its count and last use (written by the log's writer, never waited on)."""

    from row_bot import activity_log

    activity_log.rule_used(rule_id)


def list_rules() -> list[Rule]:
    """Every rule, newest first. Raises when the store can't be read (the page says so)."""

    if not _path().is_file():
        return []
    with closing(_connect()) as connection:
        rows = connection.execute("SELECT * FROM approval_rules ORDER BY created_at DESC, id").fetchall()
    return [rule for rule in (_load(row) for row in rows) if rule is not None]


def get_rule(rule_id: str) -> Rule:
    for rule in list_rules():
        if rule.rule_id == rule_id:
            return rule
    raise RuleError("not_found")


def create(scope: Scope, *, created_from: str = "card") -> Rule:
    """Save a rule for *scope* (switched on). The same rule again switches the existing one back on."""

    with closing(_connect()) as connection, connection:
        rows = connection.execute("SELECT * FROM approval_rules").fetchall()
        for row in rows:
            rule = _load(row)
            if rule is not None and rule.scope.key() == scope.key():
                connection.execute("UPDATE approval_rules SET enabled = 1 WHERE id = ?", (rule.rule_id,))
                return Rule(rule.rule_id, rule.scope, rule.words, True, rule.created_at, rule.last_used_at, rule.uses)
        if len(rows) >= MAX_RULES:
            raise RuleError("rules_full")
        rule_id = secrets.token_hex(8)
        now = time.time()
        connection.execute(
            "INSERT INTO approval_rules (id, kind, tool, app_id, app_name, tool_title, folder, command, words, "
            "enabled, created_at, created_from) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (rule_id, scope.kind, scope.tool, scope.app_id, scope.app_name, scope.tool_title, scope.folder,
             scope.command, scope.words(), now, created_from if created_from in {"card", "settings"} else "card"),
        )
    return Rule(rule_id, scope, scope.words(), True, now, None, 0)


def narrowed(rule: Rule, command: str) -> Scope:
    """A command rule with its command text changed, checked as a new rule would be; refused otherwise."""

    if rule.scope.kind != "command":
        raise RuleError("rule_not_editable")
    text = str(command or "").strip()
    if not command_ok(text, rule.scope.folder):
        raise RuleError("rule_command_refused")
    return Scope("command", rule.scope.tool, folder=rule.scope.folder, command=text)


def update(rule_id: str, *, enabled: bool | None = None, command: str | None = None) -> Rule:
    """Switch a rule on or off, or change a command rule's exact command (checked again)."""

    rule = get_rule(rule_id)
    scope = narrowed(rule, command) if command is not None else rule.scope
    if command is not None and scope.key() != rule.scope.key() and any(
            other.scope.key() == scope.key() for other in list_rules() if other.rule_id != rule_id):
        raise RuleError("rule_exists")
    with closing(_connect()) as connection, connection:
        if command is not None:
            connection.execute("UPDATE approval_rules SET command = ?, words = ? WHERE id = ?",
                               (scope.command, scope.words(), rule_id))
        if enabled is not None:
            connection.execute("UPDATE approval_rules SET enabled = ? WHERE id = ?", (int(bool(enabled)), rule_id))
    return get_rule(rule_id)


def delete(rule_id: str) -> None:
    with closing(_connect()) as connection, connection:
        if not connection.execute("DELETE FROM approval_rules WHERE id = ?", (rule_id,)).rowcount:
            raise RuleError("not_found")


# ── Would this have asked? ───────────────────────────────────────────────────


def dry_run(scope: Scope, *, now: float | None = None, days: int = DRY_RUN_DAYS) -> dict[str, Any]:
    """What a rule for *scope* would have allowed in the last *days*: counts per outcome and a few examples.

    Read from the activity log only, matched exactly as ``match`` matches; never calls a model and never runs
    anything. Examples are newest first, with any the person said no to first.
    """

    from row_bot import activity_log

    counts = {outcome: 0 for outcome in DRY_RUN_OUTCOMES}
    note = scope.note()
    found: list[dict[str, Any]] = []
    for row in activity_log.rule_candidates(note["rule_kind"], DRY_RUN_OUTCOMES,
                                            since=float(now if now is not None else time.time()) - days * 86_400):
        if (str(row["scope_app"] or ""), str(row["scope_folder"] or ""), str(row["scope_command"] or "")) != (
                note["rule_app"], note["rule_folder"], note["rule_command"]):
            continue
        counts[str(row["outcome"])] += 1
        found.append(row)
    found.sort(key=lambda row: (row["outcome"] not in {"denied", "changed"}, -float(row["at"]), -int(row["id"])))
    return {"total": sum(counts.values()), "counts": counts, "examples": found[:DRY_RUN_EXAMPLES]}
