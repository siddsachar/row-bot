"""L3a's ablation (paper, pre-registration §9): issues grouped with their evidence vs drafts from single signals.

On S1's seeded people (``tests/e2e/learning/seeded``), each person's scratch profile is copied, time is cut at the
end of live day ``--cutoff-day`` and Learning drafts changes from the evidence before it, two ways at equal
drafting budget:

- **grouped**: the product's diagnosis (issues by kind of work and what went wrong, with their evidence);
- **single**: every signal its own issue (one diagnosis call per signal), capped at the tokens ``grouped`` spent.

Each drafted lesson, way of working or skill is then tested as guidance on work it never saw, against the same
assistant without it, at equal budget per task, 3 runs per arm: the person's own later requests (live days after
the cut, with their verifiers) and the visible packs. A draft **passes** when its mean paired difference is above
0 on its own kind of work and not below -10 points on the rest (pre-registration §6.1, "better"). The yield is
passing drafts per 1M tokens the arm spent (drafting and testing), with each pass share's Wilson interval and
a bootstrap interval of the yield over drafts.

Runs on the ChatGPT lane (the eval profile's sign-in in this process's memory; refreshed tokens go back) with the
main model it lists (GPT-6.1 Sol). Refuses the everyday profile, its backups, the eval profile and a checkout.
Resumable: results are kept per draft in ``--out``. Stops on any usage or rate limit.

    uv run python scripts/learning_diagnosis_ablation.py --people idris,wren,mara --parallel 3 --s1-scratch <release check scratch>/8gb
        --work <scratch folder> --eval-profile <eval profile> --out <scratch folder>
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261022
OWN = 8
REST = 4
PACK_OWN = 4
PACK_REST = 2


def wilson(passed: int, total: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if not total:
        return None, None
    share = passed / total
    middle = (share + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(share * (1 - share) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return round(max(0.0, middle - half), 4), round(min(1.0, middle + half), 4)


def yield_interval(drafts: list[dict[str, Any]], drafting_tokens: int, *, resamples: int = 10_000,
                   seed: int = SEED) -> dict[str, Any]:
    """Passing drafts per 1M tokens (drafting plus each draft's testing), with a bootstrap over drafts."""

    tokens = drafting_tokens + sum(item["test_tokens"] for item in drafts)
    passed = sum(1 for item in drafts if item.get("passes"))
    found: dict[str, Any] = {"drafts": len(drafts), "passed": passed, "tokens": tokens,
                             "per_1m": round(passed / (tokens / 1e6), 4) if tokens else None}
    if not drafts:
        return {**found, "low": None, "high": None}
    rng = random.Random(seed)
    share = drafting_tokens / len(drafts)
    boots = []
    for _ in range(resamples):
        picks = [rng.choice(drafts) for _ in drafts]
        spent = sum(share + item["test_tokens"] for item in picks)
        boots.append(sum(1 for item in picks if item.get("passes")) / (spent / 1e6) if spent else 0.0)
    boots.sort()
    return {**found, "low": round(boots[int(0.025 * resamples)], 4), "high": round(boots[int(0.975 * resamples) - 1], 4)}


class Person:
    def __init__(self, args: argparse.Namespace, person: str) -> None:
        self.args, self.person = args, person
        self.work = Path(args.work) / person.replace("@", "_")
        self.data = self.work / "data"

    def prepare(self) -> None:
        source = Path(self.args.s1_scratch) / self.person / "data"
        if not (source / "learning.db").is_file():
            raise SystemExit(f"No seeded profile at {source}")
        if self.data.exists():
            return
        self.data.mkdir(parents=True)
        for name in ("learning.db", "learning.db-wal", "learning_config.json"):
            if (source / name).is_file():
                shutil.copy2(source / name, self.data / name)
        shutil.copytree(source / "learning", self.data / "learning",
                        ignore=shutil.ignore_patterns("arm_cache.json"))


def boot(data: Path, profile: Path) -> Any:
    """Bind Row-Bot to ``data`` with the ChatGPT lane's sign-in in memory; returns the main model's ref."""

    os.environ["ROW_BOT_DATA_DIR"] = str(data)
    os.environ["ROW_BOT_LEARNING_PREVIEW"] = "1"
    os.environ["ROW_BOT_DESKTOP_NOTIFICATIONS"] = "off"
    os.environ.setdefault("ROW_BOT_AUTO_START_OLLAMA", "0")
    from row_bot import learning, models
    from row_bot.providers import codex
    from tests.e2e.learning.seeded.live import SignIn
    from tests.e2e.live_eval import eval_profile

    learning.invalidate()
    if learning.data_dir().resolve() != data.resolve():
        raise SystemExit(f"Row-Bot's data folder is {learning.data_dir()}, not {data}")
    sign_in = SignIn(profile)
    sign_in.install()
    model = eval_profile.choose_model([item.model_id for item in codex.fetch_codex_model_infos()])
    ref = f"model:codex:{model}"
    models.adopt_saved_default(ref)
    return ref, sign_in


def cutoff_time(day: int) -> float:
    from tests.e2e.learning.seeded.generator import day_date

    return datetime.combine(day_date(day) + timedelta(days=1), datetime.min.time()).timestamp()


def draft(person: Person, arm: str, cut: float, budget: int) -> dict[str, Any]:
    """One arm's drafting on the evidence before ``cut``; its drafts' texts and the tokens it spent."""

    from row_bot import evolution
    from row_bot.learning import diagnosis, holdout
    from row_bot.learning.calls import LedgerModelClient
    from row_bot.learning.spending import TokenMeter
    from row_bot.learning.store import LearningStore

    class SpendCap(TokenMeter):
        """Equal budget by what was spent: calls go on until the arm has spent ``budget`` (not by estimate)."""

        def allows(self, estimated_tokens: int, *, local: bool = False) -> bool:
            return local or self.spent < self.cap

    store = LearningStore(person.data, clock=lambda: cut)
    holdout.mark_corrections(store)
    report = diagnosis.run(store, client=LedgerModelClient(), night=f"ablation-{arm}", now=cut,
                           meter=SpendCap(budget), single=arm == "single", save=False, context="",
                           limit=100_000, check_model=lambda: None)
    drafts = []
    for outcome in report.outcomes:
        proposal = evolution.get_proposal(outcome.proposal_id) if outcome.proposal_id else None
        if proposal is None:
            continue
        payload = proposal.get("payload") or {}
        if outcome.change in ("create_skill", "patch_skill"):
            text = f"Skill {payload.get('name') or payload.get('target_skill')}: {payload.get('description') or ''}\n" \
                   f"{payload.get('instructions') or payload.get('updated_instructions') or ''}"
        else:
            text = str(payload.get("text") or "")
        drafts.append({"arm": arm, "issue": outcome.issue, "kind": outcome.kind, "change": outcome.change,
                       "title": str(proposal.get("title") or ""), "text": text.strip(),
                       "draft_tokens": outcome.tokens})
    # The next arm starts from the same evidence: this arm's drafts and attempts go.
    store_data = evolution._load_store()
    store_data["proposals"] = [item for item in store_data["proposals"]
                               if not isinstance((item.get("payload") or {}).get("learning"), dict)]
    evolution._save_store(store_data)
    for outcome in report.outcomes:
        if outcome.attempt_id:
            store.remove("attempt", outcome.attempt_id)
    return {"arm": arm, "state": report.state, "found": report.found, "diagnosed": report.diagnosed,
            "drafted": len(drafts), "refused": report.refused, "needs_examples": report.needs_examples,
            "waits": report.waits, "train": report.train, "unreadable": report.unreadable,
            "tokens": report.tokens, "drafts": drafts,
            "refusals": dict(__import__("collections").Counter(code for item in report.outcomes for code in item.refused))}


def later_tasks(person: str, cutoff_day: int, s1_out: Path | None = None) -> list[tuple[str, Any]]:
    """The person's live requests after the cut (kind, PackTask): first those the main model got wrong at
    first try in S1's run (``s1_out``: its per-person state), where a change can show a gain; then the rest,
    in order."""

    from tests.e2e.learning.seeded import generator

    history = generator.generate(person.split("@")[0], seed=SEED, days=30)
    missed: set[str] = set()
    state = (s1_out / person / "state.json") if s1_out is not None else None
    if state is not None and state.is_file():
        missed = {str(item.get("id")) for item in json.loads(state.read_text(encoding="utf-8")).get("requests") or ()
                  if not item.get("passed_first")}
    later = [request for request in history.stream if request.day > cutoff_day]
    later.sort(key=lambda request: (request.id not in missed, request.day, request.order))
    return [(request.intended, request.task) for request in later]


def test_sets(item: dict[str, Any], later: list[tuple[str, Any]], args: argparse.Namespace) -> dict[str, tuple]:
    """The draft's own kind and the rest, on the person's later work and on the visible packs."""

    from row_bot.learning import kinds
    from row_bot.learning.packs import visible_tasks

    kind = item["kind"]
    rng = random.Random(f"{SEED}/{item['issue']}")
    own = [task for found, task in later if found == kind][: args.own]
    others = [task for found, task in later if found != kind]
    rest = rng.sample(others, min(args.rest, len(others)))
    packs = [task for task in visible_tasks() if kinds.from_pack(task.pack) == kind]
    pack_others = [task for task in visible_tasks() if kinds.from_pack(task.pack) != kind
                   and task.verifier.kind != "code_tests"]
    return {"seeded": (own, rest), "packs": (rng.sample(packs, min(args.pack_own, len(packs))),
                                             rng.sample(pack_others, min(args.pack_rest, len(pack_others))))}


def test(item: dict[str, Any], sets: dict[str, tuple], args: argparse.Namespace, data: Path) -> dict[str, Any]:
    from row_bot.learning import evaluation
    from row_bot.learning.evaluation import Arm, ArmCache
    from row_bot.learning.spending import TokenMeter

    cache = ArmCache(data)
    base = Arm("without", cache=True)
    change = Arm("with", guidance=(item["text"],), subject_kind="attempt")
    found: dict[str, Any] = {"test_tokens": 0}
    for name, (own, rest) in sets.items():
        tasks = [*own, *rest]
        if not tasks:
            found[name] = None
            continue
        result = evaluation.compare([base, change], tasks=tasks, repeats=args.repeats, save=False, cache=cache,
                                    meter=TokenMeter(10**12), backend="auto")
        errors = sum(1 for arm in result.arms for case in arm.cases if case.status == "error")
        if errors > 0.2 * sum(len(arm.cases) for arm in result.arms):
            raise RuntimeError(f"too many model errors ({errors}): stopping (a usage or rate limit?)")
        found["test_tokens"] += result.arms[1].tokens
        found["base_tokens"] = found.get("base_tokens", 0) + result.arms[0].tokens
        without = evaluation.pass_fractions(result.arms[0].cases)
        with_ = evaluation.pass_fractions(result.arms[1].cases)
        own_ids = {task.id for task in own}
        diffs = {"own": [], "rest": []}
        for task in tasks:
            if task.id in without and task.id in with_:
                part = "own" if task.id in own_ids else "rest"
                diffs[part].append((task.id, with_[task.id][1] - without[task.id][1]))
        found[name] = {
            "own": evaluation.paired_analysis(diffs["own"]), "rest": evaluation.paired_analysis(diffs["rest"]),
            "own_tasks": len(own), "rest_tasks": len(rest),
            "without_passed": round(sum(value for _, value in without.values()), 3),
            "with_passed": round(sum(value for _, value in with_.values()), 3),
        }
        own_diff = found[name]["own"]["difference"]
        rest_diff = found[name]["rest"]["difference"]
        found[name]["passes"] = bool(own_diff is not None and own_diff > 0
                                     and (rest_diff is None or rest_diff >= -0.10))
    found["passes"] = bool((found.get("seeded") or {}).get("passes"))
    found["passes_packs"] = bool((found.get("packs") or {}).get("passes"))
    return found


def summarise(people: dict[str, Any]) -> dict[str, Any]:
    from collections import Counter

    arms: dict[str, Any] = {}
    for arm in ("grouped", "single"):
        drafts = [item for person in people.values() for item in person.get(arm, {}).get("drafts", [])
                  if "passes" in item]
        drafting = sum(person.get(arm, {}).get("tokens", 0) for person in people.values())
        seeded = yield_interval(drafts, drafting)
        packs = yield_interval([{**item, "passes": item.get("passes_packs")} for item in drafts], drafting)
        passed = seeded["passed"]
        arms[arm] = {"drafting_tokens": drafting, "drafts_tested": len(drafts), "passed_seeded": passed,
                     "pass_share_seeded": round(passed / len(drafts), 4) if drafts else None,
                     "pass_share_interval": wilson(passed, len(drafts)), "yield_seeded": seeded,
                     "passed_packs": packs["passed"], "yield_packs": packs,
                     "by_change": dict(Counter(item["change"] for item in drafts)),
                     "issues_found": sum(person.get(arm, {}).get("found", 0) for person in people.values()),
                     "refused": sum(person.get(arm, {}).get("refused", 0) for person in people.values())}
    return arms


def run_person(args: argparse.Namespace, name: str) -> None:
    """One person, in its own process (Row-Bot binds its data folder once): draft both arms, test each draft."""

    person = Person(args, name)
    out = Path(args.out) / f"{name.replace('@', '_')}.json"
    entry = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
    ref, sign_in = boot(person.data, Path(args.eval_profile))
    entry.setdefault("model", ref)
    cut = cutoff_time(args.cutoff_day)
    later = later_tasks(name, args.cutoff_day, Path(args.s1_out) if args.s1_out else None)
    entry["later_requests"] = len(later)

    def save() -> None:
        out.write_text(json.dumps(entry, indent=1), encoding="utf-8")

    for arm in ("grouped", "single"):
        if arm not in entry:
            budget = args.budget if arm == "grouped" else max(1, int(entry["grouped"]["tokens"]))
            entry[arm] = draft(person, arm, cut, budget)
            save()
    for arm in ("grouped", "single"):
        for item in entry[arm]["drafts"]:
            if "passes" not in item:
                item.update(test(item, test_sets(item, later, args), args, person.data))
                save()
    entry["write_backs"] = list(sign_in.write_backs)
    save()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--people", default="idris,wren,mara")
    parser.add_argument("--s1-scratch", required=True)
    parser.add_argument("--s1-out", default="", help="S1's per-person output folder (its state.json): later requests the "
                        "main model missed at first try are tested first")
    parser.add_argument("--work", required=True)
    parser.add_argument("--eval-profile", required=True)
    parser.add_argument("--out", required=True, help="a folder: one JSON per person and summary.json")
    parser.add_argument("--cutoff-day", type=int, default=15)
    parser.add_argument("--budget", type=int, default=200_000, help="the grouped arm's drafting cap")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--own", type=int, default=OWN)
    parser.add_argument("--rest", type=int, default=REST)
    parser.add_argument("--pack-own", type=int, default=PACK_OWN)
    parser.add_argument("--pack-rest", type=int, default=PACK_REST)
    parser.add_argument("--parallel", type=int, default=1, help="people at once")
    parser.add_argument("--plan", action="store_true", help="say what would run, then stop")
    args = parser.parse_args(argv)
    profile = Path(args.eval_profile)
    out = Path(args.out)
    from tests.e2e.learning.seeded.scratch import check_scratch

    for folder in (Path(args.work), out):
        check_scratch(folder, profile)  # never the everyday profile, its backups, the eval profile or a checkout
    out.mkdir(parents=True, exist_ok=True)
    people = [item.strip() for item in args.people.split(",") if item.strip()]
    child = os.environ.get("L3A_ABLATION_PERSON")
    if child:
        run_person(args, child)
        return 0
    if args.plan:
        for name in people:
            print(f"{name}: {len(later_tasks(name, args.cutoff_day))} later requests after day {args.cutoff_day}")
        return 0
    import subprocess

    waiting = list(people)
    running: list[tuple[str, Any]] = []
    failed = 0
    while waiting or running:
        while waiting and len(running) < max(1, args.parallel):
            name = waiting.pop(0)
            Person(args, name).prepare()
            log = open(out / f"{name.replace('@', '_')}.log", "a", encoding="utf-8")  # noqa: SIM115
            running.append((name, subprocess.Popen([sys.executable, __file__, *(argv or sys.argv[1:])],
                                                   env={**os.environ, "L3A_ABLATION_PERSON": name,
                                                        "ROW_BOT_DATA_DIR": str(Person(args, name).data)},
                                                   stdout=log, stderr=subprocess.STDOUT)))
        for item in list(running):
            if item[1].poll() is not None:
                running.remove(item)
                failed += bool(item[1].returncode)
        time.sleep(2)
    people_results = {}
    for name in people:
        path = out / f"{name.replace('@', '_')}.json"
        if path.is_file():
            people_results[name] = json.loads(path.read_text(encoding="utf-8"))
    summary = {"cutoff_day": args.cutoff_day, "budget": args.budget, "repeats": args.repeats, "seed": SEED,
               "people": sorted(people_results), "arms": summarise(people_results), "failed_people": failed}
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary["arms"], indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    raise SystemExit(main())
