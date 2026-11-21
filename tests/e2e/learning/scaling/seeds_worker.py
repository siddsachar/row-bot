"""The training-seed study's child processes (``seeds.py``), one at a time, each in its own scratch profile
``<scratch>/<child>/data``.

Row-Bot binds its data folder when it is imported, so ``seeds.py`` starts this module with ``ROW_BOT_DATA_DIR`` set to
that profile:

- ``train``: the training engine, Row-Bot's llama.cpp server and the starting model's weights hard-linked from the
  study's profile (:func:`link_runtimes`: never the server's ``run`` folder, which it rewrites in place, nor the
  engine's caches), the starting model served as the study served it (``worker.Trainer.start_model``), then in §4's
  order ``sft-c`` and the two DAgger models (:class:`SeedTraining`). It trains with the study's own code
  (``worker.Trainer``, ``worker.Relabeller``, ``worker.dagger_examples``, ``dagger.row``), changing the recipe's
  training seed only; the versions are numbered after the study's.
- ``local``: Row-Bot's llama.cpp server files hard-linked from the study's profile (``explore_worker.link_server``),
  then for each arm in §4's evaluation order: its file's hash checked against ``models.json``, its model alone on this
  profile's server list at 65,536 tokens under its own name, the study's small-context prompt kept
  (``replicate_worker.set_envelope``), and every fresh request ``runs`` times (:class:`SeedsRuns`, the replication's
  ``ArmRuns``). It never touches the eval profile.

A run is the study's (``runner.StudyRunner``), recorded as the replication records it (SA2, a model-call error rerun
once, its unrecovered calls), saved after every run; a child started again continues from its own state and runs. The
card follows the replication's rule (``replicate_worker.gives_back``) between runs and between training steps, a
rollout's or an arm's model unloaded first, and takes no new GPU work from 00:25 to 08:00
(``explore_worker.new_work_quiet``). Before every relabelling call and every run the study's ChatGPT-lane tokens are
checked against the cap (exit 75); a usage or rate limit stops at once (exit 76). Its kits are :class:`TrainKit` and
:class:`EvalKit`, or :class:`DryTrainKit` and :class:`DryEvalKit` for the dry run (the scripted model on loopback,
ports 8150-8159, and the fake trainer).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.e2e.learning.scaling import dagger, dry_run, explore_worker, replicate_worker, worker
from tests.e2e.learning.scaling.design import Design
from tests.e2e.learning.scaling.explore import read_lines
from tests.e2e.learning.scaling.runner import ProviderLimit, Spec, StudyRunner
from tests.e2e.learning.scaling.seeds import (
    DAGGER,
    EVALUATE_ORDER,
    LABEL,
    LANE,
    NEW,
    SeedsDesign,
    arm_models,
    check_models,
)
from tests.e2e.learning.scripted_model import ScriptedModel
from tests.e2e.learning.seeded.scratch import GpuGate, ScratchError, check_scratch

logger = logging.getLogger("row_bot.scaling")

EXIT_TOKEN_CAP = worker.EXIT_TOKEN_CAP
EXIT_PROVIDER_LIMIT = worker.EXIT_PROVIDER_LIMIT
EXIT_REFUSED = 2
#: The dry run's loopback ports.
PORTS = range(8150, 8160)
#: What the training profile links from the study's: never the server's own ``run`` folder (its list, presets, log and
#: home, rewritten in place) nor the engine's compile caches.
NOT_LINKED = (("cache",), ("server", explore_worker.SERVER_RUN_FOLDER))
LINKED_MARKER = ".seeds-linked"


def model_name(model_ref: str) -> str:
    """The name a model ref serves under on Row-Bot's server."""

    return str(model_ref).rsplit(":", 1)[-1]


# ── The card ─────────────────────────────────────────────────────────────────


