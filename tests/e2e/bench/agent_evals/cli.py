"""The agent evals' runner (``scripts/bench_agents.py``): τ²-bench now; the container evals' plans.

    uv run python scripts/bench_agents.py tau2 plan --domain airline --split train
    ROW_BOT_BENCH=1 uv run python scripts/bench_agents.py tau2 run --domain airline --split train \\
        --trials 1 --model codex:gpt-5.2 --reasoning-effort high --user-model codex:gpt-5.2 --user-effort low
    uv run python scripts/bench_agents.py tau2 report --run-dir <run> [--published <their results.json>]

``plan`` and ``report`` import nothing from Row-Bot and make no network call. ``run`` needs
``ROW_BOT_BENCH=1`` and follows the memory harness's rules (``orchestrate``): paid APIs refused while the
paid cap is $0, the plan only through the eval profile (booked, with a throttle check first), the
stock local model only, a fresh scratch data folder, the STOP file. tau2-bench itself is a separate
checkout with its own virtual environment (``--tau2-dir`` or ``ROW_BOT_BENCH_TAU2_DIR``), never
installed into Row-Bot's.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .. import booking as booking_module
from .. import orchestrate
from ..ledger import API, LedgerCaps, billing_for, usage_book_path
from ..runtime import BenchConfig, child_env
from . import keys, llamacpp, openrouter
from .tau2 import scoring
from .variants import VARIANTS

TAU2_DIR_ENV = "ROW_BOT_BENCH_TAU2_DIR"
LANE_ENV = "ROW_BOT_BENCH_LANE"
DEFAULT_LANE = "lane-E"
TAU2_SIDE = Path(__file__).resolve().parent / "tau2" / "rowbot_tau2.py"
TAU2_VERSION = "1.0.1"
DOMAINS = ("airline", "retail")
SPLITS = ("train", "test", "base")
EXIT_TAU2_STOPPED = 3
#: Planning figures from the published GPT-5.2 runs: about 25 messages and 10 tool calls a conversation.
TURNS_PER_TASK, AGENT_CALLS_PER_TASK, USER_CALLS_PER_TASK = 8, 18, 8


def tau2_python(tau2_dir: Path) -> Path:
    for candidate in (tau2_dir / ".venv" / "Scripts" / "python.exe", tau2_dir / ".venv" / "bin" / "python"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"no virtual environment in {tau2_dir} (uv venv --python 3.12 .venv; "
                            "uv pip install --python .venv -e .)")


def tau2_dir(args: argparse.Namespace) -> Path:
    text = str(getattr(args, "tau2_dir", "") or os.environ.get(TAU2_DIR_ENV, "")).strip()
    if not text:
        raise FileNotFoundError(f"set {TAU2_DIR_ENV} or --tau2-dir to a tau2-bench v{TAU2_VERSION} checkout")
    return Path(text).resolve()


def split_tasks(root: Path, domain: str, split: str) -> list[str]:
    path = root / "data" / "tau2" / "domains" / domain / "split_tasks.json"
    return [str(item) for item in json.loads(path.read_text(encoding="utf-8"))[split]]


def tasks_needing_judge(root: Path, domain: str, tasks: list[str]) -> list[str]:
    """Tasks graded with tau2's NL-assertion judge (an LLM call): retail has 40 of 114."""
    path = root / "data" / "tau2" / "domains" / domain / "tasks.json"
    found = {str(item["id"]): item.get("evaluation_criteria") or {}
             for item in json.loads(path.read_text(encoding="utf-8"))}
    # tau2 calls the judge only when the task's reward counts NL assertions and it has some (evaluator.py).
    return [task for task in tasks
            if "NL_ASSERTION" in (found[task].get("reward_basis") or []) and found[task].get("nl_assertions")]


def chosen_tasks(root: Path, args: argparse.Namespace) -> list[str]:
    tasks = split_tasks(root, args.domain, args.split)
    if args.task_ids:
        wanted = [item.strip() for item in args.task_ids.split(",") if item.strip()]
        outside = sorted(set(wanted) - set(tasks))
        if outside:
            raise ValueError(f"tasks not in the {args.split} split: {outside}")
        tasks = [task for task in tasks if task in wanted]
    elif args.limit:
        tasks = tasks[:args.limit]
    return tasks


