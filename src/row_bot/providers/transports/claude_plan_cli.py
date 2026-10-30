"""Chat model for Claude (your Claude plan): one run of the person's own Claude Code per model call.

Each call starts the installed ``claude`` program, unmodified, in print mode with stream-json in and out:

    claude -p --input-format stream-json --output-format stream-json --verbose --include-partial-messages
      --model <m> --tools "" --max-turns 1 --permission-mode dontAsk --no-session-persistence
      --setting-sources "" --strict-mcp-config --mcp-config <file> --system-prompt-file <file>
      --disable-slash-commands [--effort <level>]

Claude Code signs in by itself, with the person's plan; Row-Bot never sees that sign-in and sets no key,
token, header or endpoint. The conversation goes in as stream-json frames: every earlier user turn with
``shouldQuery: false`` (Claude Code acknowledges each with a zero-turn result), assistant turns as they
were, and the last user turn asks the model. Text and thinking stream back as they arrive; the turn's
tool calls, usage and stop reason are read from the stream, and then the process ends.

Claude Code's own tools are off. Row-Bot's tools are listed to the model through a small MCP server whose
calls do nothing (``claude_plan_tools.py``), and ``dontAsk`` refuses them anyway: the tool calls go back
to Row-Bot's agent loop, which runs them through its approval gate like any provider's. Stop ends the
whole process tree.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import uuid
from collections import deque
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel, generate_from_stream
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable

from row_bot.cancellation import current_cancellation_scope

logger = logging.getLogger(__name__)

TOOL_SERVER = "rowbot"
TOOL_PREFIX = f"mcp__{TOOL_SERVER}__"
# Tool names reach the API as mcp__rowbot__<name>, at most 64 characters.
_WIRE_NAME_LIMIT = 50
_WIRE_NAME = re.compile(r"[A-Za-z0-9_-]+")
EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
# Claude Code's system prompt is replaced, never left to its default (a coding agent's).
_DEFAULT_SYSTEM = "You are a helpful assistant."
# How long Claude Code may stay silent: per acknowledged history frame, and during the reply.
_ACK_SECONDS = 60.0
_REPLY_SECONDS = 600.0
# After a finished reply, how long Claude Code gets to write its result before it is stopped.
_RESULT_GRACE_SECONDS = 2.0
_INITIALIZE_SECONDS = 30.0


# ── Tools ────────────────────────────────────────────────────────────────────


def wire_tool_name(name: str) -> str:
    """The MCP tool name the model sees for a Row-Bot tool: itself when it fits, else a stable short form."""
    value = str(name or "")
    if _WIRE_NAME.fullmatch(value) and len(value) <= _WIRE_NAME_LIMIT:
        return value
    clean = re.sub(r"[^A-Za-z0-9_-]", "_", value)
    return f"{clean[:41]}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:8]}"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]

    @property
    def wire(self) -> str:
        return wire_tool_name(self.name)

    def manifest(self) -> dict[str, Any]:
        return {"name": self.wire, "description": self.description, "inputSchema": self.schema}


def _input_schema(value: Any) -> dict[str, Any]:
    schema = dict(value) if isinstance(value, dict) else {}
    # A tool's input is one object; the API takes no top-level union.
    for key in ("oneOf", "anyOf", "allOf"):
        schema.pop(key, None)
    schema["type"] = "object"
    schema.setdefault("properties", {})
    return schema


def tool_spec(tool: Any) -> ToolSpec:
    """A bound tool (a LangChain tool, a function or a tool dict) as name, description and input schema."""
    if isinstance(tool, ToolSpec):
        return tool
    if isinstance(tool, dict) and "input_schema" in tool:
        payload = {"name": tool.get("name"), "description": tool.get("description"),
                   "parameters": tool.get("input_schema")}
    else:
        from langchain_core.utils.function_calling import convert_to_openai_tool

        payload = dict(convert_to_openai_tool(tool))
        if isinstance(payload.get("function"), dict):
            payload = dict(payload["function"])
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("A tool for Claude has no name.")
    return ToolSpec(name, str(payload.get("description") or ""), _input_schema(payload.get("parameters")))


# ── The conversation as stream-json frames ───────────────────────────────────


def _image_block(item: dict[str, Any]) -> dict[str, Any] | None:
    source = item.get("source")
    if isinstance(source, dict) and source.get("type") in {"base64", "url"}:
        return {"type": "image", "source": dict(source)}
    url = item.get("image_url")
    url = url.get("url") if isinstance(url, dict) else url
    if not isinstance(url, str) or not url:
        url = item.get("url") if isinstance(item.get("url"), str) else ""
    if url.startswith("data:") and ";base64," in url:
        header, data = url.split(";base64,", 1)
        return {"type": "image", "source": {"type": "base64", "media_type": header[5:] or "image/png", "data": data}}
    if url.startswith(("https://", "http://")):
        return {"type": "image", "source": {"type": "url", "url": url}}
    if item.get("type") == "image" and isinstance(item.get("base64"), str):
        return {"type": "image", "source": {"type": "base64", "media_type": str(item.get("mime_type") or "image/png"),
                                            "data": item["base64"]}}
    return None


def _content_blocks(content: Any, *, images: bool = True) -> list[dict[str, Any]]:
    """Text and image blocks only; thinking and tool blocks are never replayed (their signatures are
    bound to a request prefix Claude Code builds)."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    blocks: list[dict[str, Any]] = []
    for item in content if isinstance(content, list) else [content]:
        if isinstance(item, str):
            if item:
                blocks.append({"type": "text", "text": item})
            continue
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        if kind == "text" and isinstance(item.get("text"), str) and item["text"]:
            blocks.append({"type": "text", "text": item["text"]})
        elif images and kind in {"image", "image_url", "input_image"}:
            image = _image_block(item)
            if image:
                blocks.append(image)
    return blocks


