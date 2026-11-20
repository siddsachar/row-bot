"""Bounded tool results: per-result caps, a handle for the rest, and paging (WP D1).

A tool result too large for one turn is shortened once, when it is created. The model
sees its head and tail plus a handle, and the full text is kept in a per-conversation
file under the data folder. ``read_tool_output`` pages through that file, so nothing
becomes unreachable. The files are removed with their conversation (``thread_cleanup``).

Budgets are fixed, not scaled to the model's context window: a 1M-token model no longer
pulls 300K characters into one result, and a small local model's context is protected
by the agent's own trimming as before.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable, Iterator

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# About 25K tokens at the ~4 characters a token Row-Bot estimates with (Claude Code's default).
RESULT_CAP_CHARS = 100_000
# Command output (shell, Developer commands), as Claude Code's shell tool keeps it.
COMMAND_OUTPUT_CHARS = 30_000
# One page of a file, a diff or a fetched page.
PAGE_CHARS = 50_000
PAGE_LINES = 2_000
# One page of a saved output.
READ_CHARS = 40_000
READ_CHARS_MAX = 100_000

READER_TOOL_NAME = "read_tool_output"
# Saved outputs kept per conversation; the oldest go first (a monitor that runs all day stays bounded).
MAX_SAVED_PER_THREAD = 200
_FOLDER = "tool_outputs"
_HANDLE = re.compile(r"out_[0-9a-f]{16}")
_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9_.-]{1,96}")
# Results Row-Bot reads back from the conversation as JSON (agent orchestration); bounding child
# results belongs to the orchestrator, so these are never cut here.
_PARSED_RESULT_TOOLS = frozenset({"delegate_work", "agent_wait"})
# The conversations a tool result quotes (past-conversation search): kept on the stored result for
# Learning, which never keeps a chat's words that the person excluded from it. Never sent to the model.
QUOTED_CONVERSATIONS_KEY = "row_bot_quoted_conversations"
_quoted_conversations: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "row_bot_quoted_conversations", default=None)


# ── What a result quotes ────────────────────────────────────────────────────

def note_quoted_conversations(thread_ids: Iterable[str]) -> None:
    """Record the conversations this tool call's result quotes (a no-op outside a collecting call)."""

    found = _quoted_conversations.get()
    if found is not None:
        found.extend(str(thread) for thread in thread_ids if thread)


@contextlib.contextmanager
def collect_quoted_conversations() -> Iterator[list[str]]:
    """Collect what one tool call notes with :func:`note_quoted_conversations`, for its stored result."""

    found: list[str] = []
    token = _quoted_conversations.set(found)
    try:
        yield found
    finally:
        _quoted_conversations.reset(token)


# ── Where saved outputs live ────────────────────────────────────────────────

def outputs_root() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir() / _FOLDER


def thread_folder_name(thread_id: str) -> str:
    """A safe folder name for a conversation id (ids may hold ':' or other characters)."""
    clean = str(thread_id or "").strip() or "default"
    if _SAFE_SEGMENT.fullmatch(clean) and clean not in {".", ".."}:
        return clean
    return "t_" + hashlib.sha256(clean.encode("utf-8", "replace")).hexdigest()[:24]


def current_thread_id() -> str:
    """The conversation this tool call belongs to, from the running turn's context."""
    agent = sys.modules.get("row_bot.agent")
    if agent is not None:
        try:
            thread_id = agent.get_current_thread_id()
        except Exception:
            thread_id = ""
        if thread_id:
            return str(thread_id)
    developer_context = sys.modules.get("row_bot.developer.tool_context")
    if developer_context is not None:
        thread_id = developer_context._thread_id_var.get()  # noqa: SLF001
        if thread_id:
            return str(thread_id)
    try:
        from langchain_core.runnables.config import var_child_runnable_config

        config = var_child_runnable_config.get() or {}
        thread_id = (config.get("configurable") or {}).get("thread_id")
        if thread_id:
            return str(thread_id)
    except Exception:
        pass
    return ""


