"""Developer Studio native tools."""

from __future__ import annotations

import hashlib
import logging
import pathlib
import subprocess
from typing import Literal

from langchain_core.tools import StructuredTool
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from row_bot.developer import change_ledger
from row_bot.developer import edits as developer_edits
from row_bot.developer.git import (
    branch_changes,
    commit_changes,
    create_branch,
    fast_forward_merge,
    get_git_status,
    pull_request_text,
    switch_branch,
)
from row_bot.developer.github import create_pull_request, push_current_branch, record_pull_request_result
from row_bot.developer.sandbox import ApprovalDecision, decide_action
from row_bot.developer.review import get_file_diff, get_file_diff_stats, list_changed_files
from row_bot.developer.runtime import detect_project_commands, run_workspace_command, run_workspace_shell_command
from row_bot.developer.sandbox_runtime import (
    _client_file_patch,
    apply_patch_in_docker_sandbox,
    carry_over_pending_change,
    get_pending_change,
    list_pending_changes,
    mark_pending_change_imported,
    restore_shadow_files,
    set_pending_change_aside,
    write_file_in_docker_sandbox,
)
from row_bot.developer.storage import get_workspace
from row_bot.developer.tool_context import get_thread_id, get_workspace_id, infer_workspace_id_from_thread
from row_bot.developer.todos import list_todos, replace_todos_from_labels, set_todo_status
from row_bot.tool_outputs import (
    COMMAND_OUTPUT_CHARS,
    PAGE_CHARS,
    PAGE_LINES,
    bounded,
    compact_json,
    page_lines,
)
from row_bot.tools import registry
from row_bot.tools.base import BaseTool

logger = logging.getLogger(__name__)


