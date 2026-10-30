"""A fake Claude Code put first on PATH as ``claude``, so Row-Bot's real lookup, commands and stream-json run.

    claude = install_fake_claude(tmp_path, monkeypatch, reply=text_reply("Hello"))
    ...  # code under test runs `claude --version`, `claude auth status`, `claude -p ...`
    run = claude.runs()[-1]
    assert run["argv"][:2] == ["-p", "--input-format"]

``--version`` prints ``version``; ``auth status`` prints ``auth`` as JSON and exits with ``auth_exit``. Print
mode starts the MCP servers named in ``--mcp-config`` the way Claude Code does (``initialize``, then
``tools/list``, and ``tools/call`` on the first tool with ``call_tool``), answers ``initialize`` control
requests with ``models``, acknowledges each ``shouldQuery: false`` user frame with a zero-turn result, and
after the last frame writes ``reply``: stream-json events, ``{"sleep": seconds}``, ``{"hang": true}`` (wait to
be stopped) or ``{"exit": code}``. Every run records its arguments, environment, system prompt, the tools
its MCP server listed and every frame it was sent, as it goes, so a stopped run's record is complete.
``install_fake_claude(..., installed=False)`` leaves PATH and the usual install folders empty.

``auth login`` is Claude's sign-in as the panel shows it: it prints ``login_marker`` (a stand-in for what must
never reach Row-Bot's logs or stores) with a link and Claude's paste prompt, reads one line, and when it is
``login_code`` writes ``~/.claude/.credentials.json`` (as Claude Code does on Linux and Windows) and is signed
in from then on; any other code prints Claude's own refusal and asks again.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = r'''
import json, os, subprocess, sys, time
config = json.load(open(sys.argv[1], encoding="utf-8"))
args = sys.argv[2:]
log = open(config["log"], "a", encoding="utf-8")
def record(**fields):
    log.write(json.dumps(fields) + "\n")
    log.flush()
def out(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()
watched = ("ANTHROPIC", "CLAUDE", "DISABLE", "ENABLE", "MAX_THINKING", "ROW_BOT", "OPENAI")
env = {k: v for k, v in os.environ.items()
       if k.upper().startswith(watched) or any(part in k.upper() for part in ("KEY", "TOKEN", "SECRET"))}
record(start=True, argv=args, env=env, cwd=os.getcwd(), pid=os.getpid())
if args == ["--version"]:
    print(config["version"])
    sys.exit(0)
signed_in = os.path.join(os.path.dirname(config["log"]), "signed-in")
if args[:2] == ["auth", "status"]:
    if os.path.exists(signed_in):
        print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
                          "subscriptionType": "max"}, indent=2))
        sys.exit(0)
    print(json.dumps(config["auth"], indent=2))
    sys.exit(config["auth_exit"])
if args == ["auth", "login"]:
    marker = config.get("login_marker", "")
    sys.stdout.write("Welcome to Claude Code " + marker + "\r\n")
    sys.stdout.write("Browser didn't open? Use the url below to sign in:\r\n")
    sys.stdout.write("https://claude.ai/oauth/authorize?code=true&state=" + marker + "\r\n")
    while True:
        sys.stdout.write("Paste code here if prompted > ")
        sys.stdout.flush()
        line = sys.stdin.readline()
        if not line:
            sys.exit(1)
        code = line.strip()
        record(login_code_matched=code == config.get("login_code"))
        if code == config.get("login_code"):
            break
        sys.stdout.write("\r\nOAuth error: Invalid code\r\n\r\n")
        sys.stdout.flush()
    folder = os.path.join(os.path.expanduser("~"), ".claude")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, ".credentials.json"), "w", encoding="utf-8") as credentials:
        credentials.write(json.dumps({"claudeAiOauth": {"accessToken": marker}}))
    open(signed_in, "w").close()
    sys.stdout.write("\r\nLogin successful.\r\n")
    sys.stdout.flush()
    sys.exit(0)
if "-p" not in args:
    sys.exit(2)
def flag(name):
    return args[args.index(name) + 1] if name in args else None
system_file = flag("--system-prompt-file")
if system_file:
    record(system=open(system_file, encoding="utf-8").read())
servers = []
tools = []
mcp = flag("--mcp-config")
if mcp:
    for name, server in json.load(open(mcp, encoding="utf-8")).get("mcpServers", {}).items():
        child = subprocess.Popen([server["command"], *server["args"]], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, text=True, encoding="utf-8")
        servers.append(child)
        def rpc(method, params, ident):
            child.stdin.write(json.dumps({"jsonrpc": "2.0", "id": ident, "method": method, "params": params}) + "\n")
            child.stdin.flush()
            return json.loads(child.stdout.readline())
        rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fake"}}, 1)
        child.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        child.stdin.flush()
        listed = rpc("tools/list", {}, 2)["result"]["tools"]
        tools += ["mcp__" + name + "__" + tool["name"] for tool in listed]
        record(server=name, server_pid=child.pid, listed=listed)
        if config.get("call_tool") and listed:
            record(tool_call=rpc("tools/call", {"name": listed[0]["name"], "arguments": {}}, 3)["result"])
init = {"type": "system", "subtype": "init", "tools": tools, "model": flag("--model"),
        "permissionMode": flag("--permission-mode"), "apiKeySource": "none",
        "mcp_servers": [{"name": name, "status": "connected"} for name in json.load(open(mcp, encoding="utf-8")).get("mcpServers", {})] if mcp else []}
announced = False
for raw in sys.stdin:
    frame = json.loads(raw)
    record(frame=frame)
    if frame.get("type") == "control_request":
        out({"type": "control_response", "response": {"subtype": "success", "request_id": frame["request_id"],
             "response": {"models": config["models"], "account": {"subscriptionType": "Claude Max"}}}})
        continue
    if frame.get("type") != "user":
        continue
    if not announced:
        out(init)
        announced = True
    if frame.get("shouldQuery") is False:
        out({"type": "result", "subtype": "success", "is_error": False, "num_turns": 0, "result": ""})
        continue
    for event in config["reply"]:
        if "sleep" in event:
            time.sleep(event["sleep"])
        elif "hang" in event:
            while True:
                time.sleep(0.1)
        elif "exit" in event:
            sys.exit(event["exit"])
        else:
            out(event)
    break
for child in servers:
    child.terminate()
'''


@dataclass
class FakeClaude:
    bin_dir: Path
    log: Path

    def records(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def runs(self) -> list[dict[str, Any]]:
        """One merged record per process: argv, env, system, listed tools, tool_call and frames."""
        runs: list[dict[str, Any]] = []
        for row in self.records():
            if row.get("start"):
                runs.append({**row, "frames": []})
            elif "frame" in row:
                runs[-1]["frames"].append(row["frame"])
            else:
                runs[-1].update(row)
        return runs

    def model_runs(self) -> list[dict[str, Any]]:
        return [run for run in self.runs() if "-p" in run["argv"] and "--include-partial-messages" in run["argv"]]


def write_fake_claude_program(
    folder: Path,
    *,
    version: str = "2.1.282 (Claude Code)",
    auth: dict[str, Any] | None = None,
    auth_exit: int = 0,
    reply: list[dict[str, Any]] | None = None,
    models: list[dict[str, Any]] | None = None,
    call_tool: bool = False,
    login_code: str = "",
    login_marker: str = "",
) -> tuple[Path, Path]:
    """The fake's script and its config in ``folder`` (its runs log beside them); returns both paths."""
    folder.mkdir(parents=True, exist_ok=True)
    config = folder / "config.json"
    config.write_text(json.dumps({
        "log": str(folder / "runs.jsonl"), "version": version, "auth_exit": auth_exit, "call_tool": call_tool,
        "auth": auth if auth is not None else {"loggedIn": True, "authMethod": "claude.ai",
                                               "apiProvider": "firstParty", "subscriptionType": "max"},
        "reply": reply if reply is not None else text_reply("Hello from Claude."),
        "models": models if models is not None else MODELS,
        "login_code": login_code, "login_marker": login_marker,
    }), encoding="utf-8")
    script = folder / "fake_claude.py"
    script.write_text(_SCRIPT, encoding="utf-8")
    return script, config


