"""One headless run: the model, one conversation, the answers nobody is there to give, and the files it leaves.

Imported only after :func:`row_bot.headless.cli.prepare_process` has chosen the data folder. The task is sent
as one message on a new conversation, as the app sends it (``agent.stream_agent`` with every tool a fresh
profile has on), and the one follow-up the app would start after it runs too (it carries on a reply that
stopped on its next step, or checks files the reply says it saved). Nobody is there to answer what stops for a
person, so:

- an action that needs approval is refused, as when the person says no ("did not run"), and the run goes on;
  under ``auto`` the reviewer decides first and only what it would ask the person about is refused;
- a question to the person (``ask_person``) gets the answer an unattended workflow's question gets when nobody
  answers in time: go on with the recommended option.

Nothing here weakens a check: shell classification, the sandbox, MCP safety and the approval levels decide as
in the app.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from row_bot.headless.cli import (
    API_STYLES,
    EXIT_BUDGET,
    EXIT_DONE,
    EXIT_ERROR,
    EXIT_PROVIDER,
    EXIT_REFUSED,
    EXIT_USAGE,
    RunOptions,
    UsageError,
)

logger = logging.getLogger(__name__)

SUMMARY_SCHEMA = "row-bot-run-summary/1"
RUN_ENDPOINT_ID = "row-bot-run"
FOLDER_IN_USE = "Quit Row-Bot (or stop its server) before a run uses this data folder."
#: Answers to waiting approvals and questions one turn may need before the run gives up on it.
MAX_ANSWERS = 50
_EXIT_BY_STATUS = {"done": EXIT_DONE, "refused": EXIT_REFUSED, "budget": EXIT_BUDGET,
                   "provider_error": EXIT_PROVIDER, "usage_error": EXIT_USAGE, "error": EXIT_ERROR}


@dataclass
class RunResult:
    """What a run did: its status (``done``, ``refused``, ``budget``, ``provider_error``, ``usage_error`` or
    ``error``), why, its final answer, and the actions and questions nobody could answer."""

    status: str = "error"
    reason: str = ""
    answer: str = ""
    model: str = ""
    turns: int = 0
    refused_actions: list[dict[str, str]] = field(default_factory=list)
    unanswered_questions: int = 0
    prompts: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return _EXIT_BY_STATUS.get(self.status, EXIT_ERROR)

    def stop(self, status: str, reason: str) -> None:
        if self.status in {"running", "done", "refused"}:
            self.status, self.reason = status, str(reason)[:1000]


def run(options: RunOptions, *, data_dir: Path, throwaway: bool) -> RunResult:
    """Run the task to its end and write the output files; the result (its ``exit_code`` is the process's)."""

    from row_bot.data_folder_lock import FolderInUse, alone

    started = time.time()
    result = RunResult(status="running")
    log = _file_log(options.output_dir)
    thread_id = ""
    try:
        with alone(data_dir, message=FOLDER_IN_USE):
            try:
                model_ref = _setup(options, data_dir)
                result.model = model_ref
                thread_id = _new_conversation(model_ref, options)
                _drive(result, thread_id, model_ref, options)
            except UsageError as exc:
                result.stop("usage_error", str(exc))
            except Exception as exc:  # noqa: BLE001 - recorded; the exit code says it
                logger.exception("row-bot run failed")
                result.stop("error", f"{type(exc).__name__}: {exc}")
            if result.status == "running" and result.refused_actions:
                result.status = "refused"
                result.reason = (f"{len(result.refused_actions)} action(s) needed approval that nobody could give "
                                 f"(--approval {options.approval}), so they did not run")
            elif result.status == "running":
                result.status = "done"
            if options.output_dir is not None:
                _write_outputs(options, result, thread_id, started, throwaway)
    except FolderInUse as exc:
        result.stop("usage_error", str(exc))
    finally:
        if log is not None:
            logging.getLogger().removeHandler(log)
            log.close()
        if throwaway:
            shutil.rmtree(data_dir, ignore_errors=True)
    return result


# ── Set-up ───────────────────────────────────────────────────────────────────


def _setup(options: RunOptions, data_dir: Path) -> str:
    """Row-Bot loaded on the run's data folder, with the model and the limits; the model ref."""

    from row_bot import secret_store

    # Keys this process is handed live and die with it: never in the OS keychain or a file.
    secret_store.use_memory_backend()
    from row_bot import data_paths, learning
    from row_bot.environment_secrets import keep_environment_private, take_secret

    if Path(data_paths.get_row_bot_data_dir()).resolve() != data_dir.resolve():
        raise RuntimeError("Row-Bot was loaded on another data folder before the run chose its own")
    if learning.is_enabled():
        raise UsageError("Learning is on in this data folder, and a headless run is never recorded: "
                         "pause Learning there or use another folder")
    try:
        keep_environment_private()
    except OSError as exc:
        logger.warning("This process's environment stays readable by its own user's programs: %s", exc)
    key = take_secret(options.api_key_env) if options.api_key_env else None
    if options.api_key_env and key is None:
        raise UsageError(f"the environment variable {options.api_key_env} (--api-key-env) is empty")

    import row_bot.agent  # noqa: F401 - loads the runtime before the settings below
    from row_bot import agent_settings, models
    from row_bot.providers import runtime
    from row_bot.tools import registry

    model_ref = _endpoint_model(options, key) if options.base_url else _provider_model(options.model, key)
    models.adopt_saved_default(model_ref)
    if options.context_window:
        _set_context_window(model_ref, options.context_window)
    runtime.limit_reply_tokens(options.max_output_tokens)
    agent_settings.limit_rounds(options.max_rounds)
    registry.set_tool_config("filesystem", "workspace_root", str(options.workspace))
    return model_ref


def _endpoint_model(options: RunOptions, key: str | None) -> str:
    """The endpoint at ``--base-url`` as this data folder's own custom endpoint, and its model's ref."""

    from row_bot.providers import custom
    from row_bot.providers.selection import model_ref

    endpoint: dict[str, Any] = {
        "id": RUN_ENDPOINT_ID, "name": "row-bot run", "base_url": options.base_url,
        "transport": API_STYLES[options.api_style], "auth_required": bool(key), "api_key": key or "",
        "capability_probe": False,
    }
    if options.context_window:
        endpoint["manual_capabilities"] = {"context_window": options.context_window}
    custom.save_custom_endpoint(endpoint)
    custom.declare_endpoint_model(RUN_ENDPOINT_ID, options.model)
    return model_ref(custom.custom_provider_id(RUN_ENDPOINT_ID), options.model)


def _provider_model(text: str, key: str | None) -> str:
    """A configured provider's model (``provider:model`` or ``model:provider:model``) and its key, if any."""

    from row_bot import models
    from row_bot.environment_secrets import take_secret
    from row_bot.providers.auth_store import PROVIDER_API_KEY_ENV, replace_provider_api_key
    from row_bot.providers.catalog import get_provider_definition
    from row_bot.providers.selection import model_ref, parse_model_ref

    parsed = parse_model_ref(text if text.startswith("model:") else f"model:{text}")
    if parsed is None:
        raise UsageError("--model is provider:model (ollama:qwen3:8b), or use --base-url with the model's name")
    provider, model = parsed
    if get_provider_definition(provider) is None:
        raise UsageError(f"{provider} is not a provider here; use --base-url for an endpoint of your own")
    if key is None and provider in PROVIDER_API_KEY_ENV:
        key = take_secret(PROVIDER_API_KEY_ENV[provider])  # the provider's own variable, kept from commands
    if key:
        replace_provider_api_key(provider, key)
    try:
        if provider == "ollama":
            from row_bot.providers.model_catalog_cache import refresh_model_catalog_cache

            refresh_model_catalog_cache(reason="row-bot run", force=True, provider_id="ollama")
        else:
            models.refresh_cloud_provider_models(provider)
    except Exception as exc:  # noqa: BLE001 - the first turn's readiness error says the rest
        logger.warning("The %s model list wasn't refreshed: %s", provider, type(exc).__name__)
    return model_ref(provider, model)


def _set_context_window(model_ref: str, tokens: int) -> None:
    from row_bot import models
    from row_bot.providers.selection import parse_model_ref

    provider, model = parse_model_ref(model_ref) or ("", "")
    try:
        if provider == "ollama":
            models.set_local_model_settings(model, context=tokens)
        else:
            models.set_cloud_context_size(tokens)
    except ValueError as exc:
        raise UsageError(f"--context-window: {exc}") from None


def _new_conversation(model_ref: str, options: RunOptions) -> str:
    from row_bot import threads

    thread_id = f"run_{uuid.uuid4().hex[:12]}"
    threads.create_thread("row-bot run", thread_id=thread_id, approval_mode=options.approval_mode,
                          model_override=model_ref, seed_default_skills=False)
    if options.reasoning_effort:
        threads.set_thread_reasoning_selection(thread_id, model_ref,
                                               {"kind": "effort", "effort": options.reasoning_effort})
    return thread_id


# ── The turns ────────────────────────────────────────────────────────────────


def _drive(result: RunResult, thread_id: str, model_ref: str, options: RunOptions) -> None:
    """The task's turn, then the follow-up the app would start after it, if any (never after a follow-up)."""

    import row_bot.agent as agent
    from row_bot.application.conversation_followups import followup_after
    from row_bot.tools import registry

    tools = [tool.name for tool in registry.get_enabled_tools()]
    stop = threading.Event()
    timer = threading.Timer(options.timeout, stop.set) if options.timeout else None
    if timer is not None:
        timer.daemon = True
        timer.start()
    prompt: str | None = options.task
    try:
        while prompt is not None and result.status == "running":
            result.turns += 1
            result.prompts.append(prompt)
            config = {"configurable": {
                "thread_id": thread_id, "model_override": model_ref, "runtime_mode": "agent",
                "runtime_surface": "normal_chat", "approval_mode": options.approval_mode,
                "generation_id": uuid.uuid4().hex,
            }}
            events: Iterable[tuple[str, Any]] = agent.stream_agent(prompt, tools, config, stop_event=stop)
            for _ in range(MAX_ANSWERS):
                waiting = _consume(events, result)
                if not waiting or result.status != "running" or stop.is_set():
                    break
                events = _answer(agent, tools, config, waiting, result)
            else:
                result.stop("refused", f"the turn still waited for a person after {MAX_ANSWERS} answers")
            if stop.is_set():
                result.stop("budget", f"the run's --timeout of {options.timeout:g} seconds ran out")
            if result.status != "running" or result.turns > 1:
                break
            followup = followup_after(thread_id, result.answer, model_ref)
            prompt = followup.prompt if followup is not None else None
    finally:
        if timer is not None:
            timer.cancel()


def _consume(events: Iterable[tuple[str, Any]], result: RunResult) -> list[dict[str, Any]]:
    """Read one stream to its end: the answer, a stop, and what waits for a person."""

    waiting: list[dict[str, Any]] = []
    for kind, payload in events:
        if kind == "done":
            result.answer = str(payload or "")
        elif kind == "error":
            result.stop("provider_error", str(payload or "the model call failed"))
        elif kind == "terminal":
            reason = payload.get("terminal_reason") if isinstance(payload, dict) else ""
            result.stop("budget", "the turn reached its round ceiling" if reason == "budget_exhausted"
                        else "the turn stopped repeating the same action" if reason == "no_progress"
                        else f"the turn stopped ({reason or 'terminal'})")
        elif kind == "interrupt":
            waiting.extend(item for item in (payload or []) if isinstance(item, dict))
    return waiting


def _answer(agent: Any, tools: list[str], config: dict[str, Any], waiting: list[dict[str, Any]],
            result: RunResult) -> Iterable[tuple[str, Any]]:
    """Resume a turn that waits for a person: its question first (nobody answered), else its approvals (no)."""

    from row_bot import questions

    if any(questions.is_question(item) for item in waiting):
        result.unanswered_questions += 1
        return agent.resume_stream_agent(tools, config, False,
                                         resume_values=questions.resume_values(waiting, questions.timed_out()))
    for item in waiting:
        result.refused_actions.append({"tool": str(item.get("tool") or item.get("tool_name") or ""),
                                       "what": str(item.get("description") or item.get("summary") or "")[:300]})
    ids = [str(item["__interrupt_id"]) for item in waiting if item.get("__interrupt_id")]
    return agent.resume_stream_agent(tools, config, False, interrupt_ids=ids or None)


# ── Output ───────────────────────────────────────────────────────────────────


def _file_log(output_dir: Path | None) -> logging.Handler | None:
    if output_dir is None:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(output_dir / "row-bot.log", encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    return handler


def _usage(started: float) -> dict[str, Any]:
    from row_bot.providers.usage_ledger import usage_summary

    try:
        totals = usage_summary(since=started)
    except Exception:  # noqa: BLE001 - no ledger: no calls were recorded
        logger.warning("The run's model calls couldn't be read", exc_info=True)
        return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": None, "cost_usd": None}
    return {"calls": totals["calls"], "input_tokens": totals["input_tokens"],
            "output_tokens": totals["output_tokens"], "cache_read_tokens": totals["cache_read_tokens"],
            "cost_usd": totals["cost"], "unreported_calls": totals["unreported_calls"],
            "by_purpose": [{"purpose": row["purpose"], "calls": row["calls"], "input_tokens": row["input_tokens"],
                            "output_tokens": row["output_tokens"], "cost_usd": row["cost"]}
                           for row in totals["by_purpose"]]}


def _write_outputs(options: RunOptions, result: RunResult, thread_id: str, started: float,
                   throwaway: bool) -> None:
    """``trajectory.json`` (ATIF), ``answer.md`` and ``summary.json`` in the output folder."""

    from row_bot.headless import atif
    from row_bot.version import __version__

    folder = options.output_dir
    assert folder is not None
    usage = _usage(started)
    messages: list[Any] = []
    if thread_id:
        from row_bot import threads

        messages = threads.get_latest_checkpoint_messages(thread_id)
    finished = time.time()
    summary = {
        "schema": SUMMARY_SCHEMA,
        "row_bot_version": __version__,
        "status": result.status,
        "exit_code": result.exit_code,
        "reason": result.reason,
        "answer": result.answer,
        "model": result.model,
        "approval": options.approval,
        "workspace": str(options.workspace),
        "data_folder": "throwaway" if throwaway else str(options.data_dir),
        "settings": {"api_style": options.api_style if options.base_url else "", "context_window":
                     options.context_window, "max_output_tokens": options.max_output_tokens,
                     "reasoning_effort": options.reasoning_effort, "timeout": options.timeout,
                     "max_rounds": options.max_rounds},
        "turns": result.turns,
        "refused_actions": result.refused_actions,
        "unanswered_questions": result.unanswered_questions,
        "usage": usage,
        "started_at": _iso(started),
        "finished_at": _iso(finished),
        "seconds": round(finished - started, 3),
    }
    trajectory = atif.trajectory(
        messages, session_id=thread_id or "row-bot-run", agent_version=__version__, model_name=result.model,
        prompts=result.prompts, usage=usage,
        extra={"status": result.status, "exit_code": result.exit_code, "reason": result.reason,
               "approval": options.approval, "refused_actions": result.refused_actions},
    )
    _write(folder / "trajectory.json", json.dumps(trajectory, ensure_ascii=False, indent=1, default=str))
    _write(folder / "answer.md", result.answer)
    _write(folder / "summary.json", json.dumps(summary, ensure_ascii=False, indent=1, default=str))


def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))  # "\n" line endings on every system


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


__all__ = ["RunResult", "run"]
