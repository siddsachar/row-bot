"""Shell tool — execute shell commands with a persistent working directory.

Provides the agent with a ``run_command`` tool that executes shell commands
on the user's machine.  Working directory is tracked across calls within
the same thread (conversation).  The workspace folder is shared with the
Filesystem tool (configured once in Settings → System Access).

Safety
------
* **Blocked commands** — patterns that match catastrophic/irreversible
  operations (e.g. ``rm -rf /``, ``format C:``) are refused outright.
* **Safe commands** — read-only / informational commands (``ls``, ``pwd``,
  ``git status``, …) execute automatically without user approval.
* **Everything else** — triggers a LangGraph ``interrupt()`` so the user
  can approve or deny before execution.

Shell history is persisted to ``~/.row-bot/shell_history.json`` so that it
survives app restarts (the subprocess session itself does not survive).
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import threading
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from row_bot.approval_policy import normalize_approval_mode
from row_bot.brand import APP_DATA_DIR_ENV, DEFAULT_DATA_DIR_NAME
from row_bot.data_paths import get_row_bot_data_dir, private_data_dirs
from row_bot.process_cancellation import run_cancellable_subprocess
from row_bot.secret_store import SERVER_SECRETS_DIR_ENV
from row_bot.tools.base import BaseTool
from row_bot.tools import registry
from row_bot.tools import approval_gate
from row_bot.tools.approval_gate import (
    APPROVAL_DENIED,
    APPROVAL_NOT_NEEDED_SAFE,
    approval_check,
    resolve_approval_line,
    with_approval,
)

logger = logging.getLogger(__name__)

# ── Data directory for shell history ─────────────────────────────────────────
DATA_DIR = get_row_bot_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)
_HISTORY_PATH = DATA_DIR / "shell_history.json"


# ═════════════════════════════════════════════════════════════════════════════
# SAFETY CLASSIFICATION
# ═════════════════════════════════════════════════════════════════════════════

# Patterns that are ALWAYS blocked (catastrophic / irreversible)
_BLOCKED_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\brm\s+(-[a-z]*f[a-z]*\s+)?/(\s*$|\*)",      # rm -rf / AND rm -rf /*
        r"\brm\s+-[a-z]*r[a-z]*\s+/(etc|var|usr|home|boot|sys|proc)\b",  # rm system dirs
        r"\brm\s+-[a-z]*f[a-z]*\s+~/?$",               # rm -rf ~
        r"\bmkfs\b",                                     # format filesystem
        r"\bdd\s+.*of=/dev/",                            # dd to device
        r"\bformat\s+[a-z]:",                            # Windows format drive
        r">\s*/dev/sd[a-z]",                             # overwrite disk
        r"\b(shutdown|reboot|halt|poweroff)\b",          # system power
        r"\breg\s+delete\b.*\\\\HKLM",                  # Windows registry delete
        r"Remove-Item\s+.*-Recurse.*\s+[A-Z]:\\$",      # PowerShell rm C:\
        r"del\s+/[sq]\s+[A-Z]:\\",                       # Windows del /s C:\
        r":(){ :\|:& };:",                               # fork bomb
        r"\|\s*(ba)?sh\b",                               # pipe to shell
        r"\|\s*python[23]?\s+(-c\b|-)",                  # pipe to python -c / -
        r"\bInvoke-Expression\b|\biex\b",                # PowerShell code exec
        r"\bchmod\s+(-R\s+)?000\s+/",                    # chmod 000 /
    ]
]

# Read-only commands that run without approval, by the command's own name (the first word, not a prefix of
# it, so "fdisk" is not "fd"). The value lists the options that make the command write a file, delete or
# run another program; with any of them the command asks like any other. A "--long" option also matches
# an abbreviation of it, and a one-letter "-x" also matches inside a cluster such as "-Hx".
# Not here: curl/wget (prompt injection vectors), env/printenv (they print every secret in the environment).
_SAFE_COMMANDS: dict[str, tuple[str, ...]] = {
    # Navigation & listing
    "ls": (), "dir": (), "pwd": (), "cd": (), "pushd": (), "popd": (), "tree": ("-o",),
    "get-location": (), "set-location": (), "get-childitem": (),
    # Reading
    "cat": (), "head": (), "tail": (), "less": ("-o", "--log-file"), "more": (), "type": (),
    "get-content": (), "select-string": (),
    "wc": (), "file": ("-c", "--compile"), "stat": (), "du": (), "df": (),
    # Info
    "echo": (), "whoami": (), "uname": (), "which": (), "where": (), "get-command": (),
    "write-output": (), "write-host": (),
    # Search
    "find": ("-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"),
    "grep": (), "rg": ("--pre", "--hostname-bin"), "fd": ("-x", "--exec", "--exec-batch"), "ag": ("--pager",),
    # System info
    "systeminfo": (), "ver": (), "lsb_release": (), "ps": (), "tasklist": (),
    "ping": (), "nslookup": (), "dig": (), "traceroute": (), "tracert": (),
}
# Exact read-only command lines.
_VERSION_CHECKS = frozenset({
    ("python", "--version"), ("python3", "--version"), ("node", "--version"), ("npm", "--version"),
    ("pip", "--version"),
})
# Read-only subcommands, and the options that make them write or run another program.
_READ_ONLY_SUBCOMMANDS: dict[str, tuple[frozenset[str], tuple[str, ...]]] = {
    "pip": (frozenset({"list", "show", "freeze"}), ("--log",)),
    "conda": (frozenset({"list", "info"}), ()),
}
# Git subcommands come straight after "git": "git -c core.pager=... log" runs the pager it names.
_READ_ONLY_GIT = frozenset({"status", "log", "diff", "show"})
_GIT_WRITING_OPTIONS = ("--output", "--ext-diff", "--textconv")
_GIT_LISTING_OPTIONS = {
    "branch": frozenset({"-a", "-r", "-v", "-vv", "-l", "--all", "--remotes", "--list", "--show-current"}),
    "remote": frozenset({"-v", "--verbose"}),
    "tag": frozenset({"-l", "--list"}),
}
_HOSTNAME_READ_OPTIONS = frozenset({"-s", "-f", "-d", "-i", "-I", "-A", "--short", "--fqdn", "--long", "--domain",
                                    "--ip-address", "--all-ip-addresses", "--all-fqdns"})
# PowerShell resolves these to provider-aware cmdlets: a path such as "env:" lists environment variables
# and "HKLM:" reads the registry, not files.
_POWERSHELL_PATH_COMMANDS = frozenset({
    "ls", "dir", "cat", "type", "more", "cd", "pushd",
    "get-childitem", "get-content", "set-location", "select-string",
})
_PROVIDER_PATH = re.compile(r"^(env|variable|function|alias|cert|hklm|hkcu|wsman):|\w::", re.IGNORECASE)
# \\server\share (or //server/share): reading it connects to that server and, on Windows, signs in to it.
_NETWORK_PATH = re.compile(r"^(\\\\|//)[^\\/\s]")
_OPTION_PREFIX = re.compile(r"^--?[\w-]+[=:]")

# Outside quotes, these chain, background, pipe, redirect, open a subshell or script block, or substitute.
_OPERATOR_CHARACTERS = frozenset(";&|<>(){}`")
# PowerShell also reads these as quotes, where sh reads them as plain characters.
_LOOKALIKE_QUOTES = frozenset("\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u201f")
# Discarding output is the one redirect a read-only command may use.
_NULL_REDIRECT = re.compile(r">>?[ \t]*(/dev/null|\$null)(?=[ \t]|$)", re.IGNORECASE)
_VARIABLE = re.compile(r"\$[\w{(@?:$!#*-]")


def _shell_words(line: str) -> list[str] | None:
    """Split one command line into words, or ``None`` when it does more than run one program.

    The same text must mean the same thing to sh and to PowerShell, so anything either would read as
    chaining, backgrounding, piping, redirecting (except to the null device), substitution, a script block
    or a variable returns ``None``, as does quoting the two shells read differently.
    """
    words: list[str] = []
    word: list[str] = []
    in_word = False
    i, end = 0, len(line)
    while i < end:
        char = line[i]
        if char in " \t":
            if in_word:
                words.append("".join(word))
                word, in_word = [], False
            i += 1
            continue
        if char.isspace() or char < " " or char == "\x7f" or char in _LOOKALIKE_QUOTES:
            return None
        if char == ">" and (null := _NULL_REDIRECT.match(line, i)):
            if word not in ([], ["1"], ["2"], ["*"]):  # the stream number belongs to the redirect
                words.append("".join(word))
            word, in_word = [], False
            i = null.end()
            continue
        if char in _OPERATOR_CHARACTERS or _VARIABLE.match(line, i):
            return None
        if char in "'\"":
            close = line.find(char, i + 1)
            if close < 0:
                return None
            body = line[i + 1:close]
            # sh reads \" as an escaped quote and PowerShell as the end of the string; PowerShell reads a
            # doubled quote as an escaped one and sh as two strings, and ends a string at a lookalike quote.
            # Inside double quotes both still expand.
            if line[close + 1:close + 2] == char or not _LOOKALIKE_QUOTES.isdisjoint(body):
                return None
            if char == '"' and (body.endswith("\\") or "`" in body or _VARIABLE.search(body)):
                return None
            word.append(body)
            in_word = True
            i = close + 1
            continue
        word.append(char)
        in_word = True
        i += 1
    if in_word:
        words.append("".join(word))
    return words


def _uses_option(args: list[str], options: tuple[str, ...]) -> bool:
    for arg in args:
        name = arg.split("=", 1)[0].lower()
        for option in options:
            if option.startswith("--"):
                if len(name) > 2 and name.startswith("--") and (option.startswith(name) or name.startswith(option)):
                    return True
            elif len(option) == 2:
                if name.startswith("-") and not name.startswith("--") and option[1] in name[1:]:
                    return True
            elif name == option:
                return True
    return False


def _names_other_location(name: str, arg: str) -> bool:
    path = _OPTION_PREFIX.sub("", arg)
    if _NETWORK_PATH.match(path):
        return True
    return name in _POWERSHELL_PATH_COMMANDS and _PROVIDER_PATH.search(path) is not None


def _is_read_only_git(args: list[str]) -> bool:
    if not args:
        return False
    subcommand, rest = args[0], args[1:]
    if subcommand in _GIT_LISTING_OPTIONS:
        if subcommand == "remote" and rest[:1] in (["show"], ["get-url"]):
            return True
        if subcommand == "tag" and rest[:1] in (["-l"], ["--list"]):
            return not any(arg.startswith("-") for arg in rest[1:])
        return all(arg in _GIT_LISTING_OPTIONS[subcommand] for arg in rest)
    if args[:2] == ["stash", "list"]:
        return True
    return subcommand in _READ_ONLY_GIT and not _uses_option(rest, _GIT_WRITING_OPTIONS)


def _is_read_only(words: list[str]) -> bool:
    """True for a command line that only reads: a safe command without an option that writes or runs."""
    lowered = [word.lower() for word in words]
    name, args = lowered[0].removesuffix(".exe"), lowered[1:]
    if name in _SAFE_COMMANDS:
        return not _uses_option(args, _SAFE_COMMANDS[name]) and not any(
            _names_other_location(name, arg) for arg in words[1:])
    if (name, *args) in _VERSION_CHECKS:
        return True
    if name == "git":
        return _is_read_only_git(args)
    if name in _READ_ONLY_SUBCOMMANDS:
        subcommands, writing = _READ_ONLY_SUBCOMMANDS[name]
        return bool(args) and args[0] in subcommands and not _uses_option(args[1:], writing)
    if name == "command":
        return args[:1] == ["-v"]
    if name == "date":
        # "date -s", "date --set" and BSD "date 0101..." set the clock.
        return not _uses_option(args, ("-s", "--set")) and not any(re.fullmatch(r"[\d.]+", arg) for arg in args)
    if name == "hostname":
        # A name, "-F file" or "-b" sets it.
        return all(word in _HOSTNAME_READ_OPTIONS for word in words[1:])
    if name == "top":
        return args[:2] == ["-l", "1"]
    if name == "ip":
        return args[:1] == ["addr"] and args[1:2] in ([], ["show"], ["list"])
    if name == "ipconfig":
        # Windows "/release", "/renew" and "/flushdns", or macOS "set", change the network.
        return all(arg in ("/all", "/displaydns", "/?") for arg in args) or (
            bool(args) and (args[0].startswith("get") or args[0] == "ifcount"))
    if name == "ifconfig":
        # An interface name followed by anything ("en0 down") changes it.
        return all(arg in ("-a", "-l", "-s", "-v") for arg in args) or (
            len(args) == 1 and not args[0].startswith("-"))
    return False


# Ways a command can name the home folder, so "~/.row-bot" and "$env:USERPROFILE\.row-bot" both match.
_HOME_REFERENCES = re.compile(r"(\$env:userprofile|\$env:home|%userprofile%|\$\{home\}|\$home|~)(?=/|$)")
_DATA_DIR_SEGMENT = re.compile(r"(^|[/\s\"'=:])" + re.escape(DEFAULT_DATA_DIR_NAME.lower()) + r"($|[/\s\"'*])")
PRIVATE_DATA_REFUSAL = (
    "BLOCKED: this command reads Row-Bot's private data folder (conversations, settings, keys), which agents "
    "can't read. The person can allow it, with approval each time, in Settings > System > Advanced. Connected "
    "MCP servers are used through their tools, never through their configuration files."
)


def _slashes(text: str) -> str:
    return re.sub(r"/+", "/", text.lower().replace("\\", "/"))


def names_private_data(command: str) -> bool:
    """True when a command names Row-Bot's data folder or the server secrets folder: a path in any common
    spelling, or its variable."""
    text = _slashes(command)
    if APP_DATA_DIR_ENV.lower() in text or SERVER_SECRETS_DIR_ENV.lower() in text:
        return True
    home = _slashes(str(Path.home()))
    text = _HOME_REFERENCES.sub(lambda _match: home, text)
    folders = [_slashes(str(folder.expanduser().resolve())) for folder in private_data_dirs()]
    return any(folder in text for folder in folders) or _DATA_DIR_SEGMENT.search(text) is not None


def _append_process_note(output: str, note: str) -> str:
    text = str(output or "").rstrip()
    return f"{text}\n{note}" if text else note


def classify_command(command: str, extra_blocked: list[re.Pattern] | None = None) -> str:
    """Classify a command as ``'safe'``, ``'needs_approval'``, ``'private_data'`` or ``'blocked'``.

    Parameters
    ----------
    command : str
        The shell command to classify.
    extra_blocked : list[re.Pattern] | None
        Additional user-configured blocked patterns.

    Returns
    -------
    str
        One of ``'safe'``, ``'needs_approval'``, ``'private_data'`` (it names Row-Bot's data folder),
        ``'blocked'``.
    """
    cmd = command.strip()

    # Multi-line commands: classify each line, take the most severe.
    if '\n' in cmd:
        severities = {"blocked": 3, "private_data": 2, "needs_approval": 1, "safe": 0}
        worst = "safe"
        lines = cmd.split('\n')
        for index, line in enumerate(lines):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            line_class = classify_command(line, extra_blocked)
            # A trailing backslash joins the next line onto this one in sh, so "find . -name \" and
            # "ls -delete" are each safe but together delete.
            if line_class == "safe" and line.endswith("\\") and index < len(lines) - 1:
                line_class = "needs_approval"
            if severities.get(line_class, 0) > severities.get(worst, 0):
                worst = line_class
            if worst == "blocked":
                return "blocked"
        return worst

    # Check built-in blocked patterns
    for pattern in _BLOCKED_PATTERNS:
        if pattern.search(cmd):
            return "blocked"

    # Check user-configured blocked patterns
    if extra_blocked:
        for pattern in extra_blocked:
            if pattern.search(cmd):
                return "blocked"

    if names_private_data(cmd):
        return "private_data"

    # Safe only when the line runs one read-only program: no chaining, backgrounding, pipes, redirects or
    # substitution (operators inside quotes are plain text), and no option that writes or runs.
    words = _shell_words(cmd)
    if words and _is_read_only(words):
        return "safe"
    return "needs_approval"


# ═════════════════════════════════════════════════════════════════════════════
# SHELL SESSION — tracks working directory across commands
# ═════════════════════════════════════════════════════════════════════════════

_IS_WINDOWS = platform.system() == "Windows"
_CWD_MARKER = "___ROW_BOT_CWD___"


def shell_argv(command: str, *, report_cwd: bool = True) -> list[str]:
    """The platform shell running *command*, as ``run_command`` runs it.

    PowerShell starts fresh for every call, so its error list and native status are cleared
    before the command; a native exit code is kept, and any PowerShell error record fails the
    command even when a later statement succeeded. With *report_cwd* the folder the command
    ended in is written last to stderr (``_CWD_MARKER``), so ``cd`` carries over.
    """
    if _IS_WINDOWS:
        wrapped = (
            "$global:Error.Clear()\n"
            "$global:LASTEXITCODE = 0\n"
            "$__row_bot_terminated = $false\n"
            "try {\n"
            f"& {{\n{command}\n}}\n"
            "$__row_bot_ok = $?\n"
            "} catch {\n"
            "$__row_bot_ok = $false\n"
            "$__row_bot_terminated = $true\n"
            "[Console]::Error.WriteLine([string]$_)\n"
            "}\n"
            "$__row_bot_native_exit = $LASTEXITCODE\n"
            "$__row_bot_error_count = $Error.Count\n"
            + (f"[Console]::Error.WriteLine('{_CWD_MARKER}' + (Get-Location).Path)\n" if report_cwd else "")
            + "if ($__row_bot_native_exit -ne 0) { exit $__row_bot_native_exit }\n"
            "if ($__row_bot_terminated -or -not $__row_bot_ok -or "
            "$__row_bot_error_count -gt 0) { exit 1 }"
        )
        return ["powershell", "-NoProfile", "-NoLogo", "-Command", wrapped]
    # The marker keeps the command's own exit code.
    wrapped = (
        f"{command}\n__row_bot_status=$?\n"
        f'echo "{_CWD_MARKER}$(pwd)" >&2\nexit $__row_bot_status'
        if report_cwd else command
    )
    return [os.environ.get("SHELL", "/bin/bash"), "-c", wrapped]


def _split_cwd(output: str) -> tuple[str, str]:
    """(output without the folder marker, the folder the command ended in or '')."""
    if _CWD_MARKER not in output:
        return output, ""
    position = output.rfind(_CWD_MARKER)
    rest = output[position + len(_CWD_MARKER):]
    folder, _newline, after = rest.partition("\n")
    return (output[:position] + after).rstrip(), folder.strip()


class ShellSession:
    """A logical shell session that tracks cwd across commands.

    Each ``run_command`` call starts the platform shell through Row-Bot's process manager
    (``background_processes``), but the working directory is carried forward between calls
    so ``cd`` works as expected. A command still running after the inline limit carries on
    in the background, owned by the conversation.
    """

    def __init__(self, working_dir: str | None = None):
        self.cwd: str = working_dir or os.path.expanduser("~")
        self._lock = threading.Lock()

    def run_command(
        self,
        command: str,
        timeout: int = 120,
        *,
        conversation_id: str = "default",
        background: bool = False,
    ) -> dict:
        """Execute *command* and return a result dict.

        Returns
        -------
        dict
            ``{command, output, exit_code, cwd, duration}``; a command carrying on in the
            background also has ``process_id`` (and ``exit_code`` None).
        """
        from row_bot import background_processes
        from row_bot.cancellation import current_cancellation_scope

        start = time.time()
        scope = current_cancellation_scope()
        if scope is not None and scope.is_cancelled():
            return {"command": command, "output": "Command stopped before it started.", "exit_code": 130,
                    "cwd": self.cwd, "duration": 0.0}
        with self._lock:
            try:
                outcome = background_processes.run_command(
                    conversation_id,
                    shell_argv(command),
                    command=command,
                    folder=self.cwd,
                    timeout=timeout,
                    background=background,
                )
            except background_processes.ProcessLimitError as exc:
                return {"command": command, "output": f"Error: {exc}", "exit_code": -1, "cwd": self.cwd,
                        "duration": round(time.time() - start, 2)}
            except background_processes.ProcessStartError:
                if background:
                    raise
                # Without process containment here (an old Linux kernel), commands still run in the turn.
                return self._run_plain(command, timeout, start)
            except Exception as exc:
                return {"command": command, "output": f"Error: {exc}", "exit_code": -1, "cwd": self.cwd,
                        "duration": round(time.time() - start, 2)}
            if not outcome.finished:
                tail, _earlier = outcome.record.log.tail(8_000)
                return {"command": command, "output": _split_cwd(tail)[0], "exit_code": None, "cwd": self.cwd,
                        "duration": round(time.time() - start, 2), "process_id": outcome.record.process_id}
            output, folder = _split_cwd(outcome.output)
            if folder and os.path.isdir(folder):
                self.cwd = folder
            if outcome.earlier_output_dropped:
                output = (f"[Earlier output was not kept: Row-Bot keeps the last "
                          f"{2 * background_processes.LOG_ROTATE_BYTES // (1024 * 1024)} MB of a command's output.]\n"
                          + output)
            if outcome.cancelled:
                output = _append_process_note(output, "Command stopped by user.")
            if outcome.limit_message:
                output = _append_process_note(
                    output, f"Stopped after {timeout}s instead of carrying on in the background: "
                            f"{outcome.limit_message}")
            return {
                "command": command,
                "output": output.rstrip(),
                "exit_code": outcome.exit_code if outcome.exit_code is not None else -1,
                "cwd": self.cwd,
                "duration": round(time.time() - start, 2),
            }

    def _run_plain(self, command: str, timeout: int, start: float) -> dict:
        """Run *command* as a plain subprocess (no background continuation): stopped at *timeout*."""
        # Saved keys and Row-Bot's own secrets stay out of the command's environment.
        from row_bot.api_keys import child_environment

        try:
            proc = run_cancellable_subprocess(
                shell_argv(command),
                cwd=self.cwd,
                timeout=timeout,
                text=True,
                encoding="utf-8", errors="replace",
                env=child_environment(),
            )
            if proc.timed_out:
                return {"command": command, "output": f"Command timed out after {timeout}s", "exit_code": -1,
                        "cwd": self.cwd, "duration": timeout}
            stdout, stderr = (proc.stdout or "").rstrip(), proc.stderr or ""
            stderr, folder = _split_cwd(stderr)
            if folder and os.path.isdir(folder):
                self.cwd = folder
            if proc.cancelled:
                stderr = _append_process_note(stderr, "Command stopped by user.")
            output = stdout
            if stderr.strip():
                output = f"{output}\n{stderr.rstrip()}" if output else stderr.rstrip()
            return {"command": command, "output": output, "exit_code": proc.returncode, "cwd": self.cwd,
                    "duration": round(time.time() - start, 2)}
        except Exception as exc:
            return {"command": command, "output": f"Error: {exc}", "exit_code": -1, "cwd": self.cwd,
                    "duration": round(time.time() - start, 2)}


class ShellSessionManager:
    """One :class:`ShellSession` per ``thread_id``."""

    def __init__(self):
        self._sessions: dict[str, ShellSession] = {}
        self._lock = threading.Lock()

    def get_session(self, thread_id: str, working_dir: str | None = None) -> ShellSession:
        with self._lock:
            if thread_id not in self._sessions:
                self._sessions[thread_id] = ShellSession(working_dir)
            return self._sessions[thread_id]

    def kill_session(self, thread_id: str) -> None:
        with self._lock:
            self._sessions.pop(thread_id, None)

    def kill_all(self) -> None:
        with self._lock:
            self._sessions.clear()


# Global session manager
_session_manager = ShellSessionManager()


def get_session_manager() -> ShellSessionManager:
    """Return the global session manager (for cleanup from UI code)."""
    return _session_manager


# ═════════════════════════════════════════════════════════════════════════════
# SHELL HISTORY PERSISTENCE
# ═════════════════════════════════════════════════════════════════════════════

def _load_history() -> dict[str, list[dict]]:
    if _HISTORY_PATH.exists():
        try:
            return json.loads(_HISTORY_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_history(history: dict[str, list[dict]]) -> None:
    try:
        _HISTORY_PATH.write_text(
            json.dumps(history, default=str), encoding="utf-8"
        )
    except OSError:
        logger.warning("Failed to save shell history", exc_info=True)


def get_shell_history(thread_id: str) -> list[dict]:
    """Get shell history for a thread."""
    return _load_history().get(thread_id, [])


def append_shell_history(thread_id: str, entry: dict) -> None:
    """Append a command entry to shell history for a thread."""
    history = _load_history()
    history.setdefault(thread_id, []).append(entry)
    _save_history(history)


def clear_shell_history(thread_id: str) -> None:
    """Clear shell history for a thread."""
    history = _load_history()
    if thread_id in history:
        del history[thread_id]
        _save_history(history)


# ═════════════════════════════════════════════════════════════════════════════
# SHELL TOOL
# ═════════════════════════════════════════════════════════════════════════════

class ShellTool(BaseTool):

    @property
    def name(self) -> str:
        return "shell"

    @property
    def display_name(self) -> str:
        return "🖥️ Shell"

    @property
    def description(self) -> str:
        return (
            "Execute shell commands on the user's computer. "
            "Use this when the user asks to run terminal commands, install packages, "
            "check system information, run scripts, manage processes, or perform "
            "any task that requires command-line access. "
            "Commands run in a persistent session — cd persists across calls "
            "within the same conversation."
        )

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def config_schema(self) -> dict[str, dict]:
        return {
            "blocked_commands": {
                "label": "Additional blocked patterns (comma-separated)",
                "type": "text",
                "default": "",
            },
            "allow_data_folder": {
                "label": "Let the agent read Row-Bot's data folder (asks each time)",
                "type": "bool",
                "default": False,
            },
        }

    @property
    def destructive_tool_names(self) -> set[str]:
        # Empty — the tool self-gates via interrupt() based on per-command
        # safety classification (safe commands auto-run, others prompt).
        return set()

    @property
    def untrusted_tool_names(self) -> set[str]:
        # Process output is whatever the program printed.
        return {"run_command", "read_terminal", "start_process", "check_process", "stop_process"}

    def _get_developer_workspace_root(
        self,
        thread_id: str = "",
        developer_workspace_id: str = "",
    ) -> str | None:
        """Infer the active Developer workspace for a thread, if one exists."""
        clean_thread_id = str(thread_id or "").strip()
        clean_workspace_id = str(developer_workspace_id or "").strip()
        if not clean_workspace_id and (
            not clean_thread_id or clean_thread_id == "default"
        ):
            return None
        try:
            from row_bot.developer.storage import get_workspace
            from row_bot.threads import _get_thread_developer_workspace

            workspace_id = clean_workspace_id or _get_thread_developer_workspace(
                clean_thread_id
            )
            workspace = get_workspace(workspace_id) if workspace_id else None
        except Exception:
            logger.debug("Could not resolve Developer workspace for shell cwd", exc_info=True)
            return None
        if workspace and workspace.path and os.path.isdir(workspace.path):
            return workspace.path
        return None

    def _get_workspace_root(
        self,
        thread_id: str = "",
        developer_workspace_id: str = "",
    ) -> str | None:
        """Resolve shell cwd, preferring Developer workspaces for Developer threads."""
        developer_root = self._get_developer_workspace_root(
            thread_id,
            developer_workspace_id,
        )
        if developer_root:
            return developer_root
        fs_tool = registry.get_tool("filesystem")
        if fs_tool:
            root = fs_tool.get_config("workspace_root", "")
            if root and os.path.isdir(root):
                return root
        return None

    def _get_runtime_configurable(
        self,
        runtime_config: RunnableConfig | None,
    ) -> Mapping[str, Any]:
        """Return LangChain runtime ``configurable`` values, if available."""
        if isinstance(runtime_config, Mapping):
            configurable = runtime_config.get("configurable")
            if isinstance(configurable, Mapping):
                return configurable
        return {}

    def _get_shell_context_ids(
        self,
        runtime_config: RunnableConfig | None = None,
    ) -> tuple[str, str]:
        """Resolve the current thread and Developer workspace for shell execution."""
        configurable = self._get_runtime_configurable(runtime_config)
        thread_id = str(configurable.get("thread_id") or "").strip()
        developer_workspace_id = str(
            configurable.get("developer_workspace_id") or ""
        ).strip()

        if not thread_id or not developer_workspace_id:
            try:
                from row_bot.developer import tool_context

                thread_id = thread_id or str(tool_context.get_thread_id() or "").strip()
                developer_workspace_id = developer_workspace_id or str(
                    tool_context.get_workspace_id() or ""
                ).strip()
                if thread_id and not developer_workspace_id:
                    developer_workspace_id = str(
                        tool_context.infer_workspace_id_from_thread(thread_id) or ""
                    ).strip()
            except Exception:
                logger.debug("Could not resolve Developer shell context", exc_info=True)

        if not thread_id:
            try:
                from row_bot.agent import _current_thread_id_var

                thread_id = _current_thread_id_var.get() or ""
            except ImportError:
                thread_id = ""

        if not thread_id:
            thread_id = "default"
        return thread_id, developer_workspace_id

    def _get_extra_blocked(self) -> list[re.Pattern]:
        """Parse user-configured additional blocked patterns."""
        raw = self.get_config("blocked_commands", "")
        if not raw:
            return []
        patterns = []
        for part in raw.split(","):
            part = part.strip()
            if part:
                try:
                    patterns.append(re.compile(re.escape(part), re.IGNORECASE))
                except re.error:
                    pass
        return patterns

    def _gate(
        self,
        *,
        command: str,
        classification: str,
        payload: dict[str, Any],
        risks: tuple[str, ...],
        private_description: str,
        blocked_message: str,
    ) -> tuple[str | None, str]:
        """The approval a classified command needs, as run_command asks it: (refusal or None, approval line).

        Safe commands run without asking; the private data folder needs its setting and then
        asks (the reviewer under Auto, allowed under Hands-off); anything else asks by the
        conversation's approval level. Blocked patterns never reach here.
        """
        approval = APPROVAL_NOT_NEEDED_SAFE
        if classification == "private_data":
            if self.get_config("allow_data_folder", False) is not True:
                return PRIVATE_DATA_REFUSAL, ""
            mode = normalize_approval_mode(approval_gate.current_approval_mode())
            if mode == "block":
                return f"BLOCKED: this command reads Row-Bot's private data folder: {command}", ""
            # Asked under Ask; under Auto the reviewer approves it only when the person clearly asked
            # for it; Hands-off allows it (the setting above still has to be on).
            outcome, approval = resolve_approval_line(
                {**payload, "label": "Read Row-Bot's private data?", "description": private_description},
                approval_mode=mode,
                risks=("reads Row-Bot's private data folder (conversations, settings, keys)", *risks[1:]),
            )
            if outcome != "allow":
                return with_approval(approval or APPROVAL_DENIED, "Command cancelled by user."), approval
        if classification == "needs_approval":
            # Determine execution context for background-audit compatibility.
            # approval_check() below resolves it by the approval level.
            try:
                from row_bot.agent import is_background_workflow

                _is_bg = is_background_workflow()
            except Exception:
                _is_bg = False
            logger.debug("Shell command approval required; background=%s", _is_bg)
            blocked, approval = approval_check(
                payload,
                risks=risks,
                blocked_message=blocked_message,
                cancelled_message="Command cancelled by user.",
            )
            if blocked:
                return blocked, approval
        return None, approval

    def _session_for(self, runtime_config: RunnableConfig | None) -> tuple[str, str, ShellSession]:
        """This conversation's id, its shell session's key and the session (the folder ``cd`` left it in)."""
        thread_id, developer_workspace_id = self._get_shell_context_ids(runtime_config)
        working_dir = self._get_workspace_root(thread_id, developer_workspace_id)
        session_key = (
            thread_id
            if thread_id and thread_id != "default"
            else f"developer:{developer_workspace_id}" if developer_workspace_id else "default"
        )
        return thread_id, session_key, _session_manager.get_session(session_key, working_dir)

    def execute(
        self,
        command: str,
        runtime_config: RunnableConfig | None = None,
        approval_reason: str = "",
        background: bool = False,
    ) -> str:
        """Run a shell command.  Self-gates with ``interrupt()`` for
        non-safe commands."""
        command = command.strip()
        if not command:
            return "No command provided."

        # ── Safety classification ────────────────────────────────────────
        extra = self._get_extra_blocked()
        classification = classify_command(command, extra)

        if classification == "blocked":
            return (
                f"🚫 BLOCKED: This command matches a blocked pattern and "
                f"cannot be executed: {command}"
            )

        risks: tuple[str, ...] = ("runs a command that can change things on this computer",)
        if background:
            risks += (_BACKGROUND_RISK,)
        refusal, approval = self._gate(
            command=command,
            classification=classification,
            payload={
                "tool": "run_command",
                "label": "Run shell command",
                "description": f"Run shell command: {command}",
                "approval_reason": approval_reason,
                "args": {"command": command},
            },
            risks=risks,
            private_description=(
                "This reads Row-Bot's private data folder (conversations, settings, keys): "
                f"{command}"
            ),
            blocked_message=(
                f"BLOCKED: This command requires approval and cannot run "
                f"in Block approval mode: {command}"
            ),
        )
        if refusal:
            return refusal

        # ── Execute ──────────────────────────────────────────────────────
        thread_id, session_key, session = self._session_for(runtime_config)
        try:
            result = session.run_command(command, conversation_id=thread_id, background=background)
        except Exception as exc:
            from row_bot.background_processes import ProcessStartError

            if not isinstance(exc, ProcessStartError):
                raise
            return with_approval(approval, f"$ {command}\nError: {exc}")

        # Persist to history
        result["timestamp"] = datetime.now().isoformat()
        append_shell_history(session_key, result)

        # Format for LLM
        output = result["output"]
        exit_code = result["exit_code"]
        duration = result["duration"]
        cwd = result["cwd"]

        if result.get("process_id"):
            return with_approval(approval, _carried_on_text(result, started=background))

        parts = [f"$ {command}"]
        if output:
            # Long output keeps its head and tail here; the whole of it stays readable by handle.
            from row_bot.tool_outputs import COMMAND_OUTPUT_CHARS, bounded

            parts.append(bounded(output, tool_name="run_command", max_chars=COMMAND_OUTPUT_CHARS))
        parts.append(f"\n[Exit code: {exit_code} | Duration: {duration}s | cwd: {cwd}]")

        return with_approval(approval, "\n".join(parts))

    # ── Background processes (F1) ────────────────────────────────────────

    def start_process(
        self,
        command: str,
        folder: str = "",
        env: dict[str, str] | None = None,
        name: str = "",
        approval_reason: str = "",
        runtime_config: RunnableConfig | None = None,
    ) -> str:
        """Start a program that keeps running in the background; asks first like run_command."""
        from row_bot import background_processes as processes
        from row_bot.data_paths import is_private_data_path

        command = str(command or "").strip()
        if not command:
            return "No command provided."
        try:
            variables = processes.check_environment(env)
        except ValueError as exc:
            return f"Error: {exc}"
        thread_id, session_key, session = self._session_for(runtime_config)
        where = Path(session.cwd)
        if str(folder or "").strip():
            chosen = Path(os.path.expanduser(str(folder).strip()))
            where = chosen if chosen.is_absolute() else where / chosen
        try:
            where = where.resolve()
        except OSError:
            pass
        if not where.is_dir():
            return f"Error: the folder {where} doesn't exist. Pass an existing folder, or leave it out to use {session.cwd}."

        # Classified exactly like run_command; the variables' values count as part of the command.
        classification = classify_command("\n".join([command, *variables.values()]), self._get_extra_blocked())
        if classification == "blocked":
            return (
                f"🚫 BLOCKED: This command matches a blocked pattern and "
                f"cannot be executed: {command}"
            )
        if classification != "blocked" and is_private_data_path(where):
            classification = "private_data"
        label = " ".join(str(name or "").split())[: processes.MAX_NAME_CHARS]
        refusal, approval = self._gate(
            command=command,
            classification=classification,
            payload=start_process_approval(command, str(where), variables, label, approval_reason),
            risks=(
                "runs a command that can change things on this computer",
                _BACKGROUND_RISK,
            ),
            private_description=(
                "This starts a background process that reads Row-Bot's private data folder "
                f"(conversations, settings, keys): {command}"
            ),
            blocked_message=(
                f"BLOCKED: This command requires approval and cannot start "
                f"in Block approval mode: {command}"
            ),
        )
        if refusal:
            return refusal
        try:
            record = processes.start(
                thread_id,
                shell_argv(command, report_cwd=False),
                command=command,
                folder=str(where),
                kind="process",
                env=variables,
                name=label,
            )
        except (processes.ProcessLimitError, processes.ProcessStartError) as exc:
            return with_approval(approval, f"Error: {exc}")
        # Its first moments: until it ends, prints a local address, or a few seconds pass.
        deadline = time.monotonic() + START_WATCH_SECONDS
        while time.monotonic() < deadline and not record.urls:
            if processes.wait(record, 0.1):
                break
        return with_approval(approval, process_text(record, lines=40, conversation_id=thread_id))

    def check_process(
        self,
        process_id: str = "",
        lines: int = 40,
        runtime_config: RunnableConfig | None = None,
    ) -> str:
        """A background process's status and the end of its output, or this conversation's list."""
        from row_bot import background_processes as processes

        thread_id, _key, _session = self._session_for(runtime_config)
        process_id = str(process_id or "").strip()
        if not process_id:
            return processes_list_text(thread_id)
        record = processes.get(process_id)
        if record is None or record.conversation_id != thread_id:
            return (f"No background process {process_id!r} in this conversation. "
                    "Call check_process without a process_id to list them.")
        return process_text(record, lines=lines, conversation_id=thread_id, spill_full=True)

    def stop_process(self, process_id: str, runtime_config: RunnableConfig | None = None) -> str:
        """Stop a background process of this conversation and everything it started."""
        from row_bot import background_processes as processes

        thread_id, _key, _session = self._session_for(runtime_config)
        process_id = str(process_id or "").strip()
        record = processes.get(process_id)
        if record is None or record.conversation_id != thread_id:
            return (f"No background process {process_id!r} in this conversation. "
                    "Call check_process without a process_id to list them.")
        if record.running:
            processes.stop(process_id, by="agent")
            processes.wait(record, 15)
        return process_text(record, lines=10, conversation_id=thread_id)

    def as_langchain_tools(self) -> list:
        """Return ``run_command``, ``read_terminal`` and the background process tools."""
        from row_bot.background_processes import MAX_RUNNING_PER_CONVERSATION

        tool_instance = self

        def guarded(call):
            try:
                return call()
            except Exception as exc:
                from langgraph.errors import GraphInterrupt
                if isinstance(exc, GraphInterrupt):
                    raise
                logger.error("Shell tool error: %s", exc, exc_info=True)
                return f"Error executing command: {exc}"

        def run_command(
            command: str,
            approval_reason: str = "",
            background: bool = False,
            config: RunnableConfig = None,
        ) -> str:
            """Execute a shell command on the user's computer.

            Commands run in a persistent session — cd, environment changes,
            etc. persist across calls within the same conversation.
            Safe commands (ls, pwd, git status, …) run automatically;
            other commands require user approval.

            Args:
                command: The shell command to execute
                        (e.g. 'ls -la', 'pip install requests',
                         'git status').
                approval_reason: One short user-facing sentence explaining
                        why this command is needed if approval is required.
                background: Start it as a background process and return at
                        once, for a command that takes long (a build, a
                        download). A command still running after 2 minutes
                        carries on in the background anyway.
            """
            return guarded(lambda: tool_instance.execute(
                command,
                runtime_config=config,
                approval_reason=approval_reason,
                background=bool(background),
            ))

        def start_process(
            command: str,
            folder: str = "",
            env: dict[str, str] | None = None,
            name: str = "",
            approval_reason: str = "",
            config: RunnableConfig = None,
        ) -> str:
            return guarded(lambda: tool_instance.start_process(
                command, folder=folder, env=env, name=name, approval_reason=approval_reason,
                runtime_config=config,
            ))

        def check_process(process_id: str = "", lines: int = 40, config: RunnableConfig = None) -> str:
            return guarded(lambda: tool_instance.check_process(process_id, lines=lines, runtime_config=config))

        def stop_process(process_id: str, config: RunnableConfig = None) -> str:
            return guarded(lambda: tool_instance.stop_process(process_id, runtime_config=config))

        def read_terminal(lines: int = 50) -> str:
            """Read recent output from the user's interactive terminal.

            This reads the scrollback buffer of the user's xterm.js
            terminal — both user-typed commands and replayed agent
            command results.  Use when the user says "look at my
            terminal" or "what does the terminal show".

            Args:
                lines: Number of recent lines to return (default 50,
                       max 500).  Increase for verbose output.
            """
            try:
                from row_bot.terminal_bridge import TerminalBridge
                bridge = TerminalBridge.get_instance()
                output = bridge.read_output(lines=lines)
                if not output.strip():
                    return "(Terminal is empty — no recent output)"
                return output
            except Exception as exc:
                logger.debug("read_terminal error: %s", exc)
                return f"Could not read terminal: {exc}"

        return [
            StructuredTool.from_function(
                func=run_command,
                name="run_command",
                description=(
                    "Execute a shell command on the user's computer. "
                    "Commands run in a persistent session — cd persists "
                    "across calls. Safe commands (ls, pwd, git status, etc.) "
                    "run automatically; other commands require user approval. "
                    "When approval may be needed, include approval_reason as "
                    "one short sentence explaining why the command is needed. "
                    "Returns the command output and exit code. A command still "
                    "running after 2 minutes (or started with background=true) "
                    "carries on as a background process: the result names its "
                    "process_id for check_process and stop_process. For a server "
                    "or watcher that should keep running, use start_process."
                ),
            ),
            StructuredTool.from_function(
                func=start_process,
                name="start_process",
                args_schema=_StartProcessInput,
                description=(
                    "Start a program that keeps running in the background, such as a "
                    "development server or a file watcher, in any folder and with extra "
                    "environment variables. Asks for approval like run_command. Returns its "
                    "process_id, its first output and any local address it serves. Background "
                    "processes belong to this conversation, stop when Row-Bot quits, and at "
                    f"most {MAX_RUNNING_PER_CONVERSATION} run at once here."
                ),
            ),
            StructuredTool.from_function(
                func=check_process,
                name="check_process",
                args_schema=_CheckProcessInput,
                description=(
                    "Check a background process of this conversation: running or not, its "
                    "exit code, how long it ran, the local address it serves and the last lines "
                    "of its output (the rest through read_tool_output). Without a process_id, "
                    "list this conversation's background processes."
                ),
            ),
            StructuredTool.from_function(
                func=stop_process,
                name="stop_process",
                args_schema=_StopProcessInput,
                description=(
                    "Stop a background process of this conversation and everything it started. "
                    "Needs no approval."
                ),
            ),
            StructuredTool.from_function(
                func=read_terminal,
                name="read_terminal",
                description=(
                    "Read recent output from the user's terminal. Returns "
                    "the last N lines of plain text visible in the terminal. "
                    "Use when the user references terminal content or you "
                    "need to inspect what a previous command produced. "
                    "Default 50 lines, max 500."
                ),
            ),
        ]


# ═════════════════════════════════════════════════════════════════════════════
# BACKGROUND PROCESS TEXT
# ═════════════════════════════════════════════════════════════════════════════

_BACKGROUND_RISK = "keeps running in the background after this reply, until it ends, is stopped or Row-Bot quits"
# start_process watches a new process this long for an early failure or its local address.
START_WATCH_SECONDS = 5.0
_TAIL_CHARS = 6_000


class _StartProcessInput(BaseModel):
    command: str = Field(description="The command to run, as you would type it in the shell (e.g. 'npm run dev').")
    folder: str = Field(default="", description="The folder to run it in; empty for the shell's current folder.")
    env: dict[str, str] = Field(default_factory=dict, description="Extra environment variables, e.g. {\"PORT\": \"3000\"}.")
    name: str = Field(default="", description="A short name people see, e.g. 'Dev server'.")
    approval_reason: str = Field(
        default="",
        description="One short user-facing sentence explaining why it is needed if approval is required.",
    )


class _CheckProcessInput(BaseModel):
    process_id: str = Field(default="", description="The process_id (proc-…); empty to list this conversation's.")
    lines: int = Field(default=40, description="How many of the last output lines to show (at most 200).")


class _StopProcessInput(BaseModel):
    process_id: str = Field(description="The process_id (proc-…) to stop.")


def start_process_approval(
    command: str, folder: str, variables: dict[str, str], name: str, approval_reason: str,
) -> dict[str, Any]:
    """The approval request for starting a background process: what runs, with what, and where.

    The command leads; the folder comes last, where a long path doesn't hide the rest.
    """
    what = f"Start “{name}” in the background" if name else "Start in the background"
    description = f"{what}: {command}"
    if variables:
        shown = ", ".join(f"{key}={value[:40]}{'…' if len(value) > 40 else ''}" for key, value in variables.items())
        description += f" with {shown}"
    description += f". It keeps running after this reply until it's stopped or Row-Bot quits. Folder: {folder}"
    args: dict[str, Any] = {"command": command, "folder": folder}
    if variables:
        args["env"] = sorted(variables)
    return {
        "tool": "start_process",
        "label": "Start a background process",
        "description": description,
        "approval_reason": approval_reason,
        "args": args,
    }


def _status_words(record) -> str:
    from row_bot.background_processes import duration_words

    took = duration_words(record.runtime_seconds())
    status = record.status
    if status == "running":
        return f"running for {took}"
    if status == "stopping":
        return "stopping"
    if status == "finished":
        return f"finished (exit code 0) after {took}"
    if status == "failed":
        code = record.exit_code
        return f"failed ({'exit code ' + str(code) if code is not None else 'it could not start'}) after {took}"
    who = {"you": "by the person", "agent": "by you", "quit": "when Row-Bot quit",
           "restart": "when Row-Bot restarted", "conversation": "with its conversation",
           "limit": "at the limit"}.get(record.stopped_by, "")
    return f"stopped {who} after {took}".replace("  ", " ")


def process_text(record, *, lines: int, conversation_id: str, spill_full: bool = False) -> str:
    """A background process for the agent: its handle line first, then what it runs and its output."""
    from row_bot import background_processes as processes
    from row_bot.tool_outputs import READER_TOOL_NAME, spill

    lines = max(1, min(int(lines or 40), 200))
    parts = [f"Background process {record.process_id}: {_status_words(record)}."]
    parts.append(f"$ {record.command}")
    parts.append(f"Folder: {record.folder}")
    if record.name:
        parts.append(f"Name: {record.name}")
    if record.env_names:
        parts.append("Environment: " + ", ".join(record.env_names))
    served = processes.addresses(record)
    if served:
        listening = set(record.ports)
        parts.append("Serving: " + ", ".join(
            f"{url}{' (listening)' if int(url.rsplit(':', 1)[1]) in listening else ''}" for url in served))
    tail, earlier = record.log.tail(_TAIL_CHARS)
    shown = tail.splitlines()[-lines:]
    more = earlier or len(tail.splitlines()) > len(shown)
    if shown:
        parts.append(f"Last {len(shown)} lines of output:" if more else "Output:")
        parts.append("\n".join(shown))
    else:
        parts.append("No output yet." if record.running else "No output.")
    if more and spill_full:
        text, dropped = record.log.text()
        handle = spill(text, thread_id=conversation_id, source="check_process")
        if handle:
            note = " (earlier output is no longer kept)" if dropped else ""
            parts.append(f"[The whole output so far{note} is {len(text):,} characters, saved as {handle}: "
                         f'read it with {READER_TOOL_NAME}(handle="{handle}", offset=0).]')
    elif more:
        parts.append(f'[More output: check_process(process_id="{record.process_id}") saves all of it for reading.]')
    if record.running:
        parts.append(f'Check it with check_process(process_id="{record.process_id}"); stop it with '
                     f'stop_process(process_id="{record.process_id}"). It stops when Row-Bot quits.')
    return "\n".join(parts)


def _carried_on_text(result: dict, *, started: bool) -> str:
    """run_command's result for a command that carries on in the background (F6)."""
    from row_bot import background_processes as processes

    process_id = str(result["process_id"])
    lead = ("started in the background" if started else
            f"still running after {processes.INLINE_SECONDS // 60} minutes, so it carries on in the background")
    parts = [f"Background process {process_id}: {lead}.", f"$ {result['command']}"]
    output = str(result.get("output") or "").rstrip()
    if output:
        shown = output.splitlines()[-20:]
        parts.append("Output so far (last lines):" if len(shown) < len(output.splitlines()) else "Output so far:")
        parts.append("\n".join(shown))
    parts.append(f'Check it with check_process(process_id="{process_id}"); stop it with '
                 f'stop_process(process_id="{process_id}"). It stops when Row-Bot quits.')
    parts.append(f"\n[Exit code: still running | Duration: {result['duration']}s | cwd: {result['cwd']}]")
    return "\n".join(parts)


def processes_list_text(conversation_id: str) -> str:
    """This conversation's background processes, for the agent."""
    from row_bot import background_processes as processes

    records = processes.list_processes(conversation_id)
    if not records:
        return "No background processes in this conversation. Start one with start_process."
    running = sum(record.running for record in records)
    lines = [f"Background processes in this conversation ({running} running, at most "
             f"{processes.MAX_RUNNING_PER_CONVERSATION} at once):"]
    for record in records[:20]:
        served = processes.addresses(record) if record.running else []
        lines.append(" · ".join(filter(None, (
            f"- {record.process_id}", _status_words(record), record.name, record.command[:200],
            ", ".join(served),
        ))))
    if len(records) > 20:
        lines.append(f"…and {len(records) - 20} older ones.")
    return "\n".join(lines)


registry.register(ShellTool())