def plan(args: argparse.Namespace) -> int:
    root = tau2_dir(args)
    tasks = chosen_tasks(root, args)
    judged = tasks_needing_judge(root, args.domain, tasks)
    conversations = len(tasks) * args.trials
    print(f"tau2-bench {TAU2_VERSION} {args.domain} / {args.split}: {len(tasks)} task(s) x {args.trials} trial(s) = "
          f"{conversations} conversation(s)")
    print(f"about {conversations * AGENT_CALLS_PER_TASK} agent calls and {conversations * USER_CALLS_PER_TASK} "
          "user-simulator calls (rough; the published GPT-5.2 runs average about that)")
    print(f"{len(judged)} task(s) graded by tau2's NL-assertion judge (gpt-4.1 when matched: a paid API): "
          f"{', '.join(judged) or 'none'}")
    return 0


def tool_schemas(root: Path, domain: str) -> list[dict[str, Any]]:
    from .worker import runner_env

    completed = subprocess.run(  # noqa: S603 - the tau2 side we ship, under tau2's own interpreter
        [str(tau2_python(root)), str(TAU2_SIDE), "--dump-tools", domain], cwd=root, capture_output=True,
        env=runner_env(os.environ, "0:none"), timeout=300, check=False)
    if completed.returncode:
        raise RuntimeError(f"tau2 could not list {domain}'s tools: {completed.stderr.decode(errors='replace')[-800:]}")
    return list(json.loads(completed.stdout.decode("utf-8").strip().splitlines()[-1])["tools"])


def run_worker(run_dir: Path, spec: dict[str, Any], extra_env: dict[str, str] | None = None) -> int:
    stem = spec["task_id"]
    data_dir, workspace = run_dir / "data" / stem, run_dir / "workspace" / stem
    for folder in (data_dir, workspace, run_dir / "specs", run_dir / "logs"):
        folder.mkdir(parents=True, exist_ok=True)
    spec = {**spec, "run_dir": str(run_dir), "log": str(run_dir / "logs" / f"{stem}.log")}
    spec_path = run_dir / "specs" / f"{stem}.json"
    spec_path.write_text(json.dumps(spec, indent=1, default=str), encoding="utf-8")
    completed = subprocess.run([sys.executable, "-m", "tests.e2e.bench.agent_evals.worker", "--spec", str(spec_path)],
                               cwd=orchestrate.REPO_ROOT, env={**child_env(data_dir, workspace), **(extra_env or {})},
                               check=False)
    return completed.returncode


