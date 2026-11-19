"""``python -m tests.e2e.bench.agent_evals.worker --spec <file>``: Row-Bot as a benchmark's agent.

Started by ``scripts/bench_agents.py`` with :func:`~tests.e2e.bench.runtime.child_env` (a scratch
data folder, test mode, the install count off, Learning unset). It pins the model, the approval
level and the benchmark's own tools (the ``bench_external`` group, from the spec's schemas), opens
the bridge on a loopback port, starts the benchmark's runner (in the benchmark's own virtual
environment) with the bridge's address, and serves it until the runner is done. Each session is one
trajectory in ``<run>/trajectories.jsonl``; every model call is in the ledger.

Exit codes as the memory worker's: 0 done, 75 a cap stopped it, 76 the provider's usage limit,
77 the stop file; 1 anything else (the runner's own exit code is in the run's ``runner.json``).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Mapping

EXIT_BUDGET, EXIT_PROVIDER_LIMIT, EXIT_STOPPED = 75, 76, 77
#: Names whose values never reach the benchmark's runner (it needs no provider key: Row-Bot makes every call).
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")
#: A closed loopback port: an HTTP client in the runner that tries a provider fails at once.
DEAD_PROXY = "http://127.0.0.1:9"


def runner_env(base: Mapping[str, str], bridge_address: str, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The benchmark runner's environment: no secrets, no Row-Bot data folder, the bridge's address, litellm's
    local cost map, and every HTTP proxy at a closed port (loopback stays direct)."""
    from .wire import TOKEN_ENV

    env = {key: value for key, value in base.items()
           if not any(marker in key.upper() for marker in _SECRET_MARKERS) and not key.startswith("ROW_BOT_")}
    env.update({TOKEN_ENV: bridge_address, "LITELLM_LOCAL_MODEL_COST_MAP": "True", "LITELLM_TELEMETRY": "False",
                "HTTP_PROXY": DEAD_PROXY, "HTTPS_PROXY": DEAD_PROXY, "http_proxy": DEAD_PROXY,
                "https_proxy": DEAD_PROXY, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
                "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    env.update(dict(extra or {}))
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    args = parser.parse_args(argv)
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    run_dir = Path(spec["run_dir"])

    from ..runtime import check_environment

    check_environment()
    from . import keys

    # The paid keys this run needs, from the owner's keys file into this process only (never the spec).
    key_sources = keys.load_into(os.environ, spec.get("keys") or [], keys.keys_file(), keys.profile_folder())
    from ..worker import _log_to

    if spec.get("log"):
        _log_to(spec["log"])
    from tests.e2e.live_eval.pricing import load_rates
    from tests.e2e.live_eval.report import redact

    from ..ledger import CostLedger, LedgerCaps, UsageBook
    from ..runtime import BenchConfig, BenchRuntime
    from ..trajectory import Step, Trajectory, append_jsonl
    from . import bridge
    from .host import RuntimeHost

    caps = LedgerCaps(**spec["caps"])
    ledger = CostLedger(caps, UsageBook(Path(spec["usage_book"])), rates=load_rates(), path=run_dir / "ledger.jsonl")
    config = BenchConfig(**{**spec["config"], "tools": (bridge.GROUP,),
                            "blocked_hosts": tuple(spec["config"].get("blocked_hosts") or ()), "canaries": ()})
    profile = os.environ.get("ROW_BOT_LIVE_EVAL_CODEX_PROFILE", "").strip()
    runtime = BenchRuntime(config, ledger, task_id=spec["task_id"], eval_profile=Path(profile) if profile else None)
    summary = Trajectory(task_id=spec["task_id"], benchmark=spec["benchmark"])
    router = bridge.ToolRouter()
    specs = list(spec["external_tools"])
    host = None
    code, runner_code = 0, None
    try:
        from row_bot.tools import registry

        registry.register(bridge.external_tool_group(specs, router))
        runtime.setup()
        summary.config = {**config.to_dict(), "kind": "bridge", "caps": asdict(caps),
                          "network": runtime.policy.to_dict(), "setup_notes": runtime.notes,
                          **({"local_model": runtime.local_model} if runtime.local_model else {}),
                          "paid_keys": key_sources, **spec.get("record", {})}
        host = RuntimeHost(runtime, spec["benchmark"], run_dir / "trajectories.jsonl", redact=redact,
                           record=spec.get("record", {}))
        server = bridge.BridgeServer(host, router, bridge.tool_names(specs))
        runner = spec["runner"]
        with open(run_dir / "logs" / f"{spec['task_id']}-runner.log", "ab") as log:
            process = subprocess.Popen(  # noqa: S603 - the benchmark's own runner, from the spec we wrote
                [str(item) for item in runner["argv"]], cwd=runner.get("cwd") or None,
                env=runner_env(os.environ, server.address, runner.get("env")), stdout=log, stderr=subprocess.STDOUT)
            try:
                server.serve(alive=lambda: process.poll() is None,
                             stop=lambda: (run_dir / "STOP").exists())
            finally:
                try:
                    runner_code = process.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    process.kill()
                    runner_code = process.wait()
        summary.steps.append(Step(kind="bridge", name="counts", metrics={**server.counts, "runner_exit": runner_code}))
        stopped = server.stopped
        if stopped is not None:
            code = {"limit": EXIT_PROVIDER_LIMIT, "budget": EXIT_BUDGET, "stop": EXIT_STOPPED}.get(stopped.kind, 1)
            summary.status, summary.reason = {"limit": "provider_limit", "budget": "budget",
                                              "stop": "stopped"}.get(stopped.kind, "error"), str(stopped)[:500]
        elif runner_code:
            code, summary.status, summary.reason = 1, "error", f"the benchmark runner exited {runner_code}"
        else:
            summary.status = "done"
    except Exception as exc:  # noqa: BLE001 - recorded; the orchestrator reads the code
        summary.status, summary.reason, code = "error", f"{type(exc).__name__}: {exc}"[:500], 1
        logging.getLogger("row_bot.bench").exception("bench agent-eval worker failed")
    finally:
        if host is not None:
            host.flush()
        summary.finished_at = time.time()
        summary.config["ledger_refusals"] = len(ledger.refused)
        if runtime.sign_in is not None:
            summary.config["credentials_write_backs"] = runtime.sign_in.write_backs
        runtime.close()
        (run_dir / "runner.json").write_text(json.dumps({"runner_exit": runner_code, "worker_exit": code,
                                                         "status": summary.status, "reason": summary.reason},
                                                        indent=1), encoding="utf-8")
        append_jsonl(run_dir / "runs.jsonl", summary, redact=redact)
    return code


if __name__ == "__main__":
    sys.exit(main())
