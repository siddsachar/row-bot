"""The ``row-bot run`` subcommand: its flags, and the process set-up that comes before Row-Bot loads.

Row-Bot's modules bind the data folder when they are imported, so the folder is chosen here (a fresh
throwaway one unless ``--data-dir`` names one) and put in the environment before :mod:`row_bot.headless.run`
is imported. Nothing here opens a database, reads a secret or reaches the network.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXIT_DONE = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_BUDGET = 4
EXIT_PROVIDER = 5

#: ``--api-style``: the request format the endpoint at ``--base-url`` speaks.
API_STYLES = {"chat": "openai_chat", "responses": "openai_responses", "anthropic": "anthropic_messages"}
#: ``--approval``: Row-Bot's approval levels. Hands-off is only ever the person's explicit choice.
APPROVALS = {"ask": "approve", "auto": "auto", "hands-off": "full"}
ENV_PREFIX = "ROW_BOT_RUN_"
_EPILOG = """\
exit codes:
  0  done
  1  Row-Bot failed (see the log)
  2  the command line is wrong (a flag, the task file, the key's variable)
  3  finished, but an action that needed approval was refused (nobody could approve it)
  4  the --timeout or --max-rounds budget ran out, or the turn stopped repeating itself
  5  the model endpoint failed (unreachable, key refused, a rate limit, the model can't run)

Flags marked [env] can also be set with ROW_BOT_RUN_<FLAG> (ROW_BOT_RUN_MODEL, ROW_BOT_RUN_BASE_URL, ...).
"""


class UsageError(ValueError):
    """The command line can't start a run; ``str()`` says why."""


@dataclass(frozen=True)
class RunOptions:
    task: str
    workspace: Path
    model: str
    base_url: str = ""
    api_style: str = "chat"
    api_key_env: str = ""
    context_window: int | None = None
    max_output_tokens: int | None = None
    reasoning_effort: str = ""
    approval: str = "ask"
    timeout: float | None = None
    max_rounds: int | None = None
    output_dir: Path | None = None
    data_dir: Path | None = None

    @property
    def approval_mode(self) -> str:
        return APPROVALS[self.approval]


def _env(name: str) -> str | None:
    value = os.environ.get(ENV_PREFIX + name, "").strip()
    return value or None


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value <= 0:
        raise argparse.ArgumentTypeError("must be above 0")
    return value


def _positive_seconds(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of seconds") from None
    if value <= 0:
        raise argparse.ArgumentTypeError("must be above 0")
    return value


def add_run_subcommand(subparsers: Any) -> argparse.ArgumentParser:
    """Add ``run`` to the launcher's subcommands."""

    parser = subparsers.add_parser(
        "run",
        help="Run one task headless and exit (no window, server or tray)",
        description="Run one task headless with Row-Bot's own prompts and tools, then exit. By default on a "
                    "fresh throwaway data folder: your profile, memories and chats are never read or written.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    task = parser.add_mutually_exclusive_group(required=True)
    task.add_argument("--task", help="The task, as text")
    task.add_argument("--task-file", help="A UTF-8 file holding the task ('-' reads standard input)")
    parser.add_argument("--workspace", default=".", help="The folder the agent works in (default: here)")
    parser.add_argument("--model", default=_env("MODEL"),
                        help="[env] With --base-url, the model's name at that endpoint; otherwise a Row-Bot "
                             "model ref, provider:model (ollama:qwen3:8b, anthropic:<model>, openai:<model>)")
    endpoint = parser.add_argument_group("a custom model endpoint")
    endpoint.add_argument("--base-url", default=_env("BASE_URL"),
                          help="[env] The endpoint's address (https://host/v1; for --api-style anthropic, the "
                               "address before /v1)")
    endpoint.add_argument("--api-style", choices=sorted(API_STYLES), default=_env("API_STYLE") or "chat",
                          help="[env] chat: OpenAI Chat Completions (default); responses: OpenAI Responses; "
                               "anthropic: Anthropic Messages")
    endpoint.add_argument("--api-key-env", default=_env("API_KEY_ENV"), metavar="NAME",
                          help="[env] The environment variable holding the key (never the key itself). It is "
                               "removed from the environment before the agent runs a command. Without "
                               "--base-url it defaults to the provider's own (ANTHROPIC_API_KEY, ...)")
    limits = parser.add_argument_group("limits")
    limits.add_argument("--context-window", type=_positive_int, default=_env("CONTEXT_WINDOW"), metavar="TOKENS",
                        help="[env] The context window to work within")
    limits.add_argument("--max-output-tokens", type=_positive_int, default=_env("MAX_OUTPUT_TOKENS"),
                        metavar="TOKENS", help="[env] The longest reply the model may write")
    limits.add_argument("--reasoning-effort", default=_env("REASONING_EFFORT") or "", metavar="LEVEL",
                        help="[env] The model's reasoning effort, where it has one (low, medium, high, ...)")
    limits.add_argument("--timeout", type=_positive_seconds, default=_env("TIMEOUT"), metavar="SECONDS",
                        help="[env] Stop the run after this long (exit 4)")
    limits.add_argument("--max-rounds", type=_positive_int, default=_env("MAX_ROUNDS"), metavar="N",
                        help="[env] Model rounds a turn may take (default 1000; exit 4 at the ceiling)")
    parser.add_argument("--approval", choices=sorted(APPROVALS), default="ask",
                        help="Who approves actions that need approval. ask (default): nobody can, so they are "
                             "refused; auto: Row-Bot's reviewer approves what matches the task and refuses the "
                             "rest; hands-off: everything is allowed (only when you pass it)")
    parser.add_argument("--output-dir", default=_env("OUTPUT_DIR"), metavar="DIR",
                        help="[env] Write trajectory.json (ATIF), answer.md, summary.json and row-bot.log here")
    parser.add_argument("--data-dir", metavar="DIR",
                        help="Use this data folder (created if need be) instead of a throwaway one")
    return parser


def options_from_args(args: argparse.Namespace) -> RunOptions:
    """The run's options from the parsed command line; :class:`UsageError` when they can't start a run."""

    if args.task is not None:
        task = str(args.task)
    elif args.task_file == "-":
        task = sys.stdin.read()
    else:
        try:
            task = Path(args.task_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise UsageError(f"can't read the task file: {exc}") from None
    if not task.strip():
        raise UsageError("the task is empty")
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise UsageError(f"the workspace {workspace} is not a folder")
    model = str(args.model or "").strip()
    if not model:
        raise UsageError("name the model with --model (or ROW_BOT_RUN_MODEL)")
    base_url = str(args.base_url or "").strip().rstrip("/")
    if base_url and not base_url.startswith(("http://", "https://")):
        raise UsageError("--base-url must start with http:// or https://")
    if args.api_style not in API_STYLES:
        raise UsageError(f"--api-style is one of {', '.join(sorted(API_STYLES))}")
    key_env = str(args.api_key_env or "").strip()
    if key_env and key_env not in os.environ:
        raise UsageError(f"the environment variable {key_env} (--api-key-env) is not set")
    return RunOptions(
        task=task, workspace=workspace, model=model, base_url=base_url, api_style=args.api_style,
        api_key_env=key_env, context_window=args.context_window, max_output_tokens=args.max_output_tokens,
        reasoning_effort=str(args.reasoning_effort or "").strip().lower(), approval=args.approval,
        timeout=args.timeout, max_rounds=args.max_rounds,
        output_dir=Path(args.output_dir).expanduser().resolve() if args.output_dir else None,
        data_dir=Path(args.data_dir).expanduser().resolve() if args.data_dir else None,
    )


def prepare_process(options: RunOptions) -> tuple[Path, bool]:
    """Set this process up for the run, before Row-Bot loads: (the data folder, whether it is throwaway).

    The data folder, the workspace, the install count off (no server starts anyway) and Learning's preview
    flag unset, so Learning never exists in a headless run and records nothing."""

    if options.data_dir is not None:
        data_dir, throwaway = options.data_dir, False
        data_dir.mkdir(parents=True, exist_ok=True)
    else:
        data_dir, throwaway = Path(tempfile.mkdtemp(prefix="row-bot-run-")), True
    os.environ["ROW_BOT_DATA_DIR"] = str(data_dir)
    os.environ["ROW_BOT_WORKSPACE"] = str(options.workspace)
    os.environ["ROW_BOT_INSTALL_COUNT"] = "0"
    for name in ("ROW_BOT_LEARNING_PREVIEW", "ROW_BOT_LEARNING"):
        os.environ.pop(name, None)
    return data_dir, throwaway


def dispatch_run_command(args: argparse.Namespace) -> int:
    """``row-bot run``: the exit code."""

    try:
        options = options_from_args(args)
    except UsageError as exc:
        print(f"row-bot run: {exc}", file=sys.stderr)
        return EXIT_USAGE
    data_dir, throwaway = prepare_process(options)
    # The answer goes to standard output; on standard error only warnings (the full log is in --output-dir).
    for handler in logging.getLogger().handlers:
        handler.setLevel(max(handler.level, logging.WARNING))
    from row_bot.headless.run import run

    result = run(options, data_dir=data_dir, throwaway=throwaway)
    if result.answer:
        print(result.answer)
    if result.status != "done":
        print(f"row-bot run: {result.status}: {result.reason or 'see the log'}", file=sys.stderr)
    return result.exit_code


__all__ = [
    "API_STYLES", "APPROVALS", "EXIT_BUDGET", "EXIT_DONE", "EXIT_ERROR", "EXIT_PROVIDER", "EXIT_REFUSED",
    "EXIT_USAGE", "RunOptions", "UsageError", "add_run_subcommand", "dispatch_run_command", "options_from_args",
    "prepare_process",
]