def install_fake_claude(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    installed: bool = True,
    version: str = "2.1.282 (Claude Code)",
    auth: dict[str, Any] | None = None,
    auth_exit: int = 0,
    reply: list[dict[str, Any]] | None = None,
    models: list[dict[str, Any]] | None = None,
    call_tool: bool = False,
    login_code: str = "",
    login_marker: str = "",
) -> FakeClaude:
    bin_dir = tmp_path / "fake-claude-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    home = tmp_path / "fake-claude-home"
    home.mkdir(exist_ok=True)
    log = bin_dir / "runs.jsonl"
    # Only this folder on PATH, and a home without Claude Code's install folders: the real one is never found.
    monkeypatch.setenv("PATH", str(bin_dir))
    for name in ("HOME", "USERPROFILE", "APPDATA"):
        monkeypatch.setenv(name, str(home))
    from row_bot.providers import claude_subscription

    claude_subscription.forget_claude_plan_status()
    if not installed:
        return FakeClaude(bin_dir, log)
    script, config = write_fake_claude_program(
        bin_dir, version=version, auth=auth, auth_exit=auth_exit, reply=reply, models=models,
        call_tool=call_tool, login_code=login_code, login_marker=login_marker)
    if os.name == "nt":
        (bin_dir / "claude.cmd").write_text(f'@"{sys.executable}" "{script}" "{config}" %*\r\n', encoding="utf-8")
    else:
        shim = bin_dir / "claude"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "{config}" "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
    return FakeClaude(bin_dir, log)


# ── Recorded stream-json shapes (Claude Code 2.1.282) ─────────────────────────

