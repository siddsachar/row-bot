"""The study's child processes (SC1): one task each, in one scratch profile.

Row-Bot binds its data folder when it is imported, so ``study.py`` starts this module once per task with
``ROW_BOT_DATA_DIR`` set to the profile it works in:

- ``pool`` (``<scratch>/pool/<person>/data``): the teacher's pool for one person (:class:`PoolRun`, the release
  check's own run with the study's seed and days and no nights), then its rows;
- ``train``, ``dagger`` and ``evaluate`` (``<scratch>/study/data``): every local model of the study is trained,
  served (Row-Bot's own llama.cpp server) and run in this one profile, one process at a time.

Each task saves where it is in its state file under ``--out`` after every step and continues from there when started
again. It stops cleanly at its token cap (exit 75) and at once when the ChatGPT lane says its usage or rate limit was
reached (exit 76). What a task needs from the computer (the teacher's sign-in, the training engine, serving, the
graphics card) comes from its kit: :class:`LiveKit` for the study, ``dry_run.DryKit`` for the dry run.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.e2e.learning.scaling import dagger, retrieval
from tests.e2e.learning.scaling import design as design_module
from tests.e2e.learning.scaling.design import DAGGER_ROUND_1, Design
from tests.e2e.learning.scaling.runner import (
    Person,
    ProviderLimit,
    Spec,
    StudyRunner,
    decisions,
)
from tests.e2e.learning.seeded import live
from tests.e2e.learning.seeded.scratch import (
    GpuGate,
    ScratchError,
    check_scratch,
    in_quiet_hours,
    quiet_reason,
)

logger = logging.getLogger("row_bot.scaling")

EXIT_TOKEN_CAP = 75
EXIT_PROVIDER_LIMIT = 76
#: No new GPU work from 00:25 (a run or a training step started then is done before 00:40), none until 05:00 (O116).
NEW_WORK_STOPS = (0, 25)
PURPOSE = "learning_test"
#: A training run that stops this many times in a row without a new step has failed (Lane B's own crash rule).
MAX_STALLS = 3
WATCH_SECONDS = 5.0


# ── Small state files ────────────────────────────────────────────────────────


class JsonState:
    """A JSON file saved whole after every step (written beside itself, then moved in place)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=1, sort_keys=True, default=str), encoding="utf-8")
        temporary.replace(self.path)