class SeedsRunner(StudyRunner):
    """The study's runner, booking the card as this study's lane and giving it back by the replication's rule
    (``replicate_worker.gives_back``) with its model unloaded first."""

    def card(self, arm: str) -> None:
        if self._arm and self._arm != arm:
            self.unload(self._arm)
        self._arm = arm
        if not self.arms[arm].local:
            self.gpu.release()
            return
        if self.gpu.held is not None and replicate_worker.gives_back(self.gpu):
            # The card goes back empty (see replicate_worker.ReplicationRunner); the server loads the model again for
            # the next run.
            self.unload(arm)
            self.gpu.release()
        self.gpu.acquire(f"seeds: {arm} runs",
                         expect="about 2 h, one arm's runs (the card goes back between runs when asked), up to 17 GB")


class SeedTrainer(worker.Trainer):
    """The study's trainer, its versions numbered from ``first_version`` (after the study's), giving the card back
    between training steps by the replication's rule (another lane's open EXCLUSIVE window too)."""

    def __init__(self, *args: Any, first_version: int, **options: Any) -> None:
        super().__init__(*args, **options)
        self.first_version = int(first_version)

    def next_version(self) -> int:
        """The next version number: after the study's, the profile's jobs and every model this study planned."""

        planned = [int(entry.get("version") or 0) + 1 for entry in (self.state.data.get("arms") or {}).values()]
        return max([self.first_version, int(self.supervisor.jobs.next_version()), *planned])

    def _watch(self, job_id: str) -> bool:
        while True:
            job = self.supervisor.jobs.get(job_id)
            if job is None or job.state not in ("preparing", "running", "pausing", "waiting"):
                return False
            if replicate_worker.gives_back(self.gpu):
                self.supervisor.stop_now("local_gpu")
                return True
            time.sleep(worker.WATCH_SECONDS)


# ── The training profile ─────────────────────────────────────────────────────


def link_runtimes(source_data: Path, data: Path) -> None:
    """The training engine, Row-Bot's llama.cpp server and the downloaded weights from ``source_data`` into ``data``:
    hard links (nothing in the source is written; a file this run rewrites is a copy), small records copied, never the
    server's ``run`` folder or the engine's caches; the marker last, so a stopped link continues."""

    source = source_data / "runtimes" / "learning"
    target = data / "runtimes" / "learning"
    if (target / LINKED_MARKER).is_file():
        return
    if not source.is_dir():
        raise ScratchError(f"{source} holds no training engine")
    for item in sorted(source.rglob("*")):
        relative = item.relative_to(source)
        if any(relative.parts[: len(skip)] == skip for skip in NOT_LINKED):
            continue
        destination = target / relative
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            continue
        if item.suffix == ".json":
            shutil.copy2(item, destination)
            continue
        try:
            os.link(item, destination)
        except OSError:
            shutil.copy2(item, destination)
    target.mkdir(parents=True, exist_ok=True)
    (target / LINKED_MARKER).write_text(str(source), encoding="utf-8")


# ── Kits ─────────────────────────────────────────────────────────────────────


def _gate(args: argparse.Namespace, label: str) -> GpuGate:
    return GpuGate(Path(args.gpu_booking) if args.gpu_booking else None, args.lane, label=label,
                   quiet=explore_worker.new_work_quiet)


class TrainKit(worker.LiveKit):
    """The study's own training kit (the eval profile's sign-in for the teacher, the installed engine, Row-Bot's own
    server), with the engine linked from the study's profile, a model unloaded on this profile's server by name, and
    the card taking no new work from 00:25 to 08:00."""

    def prepare(self, study_data: Path) -> None:
        from row_bot.learning.training import engine

        link_runtimes(study_data, Path(self.args.data))
        if not engine.status(Path(self.args.data), refresh=True).installed:
            raise ScratchError(f"{study_data} holds no installed training engine to link")

    def unload_model(self, name: str) -> None:
        explore_worker.LiveKit(self.args).unload(name)

    def finish(self) -> None:
        from row_bot.learning.training import llama_server

        llama_server.shutdown()

    def gpu(self, label: str = LABEL) -> GpuGate:
        return _gate(self.args, label)


