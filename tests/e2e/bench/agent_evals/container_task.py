"""``python -m tests.e2e.bench.agent_evals.container_task``: one benchmark task inside its task container.

PawBench and Harbor (Terminal-Bench, SkillsBench) give every task its own container and grade the files it
leaves. Row-Bot is installed in the image (``pawbench/docker/Dockerfile.pawbench-rowbot``) and this runs it
there, the way their Codex, Claude Code and Hermes adapters run those agents:

    python -m tests.e2e.bench.agent_evals.container_task --benchmark pawbench \\
        --instruction-file /tmp/rowbot/instruction.txt --workspace /app/working/workspaces/default \\
        --model ollama:qwen3.6:27b-q4_K_M --out /tmp/rowbot

It starts itself again with the bench environment (``runtime.child_env``: a scratch data folder under
``--out``, test mode, the install count off, Learning unset), then sends the instruction as one message on
a Hands-off conversation with Row-Bot's own tools (:class:`~.product.ProductRuntime`), runs the one
follow-up the app would, and writes:

- ``<workspace>/sessions/rowbot-<id>.jsonl``: the transcript PawBench's grader reads (OpenClaw format);
- ``<out>/trajectory.jsonl``: Row-Bot's trajectory (every model call, tokens, cost);
- ``<out>/atif.json``: the same as ATIF, for Harbor;
- ``<out>/system_prompt.txt``: the system prompt the model read (PawBench prepends it to the transcript);
- ``<out>/result.json``: status and answer.

The local model is reached at ``OLLAMA_HOST`` (``http://host.docker.internal:11434`` from a container).
Exit codes as the other workers': 0 done, 75 a cap, 76 the provider's usage limit, 1 anything else.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

CHILD_FLAG = "--in-bench-env"


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--benchmark", required=True, choices=("pawbench", "terminal-bench", "skillsbench"))
    parser.add_argument("--instruction-file", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--model", required=True, help="provider:model (ollama:qwen3.8:27b)")
    parser.add_argument("--reasoning-effort", default="")
    parser.add_argument("--out", required=True)
    parser.add_argument("--openrouter-base-url", default="",
                        help="the host's bench relay (relay.py); OPENROUTER_API_KEY then holds the relay's token")
    parser.add_argument(CHILD_FLAG, action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if not args.in_bench_env:
        from ..runtime import child_env

        data_dir = out / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        env = child_env(data_dir, Path(args.workspace))
        # Learning, the install count and the keychain are settled by child_env; the GPU is the host's.
        return subprocess.run([sys.executable, "-m", "tests.e2e.bench.agent_evals.container_task",
                               *(argv if argv is not None else sys.argv[1:]), CHILD_FLAG], env=env,
                              check=False).returncode
    return run(args, out)


def run(args: argparse.Namespace, out: Path) -> int:
    from tests.e2e.live_eval.pricing import load_rates
    from tests.e2e.live_eval.report import redact

    from ..ledger import BudgetStop, CostLedger, LedgerCaps, UsageBook
    from ..runtime import BenchConfig, ProviderLimit
    from ..trajectory import Step, Trajectory, append_jsonl, to_atif
    from .pawbench import transcript
    from .product import ProductRuntime

    provider, _, model = args.model.partition(":")
    if args.openrouter_base_url:
        use_relay(args.openrouter_base_url)
    config = BenchConfig(provider=provider, model=model, tools=(), reasoning_effort=args.reasoning_effort)
    ledger = CostLedger(LedgerCaps.from_env(), UsageBook(out / "usage-book.json"), rates=load_rates(),
                        path=out / "ledger.jsonl")
    profile = os.environ.get("ROW_BOT_LIVE_EVAL_CODEX_PROFILE", "").strip()
    runtime = ProductRuntime(config, ledger, task_id=f"{args.benchmark}-task", benchmark=args.benchmark,
                             eval_profile=Path(profile) if profile else None)
    trajectory = Trajectory(task_id=runtime.task_id, benchmark=args.benchmark)
    workspace = Path(args.workspace)
    result: dict[str, object] = {"status": "error", "answer": ""}
    code = 1
    try:
        runtime.setup()
        trajectory.config = {**config.to_dict(), "kind": "container_task", "setup_notes": runtime.notes,
                             **({"local_model": runtime.local_model} if runtime.local_model else {})}
        thread_id = runtime.new_thread(f"{args.benchmark} task")
        steps = runtime.turn_with_followups(thread_id, Path(args.instruction_file).read_text(encoding="utf-8"),
                                            [workspace])
        trajectory.steps.extend(steps)
        trajectory.config["followups"] = runtime.followups
        result = {"status": steps[-1].status, "answer": steps[-1].answer, "turns": len(steps)}
        trajectory.status = "done" if steps[-1].status == "done" else steps[-1].status
        code = 0 if trajectory.status == "done" else 1
        write_transcript(thread_id, workspace, f"{provider}:{model}", transcript)
        write_system_prompt(out)
    except BudgetStop as exc:
        trajectory.status, trajectory.reason, code = "budget", str(exc)[:500], 75
    except ProviderLimit as exc:
        trajectory.status, trajectory.reason, code = "provider_limit", str(exc)[:500], 76
    except Exception as exc:  # noqa: BLE001 - recorded; the adapter reads the code
        trajectory.status, trajectory.reason = "error", f"{type(exc).__name__}: {exc}"[:500]
    finally:
        trajectory.finished_at = time.time()
        trajectory.steps.append(Step(kind="ledger", name="totals", metrics={"refused": ledger.refused[:5]}))
        runtime.close()
        append_jsonl(out / "trajectory.jsonl", trajectory, redact=redact)
        (out / "atif.json").write_text(redact(json.dumps(to_atif(trajectory), ensure_ascii=False)), encoding="utf-8")
        (out / "result.json").write_text(redact(json.dumps({**result, "exit": code, "reason": trajectory.reason})),
                                         encoding="utf-8")
    return code


def use_relay(base_url: str) -> None:
    """Point Row-Bot's OpenRouter provider at the host's relay (this process only): the container never holds
    the real key, and the relay pins the route and holds the spend cap."""
    from dataclasses import replace

    from row_bot.providers import catalog

    catalog.PROVIDER_DEFINITIONS["openrouter"] = replace(catalog.PROVIDER_DEFINITIONS["openrouter"],
                                                         base_url=base_url)


def write_transcript(thread_id: str, workspace: Path, model: str, transcript: object) -> Path:
    from row_bot import threads

    messages = transcript.from_langchain(threads.get_latest_checkpoint_messages(thread_id))  # type: ignore[attr-defined]
    folder = workspace / "sessions"
    folder.mkdir(parents=True, exist_ok=True)
    session = f"rowbot-{uuid.uuid4().hex[:12]}"
    path = folder / f"{session}.jsonl"
    path.write_text(transcript.session_jsonl(session, messages, model=model, cwd=str(workspace)),  # type: ignore[attr-defined]
                    encoding="utf-8")
    return path


def write_system_prompt(out: Path) -> None:
    try:
        from row_bot.agent import get_agent_system_prompt

        (out / "system_prompt.txt").write_text(str(get_agent_system_prompt()), encoding="utf-8")
    except Exception:  # noqa: BLE001 - the transcript stands without it
        pass


if __name__ == "__main__":
    sys.exit(main())