def _thread_folder(thread_id: str | None) -> Path:
    tid = current_thread_id() if thread_id is None else thread_id
    return outputs_root() / thread_folder_name(tid)


def spill(text: str, *, thread_id: str | None = None, source: str = "", external: bool = False) -> str | None:
    """Save *text* for this conversation and return its handle, or None if it can't be saved.

    The handle remembers where the text came from: *source* is the tool that produced it, and
    *external* marks a plugin, Custom Tool or MCP tool. Text that already carries an
    ``EXTERNAL_CONTENT`` fence (a child Agent's words) is remembered as fenced. Reading it back
    is fenced only when its source would have been (``saved_output_source``).
    """
    text = str(text)
    data = text.encode("utf-8", "replace")
    handle = "out_" + hashlib.sha256(data).hexdigest()[:16]
    try:
        folder = _thread_folder(thread_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{handle}.txt"
        if not path.exists():
            partial = folder / f"{handle}.{os.getpid()}.part"
            partial.write_bytes(data)
            os.replace(partial, path)
            _keep_newest(folder, keep=path)
        _remember_source(folder / f"{handle}.json", source=source, external=external,
                         fenced="<EXTERNAL_CONTENT" in text)
        return handle
    except OSError:
        logger.warning("Could not save a long tool result for paging", exc_info=True)
        return None


def _remember_source(path: Path, *, source: str, external: bool, fenced: bool) -> None:
    """Record the tools a saved text came from; the same text from two tools keeps both."""
    known = _read_source(path) or {}
    tools = sorted({*known.get("tools", []), *([source] if source else [])})
    record = {
        "tools": tools,
        "external": bool(known.get("external")) or external,
        "fenced": bool(known.get("fenced")) or fenced,
    }
    if record != known:
        partial = path.with_name(f"{path.stem}.{os.getpid()}.part")
        partial.write_text(json.dumps(record), encoding="utf-8")
        os.replace(partial, path)


def _read_source(path: Path) -> dict | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get("tools"), list):
        return None
    return record


def saved_output_source(handle: str) -> dict | None:
    """Where a saved output of this conversation came from: ``{"tools", "external", "fenced"}``.

    None when the handle is unknown or its record is missing; callers then treat the text as
    outside content.
    """
    clean = str(handle or "").strip()
    if not _HANDLE.fullmatch(clean):
        return None
    return _read_source(_thread_folder(None) / f"{clean}.json")


def is_external_tool(name: str) -> bool:
    """Whether *name* is an MCP, plugin or Custom Tool (third-party code; its results are fenced)."""
    name = str(name or "")
    if name.startswith("mcp_"):
        return True
    try:
        from row_bot.plugins import registry as plugin_registry

        names = set(plugin_registry.get_plugin_tool_names())
        names.update(
            str(record.get("runtime_name") or "")
            for record in plugin_registry.get_enabled_plugin_tool_records()
        )
    except Exception:
        return False
    return name in names


def _keep_newest(folder: Path, *, keep: Path) -> None:
    saved = sorted(folder.glob("out_*.txt"), key=lambda item: item.stat().st_mtime)
    for old in saved[: max(0, len(saved) - MAX_SAVED_PER_THREAD)]:
        if old != keep:
            old.unlink(missing_ok=True)
            old.with_suffix(".json").unlink(missing_ok=True)


def delete_thread_outputs(thread_id: str) -> int:
    """Remove a conversation's saved outputs; returns 1 when a folder was removed."""
    root = outputs_root()
    folder = root / thread_folder_name(thread_id)
    if not folder.exists():
        return 0
    from row_bot.thread_cleanup import resolve_managed_path

    shutil.rmtree(resolve_managed_path(root, folder))
    return 1


def copy_saved_outputs(source_thread_id: str, target_thread_id: str, handles: Iterable[str]) -> int:
    """Copy the named saved outputs of one conversation into another's folder (a fork's history names them).

    Handles are the same in both, so ``read_tool_output`` finds them in the copy; returns how many were copied.
    """
    source = _thread_folder(source_thread_id)
    target = _thread_folder(target_thread_id)
    copied = 0
    for handle in sorted(set(handles)):
        text = source / f"{handle}.txt"
        if not _HANDLE.fullmatch(handle) or not text.is_file():
            continue
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(text, target / text.name)
        record = source / f"{handle}.json"
        if record.is_file():
            shutil.copyfile(record, target / record.name)
        copied += 1
    return copied


