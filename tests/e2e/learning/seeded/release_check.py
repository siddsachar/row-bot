"""The Learning release check (S1, PLAN.md O92): one command, seeded people, the whole loop.

    uv run python scripts/learning_release_check.py --people all --days 30 --tiers 8gb \\
        --out <folder> --scratch <folder> --eval-profile <folder> --runtimes-from <folder>

For each made-up person (``people/``) and each tier it runs ``live.py`` in its own process with its own
scratch profile (``--scratch``/``<run>/<person>/data``, Learning's preview flag on, the main model
GPT-6.1 Sol on the ChatGPT lane from the eval profile), one person at a time, and then writes the
report (``report.json`` and ``report.md`` in ``--out``): per person and overall, pass rates with their
denominators and 95% intervals, the product's paired analyses, tokens, GPU-hours, time per task, the
survival checks, and the stages not built yet.

- **Resumes** where it stopped: each person's ``state.json`` says what is done; run the same command
  again. ``--people mara`` runs one person, ``--stages days`` one stage.
- **A planned crash** (the first training is crashed at a checkpoint, ``--crash``, on by default) shows
  as exit code 86: the person's process is started again and Row-Bot's recovery and the person's
  Continue take it from the checkpoint.
- **Tokens:** ``--cap-tokens`` (default 25M) counts everything on the ChatGPT lane, the simulated
  person included; a person's process stops cleanly at the cap (exit 75) and the run says so.
- ``--plan`` prints what would run (people, requests, the token estimate) and runs nothing.
- ``--archive mara@before-O106`` keeps a person's run under that name (their results and scratch profile)
  so the person can run again fresh; the report lists kept runs apart, and their tokens count in the spend.
  Stop the run first.
- It writes only under ``--out`` and ``--scratch`` and refuses the everyday profile, its backups, the
  eval profile and a Row-Bot checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from tests.e2e.learning.seeded import generator, report as report_module
from tests.e2e.learning.seeded.scratch import PROTECT_ENV, ScratchError, check_scratch

ROOT = Path(__file__).resolve().parents[4]
EXIT_PLANNED_CRASH = 86
EXIT_TOKEN_CAP = 75
MAX_RESTARTS = 3
STAGES = ("profile", "past", "days", "checks", "benchmark", "tidy")
#: Run after every person's work (O116): the benchmarks hold the card longest, and tidy follows them.
LAST = ("benchmark", "tidy")
#: A person's run kept under another name: ``<person>@<label>`` (``--archive``).
ARCHIVE_MARK = "@"
#: Rough cloud tokens for --plan, from the pilots: a live request (a turn or two on GPT-6.1 Sol with the
#: person's tools, a correction now and then: about 50K), the main model on the packs once a run (64 visible
#: + 32 hidden x 3, kept 30 days and shared between the people), and its replay of a person's held-out work
#: in each of their comparisons (about three: the runner's two and the benchmark).
PLAN_TOKENS_PER_REQUEST = 50_000
PLAN_TOKENS_MAIN_PACKS = 2_500_000
PLAN_TOKENS_REPLAY_PER_PERSON = 3_000_000

Runner = Callable[[list[str], Mapping[str, str], Path], int]


def _run_child(command: list[str], env: Mapping[str, str], log: Path) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(command[2:6])} ===\n")
        handle.flush()
        process = subprocess.Popen(command, cwd=str(ROOT), env=dict(env), stdout=handle, stderr=subprocess.STDOUT)
        return process.wait()


class ReleaseCheck:
    """Runs the people's processes and writes the report (see the module's docstring)."""

    def __init__(self, args: argparse.Namespace, *, runner: Runner = _run_child,
                 say: Callable[[str], None] = lambda text: print(text, flush=True)) -> None:
        self.args = args
        self.out = check_scratch(Path(args.out), Path(args.eval_profile) if args.eval_profile else None)
        self.scratch = check_scratch(Path(args.scratch), Path(args.eval_profile) if args.eval_profile else None)
        # Anything of Row-Bot this process imports (the recipes, the pack format) binds a scratch folder.
        os.environ["ROW_BOT_DATA_DIR"] = str(self.scratch / "orchestrator")
        self.runner = runner
        self.say = say
        self.people = generator.people() if args.people == "all" else [item.strip() for item in args.people.split(",")
                                                                        if item.strip()]
        unknown = [item for item in self.people if item not in generator.people()]
        if unknown:
            raise ScratchError(f"unknown seeded people: {unknown}")
        self.tiers = [item.strip() for item in args.tiers.split(",") if item.strip()]
        self.stages = [item.strip() for item in args.stages.split(",") if item.strip()]
        if any(stage not in STAGES for stage in self.stages):
            raise ScratchError(f"stages are {', '.join(STAGES)}")
        self.run_file = self.out / "run.json"
        try:
            self.run: dict[str, Any] = json.loads(self.run_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.run = {}

    # ── Where things go ─────────────────────────────────────────────────

    def person_dir(self, tier: str, person: str) -> Path:
        return self.out / tier / person

    def data_dir(self, tier: str, person: str) -> Path:
        return self.scratch / tier / person / "data"

    def save_run(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        self.run_file.write_text(json.dumps(self.run, indent=1, sort_keys=True), encoding="utf-8")

    # ── Tiers ───────────────────────────────────────────────────────────

    def tier_status(self, tier: str) -> str:
        """``ran`` for a tier whose recipe is certified, otherwise why not."""

        try:
            from row_bot.learning.training import recipes

            recipe = recipes.load(tier)
        except Exception:  # noqa: BLE001
            return "no recipe for this tier in this build"
        return "ran" if getattr(recipe, "certified", False) else "not certified yet (L2x certifies the larger tiers)"

    # ── Plan ────────────────────────────────────────────────────────────

    def plan(self) -> dict[str, Any]:
        rows = []
        for person in self.people:
            history = generator.generate(person, seed=self.args.seed, days=self.args.days)
            rows.append({"person": person, "past_conversations": len(history.past),
                         "past_turns": sum(len(item.requests) for item in history.past),
                         "live_requests": len(history.stream), "history": history.fingerprint()[:12]})
        requests = sum(row["live_requests"] for row in rows)
        estimate = (requests * PLAN_TOKENS_PER_REQUEST + PLAN_TOKENS_MAIN_PACKS
                    + len(rows) * PLAN_TOKENS_REPLAY_PER_PERSON) * len([tier for tier in self.tiers
                                                                       if self.tier_status(tier) == "ran"] or [1])
        return {"people": rows, "tiers": {tier: self.tier_status(tier) for tier in self.tiers},
                "estimated_cloud_tokens": estimate, "cap": self.args.cap_tokens,
                "fits": estimate <= self.args.cap_tokens}

    # ── Running ─────────────────────────────────────────────────────────

    def spent(self) -> int:
        """Cloud tokens already spent by every person of this run (their results)."""

        total = 0
        for tier in self.tiers:
            paths = [self.person_dir(tier, person) / "result.json" for person in generator.people()]
            paths += sorted((self.out / tier).glob(f"*{ARCHIVE_MARK}*/result.json"))
            for path in paths:
                try:
                    total += int(json.loads(path.read_text(encoding="utf-8"))["tokens"]["cloud_total"])
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        return total

    def child_env(self, data: Path) -> dict[str, str]:
        protect = os.environ.get(PROTECT_ENV, "")
        env = {key: value for key, value in os.environ.items() if not key.startswith("ROW_BOT_")}
        if protect:
            env[PROTECT_ENV] = protect
        env.update({"ROW_BOT_DATA_DIR": str(data), "ROW_BOT_LEARNING_PREVIEW": "1",
                    "ROW_BOT_DESKTOP_NOTIFICATIONS": "off", "ROW_BOT_AUTO_START_OLLAMA": "0",
                    "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
        return env

    def run_person(self, tier: str, person: str, spent_before: int, stages: Sequence[str] = ()) -> dict[str, Any]:
        data = self.data_dir(tier, person)
        data.mkdir(parents=True, exist_ok=True)
        run_dir = self.person_dir(tier, person)
        command = [sys.executable, "-m", "tests.e2e.learning.seeded.live", "--person", person, "--data", str(data),
                   "--run-dir", str(run_dir), "--days", str(self.args.days), "--seed", str(self.args.seed),
                   "--tier", tier, "--stages", ",".join(stages or self.stages),
                   "--eval-profile", str(self.args.eval_profile),
                   "--cap-tokens", str(self.args.cap_tokens), "--spent-before", str(spent_before),
                   "--lane", self.args.lane]
        if self.args.runtimes_from:
            command += ["--runtimes-from", str(self.args.runtimes_from)]
        if self.args.gpu_booking:
            command += ["--gpu-booking", str(self.args.gpu_booking)]
        if self.args.crash:
            command.append("--crash")
        if not self.args.gpu:
            command.append("--no-gpu")
        if self.args.pilot_compare:
            command.append("--pilot-compare")
        record: dict[str, Any] = {"exits": []}
        for attempt in range(MAX_RESTARTS + 1):
            started = time.monotonic()
            code = self.runner(command, self.child_env(data), run_dir / "person.log")
            record["exits"].append({"code": code, "seconds": round(time.monotonic() - started, 1)})
            if code == EXIT_PLANNED_CRASH:
                self.say(f"{tier}/{person}: crashed at a checkpoint as planned; starting it again")
                continue
            break
        last = record["exits"][-1]["code"]
        record["state"] = {0: "done", EXIT_TOKEN_CAP: "token_cap"}.get(last, "failed")
        return record

    def execute(self) -> dict[str, Any]:
        self.run.setdefault("started_at", time.strftime("%Y-%m-%d %H:%M"))
        self.run.update({"seed": self.args.seed, "days": self.args.days, "people": self.people,
                         "build": _build(), "cap_tokens": self.args.cap_tokens})
        self.run.setdefault("tiers", {})
        self.run.setdefault("people_runs", {})
        self.save_run()
        capped = False
        work = [stage for stage in self.stages if stage not in LAST]
        last = [stage for stage in self.stages if stage in LAST]
        for stages, suffix in ((work, ""), (last, " (benchmark)")):  # every person's work, then the benchmarks
            if not stages:
                continue
            for tier in self.tiers:
                status = self.tier_status(tier)
                self.run["tiers"][tier] = status
                self.save_run()
                if status != "ran":
                    if not suffix:
                        self.say(f"tier {tier}: {status}; skipped")
                    continue
                for person in self.people:
                    key = f"{tier}/{person}{suffix}"
                    spent = self.spent()
                    if capped or spent >= self.args.cap_tokens:
                        self.say(f"token cap reached ({spent:,}); {key} not run")
                        self.run["people_runs"][key] = {"state": "token_cap"}
                        self.save_run()
                        continue
                    self.say(f"── {key} (cloud tokens so far {spent:,})")
                    outcome = self.run_person(tier, person, spent, stages)
                    self.run["people_runs"][key] = outcome
                    self.save_run()
                    self.say(f"{key}: {outcome['state']}")
                    capped = capped or outcome["state"] == "token_cap"  # the cap is the whole run's
        return self.write_report()

    def results(self) -> list[dict[str, Any]]:
        found = []
        for tier in self.tiers:
            for person in self.people:
                path = self.person_dir(tier, person) / "result.json"
                try:
                    found.append(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    continue
        return found

    def archived(self) -> list[dict[str, Any]]:
        """Runs kept under another name (``--archive``), each with the name it was kept as."""

        found = []
        for tier in self.tiers:
            for path in sorted((self.out / tier).glob(f"*{ARCHIVE_MARK}*/result.json")):
                try:
                    result = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                result["kept_as"] = path.parent.name
                found.append(result)
        return found

    def archive(self, name: str) -> list[Path]:
        """Keep ``person@label``'s results and scratch profile under that name, so the person runs again fresh."""

        person, mark, label = name.partition(ARCHIVE_MARK)
        if person not in generator.people() or not mark or not label.strip() or "/" in label or "\\" in label:
            raise ScratchError(f"--archive takes <person>{ARCHIVE_MARK}<label>, e.g. mara{ARCHIVE_MARK}before-O106")
        moved = []
        for tier in self.tiers:
            for parent in (self.out / tier, self.scratch / tier):
                source, target = parent / person, parent / f"{person}{ARCHIVE_MARK}{label}"
                if source.exists():
                    if target.exists():
                        raise ScratchError(f"{target} exists already")
                    source.rename(target)
                    moved.append(target)
        return moved

    def write_report(self) -> dict[str, Any]:
        results = self.results()
        if results:
            self.run.setdefault("model", results[0].get("model"))
        built = report_module.build(results, run=self.run, tiers=self.run.get("tiers"),
                                    cap_tokens=self.args.cap_tokens, archived=self.archived())
        (self.out / "report.json").write_text(json.dumps(built, indent=1, sort_keys=True), encoding="utf-8")
        (self.out / "report.md").write_text(report_module.markdown(built), encoding="utf-8", newline="\n")
        self.say(f"Report: {self.out / 'report.md'}")
        return built