class EvalKit(replicate_worker.LiveKit):
    """The replication's local kit (Row-Bot's own llama.cpp server in this profile, an arm served alone at its
    context), booking the card as this study."""

    def gpu(self) -> GpuGate:
        return _gate(self.args, LABEL)


class DryModel(ScriptedModel):
    """The scripted model on the first free port of 8150-8159 (and no other)."""

    def __init__(self) -> None:  # never binds port 0 first, as ScriptedModel's own would
        self.lock = threading.Lock()
        self.expression = "1 + 1"
        self.requests = 0
        self.agent_requests = 0
        self.tools_seen: set[str] = set()
        for port in PORTS:
            try:
                self.server = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
                break
            except OSError:
                continue
        else:
            raise OSError("no free port in 8150-8159")
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)


class DryTrainKit(dry_run.DryKit):
    """The study's dry-run kit (the fake trainer behind the real worker protocol, fake exports and serving, the
    scripted model as the teacher, ``start`` and every version), on this study's ports."""

    def boot(self, data: Path) -> None:
        if self.model is not None:
            return
        from row_bot.providers import custom

        self.model = DryModel().__enter__()
        for endpoint in custom.list_custom_endpoints():  # a resumed child: its endpoints on this process's port
            if str(endpoint.get("id") or "").startswith(dry_run.PREFIX):
                self._endpoint(str(endpoint["id"])[len(dry_run.PREFIX):])

    def prepare(self, study_data: Path) -> None:
        return None

    def unload_model(self, name: str) -> None:
        return None

    def finish(self) -> None:
        return None

    def gpu(self, label: str = LABEL) -> GpuGate:
        return super().gpu(label)


class DryEvalKit(replicate_worker.DryKit):
    """The replication's dry local kit (every arm its own endpoint declared at its context), on this study's ports."""

    def boot(self, data: Path) -> None:
        if self.model is None:
            self.model = DryModel().__enter__()

    def gpu(self) -> GpuGate:  # type: ignore[override]
        return dry_run.DryKit.gpu(self, LABEL)


def kit_for(args: argparse.Namespace, design: SeedsDesign) -> Any:
    if args.task == "train":
        return DryTrainKit(args) if design.dry_run else TrainKit(args)
    return DryEvalKit(args) if design.dry_run else EvalKit(args)


# ── train ────────────────────────────────────────────────────────────────────


