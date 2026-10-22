"""Plan, run and report a live eval: the code behind ``scripts/live_eval.py`` and ``tests/e2e``.

Planning (``plan``, ``run --dry-run``) imports nothing from Row-Bot and makes no
network call. A run starts one worker process per provider (``worker.py``),
each with its own fresh data folder inside the run folder, and stops before a
cap would be passed. Results land in ``.tmp/live-eval/<timestamp>/`` (git
ignores ``.tmp/``) unless ``ROW_BOT_LIVE_EVAL_OUT`` names another folder.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import eval_profile
from . import providers as provider_registry
from . import variants
from .budget import BudgetConfigError, BudgetGuard, Caps, Spend
from .checks import CHECKS
from .ledger import everyday_data_folder, summarize_folder
from .manual import MANUAL_CHECKS
from .pricing import RATES_ENV, load_rates
from .runner_limits import FREE_PROVIDERS
from .report import (
    BUDGET,
    ERROR,
    PASS,
    SKIPPED,
    ProbeRecord,
    Report,
    TaskRecord,
    diff_reports,
    redact,
    render_diff_markdown,
    write_report,
)
from .tasks import Context, estimate, select_tasks

REPO_ROOT = Path(__file__).resolve().parents[3]
ENABLE_ENV = "ROW_BOT_LIVE_EVAL"
OUT_ENV = "ROW_BOT_LIVE_EVAL_OUT"
TIMEOUT_ENV = "ROW_BOT_LIVE_EVAL_TIMEOUT_MIN"
DEFAULT_TIMEOUT_MIN = 60
PROBES_FILE = "probes.jsonl"
_CHILD_ENV_DROP = ("PYTEST_XDIST_WORKER", "PYTEST_CURRENT_TEST", "PYTEST_XDIST_TESTRUNUID", "ROW_BOT_TEST_SHARD")


def enabled(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return str(source.get(ENABLE_ENV, "")).strip().lower() in {"1", "true", "yes"}


def new_run_dir(env: Mapping[str, str] | None = None, *, suffix: str = "") -> Path:
    source = os.environ if env is None else env
    explicit = str(source.get(OUT_ENV, "") or "").strip()
    folder = Path(explicit) if explicit else REPO_ROOT / ".tmp" / "live-eval" / (time.strftime("%Y%m%d-%H%M%S") + suffix)
    if everyday_data_folder(folder):
        raise ValueError("the run folder may not be inside the everyday Row-Bot data folder")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# ── Plan (no network, no Row-Bot) ──────────────────────────────────────────


def build_plan(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = dict(os.environ if env is None else env)
    caps = Caps.from_env(source)
    rates_path = str(source.get(RATES_ENV, "") or "").strip()
    rates = load_rates(rates_path) if rates_path else {}
    variant = variants.parse(source.get(variants.VARIANT_ENV, ""))
    selection = provider_registry.select(source)
    context = Context.from_env(env=source)
    rows = []
    total_tokens = 0
    total_usd: float | None = 0.0
    for state in selection.chosen:
        pid = state.target.provider_id
        tasks = select_tasks(pid, source)
        planned_model = eval_profile.PREFERRED_MODELS[-1] if pid == "codex" and state.model == "auto" else state.model
        estimates = [(task.task_id, *estimate(task, context, rates, pid, planned_model)) for task in tasks]
        tokens = sum(item[1] for item in estimates)
        usd_values = [item[2] for item in estimates]
        usd = None if any(value is None for value in usd_values) else sum(usd_values)  # type: ignore[arg-type]
        free = pid in FREE_PROVIDERS
        if not free:
            total_tokens += tokens
            total_usd = None if total_usd is None or usd is None else total_usd + usd
        row = {
            "provider": pid,
            "model": "gpt-6.1-sol or gpt-5.6-sol (live catalog)" if pid == "codex" and state.model == "auto"
            else state.model,
            "status": "run",
            "reason": state.reason,
            "tasks": [{"task": task_id, "tokens": tok, "usd": usd_item} for task_id, tok, usd_item in estimates],
            "tokens": tokens,
            "usd": None if free else usd,
            "priced_by": "free (local)" if free else (
                "provider" if state.target.reports_cost else ("rates" if usd is not None else "unpriced")),
        }
        if pid == "codex":
            row["cap_tokens"] = eval_profile.max_tokens(source)
            row["fits_cap"] = tokens <= row["cap_tokens"]
        rows.append(row)
    skipped = [{"provider": s.target.provider_id, "status": "skip", "reason": s.reason} for s in selection.skipped]
    guard = BudgetGuard(caps)
    over = guard.over_by(total_tokens, total_usd)
    chosen_ids = {row["provider"] for row in rows}
    if str(source.get(provider_registry.DEEPSEEK_KEY_ENV, "") or "").strip():
        chosen_ids.add("deepseek")  # B1's DeepSeek check only: an in-memory custom endpoint
    checks = [
        {"check": check.check_id, "source": check.source, "providers": sorted(set(check.providers) & chosen_ids)
         if check.providers else sorted(chosen_ids), "description": check.description}
        for check in CHECKS
    ]
    return {
        "caps": {"max_usd": caps.max_usd, "max_tokens": caps.max_tokens, "describe": caps.describe()},
        "variant": variant.describe(),
        "rates": rates_path,
        "context": asdict(context),
        "providers": rows,
        "skipped": skipped,
        "oauth_only": list(provider_registry.OAUTH_ONLY),
        "total_tokens": total_tokens,
        "total_usd": total_usd,
        "fits": not over,
        "over": over,
        "checks": checks,
        "manual_checks": len(MANUAL_CHECKS),
    }


def render_plan(plan: Mapping[str, Any]) -> str:
    lines = [
        "Row-Bot live eval plan (dry run: no network call, nothing was run)",
        f"Caps: {plan['caps']['describe']}",
        f"Variant: {plan['variant']}",
        f"Rates file: {plan['rates'] or 'none (only OpenRouter calls are priced; others are unpriced)'}",
        "",
        "Providers:",
    ]
    for row in plan["providers"]:
        usd = row["priced_by"] if row["usd"] is None else f"<= ${row['usd']:.2f}"
        note = f"  ({row['reason']})" if row["reason"] else ""
        if "cap_tokens" in row:
            note += (f"  [lane cap {row['cap_tokens']:,} tokens: "
                     + ("fits" if row["fits_cap"] else "the run stops before passing it") + "]")
        lines.append(f"  RUN   {row['provider']:<13} {row['model']:<32} {len(row['tasks']):>2} tasks  "
                     f"~{row['tokens']:>10,} tokens  {usd}{note}")
    for row in plan["skipped"]:
        lines.append(f"  SKIP  {row['provider']:<13} {row['reason']}")
    lines.append(f"  OAuth-only (manual checklist): {', '.join(plan['oauth_only'])}")
    if plan["providers"]:
        lines += ["", "Tasks x providers (estimated tokens, an upper bound counting cache reads as full input):"]
        names = [row["provider"] for row in plan["providers"]]
        lines.append(f"  {'task':<26}" + "".join(f"{name[:12]:>14}" for name in names))
        all_tasks: list[str] = []
        for row in plan["providers"]:
            for item in row["tasks"]:
                if item["task"] not in all_tasks:
                    all_tasks.append(item["task"])
        for task_id in all_tasks:
            cells = []
            for row in plan["providers"]:
                found = next((item for item in row["tasks"] if item["task"] == task_id), None)
                cells.append(f"{found['tokens']:>14,}" if found else f"{'-':>14}")
            lines.append(f"  {task_id:<26}" + "".join(cells))
    total_usd = "unpriced" if plan["total_usd"] is None else f"<= ${plan['total_usd']:.2f}"
    lines += [
        "",
        f"Total: ~{plan['total_tokens']:,} tokens, {total_usd}.",
        "Fits the caps: " + ("yes" if plan["fits"] else f"no ({plan['over']}); the run stops before passing a cap"),
        "",
        "Verification checks (tests/e2e/test_live_verification.py):",
    ]
    for check in plan["checks"]:
        lines.append(f"  {check['check']:<30} {check['source']:<5} {', '.join(check['providers']) or '(no provider)'}")
    lines += ["", f"Manual checks not automated: {plan['manual_checks']} (scripts/live_eval.py manual)"]
    return "\n".join(lines)


# ── Run ────────────────────────────────────────────────────────────────────


def _ollama_ready(model: str) -> str:
    """'' when OLLAMA_HOST answers and lists *model*; otherwise why not."""
    import httpx

    host = os.environ.get("OLLAMA_HOST", "") or "127.0.0.1:11434"
    base = host if host.startswith("http") else f"http://{host.replace('0.0.0.0', '127.0.0.1')}"
    try:
        tags = httpx.get(f"{base}/api/tags", timeout=5).json().get("models") or []
    except Exception as exc:
        return f"OLLAMA_HOST {base} did not answer ({type(exc).__name__})"
    names = {str(item.get("name") or "") for item in tags} | {str(item.get("model") or "") for item in tags}
    if model not in names and f"{model}:latest" not in names:
        return f"model {model} is not installed in Ollama"
    return ""


def child_env(data_dir: Path, workspace: Path, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The worker's environment: the run's own data folder, the keychain closed, git stopped at .tmp."""
    env = {key: value for key, value in (os.environ if base is None else base).items() if key not in _CHILD_ENV_DROP}
    env.update({
        "ROW_BOT_DATA_DIR": str(data_dir),
        # Any truthy value closes the OS keychain; "true" (not "1") keeps the optional
        # tool modules importable, so the "default" tool profile matches the app's.
        "ROW_BOT_TEST_MODE": "true",
        "ROW_BOT_AUTO_START_OLLAMA": "0",
        # A failed eval turn never reaches the desktop (no notification, no sound).
        "ROW_BOT_DESKTOP_NOTIFICATIONS": "off",
        "ROW_BOT_WORKSPACE": str(workspace),
        "GIT_CEILING_DIRECTORIES": str(REPO_ROOT / ".tmp"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    })
    return env


def run_provider(state: provider_registry.Availability, task_ids: Sequence[str], run_dir: Path, guard: BudgetGuard,
                 *, variant_text: str = "", label: str = "baseline",
                 bridge: eval_profile.Runner = eval_profile.run_bridge) -> dict[str, Any]:
    """One provider's tasks in a worker process; charges the guard with what it spent."""
    pid = state.target.provider_id
    if pid == "ollama":
        why = _ollama_ready(state.model)
        if why:
            return {"provider": pid, "model": state.model, "status": "skipped", "reason": why, "tasks": []}
    remaining = guard.remaining()
    if pid not in FREE_PROVIDERS and ((remaining.max_tokens is not None and remaining.max_tokens <= 0) or (
        remaining.max_usd is not None and remaining.max_usd <= 0
    )):
        return {"provider": pid, "model": state.model, "status": "skipped", "reason": "budget used up", "tasks": []}
    stdin: str | None = None
    profile: Path | None = None
    bundle: eval_profile.CodexBundle | None = None
    if pid == "codex":
        # The ChatGPT lane: the sign-in comes from the eval profile, through a pipe, never a file.
        ready, why = eval_profile.availability()
        if not ready:
            return {"provider": pid, "model": state.model, "status": "skipped", "reason": why, "tasks": []}
        profile = eval_profile.profile_folder()
        try:
            bundle = eval_profile.read_bundle(profile, runner=bridge) if profile is not None else None
        except eval_profile.EvalProfileError as exc:
            return {"provider": pid, "model": state.model, "status": "skipped", "reason": str(exc), "tasks": []}
        if bundle is None:
            reason = f"the eval profile has no ChatGPT sign-in: {eval_profile.sign_in_hint()}"
            return {"provider": pid, "model": state.model, "status": "skipped", "reason": reason, "tasks": []}
        lane_cap = eval_profile.max_tokens()
        remaining = Caps(max_usd=remaining.max_usd, max_tokens=lane_cap if remaining.max_tokens is None
                         else min(remaining.max_tokens, lane_cap))
        stdin = json.dumps({key: value for key, value in bundle.payload().items() if key != "revision"})
    stem = f"{label}-{pid}".replace(":", "_").replace("/", "_").replace("=", "_").replace(",", "_")
    data_dir = run_dir / "data" / stem
    workspace = run_dir / "workspace" / stem
    out = run_dir / "workers" / f"{stem}.json"
    log = run_dir / "logs" / f"{stem}.log"
    for folder in (data_dir, workspace, out.parent, log.parent):
        folder.mkdir(parents=True, exist_ok=True)
    argv = [
        sys.executable, "-m", "tests.e2e.live_eval.worker",
        "--provider", pid, "--model", state.model, "--tasks", ",".join(task_ids),
        "--out", str(out), "--workspace", str(workspace), "--log", str(log), "--variant", variant_text,
        "--max-usd", "" if remaining.max_usd is None else f"{remaining.max_usd:.6f}",
        "--max-tokens", "" if remaining.max_tokens is None else str(remaining.max_tokens),
    ]
    if stdin is not None:
        argv += ["--credentials", "stdin"]
    timeout = 60 * float(os.environ.get(TIMEOUT_ENV, "") or DEFAULT_TIMEOUT_MIN)
    stdout = ""
    try:
        completed = subprocess.run(argv, cwd=REPO_ROOT, env=child_env(data_dir, workspace), timeout=timeout,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", input=stdin)
        stdout = completed.stdout or ""
        stderr_tail = redact(completed.stderr[-2000:])
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
        stderr_tail = f"worker timed out after {timeout / 60:.0f} minutes"
    credentials = ""
    if profile is not None and bundle is not None:
        # Only tokens the run refreshed go back, so the profile's sign-in survives a rotation.
        try:
            credentials = eval_profile.write_back(profile, bundle, eval_profile.refreshed_from(stdout), runner=bridge)
        except eval_profile.EvalProfileError as exc:
            credentials = f"not written: {exc}"
        eval_profile.scrub_worker_folder(data_dir)
    result = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {
        "provider": pid, "model": state.model, "status": "error", "tasks": [], "spent": {},
    }
    if credentials:
        result["credentials"] = credentials
    if result.get("status") != "ran":
        result.setdefault("reason", stderr_tail.strip().splitlines()[-1] if stderr_tail.strip() else "worker failed")
        result["stderr_tail"] = stderr_tail
    spent = result.get("spent") or {}
    guard.charge_spend(Spend(**{key: spent[key] for key in ("tokens", "usd", "unpriced_tokens", "calls") if key in spent}))
    return result


def read_probes(run_dir: Path) -> list[ProbeRecord]:
    path = run_dir / PROBES_FILE
    if not path.is_file():
        return []
    return [ProbeRecord.from_dict(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_probe(run_dir: Path, probe: ProbeRecord) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / PROBES_FILE).open("ab") as handle:
        handle.write((redact(json.dumps(asdict(probe), default=str)) + "\n").encode("utf-8"))


def run(env: Mapping[str, str] | None = None, *, run_dir: Path | None = None, guard: BudgetGuard | None = None,
        variant_text: str | None = None, checks: bool = False) -> tuple[Report, Path]:
    """Run every chosen provider's tasks (and, with *checks*, the verification checks); write the report."""
    source = dict(os.environ if env is None else env)
    if variant_text is not None:
        source[variants.VARIANT_ENV] = variant_text
    plan = build_plan(source)
    caps = Caps.from_env(source)
    guard = guard or BudgetGuard(caps)
    folder = run_dir or new_run_dir(source)
    variant = variants.parse(source.get(variants.VARIANT_ENV, ""))
    rates = load_rates(source[RATES_ENV]) if source.get(RATES_ENV) else {}
    report = Report(run_id=folder.name, variant=variant.label)
    selection = provider_registry.select(source)
    for state in selection.chosen:
        task_ids = [task.task_id for task in select_tasks(state.target.provider_id, source)]
        result = run_provider(state, task_ids, folder, guard, variant_text=source.get(variants.VARIANT_ENV, ""),
                              label=variant.label)
        report.providers.append({
            "provider": state.target.provider_id, "model": result.get("model") or state.model,
            "status": result.get("status"), "reason": result.get("reason", ""),
            "setup": result.get("setup_notes", []), "variant_changes": result.get("variant_changes", []),
            # The ChatGPT lane: whether refreshed tokens went back to the eval profile (never the tokens).
            "credentials": result.get("credentials", ""),
        })
        report.tasks.extend(TaskRecord.from_dict(task).finish(rates) for task in result.get("tasks") or [])
    for row in plan["skipped"]:
        report.providers.append({"provider": row["provider"], "model": "", "status": "skipped", "reason": row["reason"]})
    if checks:
        run_checks(folder, guard)
    report.probes = read_probes(folder)
    report.caps = plan["caps"]
    report.spent = asdict(guard.spent)
    if guard.spent.unpriced_tokens:
        report.notes.append(
            f"{guard.spent.unpriced_tokens:,} tokens were unpriced (no provider cost and no rate); the USD cap "
            "covered only priced calls, the token cap covered all of them."
        )
    if any(task.status == BUDGET for task in report.tasks):
        report.notes.append("The run stopped before a cap would have been passed; later tasks did not run.")
    write_report(report, folder, rates=rates, manual=MANUAL_CHECKS)
    guard.save(folder / "budget.json")
    return report, folder


def run_checks(run_dir: Path, guard: BudgetGuard) -> int:
    """The verification checks under pytest, with what is left of the caps."""
    remaining = guard.remaining()
    env = {key: value for key, value in os.environ.items() if key not in _CHILD_ENV_DROP}
    env.update({
        ENABLE_ENV: "1",
        OUT_ENV: str(run_dir),
        "ROW_BOT_LIVE_EVAL_MAX_USD": "off" if remaining.max_usd is None else f"{max(remaining.max_usd, 1e-6):.6f}",
        "ROW_BOT_LIVE_EVAL_MAX_TOKENS": "off" if remaining.max_tokens is None else str(max(remaining.max_tokens, 1)),
    })
    argv = [sys.executable, "-m", "pytest", "tests/e2e/test_live_verification.py", "-m", "live_provider", "-q",
            "-p", "no:cacheprovider", "-o", "addopts="]
    completed = subprocess.run(argv, cwd=REPO_ROOT, env=env, check=False)
    budget_file = run_dir / "checks-budget.json"
    if budget_file.is_file():
        spent = json.loads(budget_file.read_text(encoding="utf-8")).get("spent") or {}
        guard.charge_spend(Spend(**spent))
    return completed.returncode


def ab(env: Mapping[str, str] | None, variant_a: str, variant_b: str) -> tuple[Path, dict[str, Any]]:
    """The same tasks under two variants (one shared budget), then the diff."""
    source = dict(os.environ if env is None else env)
    folder = new_run_dir(source, suffix="-ab")
    guard = BudgetGuard(Caps.from_env(source))
    report_a, _ = run(source, run_dir=folder / "a", guard=guard, variant_text=variant_a)
    report_b, _ = run(source, run_dir=folder / "b", guard=guard, variant_text=variant_b)
    diff = diff_reports(report_a, report_b)
    (folder / "ab.json").write_bytes((json.dumps(diff, indent=2, default=str) + "\n").encode("utf-8"))
    (folder / "ab.md").write_bytes(render_diff_markdown(diff).encode("utf-8"))
    return folder, diff


def summarize(report: Report) -> str:
    ran = [task for task in report.tasks if task.status in {PASS, "fail", ERROR}]
    passed = sum(task.status == PASS for task in report.tasks)
    stopped = sum(task.status == BUDGET for task in report.tasks)
    skipped = sum(task.status == SKIPPED for task in report.tasks)
    return (f"{passed}/{len(ran)} tasks passed; {stopped} stopped by the budget; {skipped} skipped; "
            f"spent {report.spent.get('tokens', 0):,} tokens, ${float(report.spent.get('usd') or 0):.4f} priced")


# ── CLI ────────────────────────────────────────────────────────────────────


def _apply_flags(args: argparse.Namespace) -> None:
    mapping = {
        "providers": provider_registry.PROVIDERS_ENV,
        "tasks": "ROW_BOT_LIVE_EVAL_TASKS",
        "tier": "ROW_BOT_LIVE_EVAL_TIER",
        "variant": variants.VARIANT_ENV,
        "out": OUT_ENV,
        "max_usd": "ROW_BOT_LIVE_EVAL_MAX_USD",
        "max_tokens": "ROW_BOT_LIVE_EVAL_MAX_TOKENS",
        "rates": RATES_ENV,
    }
    for attribute, name in mapping.items():
        value = getattr(args, attribute, None)
        if value:
            os.environ[name] = str(value)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--providers", help=f"comma list (sets {provider_registry.PROVIDERS_ENV})")
    parser.add_argument("--tasks", help="comma list of task ids (sets ROW_BOT_LIVE_EVAL_TASKS)")
    parser.add_argument("--tier", choices=("core", "full", "learning"))
    parser.add_argument("--max-usd", dest="max_usd")
    parser.add_argument("--max-tokens", dest="max_tokens")
    parser.add_argument("--rates", help="rates file (USD per million tokens)")
    parser.add_argument("--out", help="run folder (default .tmp/live-eval/<timestamp>)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="live_eval", description="Row-Bot's opt-in live eval (WP B3).")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="list tasks x providers and the estimated budget; no network")
    _common(plan)
    plan.add_argument("--variant")
    plan.add_argument("--json", action="store_true")
    run_parser = sub.add_parser("run", help="run the eval against real providers (needs ROW_BOT_LIVE_EVAL=1)")
    _common(run_parser)
    run_parser.add_argument("--variant")
    run_parser.add_argument("--checks", action="store_true", help="also run the verification checks")
    run_parser.add_argument("--dry-run", action="store_true", help="same as plan")
    ab_parser = sub.add_parser("ab", help="run the same tasks under two variants and diff them")
    _common(ab_parser)
    ab_parser.add_argument("--a", required=True)
    ab_parser.add_argument("--b", required=True)
    ab_parser.add_argument("--dry-run", action="store_true")
    diff_parser = sub.add_parser("diff", help="diff two report.json files")
    diff_parser.add_argument("a")
    diff_parser.add_argument("b")
    ledger_parser = sub.add_parser("ledger", help="summarize a validation data folder's usage.db (counts only)")
    ledger_parser.add_argument("--data-dir", required=True)
    sub.add_parser("manual", help="print the manual checklist")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):  # a Windows console defaults to a code page without "→"
            reconfigure(encoding="utf-8", errors="replace")
    try:
        if args.command == "manual":
            for item in MANUAL_CHECKS:
                print(f"- {item.check_id} ({item.source})\n  Do: {item.steps}\n  Expect: {item.expect}\n")
            return 0
        if args.command == "diff":
            diff = diff_reports(Report.load(Path(args.a)), Report.load(Path(args.b)))
            print(render_diff_markdown(diff))
            return 0
        if args.command == "ledger":
            folder = Path(args.data_dir)
            if everyday_data_folder(folder):
                print("Refusing: point --data-dir at a separate validation folder, never the everyday ~/.row-bot.")
                return 2
            rates_path = os.environ.get(RATES_ENV, "")
            print(json.dumps(summarize_folder(folder, load_rates(rates_path) if rates_path else {}), indent=2))
            return 0
        _apply_flags(args)
        if args.command == "plan" or getattr(args, "dry_run", False):
            if args.command == "ab":
                for label, text in (("A", args.a), ("B", args.b)):
                    os.environ[variants.VARIANT_ENV] = text
                    print(f"== Variant {label}\n" + render_plan(build_plan()) + "\n")
                return 0
            plan = build_plan()
            print(json.dumps(plan, indent=2, default=str) if getattr(args, "json", False) else render_plan(plan))
            return 0
        if not enabled():
            print(render_plan(build_plan()))
            print(f"\nThis calls real providers and spends money. Set {ENABLE_ENV}=1 to run it.")
            return 2
        if args.command == "ab":
            folder, diff = ab(None, args.a, args.b)
            print(render_diff_markdown(diff))
            print(f"A/B written to {folder}")
            return 0
        report, folder = run(None, checks=args.checks)
        print(summarize(report))
        print(f"Report: {folder / 'report.md'}")
        return 0
    except (BudgetConfigError, variants.VariantError, ValueError) as exc:
        print(f"live_eval: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