def run(args: argparse.Namespace) -> int:
    if not orchestrate.enabled():
        print(f"set {orchestrate.ENABLE_ENV}=1 to run (it uses the models you name)")
        return 2
    caps = LedgerCaps.from_env()
    local = bool(args.local_gguf)
    if local:  # the local llama.cpp lane: Row-Bot's own model is the bench's llama-server (llamacpp.py)
        if not args.local_bin or not args.local_sha256:
            print("not running: the local lane needs --local-bin, --local-gguf and --local-sha256")
            return 2
        args.model = f"{llamacpp.PROVIDER_ID}:{args.local_alias}"
    provider, model = orchestrate.split_ref(args.model)
    for ref in filter(None, (None if local else args.model, args.user_model, args.nl_judge)):
        ref_provider, ref_model = orchestrate.split_ref(ref)
        problem = orchestrate.lane_problem(ref_provider, caps, model=ref_model)
        if problem:
            print(f"not running: {ref}: {problem}")
            return 2
    if local and not str(os.environ.get(orchestrate.BOOKING_ENV, "")).strip():
        print(f"not running: set {orchestrate.BOOKING_ENV}: the graphics card is booked")
        return 2
    refs = [orchestrate.split_ref(ref) for ref in filter(None, (args.model, args.user_model, args.nl_judge))]
    paid = [provider_id for provider_id, _ in refs
            if provider_id != llamacpp.PROVIDER_ID and billing_for(provider_id) == API]
    direct = sorted({provider_id for provider_id in paid if provider_id != "openrouter"})
    if direct:
        print(f"not running: paid models go through OpenRouter (one key, one balance), not {direct}; "
              "name them openrouter:<model>")
        return 2
    try:
        pins = openrouter_pins([model for provider_id, model in refs if provider_id == "openrouter"],
                               args.openrouter_quant, args.openrouter_host)
    except openrouter.RoutingError as exc:
        print(f"not running: {exc}")
        return 2
    try:
        key_names = keys.names_for(paid)
    except keys.KeysError as exc:
        print(f"not running: {exc}")
        return 2
    why = keys.problem(key_names, keys.keys_file(), keys.profile_folder())
    if why:
        print(f"not running: {why}")
        return 2
    root = tau2_dir(args)
    tasks = chosen_tasks(root, args)
    judged = tasks_needing_judge(root, args.domain, tasks)
    if judged and not args.nl_judge:
        print(f"not running: {len(judged)} task(s) need tau2's NL-assertion judge ({', '.join(judged[:8])}...); "
              "pass --nl-judge (openai:gpt-4.1-2025-04-14 is the matched judge, a paid API) or choose other tasks")
        return 2
    # Arms started side by side in the same second must never share a folder: the variant and a short random
    # suffix are in the name.
    label = f"tau2-{args.domain}-{args.split}{'-' + args.variant if args.variant else ''}-{os.urandom(2).hex()}"
    run_dir = Path(args.run_dir) if args.run_dir else orchestrate.new_run_dir(label)
    run_dir.mkdir(parents=True, exist_ok=True)
    if (run_dir / "tau2" / "results.json").exists():
        print(f"not running: {run_dir} already has tau2 results (start a new run folder)")
        return 2
    print(f"run folder: {run_dir}")
    config = BenchConfig(provider=provider, model=model, tools=(), reasoning_effort=args.reasoning_effort or "")
    record = {"run": {"benchmark": "tau2-bench", "tau2_version": TAU2_VERSION, "domain": args.domain,
                      "split": args.split, "tasks": tasks, "split_tasks": split_tasks(root, args.domain, args.split),
                      "trials": args.trials, "seed": args.seed,
                      "user_model": args.user_model, "user_effort": args.user_effort,
                      "nl_judge": args.nl_judge or "", "git_commit": orchestrate._git_commit(),  # noqa: SLF001
                      "label": "self-run", "matched": matched_label(args), "followups": args.followups,
                      "variant": args.variant or "as shipped"}}
    (run_dir / "run.json").write_text(json.dumps({**record["run"], "config": config.to_dict(), "caps": asdict(caps)},
                                                 indent=1), encoding="utf-8")
    argv = [str(tau2_python(root)), str(TAU2_SIDE), "run", "--domain", args.domain, "--split", args.split,
            "--num-trials", str(args.trials), "--seed", str(args.seed), "--max-steps", str(args.max_steps),
            "--user-model", args.user_model, "--user-effort", args.user_effort,
            "--save-to", str(run_dir / "tau2")]
    if args.task_ids or args.limit:
        argv += ["--task-ids", ",".join(tasks)]
    if args.nl_judge:
        argv += ["--nl-judge", args.nl_judge, "--nl-judge-effort", args.nl_judge_effort]
    spec = {"task_id": f"tau2-{args.domain}-{args.split}", "benchmark": "tau2-bench", "config": asdict(config),
            "caps": asdict(caps), "usage_book": str(usage_book_path()), "external_tools": tool_schemas(root, args.domain),
            "runner": {"argv": argv, "cwd": str(root)}, "record": record, "keys": key_names,
            "followups": args.followups, "variant": args.variant,
            "openrouter_pins": {model: pin for model, (pin, _) in pins.items()},
            "rates_file": str(openrouter.write_rates(run_dir / "openrouter-rates.json",
                                                     {model: endpoint for model, (_, endpoint) in pins.items()}))
            if pins else ""}
    kind = booking_module.GPU if local else orchestrate.booking_kind(provider)
    what = f"tau2 {args.domain} {args.split} ({len(tasks)} tasks x {args.trials})"
    book = booking_module.BookingFile(Path(os.environ[orchestrate.BOOKING_ENV]), lane=lane()) if kind else None
    server = None
    if local:
        found = llamacpp.sha256_of(Path(args.local_gguf))  # read-only, before anything loads
        if found != args.local_sha256.lower():
            print(f"not running: {args.local_gguf} does not have the expected SHA-256")
            return 2
        record["run"]["local_model"] = {"gguf": Path(args.local_gguf).name, "sha256": found,
                                        "server": Path(args.local_bin).name, "context": args.local_ctx}
        (run_dir / "run.json").write_text(json.dumps({**record["run"], "config": config.to_dict(),
                                                      "caps": asdict(caps)}, indent=1), encoding="utf-8")
    if book is not None:
        if not book.wait_for(kind, stop=lambda: (run_dir / "STOP").exists()):
            return 77
        book.start(kind, what, f"{max(10, len(tasks) * args.trials * 3)} min, "
                               f"{'11' if local else str(orchestrate.GPU_GB)} GB")
    try:
        extra: dict[str, str] = {}
        if local:
            server = llamacpp.LlamaServer(Path(args.local_bin), Path(args.local_gguf), run_dir / "llama-server",
                                          context=args.local_ctx, alias=args.local_alias)
            spec["local_endpoint"] = {"port": server.port, "context": args.local_ctx, "pid": server.pid}
            extra[llamacpp.KEY_ENV] = server.key  # the worker's environment only, never the spec file
        code = throttle_check(run_dir, provider, model, caps)
        if not code:
            code = run_worker(run_dir, spec, extra)
    finally:
        if server is not None:
            server.stop()  # by its exact PID
        if book is not None:
            if kind == booking_module.GPU and not local:
                orchestrate.unload_local_model(model)
            book.end(kind, what)
    if code == orchestrate.EXIT_PROVIDER_LIMIT:
        print("stopped: the provider's usage limit (see runner.json); finished conversations are kept in tau2/")
    if (run_dir / "tau2" / "results.json").exists():
        print(write_report(run_dir, published=Path(args.published) if args.published else None))
    return code


