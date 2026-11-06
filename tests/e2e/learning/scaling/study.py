"""The controlled study (``paper/PREREG-SCALING.md`` v1, SC1): how much teacher data a local model needs, DAgger
against behaviour cloning at equal rows, training against retrieval, and whether replay agreement tracks outcome
success, on the seeded people's work whose outcomes the product's verifiers check.

    python -m tests.e2e.learning.scaling.study --plan --out <folder> --scratch <folder>
    python -m tests.e2e.learning.scaling.study --out <folder> --scratch <folder> --eval-profile <folder> \\
        --runtimes-from <folder> --gpu-booking <file> --lane <lane> --cap-tokens <N> <stage> [<stage> ...]
    python -m tests.e2e.learning.scaling.study --dry-run --out <folder> --scratch <folder>

**Stages** run only when named (in this order), each continuing from its own state file under ``--out``:

- ``pool``: the teacher (GPT-6.1 Sol on the ChatGPT lane) runs every training-block request of each person through
  the real runtime with the simulated person, recorded by Learning's capture; the product's dataset code builds the
  rows (``worker.PoolRun``).
- ``subsets``: the nested subsets, stratified by person, drawn once (``subsets.py``).
- ``train``: the starting model served, then the product's training of ``sft-100`` ... ``sft-3000`` and ``sft-1000b``.
- ``dagger``: two rounds of relabelled rows and ``dagger-1000`` (``dagger.py``).
- ``retrieval``: the retrieval arm's index of the 1,000-row subset (``retrieval.py``).
- ``evaluate``: every arm on every held-out request and the hidden pack tasks, then replay agreement.
- ``analyse``: §7, only once every arm has finished (``analysis.py``): ``report.json`` and ``report.md``.

``--plan`` prints the plan (``design.plan``) and runs nothing. ``--dry-run`` runs every stage with fakes
(``dry_run.py``).

**The build pin** (§2): before its first stage the study records its commit, a hash of the code it runs (Row-Bot's
source and the harness) and its design in ``build.json``; a later stage on another build or design refuses.

It writes only under ``--out`` and ``--scratch``, and refuses the everyday profile, its backups, the eval profile and a
Row-Bot checkout for every folder it writes. Each child process stops cleanly at the token cap (exit 75) and at once
on a usage or rate limit (exit 76); the study then stops and says so.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from tests.e2e.learning.scaling import design as design_module
from tests.e2e.learning.scaling.design import ARMS, DESIGN, PREREG_SHA256, Design
from tests.e2e.learning.seeded.scratch import PROTECT_ENV, ScratchError, check_scratch

ROOT = Path(__file__).resolve().parents[4]
STAGES = ("pool", "subsets", "train", "dagger", "retrieval", "evaluate", "analyse")
EXIT_TOKEN_CAP = 75
EXIT_PROVIDER_LIMIT = 76
#: What the code hash covers: Row-Bot's source and every part of the harness the study runs.
PINNED = ("src/row_bot", "tests/e2e/learning", "tests/e2e/live_eval", "tests/fixtures")
PINNED_SUFFIXES = (".py", ".json")
FORMAT = "row_bot.learning.scaling_study_report"

Runner = Callable[[list[str], Mapping[str, str], Path], int]


class Stopped(RuntimeError):
    """A stage that stopped (the cap, a limit, a refusal): the study says why and goes no further."""


def _run_child(command: list[str], env: Mapping[str, str], log: Path) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(command[2:5])} ===\n")
        handle.flush()
        return subprocess.Popen(command, cwd=str(ROOT), env=dict(env), stdout=handle, stderr=subprocess.STDOUT).wait()


def commit(root: Path = ROOT) -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
                              timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def code_hash(root: Path = ROOT) -> str:
    """SHA-256 of the pinned code: every ``.py`` and ``.json`` file under :data:`PINNED`, by path, line endings as
    ``\\n`` (a Windows checkout hashes the same)."""

    digest = hashlib.sha256()
    for top in PINNED:
        for path in sorted((root / top).rglob("*")):
            if path.suffix in PINNED_SUFFIXES and path.is_file() and "__pycache__" not in path.parts:
                digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
                digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False, default=str), encoding="utf-8")


class Study:
    """The study's stages (see the module's docstring)."""

    def __init__(self, args: argparse.Namespace, *, runner: Runner = _run_child,
                 say: Callable[[str], None] = lambda text: print(text, flush=True),
                 build: Callable[[], dict[str, str]] | None = None) -> None:
        from tests.e2e.learning.scaling import dry_run

        self.args = args
        self.design: Design = dry_run.DESIGN if args.dry_run else DESIGN
        profile = Path(args.eval_profile) if args.eval_profile else None
        self.out = check_scratch(Path(args.out), profile)
        self.scratch = check_scratch(Path(args.scratch), profile)
        for folder in self.folders():
            check_scratch(folder, profile)
        # Anything of Row-Bot this process imports (the generator's pack format, the analysis) binds a scratch folder.
        os.environ["ROW_BOT_DATA_DIR"] = str(self.scratch / "orchestrator")
        self.runner = runner
        self.say = say
        self.build = build or (lambda: {"commit": commit(), "code_hash": code_hash()})

    def folders(self) -> list[Path]:
        """Every folder the study writes."""

        return [self.out / stage for stage in STAGES] + [self.scratch / "orchestrator", self.study_data] + [
            self.pool_data(person) for person in self.design.people]

    @property
    def study_data(self) -> Path:
        return self.scratch / "study" / "data"

    def pool_data(self, person: str) -> Path:
        return self.scratch / "pool" / person / "data"

    # ── The build pin ───────────────────────────────────────────────────

    def pin(self) -> dict[str, Any]:
        """Record the build before the first run; refuse another build or design afterwards (§2)."""

        current = json.loads(json.dumps({**self.build(), "design": self.design.to_dict(), "prereg_sha256": PREREG_SHA256}))
        path = self.out / "build.json"
        saved = read_json(path)
        if saved:
            changed = [key for key in ("commit", "code_hash", "design", "prereg_sha256") if saved.get(key) != current[key]]
            if changed:
                raise ScratchError(f"this study is pinned to build {saved.get('commit', '')[:12]} (code "
                                   f"{saved.get('code_hash', '')[:12]}); this one differs in {', '.join(changed)}: "
                                   "§2 allows another build only by an amendment, with the affected runs rerun")
            return saved
        write_json(path, {**current, "pinned_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        write_json(self.out / "design.json", self.design.to_dict())
        return current

    # ── Children ────────────────────────────────────────────────────────

    def spent(self) -> int:
        """ChatGPT-lane tokens this study has spent so far (every task's own count)."""

        total = sum(int((read_json(self.out / "pool" / person / "result.json").get("tokens") or {})
                        .get("cloud_total") or 0) for person in self.design.people)
        return total + sum(int(read_json(self.out / stage / "state.json").get("cloud_tokens") or 0)
                           for stage in ("dagger", "evaluate"))

    def child_env(self, data: Path) -> dict[str, str]:
        protect = os.environ.get(PROTECT_ENV, "")
        env = {key: value for key, value in os.environ.items() if not key.startswith("ROW_BOT_")}
        if protect:
            env[PROTECT_ENV] = protect
        env.update({"ROW_BOT_DATA_DIR": str(data), "ROW_BOT_LEARNING_PREVIEW": "1",
                    "ROW_BOT_DESKTOP_NOTIFICATIONS": "off", "ROW_BOT_AUTO_START_OLLAMA": "0",
                    "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
        return env

    def child(self, task: str, data: Path, *, person: str = "") -> None:
        data.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "tests.e2e.learning.scaling.worker", task, "--out", str(self.out),
                   "--data", str(data), "--lane", self.args.lane, "--cap-tokens", str(self.args.cap_tokens),
                   "--spent-before", str(self.spent())]
        for flag, value in (("--person", person), ("--eval-profile", self.args.eval_profile),
                            ("--runtimes-from", self.args.runtimes_from), ("--gpu-booking", self.args.gpu_booking)):
            if value:
                command += [flag, str(value)]
        name = f"{task}-{person}" if person else task
        self.say(f"── {name} (ChatGPT-lane tokens so far {self.spent():,})")
        code = self.runner(command, self.child_env(data), self.out / "logs" / f"{name}.log")
        if code == EXIT_TOKEN_CAP:
            raise Stopped(f"{name}: the token cap ({self.args.cap_tokens:,}) was reached; it stopped cleanly. A cap "
                          "stop is reported, and nothing reruns without an amendment (§8)")
        if code == EXIT_PROVIDER_LIMIT:
            raise Stopped(f"{name}: the ChatGPT lane answered with a usage or rate limit; run the stage again later")
        if code != 0:
            raise Stopped(f"{name} failed (exit {code}): see {self.out / 'logs' / (name + '.log')}")

    # ── Stages ──────────────────────────────────────────────────────────

    def stage_pool(self) -> None:
        from tests.e2e.learning.seeded import report

        people = {}
        for person in self.design.people:
            if not read_json(self.out / "pool" / person / "state.json").get("stages", {}).get("rows"):
                self.child("pool", self.pool_data(person), person=person)
            state = read_json(self.out / "pool" / person / "state.json")
            work = report.live_work(state.get("requests") or [])
            people[person] = {"rows": (state.get("rows") or {}), "requests": work["requests"],
                              "pass_rate_first_try": work["first_try"].get("overall"),
                              "pass_rate_final": work["final"].get("overall"), "corrections": work["corrections"],
                              "teach": work["teach"], "stops": work["stops"],
                              "cloud_tokens": (read_json(self.out / "pool" / person / "result.json").get("tokens")
                                               or {}).get("cloud_total"), "model": state.get("model")}
        rows = sum(int((item["rows"] or {}).get("rows") or 0) for item in people.values())
        write_json(self.out / "pool" / "summary.json", {"people": people, "rows": rows,
                                                         "subsets": design_module.subset_sizes(self.design, rows)})
        self.say(f"pool: {rows:,} rows")

    def pool_rows(self) -> list[dict[str, Any]]:
        from tests.e2e.learning.scaling.worker import read_lines

        return [row for person in self.design.people for row in read_lines(self.out / "pool" / person / "rows.jsonl")]

    def stage_subsets(self) -> None:
        from tests.e2e.learning.scaling import subsets

        if (self.out / "subsets" / "summary.json").is_file():
            return
        rows = self.pool_rows()
        by_person: dict[str, list[str]] = {}
        for row in rows:
            by_person.setdefault(row["person"], []).append(row["id"])
        try:
            drawn = subsets.draw(by_person, self.design.sizes, self.design.subset_seed)
        except subsets.SubsetError as error:
            raise Stopped(f"subsets: {error}") from error
        owner = {row["id"]: row["person"] for row in rows}
        for subset in drawn:
            write_json(self.out / "subsets" / f"n{subset.n}.json", subset.to_dict(owner))
        write_json(self.out / "subsets" / "summary.json", {
            "seed": self.design.subset_seed, "pool": len(rows),
            "subsets": {arm: {key: value for key, value in subset.to_dict(owner).items() if key != "ids"}
                        for arm, subset in zip(design_module.SFT, drawn)}})
        self.say("subsets: " + ", ".join(f"N={item.n}: {len(item.rows)}" for item in drawn))

    def stage_train(self) -> None:
        self.child("train", self.study_data)

    def stage_dagger(self) -> None:
        self.child("dagger", self.study_data)

    def stage_retrieval(self) -> None:
        from tests.e2e.learning.scaling import retrieval

        n = self.design.subset_of("retrieval")
        wanted = set(read_json(self.out / "subsets" / f"n{n}.json").get("ids") or [])
        if not wanted:
            raise Stopped(f"retrieval: the {n}-row subset hasn't been drawn")
        held = [request for person in self.design.people for request in design_module.blocks(self.design, person)[1]]
        index = retrieval.Index.build([row for row in self.pool_rows() if row["id"] in wanted],
                                      held_out=[request.id for request in held])
        folder = self.out / "retrieval"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.jsonl").write_text("".join(line + "\n" for line in index.to_lines()), encoding="utf-8",
                                            newline="\n")
        write_json(folder / "picks.json", {request.id: [entry.id for entry in index.pick(request.prompt,
                                                                                         self.design.retrieval_k)]
                                           for request in held})
        write_json(folder / "summary.json", {
            "rows": len(index.entries), "subset": n, "k": self.design.retrieval_k,
            "example_chars": retrieval.EXAMPLE_CHARS,
            "into_the_prompt": "runtime: Learning's ways-of-working overlay (one guidance entry per example); "
                               "replay: the arm's guidance"})
        self.say(f"retrieval: {len(index.entries)} rows indexed")

    def stage_evaluate(self) -> None:
        self.child("evaluate", self.study_data)

    def stage_analyse(self) -> dict[str, Any]:
        from tests.e2e.learning.scaling import analysis
        from tests.e2e.learning.scaling.worker import read_lines

        evaluation = read_json(self.out / "evaluate" / "state.json")
        if not evaluation.get("done"):
            raise Stopped("analyse: the results are computed only when every arm has finished (§8)")
        built = self.arms(evaluation, read_lines(self.out / "evaluate" / "runs.jsonl"),
                          read_lines(self.out / "evaluate" / "replay.jsonl"))
        clusters = {item["task"]: item["cluster"] for item in read_lines(self.out / "evaluate" / "runs.jsonl")}
        dagger = read_json(self.out / "dagger" / "state.json")
        report = {
            "format": FORMAT, "version": 1, "prereg": {"file": design_module.PREREG, "sha256": PREREG_SHA256},
            "build": read_json(self.out / "build.json"), "teacher": read_json(self.out / "teacher.json"),
            "pool": read_json(self.out / "pool" / "summary.json"),
            "subsets": read_json(self.out / "subsets" / "summary.json"),
            "training": {arm: {key: entry.get(key) for key in ("state", "why", "version", "rows", "examples",
                                                                "validation", "steps", "seed", "kept_pass", "failure",
                                                                "loss", "training_seconds", "ready_seconds")}
                         for arm, entry in (read_json(self.out / "train" / "state.json").get("arms") or {}).items()},
            "dagger": {"rounds": {key: dagger.get(key) for key in ("round_1", "round_2")},
                       "teacher_tokens": dagger.get("teacher_tokens"),
                       "overlap_with_1000_subset": dagger.get("overlap_with_1000_subset"),
                       "requests": len(dagger.get("requests") or [])},
            "retrieval": read_json(self.out / "retrieval" / "summary.json"),
            "evaluation": {"unavailable": evaluation.get("unavailable"),
                           "replay_cases": len(read_lines(self.out / "evaluate" / "replay_cases.jsonl")),
                           "hidden_pack_tasks": self.design.hidden_tasks},
            "analysis": analysis.analyse(built, clusters, self.design),
        }
        write_json(self.out / "analyse" / "report.json", report)
        (self.out / "analyse" / "report.md").write_text(analysis.markdown(report), encoding="utf-8", newline="\n")
        self.say(f"Report: {self.out / 'analyse' / 'report.md'}")
        return report

    def arms(self, evaluation: Mapping[str, Any], runs: Sequence[Mapping[str, Any]],
             replay: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        """Each arm's results for the analysis: its runs, replay and hidden aggregates, the rows it used, tokens
        (held-out, replay and hidden) and GPU-hours (training and evaluation)."""

        trained = read_json(self.out / "train" / "state.json").get("arms") or {}
        hidden = (evaluation.get("hidden") or {}).get("arms") or {}
        retrieval_rows = read_json(self.out / "retrieval" / "summary.json").get("rows")
        found: dict[str, dict[str, Any]] = {}
        for arm in ARMS:
            if arm in (evaluation.get("unavailable") or {}):
                found[arm] = {"state": "couldnt_start", "why": evaluation["unavailable"][arm]}
                continue
            if arm not in (evaluation.get("arms") or {}):
                found[arm] = {"state": "not_run", "why": "not evaluated"}
                continue
            tokens = dict((evaluation.get("tokens") or {}).get(arm) or {"cloud": 0, "local": 0})
            tokens["hidden"] = (hidden.get(arm) or {}).get("tokens")
            entry = trained.get(arm) or {}
            found[arm] = {
                "state": "done", "runs": [item for item in runs if item["arm"] == arm],
                "replay": [item for item in replay if item["arm"] == arm], "hidden": hidden.get(arm) or {},
                "n": entry.get("rows") if arm in design_module.TRAINED else retrieval_rows if arm == "retrieval" else None,
                "tokens": tokens,
                "gpu_seconds": float((evaluation.get("gpu_seconds") or {}).get(arm) or 0.0)
                + float(entry.get("training_seconds") or 0.0) + float(entry.get("ready_seconds") or 0.0)}
        return found

    # ── Running ─────────────────────────────────────────────────────────

    def run(self, stages: Sequence[str]) -> int:
        unknown = [stage for stage in stages if stage not in STAGES]
        if unknown or not stages:
            raise ScratchError(f"name the stages to run, from: {', '.join(STAGES)}")
        build = self.pin()
        self.say(f"build {build.get('commit', '')[:12]}, code {build.get('code_hash', '')[:12]}")
        try:
            for stage in STAGES:
                if stage in stages:
                    self.say(f"stage {stage}")
                    getattr(self, f"stage_{stage}")()
        except Stopped as stopped:
            self.say(f"stopped: {stopped}")
            return 3
        return 0


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Row-Bot Learning's controlled scaling study (PREREG-SCALING.md v1)")
    parser.add_argument("stages", nargs="*", help=f"the stages to run: {', '.join(STAGES)}")
    parser.add_argument("--out", required=True, help="where the study's state, results and report go")
    parser.add_argument("--scratch", required=True, help="where the scratch profiles go")
    parser.add_argument("--eval-profile", default=os.environ.get("ROW_BOT_LIVE_EVAL_CODEX_PROFILE", ""),
                        help="the ChatGPT lane's eval profile (its sign-in is read into memory)")
    parser.add_argument("--runtimes-from", default="", help="a data folder with the training engine and starting model")
    parser.add_argument("--gpu-booking", default="", help="the shared GPU booking file")
    parser.add_argument("--lane", default="lane-scaling")
    parser.add_argument("--cap-tokens", type=int, default=250_000_000,
                        help="the ChatGPT-lane tokens the whole study may spend (§8 expects 150-200M)")
    parser.add_argument("--plan", action="store_true", help="print the plan and run nothing")
    parser.add_argument("--dry-run", action="store_true", help="every stage with fakes (no network, no GPU)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # a log file on Windows is cp1252 otherwise
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    args = parse(argv)
    try:
        study = Study(args)
        if args.plan:
            print(json.dumps(design_module.plan(study.design), indent=1, ensure_ascii=False))
            return 0
        if args.dry_run:
            return study.run(args.stages or list(STAGES))
        if not args.eval_profile:
            raise ScratchError("--eval-profile (or ROW_BOT_LIVE_EVAL_CODEX_PROFILE) names the ChatGPT lane's eval profile")
        return study.run(args.stages)
    except ScratchError as error:
        print(f"scaling study: {error}", file=sys.stderr)
        return 2


__all__ = ["STAGES", "Study", "Stopped", "code_hash", "commit", "main", "parse"]


if __name__ == "__main__":
    sys.exit(main())