def _tool_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {"input": value}
        return parsed if isinstance(parsed, dict) else {"input": parsed}
    return {}


def _assistant_blocks(message: AIMessage) -> list[dict[str, Any]]:
    blocks = _content_blocks(message.content, images=False)
    for call in getattr(message, "tool_calls", None) or []:
        name = str(call.get("name") or "").strip()
        if name:
            blocks.append({"type": "tool_use", "id": str(call.get("id") or f"toolu_{uuid.uuid4().hex[:24]}"),
                           "name": TOOL_PREFIX + wire_tool_name(name), "input": _tool_arguments(call.get("args"))})
    return blocks


def _tool_result(message: ToolMessage) -> dict[str, Any]:
    content = message.content
    result: dict[str, Any] = {"type": "tool_result", "tool_use_id": str(message.tool_call_id)}
    if isinstance(content, str):
        result["content"] = content
    else:
        result["content"] = _content_blocks(content) or ""
    if getattr(message, "status", "") == "error":
        result["is_error"] = True
    return result


def conversation_frames(messages: Sequence[BaseMessage]) -> tuple[str, list[dict[str, Any]]]:
    """The system prompt and the stream-json frames for ``messages``; the last frame asks the model."""
    system: list[str] = []
    turns: list[tuple[str, list[dict[str, Any]]]] = []
    for message in messages:
        if isinstance(message, SystemMessage):
            text = "".join(block["text"] for block in _content_blocks(message.content, images=False))
            if text.strip():
                system.append(text)
            continue
        if isinstance(message, AIMessage):
            role, blocks = "assistant", _assistant_blocks(message)
        elif isinstance(message, ToolMessage):
            role, blocks = "user", [_tool_result(message)]
        else:
            role, blocks = "user", _content_blocks(message.content)
        if not blocks:
            continue
        if turns and turns[-1][0] == role:
            turns[-1][1].extend(blocks)
        else:
            turns.append((role, blocks))
    if not turns or turns[-1][0] != "user":
        raise ValueError("A Claude plan call needs the conversation to end with a message to Claude.")
    frames: list[dict[str, Any]] = []
    for index, (role, blocks) in enumerate(turns):
        frame: dict[str, Any] = {"type": role, "message": {"role": role, "content": blocks}}
        if role == "user" and index < len(turns) - 1:
            frame["shouldQuery"] = False
        frames.append(frame)
    return "\n\n".join(system) or _DEFAULT_SYSTEM, frames


# ── Claude Code's command line ───────────────────────────────────────────────


def claude_code_argv(program: str, *, model: str, system_file: Path, mcp_file: Path,
                     effort: str | None = None) -> list[str]:
    argv = [program, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--include-partial-messages", "--model", model, "--tools", "", "--max-turns", "1",
            "--permission-mode", "dontAsk", "--no-session-persistence", "--setting-sources", "",
            "--strict-mcp-config", "--mcp-config", str(mcp_file), "--system-prompt-file", str(system_file),
            "--disable-slash-commands"]
    if effort:
        argv += ["--effort", effort]
    return argv