def openrouter_pins(models: list[str], quant_text: str, host_text: str = "", *,
                    fetch: Callable[[str], list[dict[str, Any]]] | None = None) -> dict[str, tuple[dict, dict]]:
    """Each OpenRouter model's provider pin and the endpoint it lands on: the first-party host, or the host the
    published row used (``--openrouter-host model=Novita``), no fallbacks, at the precision
    ``--openrouter-quant model=bf16`` names. RoutingError when a model can't be pinned."""
    quants = dict(item.split("=", 1) for item in quant_text.split(",") if "=" in item)
    hosts = dict(item.split("=", 1) for item in host_text.split(",") if "=" in item)
    found = {}
    for model in dict.fromkeys(models):
        endpoints = (fetch or openrouter.fetch_endpoints)(model)
        quant, host = quants.get(model, ""), hosts.get(model, "")
        endpoint = openrouter.check_endpoints(model, endpoints, quantization=quant, host=host)
        found[model] = (openrouter.pin_for(model, quantization=quant, host=host), endpoint)
    return found


def lane() -> str:
    """This lane's name in the booking file (``ROW_BOT_BENCH_LANE``): never the memory lane's ``lane-bench``,
    so an END line here can never close that lane's booking."""
    name = os.environ.get(LANE_ENV, "").strip() or DEFAULT_LANE
    if name == booking_module.LANE:
        raise ValueError(f"{LANE_ENV} must not be {booking_module.LANE} (the memory lane's name)")
    return name


def throttle_check(run_dir: Path, provider: str, model: str, caps: LedgerCaps) -> int:
    """The plan's throttle check (one tiny call), inside this run's own booking (or a shared one)."""
    from ..ledger import PLAN, billing_for

    if billing_for(provider) != PLAN:
        return 0
    spec = {"kind": "probe", "task_id": f"probe-{run_dir.name}", "benchmark": "probe",
            "config": asdict(BenchConfig(provider=provider, model=model, tools=())), "caps": asdict(caps),
            "usage_book": str(usage_book_path())}
    code = orchestrate.run_worker(run_dir, spec)
    if code == orchestrate.EXIT_PROVIDER_LIMIT:
        orchestrate.limit_report(run_dir)
    elif code:
        print(f"the throttle check failed (exit {code}); see {run_dir / 'logs'}")
    return code