def _active_workspace(*, write: bool = False):
    workspace_id = get_workspace_id()
    if not workspace_id:
        workspace_id = infer_workspace_id_from_thread(get_thread_id())
    if not workspace_id:
        raise ValueError("No code folder is open in this conversation. Open or create a code folder for it first.")
    workspace = get_workspace(workspace_id)
    if workspace is None:
        raise ValueError("The active Developer workspace could not be found.")
    from row_bot.application.conversation_writer import require_execution_writer
    require_execution_writer(workspace_id, write=write)
    root = pathlib.Path(workspace.path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Developer workspace folder does not exist: {root}")
    return workspace, root


def _inside(root: pathlib.Path, rel_path: str) -> pathlib.Path:
    clean = (rel_path or ".").strip().replace("\\", "/")
    if clean in {"", "."}:
        return root
    if clean.startswith("/") or clean.startswith("../") or "/../" in clean:
        raise ValueError(f"Path escapes workspace: {rel_path}")
    target = (root / clean).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace: {rel_path}") from exc
    return target


def _workspace_rel(root: pathlib.Path, path: pathlib.Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def _workspace_info() -> str:
    workspace, _root = _active_workspace()
    from row_bot.developer.agent_context import build_developer_agent_context

    return build_developer_agent_context(workspace.id, get_thread_id())


def _active_approval_mode() -> str:
    thread_id = get_thread_id()
    if thread_id:
        try:
            from row_bot.threads import _get_thread_approval_mode

            return _get_thread_approval_mode(thread_id)
        except Exception:
            pass
    try:
        from row_bot.agent import get_approval_mode

        return get_approval_mode()
    except Exception:
        from row_bot.approval_policy import DEFAULT_APPROVAL_MODE

        return DEFAULT_APPROVAL_MODE


# Developer actions that reach other people or can't be taken back, for the Auto reviewer.
_DEVELOPER_RISKS = {
    "developer_push_current_branch": "pushes commits to a shared remote other people can see",
    "developer_create_pull_request": "opens a pull request other people can see",
    "developer_revert_change_set": "restores files, undoing earlier changes",
    "developer_import_sandbox_changes": "applies sandbox changes to the real code folder",
    "developer_prepare_sandbox": "turns the sandbox network on while it downloads and installs packages from the "
                                 "internet into the sandbox",
}


def _ask(payload: dict) -> object:
    """The approval a Developer action needs, by the conversation's approval level.

    The person under Ask; the reviewer first under Auto (the person when it asks); the level
    itself under Hands-off. Returns what ``interrupt()`` would: True when approved.
    """
    from row_bot.tools.approval_gate import request_approval

    tool = str(payload.get("tool") or "")
    risks = [f"a Developer action in the conversation's code folder: {payload.get('label') or tool}"]
    if tool in _DEVELOPER_RISKS:
        risks.append(_DEVELOPER_RISKS[tool])
    return request_approval(
        payload,
        approval_mode=_active_approval_mode(),
        risks=risks,
        # This module's own name, so a test can stand in for the person.
        approval_callback=lambda request: interrupt(request),
    )


class _ListFilesInput(BaseModel):
    path: str = Field(default=".", description="Workspace-relative folder to list.")
    limit: int = Field(default=80, description="Maximum entries to return.")


def _list_files(path: str = ".", limit: int = 80) -> str:
    _workspace, root = _active_workspace()
    folder = _inside(root, path)
    if not folder.exists():
        return f"Path not found: {path}"
    if folder.is_file():
        return f"{_workspace_rel(root, folder)} ({folder.stat().st_size} bytes)"
    skip = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build"}
    rows: list[str] = []
    for entry in sorted(folder.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        if entry.name in skip:
            continue
        suffix = "/" if entry.is_dir() else ""
        rows.append(f"- {_workspace_rel(root, entry)}{suffix}")
        if len(rows) >= max(1, min(limit, 300)):
            break
    return "\n".join(rows) if rows else "No files found."


class _ReadFileInput(BaseModel):
    path: str = Field(description="Workspace-relative text file path.")
    offset: int = Field(default=1, description="First line to read (1-based).")
    limit: int = Field(default=PAGE_LINES, description="Most lines to read.")


def _read_file(path: str, offset: int = 1, limit: int = PAGE_LINES) -> str:
    _workspace, root = _active_workspace()
    target = _inside(root, path)
    if not target.is_file():
        return f"File not found: {path}"
    data = target.read_bytes()
    text = data.decode("utf-8", errors="replace")
    revision = hashlib.sha256(data).hexdigest()
    # The SHA256 is of the whole file, on every page: developer_write_file checks it.
    page, footer = page_lines(
        text, offset=offset, limit=limit, max_chars=PAGE_CHARS, tool_name="developer_read_file"
    )
    return f"SHA256: {revision}\n" + page + (f"\n{footer}" if footer else "")


class _SearchInput(BaseModel):
    query: str = Field(description="Text or regex to search for inside the active workspace.")
    glob: str = Field(default="", description="Optional ripgrep glob such as '*.py'.")
    limit: int = Field(default=80, description="Maximum matching lines to return.")


def _search(query: str, glob: str = "", limit: int = 80) -> str:
    _workspace, root = _active_workspace()
    if not str(query or "").strip():
        return "Enter a non-empty search query."
    # A minified line would otherwise fill the result; show a preview of it instead.
    args = ["rg", "--line-number", "--no-heading", "--color=never", "--hidden",
            f"--max-columns={_SEARCH_LINE_CHARS}", "--max-columns-preview"]
    for ignored in [".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", "dist", "build"]:
        args.extend(["--glob", f"!{ignored}/**"])
    if glob:
        args.extend(["--glob", glob])
    args.append(query)
    try:
        proc = subprocess.run(args, cwd=str(root), capture_output=True, text=True, timeout=30)
        output = proc.stdout.strip()
    except (FileNotFoundError, PermissionError):
        matches: list[str] = []
        lowered = query.lower()
        for file in root.rglob("*"):
            if not file.is_file():
                continue
            rel = _workspace_rel(root, file)
            if any(part in {".git", "node_modules", ".venv", "venv", "__pycache__"} for part in file.parts):
                continue
            try:
                for idx, line in enumerate(file.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if lowered in line.lower():
                        matches.append(f"{rel}:{idx}:{_search_preview(line)}")
                        if len(matches) >= limit:
                            break
            except Exception:
                continue
            if len(matches) >= limit:
                break
        output = "\n".join(matches)
    if not output:
        return "No matches found."
    rows = output.splitlines()[: max(1, min(limit, 300))]
    return "\n".join(rows)


_SEARCH_LINE_CHARS = 300


def _search_preview(line: str) -> str:
    if len(line) <= _SEARCH_LINE_CHARS:
        return line
    return line[:_SEARCH_LINE_CHARS] + f" [... {len(line) - _SEARCH_LINE_CHARS} more characters]"


def _git_status() -> str:
    workspace, _root = _active_workspace()
    status = get_git_status(workspace.path)
    return compact_json(status.__dict__)


class _DiffInput(BaseModel):
    path: str = Field(default="", description="Workspace-relative file path. Leave empty for the changed files with line counts.")
    offset: int = Field(default=1, description="First line of the file's diff to show (1-based).")
    limit: int = Field(default=PAGE_LINES, description="Most diff lines to show.")


_DIFF_SUMMARY_FILES = 300
_DIFF_READ_LIMIT = 20_000_000  # Whole diffs are paged here; this only guards memory.


def _diff(path: str = "", offset: int = 1, limit: int = PAGE_LINES) -> str:
    workspace, _root = _active_workspace()
    if path:
        diff = get_file_diff(workspace.path, path, limit=_DIFF_READ_LIMIT)
        if not diff:
            return "No textual diff available."
        added, removed = get_file_diff_stats(workspace.path, path)
        page, footer = page_lines(
            diff, offset=offset, limit=limit, max_chars=PAGE_CHARS, tool_name="developer_get_diff"
        )
        return f"{path}: +{added} -{removed}\n{page}" + (f"\n{footer}" if footer else "")
    changed = list_changed_files(workspace.path)
    if not changed:
        return "No changed files detected."
    rows = []
    for item in changed[:_DIFF_SUMMARY_FILES]:
        added, removed = get_file_diff_stats(workspace.path, item.path)
        rows.append(f"- {item.status} {item.path} (+{added} -{removed})")
    if len(changed) > _DIFF_SUMMARY_FILES:
        rows.append(f"... and {len(changed) - _DIFF_SUMMARY_FILES} more changed files")
    rows.append("Pass a path to see that file's diff.")
    return "\n".join(rows)


class _TodoInput(BaseModel):
    labels: list[str] = Field(default_factory=list, description="Todo labels to replace the current plan with.")
    todo_id: str = Field(default="", description="Existing todo id to update.")
    status: Literal["", "pending", "in_progress", "completed", "blocked"] = Field(
        default="", description="New status for todo_id."
    )


def _update_todos(labels: list[str] | None = None, todo_id: str = "", status: str = "") -> str:
    thread_id = get_thread_id()
    if not thread_id:
        raise ValueError("No active Developer thread.")
    if labels:
        todos = replace_todos_from_labels(thread_id, labels)
    elif todo_id and status:
        todos = set_todo_status(thread_id, todo_id, status)  # type: ignore[arg-type]
    else:
        todos = list_todos(thread_id)
    return compact_json([todo.__dict__ for todo in todos])


class _RunDetectedInput(BaseModel):
    command: str = Field(description="Detected command label or exact detected command to run.")


def _run_detected(command: str) -> str:
    workspace, _root = _active_workspace(write=True)
    thread_id = get_thread_id()
    specs = detect_project_commands(workspace.path)
    chosen = next((spec for spec in specs if spec.label == command or spec.command == command), None)
    if chosen is None:
        return "Command is not in the detected Developer command list. Use the Inspector to add/approve custom commands later."
    def run(confirmed: bool):
        return run_workspace_command(
            workspace.path,
            chosen.command,
            _active_approval_mode(),
            workspace_id=workspace.id,
            thread_id=thread_id,
            detected_test=True,
            confirmed=confirmed,
        )

    result = run(False)
    if result.decision and result.decision.requires_approval:
        approval = _ask({
            "tool": "developer_run_detected_test",
            "label": "Run project command",
            "description": f"Run in {workspace.name}: {chosen.command}",
            "args": {"workspace": workspace.name, "command": chosen.command},
        })
        if not approval:
            return "Command cancelled by user."
        result = run(True)
    return _command_text(result, tool_name="developer_run_detected_test")


def _command_text(result, *, tool_name: str) -> str:
    """A command result as plain text: stdout and stderr verbatim, empty fields left out."""
    lines = [f"$ {result.command}"]
    status = "not run" if result.returncode is None else f"exit code {result.returncode}"
    details = [status]
    if result.cwd:
        details.append(f"cwd {result.cwd}")
    if result.execution_mode and result.execution_mode != "local":
        details.append(f"{result.execution_mode}" + (f" ({result.sandbox_backend})" if result.sandbox_backend else ""))
    if result.process_id:
        details.append(f"process {result.process_id}")
    if result.code:
        details.append(f"code {result.code}")
    lines.append(" · ".join(details))
    decision = result.decision
    if decision is not None and (result.returncode is None or decision.decision != "allow"):
        lines.append(f"policy: {decision.decision}" + (f" — {decision.reason}" if decision.reason else ""))
    if result.changed_files:
        lines.append("changed files: " + ", ".join(result.changed_files))
    if result.sandbox_pending_change_id:
        lines.append(f"sandbox pending change: {result.sandbox_pending_change_id}")
    if result.execution_mode == "docker" and result.returncode not in (None, 0):
        from row_bot.developer.sandbox_prepare import UNPREPARED_LINE, missing_tools

        # A test run that failed for want of its tools (F18): the chat offers Prepare sandbox.
        why = missing_tools(f"{result.stdout}\n{result.stderr}")
        if why:
            lines.append(f"{UNPREPARED_LINE}{why}. Offer to prepare it: developer_prepare_sandbox installs "
                         "the project's test dependencies with the sandbox network on for the install only, "
                         "then off again (it asks the person first).")
    output = []
    if result.stdout:
        output.append("stdout:\n" + result.stdout.rstrip("\n"))
    if result.stderr:
        output.append("stderr:\n" + result.stderr.rstrip("\n"))
    body = bounded("\n".join(output), tool_name=tool_name, max_chars=COMMAND_OUTPUT_CHARS)
    return "\n".join(lines) + (f"\n{body}" if body else "")


class _RunCommandInput(BaseModel):
    command: str = Field(description="Shell command to run in the active Developer workspace.")
    timeout: int = Field(default=120, description="Maximum seconds to allow the command to run.")
    approval_reason: str = Field(
        default="",
        description="One short user-facing sentence explaining why this command is needed if approval is required.",
    )


def _run_command(command: str, timeout: int = 120, approval_reason: str = "") -> str:
    workspace, _root = _active_workspace(write=True)
    thread_id = get_thread_id()
    result = run_workspace_shell_command(
        workspace.path,
        command,
        _active_approval_mode(),
        workspace_id=workspace.id,
        thread_id=thread_id,
        timeout=max(5, min(int(timeout or 120), 600)),
        confirmed=False,
    )
    if result.decision and result.decision.requires_approval:
        approval = _ask({
            "tool": "developer_run_command",
            "label": "Run Developer shell command",
            "description": f"Run in {workspace.name}: {command}",
            "approval_reason": approval_reason,
            "args": {"workspace": workspace.name, "command": command},
        })
        if not approval:
            return "Command cancelled by user."
        result = run_workspace_shell_command(
            workspace.path,
            command,
            _active_approval_mode(),
            workspace_id=workspace.id,
            thread_id=thread_id,
            timeout=max(5, min(int(timeout or 120), 600)),
            confirmed=True,
        )
    return _command_text(result, tool_name="developer_run_command")


def _prepare_sandbox() -> str:
    """Prepare the Docker sandbox for tests (F18): asks, then installs with the network on for that only."""
    from row_bot.developer import sandbox_prepare

    workspace, _root = _active_workspace(write=True)
    if workspace.execution_mode != "docker":
        return ("This code folder runs commands on this computer, not in the Docker sandbox, so there is no "
                "sandbox to prepare. To install test tools here, use developer_run_command (it asks first).")
    running = sandbox_prepare.active_for(workspace.id)
    if running is not None:
        sandbox_prepare.wait(running, 120)
        return sandbox_prepare.summary(running)
    plan = sandbox_prepare.plan_for(workspace.path)
    if plan is None:
        return ("Row-Bot found nothing to install: the code folder has no pyproject.toml, requirements*.txt "
                "or package.json. Ask the person what the tests need.")
    decision = decide_action(_active_approval_mode(), "run_install")
    if decision.decision == "block":
        return ("Approvals are set to Block in this conversation, so Row-Bot can't prepare the sandbox. Tell the "
                "person; they can change Approvals, or use Prepare sandbox in the code folder's Sandbox settings.")
    installs = "; ".join(plan.commands)
    approval = _ask({
        "tool": "developer_prepare_sandbox",
        "label": "Prepare sandbox",
        "question": "Prepare the sandbox for tests?",
        "description": (f"Turns the sandbox network on for the install only, installs {plan.summary} (from "
                        f"{', '.join(plan.sources)}) in {workspace.name}'s sandbox, then turns the network off "
                        "again."),
        "args": {"workspace": workspace.name, "installs": installs},
    })
    if not approval:
        return "Prepare sandbox cancelled by the person: nothing was installed and the sandbox network stayed off."
    try:
        job = sandbox_prepare.start(workspace, get_thread_id(), by="agent")
    except sandbox_prepare.PrepareError as exc:
        return f"Prepare sandbox couldn't start: {exc}"
    sandbox_prepare.wait(job, 120)
    return sandbox_prepare.summary(job)


class _GitBranchInput(BaseModel):
    branch_name: str = Field(description="Git branch name.")


def _create_branch(branch_name: str) -> str:
    workspace, _root = _active_workspace(write=True)
    decision = decide_action(_active_approval_mode(), "git_branch")
    if not decision.allowed:
        approval = _ask({
            "tool": "developer_create_branch",
            "label": "Create Developer branch",
            "description": f"Create branch {branch_name} in {workspace.name}",
            "args": {"workspace": workspace.name, "branch_name": branch_name},
        }) if decision.requires_approval else False
        if not approval:
            return decision.reason
    status = create_branch(workspace.path, branch_name)
    return compact_json(status.__dict__)


def _switch_branch(branch_name: str) -> str:
    workspace, _root = _active_workspace(write=True)
    decision = decide_action(_active_approval_mode(), "git_branch")
    if not decision.allowed:
        approval = _ask({
            "tool": "developer_switch_branch",
            "label": "Switch Developer branch",
            "description": f"Switch to branch {branch_name} in {workspace.name}",
            "args": {"workspace": workspace.name, "branch_name": branch_name},
        }) if decision.requires_approval else False
        if not approval:
            return decision.reason
    status = switch_branch(workspace.path, branch_name)
    return compact_json(status.__dict__)


class _GitCommitInput(BaseModel):
    message: str = Field(description="Commit message.")
    paths: list[str] = Field(default_factory=list, description="Workspace-relative paths to commit. Leave empty to commit all staged/unstaged changes.")


def _commit_changes(message: str, paths: list[str] | None = None) -> str:
    workspace, _root = _active_workspace(write=True)
    decision = decide_action(_active_approval_mode(), "git_commit")
    if not decision.allowed:
        approval = _ask({
            "tool": "developer_commit_changes",
            "label": "Create Developer commit",
            "description": f"Commit changes in {workspace.name}: {message}",
            "args": {"workspace": workspace.name, "message": message, "paths": paths or []},
        }) if decision.requires_approval else False
        if not approval:
            return decision.reason
        decision = ApprovalDecision("allow", "User explicitly approved this commit.")
    status = commit_changes(workspace.path, message, paths or None)
    return compact_json({"decision": decision.__dict__, "status": status.__dict__})


def _push_current_branch() -> str:
    workspace, _root = _active_workspace(write=True)
    result = push_current_branch(workspace.path, _active_approval_mode(), confirmed=False)
    if result.decision and result.decision.requires_approval:
        approval = _ask({
            "tool": "developer_push_current_branch",
            "label": "Push Developer branch",
            "description": f"Push the current branch from {workspace.name} to origin",
            "args": {"workspace": workspace.name},
        })
        if not approval:
            return "Push cancelled by user."
        result = push_current_branch(workspace.path, _active_approval_mode(), confirmed=True)
    return compact_json(result.__dict__)


class _PullRequestInput(BaseModel):
    title: str = Field(default="", description="Pull request title. Leave empty to write it from the branch's commits.")
    body: str = Field(default="", description="Pull request body. Leave empty to list the branch's commits and files.")
    draft: bool = Field(default=True, description="Open as a draft. Keep the default unless the person asked for a ready PR.")


def _create_pull_request(title: str = "", body: str = "", draft: bool = True) -> str:
    """Open a pull request for the pushed current branch with the GitHub CLI on this computer (F20)."""
    workspace, _root = _active_workspace(write=True)
    if not title.strip():
        status = get_git_status(workspace.path)
        suggested = pull_request_text(status.branch or "", branch_changes(workspace.path))
        if suggested is None:
            return "The current branch adds no commits to its base branch, so there is nothing to open a pull request for."
        title = suggested["title"]
        body = body.strip() or suggested["body"]
    title, body = title.strip()[:200], body.strip()[:8000]
    branch = get_git_status(workspace.path).branch or ""
    result = create_pull_request(workspace.path, _active_approval_mode(), title=title, body=body, draft=draft)
    if result.decision and result.decision.requires_approval:
        approval = _ask({
            "tool": "developer_create_pull_request",
            "label": "Open pull request",
            "description": f"Open a {'draft ' if draft else ''}pull request from {workspace.name}: {title}",
            "args": {"workspace": workspace.name, "title": title, "draft": draft},
        })
        if not approval:
            return "Pull request cancelled by user."
        result = create_pull_request(
            workspace.path, _active_approval_mode(), title=title, body=body, draft=draft, confirmed=True
        )
    # The Git tab shows the branch's pull request instead of an empty form (B336).
    try:
        known = record_pull_request_result(workspace.id, workspace.path, branch, result, title=title, draft=draft)
    except OSError:
        logger.warning("The opened pull request was not remembered", exc_info=True)
        known = None
    if known and not result.ok:
        return compact_json({"ok": True, "url": known["url"], "already_open": True,
                             "message": f"This branch already has pull request #{known['number']}."})
    if result.ran and not result.ok and "push" in result.stderr.lower():
        return "GitHub needs the branch first: push it with developer_push_current_branch, then open the pull request."
    return compact_json({
        "ok": result.ok,
        "url": result.url,
        "draft": draft,
        "title": title,
        "error": "" if result.ok else (result.stderr.strip()[-2000:] or "The pull request was not created."),
    })


class _GitFastForwardInput(BaseModel):
    branch_name: str = Field(description="Branch name to fast-forward merge into the current branch.")


def _fast_forward_merge(branch_name: str) -> str:
    workspace, _root = _active_workspace(write=True)
    decision = decide_action(_active_approval_mode(), "git_branch")
    if not decision.allowed:
        approval = _ask({
            "tool": "developer_fast_forward_merge",
            "label": "Fast-forward merge",
            "description": f"Fast-forward merge {branch_name} into the current branch in {workspace.name}",
            "args": {"workspace": workspace.name, "branch_name": branch_name},
        }) if decision.requires_approval else False
        if not approval:
            return decision.reason
    status = fast_forward_merge(workspace.path, branch_name)
    return compact_json(status.__dict__)


class _ImportSandboxInput(BaseModel):
    pending_change_id: str = Field(
        default="",
        description="Any pending sandbox change id from this conversation, or empty. Every pending change is imported "
        "together, oldest first, under one approval.",
    )
    summary: str = Field(default="", description="Short summary for the imported sandbox patch.")


def _import_sandbox_changes(pending_change_id: str = "", summary: str = "") -> str:
    workspace, root = _active_workspace(write=True)
    thread_id = get_thread_id()
    pending = get_pending_change(pending_change_id) if pending_change_id else None
    if pending_change_id and (pending is None or pending.workspace_id != workspace.id):
        raise ValueError(f"Sandbox pending change not found: {pending_change_id}")
    if pending is not None and pending.imported:
        return f"Sandbox change {pending_change_id} was already imported."
    if pending is not None and pending.set_aside:
        return (f"Sandbox change {pending_change_id} was set aside, not imported: its files changed in the code "
                "folder after the sandbox made it. Make the change again.")
    # Each sandbox change builds on the ones before it, so they go in oldest first (B303), and one approval
    # covers all of this conversation's pending changes rather than a card each (F21).
    batch = sorted(list_pending_changes(workspace_id=workspace.id, thread_id=pending.thread_id if pending else thread_id),
                   key=lambda change: change.created_at)
    if not batch:
        return "There are no sandbox changes waiting to import."
    files = list(dict.fromkeys(path for change in batch for path in change.files))
    decision = decide_action(_active_approval_mode(), "edit")
    if decision.decision == "block":
        return decision.reason
    confirmed = False
    if decision.requires_approval:
        sets = f" ({len(batch)} sandbox changes)" if len(batch) > 1 else ""
        approval = _ask({
            "tool": "developer_import_sandbox_changes",
            "label": "Apply these changes",
            "description": summary or f"Apply {len(files)} changed file(s) from Docker Sandbox to {workspace.name}{sets}",
            "args": {"workspace": workspace.name, "files": files, "changes": len(batch)},
        })
        if not approval:
            return "Sandbox import cancelled by user."
        confirmed = True
    lines: list[str] = []
    set_aside: list = []
    # Files of a change that was set aside: a later change to them built on it, so it stays out too.
    held: set[str] = set()

    def apply(change, patch: str):
        return developer_edits.apply_patch_to_workspace(
            workspace_id=workspace.id,
            thread_id=thread_id,
            patch=patch,
            approval_mode=_active_approval_mode(),
            summary=(summary if change is batch[-1] else "") or f"Import sandbox changes from: {change.command[:80]}",
            confirmed=confirmed,
        )

    def carry_over(change):
        """The code folder changed since the sandbox made this change (B334): merge it into the files as they are."""
        merged = carry_over_pending_change(change, root)
        if merged is None:
            return None, None, "set_aside"
        current = {path: _host_text(root, path) for path in merged}
        patch = "".join(_client_file_patch(path, current[path], merged[path])
                        for path in change.files if current[path] != merged[path])
        if not patch:
            return None, None, "already"
        try:
            change_set, decision = apply(change, patch)
        except ValueError:
            return None, None, "set_aside"
        return change_set, decision, "carried"

    for change in batch:
        change_set, decision, outcome = None, None, "set_aside"
        if held.isdisjoint(change.files):
            try:
                change_set, decision = apply(change, change.patch)
                outcome = "imported"
            except ValueError:
                change_set, decision, outcome = carry_over(change)
        if outcome == "already":
            mark_pending_change_imported(change.id)
            lines.append(f"Sandbox change {change.id} was already in the code folder.")
            continue
        if outcome != "set_aside":
            if change_set is None:
                return "\n".join([*lines, decision.reason])
            mark_pending_change_imported(change.id)
            how = " (carried over onto the code folder's newer copy of its files)" if outcome == "carried" else ""
            lines.append(f"Imported sandbox change {change.id} as change set {change_set.id}{how}.\n"
                         + "\n".join(f"- {item.action} {item.path}" for item in change_set.files))
            continue
        # Never left waiting once it can't go in: set aside, and said so (B334).
        set_pending_change_aside(change.id, "code_folder_changed")
        held.update(change.files)
        set_aside.append(change)
    if set_aside:
        files = list(dict.fromkeys(path for change in set_aside for path in change.files))
        try:
            restore_shadow_files(workspace, files)
            restored = " The sandbox's copies of those files now match the code folder again."
        except (OSError, ValueError):
            logger.warning("Sandbox copies were not restored after a set-aside import", exc_info=True)
            restored = ""
        names = ", ".join(change.id for change in set_aside)
        lines.append(
            f"Set aside, not imported: sandbox change {names} ({', '.join(files)}). Those files changed in the code "
            f"folder after the sandbox made the change, in the same lines, so it couldn't be carried over.{restored} "
            "Make the change again, or tell the person it wasn't made.")
    return "\n".join(lines)


def _host_text(root: pathlib.Path, path: str) -> str | None:
    target = _inside(root, path)
    return target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None


class _PatchInput(BaseModel):
    patch: str = Field(description="Unified diff patch to apply inside the active workspace.")
    summary: str = Field(default="", description="Short summary of the intended change.")


def _preview_patch(patch: str, summary: str = "") -> str:
    workspace, _root = _active_workspace()
    return developer_edits.preview_patch(workspace.id, patch)


def _apply_patch(patch: str, summary: str = "") -> str:
    workspace, _root = _active_workspace(write=True)
    thread_id = get_thread_id()
    if workspace.execution_mode == "docker":
        decision = developer_edits.ordinary_edit_decision(_active_approval_mode())
        if decision.decision == "block":
            return decision.reason
        if decision.requires_approval:
            approval = _ask({
                "tool": "developer_apply_patch",
                "label": "Apply Developer patch in Docker Sandbox",
                "description": summary or "Apply a patch inside the Docker Sandbox shadow workspace",
                "args": {"workspace": workspace.name, "summary": summary},
            })
            if not approval:
                return "Sandbox patch application cancelled by user."
        outcome = apply_patch_in_docker_sandbox(
            workspace,
            patch,
            thread_id=thread_id,
            summary=summary,
        )
        if outcome.returncode != 0:
            return outcome.stderr or "Docker Sandbox patch failed."
        if not outcome.pending_change_id:
            return "Patch applied in Docker Sandbox, but it produced no file changes."
        return (
            f"Applied patch in Docker Sandbox as pending change {outcome.pending_change_id}.\n"
            "The real workspace was not changed. Import this sandbox change to apply it to the repo.\n"
            + "\n".join(f"- {path}" for path in outcome.changed_files)
        )
    change_set, decision = developer_edits.apply_patch_to_workspace(
        workspace_id=workspace.id,
        thread_id=thread_id,
        patch=patch,
        approval_mode=_active_approval_mode(),
        summary=summary,
        confirmed=False,
    )
    if decision.requires_approval:
        approval = _ask({
            "tool": "developer_apply_patch",
            "label": "Apply Developer patch",
            "description": summary or "Apply a patch inside the active Developer workspace",
            "args": {"workspace": workspace.name, "summary": summary},
        })
        if not approval:
            return "Patch application cancelled by user."
        change_set, decision = developer_edits.apply_patch_to_workspace(
            workspace_id=workspace.id,
            thread_id=thread_id,
            patch=patch,
            approval_mode=_active_approval_mode(),
            summary=summary,
            confirmed=True,
        )
    if change_set is None:
        return decision.reason
    return (
        f"Applied patch as change set {change_set.id}.\n"
        + "\n".join(f"- {item.action} {item.path}" for item in change_set.files)
    )


class _WriteFileInput(BaseModel):
    path: str = Field(description="Workspace-relative file path to create or replace.")
    content: str = Field(description="Complete UTF-8 text content for the file.")
    summary: str = Field(default="", description="Short summary of the intended file change.")
    expected_sha256: str = Field(default="", description="SHA256 from developer_read_file when replacing an existing file. Leave empty only for a new file.")


class _ImportMediaInput(BaseModel):
    media_ref: str = Field(description="Opaque generated-media reference owned by this conversation.")
    path: str = Field(description="New workspace-relative .png, .jpg, or .mp4 file path in an existing folder. Never overwrite a file.")


def _import_media(media_ref: str, path: str) -> str:
    workspace, _root = _active_workspace(write=True)
    if workspace.execution_mode == "docker":
        return "Import into the real code folder is unavailable while Docker Sandbox mode is active."
    change_set, decision = developer_edits.import_conversation_media(
        workspace_id=workspace.id, thread_id=get_thread_id(), media_ref=media_ref,
        path=path, approval_mode=_active_approval_mode(),
    )
    if decision.requires_approval:
        approval = _ask({
            "tool": "developer_import_media",
            "label": "Import generated media",
            "description": f"Copy one conversation output to {path} in {workspace.name}",
            "args": {"workspace": workspace.name, "media_ref": media_ref, "path": path},
        })
        if not approval:
            return "Media import cancelled by user."
        change_set, decision = developer_edits.import_conversation_media(
            workspace_id=workspace.id, thread_id=get_thread_id(), media_ref=media_ref,
            path=path, approval_mode=_active_approval_mode(), confirmed=True,
        )
    if change_set is None:
        return decision.reason if decision.decision == "block" or decision.requires_approval else "Media already exists at that path."
    return f"Imported {path} as change set {change_set.id}."


def _write_file(path: str, content: str, summary: str = "", expected_sha256: str = "") -> str:
    workspace, _root = _active_workspace(write=True)
    thread_id = get_thread_id()
    if workspace.execution_mode == "docker":
        decision = developer_edits.ordinary_edit_decision(_active_approval_mode())
        if decision.decision == "block":
            return decision.reason
        if decision.requires_approval:
            approval = _ask({
                "tool": "developer_write_file",
                "label": "Write Developer file in Docker Sandbox",
                "description": summary or f"Write {path} inside the Docker Sandbox shadow workspace",
                "args": {"workspace": workspace.name, "path": path, "summary": summary},
            })
            if not approval:
                return "Sandbox file write cancelled by user."
        outcome = write_file_in_docker_sandbox(
            workspace,
            path,
            content,
            thread_id=thread_id,
        )
        if outcome.returncode != 0:
            return outcome.stderr or "Docker Sandbox file write failed."
        if not outcome.pending_change_id:
            return f"Wrote {path} in Docker Sandbox, but it produced no file changes."
        return (
            f"Wrote {path} in Docker Sandbox as pending change {outcome.pending_change_id}.\n"
            "The real workspace was not changed. Import this sandbox change to apply it to the repo."
        )
    change_set, decision = developer_edits.write_file_to_workspace(
        workspace_id=workspace.id,
        thread_id=thread_id,
        path=path,
        content=content,
        approval_mode=_active_approval_mode(),
        summary=summary,
        confirmed=False,
        expected_sha256=expected_sha256,
    )
    if decision.requires_approval:
        approval = _ask({
            "tool": "developer_write_file",
            "label": "Write Developer file",
            "description": summary or f"Write {path} inside the active Developer workspace",
            "args": {"workspace": workspace.name, "path": path, "summary": summary},
        })
        if not approval:
            return "File write cancelled by user."
        change_set, decision = developer_edits.write_file_to_workspace(
            workspace_id=workspace.id,
            thread_id=thread_id,
            path=path,
            content=content,
            approval_mode=_active_approval_mode(),
            summary=summary,
            confirmed=True,
            expected_sha256=expected_sha256,
        )
    if change_set is None:
        return decision.reason
    return f"Wrote {path} as change set {change_set.id}."


class _RevertInput(BaseModel):
    change_set_id: str = Field(description="Agent change set id to revert.")


def _revert_change_set(change_set_id: str) -> str:
    workspace, _root = _active_workspace(write=True)
    saved, _ = change_ledger.read_change_set(change_set_id)
    if change_ledger.requires_guarded_undo(saved):
        from row_bot import agent, conversation_resources
        from row_bot.developer import client_undo
        from row_bot.runtime import admissions, executions
        thread_id = get_thread_id()
        context = conversation_resources.current_execution_context()
        captured = context.resolve("workspace") if context else None
        def validate():
            current = conversation_resources.current_execution_context()
            execution = executions.current_execution()
            if (not current or current != context or current.conversation_id != thread_id
                    or not captured or captured.resource_id != workspace.id or current.resolve("workspace") != captured
                    or get_thread_id() != thread_id or get_workspace_id() != workspace.id
                    or execution is not None and execution.cancel_scope.is_cancelled()):
                raise ValueError("resource_binding_revoked")
        review, command_id = client_undo.prepare_retained_undo(workspace.id, thread_id, change_set_id, validate=validate)
        if review.policy_decision == "block":
            return "Undo is blocked by the current workspace policy."
        client_undo.reserve_retained_undo_review(review, command_id, validate=validate)
        if review.approval_required:
            approved = _ask({"tool": "developer_revert_change_set", "label": "Undo imported workspace changes",
                "description": "Restore the exact retained original files. Created directories will remain.",
                "args": {"change_set_id": change_set_id, "files": list(review.files),
                    "directories_retained": list(review.directories_retained), "action_digest": review.action_digest}})
            if not approved:
                admissions.reject_command("developer-undo:" + thread_id, command_id, "action_denied")
                return "Workspace Undo cancelled by user."
        result = client_undo.execute_retained_undo(review, command_id, confirmed=True, validate=validate,
            borrowed_run_id=agent.get_active_runtime_context().get("agent_run_id") or None)
        if result.status != "undone":
            return f"Workspace Undo {result.status}: {result.code}. Retry this same change set to recover its original operation."
        return f"Reverted {len(result.files_restored)} file(s) from change set {change_set_id}. Created directories were retained."
    return developer_edits.revert_change_set(workspace.id, change_set_id)


def _list_agent_changes() -> str:
    workspace, _root = _active_workspace()
    rows = change_ledger.list_change_sets(workspace_id=workspace.id, thread_id=get_thread_id())
    if not rows:
        return "No agent-owned change sets recorded for this Developer thread."
    return compact_json([
        {
            "id": item.id,
            "summary": item.summary,
            "files": [file.__dict__ for file in item.files],
            "created_at": item.created_at,
        }
        for item in rows[:20]
    ])


def _changes_workspace(func):
    """After a tool that can change the folder, its Inspector snapshot is read again (B302)."""
    from functools import wraps

    @wraps(func)
    def changed(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        finally:
            workspace_id = get_workspace_id() or infer_workspace_id_from_thread(get_thread_id())
            if workspace_id:
                from row_bot.developer.inspector_snapshot import invalidate_workspace_snapshots

                invalidate_workspace_snapshots(workspace_id)

    return changed


class DeveloperTool(BaseTool):
    @property
    def name(self) -> str:
        return "developer"

    @property
    def display_name(self) -> str:
        return "Developer"

    @property
    def description(self) -> str:
        return "Developer Studio workspace tools for reading, searching, editing, testing, and reviewing code."

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def untrusted_tool_names(self) -> set[str]:
        # Repository files and the output of commands run in the code folder.
        return {
            "developer_read_file", "developer_search", "developer_get_diff",
            "developer_run_detected_test", "developer_run_command",
        }

    def execute(self, query: str) -> str:
        return _workspace_info()

    def as_langchain_tools(self) -> list:
        changes = _changes_workspace
        return [
            StructuredTool.from_function(func=_workspace_info, name="developer_workspace_info", description="Return the active Developer workspace, path, branch, dirty state, remote, and approval mode."),
            StructuredTool.from_function(func=_list_files, name="developer_list_files", description="List files under the active Developer workspace. Paths must be workspace-relative.", args_schema=_ListFilesInput),
            StructuredTool.from_function(func=_read_file, name="developer_read_file", description="Read a workspace-relative text file from the active Developer workspace, a page of lines at a time; the result starts with the file's SHA256.", args_schema=_ReadFileInput),
            StructuredTool.from_function(func=_search, name="developer_search", description="Search text in the active Developer workspace using a safe workspace-scoped search.", args_schema=_SearchInput),
            StructuredTool.from_function(func=_git_status, name="developer_git_status", description="Return structured Git state for the active Developer workspace."),
            StructuredTool.from_function(func=changes(_create_branch), name="developer_create_branch", description="Create a Git branch in the active Developer workspace using the thread approval mode.", args_schema=_GitBranchInput),
            StructuredTool.from_function(func=changes(_switch_branch), name="developer_switch_branch", description="Switch Git branches in the active Developer workspace using the thread approval mode.", args_schema=_GitBranchInput),
            StructuredTool.from_function(func=changes(_commit_changes), name="developer_commit_changes", description="Create a Git commit in the active Developer workspace using the thread approval mode.", args_schema=_GitCommitInput),
            StructuredTool.from_function(func=changes(_push_current_branch), name="developer_push_current_branch", description="Push the current branch to origin using the thread approval mode."),
            StructuredTool.from_function(func=changes(_create_pull_request), name="developer_create_pull_request", description="Open a GitHub pull request for the current branch once it is pushed. Runs the GitHub CLI on this computer, never in the sandbox, and asks first by the thread approval mode. Draft by default; leave title and body empty to describe the branch's own commits and files.", args_schema=_PullRequestInput),
            StructuredTool.from_function(func=changes(_fast_forward_merge), name="developer_fast_forward_merge", description="Fast-forward merge another branch into the current branch using the thread approval mode.", args_schema=_GitFastForwardInput),
            StructuredTool.from_function(func=_diff, name="developer_get_diff", description="List changed files with added/removed line counts, or show one file's diff in pages.", args_schema=_DiffInput),
            StructuredTool.from_function(func=_update_todos, name="developer_update_todos", description="Create or update the visible Developer todo plan for this code thread. Keep it current: mark an item in_progress when you start it and completed when it is done, including work a helper agent finished, before you end your turn.", args_schema=_TodoInput),
            StructuredTool.from_function(func=changes(_run_detected), name="developer_run_detected_test", description="Run a command from the detected Developer test/lint/typecheck command list.", args_schema=_RunDetectedInput),
            StructuredTool.from_function(func=changes(_run_command), name="developer_run_command", description="Run a shell command in the active Developer workspace after policy checks and record file side effects.", args_schema=_RunCommandInput),
            StructuredTool.from_function(func=_prepare_sandbox, name="developer_prepare_sandbox", description="Prepare the Docker sandbox for tests when a test run failed because pytest or the project's packages are missing: installs the test dependencies from pyproject.toml, requirements*.txt or package.json with the sandbox network on for the install only, then off again. Asks the person first."),
            StructuredTool.from_function(func=changes(_import_sandbox_changes), name="developer_import_sandbox_changes", description="Import this conversation's pending Docker Sandbox changes into the real workspace, all together and oldest first, after one approval. Call it once when the work is ready, not once per change.", args_schema=_ImportSandboxInput),
            StructuredTool.from_function(func=_preview_patch, name="developer_preview_patch", description="Validate and preview a unified diff patch without writing files.", args_schema=_PatchInput),
            StructuredTool.from_function(func=changes(_apply_patch), name="developer_apply_patch", description="Apply a validated unified diff patch inside the active Developer workspace and record an agent-owned change set.", args_schema=_PatchInput),
            StructuredTool.from_function(func=changes(_write_file), name="developer_write_file", description="Create or replace a workspace-relative text file and record an agent-owned change set.", args_schema=_WriteFileInput),
            StructuredTool.from_function(func=changes(_import_media), name="developer_import_media", description="Copy one generated image or video from this conversation into a new code-folder file through Developer's writer lease and change ledger. Requires the exact media reference and an unused path.", args_schema=_ImportMediaInput),
            StructuredTool.from_function(func=_list_agent_changes, name="developer_list_agent_changes", description="List agent-owned change sets recorded for this Developer thread."),
            StructuredTool.from_function(func=changes(_revert_change_set), name="developer_revert_agent_changes", description="Revert an agent-owned change set if files have not drifted.", args_schema=_RevertInput),
        ]


registry.register(DeveloperTool())