MODELS = [
    {"value": "default", "displayName": "Default (recommended)", "resolvedModel": "claude-opus-5-5[1m]",
     "description": "Opus 5.5 with 1M context · Best for everyday, complex tasks",
     "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"], "supportsEffort": True,
     "supportsAdaptiveThinking": True},
    {"value": "opus[1m]", "displayName": "Opus (1M context)", "resolvedModel": "claude-opus-5-5[1m]",
     "description": "Opus 5.5 with 1M context · Best for everyday, complex tasks",
     "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"], "supportsEffort": True,
     "supportsAdaptiveThinking": True},
    {"value": "sonnet", "displayName": "Sonnet", "resolvedModel": "claude-sonnet-5",
     "description": "Sonnet 5 · Efficient for routine tasks",
     "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"], "supportsEffort": True,
     "supportsAdaptiveThinking": True},
    {"value": "haiku", "displayName": "Haiku", "resolvedModel": "claude-haiku-4-5-20251001",
     "description": "Haiku 4.5 · Fastest for quick answers"},
]


def _stream(event: dict[str, Any]) -> dict[str, Any]:
    return {"type": "stream_event", "event": event, "parent_tool_use_id": None, "session_id": "synthetic"}


def _start(model: str, input_tokens: int) -> dict[str, Any]:
    return _stream({"type": "message_start", "message": {
        "model": model, "id": "msg_synthetic", "type": "message", "role": "assistant", "content": [],
        "stop_reason": None, "usage": {"input_tokens": input_tokens, "cache_creation_input_tokens": 0,
                                       "cache_read_input_tokens": 0, "output_tokens": 1}}})


def _finish(stop_reason: str, *, input_tokens: int, output_tokens: int, cache_read: int = 0) -> list[dict[str, Any]]:
    return [
        _stream({"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                 "usage": {"input_tokens": input_tokens, "cache_creation_input_tokens": 0,
                           "cache_read_input_tokens": cache_read, "output_tokens": output_tokens}}),
        _stream({"type": "message_stop"}),
    ]


def text_reply(text: str, *, thinking: str = "", model: str = "claude-sonnet-5", cache_read: int = 0,
               stop_reason: str = "end_turn") -> list[dict[str, Any]]:
    events = [_start(model, 1056)]
    index = 0
    if thinking:
        events += [_stream({"type": "content_block_start", "index": 0,
                            "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
                   _stream({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "thinking_delta", "thinking": thinking}}),
                   _stream({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "signature_delta", "signature": "synthetic-signature"}}),
                   _stream({"type": "content_block_stop", "index": 0})]
        index = 1
    events.append(_stream({"type": "content_block_start", "index": index,
                           "content_block": {"type": "text", "text": ""}}))
    for start in range(0, len(text), 8):
        events.append(_stream({"type": "content_block_delta", "index": index,
                               "delta": {"type": "text_delta", "text": text[start:start + 8]}}))
    events += [{"type": "assistant", "message": {"model": model, "id": "msg_synthetic", "role": "assistant",
                                                 "content": [{"type": "text", "text": text}]}},
               _stream({"type": "content_block_stop", "index": index})]
    events += _finish(stop_reason, input_tokens=1056, output_tokens=15, cache_read=cache_read)
    events.append({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
                   "stop_reason": stop_reason, "result": text})
    return events


def tool_reply(name: str, arguments: dict[str, Any], *, model: str = "claude-sonnet-5",
               call_id: str = "toolu_synthetic01") -> list[dict[str, Any]]:
    """A reply calling ``name`` (as Claude Code names an MCP tool), then the refusal Claude Code writes."""
    wire = "mcp__rowbot__" + name
    body = json.dumps(arguments)
    events = [_start(model, 1160),
              _stream({"type": "content_block_start", "index": 0, "content_block": {
                  "type": "tool_use", "id": call_id, "name": wire, "input": {}}}),
              _stream({"type": "content_block_delta", "index": 0,
                       "delta": {"type": "input_json_delta", "partial_json": ""}})]
    for start in range(0, len(body), 6):
        events.append(_stream({"type": "content_block_delta", "index": 0,
                               "delta": {"type": "input_json_delta", "partial_json": body[start:start + 6]}}))
    events += [{"type": "assistant", "message": {"model": model, "role": "assistant", "content": [
                   {"type": "tool_use", "id": call_id, "name": wire, "input": arguments}]}},
               _stream({"type": "content_block_stop", "index": 0}),
               {"type": "user", "message": {"role": "user", "content": [{
                   "type": "tool_result", "tool_use_id": call_id, "is_error": True,
                   "content": f"Permission to use {wire} has been denied because Claude Code is running in "
                              "don't ask mode."}]}}]
    events += _finish("tool_use", input_tokens=1160, output_tokens=133)
    # What Claude Code would do next if it were not stopped: another model turn.
    events += [_start(model, 1300), {"hang": True}]
    return events


def error_reply(code: str, text: str) -> list[dict[str, Any]]:
    """An error Claude Code reports: an assistant message carrying the error, then a failed result."""
    return [{"type": "assistant", "error": code, "message": {
                "model": "<synthetic>", "role": "assistant", "content": [{"type": "text", "text": text}]}},
            {"type": "result", "subtype": "success", "is_error": True, "num_turns": 1, "result": text}]