def matched_label(args: argparse.Namespace) -> str:
    """Whether this run's setup is the published GPT-5.2-high row's: the same model and efforts for the agent
    and the user simulator, tau2's own NL judge (gpt-4.1) for retail, 4 trials, seed 300, 200 steps; every
    model through OpenRouter pinned to OpenAI's own endpoint (the ChatGPT plan no longer serves gpt-5.2)."""
    gpt52 = "openrouter:openai/gpt-5.2"
    same = (args.model == gpt52 and args.reasoning_effort == "high" and args.user_model == gpt52
            and args.user_effort == "low" and args.trials == 4 and args.seed == 300 and args.max_steps == 200
            and (args.domain == "airline" or args.nl_judge == "openrouter:openai/gpt-4.1"))
    return "matched to Sierra's GPT-5.2 high row (OpenRouter, OpenAI host)" if same else "diagnostic, not matched"


def write_report(run_dir: Path, *, published: Path | None = None) -> str:
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    results = scoring.load_results(run_dir / "tau2" / "results.json")
    # tau2 leaves infrastructure errors out of its metrics; so does this report, and it counts them.
    ours = scoring.trial_rewards(results, exclude=scoring.NOT_SCORED)
    infra = sum(len(values) for values in scoring.trial_rewards(results).values()) - sum(map(len, ours.values()))
    theirs = scoring.trial_rewards(scoring.load_results(published), exclude=scoring.NOT_SCORED) if published else {}
    comparison = scoring.Comparison(tasks=list(meta["tasks"]), ours=ours, theirs=theirs,
                                    k_max=max(1, int(meta["trials"])), split_tasks=list(meta.get("split_tasks") or []))
    text = scoring.render(comparison, title=f"tau2-bench {meta['tau2_version']} {meta['domain']} / {meta['split']}: "
                          f"Row-Bot ({meta['config']['provider']}:{meta['config']['model']}, reasoning "
                          f"{meta['config']['reasoning_effort']}), self-run, {meta['matched']}",
                          ours_label="Row-Bot (self-run)", theirs_label=f"published ({published.name})"
                          if published else "published (none given)")
    if infra:
        text += f"\n{infra} conversation(s) ended in an infrastructure error and are not scored.\n"
    (run_dir / "report.md").write_text(text, encoding="utf-8")
    (run_dir / "scores.json").write_text(json.dumps({**comparison.summary(), "not_scored": infra}, indent=1),
                                         encoding="utf-8")
    return text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench_agents.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="eval", required=True)
    tau2 = sub.add_parser("tau2", help="tau2-bench (retail, airline) with Row-Bot as the agent")
    tau2_sub = tau2.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        item = tau2_sub.add_parser(name)
        item.add_argument("--tau2-dir", default="")
        item.add_argument("--domain", required=True, choices=DOMAINS)
        item.add_argument("--split", default="train", choices=SPLITS)
        item.add_argument("--task-ids", default="")
        item.add_argument("--limit", type=int, default=0, help="the first N tasks of the split")
        item.add_argument("--trials", type=int, default=1)
    run_parser = tau2_sub.choices["run"]
    run_parser.add_argument("--model", required=True, help="Row-Bot's model, provider:model (codex:gpt-5.2)")
    run_parser.add_argument("--reasoning-effort", default="")
    run_parser.add_argument("--user-model", required=True, help="the user simulator, provider:model")
    run_parser.add_argument("--user-effort", default="low")
    run_parser.add_argument("--nl-judge", default="", help="retail's NL-assertion judge, provider:model")
    run_parser.add_argument("--nl-judge-effort", default="")
    run_parser.add_argument("--seed", type=int, default=300)
    run_parser.add_argument("--max-steps", type=int, default=200)
    run_parser.add_argument("--followups", default="none", choices=("none", "app"),
                            help="app: a reply that stops on its next step gets the app's one carry-on turn")
    run_parser.add_argument("--openrouter-quant", default="",
                            help="model=precision pins for OpenRouter models (recursive/flint-v1.0:free=bf16)")
    run_parser.add_argument("--openrouter-host", default="",
                            help="model=Host where the published row ran on another host than the model's maker "
                                 "(the Harness Leaderboard's DeepSeek-V4.1-Flash: low on Novita, high on DeepInfra)")
    run_parser.add_argument("--local-bin", default="", help="the local lane: a llama.cpp build's bin folder (read-only)")
    run_parser.add_argument("--local-gguf", default="", help="the local lane: the GGUF (read-only, hash-checked)")
    run_parser.add_argument("--local-sha256", default="")
    run_parser.add_argument("--local-ctx", type=int, default=65_536)
    run_parser.add_argument("--local-alias", default="qwen3.5-9b-q8_0")
    run_parser.add_argument("--variant", default="", choices=VARIANTS,
                            help="a bench-only arm of a paired A/B (agent_evals.variants); '' is Row-Bot as shipped")
    run_parser.add_argument("--run-dir", default="")
    run_parser.add_argument("--published", default="", help="a published run's results.json (gzip or plain)")
    report = tau2_sub.add_parser("report")
    report.add_argument("--run-dir", required=True)
    report.add_argument("--published", default="")
    key_parser = sub.add_parser("keys", help="whether each paid provider's key is found (never a value)")
    key_parser.add_argument("--providers", default="openrouter")
    relay_parser = sub.add_parser("relay", help="run a container benchmark's command with the host's OpenRouter "
                                                "relay (the key stays on the host): relay ... -- harbor run ...")
    relay_parser.add_argument("--model", action="append", required=True, help="an OpenRouter slug to allow")
    relay_parser.add_argument("--openrouter-quant", default="")
    relay_parser.add_argument("--openrouter-host", default="")
    relay_parser.add_argument("--max-usd", type=float, default=0.0, help="the relay's spend cap (0: free models only)")
    relay_parser.add_argument("--per-minute", type=int, default=18, help="pace under OpenRouter's free-tier limit")
    relay_parser.add_argument("--per-day", type=int, default=950)
    relay_parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def relay(args: argparse.Namespace) -> int:
    """Start the relay, run *command* with ``ROW_BOT_BENCH_RELAY_URL``/``_TOKEN`` (never the key), stop the relay."""
    from . import relay as relay_module

    if not orchestrate.enabled():
        print(f"set {orchestrate.ENABLE_ENV}=1 to run (it spends from the OpenRouter balance up to --max-usd)")
        return 2
    command = [item for item in args.command if item != "--"]
    if not command:
        print("not running: give the benchmark's command after --")
        return 2
    try:
        pins = openrouter_pins(list(args.model), args.openrouter_quant, args.openrouter_host)
    except openrouter.RoutingError as exc:
        print(f"not running: {exc}")
        return 2
    paid = [model for model, (_, endpoint) in pins.items() if any(
        float((endpoint.get("pricing") or {}).get(key) or 0) for key in ("prompt", "completion"))]
    if paid and args.max_usd <= 0:
        print(f"not running: {paid} cost money and --max-usd is 0")
        return 2
    held: dict[str, str] = {}
    try:
        keys.load_into(held, ["OPENROUTER_API_KEY"], keys.keys_file(), keys.profile_folder())
    except keys.KeysError as exc:
        print(f"not running: {exc}")
        return 2
    policy = relay_module.RelayPolicy(pins={model: pin for model, (pin, _) in pins.items()}, max_usd=args.max_usd,
                                      per_minute=args.per_minute, per_day=args.per_day)
    server = relay_module.RelayServer(policy, held.pop("OPENROUTER_API_KEY"))
    env = {**os.environ, "ROW_BOT_BENCH_RELAY_URL": server.base_url("host.docker.internal"),
           "ROW_BOT_BENCH_RELAY_TOKEN": server.token}
    try:
        code = subprocess.run(command, env=env, check=False).returncode  # noqa: S603 - the owner's own command
    finally:
        server.close()
    print(f"relay: {policy.requests} request(s), ${policy.spent_usd:.4f} spent, {len(policy.refused)} refused")
    return code


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.eval == "relay":
        return relay(args)
    if args.eval == "keys":
        names = keys.names_for(item for item in args.providers.split(",") if item.strip())
        for name, state in keys.status(names, keys.keys_file(), keys.profile_folder()).items():
            print(f"{name}: {state}")
        return 0
    if args.command == "plan":
        return plan(args)
    if args.command == "run":
        return run(args)
    print(write_report(Path(args.run_dir), published=Path(args.published) if args.published else None))
    return 0