def sweep_orphaned_outputs(thread_ids: Iterable[str], *, older_than: float | None = None) -> int:
    """Remove saved outputs of conversations that no longer exist.

    With *older_than* (a timestamp), a folder changed since then is kept: its conversation
    may be one that is still being created.
    """
    root = outputs_root()
    if not root.exists():
        return 0
    from row_bot.thread_cleanup import resolve_managed_path

    owners = {thread_folder_name(thread_id) for thread_id in thread_ids}
    removed = 0
    for child in list(root.iterdir()):
        if child.name in owners:
            continue
        try:
            if older_than is not None and child.stat().st_mtime > older_than:
                continue
            path = resolve_managed_path(root, child)
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed += 1
        except (OSError, ValueError):
            logger.warning("Could not remove orphaned tool output %s", child, exc_info=True)
    return removed


# ── Shortening ──────────────────────────────────────────────────────────────

def _head(text: str, size: int) -> str:
    head = text[:size]
    cut = head.rfind("\n")
    return head[: cut + 1] if cut >= size * 0.8 else head


def _tail(text: str, size: int) -> str:
    tail = text[-size:] if size else ""
    cut = tail.find("\n")
    return tail[cut + 1:] if 0 <= cut <= size * 0.2 else tail


def bounded(
    text: str,
    *,
    tool_name: str,
    max_chars: int,
    thread_id: str | None = None,
    external: bool = False,
) -> str:
    """Return *text* if it fits, else its head and tail with a handle to the full text.

    *tool_name* is the tool that produced the text: it names the output and decides whether
    reading the rest back is fenced. The notice comes first, so it survives any later
    shortening of the result's end.
    """
    text = str(text)
    if len(text) <= max_chars:
        return text
    handle = spill(text, thread_id=thread_id, source=tool_name, external=external)
    head = _head(text, max(1, max_chars * 2 // 3))
    tail = _tail(text, max(0, max_chars - len(head)))
    tail_start = len(text) - len(tail)
    label = tool_name or "Tool"
    if handle:
        notice = (
            f"[{label} output is {len(text):,} characters; showing the first {len(head):,} and the last "
            f"{len(tail):,}. The full output is saved as {handle}: read the rest with "
            f'{READER_TOOL_NAME}(handle="{handle}", offset={len(head)}).]'
        )
    else:
        notice = (
            f"[{label} output is {len(text):,} characters; showing the first {len(head):,} and the last "
            f"{len(tail):,}. The full output could not be saved.]"
        )
    omitted = f"[… {tail_start - len(head):,} characters omitted (offset {len(head):,} to {tail_start:,}) …]"
    return f"{notice}\n{head}\n{omitted}\n{tail}"


# The notice ``bounded`` puts first when it saved the full text.
_SAVED_NOTICE = re.compile(
    r"\[[^\n]{0,300} output is [\d,]+ characters; showing the first [\d,]+ and the last [\d,]+\. "
    r"The full output is saved as (out_[0-9a-f]{16}): [^\n]*\]\n"
)


def full_text(text: str, *, thread_id: str, max_chars: int) -> str:
    """The whole of a result ``bounded`` shortened, read back from its conversation's saved output.

    *text* itself when it wasn't shortened, its saved output is gone, or the whole is longer than
    *max_chars*. For a reader outside the model (an app's view of its own result), not the model.
    """
    match = _SAVED_NOTICE.match(str(text))
    if match is None:
        return text
    path = _thread_folder(thread_id) / f"{match.group(1)}.txt"
    try:
        if path.stat().st_size > max_chars * 4:
            return text
        whole = path.read_bytes().decode("utf-8", "replace")
    except OSError:
        return text
    return whole if len(whole) <= max_chars else text


def cap_tool_message(message: Any, *, thread_id: str | None = None, source: str = "") -> Any:
    """Bound a freshly created ToolMessage's text to ``RESULT_CAP_CHARS``.

    *source* is the tool that really ran (a ``tool_invoke`` call names its target); it
    defaults to the message's name. A chart's figure or a plugin's image first moves out of
    the text to the client (``rendered_results``, WP D2), so the model reads a summary. Pages
    of the reader itself are left as they are. Images and other non-text blocks are kept.
    """
    from row_bot.rendered_results import move_rendered_payload

    name = str(getattr(message, "name", "") or "")
    source = source or name
    message = move_rendered_payload(message, source=source, thread_id=thread_id)
    content = getattr(message, "content", None)
    if READER_TOOL_NAME in {name, source} or {name, source} & _PARSED_RESULT_TOOLS:
        return message

    def cap(text: str) -> str:
        if len(text) <= RESULT_CAP_CHARS:
            return text
        return bounded(text, tool_name=source, max_chars=RESULT_CAP_CHARS, thread_id=thread_id,
                       external=is_external_tool(source))

    if isinstance(content, str):
        capped = cap(content)
        message = message if capped is content else message.model_copy(update={"content": capped})
    elif isinstance(content, list):
        changed = False
        blocks: list[Any] = []
        for block in content:
            if isinstance(block, str):
                new = cap(block)
            elif isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                text = cap(block["text"])
                new = block if text is block["text"] else {**block, "text": text}
            else:
                new = block
            changed = changed or new is not block
            blocks.append(new)
        message = message.model_copy(update={"content": blocks}) if changed else message
    if getattr(message, "status", "success") != "error" and _is_mcp_error_result(source, message.content):
        # The provider gets the error flag (Claude's is_error), and an error is not a result the
        # finalization fallback answers from.
        message = message.model_copy(update={"status": "error"})
    return message


_APPROVAL_LINE = re.compile(r"\AApproval: [^\n]*\n?")


def _is_mcp_error_result(source: str, content: Any) -> bool:
    """Whether a result is an MCP tool's error: its server marked it ``isError``, which leads the
    text with ``ERROR_PREFIX``. Only a name this MCP runtime issued counts, never a look-alike."""
    if not source.startswith("mcp_"):
        return False
    runtime = sys.modules.get("row_bot.mcp_client.runtime")
    if runtime is None:
        return False  # Nothing has run an MCP tool in this process.
    if isinstance(content, list):
        content = next((block.get("text") for block in content
                        if isinstance(block, dict) and block.get("type") == "text"), "")
    from row_bot.mcp_client.results import ERROR_PREFIX

    # The approval line Row-Bot adds is not the app's.
    if not _APPROVAL_LINE.sub("", str(content or ""), count=1).startswith(ERROR_PREFIX):
        return False
    try:
        return bool(runtime.servers_for_tool(source))
    except Exception:
        return False


# ── Paging ──────────────────────────────────────────────────────────────────

def page_lines(
    text: str,
    *,
    offset: int = 1,
    limit: int = PAGE_LINES,
    max_chars: int = PAGE_CHARS,
    thread_id: str | None = None,
    tool_name: str = "",
) -> tuple[str, str]:
    """One page of *text* by lines: (page, footer). The footer is empty when the page is the whole text.

    *offset* is the 1-based first line. A page stops at *limit* lines or *max_chars*
    characters, at a line boundary. A single line longer than a page is cut, and the
    whole text is saved under a handle so the rest of that line stays reachable.
    """
    lines = text.splitlines(keepends=True)
    total = len(lines)
    start = max(1, int(offset or 1))
    limit = max(1, int(limit or PAGE_LINES))
    if total == 0:
        return "", ""
    if start > total:
        return "", f"[Line {start:,} is past the end: the text has {total:,} lines.]"
    parts: list[str] = []
    size = 0
    long_line = 0
    index = start - 1
    while index < total and len(parts) < limit:
        line = lines[index]
        if len(line) > max_chars and not parts:
            parts.append(line[:max_chars] + "\n")
            long_line = index + 1
            index += 1
            break
        if size + len(line) > max_chars:
            break
        parts.append(line)
        size += len(line)
        index += 1
    end = index
    page = "".join(parts)
    if start == 1 and end >= total and not long_line:
        return page, ""
    notes = [f"Lines {start:,}–{end:,} of {total:,}."]
    if end < total:
        notes.append(f"Continue with offset={end + 1}.")
    else:
        notes.append("End of text.")
    if long_line:
        handle = spill(text, thread_id=thread_id, source=tool_name)
        line_start = sum(len(item) for item in lines[: long_line - 1])
        if handle:
            notes.append(
                f"Line {long_line:,} is {len(lines[long_line - 1]):,} characters and is cut here; read the rest "
                f'with {READER_TOOL_NAME}(handle="{handle}", offset={line_start + max_chars}).'
            )
        else:
            notes.append(f"Line {long_line:,} is {len(lines[long_line - 1]):,} characters and is cut here.")
    return page, "[" + " ".join(notes) + "]"


def page_chars(text: str, *, offset: int = 0, limit: int = READ_CHARS) -> tuple[str, int, int]:
    """A character window of *text*: (window, start, end). Ends at a line break when one is near."""
    total = len(text)
    start = min(max(0, int(offset or 0)), total)
    end = min(total, start + max(1, int(limit or READ_CHARS)))
    if end < total:
        cut = text.rfind("\n", start, end)
        if cut >= start + (end - start) * 0.9:
            end = cut + 1
    return text[start:end], start, end


def read_tool_output(handle: str, offset: int = 0, limit: int = READ_CHARS) -> str:
    """Read part of a saved tool output of this conversation."""
    clean = str(handle or "").strip()
    if not _HANDLE.fullmatch(clean):
        return f"Unknown handle {clean!r}: use the out_… handle named in a shortened tool result."
    path = _thread_folder(None) / f"{clean}.txt"
    try:
        text = path.read_bytes().decode("utf-8", "replace")
    except OSError:
        return f"No saved output {clean} in this conversation; it may have been removed. Run the tool again."
    limit = min(max(1_000, int(limit or READ_CHARS)), READ_CHARS_MAX)
    window, start, end = page_chars(text, offset=offset, limit=limit)
    total = len(text)
    if start >= total:
        return f"[{clean}: offset {start:,} is at or past the end ({total:,} characters).]"
    more = f"Continue with offset={end}." if end < total else "End of output."
    return f"[{clean}: characters {start:,}–{end:,} of {total:,}. {more}]\n{window}"


class _ReadToolOutputInput(BaseModel):
    handle: str = Field(description="The out_… handle.")
    offset: int = Field(default=0, description="Character offset.")
    limit: int = Field(default=READ_CHARS, description=f"At most {READ_CHARS_MAX}.")


def build_reader_tool() -> StructuredTool:
    """The tool that pages through saved outputs; bound with every Agent tool set."""
    return StructuredTool.from_function(
        func=read_tool_output,
        name=READER_TOOL_NAME,
        description="Read more of a shortened tool result, by the handle and offset it names.",
        args_schema=_ReadToolOutputInput,
    )


# ── Concise structured results ──────────────────────────────────────────────

def drop_empty(value: Any) -> Any:
    """*value* with empty fields (None, '', [], {}) left out of every mapping, at any depth.

    ``False`` and ``0`` are kept: they carry meaning (``"ok": false``).
    """
    if isinstance(value, dict):
        cleaned = {key: drop_empty(item) for key, item in value.items()}
        return {key: item for key, item in cleaned.items() if item not in (None, "", [], {})}
    if isinstance(value, (list, tuple)):
        return [drop_empty(item) for item in value]
    return value


def compact_json(value: Any, *, sort_keys: bool = False, drop_empty_fields: bool = True) -> str:
    """Compact JSON for the model: no indentation, and (by default) empty fields dropped."""
    return json.dumps(
        drop_empty(value) if drop_empty_fields else value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=sort_keys,
        default=str,
    )
