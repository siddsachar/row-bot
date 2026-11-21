"""L3b's ablation (paper, pre-registration §9): lessons on/off x ways of working on/off, and the harder split.

Phases (each resumable: pack results are kept per arm, task and run in the scratch folder's arm cache):

- ``harder``: the main model alone on the harder split (O31) and, for comparison, on the visible packs, through
  the real runtime (the evaluation worker), ``--repeats`` runs per task: how often it really fails there.
- ``twobytwo``: on one seeded person (a copy of S1's or L3a's scratch profile), the four arms **none**,
  **lessons**, **ways** and **both** (the person's drafted lessons as the memories recall finds, their drafted
  ways of working as the turn's overlay), at equal budget, paired, ``--repeats`` runs per arm on hidden
  problems: the person's hidden corrected problems (replay, the change as guidance), the packs' hidden tasks,
  and the visible and harder splits (real runtime). ``--model main`` runs GPT-6.1 Sol (the ChatGPT lane);
  ``--model <ref>`` a local model served by Row-Bot's own server (the personal model, ``--serve-context``).
- ``pilot``: L3a's grouped-vs-single pilot again, on the harder split: every lesson and way of working each arm
  of L3a's ablation drafted is tested against the same assistant without it (the real runtime, equal budget,
  paired, ``--repeats`` runs), and passes by the pre-registered rule (section 6.1: mean paired difference above 0 on
  its kind of work, not below -10 points on the rest). Skills are left out (the harder split has no skill use).

``--ports`` keeps Row-Bot's own model server on the given loopback ports (a lane's range); ``--no-container``
leaves out the coding tasks so no container engine is asked.

Only aggregates are written (``--out``), with the pre-registered paired analysis from ``evaluation`` (mean
paired difference, 95% cluster-bootstrap interval, permutation p-value) and denominators. Refuses the everyday
profile and its backups. Stops at once on any usage or rate limit.

    uv run python scripts/learning_change_ablation.py harder --data <scratch>/harder --eval-profile <profile>
        --out <scratch>/out --repeats 3
    uv run python scripts/learning_change_ablation.py twobytwo --source <a seeded person's profile copy>
        --data <scratch>/<person> --eval-profile <profile> --out <scratch>/out --model main
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261023


def _say(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def _refuse(data: Path) -> None:
    everyday = (Path.home() / ".row-bot").resolve()
    resolved = data.resolve()
    if resolved == everyday or everyday in resolved.parents or "RowBot-backups" in str(resolved):
        raise SystemExit("Refused: this is the everyday profile or its backup.")


def boot(data: Path, profile: Path | None) -> tuple[str, Any]:
    """Bind Row-Bot to ``data`` (with the ChatGPT lane's sign-in in memory when ``profile`` is given)."""

    os.environ["ROW_BOT_DATA_DIR"] = str(data)
    os.environ["ROW_BOT_DESKTOP_NOTIFICATIONS"] = "off"
    os.environ.setdefault("ROW_BOT_AUTO_START_OLLAMA", "0")
    sys.path.insert(0, str(ROOT))
    from row_bot import learning, models

    learning.invalidate()
    if learning.data_dir().resolve() != data.resolve():
        raise SystemExit(f"Row-Bot's data folder is {learning.data_dir()}, not {data}")
    if profile is None:
        return "", None
    from row_bot.providers import codex
    from tests.e2e.learning.seeded.live import SignIn
    from tests.e2e.live_eval import eval_profile

    sign_in = SignIn(profile)
    sign_in.install()
    model = eval_profile.choose_model([item.model_id for item in codex.fetch_codex_model_infos()])
    ref = f"model:codex:{model}"
    models.adopt_saved_default(ref)
    return ref, sign_in


class Limited(Exception):
    pass


def pin_ports(spec: str) -> None:
    """Keep Row-Bot's own model server on a port in ``spec`` ("8121-8129"), the first one free."""

    if not spec:
        return
    low, _, high = spec.partition("-")
    ports = range(int(low), int(high or low) + 1)
    from row_bot.learning.training import llama_server

    def first_free() -> int:
        for port in ports:
            if llama_server.port_free(port):
                return port
        raise SystemExit(f"No free port in {spec}")

    llama_server.free_port = first_free


def wilson(passed: int, total: int, z: float = 1.96) -> tuple[float | None, float | None]:
    """The 95% Wilson score interval of a pass rate (centred on the adjusted rate), or (None, None)."""

    if total <= 0:
        return None, None
    rate = passed / total
    denominator = 1 + z * z / total
    centre = (rate + z * z / (2 * total)) / denominator
    spread = z * ((rate * (1 - rate) / total + z * z / (4 * total * total)) ** 0.5) / denominator
    return round(max(0.0, centre - spread), 4), round(min(1.0, centre + spread), 4)


def _container(args: argparse.Namespace) -> bool:
    if getattr(args, "no_container", False):
        return False
    from row_bot.learning.sandbox import container_runtime

    return bool(container_runtime())


def _check_limit(case: Any) -> None:
    detail = str(getattr(case, "detail", "") or "").lower()
    if getattr(case, "status", "") == "error" and any(word in detail for word in ("rate limit", "usage limit", "429",
                                                                                  "quota", "too many requests")):
        raise Limited(detail[:200])


def _worker(arms: list[Any], data: Path, ref: str, *, local: bool) -> tuple[Any, Any]:
    from row_bot.learning import eval_worker

    worker_arms = {arm.name: eval_worker.WorkerArm(arm.name, ref, local) for arm in arms}
    credentials, providers, unsupported = eval_worker.gather(list(worker_arms.values()))
    if unsupported:
        raise SystemExit(f"These arms can't be handed to the worker: {unsupported}")
    spec = {"arms": [{"name": arm.name, "model_ref": ref, "lessons": [list(item) for item in arm.lessons],
                      "ways": [list(item) for item in arm.ways]} for arm in arms],
            "tools": eval_worker.safe_tools(eval_worker.person_tools()), "providers": providers,
            "code_backend": "container"}
    worker = eval_worker.EvalWorker(spec, credentials=credentials, person_data=data)
    worker.start()
    return worker, eval_worker.RuntimeRunner(worker, worker_arms, record_usage=eval_worker.ledger_recorder())


def _summary(comparison: Any) -> dict[str, Any]:
    return {
        "arms": {item.arm.name: {"passed": item.passed, "total": item.total, "by_kind": item.by_kind,
                                 "tokens": item.measured_tokens, "cloud_tokens": item.measured_cloud_tokens,
                                 "seconds": round(item.seconds, 1), "ran": item.ran, "cached": item.cached,
                                 "causes": item.causes} for item in comparison.arms},
        "analysis": comparison.analysis, "repeats": comparison.repeats, "repeated": comparison.repeated,
        "stopped": comparison.stopped,
    }


def _write(out: Path, name: str, value: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_text(json.dumps(value, indent=1, sort_keys=True, default=str), encoding="utf-8", newline="\n")
    _say(f"wrote {out / name}")


def phase_harder(args: argparse.Namespace) -> None:
    from row_bot.learning import evaluation, kinds
    from row_bot.learning.packs import harder, visible_tasks
    from row_bot.learning.sandbox import container_runtime
    from row_bot.learning.spending import TokenMeter
    from row_bot.learning.store import LearningStore

    data = Path(args.data).resolve()
    _refuse(data)
    data.mkdir(parents=True, exist_ok=True)
    ref, sign_in = boot(data, Path(args.eval_profile))
    from row_bot import learning

    learning.read_settings()
    container = bool(container_runtime())
    tasks = [task for task in harder.tasks() if container or task.verifier.kind != "code_tests"]
    if args.visible:
        per_pack: dict[str, int] = {}
        for task in visible_tasks():
            if not container and task.pack == "small_coding":
                continue
            per_pack[task.pack] = per_pack.get(task.pack, 0) + 1
            if per_pack[task.pack] <= args.visible:
                tasks.append(task)
    arm = evaluation.Arm("main", model_ref=ref, cache=True)
    cache = evaluation.ArmCache(data)
    meter = TokenMeter(int(args.cap))
    started = time.monotonic()
    done = [0]

    def counted(_arm: str, case: Any) -> None:
        done[0] += 1
        _check_limit(case)
        _say(f"{done[0]}/{len(tasks) * args.repeats}: {case.task_id} {case.status} {case.cause} "
             f"{case.tokens} tokens ({cache.hits} kept)")

    worker, runner = _worker([arm], data, ref, local=False)
    try:
        result = evaluation.compare([arm], tasks=tasks, runner=runner, cache=cache, meter=meter,
                                    store=LearningStore(data), save=False, on_case=counted,
                                    budget=evaluation.Budget(task_tokens=150_000), repeats=args.repeats,
                                    kind_key=kinds.from_label)
    finally:
        worker.stop()
        if sign_in is not None:
            sign_in.scrub()
    item = result.arms[0]
    hard = [case for case in item.cases if case.task_id.startswith(harder.PREFIX)]
    visible = [case for case in item.cases if not case.task_id.startswith(harder.PREFIX)]

    def rate(cases: list[Any]) -> dict[str, Any]:
        fractions = evaluation.pass_fractions(cases)
        counted_runs = [case for case in cases if case.counted]
        passed = sum(case.passed for case in counted_runs)
        low, high = wilson(passed, len(counted_runs))
        by_task = {task: round(value, 3) for task, (_kind, value) in sorted(fractions.items())}
        return {"tasks": len(fractions), "runs": len(counted_runs), "passed_runs": passed,
                "pass_rate": round(passed / len(counted_runs), 4) if counted_runs else None,
                "wilson_low": low, "wilson_high": high,
                "mean_task_pass_fraction": round(sum(by_task.values()) / len(by_task), 4) if by_task else None,
                "per_task": by_task,
                "causes": dict(sorted({cause: sum(1 for case in cases if case.cause == cause)
                                       for cause in {case.cause for case in cases if case.cause}}.items()))}

    _write(Path(args.out), "harder_baseline.json", {
        "model": ref, "repeats": args.repeats, "container": container, "minutes": round((time.monotonic() - started) / 60, 1),
        "harder": rate(hard), "visible": rate(visible) if visible else None,
        "tokens": item.measured_tokens, "cloud_tokens": item.measured_cloud_tokens, "stopped": result.stopped,
    })


SERVE_CONTEXT = 32_768
PAIRS = (("none", "lessons"), ("none", "ways"), ("none", "both"), ("lessons", "both"), ("ways", "both"))


def load_drafts(paths: list[str]) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    """The lessons ((title, text)) and ways of working ((slot, text)) Learning drafted for the seeded people
    (L3a's product arm, ``grouped``): what the 2x2 turns on and off. Skills are left out."""

    from row_bot.learning import behaviour

    lessons, ways = [], []
    for path in paths:
        found = json.loads(Path(path).read_text(encoding="utf-8"))
        for item in (found.get("grouped") or {}).get("drafts") or ():
            text = " ".join(str(item.get("text") or "").split())
            if not text:
                continue
            if item.get("change") == "lesson":
                lessons.append((str(item.get("title") or "Lesson"), text))
            elif item.get("change") == "way_of_working":
                slot = str(item.get("slot") or "guidance")
                ways.append((slot if slot in behaviour.SLOTS else "guidance", text[: behaviour.MAX_TEXT]))
    return tuple(lessons), tuple(ways)


def phase_serve(args: argparse.Namespace) -> None:
    """Make the personal model ready in Row-Bot's own server, as the product serves it (32K, the bridge)."""

    data = Path(args.data).resolve()
    _refuse(data)
    boot(data, None)
    pin_ports(args.ports)
    from row_bot.learning.training import hardware, serving

    found = hardware.detect()
    hardware.check(refresh=True, detector=lambda: found)
    folder = Path(args.out) / "serve" / "pm"
    folder.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    outcome = serving.serve("row_bot", version=1, gguf=Path(args.gguf), serve_context=SERVE_CONTEXT, folder=folder,
                            root=data, tag="row-bot-personal-l3b", tool_turn=False)
    entry = {**serving.describe(outcome), "seconds": round(time.monotonic() - started, 1), "gguf": Path(args.gguf).name}
    _write(Path(args.out), "serve_pm.json", entry)
    _say(f"serve: {entry.get('state')} {entry.get('model_ref')}")


def phase_twobytwo(args: argparse.Namespace) -> None:
    import contextlib

    from row_bot.learning import evaluation, kinds
    from row_bot.learning.packs import PACKS, harder, visible_tasks
    from row_bot.learning.spending import TokenMeter
    from row_bot.learning.store import LearningStore

    data = Path(args.data).resolve()
    _refuse(data)
    local = args.model != "main"
    ref, sign_in = boot(data, None if local else Path(args.eval_profile))
    pin_ports(args.ports)
    if local:
        ref = args.model
    lessons, ways = load_drafts(args.drafts)
    _say(f"pool: {len(lessons)} lessons, {len(ways)} ways of working; model {ref}")
    serve = SERVE_CONTEXT if local else None
    texts_l, texts_w = tuple(text for _title, text in lessons), tuple(text for _slot, text in ways)
    arms = [
        evaluation.Arm("none", model_ref=ref, serve_context=serve),
        evaluation.Arm("lessons", model_ref=ref, serve_context=serve, lessons=lessons, guidance=texts_l),
        evaluation.Arm("ways", model_ref=ref, serve_context=serve, ways=ways, guidance=texts_w),
        evaluation.Arm("both", model_ref=ref, serve_context=serve, lessons=lessons, ways=ways,
                       guidance=texts_l + texts_w),
    ]
    container = _container(args)
    chosen = [task for task in harder.tasks() if container or task.verifier.kind != "code_tests"]
    if args.harder_ids:
        wanted = set(args.harder_ids.split(","))
        chosen = [task for task in chosen if task.id in wanted]
    if args.visible:
        per_pack: dict[str, int] = {}
        for task in visible_tasks():
            if not container and task.pack == "small_coding":
                continue
            per_pack[task.pack] = per_pack.get(task.pack, 0) + 1
            if per_pack[task.pack] <= args.visible:
                chosen.append(task)
    packs = [] if args.no_hidden_packs else [pack for pack in PACKS if container or pack != "small_coding"]
    verifiers = None if container else ["answer", "json_answer", "file", "app_state"]
    store = LearningStore(data)
    meter = TokenMeter(int(args.cap))
    cache = evaluation.ArmCache(data)
    budget = evaluation.Budget(task_tokens=150_000)
    started = time.monotonic()
    done = [0]

    def counted(_arm: str = "", case: Any = None) -> None:
        done[0] += 1
        if case is not None:
            _check_limit(case)
        if done[0] % 10 == 0:
            _say(f"{done[0]} runs in {round((time.monotonic() - started) / 60, 1)} min ({cache.hits} kept)")

    worker, runner = _worker(arms, data, ref, local=local)
    hold = contextlib.nullcontext()
    if local:
        from row_bot.learning.training import llama_server

        hold = llama_server.get(data).hold()
    try:
        with hold:
            shown = evaluation.compare(arms, tasks=chosen, runner=runner, cache=cache, meter=meter, store=store,
                                       save=False, on_case=counted, budget=budget, repeats=args.repeats,
                                       kind_key=kinds.from_label, pairs=PAIRS) if chosen else None
            hidden = None
            if args.replay != "none" or packs:
                hidden = evaluation.audit_check(
                    arms, asked_by="the L3b ablation", store=store, runner=runner, cache=cache, meter=meter,
                    packs=packs, verifiers=verifiers, budget=budget, save=False, repeats=args.repeats,
                    combine_with=shown, kind_key=kinds.from_label, pairs=PAIRS,
                    include_replay=args.replay != "none", corrected={"corrected": True, "uncorrected": False,
                                                                     "all": None}.get(args.replay),
                    on_progress=lambda *_done: counted())
    finally:
        worker.stop()
        if sign_in is not None:
            sign_in.scrub()
    _write(Path(args.out), f"twobytwo_{args.label}.json", {
        "model": ref if not local else "personal model (Qwen3.5 9B QLoRA, Row-Bot's own server, 32K)",
        "label": args.label, "repeats": args.repeats, "container": container,
        "pool": {"lessons": len(lessons), "ways": len(ways)},
        "visible_and_harder_tasks": len(chosen), "minutes": round((time.monotonic() - started) / 60, 1),
        "shown": _summary(shown) if shown is not None else None,
        "hidden": _summary(hidden) if hidden is not None else None,
    })


def load_pilot_drafts(paths: list[str]) -> list[dict[str, Any]]:
    """Every lesson and way of working L3a's ablation drafted, per arm (grouped, single) and person."""

    from row_bot.learning import behaviour

    found: list[dict[str, Any]] = []
    for path in paths:
        person = Path(path).stem
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for arm in ("grouped", "single"):
            for index, item in enumerate((data.get(arm) or {}).get("drafts") or ()):
                text = " ".join(str(item.get("text") or "").split())
                change = str(item.get("change") or "")
                if not text or change not in ("lesson", "way_of_working"):
                    continue
                slot = str(item.get("slot") or "guidance")
                found.append({"name": f"{arm}-{person}-{index}", "arm": arm, "person": person, "change": change,
                              "kind": str(item.get("kind") or ""), "title": str(item.get("title") or "Lesson"),
                              "text": text if change == "lesson" else text[: behaviour.MAX_TEXT],
                              "slot": slot if slot in behaviour.SLOTS else "guidance"})
    return found


def phase_pilot(args: argparse.Namespace) -> None:
    import contextlib

    from row_bot.learning import changes, evaluation, kinds
    from row_bot.learning.packs import harder
    from row_bot.learning.spending import TokenMeter
    from row_bot.learning.store import LearningStore

    data = Path(args.data).resolve()
    _refuse(data)
    local = args.model != "main"
    ref, sign_in = boot(data, None if local else Path(args.eval_profile))
    pin_ports(args.ports)
    if local:
        ref = args.model
    drafts = load_pilot_drafts(args.drafts)
    _say(f"pilot: {len(drafts)} drafts ({sum(d['arm'] == 'grouped' for d in drafts)} grouped); model {ref}")
    serve = SERVE_CONTEXT if local else None
    arms = [evaluation.Arm("none", model_ref=ref, serve_context=serve)]
    for draft in drafts:
        if draft["change"] == "lesson":
            arms.append(evaluation.Arm(draft["name"], model_ref=ref, serve_context=serve,
                                       lessons=((draft["title"], draft["text"]),), guidance=(draft["text"],)))
        else:
            arms.append(evaluation.Arm(draft["name"], model_ref=ref, serve_context=serve,
                                       ways=((draft["slot"], draft["text"]),), guidance=(draft["text"],)))
    container = _container(args)
    chosen = [task for task in harder.tasks() if container or task.verifier.kind != "code_tests"]
    meter = TokenMeter(int(args.cap))
    cache = evaluation.ArmCache(data)
    started = time.monotonic()
    done = [0]

    def counted(_arm: str = "", case: Any = None) -> None:
        done[0] += 1
        if case is not None:
            _check_limit(case)
        if done[0] % 25 == 0:
            _say(f"{done[0]}/{len(arms) * len(chosen) * args.repeats} runs in "
                 f"{round((time.monotonic() - started) / 60, 1)} min")

    worker, runner = _worker(arms, data, ref, local=local)
    hold = contextlib.nullcontext()
    if local:
        from row_bot.learning.training import llama_server

        hold = llama_server.get(data).hold()
    try:
        with hold:
            result = evaluation.compare(arms, tasks=chosen, runner=runner, cache=cache, meter=meter,
                                        store=LearningStore(data), save=False, on_case=counted,
                                        budget=evaluation.Budget(task_tokens=150_000), repeats=args.repeats,
                                        kind_key=kinds.from_label,
                                        pairs=[("none", draft["name"]) for draft in drafts])
    finally:
        worker.stop()
        if sign_in is not None:
            sign_in.scrub()
    analysis = (result.analysis or {}).get("arms") or {}
    rows = []
    for draft in drafts:
        by_kind = (analysis.get(draft["name"]) or {}).get("by_kind") or {}
        kind = kinds.from_label(draft["kind"]) if draft["kind"] else ""
        own = by_kind.get(kind) or {}
        rest = changes._rest(by_kind, kind)  # noqa: SLF001 - the product's own reading of the rule
        rows.append({"arm": draft["arm"], "person": draft["person"], "change": draft["change"], "kind": kind,
                     "own": {key: own.get(key) for key in ("problems", "baseline", "arm", "difference", "low",
                                                           "high", "p_value")},
                     "rest": rest, "verdict": changes.verdict(own, rest)})
    totals = {}
    for arm in ("grouped", "single"):
        mine = [row for row in rows if row["arm"] == arm]
        passed = sum(row["verdict"] == "better" for row in mine)
        low, high = wilson(passed, len(mine))
        totals[arm] = {"drafts": len(mine), "passed": passed,
                       "pass_rate": round(passed / len(mine), 4) if mine else None,
                       "wilson_low": low, "wilson_high": high,
                       "verdicts": dict(sorted({v: sum(r["verdict"] == v for r in mine)
                                                for v in {r["verdict"] for r in mine}}.items())),
                       "too_few": sum(row["verdict"] == "too_few" for row in mine)}
    baseline = next(item for item in result.arms if item.arm.name == "none")
    _write(Path(args.out), f"pilot_{args.label}.json", {
        "model": ref if not local else "personal model (Qwen3.5 9B QLoRA, Row-Bot's own server, 32K)",
        "label": args.label, "repeats": args.repeats, "container": container, "tasks": len(chosen),
        "minutes": round((time.monotonic() - started) / 60, 1), "totals": totals, "drafts": rows,
        "baseline": {"passed": baseline.passed, "total": baseline.total, "by_kind": baseline.by_kind},
        "tokens": sum(item.measured_tokens for item in result.arms),
        "cloud_tokens": sum(item.measured_cloud_tokens for item in result.arms), "stopped": result.stopped,
    })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="phase", required=True)
    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--data", required=True)
    serve_parser.add_argument("--gguf", required=True)
    serve_parser.add_argument("--out", required=True)
    serve_parser.add_argument("--ports", default="", help="keep the model server on these ports (8121-8129)")
    two = sub.add_parser("twobytwo")
    two.add_argument("--data", required=True)
    two.add_argument("--drafts", action="append", required=True, help="an L3a ablation result (its drafts)")
    two.add_argument("--model", default="main", help="main (the ChatGPT lane) or a served local model's ref")
    two.add_argument("--eval-profile", default="")
    two.add_argument("--out", required=True)
    two.add_argument("--label", required=True)
    two.add_argument("--repeats", type=int, default=3)
    two.add_argument("--visible", type=int, default=0, help="visible tasks per pack beside the harder split")
    two.add_argument("--harder-ids", default="", help="only these harder tasks (comma-separated)")
    two.add_argument("--no-hidden-packs", action="store_true")
    two.add_argument("--replay", choices=("none", "corrected", "uncorrected", "all"), default="corrected")
    two.add_argument("--cap", type=int, default=4_000_000)
    two.add_argument("--ports", default="", help="keep the model server on these ports (8121-8129)")
    two.add_argument("--no-container", action="store_true", help="leave out coding tasks; ask no container engine")
    pilot = sub.add_parser("pilot")
    pilot.add_argument("--data", required=True)
    pilot.add_argument("--drafts", action="append", required=True, help="an L3a ablation result (its drafts)")
    pilot.add_argument("--model", default="main", help="main (the ChatGPT lane) or a served local model's ref")
    pilot.add_argument("--eval-profile", default="")
    pilot.add_argument("--out", required=True)
    pilot.add_argument("--label", required=True)
    pilot.add_argument("--repeats", type=int, default=3)
    pilot.add_argument("--cap", type=int, default=4_000_000)
    pilot.add_argument("--ports", default="", help="keep the model server on these ports (8121-8129)")
    pilot.add_argument("--no-container", action="store_true", help="leave out coding tasks; ask no container engine")
    harder_parser = sub.add_parser("harder")
    harder_parser.add_argument("--data", required=True)
    harder_parser.add_argument("--eval-profile", required=True)
    harder_parser.add_argument("--out", required=True)
    harder_parser.add_argument("--repeats", type=int, default=3)
    harder_parser.add_argument("--visible", type=int, default=0, help="visible tasks per pack to run beside it")
    harder_parser.add_argument("--cap", type=int, default=4_000_000, help="cloud tokens this phase may spend")
    args = parser.parse_args(argv)
    try:
        if args.phase == "harder":
            phase_harder(args)
        elif args.phase == "serve":
            phase_serve(args)
        elif args.phase == "pilot":
            phase_pilot(args)
        else:
            phase_twobytwo(args)
    except Limited as error:
        _say(f"STOPPED: a usage or rate limit ({error}). Nothing more is sent.")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