def _tool_server(tools_file: Path) -> dict[str, Any]:
    """The MCP server entry: this Python, isolated, running the stub with the tools file."""
    stub = Path(__file__).with_name("claude_plan_tools.py")
    return {"type": "stdio", "command": sys.executable, "args": ["-I", "-S", "-B", str(stub), str(tools_file)]}


def _write(path: Path, text: str) -> Path:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


@contextmanager
def _call_folder() -> Iterator[Path]:
    """A folder of this profile's own for one call's prompt and tool files, removed afterwards."""
    from row_bot.data_paths import get_row_bot_data_dir

    root = get_row_bot_data_dir() / "runtime" / "claude-code-calls"
    root.mkdir(parents=True, exist_ok=True)
    folder = root / uuid.uuid4().hex
    folder.mkdir(mode=0o700)
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# ── Running Claude Code ──────────────────────────────────────────────────────


class ClaudeCodeRun:
    """One Claude Code process: frames in, events out, and its whole tree stopped at the end or on Stop."""

    def __init__(self, argv: list[str], *, env: dict[str, str], cwd: Path) -> None:
        options: dict[str, Any] = {}
        if os.name == "nt":
            options["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        else:
            options["start_new_session"] = True
        self.argv = argv
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, cwd=str(cwd), env=env, shell=False, **options)
        self._events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=40)
        self._stop_lock = threading.Lock()
        self._stopped = False
        threading.Thread(target=self._read_stdout, name="claude-code-stdout", daemon=True).start()
        threading.Thread(target=self._read_stderr, name="claude-code-stderr", daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        try:
            for raw in self.process.stdout:
                try:
                    event = json.loads(raw.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if isinstance(event, dict):
                    self._events.put(event)
        except (OSError, ValueError):
            pass
        finally:
            self._events.put(None)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        try:
            for raw in self.process.stderr:
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    self._stderr.append(line)
        except (OSError, ValueError):
            pass

    def send(self, frame: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        try:
            self.process.stdin.write((json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8"))
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise ClaudeCodeEnded(self) from exc

    def close_input(self) -> None:
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
        except OSError:
            pass

    def next_event(self, timeout: float) -> dict[str, Any] | None:
        """The next event; None once Claude Code's output ended. Raises after ``timeout`` of silence."""
        try:
            return self._events.get(timeout=timeout)
        except queue.Empty:
            raise ClaudeCodeSilent() from None

    def stderr_tail(self) -> str:
        return " ".join(list(self._stderr)[-3:])[:300]

    def stop(self) -> None:
        """End the process and everything it started (its tool server included).

        Stop (another thread) and the end of the call may both stop the run; whichever comes second waits
        until the whole tree has ended.
        """
        with self._stop_lock:
            if not self._stopped:
                self._stopped = True
                self._stop()

    def _stop(self) -> None:
        self.close_input()
        if self.process.poll() is None:
            try:
                import psutil

                from row_bot.process_cancellation import kill_process_tree

                kill_process_tree(psutil.Process(self.process.pid))
            except Exception:
                logger.debug("Claude Code's process tree was already gone", exc_info=True)
                try:
                    self.process.kill()
                except OSError:
                    pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("Claude Code did not end after it was stopped")


class ClaudeCodeEnded(RuntimeError):
    def __init__(self, run: ClaudeCodeRun) -> None:
        tail = run.stderr_tail()
        super().__init__("Claude Code ended before it finished the reply." + (f" It said: {tail}" if tail else ""))


class ClaudeCodeSilent(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Claude Code stopped answering, so Row-Bot ended it. Try again.")


# ── Reading the reply ────────────────────────────────────────────────────────


_PLAN_LIMIT = re.compile(r"session limit|weekly limit|usage limit|limit reached|resets?\b|out of (extra )?usage",
                         re.IGNORECASE)
# "… · resets 3pm (Europe/London)": when the plan's limit lifts, in Claude Code's own words.
_RESETS = re.compile(r"\bresets?\s+(?:at\s+)?([^\u00b7|]{1,60}?)\s*(?:[\u00b7|]|$)", re.IGNORECASE)


def _short(text: Any) -> str:
    return " ".join(str(text or "").split())[:240]


def claude_code_error(code: str, text: str) -> Exception:
    """An error Claude Code reported (``assistant.error`` or a failed result), in plain words."""
    from row_bot.providers.claude_subscription import (
        CLAUDE_PLAN_SETTINGS,
        CLAUDE_SUBSCRIPTION_PROVIDER_ID,
        SIGNED_OUT_MESSAGE,
        ClaudePlanUnavailable,
    )

    detail = _short(text)
    lowered = detail.lower()
    if code == "authentication_failed" or "not logged in" in lowered or "please run /login" in lowered:
        from row_bot.providers.runtime import ProviderSignInRequired

        return ProviderSignInRequired(
            CLAUDE_SUBSCRIPTION_PROVIDER_ID, "Claude",
            f"{SIGNED_OUT_MESSAGE} Sign in in {CLAUDE_PLAN_SETTINGS}.")
    if code == "rate_limit" or "rate limit" in lowered:
        if _PLAN_LIMIT.search(detail):
            until = _RESETS.search(detail)
            return ClaudePlanUnavailable(
                f"Your Claude plan's limit is reached{' until ' + until.group(1) if until else ''}. Pick another "
                "model for now.", state="plan_limit")
        return ClaudePlanUnavailable("Claude is limiting requests right now. Wait a moment, then try again.",
                                     state="rate_limited")
    if code == "billing_error":
        return ClaudePlanUnavailable(
            f"Your Claude plan can't use this model ({detail or 'it needs another kind of billing'}). Choose "
            "another Claude model.", state="billing")
    if "does not support this model" in lowered or "update claude code" in lowered:
        return ClaudePlanUnavailable(
            f"This Claude model needs a newer Claude Code. Update Claude Code in {CLAUDE_PLAN_SETTINGS}, then "
            "try again.", state="too_old")
    if code in {"overloaded", "server_error"}:
        return ClaudePlanUnavailable(
            f"Anthropic's service is busy or having trouble ({detail or code}). Try again in a moment.",
            state="unavailable")
    return ClaudePlanUnavailable(f"Claude Code couldn't finish the reply: {detail or code or 'no reason given'}.")


def _message_text(message: Any) -> str:
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    return " ".join(str(block.get("text") or "") for block in content or []
                    if isinstance(block, dict) and block.get("type") == "text")


@dataclass
class ClaudeTurnReader:
    """Claude Code's stream-json events for one model turn, as chat chunks.

    ``names`` maps the MCP names the model called back to Row-Bot's tool names.
    """

    names: dict[str, str] = field(default_factory=dict)
    model: str = ""
    stop_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    finished: bool = False
    _started: bool = False
    _tools: dict[int, dict[str, str]] = field(default_factory=dict)

    def tool_name(self, name: str) -> str:
        bare = name[len(TOOL_PREFIX):] if name.startswith(TOOL_PREFIX) else name
        return self.names.get(bare, bare)

    def feed(self, event: dict[str, Any]) -> Iterator[ChatGenerationChunk]:
        kind = event.get("type")
        if kind == "stream_event" and not event.get("parent_tool_use_id"):
            yield from self._stream_event(event.get("event") if isinstance(event.get("event"), dict) else {})
        elif kind == "assistant":
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            code = event.get("error") or message.get("error")
            if code:
                raise claude_code_error(str(code), _message_text(message))
        elif kind == "result":
            if event.get("is_error") and not self.finished:
                raise claude_code_error(str(event.get("subtype") or ""),
                                        str(event.get("result") or "") or " ".join(map(str, event.get("errors") or [])))
            self.finished = True
            if not self.stop_reason and isinstance(event.get("stop_reason"), str):
                self.stop_reason = event["stop_reason"]
        elif kind == "system" and event.get("subtype") == "init":
            for server in event.get("mcp_servers") or []:
                if isinstance(server, dict) and server.get("name") == TOOL_SERVER and server.get("status") == "failed":
                    from row_bot.providers.claude_subscription import ClaudePlanUnavailable

                    raise ClaudePlanUnavailable("Claude Code couldn't load Row-Bot's tools for this reply. Try again.")

    def _stream_event(self, event: dict[str, Any]) -> Iterator[ChatGenerationChunk]:
        kind = event.get("type")
        if kind == "message_start":
            if self._started:
                # Claude Code began another model turn: this call's turn is over.
                self.finished = True
                return
            self._started = True
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            self.model = str(message.get("model") or self.model)
            self._add_usage(message.get("usage"))
        elif kind == "content_block_start":
            index = int(event.get("index") or 0)
            block = event.get("content_block") if isinstance(event.get("content_block"), dict) else {}
            if block.get("type") == "tool_use":
                self._tools[index] = {"id": str(block.get("id") or f"toolu_{uuid.uuid4().hex[:24]}"),
                                      "name": self.tool_name(str(block.get("name") or ""))}
                yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[{
                    "name": self._tools[index]["name"], "args": "", "id": self._tools[index]["id"], "index": index}]))
            elif block.get("type") == "text" and block.get("text"):
                yield ChatGenerationChunk(message=AIMessageChunk(content=str(block["text"])))
        elif kind == "content_block_delta":
            index = int(event.get("index") or 0)
            delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                yield ChatGenerationChunk(message=AIMessageChunk(content=str(delta["text"])))
            elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                yield ChatGenerationChunk(message=AIMessageChunk(
                    content="", additional_kwargs={"reasoning_content": str(delta["thinking"])}))
            elif delta.get("type") == "input_json_delta" and index in self._tools:
                yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[{
                    "name": None, "args": str(delta.get("partial_json") or ""), "id": None, "index": index}]))
        elif kind == "message_delta":
            delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            if delta.get("stop_reason"):
                self.stop_reason = str(delta["stop_reason"])
            self._add_usage(event.get("usage"), replace=True)
        elif kind == "message_stop":
            self.finished = True

    def _add_usage(self, usage: Any, *, replace: bool = False) -> None:
        if not isinstance(usage, dict):
            return
        for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and value >= 0 and (replace or key not in self.usage):
                self.usage[key] = value

    def last_chunk(self) -> ChatGenerationChunk:
        """The closing chunk: why the reply ended, which model wrote it and what it used."""
        cache_read = self.usage.get("cache_read_input_tokens", 0)
        cache_write = self.usage.get("cache_creation_input_tokens", 0)
        input_tokens = self.usage.get("input_tokens", 0) + cache_read + cache_write
        output_tokens = self.usage.get("output_tokens", 0)
        metadata: dict[str, Any] = {"model_provider": "anthropic"}
        if self.stop_reason:
            metadata["stop_reason"] = self.stop_reason
        if self.model:
            metadata["model_name"] = self.model
        usage = None
        if self.usage:
            usage = {"input_tokens": input_tokens, "output_tokens": output_tokens,
                     "total_tokens": input_tokens + output_tokens,
                     "input_token_details": {"cache_read": cache_read, "cache_creation": cache_write}}
        return ChatGenerationChunk(message=AIMessageChunk(content="", response_metadata=metadata,
                                                          usage_metadata=usage, chunk_position="last"))


# ── The chat model ───────────────────────────────────────────────────────────


class ChatClaudePlan(BaseChatModel):
    """Claude through the person's own Claude Code and Claude plan."""

    model_name: str
    reasoning_plan: Any | None = None
    max_tokens: int | None = None

    @property
    def _llm_type(self) -> str:
        return "claude_plan_cli"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_name": self.model_name}

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: Any = None, **kwargs: Any) -> Runnable:
        # Claude Code can't force a tool; "none" binds none.
        specs = [] if tool_choice == "none" else [tool_spec(tool) for tool in tools]
        return self.bind(tools=specs, **kwargs)

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                  run_manager: Any | None = None, **kwargs: Any) -> ChatResult:
        return generate_from_stream(self._stream(messages, stop=stop, run_manager=run_manager, **kwargs))

    def _effort(self) -> str | None:
        plan = self.reasoning_plan
        if plan is None or getattr(plan, "is_default", True):
            return None
        selection = plan.selection
        effort = str(getattr(selection, "effort", "") or "")
        return effort if getattr(selection, "kind", "") == "effort" and effort in EFFORTS else None

    def _stream(self, messages: list[BaseMessage], stop: list[str] | None = None,
                run_manager: Any | None = None, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        from row_bot.providers.claude_subscription import (
            claude_code_environment,
            claude_code_workdir,
            require_claude_plan,
        )

        status = require_claude_plan()
        scope = current_cancellation_scope()
        if scope is not None and scope.is_cancelled():
            return
        tools = [tool_spec(tool) for tool in kwargs.get("tools") or []]
        system, frames = conversation_frames(messages)
        names = {spec.wire: spec.name for spec in tools}
        for message in messages:
            for call in getattr(message, "tool_calls", None) or []:
                name = str(call.get("name") or "")
                names.setdefault(wire_tool_name(name), name)
        env = claude_code_environment()
        max_tokens = kwargs.get("max_tokens") or self.max_tokens
        if max_tokens:
            env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(int(max_tokens))
        with _call_folder() as folder:
            servers: dict[str, Any] = {}
            if tools:
                tools_file = _write(folder / "tools.json", json.dumps([spec.manifest() for spec in tools]))
                servers[TOOL_SERVER] = _tool_server(tools_file)
            argv = claude_code_argv(
                status.program, model=self.model_name, effort=self._effort(),
                system_file=_write(folder / "system.md", system),
                mcp_file=_write(folder / "mcp.json", json.dumps({"mcpServers": servers})),
            )
            try:
                run = ClaudeCodeRun(argv, env=env, cwd=claude_code_workdir())
            except OSError as exc:
                from row_bot.providers.claude_subscription import ClaudePlanUnavailable, forget_claude_plan_status

                forget_claude_plan_status()
                raise ClaudePlanUnavailable(
                    "Claude Code didn't start. Check Claude in Settings › Providers, then try "
                    "again.") from exc
            unregister = scope.register(run.stop, "claude_plan.stop") if scope is not None else None
            try:
                yield from self._turn(run, frames, ClaudeTurnReader(names=names, model=self.model_name),
                                      run_manager, scope)
            finally:
                if unregister is not None:
                    unregister()
                run.stop()

    def _turn(self, run: ClaudeCodeRun, frames: list[dict[str, Any]], reader: ClaudeTurnReader,
              run_manager: Any, scope: Any) -> Iterator[ChatGenerationChunk]:
        def cancelled() -> bool:
            return scope is not None and scope.is_cancelled()

        for frame in frames[:-1]:
            run.send(frame)
            if frame["type"] == "user":
                self._acknowledged(run, cancelled)
            if cancelled():
                return
        run.send(frames[-1])
        run.close_input()
        while not reader.finished:
            event = run.next_event(_REPLY_SECONDS)
            if cancelled():
                return
            if event is None:
                raise ClaudeCodeEnded(run)
            for chunk in reader.feed(event):
                if run_manager and isinstance(chunk.message.content, str) and chunk.message.content:
                    run_manager.on_llm_new_token(chunk.message.content, chunk=chunk)
                yield chunk
        if reader.stop_reason != "tool_use":
            # A finished answer: Claude Code writes its result and ends by itself.
            try:
                while (event := run.next_event(_RESULT_GRACE_SECONDS)) is not None:
                    if event.get("type") == "result":
                        break
            except ClaudeCodeSilent:
                pass
        # A tool call: Claude Code would only refuse it now, so it is stopped here.
        if not cancelled():
            yield reader.last_chunk()

    @staticmethod
    def _acknowledged(run: ClaudeCodeRun, cancelled: Any) -> None:
        """Wait for Claude Code to take one earlier turn without asking the model."""
        while True:
            event = run.next_event(_ACK_SECONDS)
            if event is None:
                raise ClaudeCodeEnded(run)
            if event.get("type") == "assistant" and (event.get("error") or (event.get("message") or {}).get("error")):
                message = event.get("message") if isinstance(event.get("message"), dict) else {}
                raise claude_code_error(str(event.get("error") or message.get("error")), _message_text(message))
            if event.get("type") == "result":
                if event.get("is_error") or event.get("num_turns") not in (0, None):
                    raise claude_code_error(str(event.get("subtype") or ""), str(event.get("result") or ""))
                return
            if cancelled():
                return


# ── Claude Code's model list ─────────────────────────────────────────────────


def initialize_claude_code(program: str) -> dict[str, Any]:
    """Claude Code's answer to ``initialize`` (its models and account kind); asks no model anything."""
    from row_bot.providers.claude_subscription import claude_code_environment, claude_code_workdir

    with _call_folder() as folder:
        argv = [program, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--tools", "", "--setting-sources", "", "--strict-mcp-config",
                "--mcp-config", str(_write(folder / "mcp.json", json.dumps({"mcpServers": {}}))),
                "--disable-slash-commands", "--no-session-persistence"]
        run = ClaudeCodeRun(argv, env=claude_code_environment(), cwd=claude_code_workdir())
        request_id = f"row-bot-{uuid.uuid4().hex[:12]}"
        try:
            run.send({"type": "control_request", "request_id": request_id, "request": {"subtype": "initialize"}})
            while (event := run.next_event(_INITIALIZE_SECONDS)) is not None:
                response = event.get("response") if isinstance(event.get("response"), dict) else {}
                if event.get("type") == "control_response" and response.get("request_id") == request_id:
                    if response.get("subtype") != "success":
                        raise RuntimeError(f"Claude Code refused to start: {_short(response.get('error'))}")
                    inner = response.get("response")
                    return inner if isinstance(inner, dict) else {}
            raise ClaudeCodeEnded(run)
        finally:
            run.stop()