class SeedTraining:
    """The new models in §4's order with the study's own training and DAgger code, only the training seed changed
    (see the module's docstring). Its state is ``<out>/train/state.json`` (the trainer's own: every model trained, and
    which one each arm is) and, per DAgger model, ``<out>/train/<arm>/`` (its rounds' rows and requests)."""

    def __init__(self, args: argparse.Namespace, kit: Any) -> None:
        self.args = args
        self.kit = kit
        self.data, self.out = Path(args.data), Path(args.out)
        saved = json.loads((self.out / "design.json").read_text(encoding="utf-8"))
        self.design = SeedsDesign.from_dict(saved["seeds"])
        self.study = Design.from_dict(saved["study_design"])
        self.study_out, self.study_data = Path(saved["study_out"]), Path(saved["study_data"])
        self.folder = self.out / "train"
        self.state = worker.JsonState(self.folder / "state.json")
        self.trainer: Any = None
        self.runner: Any = None
        self.teacher_ref = ""

    # ── The study's records ─────────────────────────────────────────────

    def study_arms(self) -> dict[str, Any]:
        return worker.JsonState(self.study_out / "train" / "state.json").data.get("arms") or {}

    def first_version(self) -> int:
        """The first version this study makes: after every version of the study's."""

        return max([int(entry.get("version") or 0) for entry in self.study_arms().values()] or [0]) + 1

    def sft_rows(self) -> list[dict[str, Any]]:
        """The study's 1,000-target subset's rows, as ``sft-1000`` and ``sft-1000b`` trained on them."""

        return worker.subset_rows(self.study_out, self.study.sizes[2])

    def dagger_sequences(self, requests: Mapping[str, Any]) -> tuple[dict[int, list[str]], dict[int, list[str]]]:
        """Each round's requests in the order it runs them: the study's own of that round (checked to be what its order
        gives: round 1 from the order's start, round 2 after it), then those neither round ran, in the same order. And
        the study's per round."""

        by_person: dict[str, list[str]] = {}
        for request in requests.values():
            by_person.setdefault(request.person, []).append(request.id)
        in_subset = {row["request"] for row in self.sft_rows()}
        order = dagger.order(by_person, in_subset, f"{self.study.subset_seed}/dagger")
        recorded = worker.JsonState(self.study_out / "dagger" / "state.json").data.get("requests") or []
        theirs = {number: [item["request"] for item in recorded if item.get("round") == number] for number in (1, 2)}
        first = len(theirs[1])
        if not theirs[1] or theirs[1] != order[:first] or theirs[2] != order[first:first + len(theirs[2])]:
            raise ScratchError("the study's DAgger rounds aren't the requests its order gives "
                               f"({self.study_out / 'dagger' / 'state.json'}): not the study's procedure")
        rest = [item for item in order if item not in set(theirs[1]) | set(theirs[2])]
        return {number: theirs[number] + rest for number in (1, 2)}, theirs

    # ── Running ─────────────────────────────────────────────────────────

    def run(self) -> int:
        worker.prepare_profile(self.data, SimpleNamespace(runtimes_from=""))
        self.kit.prepare(self.study_data)
        self.kit.boot(self.data)
        gpu = self.kit.gpu(LABEL)
        self.trainer = SeedTrainer(self.data, self.study, self.kit, self.state, gpu, first_version=self.first_version())
        try:
            self.trainer.start_model()
            for arm in NEW:  # §4's order
                if arm in DAGGER:
                    self.dagger(arm)
                else:
                    self.sft(arm)
        finally:
            if self.runner is not None:
                self.runner.finish()
            gpu.release()
            self.kit.finish()
        self.state.data["done"] = True
        self.state.save()
        return 0

    def model(self, arm: str, key: str, examples: Sequence[Any]) -> dict[str, Any]:
        """``key`` (the arm, or its round-1 student) trained with the arm's seed (:meth:`trained`, a crashed run started
        again once). §4: one that fails the product's ready check is reported and retrained once with the retraining
        seed; a second failure of the same model stops the study."""

        seeds = {key: self.design.seed_of(arm), f"{key}@{self.design.retrain_seed}": self.design.retrain_seed}
        chosen = self.state.data.setdefault("models", {})
        entry: dict[str, Any] = {}
        for attempt, (name, seed) in enumerate(seeds.items()):
            run, entry = self.trained(name, seed, examples)
            if entry.get("state") == "ready":
                chosen[key] = run
                self.state.save()
                return entry
            if attempt == 0 and key not in (self.state.data.get("retrained") or {}):
                self.state.data.setdefault("retrained", {})[key] = {
                    "why": entry.get("why") or entry.get("failure"), "seed": self.design.retrain_seed,
                    "as": f"{key}@{self.design.retrain_seed}", "at": time.strftime("%Y-%m-%d %H:%M:%S")}
                self.state.save()
                logger.warning("%s couldn't start; §4 retrains it once with seed %s", key, self.design.retrain_seed)
        raise ScratchError(f"{key}: retrained once with seed {self.design.retrain_seed}, it still couldn't start "
                           f"({entry.get('why') or entry.get('failure')}); §4 retrains once, so the study stops")

    def trained(self, name: str, seed: int, examples: Sequence[Any]) -> tuple[str, dict[str, Any]]:
        """``name`` trained with ``seed``: the run's name and its entry (ready, or it couldn't start). §4: a training run
        that crashed before it made a model is started again once with the same seed (``<name>+restart``; not a
        retrain, logged); a second crash stops the study."""

        restart = f"{name}+restart"
        for run in (name, restart):
            entry = self.trainer.train(run, examples, worker.recipe_for(self.study, seed=seed),
                                       version=self.trainer.next_version())
            if entry.get("state") != "failed":
                return run, entry
            if run == name and name not in (self.state.data.get("restarted") or {}):
                self.state.data.setdefault("restarted", {})[name] = {
                    "why": entry.get("why"), "seed": seed, "as": restart, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
                self.state.save()
                logger.warning("%s's training run crashed (%s); §4 starts it again once with seed %s", name,
                               entry.get("why"), seed)
        raise ScratchError(f"{name}: its training run crashed again after §4's one restart with seed {seed} "
                           f"({entry.get('why')}); the study stops")

    def sft(self, arm: str) -> dict[str, Any]:
        """The study's 1,000-target subset with the study's recipe and this arm's seed; its compiled dataset must be
        the one ``sft-1000`` trained on."""

        examples = worker.examples_of(self.sft_rows())
        wanted = (self.study_arms().get("sft-1000") or {}).get("dataset")
        compiled = self.trainer.compiled(examples, worker.recipe_for(self.study, seed=self.design.seed_of(arm)))
        if compiled.id != wanted:
            raise ScratchError(f"{arm}'s compiled dataset is {compiled.id}, not the study's sft-1000 {wanted}: not the "
                               "same subset")
        self.state.data.setdefault("datasets", {})[arm] = compiled.id
        self.state.save()
        return self.model(arm, arm, examples)

    def dagger(self, arm: str) -> dict[str, Any]:
        """The study's DAgger with this arm's seed: two rounds (``round``), the round-1 model trained between them,
        then the arm's model on both rounds' rows."""

        from row_bot.providers.selection import parse_model_ref

        folder = self.folder / arm
        state = worker.JsonState(folder / "state.json")
        if state.data.get("done"):
            return (self.state.data.get("arms") or {}).get((self.state.data.get("models") or {}).get(arm, "")) or {}
        if not self.teacher_ref:
            self.teacher_ref = self.kit.teacher(self.out)
        start = self.trainer.start_model()["ref"]
        requests, owned, people = worker.owners(self.study, "training")
        sequences, theirs = self.dagger_sequences(requests)
        if self.runner is None:  # once per process: it patches the runtime
            self.runner = SeedsRunner(data=self.data, arms={}, owners=owned, gpu=self.trainer.gpu,
                                      task_tokens=self.study.task_tokens, seconds=self.study.seconds,
                                      unload=lambda which: self.kit.unload_model(
                                          model_name(self.runner.arms[which].model_ref)))
            self.runner.boot(people)
        self.runner.arms = {"student-1": Spec("student-1", start, local=True)}
        others = sum(int(worker.JsonState(self.folder / other / "state.json").data.get("cloud_tokens") or 0)
                     for other in DAGGER if other != arm and other in NEW)
        budget = SimpleNamespace(cap_tokens=self.args.cap_tokens, spent_before=int(self.args.spent_before) + others)
        relabel = worker.Relabeller(self.runner, self.teacher_ref, self.study, self.kit.measure(self.data), state, budget)
        self.runner.after = relabel
        provider = (parse_model_ref(self.teacher_ref) or ("unknown",))[0]
        state.data.update({"seed": self.design.seed_of(arm), "teacher": self.teacher_ref,
                           "the_studys_requests": {str(number): len(theirs[number]) for number in (1, 2)}})
        state.save()
        for number in (1, 2):
            if number == 2:
                student = self.model(arm, f"{arm}-r1",
                                     worker.dagger_examples(read_lines(folder / "round-1.jsonl"), provider))
                self.runner.arms["student-2"] = Spec("student-2", str(student["model_ref"]), local=True)
            self.round(number, folder, state, relabel, requests, sequences[number], set(theirs[number]))
        rows = read_lines(folder / "round-1.jsonl") + read_lines(folder / "round-2.jsonl")
        entry = self.model(arm, arm, worker.dagger_examples(rows, provider))
        state.data["done"] = True
        state.save()
        self.runner.after = None
        return entry

    def round(self, number: int, folder: Path, state: Any, relabel: Any, requests: Mapping[str, Any],
              sequence: Sequence[str], theirs: set[str]) -> None:
        """One round as the study ran it (``worker.dagger_main``): each request's run by the round's student, its
        model calls relabelled until the round holds ``dagger_targets`` rows, a request run once across both rounds."""

        path = folder / f"round-{number}.jsonl"
        arm = f"student-{number}"
        target = int(self.study.dagger_targets)
        kept = read_lines(path)
        done = state.data.setdefault("requests", [])
        used = {item["request"] for item in done}
        for request_id in sequence:
            if len(kept) >= target:
                break
            if request_id in used:
                continue
            relabel.wanted, relabel.rows, relabel.counts = target - len(kept), [], {}
            run = self.runner.run_task(requests[request_id].task, arm)
            worker.append_lines(path, relabel.rows)
            kept.extend(relabel.rows)
            used.add(request_id)
            done.append({"round": number, "request": request_id, "person": requests[request_id].person,
                         "status": run.case.status, "rows": len(relabel.rows), "left_out": relabel.counts,
                         "the_studys": request_id in theirs, "seconds": run.case.seconds})
            state.data["gpu_seconds"] = round(float(state.data.get("gpu_seconds", 0.0)) + run.case.seconds, 1)
            state.save()
        self.runner.finish()
        ran = [item["request"] for item in done if item["round"] == number]
        state.data[f"round_{number}"] = {"rows": len(kept), "requests": len(ran),
                                         "student": self.runner.arms[arm].model_ref,
                                         "the_studys_requests": len(theirs), "same": len(set(ran) & theirs),
                                         "not_run": len(theirs - set(ran)), "added": len(set(ran) - theirs)}
        state.save()
        if len(kept) < target:
            raise ScratchError(f"DAgger round {number}: every training-block request gave {len(kept)} rows, fewer "
                               f"than {target}")


# ── local ────────────────────────────────────────────────────────────────────


class SeedsRuns(replicate_worker.ArmRuns):
    """The six arms in §4's evaluation order: every fresh request ``runs`` times per arm, as the replication runs a
    local arm (``ArmRuns``), saved after each run. Its state and runs are under ``<out>/local``."""

    def __init__(self, args: argparse.Namespace, kit: Any, hasher: Callable[[Path], str] = replicate_worker.sha256_file,
                 ) -> None:
        self.args = args
        self.kit = kit
        self.task = "local"
        self.hasher = hasher
        self.data, self.out = Path(args.data), Path(args.out)
        saved = json.loads((self.out / "design.json").read_text(encoding="utf-8"))
        self.design = SeedsDesign.from_dict(saved["seeds"])
        self.study_out, self.study_data = Path(saved["study_out"]), Path(saved["study_data"])
        self.train_data = Path(saved["train_data"])
        self.folder = self.out / "local"
        self.state = worker.JsonState(self.folder / "state.json")
        self.runner: Any = None
        self.names: dict[str, str] = {}

    def start(self, arms: Mapping[str, Spec], gpu: GpuGate) -> tuple[dict[str, Any], list[str]]:
        """The fresh requests and the runner (once per process, after the profile is prepared)."""

        requests, owned, people = worker.owners(self.design.scaling(), "held_out")
        self.runner = SeedsRunner(data=self.data, arms=arms, owners=owned, gpu=gpu, task_tokens=self.design.task_tokens,
                                  seconds=self.design.seconds)
        self.runner.boot(people)
        self.runner.on_run = self.spend
        self.state.data.update({"tasks": len(requests), "runs_per_task": self.design.runs})
        self.state.save()
        return requests, list(requests)

    def local(self) -> int:
        """The six arms in §4's order, each served at 65,536 tokens just before its runs."""

        if not worker.JsonState(self.out / "train" / "state.json").data.get("done"):
            raise ScratchError("the arms are evaluated after the new models are trained (§4): training hasn't finished")
        models = arm_models(self.out, self.study_out, self.study_data, self.train_data)
        worker.prepare_profile(self.data, SimpleNamespace(runtimes_from=""))
        self.kit.prepare(self.study_data)
        self.kit.boot(self.data)
        gpu = self.kit.gpu()
        requests, ids = self.start({}, gpu)
        try:
            for arm in EVALUATE_ORDER:
                if not self.remaining(arm, ids):
                    continue
                self.arm(arm, models[arm], gpu)
                self.held_out(arm, requests, ids)
        finally:
            self.runner.finish()
            self.gpu()
            self.kit.finish()
        self.state.data["done"] = True
        self.state.save()
        return 0

    def arm(self, arm: str, model: tuple[str, str, Path], gpu: GpuGate) -> None:
        """Before ``arm``'s runs: its file's hash checked, the last arm's model unloaded, its model served alone at the
        study's context, the envelope set, and Row-Bot seeing that window with the small-context profile."""

        context = int(self.design.context)
        check_models(self.out, {arm: model[2]}, before=f"serving {arm}", hasher=self.hasher)
        self.runner.finish()
        started = time.monotonic()
        served = self.kit.serve_arm(arm, model, context, gpu)
        serving = self.state.data.setdefault("serving_seconds", {})
        serving[arm] = round(float(serving.get(arm, 0.0)) + time.monotonic() - started, 1)
        threshold = replicate_worker.set_envelope(context)
        self.runner.arms[arm] = Spec(arm, str(served["model_ref"]), local=True)
        self.names[arm] = str(served["name"])
        self.runner.unload = lambda which: self.kit.unload(self.names[which])
        size, small = replicate_worker.sees(served["model_ref"])
        served.update({"effective_context": size, "small_profile": small, "small_profile_tokens": threshold})
        self.state.data.setdefault("serving", {})[arm] = served
        self.state.save()
        if size != context or not small:
            raise ScratchError(f"{arm}: Row-Bot sees a {size:,}-token window (small-context profile {small}), not "
                               f"{context:,} with the study's small-context profile")


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One child of the training-seed study (started by seeds.py)")
    parser.add_argument("task", choices=("train", "local"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--eval-profile", default="")
    parser.add_argument("--gpu-booking", default="")
    parser.add_argument("--lane", default=LANE)
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
    profile = Path(args.eval_profile) if args.eval_profile else None
    check_scratch(Path(args.data), profile)
    check_scratch(Path(args.out), profile)
    design = SeedsDesign.from_dict(json.loads((Path(args.out) / "design.json").read_text(encoding="utf-8"))["seeds"])
    if args.task == "local" and args.eval_profile:
        logger.error("seeds local refused: the evaluation never touches the eval profile")
        return EXIT_REFUSED
    kit = kit_for(args, design)
    try:
        return SeedTraining(args, kit).run() if args.task == "train" else SeedsRuns(args, kit).local()
    except SystemExit as stop:
        if stop.code in (EXIT_TOKEN_CAP, EXIT_PROVIDER_LIMIT):
            return int(stop.code)
        raise
    except ProviderLimit:
        logger.warning("the ChatGPT lane answered with a usage or rate limit: stopping at once")
        return EXIT_PROVIDER_LIMIT
    except ScratchError as error:
        logger.error("seeds %s refused: %s", args.task, error)
        return EXIT_REFUSED


__all__ = ["DryEvalKit", "DryModel", "DryTrainKit", "EvalKit", "SeedTrainer", "SeedTraining", "SeedsRunner",
           "SeedsRuns", "TrainKit", "kit_for", "link_runtimes", "main", "model_name"]


if __name__ == "__main__":
    sys.exit(main())