def _build() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Row-Bot Learning's release check on seeded people (S1)")
    parser.add_argument("--people", default="all", help="all, or ids separated by commas")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--tiers", default="8gb", help="tiers separated by commas (an uncertified tier is reported)")
    parser.add_argument("--out", required=True, help="where the results and the report go")
    parser.add_argument("--scratch", required=True, help="where the people's scratch profiles go")
    parser.add_argument("--eval-profile", default=os.environ.get("ROW_BOT_LIVE_EVAL_CODEX_PROFILE", ""),
                        help="the ChatGPT lane's eval profile (its sign-in is read, refreshed tokens written back)")
    parser.add_argument("--runtimes-from", default="", help="a data folder with the training engine and starting model")
    parser.add_argument("--seed", type=int, default=20261022)
    parser.add_argument("--stages", default=",".join(STAGES))
    parser.add_argument("--cap-tokens", type=int, default=25_000_000)
    parser.add_argument("--gpu-booking", default="", help="the shared GPU booking file")
    parser.add_argument("--lane", default="lane-B")
    parser.add_argument("--no-crash", dest="crash", action="store_false", help="don't crash the first training")
    parser.add_argument("--no-gpu", dest="gpu", action="store_false",
                        help="a pilot: nights run Dream and the vault but not Learning's runner (no training)")
    parser.add_argument("--pilot-compare", action="store_true",
                        help="a pilot: comparisons run one visible task a pack and no hidden problems")
    parser.add_argument("--plan", action="store_true", help="print the plan and run nothing")
    parser.add_argument("--report-only", action="store_true", help="write the report from the results so far")
    parser.add_argument("--archive", default="", help="keep a person's run as <person>@<label> and stop")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # a log file on Windows is cp1252 otherwise
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    args = parse(argv)
    try:
        check = ReleaseCheck(args)
        if args.plan:
            print(json.dumps(check.plan(), indent=1))
            return 0
        if args.report_only:
            check.write_report()
            return 0
        if args.archive:
            for path in check.archive(args.archive):
                print(f"kept: {path}")
            return 0
        if not args.eval_profile:
            raise ScratchError("--eval-profile (or ROW_BOT_LIVE_EVAL_CODEX_PROFILE) names the ChatGPT lane's eval profile")
        check.execute()
    except ScratchError as error:
        print(f"release check: {error}", file=sys.stderr)
        return 2
    return 0


__all__ = ["EXIT_PLANNED_CRASH", "EXIT_TOKEN_CAP", "ReleaseCheck", "STAGES", "main", "parse"]