def read_lines(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


def append_lines(path: Path, items: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def cluster(request: Any) -> str:
    """A held-out task's cluster (§7): the person's simulated day."""

    return f"{request.person}/d{request.day}"


# ── Kits ─────────────────────────────────────────────────────────────────────


def new_work_quiet() -> str | None:
    """Why no new GPU work starts now (the participant's quiet hours, started early enough that work in hand ends
    before them), or None."""

    if in_quiet_hours(start=NEW_WORK_STOPS):
        return quiet_reason() or "the participant's quiet hours are near (no new GPU work from 00:25 to 05:00)"
    return None


class LiveKit:
    """The study's own: the eval profile's ChatGPT sign-in in this process's memory (as the release check holds it),
    the installed training engine, Row-Bot's own llama.cpp server, Ollama for the 27B and the shared card."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.sign_in: Any = None

    def boot(self, data: Path) -> None:
        return None

    def teacher(self, out: Path) -> str:
        """The teacher's model ref: the newest Sol the catalog lists, chosen once for the whole study (logged)."""

        from row_bot.providers import codex
        from tests.e2e.live_eval import eval_profile

        if self.sign_in is None:
            self.sign_in = live.SignIn(Path(self.args.eval_profile))
            self.sign_in.install()
        record = JsonState(out / "teacher.json")
        if not record.data.get("model"):
            record.data = {"model": eval_profile.choose_model([item.model_id for item in codex.fetch_codex_model_infos()]),
                           "chosen_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            record.save()
        return f"model:codex:{record.data['model']}"

    def engine(self, data: Path) -> Any:
        from row_bot.learning.training import engine

        return engine.LocalEngine(data)

    def supervisor_options(self) -> dict[str, Any]:
        return {}

    def maker_options(self, data: Path) -> dict[str, Any]:
        return {}

    def before_release(self, run: Path) -> None:
        return None

    def start(self, data: Path, recipe: Any) -> tuple[str | None, str | None]:
        """The untouched starting model, served as the versions are (the product's own ``base_model.ensure``)."""

        from row_bot.learning.training import base_model, engine, serving, weights

        model = weights.QWEN35_9B
        found = weights.status(model, data)
        return base_model.ensure(root=data, engine_status=engine.status(data), model=model,
                                 weights_folder=found.folder if found.state == "downloaded" else None,
                                 weights_bytes=model.weights_bytes or model.download_bytes, recipe=recipe,
                                 runtime=serving.ROW_BOT)

    def measure(self, data: Path) -> Any:
        """Rows measured exactly with the engine's own tokenizer and chat template (the planner's own measure)."""

        from row_bot.learning.training import engine, planner

        if not engine.status(data).installed:
            raise ScratchError(f"{data} holds no training engine: start the study with --runtimes-from")
        return lambda rows, max_seq: planner.engine_measure(rows, max_seq, root=data)

    def local_27b(self, design: Design) -> str:
        """Qwen3.8 27B in the computer's own Ollama, served as the personal arms are (their context, thinking off).
        Never downloaded: a missing model refuses."""

        from row_bot import models
        from row_bot.learning.training import serving

        try:
            serving.OllamaRuntime().show(design.local_27b)
        except Exception as error:
            raise ScratchError(f"{design.local_27b} isn't in this computer's Ollama ({type(error).__name__}): the "
                               "study never downloads it") from error
        from row_bot.learning.training import recipes

        models.set_local_model_settings(design.local_27b, context=recipes.load(design.tier).serve_context,
                                        reasoning="off")
        return f"model:ollama:{design.local_27b}"

    def unload(self, arm: str, design: Design) -> None:
        if arm == "local-27b":
            from row_bot.learning.training import serving

            serving.OllamaRuntime().unload(design.local_27b)

    def gpu(self, label: str) -> GpuGate:
        return GpuGate(Path(self.args.gpu_booking) if self.args.gpu_booking else None, self.args.lane, label=label,
                       quiet=new_work_quiet)


def kit_for(args: argparse.Namespace, design: Design) -> Any:
    if design.dry_run:
        from tests.e2e.learning.scaling.dry_run import DryKit

        return DryKit(args)
    return LiveKit(args)


# ── The study profile ────────────────────────────────────────────────────────


def recipe_for(design: Design, *, seed: int | None = None) -> Any:
    """The certified recipe of the study's tier (its own training seed unless ``seed``)."""

    from row_bot.learning.training import recipes

    recipe = recipes.load(design.tier)
    return replace(recipe, settings={**recipe.settings, "seed": int(seed)}) if seed is not None else recipe


def prepare_profile(data: Path, args: argparse.Namespace) -> None:
    """Learning on (recording the study's own chats), Row-Bot's own server, the engine and starting model linked."""

    from row_bot import learning
    from row_bot.learning.store import LearningStore
    from tests.fixtures.learning import save_learning_settings

    learning.invalidate()
    if learning.data_dir().resolve() != data.resolve():
        raise ScratchError(f"Row-Bot's data folder is {learning.data_dir()}, not {data}")
    if not learning.preview_enabled():
        raise ScratchError("Start the study with ROW_BOT_LEARNING_PREVIEW=1.")
    if not (data / learning.SETTINGS_FILE).is_file():
        save_learning_settings(data, enabled=True, sources={"my_chats": True, "workflows_and_goals": False,
                                                            "past_chats": False},
                               apply_mode="ask_first", train_in_quiet_hours=False, train_where="this_computer",
                               serve_runtime="row_bot", model_download="waiting", starting_model="qwen3.5-9b")
        learning.invalidate()
    LearningStore(data).create()
    if args.runtimes_from:
        # The release check's own linking: hard links, nothing in the source is ever written.
        live.PersonRun._link_engine(SimpleNamespace(data=data, args=SimpleNamespace(runtimes_from=args.runtimes_from)))


def cloud_check(state: JsonState, args: argparse.Namespace) -> None:
    """Stop cleanly at the token cap (everything this study spent on the ChatGPT lane, before and here)."""

    spent = int(args.spent_before) + int(state.data.get("cloud_tokens", 0))
    if spent >= int(args.cap_tokens):
        state.data["capped"] = {"spent": spent, "cap": int(args.cap_tokens), "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        state.save()
        logger.warning("token cap reached (%s of %s); stopping cleanly", spent, args.cap_tokens)
        raise SystemExit(EXIT_TOKEN_CAP)


def owners(design: Design, block: str) -> tuple[dict[str, Any], dict[str, tuple[Person, tuple[str, ...]]], list[Person]]:
    """The block's requests by id, each one's person and approved paths, and the people."""

    people = [Person.load(person) for person in design.people]
    requests: dict[str, Any] = {}
    owned: dict[str, tuple[Person, tuple[str, ...]]] = {}
    for person in people:
        training, held = design_module.blocks(design, person.id)
        for request in (training if block == "training" else held):
            requests[request.id] = request
            owned[request.id] = (person, tuple(request.approvals))
    return requests, owned, people


# ── pool ─────────────────────────────────────────────────────────────────────


class PoolRun(live.PersonRun):
    """The teacher's pool for one person (§3): the release check's run (profile, the 30-day past through the import,
    the training block's days with the simulated person) with the study's teacher and no nights (no training, Dream
    or vault refresh), then the rows the product's dataset code builds from what Learning recorded."""

    def __init__(self, args: argparse.Namespace, kit: Any, out: Path, design: Design) -> None:
        super().__init__(args)
        self.kit = kit
        self.out = out
        self.design = design

    def boot(self) -> None:
        os.environ["ROW_BOT_DESKTOP_NOTIFICATIONS"] = "off"
        os.environ.setdefault("ROW_BOT_AUTO_START_OLLAMA", "0")
        from row_bot import learning, models

        learning.invalidate()
        if learning.data_dir().resolve() != self.data:
            raise ScratchError(f"Row-Bot's data folder is {learning.data_dir()}, not this person's {self.data}")
        if not learning.preview_enabled():
            raise ScratchError("Start the study with ROW_BOT_LEARNING_PREVIEW=1.")
        self.kit.boot(self.data)
        self.main_ref = self.kit.teacher(self.out)
        self.sign_in = self.kit.sign_in
        self.state.data["model"] = self.main_ref.split(":", 2)[-1]
        models.adopt_saved_default(self.main_ref)
        self._tools()
        builds = self.state.data.setdefault("builds", [])
        if not builds or builds[-1].get("build") != self.build:
            builds.append({"build": self.build, "from_day": self.clock.day, "at": time.strftime("%Y-%m-%d %H:%M")})
            self.save()

    def night(self, day: int) -> None:
        saved = self.state.data["nights"].setdefault(str(day), {"steps": {}})
        self.clock.set(day + 1, live.clock_module.NIGHT_HOUR)
        saved.update({"done": True, "skipped": "no night runner during the pool (§3)", "build": self.build})
        self.save()

    def stage_rows(self) -> None:
        """The pool's rows: the product's dataset in the serving shape, measured with the engine (rows longer than
        the training length are left out), not trained on and not refreshed into the vault. Only the training
        block's own turns: the 30-day past's imported rows are kept out."""

        from row_bot.learning import capture
        from row_bot.learning.store import LearningStore
        from row_bot.learning.training import dataset

        capture.flush(60.0)
        compiled = dataset.compile_dataset(LearningStore(self.data), recipe_for(self.design),
                                           measure=self.kit.measure(self.data),
                                           workspace_roots=(str(self.workspace),), refresh_vault=False)
        by_thread = {record["thread"]: record for record in self.state.data["requests"]}
        requests = {request.id: request for request in self.history.stream}
        rows, historical, unmatched = [], 0, 0
        for example in (*compiled.train, *compiled.validation):
            if example.historical:
                historical += 1
                continue
            record = by_thread.get(example.thread_id)
            if record is None:
                unmatched += 1
                continue
            request = requests[record["id"]]
            rows.append({"id": example.id, "person": self.person_id, "request": request.id, "day": request.day,
                         "thread": example.thread_id, "started_at": example.started_at, "provider": example.provider,
                         "tokens": example.tokens if example.tokens is not None else example.estimated_tokens,
                         "measured": example.tokens is not None, "derived": example.derived, "text": request.prompt,
                         "row": example.row})
        path = self.run_dir / "rows.jsonl"
        path.unlink(missing_ok=True)
        append_lines(path, rows)
        self.state.data["rows"] = {"rows": len(rows), "past_rows_left_out": historical, "unmatched": unmatched,
                                   "left_out": dict(compiled.left_out), "measured": compiled.measured,
                                   "rendering": dict(compiled.rendering), "dataset": compiled.id}
        self.save()


def pool_main(args: argparse.Namespace, design: Design, kit: Any) -> int:
    out = Path(args.out)
    run_dir = out / "pool" / args.person
    namespace = argparse.Namespace(
        person=args.person, data=args.data, run_dir=str(run_dir), days=design.training_days[1], seed=design.seed,
        tier=design.tier, eval_profile=args.eval_profile, runtimes_from=args.runtimes_from, cap_tokens=args.cap_tokens,
        spent_before=args.spent_before, gpu_booking="", lane=args.lane, crash=False, gpu=False, pilot_compare=False,
        generations=0, generation_days=0, night_every=1, monthly_limit=live.MONTHLY_LIMIT)
    run = PoolRun(namespace, kit, out, design)
    run.boot()
    code = 0
    try:
        for stage in ("profile", "past", "days", "rows"):
            if run.state.done(stage):
                continue
            run.note(f"stage {stage}")
            getattr(run, f"stage_{stage}")()
            run.state.finish(stage)
    except SystemExit as stop:
        if stop.code != EXIT_TOKEN_CAP:
            raise
        code = EXIT_TOKEN_CAP
    (run_dir / "result.json").write_text(json.dumps(run.result(), indent=1, sort_keys=True, default=str),
                                         encoding="utf-8")
    return code


# ── train ────────────────────────────────────────────────────────────────────


class Trainer:
    """The product's training of one arm on given rows: a job with those rows leased, trained by the engine through
    Row-Bot's worker (booking the card, yielding it at a quiet hour or to the participant's training and continuing
    from its checkpoint), then made a version by the product (its passes walked back until one passes the ready
    check; none: "couldn't start")."""

    def __init__(self, data: Path, design: Design, kit: Any, state: JsonState, gpu: GpuGate) -> None:
        from row_bot.learning.lease import ResourceLease
        from row_bot.learning.training.supervisor import TrainingSupervisor

        self.data = data
        self.design = design
        self.kit = kit
        self.state = state
        self.gpu = gpu
        self.supervisor = TrainingSupervisor(root=data, engine=kit.engine(data), lease=ResourceLease(),
                                             **kit.supervisor_options())

    def start_model(self) -> dict[str, Any]:
        found = self.state.data.get("start") or {}
        if found.get("ref"):
            return found
        self.gpu.acquire("scaling study: the starting model's export", expect="10 min, 20 GB")
        try:
            ref, why = self.kit.start(self.data, recipe_for(self.design))
        finally:
            self.gpu.release()
        self.state.data["start"] = {"ref": ref, "why": why}
        self.state.save()
        if not ref:
            raise ScratchError(f"the starting model couldn't be served ({why})")
        return self.state.data["start"]

    def compiled(self, examples: Sequence[Any], recipe: Any) -> Any:
        """The rows as the product's dataset: its own split (the newest conversations validate) and identity."""

        import hashlib

        from row_bot.learning.training import dataset, shape

        train, validation = dataset._split(list(examples), float(recipe.validation_share))  # noqa: SLF001
        envelope = shape.envelope_for(int(recipe.serve_context))
        rendering = shape.describe(envelope)
        body = {"compiler": dataset.COMPILER_VERSION, "recipe": recipe.name, "max_seq_length": recipe.max_seq_length,
                "rendering": rendering.get("name"), "train": [item.row for item in train],
                "validation": [item.row for item in validation]}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                           separators=(",", ":")).encode("utf-8")).hexdigest()
        return dataset.CompiledDataset(
            id=f"ds_{digest[:32]}", sha256=digest, recipe=recipe.name, max_seq_length=recipe.max_seq_length,
            train=tuple(train), validation=tuple(validation), left_out={},
            providers=tuple(sorted({item.provider for item in examples})),
            measured=all(item.tokens is not None for item in examples), rendering=rendering)

    def train(self, arm: str, examples: Sequence[Any], recipe: Any) -> dict[str, Any]:
        from row_bot.learning.training import jobs as jobs_module
        from row_bot.learning.training import planner, releases, serving

        entry = self.state.data.setdefault("arms", {}).setdefault(arm, {"arm": arm})
        if entry.get("state") in ("ready", "couldnt_start", "failed"):
            return entry
        jobs = self.supervisor.jobs
        if not entry.get("job"):
            compiled = self.compiled(examples, recipe)
            steps = recipe.steps_for(compiled.examples)
            job = jobs.plan(steps_total=steps, checkpoint_every=recipe.checkpoint_interval(steps),
                            recipe=recipe.spec(planner._card_mib()),  # noqa: SLF001 - the planner's own card size
                            dataset={"id": compiled.id, "examples": compiled.examples})
            planner.lease(job, compiled, self.data)
            entry.update({"job": job.id, "version": job.version, "rows": len(examples), "examples": compiled.examples,
                          "validation": len(compiled.validation), "dataset": compiled.id, "steps": steps,
                          "seed": recipe.settings.get("seed"), "recipe": recipe.name, "training_seconds": 0.0})
            self.state.save()
        job = jobs.get(entry["job"])
        minutes = math.ceil(int(entry["steps"]) * design_module.SECONDS_PER_STEP / 60)
        stalled = 0
        while job is not None and job.state not in ("evaluating", "ready", "failed", "stopped", "not_used"):
            if stalled >= MAX_STALLS:
                entry.update({"state": "failed", "why": f"the training worker stopped {stalled} times without a step"})
                self.state.save()
                return entry
            self.gpu.acquire(f"scaling study: training {arm}", expect=f"{minutes} min, 21 GB")
            started, steps = time.monotonic(), int(job.steps_done or 0)
            try:
                self.supervisor.train_now(job.id)
                yielded = self._watch(job.id)
                self.supervisor.wait_idle(180)
            finally:
                entry["training_seconds"] = round(float(entry["training_seconds"]) + time.monotonic() - started, 1)
                self.state.save()
                self.gpu.release()
            job = jobs.get(job.id)
            moved = job is not None and int(job.steps_done or 0) > steps
            stalled = 0 if (moved or yielded) else stalled + 1
        if job is None or job.state in ("failed", "stopped"):
            entry.update({"state": "failed", "why": getattr(job, "error_code", None) or "the run didn't finish"})
            self.state.save()
            return entry
        if job.state == "evaluating":
            self.kit.before_release(jobs_module.run_dir(job.id, self.data))
            self.gpu.acquire(f"scaling study: making {arm} ready", expect="10 min, 21 GB")
            started = time.monotonic()
            try:
                maker = releases.ReleaseMaker(self.data, jobs=jobs, recipe_for=lambda: recipe, start_threads=False,
                                              **self.kit.maker_options(self.data))
                maker.start(job, runtime=serving.ROW_BOT)
            finally:
                entry["ready_seconds"] = round(float(entry.get("ready_seconds", 0.0)) + time.monotonic() - started, 1)
                self.gpu.release()
        version = releases.read_state(releases.version_dir(int(entry["version"]), self.data))
        ready = version is not None and version.phase == releases.READY and bool(version.model_ref)
        entry.update({"state": "ready" if ready else "couldnt_start", "model_ref": version.model_ref if version else None,
                      "phase": version.phase if version else None, "failure": version.failure if version else None,
                      "kept_pass": dict(version.kept_pass or {}) if version else {},
                      "loss": getattr(jobs.get(job.id), "loss", None)})
        if not ready:
            entry["why"] = "couldn't start: no pass passed the ready check (§8)"
        self.state.save()
        return entry

    def _watch(self, job_id: str) -> bool:
        """Until the run ends; it yields the card (stopping at its checkpoint) at a quiet hour, when the participant
        trains or another lane waits, and the loop books it again (waiting) and continues from there. True when it
        yielded."""

        from tests.e2e.learning.scaling.runner import yield_card

        while True:
            job = self.supervisor.jobs.get(job_id)
            if job is None or job.state not in ("preparing", "running", "pausing", "waiting"):
                return False
            if yield_card(self.gpu):
                self.supervisor.stop_now("local_gpu")
                return True
            time.sleep(WATCH_SECONDS)


def pool_examples(out: Path, ids: Sequence[str]) -> list[Any]:
    """The pool rows ``ids`` as the dataset's examples."""

    from row_bot.learning.training import dataset

    rows = {row["id"]: row for path in sorted((out / "pool").glob("*/rows.jsonl")) for row in read_lines(path)}
    return [dataset.Example(id=row["id"], conversation_id=row["thread"], thread_id=row["thread"],
                            started_at=float(row["started_at"]), provider=row["provider"], historical=False,
                            row=row["row"], estimated_tokens=int(row["tokens"]),
                            tokens=int(row["tokens"]) if row["measured"] else None, derived=bool(row["derived"]))
            for row in (rows[item] for item in ids)]


def subset_ids(out: Path, n: int) -> list[str]:
    return list(json.loads((out / "subsets" / f"n{n}.json").read_text(encoding="utf-8"))["ids"])


def train_main(args: argparse.Namespace, design: Design, kit: Any) -> int:
    data, out = Path(args.data), Path(args.out)
    prepare_profile(data, args)
    kit.boot(data)
    trainer = Trainer(data, design, kit, JsonState(out / "train" / "state.json"), kit.gpu("scaling study, training"))
    trainer.start_model()
    for arm in ("sft-100", "sft-300", "sft-1000", "sft-1000b", "sft-3000"):
        seed = design.second_seed if arm == "sft-1000b" else None
        trainer.train(arm, pool_examples(out, subset_ids(out, design.subset_of(arm))), recipe_for(design, seed=seed))
    return 0


# ── dagger ───────────────────────────────────────────────────────────────────


def ask_teacher(messages: Sequence[Any], tools: Sequence[Mapping[str, Any]] | None, model_ref: str,
                thread: str) -> tuple[Any, int, bool]:
    """The teacher's action at a prefix, asked as the Agent asks it (its reasoning plan for the conversation, as it
    ran in the pool), recorded in the usage ledger. Returns the message, its tokens and whether they were local."""

    from row_bot import models
    from row_bot.learning.calls import CHARS_PER_TOKEN, estimate_tokens, text_of
    from row_bot.providers.reasoning import (
        canonical_reasoning_model_ref,
        request_plan_for,
    )
    from row_bot.providers.resolution import resolve_provider_config
    from row_bot.learning.spending import is_local_provider
    from row_bot.providers.usage_ledger import with_usage_purpose

    resolved = resolve_provider_config(model_ref, allow_legacy_local=True)
    plan = request_plan_for(thread, canonical_reasoning_model_ref(resolved.provider_id, resolved.runtime_model))
    llm = models.get_llm_for(resolved.selection_ref, reasoning_plan=plan)
    message = with_usage_purpose(llm.bind_tools(list(tools)) if tools else llm, PURPOSE).invoke(list(messages))
    usage = getattr(message, "usage_metadata", None) or {}
    tokens = int(usage.get("input_tokens") or estimate_tokens(messages, tools)) + int(
        usage.get("output_tokens") or math.ceil(len(text_of(message)) / CHARS_PER_TOKEN))
    return message, tokens, is_local_provider(resolved.provider_id)


class Relabeller:
    """At each of the student's model calls in a run: the teacher's action at that prefix, as a row (``dagger.row``),
    kept when it fits the training length."""

    def __init__(self, runner: StudyRunner, teacher_ref: str, design: Design, measure: Any, state: JsonState,
                 args: argparse.Namespace) -> None:
        from row_bot.learning.training import shape

        self.runner = runner
        self.teacher_ref = teacher_ref
        self.design = design
        self.measure = measure
        self.state = state
        self.args = args
        self.recipe = recipe_for(design)
        self.envelope = shape.envelope_for(int(self.recipe.serve_context))
        self.wanted = 0
        self.rows: list[dict[str, Any]] = []
        self.counts: dict[str, int] = {}

    def __call__(self, arm: str, task: Any, thread: str) -> None:
        from langchain_core.messages import messages_from_dict

        from row_bot.learning import capture
        from row_bot.learning.store import LearningStore
        from row_bot.learning.training import dataset
        from row_bot.tools import registry

        store = LearningStore()
        root = str(registry.get_tool_config("filesystem", "workspace_root", "") or "")
        for decision in decisions(thread):
            if len(self.rows) >= self.wanted:
                return
            request = capture.read_request(store, decision)
            if request is None or request.get("purpose") != "agent":
                self._count("not_an_agent_call")
                continue
            cloud_check(self.state, self.args)
            self.runner.reset_gate(self.design.task_tokens)
            message, tokens, local = ask_teacher(messages_from_dict(list(request["messages"])), request.get("tools"),
                                                 self.teacher_ref, thread)
            self.state.data["cloud_tokens"] = int(self.state.data.get("cloud_tokens", 0)) + (0 if local else tokens)
            self.state.data["teacher_tokens"] = int(self.state.data.get("teacher_tokens", 0)) + tokens
            found = dagger.row(request, message, envelope=self.envelope, roots=(root,) if root else ())
            if found is None:
                self._count("empty_action")
                continue
            if self.measure is not None:
                length, targets = self.measure([found], self.recipe.max_seq_length)[0]
                measured = True
            else:
                length, targets, measured = dataset.estimate_tokens(found), 1, False
            if length > self.recipe.max_seq_length or targets <= 0:
                self._count("too_long" if length > self.recipe.max_seq_length else "no_target")
                continue
            self.rows.append({"id": f"dagger-{decision.id}", "decision": decision.id, "request": task.id,
                              "started_at": decision.created_at, "tokens": int(length), "measured": measured,
                              "row": found})

    def _count(self, why: str) -> None:
        self.counts[why] = self.counts.get(why, 0) + 1


def dagger_examples(rows: Sequence[Mapping[str, Any]], provider: str) -> list[Any]:
    from row_bot.learning.training import dataset

    return [dataset.Example(id=row["id"], conversation_id=f"dagger-{row['request']}", thread_id=f"dagger-{row['request']}",
                            started_at=float(row["started_at"]), provider=provider, historical=False, row=row["row"],
                            estimated_tokens=int(row["tokens"]), tokens=int(row["tokens"]) if row["measured"] else None)
            for row in rows]


def dagger_main(args: argparse.Namespace, design: Design, kit: Any) -> int:
    """Two rounds of ``design.dagger_rows`` rows, the round-1 model trained between them, then ``dagger-1000``."""

    from row_bot.providers.selection import parse_model_ref

    data, out = Path(args.data), Path(args.out)
    prepare_profile(data, args)
    kit.boot(data)
    teacher_ref = kit.teacher(out)
    state = JsonState(out / "dagger" / "state.json")
    gpu = kit.gpu("scaling study, DAgger")
    trainer = Trainer(data, design, kit, JsonState(out / "train" / "state.json"), gpu)
    start = trainer.start_model()["ref"]
    requests, owned, people = owners(design, "training")
    by_person: dict[str, list[str]] = {}
    for request in requests.values():
        by_person.setdefault(request.person, []).append(request.id)
    in_subset = {row["request"] for row in pool_rows(out, subset_ids(out, design.sizes[2]))}
    order = dagger.order(by_person, in_subset, f"{design.subset_seed}/dagger")
    provider = (parse_model_ref(teacher_ref) or ("unknown",))[0]
    runner = StudyRunner(data=data, arms={"student-1": Spec("student-1", start, local=True)}, owners=owned, gpu=gpu,
                         task_tokens=design.task_tokens, seconds=design.seconds)
    runner.boot(people)
    relabel = Relabeller(runner, teacher_ref, design, kit.measure(data), state, args)
    runner.after = relabel
    done = state.data.setdefault("requests", [])
    position = 0
    for round_number in (1, 2):
        path = out / "dagger" / f"round-{round_number}.jsonl"
        arm = f"student-{round_number}"
        if round_number == 2:
            runner.arms[arm] = Spec(arm, _round_model(trainer, out, design, provider), local=True)
            position = len([item for item in done if item["round"] == 1])
        kept = read_lines(path)
        asked = {item["request"] for item in done if item["round"] == round_number}
        rotated = order[position:] + order[:position]
        for request_id in rotated:
            if len(kept) >= design.dagger_rows:
                break
            if request_id in asked:
                continue
            relabel.wanted, relabel.rows, relabel.counts = design.dagger_rows - len(kept), [], {}
            run = runner.run_task(requests[request_id].task, arm)
            append_lines(path, relabel.rows)
            kept.extend(relabel.rows)
            done.append({"round": round_number, "request": request_id, "person": requests[request_id].person,
                         "status": run.case.status, "rows": len(relabel.rows), "left_out": relabel.counts,
                         "in_1000_subset": request_id in in_subset, "seconds": run.case.seconds})
            state.data["gpu_seconds"] = round(float(state.data.get("gpu_seconds", 0.0)) + run.case.seconds, 1)
            state.save()
        runner.finish()
        state.data[f"round_{round_number}"] = {"rows": len(kept), "requests": len([item for item in done
                                                                                  if item["round"] == round_number]),
                                              "student": runner.arms[arm].model_ref}
        state.save()
        if len(kept) < design.dagger_rows:
            raise ScratchError(f"DAgger round {round_number}: every training-block request gave {len(kept)} rows, "
                               f"fewer than {design.dagger_rows}")
    rows = read_lines(out / "dagger" / "round-1.jsonl") + read_lines(out / "dagger" / "round-2.jsonl")
    trainer.train("dagger-1000", dagger_examples(rows, provider), recipe_for(design))
    state.data["overlap_with_1000_subset"] = sum(1 for item in done if item["in_1000_subset"])
    state.data["done"] = True
    state.save()
    return 0


def _round_model(trainer: Trainer, out: Path, design: Design, provider: str) -> str:
    """The round-1 model: ``start`` trained on round 1's rows (the same recipe)."""

    entry = trainer.train(DAGGER_ROUND_1, dagger_examples(read_lines(out / "dagger" / "round-1.jsonl"), provider),
                          recipe_for(design))
    if entry.get("state") != "ready":
        raise ScratchError(f"the round-1 model couldn't start ({entry.get('why') or entry.get('failure')}): DAgger's "
                           "second round has no student")
    return str(entry["model_ref"])


def pool_rows(out: Path, ids: Sequence[str]) -> list[dict[str, Any]]:
    wanted = set(ids)
    return [row for path in sorted((out / "pool").glob("*/rows.jsonl")) for row in read_lines(path) if row["id"] in wanted]


# ── evaluate ─────────────────────────────────────────────────────────────────


def arm_specs(design: Design, kit: Any, out: Path, teacher_ref: str) -> tuple[dict[str, Spec], dict[str, str]]:
    """Every arm that can run, and why the others can't (a version that couldn't start isn't run, §8)."""

    trained = JsonState(out / "train" / "state.json").data
    start = (trained.get("start") or {}).get("ref")
    if not start:
        raise ScratchError("the train stage hasn't served the starting model")
    specs: dict[str, Spec] = {"start": Spec("start", start, local=True)}
    unavailable: dict[str, str] = {}
    for arm in design_module.TRAINED:
        entry = (trained.get("arms") or {}).get(arm) or {}
        if entry.get("state") == "ready":
            specs[arm] = Spec(arm, str(entry["model_ref"]), local=True)
        else:
            unavailable[arm] = str(entry.get("why") or entry.get("state") or "not trained")
    specs["retrieval"] = Spec("retrieval", start, local=True, retrieves=True)
    specs["local-27b"] = Spec("local-27b", kit.local_27b(design), local=True)
    specs["teacher"] = Spec("teacher", teacher_ref, local=False)
    return {arm: specs[arm] for arm in design_module.ARMS if arm in specs}, unavailable


class Evaluation:
    """Every arm on the held-out requests and the hidden pack tasks, then replay agreement (see ``study.py``)."""

    def __init__(self, args: argparse.Namespace, design: Design, kit: Any) -> None:
        self.args = args
        self.design = design
        self.kit = kit
        self.data, self.out = Path(args.data), Path(args.out)
        self.folder = self.out / "evaluate"
        self.state = JsonState(self.folder / "state.json")

    def run(self) -> int:
        from row_bot.learning.evaluation import EvaluationError, audit_size

        prepare_profile(self.data, self.args)
        self.kit.boot(self.data)
        teacher = self.kit.teacher(self.out)
        self.specs, unavailable = arm_specs(self.design, self.kit, self.out, teacher)
        self.state.data["unavailable"] = unavailable
        self.state.data["arms"] = {arm: {"model_ref": spec.model_ref, "local": spec.local}
                                   for arm, spec in self.specs.items()}
        self.state.save()
        packs = list(self.design.hidden_packs) or None
        try:
            hidden = audit_size(asked_by="scaling study: size", packs=packs, include_replay=False)
        except EvaluationError as error:
            raise ScratchError(f"the hidden pack tasks can't be counted ({error.code})") from error
        if hidden != self.design.hidden_tasks:
            raise ScratchError(f"{hidden} hidden pack tasks, not the {self.design.hidden_tasks} the study runs")
        self.requests, owned, people = owners(self.design, "held_out")
        lines = (self.out / "retrieval" / "index.jsonl").read_text(encoding="utf-8").splitlines()
        self.index = retrieval.Index.from_lines(lines)
        self.runner = StudyRunner(data=self.data, arms=self.specs, owners=owned, gpu=self.kit.gpu("scaling study, "
                                  "evaluation"), task_tokens=self.design.task_tokens, seconds=self.design.seconds,
                                  index=self.index, k=self.design.retrieval_k,
                                  unload=lambda arm: self.kit.unload(arm, self.design))
        self.runner.boot(people)
        self.runner.on_run = self._spend
        try:
            self.held_out("teacher")
            for arm in self.specs:
                if arm != "teacher":
                    self.held_out(arm)
                    self.replay(arm)
            self.replay("teacher")
            self.hidden(packs)
        finally:
            self.runner.finish()
            self._gpu()
        self.state.data["done"] = True
        self.state.save()
        return 0

    def _gpu(self) -> None:
        seconds = self.state.data.setdefault("gpu_seconds", {})
        for arm, value in self.runner.gpu_seconds.items():
            seconds[arm] = round(float(seconds.get(arm, 0.0)) + value, 1)
        self.runner.gpu_seconds.clear()
        self.state.save()

    def _spend(self, arm: str, case: Any) -> None:
        """Every run's and replay call's tokens, saved at once (a resumed evaluation counts what it spent before)."""

        cloud = max(0, int(case.tokens) - int(case.local_tokens))
        self.state.data["cloud_tokens"] = int(self.state.data.get("cloud_tokens", 0)) + cloud
        tokens = self.state.data.setdefault("tokens", {}).setdefault(arm, {"cloud": 0, "local": 0})
        tokens["cloud"] += cloud
        tokens["local"] += int(case.local_tokens)
        self.state.save()

    def held_out(self, arm: str) -> None:
        """Every held-out request ``design.runs`` times (a provider outage run once more, then kept as it is); the
        teacher's first run of each also gives its decisions as replay cases."""

        path = self.folder / "runs.jsonl"
        done = {(item["arm"], item["task"], item["repeat"]) for item in read_lines(path)}
        cases: list[dict[str, Any]] = []

        def capture_cases(_arm: str, task: Any, thread: str) -> None:
            from row_bot.learning.evaluation import collect_replay
            from row_bot.learning.store import LearningStore

            found = collect_replay(LearningStore(), refresh=False, decisions=decisions(thread), include_untrusted=True,
                                   limit=10_000)
            cases.extend({"case": case.decision_id, "request": task.id, "cluster": cluster(self.requests[task.id]),
                          "content": {**case.to_content(), "id": case.decision_id, "thread_id": case.thread_id,
                                      "conversation_id": case.conversation_id, "created_at": case.created_at,
                                      "kind_of_work": case.kind_of_work}}
                         for case in found.cases if case.reference_source == "captured" and case.check == "exact_action")

        for request_id, request in self.requests.items():
            for repeat in range(self.design.runs):
                if (arm, request_id, repeat) in done:
                    continue
                if not self.specs[arm].local:
                    cloud_check(self.state, self.args)
                reran = False
                for attempt in range(2):
                    cases.clear()
                    self.runner.after = capture_cases if arm == "teacher" and repeat == 0 else None
                    run = self.runner.run_task(request.task, arm)
                    if run.case.status != "error" or attempt:
                        break
                    reran = True
                append_lines(self.folder / "replay_cases.jsonl", cases)
                append_lines(path, [{"arm": arm, "task": request_id, "repeat": repeat, "cluster": cluster(request),
                                     **_case_fields(run.case), "reran_after_outage": reran,
                                     "approvals": run.approvals}])
                self.state.save()
        self._gpu()

    def replay(self, arm: str) -> None:
        """Replay agreement (§6): every teacher decision captured on the held-out block re-asked at its prefix,
        ``design.runs`` times, and checked by the field study's checker (METRIC_VERSION 3)."""

        from row_bot.learning import evaluation

        path = self.folder / "replay.jsonl"
        done = {(item["arm"], item["case"], item["repeat"]) for item in read_lines(path)}
        client = evaluation.LedgerModelClient()
        spec = self.specs[arm]
        serve = recipe_for(self.design).serve_context if spec.local else None
        started = time.monotonic()
        for item in read_lines(self.folder / "replay_cases.jsonl"):
            case = evaluation.ReplayCase.from_content(item["content"])
            for repeat in range(self.design.runs):
                if (arm, item["case"], repeat) in done:
                    continue
                guidance = retrieval.guidance(self.index.pick(case.text, self.design.retrieval_k)) if spec.retrieves else ()
                model = evaluation.Arm(arm, model_ref=spec.model_ref, guidance=guidance, serve_context=serve,
                                       sees_images=not spec.local)
                if not spec.local:
                    cloud_check(self.state, self.args)
                self.runner.card(arm)
                for attempt in range(2):
                    self.runner.reset_gate(self.design.task_tokens)
                    found = evaluation.run_replay_case(case, model, client=client,
                                                       budget=evaluation.Budget(task_tokens=self.design.task_tokens))
                    self._spend(arm, found)
                    if found.status != "error" or attempt:
                        break
                append_lines(path, [{"arm": arm, "case": item["case"], "repeat": repeat, "cluster": item["cluster"],
                                     **_case_fields(found)}])
                self.state.save()
        if spec.local:
            seconds = self.state.data.setdefault("gpu_seconds", {})
            seconds[arm] = round(float(seconds.get(arm, 0.0)) + time.monotonic() - started, 1)
            self.state.save()

    def hidden(self, packs: list[str] | None) -> None:
        """The packs' hidden tasks through the vault, every arm at once (``start`` first: each arm's paired analysis
        is against it), arm after arm. Their results are kept per run (the vault's result cache), so a resumed
        evaluation runs only what is left; a second pass runs each outage once more."""

        from row_bot.learning import evaluation
        from row_bot.learning.spending import TokenMeter
        from tests.e2e.learning.scaling.runner import RETRIEVAL_MARK

        if (self.state.data.get("hidden") or {}).get("done"):
            return
        import hashlib

        index_mark = hashlib.sha256((self.out / "retrieval" / "index.jsonl").read_bytes()).hexdigest()[:16]
        arms = [evaluation.Arm(arm, model_ref=spec.model_ref, cache=True,
                               ways=((RETRIEVAL_MARK, f"index {index_mark}"),) if spec.retrieves else ())
                for arm, spec in self.specs.items()]
        cache = evaluation.ArmCache(self.data)
        comparison = None
        for _pass in range(2):
            spent = int(self.args.spent_before) + int(self.state.data.get("cloud_tokens", 0))
            meter = TokenMeter(max(0, int(self.args.cap_tokens) - spent))
            comparison = evaluation.audit_check(
                arms, asked_by="scaling study: transfer", runner=self.runner, cache=cache, meter=meter, packs=packs,
                include_replay=False, budget=evaluation.Budget(task_tokens=self.design.task_tokens),
                repeats=self.design.runs, seed=self.design.analysis_seed, save=False, order="model")
            self._gpu()
            if comparison.stopped:
                self.state.data["hidden"] = {"stopped": comparison.stopped}
                self.state.save()
                raise SystemExit(EXIT_TOKEN_CAP)
        overall = (comparison.analysis.get("arms") or {}) if comparison is not None else {}
        self.state.data["hidden"] = {"done": True, "arms": {
            item.arm.name: {"passed": item.passed, "total": item.total, "by_kind": {key: list(value) for key, value in
                                                                                   item.by_kind.items()},
                            "causes": dict(item.causes), "tokens": item.measured_tokens,
                            "cloud_tokens": item.measured_cloud_tokens, "seconds": item.seconds,
                            "against_start": (overall.get(item.arm.name) or {}).get("overall")}
            for item in comparison.arms}}
        self.state.save()


def _case_fields(case: Any) -> dict[str, Any]:
    return {"status": case.status, "cause": case.cause, "detail": case.detail, "tokens": case.tokens,
            "local_tokens": case.local_tokens, "steps": case.steps, "seconds": case.seconds, "guarded": case.guarded}


def evaluate_main(args: argparse.Namespace, design: Design, kit: Any) -> int:
    return Evaluation(args, design, kit).run()


# ── Entry ────────────────────────────────────────────────────────────────────

TASKS = {"pool": pool_main, "train": train_main, "dagger": dagger_main, "evaluate": evaluate_main}


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One task of the scaling study (started by study.py)")
    parser.add_argument("task", choices=sorted(TASKS))
    parser.add_argument("--out", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--person", default="")
    parser.add_argument("--eval-profile", default="")
    parser.add_argument("--runtimes-from", default="")
    parser.add_argument("--gpu-booking", default="")
    parser.add_argument("--lane", required=True)
    parser.add_argument("--cap-tokens", type=int, required=True)
    parser.add_argument("--spent-before", type=int, default=0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    args = parse(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    out = Path(args.out)
    check_scratch(Path(args.data), Path(args.eval_profile) if args.eval_profile else None)
    check_scratch(out, Path(args.eval_profile) if args.eval_profile else None)
    design = Design.from_dict(json.loads((out / "design.json").read_text(encoding="utf-8")))
    try:
        return TASKS[args.task](args, design, kit_for(args, design))
    except SystemExit as stop:
        if stop.code in (EXIT_TOKEN_CAP, EXIT_PROVIDER_LIMIT):
            return int(stop.code)
        raise
    except ProviderLimit:
        logger.warning("the ChatGPT lane answered with a usage or rate limit: stopping at once")
        return EXIT_PROVIDER_LIMIT


if __name__ == "__main__":
    sys.exit(main())
