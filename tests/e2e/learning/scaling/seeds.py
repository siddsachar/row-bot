"""The training-seed study (``paper/PREREG-SEEDS.md``, version 1): does on-policy relabelling's lead over plain
fine-tuning survive retraining? Three training seeds of each method, served at 65,536 tokens, on **fresh held-out
tasks**: simulated days 371-450 of the same three seeded people, 485 requests in 240 person-days.

    python -m tests.e2e.learning.scaling.seeds --plan --out <folder> --scratch <folder>
    python -m tests.e2e.learning.scaling.seeds --study-out <study>/out --study-data <study>/scratch/study/data \\
        --eval-profile <folder> --gpu-booking <file> [--lane lane-paper] [--cap-tokens N] \\
        --out <folder> --scratch <folder> train evaluate analyse
    python -m tests.e2e.learning.scaling.seeds --dry-run --out <folder> --scratch <folder>

**Arms** (§4), each served at 65,536 tokens with the study's small-context prompt kept (the replication's ``--envelope
study``), each running every fresh request 3 times:

- ``sft-a``, ``sft-b``, ``dagger-a``: the study's own files, never retrained: ``sft-1000`` (version 3, the recipe's seed
  3407), ``sft-1000b`` (version 4, the study's ``second_seed``, 3408) and ``dagger-1000`` (version 7, 3407);
- ``sft-c``: the study's 1,000-target subset (seed 20261106) with the study's recipe, its training seed 20261121; its
  compiled dataset must be the study's ``sft-1000`` one (the same id), else it refuses;
- ``dagger-b``, ``dagger-c``: the study's DAgger (``worker.Relabeller``, ``dagger.row``, two rounds of 500 rows, the
  round-1 student ``start``, the round-2 student the round-1 model), training seeds 20261122 and 20261123 for the round-1
  model and the final one alike. Each round runs **the study's own requests of that round first**, in the study's
  order (checked to be what ``dagger.order`` gives), and stops at 500 rows as the study did; a round whose requests give
  fewer rows goes on with the requests neither of the study's rounds ran, in the same order. What each round ran beside
  the study's is recorded (the same, not run, added).

**Ready check** (§4): every new model, round-1 models included, passes the product's ready check before it is used;
one that fails is reported and retrained once with seed 20261124, and a second failure of the same model stops the
study. A training run that crashes before it makes a model is started again once with the same seed (not a retrain;
logged); a second crash of the same model stops the study. The new versions are numbered after the study's (8 on), so
no served name is the study's.

**Order** (§4, fixed): train ``sft-c``, ``dagger-b``, ``dagger-c``; then evaluate ``dagger-a``, ``sft-a``, ``dagger-b``,
``sft-b``, ``dagger-c``, ``sft-c``, each arm in full before the next.

**Stages** run only when named, each continuing from its own state under ``--out``:

- ``train``: the study's three model files' SHA-256 logged, then a child process (``seeds_worker.py train``) in its own
  scratch profile ``<scratch>/train/data`` (the training engine, Row-Bot's llama.cpp server and the starting model's
  weights hard-linked from the study's profile, never its server's ``run`` folder or caches): the starting model, then
  the three new models; their files' SHA-256 logged when they are ready. It uses the eval profile's ChatGPT sign-in for
  DAgger's teacher (the newest Sol the catalog lists, logged in ``teacher.json``).
- ``evaluate``: every model file checked against its logged hash, then a child (``seeds_worker.py local``) in
  ``<scratch>/local/data`` runs the six arms in Row-Bot's own llama.cpp server, one model loaded at a time, each file
  checked again before it is served. It never touches the eval profile.
- ``analyse``: T1 and the secondaries (``seeds_analysis.py``), only once every arm has finished (§8).

**The card** (§8): booked in the booking file as ``lane-paper``; no new GPU work from 00:25 to 08:00
(``explore_worker.new_work_quiet``); between runs and training steps it goes back to the participant's own training,
to a lane that waits and to another lane's open EXCLUSIVE window (``replicate_worker.gives_back``), a rollout's or an
arm's model unloaded first.

``--plan`` prints the fresh block, the arms, the order, the runs and the estimates from the generator alone: it reads
no folder, starts nothing and reaches nothing. ``--dry-run`` makes a made-up finished study (stand-in GGUFs, a made-up
pool and the study's own subsets and DAgger order) and runs every stage on one person and three fresh days, one run
each, with the scripted model on loopback (ports 8150-8159) and the fake trainer: no GPU, no network.

**Pinned before the first run** (§8, §11), refused afterwards when they change: ``build.json`` (the commit, the code
hash, the design, this pre-registration's hash, the study it reads); ``fresh.json`` (every fresh request with its
repeated-wording flag, after checking that generating to day 450 leaves days 1-370 as generating to day 370 made them);
``models.json`` (every model file's SHA-256, logged before the first evaluation run). While the pre-registration is a
draft (version 0) a real stage refuses: only ``--plan`` and ``--dry-run`` run.

It writes only under ``--out`` and ``--scratch`` (refusing the everyday profile, its backups, the eval profile, a
checkout and the study's own folders) and reads the study's folders without writing there. One ``--cap-tokens``
(default 150M ChatGPT-lane tokens, §8) covers both children: a child stops cleanly at the cap (exit 75) and at once on a
usage or rate limit (exit 76); the study then stops, says so, and exits with that code.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from tests.e2e.learning.scaling import replicate
from tests.e2e.learning.scaling.explore import _inside, child_env, read_lines
from tests.e2e.learning.scaling.replicate import Halt, ModelChanged, sha256_file
from tests.e2e.learning.scaling.study import (
    EXIT_PROVIDER_LIMIT,
    EXIT_TOKEN_CAP,
    Runner,
    Stopped,
    _run_child,
    code_hash,
    commit,
    read_json,
    write_json,
)
from tests.e2e.learning.seeded.scratch import ScratchError, check_scratch

PREREG = "PREREG-SEEDS.md"
#: Version 1, fixed Sat 21 Nov 2026 13:12 GMT and approved by the participant; its hash filed publicly before any run.
#: A real stage refuses while the version is below 1.
PREREG_VERSION = 1
PREREG_SHA256 = "df980cf7a8b8ff17f813b09d6e82f2c0088b3663ccdd4158d74f78da4cd5940a"
AMENDMENTS: tuple[str, ...] = ()

STAGES = ("train", "evaluate", "analyse")
CHILDREN = ("train", "local")
SFT = ("sft-a", "sft-b", "sft-c")
DAGGER = ("dagger-a", "dagger-b", "dagger-c")
ARMS = (*SFT, *DAGGER)
#: §4's fixed order: the new models trained, then every arm evaluated, each in full before the next.
TRAIN_ORDER = ("sft-c", "dagger-b", "dagger-c")
EVALUATE_ORDER = ("dagger-a", "sft-a", "dagger-b", "sft-b", "dagger-c", "sft-c")
NEW = TRAIN_ORDER
#: The study's arm each reused arm is, and the file §4 names in the study's profile.
STUDY_ARM = {"sft-a": "sft-1000", "sft-b": "sft-1000b", "dagger-a": "dagger-1000"}
GGUFS = {"sft-a": "learning_models/versions/v3/row-bot-personal-v3-Q8_0.gguf",
         "sft-b": "learning_models/versions/v4/row-bot-personal-v4-Q8_0.gguf",
         "dagger-a": "learning_models/versions/v7/row-bot-personal-v7-Q8_0.gguf"}
#: The study's training seeds (its ``train/state.json``): the recipe's own, and ``second_seed`` for sft-1000b.
STUDY_SEEDS = {"sft-a": 3407, "sft-b": 3408, "dagger-a": 3407}
TEACHER = "GPT-6.1 Sol through the eval profile's ChatGPT sign-in (the newest Sol the catalog lists, logged)"
LANE = "lane-paper"
LABEL = "seeds"
FORMAT = "row_bot.learning.scaling_seeds_report"

# ── What --plan estimates with (each says where it comes from) ───────────────
#: The replication's five local arms: 10.7 GPU-hours for 5 × 479 × 3 runs with serving (38,511.7 s: runs 36,535.2 s,
#: serving 1,976.5 s; its ``local/state.json``).
EVAL_GPU_SECONDS_PER_RUN = 38_511.7 / (5 * 479 * 3)
#: The study's own training (``train/state.json``): training and ready seconds of each model.
TRAIN_SECONDS = {"sft-1000": 2_533.8 + 184.2, "dagger-r1": 4_889.5 + 240.2, "dagger-1000": 9_401.9 + 191.0}
#: The study's DAgger student runs (``dagger/state.json``): 1,054.4 s for its 201 requests.
ROLLOUT_SECONDS = 1_054.4
#: The study's starting model's export: 19:49 to 19:52 in the booking file (Sat 7 Nov).
START_SECONDS = 180.0
#: The study's DAgger relabelling, both rounds (``dagger/state.json``): 8,528,419 ChatGPT-lane tokens.
RELABEL_TOKENS = 8_528_419
#: A version's Q8_0 GGUF (the study's v3, v4 and v7).
GGUF_BYTES = 9_786_060_672


@dataclass(frozen=True)
class SeedsDesign:
    """The study's numbers (§2-§8); the defaults are the pre-registration's. Pinned with the build."""

    people: tuple[str, ...] = ("idris", "mara", "wren")
    seed: int = 20261105
    #: Days 1-370 (the study's and the replication's): unchanged by generating further; the repeated-wording reference.
    study_days: tuple[int, int] = (1, 370)
    fresh_days: tuple[int, int] = (371, 450)
    arms: tuple[str, ...] = ARMS
    sft_c_seed: int = 20261121
    dagger_b_seed: int = 20261122
    dagger_c_seed: int = 20261123
    #: §4: a new model that fails the product's ready check is retrained once with this seed.
    retrain_seed: int = 20261124
    runs: int = 3
    task_tokens: int = 150_000
    seconds: float = 900.0
    context: int = 65_536
    #: ``study``: the small-context profile the study served at 32K, kept up to the context (the replication's).
    envelope: str = "study"
    analysis_seed: int = 20261022
    resamples: int = 10_000
    permutations: int = 10_000
    min_tasks: int = 60
    dry_run: bool = False

    def seed_of(self, arm: str) -> int:
        """A new arm's training seed (its round-1 model's too)."""

        return {"sft-c": self.sft_c_seed, "dagger-b": self.dagger_b_seed, "dagger-c": self.dagger_c_seed}[arm]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SeedsDesign:
        known = {item.name for item in fields(cls)}
        return cls(**{key: tuple(value) if isinstance(value, list) else value for key, value in data.items()
                      if key in known})

    def scaling(self) -> Any:
        """The study's own ``Design`` for its functions (``worker.owners``, ``analysis.paired``): days 1-370 as the first
        block and the fresh days as the held-out block, with this design's numbers."""

        from tests.e2e.learning.scaling.design import Design

        return Design(people=self.people, seed=self.seed, training_days=self.study_days,
                      held_out_days=self.fresh_days, runs=self.runs, task_tokens=self.task_tokens,
                      seconds=self.seconds, analysis_seed=self.analysis_seed, resamples=self.resamples,
                      permutations=self.permutations, min_tasks=self.min_tasks, dry_run=self.dry_run)


DESIGN = SeedsDesign()
#: The dry run's: one person, three fresh days, one run each, few resamples.
DRY_DESIGN = SeedsDesign(people=("mara",), fresh_days=(371, 373), runs=1, resamples=200, permutations=200, min_tasks=1,
                         dry_run=True)


# ── The plan ─────────────────────────────────────────────────────────────────


def plan(design: SeedsDesign = DESIGN) -> dict[str, Any]:
    """What the study would do (see the module's docstring): nothing runs, nothing is reached, no folder is read."""

    fresh = replicate.fresh_block(design)
    people: dict[str, Any] = {}
    for person in design.people:
        mine = [(request_id, item) for request_id, item in fresh.items() if item["person"] == person]
        repeated = sum(1 for _, item in mine if item["repeated"])
        people[person] = {
            "requests": len(mine), "person_days": len({item["day"] for _, item in mine}),
            "repeated_wording": repeated, "new_wording": len(mine) - repeated,
            "by_template": dict(Counter(item["template"] for _, item in mine)),
            "ids": [f"{request_id} d{item['day']} {item['template']}" + (" repeated" if item["repeated"] else "")
                    for request_id, item in mine]}
    requests = len(fresh)
    repeated = sum(1 for item in fresh.values() if item["repeated"])
    runs_per_arm = requests * design.runs
    evaluation = {arm: round(runs_per_arm * EVAL_GPU_SECONDS_PER_RUN / 3600, 2) for arm in EVALUATE_ORDER}
    dagger_seconds = TRAIN_SECONDS["dagger-r1"] + ROLLOUT_SECONDS + TRAIN_SECONDS["dagger-1000"]
    training = {"starting model": round(START_SECONDS / 3600, 2), "sft-c": round(TRAIN_SECONDS["sft-1000"] / 3600, 2),
                "dagger-b": round(dagger_seconds / 3600, 2), "dagger-c": round(dagger_seconds / 3600, 2)}
    trained = sum(training.values())
    evaluated = sum(evaluation.values())

    def arm_row(arm: str) -> dict[str, Any]:
        entry: dict[str, Any] = {"arm": arm, "method": "dagger" if arm in DAGGER else "sft",
                                 "evaluated": EVALUATE_ORDER.index(arm) + 1, "context": design.context,
                                 "envelope": "the study's small-context prompt kept", "runs": runs_per_arm}
        if arm in STUDY_ARM:
            entry.update({"model": f"the study's {STUDY_ARM[arm]}, never retrained", "gguf": GGUFS[arm],
                          "training_seed": STUDY_SEEDS[arm], "file_hash": "logged before the first run"})
        else:
            entry.update({"model": "new: " + ("the study's 1,000-target subset (seed 20261106) and recipe"
                                              if arm == "sft-c" else "the study's DAgger procedure"),
                          "trained": TRAIN_ORDER.index(arm) + 1, "training_seed": design.seed_of(arm),
                          "if_not_ready": f"retrained once with {design.retrain_seed} (reported)",
                          "if_it_crashes": "started again once with the same seed (logged)",
                          "file_hash": "logged when it is ready, before the first run"})
        return entry

    return {
        "prereg": {"file": PREREG, "version": PREREG_VERSION, "sha256": PREREG_SHA256, "amendments": list(AMENDMENTS),
                   "state": "draft: a real stage refuses until version 1 is fixed" if PREREG_VERSION < 1 else "fixed"},
        "design": design.to_dict(),
        "fresh_block": {"days": list(design.fresh_days), "requests": requests,
                        "person_days": sum(item["person_days"] for item in people.values()),
                        "by_person": {person: item["requests"] for person, item in people.items()},
                        "repeated_wording": repeated, "new_wording": requests - repeated,
                        "repeated_rule": f"the same prompt text as a request of days {design.study_days[0]}-"
                                         f"{design.study_days[1]} (any of the people)"},
        "study_days": {"days": list(design.study_days), **replicate.unchanged_days(design)},
        "people": people,
        "arms": [arm_row(arm) for arm in design.arms],
        "order": {"train": list(TRAIN_ORDER), "evaluate": list(EVALUATE_ORDER)},
        "training": {
            "sft-c": "the study's 1,000-target subset (217 rows), its recipe (32gb-v1), seed "
                     f"{design.sft_c_seed}; the compiled dataset must be the study's sft-1000 one",
            "dagger": "two rounds of 500 rows (the round-1 student start, the round-2 student the round-1 model, both "
                      "models trained with the arm's seed); each round runs the study's own requests of that round "
                      "first (108 and 93), in its order, and stops at 500 rows; fewer rows go on with the requests "
                      "neither round ran",
            "seeds": {arm: design.seed_of(arm) for arm in NEW}, "retrain_seed": design.retrain_seed,
            "versions": "numbered after the study's (8 on)"},
        "runs": {"per_task": design.runs, "per_arm": runs_per_arm, "arms": len(design.arms),
                 "total": runs_per_arm * len(design.arms), "model_call_error": "rerun once at once"},
        "hypothesis": {"T1": "mean of the three dagger arms − mean of the three sft arms, per task, on the tasks counted "
                             "in all six; cluster bootstrap and paired permutation by cluster (a cluster's three pairs "
                             "swap their method labels together), no correction",
                       "min_tasks": design.min_tasks,
                       "sensitivity": f"T1 on the {requests - repeated} new-wording requests (unadjusted)"},
        "chatgpt_lane_tokens": {"relabelling": 2 * RELABEL_TOKENS, "evaluation": 0,
                                "total": 2 * RELABEL_TOKENS,
                                "basis": "the study's DAgger relabelling: 8,528,419 tokens for its two rounds, for "
                                         "each of dagger-b and dagger-c; the six arms are local and the simulated "
                                         "person's approvals are a fixed policy (no model calls)",
                                "expected": "about 30-60M (§8)", "cap": 150_000_000},
        "gpu_hours": {"training": {**training, "total": round(trained, 1)},
                      "evaluation": {**evaluation, "total": round(evaluated, 1)},
                      "total": round(trained + evaluated, 1),
                      "basis": "training: the study's own records (sft-1000 2,718 s; dagger-r1 5,130 s, its student runs "
                               "1,054 s and dagger-1000 9,593 s for each DAgger model; the starting model's export "
                               "about 3 min); evaluation: the replication's 10.7 GPU-hours for 5 local arms × 479 × 3 "
                               "runs with serving (5.36 s a run); a retraining (§4) adds that model's own time",
                      "expected": "about 18 hours: training about 6, evaluation about 2 an arm (§8)"},
        "wall_hours": {"training": round(trained, 1), "evaluation": round(evaluated, 1),
                       "note": "the runs' own time; the card waits for quiet hours (no new GPU work 00:25-08:00), the "
                               "participant's own training and other lanes' bookings"},
        "disk_gb": {"training_profile": round(6 * GGUF_BYTES / 1e9),
                    "note": "the starting model and five versions (two round-1 students) at 9.8 GB each in "
                            "<scratch>/train/data, besides checkpoints"},
        "budget_per_run": {"tokens": design.task_tokens, "steps": "the task's own step limit × 3 tool calls",
                           "seconds": design.seconds},
    }


# ── The model files ──────────────────────────────────────────────────────────


def study_files(study_out: Path, study_data: Path) -> dict[str, tuple[str, str, Path]]:
    """The study's three reused arms as it served them (ref, name on Row-Bot's server, GGUF), checked to be the files
    §4 names in its own profile."""

    from tests.e2e.learning.scaling.explore_worker import study_arms

    found = study_arms(study_out, study_data, tuple(STUDY_ARM.values()))
    chosen = {}
    for arm, model in STUDY_ARM.items():
        wanted = (study_data / GGUFS[arm]).resolve()
        if found[model][2].resolve() != wanted:
            raise ScratchError(f"the study serves {model} from {found[model][2]}, not {GGUFS[arm]} (§4)")
        chosen[arm] = found[model]
    return chosen


def new_files(out: Path, train_data: Path) -> dict[str, tuple[str, str, Path]]:
    """The three new arms as this study's training made them ready (ref, name, GGUF in the training profile): each
    arm's model is its own or, after §4's retraining, the retrained one."""

    from row_bot.learning.training import releases

    state = read_json(out / "train" / "state.json")
    found = {}
    for arm in NEW:
        key = (state.get("models") or {}).get(arm)
        entry = (state.get("arms") or {}).get(key or "") or {}
        if entry.get("state") != "ready" or not entry.get("model_ref"):
            raise ScratchError(f"{arm} has no ready model in {out / 'train' / 'state.json'}")
        folder = releases.version_dir(int(entry["version"]), train_data)
        version = releases.read_state(folder)
        if version is None or not version.gguf:
            raise ScratchError(f"{arm}'s version {entry['version']} names no GGUF ({folder})")
        gguf = Path(version.gguf) if Path(version.gguf).is_absolute() else folder / version.gguf
        if not gguf.is_file():
            raise ScratchError(f"{arm}'s GGUF ({gguf}) isn't there")
        ref = str(entry["model_ref"])
        found[arm] = (ref, ref.rsplit(":", 1)[-1], gguf)
    return found


def arm_models(out: Path, study_out: Path, study_data: Path, train_data: Path) -> dict[str, tuple[str, str, Path]]:
    """All six arms' models (ref, name, GGUF)."""

    found = study_files(study_out, study_data)
    found.update(new_files(out, train_data))
    return {arm: found[arm] for arm in ARMS}


def check_models(out: Path, files: Mapping[str, Path], *, before: str, hasher: Callable[[Path], str] = sha256_file,
                 record: bool = False) -> dict[str, Any]:
    """Hash ``files`` and compare them with ``<out>/models.json``: a file whose path, size or SHA-256 changed since it
    was logged stops the study (§8). ``record``: a file not logged yet is logged now (the study's three before training,
    each new one when it is ready, all before the first evaluation run); otherwise a file never logged refuses. Every
    check is logged there."""

    path = out / "models.json"
    saved = read_json(path)
    logged = saved.setdefault("models", {})
    current = {arm: {"path": str(Path(file)), "bytes": Path(file).stat().st_size, "sha256": hasher(Path(file))}
               for arm, file in files.items()}
    unlogged = [arm for arm in files if arm not in logged]
    if unlogged and not record:
        raise ModelChanged(f"{', '.join(unlogged)} has no logged hash in {path}: every model file is logged before "
                           "the first run")
    changed = [arm for arm in files if arm in logged
               and {key: logged[arm].get(key) for key in ("path", "bytes", "sha256")} != current[arm]]
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for arm in unlogged:
        logged[arm] = {**current[arm], "logged_at": now, "logged_before": before}
    saved.setdefault("checks", []).append({"before": before, "at": now, "models": list(files), "logged": unlogged,
                                           "changed": changed})
    write_json(path, saved)
    if changed:
        raise ModelChanged(f"{', '.join(changed)} differs from its logged hash ({path}): §8 stops the study before its "
                           "next run; it resumes only under an amendment")
    return saved


# ── The dry run's made-up study ──────────────────────────────────────────────

#: The dry study's arms' names on its server (as the real study named them) and versions.
DRY_NAMES = {"sft-1000": "row-bot-personal-v3", "sft-1000b": "row-bot-personal-v4",
             "dagger-1000": "row-bot-personal-v7"}
DRY_VERSIONS = {"sft-1000": 3, "sft-1000b": 4, "dagger-r1": 6, "dagger-1000": 7}


def _dry_row(request: Any, number: int) -> dict[str, Any]:
    """A made-up pool row for a training-block request: two tool calls and an answer, three targets."""

    call = {"role": "assistant", "content": "", "weight": 1,
            "tool_calls": [{"id": f"call_{number}", "type": "function",
                            "function": {"name": "calculator", "arguments": json.dumps({"expression": "1 + 1"})}}]}
    return {"id": f"ep_dry_{number:03d}", "person": request.person, "request": request.id, "day": request.day,
            "thread": f"dry-thread-{number}", "started_at": float(1_700_000_000 + number), "provider": "codex",
            "tokens": 600, "measured": True, "derived": True, "text": request.prompt, "targets": 3,
            "row": {"messages": [{"role": "system", "content": "You are Row-Bot."},
                                 {"role": "user", "content": request.prompt}, call,
                                 {"role": "tool", "content": "2", "tool_call_id": f"call_{number}"},
                                 {**call, "tool_calls": [{**call["tool_calls"][0], "id": f"call_{number}b"}]},
                                 {"role": "tool", "content": "2", "tool_call_id": f"call_{number}b"},
                                 {"role": "assistant", "content": "2", "weight": 1}]}}


def dry_study(folder: Path) -> tuple[Path, Path]:
    """A made-up finished study for the dry run (its ``out`` and its profile): the study's dry design, a made-up pool with
    the subsets the study's own code draws, its three arms' stand-in GGUFs where §4 names them (on its server's list), its
    training records (with sft-1000's dataset id) and its two DAgger rounds' requests in its own order. Made once; its
    numbers mean nothing."""

    from tests.e2e.learning.scaling import dagger, subsets, worker
    from tests.e2e.learning.scaling import design as design_module
    from tests.e2e.learning.scaling.dry_run import DESIGN as STUDY_DESIGN
    from tests.e2e.learning.scaling.explore import DRY_PROVIDER

    out, data = folder / "out", folder / "data"
    if (out / "design.json").is_file():
        return out.resolve(), data.resolve()
    requests = [item for person in STUDY_DESIGN.people for item in design_module.blocks(STUDY_DESIGN, person)[0]]
    rows = [_dry_row(request, number) for number, request in enumerate(requests)]
    for person in STUDY_DESIGN.people:
        worker.append_lines(out / "pool" / person / "rows.jsonl", [row for row in rows if row["person"] == person])
    by_person: dict[str, list[str]] = {}
    for row in rows:
        by_person.setdefault(row["person"], []).append(row["id"])
    owner = {row["id"]: row["person"] for row in rows}
    targets = {row["id"]: int(row["targets"]) for row in rows}
    for subset in subsets.draw(by_person, targets, STUDY_DESIGN.sizes, STUDY_DESIGN.subset_seed):
        write_json(out / "subsets" / f"n{subset.n}.json", subset.to_dict(owner, targets))
    n = STUDY_DESIGN.sizes[2]
    recipe = worker.recipe_for(STUDY_DESIGN)
    dataset = worker.Trainer.compiled(None, worker.examples_of(worker.subset_rows(out, n)), recipe)  # type: ignore[arg-type]
    listed = []
    for arm, model in STUDY_ARM.items():
        gguf = data / GGUFS[arm]
        gguf.parent.mkdir(parents=True, exist_ok=True)
        gguf.write_bytes(f"GGUF stand-in for {model}".encode())
        listed.append({"name": DRY_NAMES[model], "gguf": str(gguf), "context": 32_768})
    write_json(data / "runtimes" / "learning" / "server" / "run" / "models.json", {"models": listed})
    arms = {model: {"state": "ready", "model_ref": f"model:{DRY_PROVIDER}:{DRY_NAMES[model]}",
                    "version": DRY_VERSIONS[model], "seed": STUDY_SEEDS[arm], "recipe": recipe.name,
                    "dataset": dataset.id if model != "dagger-1000" else "ds_dry_dagger", "steps": 2,
                    "training_seconds": 1.0, "ready_seconds": 1.0} for arm, model in STUDY_ARM.items()}
    arms["dagger-r1"] = {"state": "ready", "model_ref": f"model:{DRY_PROVIDER}:row-bot-personal-v6", "version": 6,
                         "seed": 3407, "recipe": recipe.name, "steps": 1, "training_seconds": 1.0, "ready_seconds": 1.0}
    write_json(out / "train" / "state.json", {"start": {"ref": f"model:{DRY_PROVIDER}:row-bot-personal-base"},
                                              "arms": arms})
    in_subset = {row["request"] for row in worker.subset_rows(out, n)}
    order = dagger.order({person: [item.id for item in requests if item.person == person]
                          for person in STUDY_DESIGN.people}, in_subset, f"{STUDY_DESIGN.subset_seed}/dagger")
    write_json(out / "dagger" / "state.json", {
        "done": True, "teacher_tokens": 0, "cloud_tokens": 0, "gpu_seconds": 2.0,
        "round_1": {"rows": 2, "requests": 1}, "round_2": {"rows": 2, "requests": 1},
        "requests": [{"round": 1, "request": order[0], "rows": 2, "status": "passed", "left_out": {}, "seconds": 1.0},
                     {"round": 2, "request": order[1], "rows": 2, "status": "passed", "left_out": {}, "seconds": 1.0}]})
    write_json(out / "build.json", {"commit": "dry-study", "code_hash": "dry-study"})
    write_json(out / "design.json", STUDY_DESIGN.to_dict())  # last: it marks the study as made
    return out.resolve(), data.resolve()


# ── The training records the report shows ────────────────────────────────────

_TRAINING_FIELDS = ("state", "version", "seed", "recipe", "rows", "targets", "trained_targets", "examples", "validation",
                    "steps", "dataset", "kept_pass", "loss", "training_seconds", "ready_seconds", "model_ref", "why")


def _relabelling(state: Mapping[str, Any], study_rounds: Mapping[int, Sequence[str]] | None = None) -> dict[str, Any]:
    """A DAgger model's relabelling: per round its rollouts (requests run), labelled decisions (rows kept), what was left
    out and, for a new model, how its requests compare with the study's; its teacher tokens."""

    requests = state.get("requests") or []
    rounds = {}
    for number in (1, 2):
        mine = [item for item in requests if item.get("round") == number]
        left = Counter()
        for item in mine:
            left.update(item.get("left_out") or {})
        found = {"rollouts": len(mine), "rows": int((state.get(f"round_{number}") or {}).get("rows")
                                                   or sum(int(item.get("rows") or 0) for item in mine)),
                 "statuses": dict(sorted(Counter(str(item.get("status")) for item in mine).items())),
                 "left_out": dict(sorted(left.items())),
                 "student": (state.get(f"round_{number}") or {}).get("student")}
        if study_rounds is not None:
            ran, theirs = [item["request"] for item in mine], list(study_rounds.get(number) or ())
            found.update({"the_studys_requests": len(theirs), "same": len(set(ran) & set(theirs)),
                          "not_run": len(set(theirs) - set(ran)), "added": len(set(ran) - set(theirs))})
        rounds[f"round_{number}"] = found
    return {"rounds": rounds, "teacher_tokens": int(state.get("teacher_tokens") or 0),
            "cloud_tokens": int(state.get("cloud_tokens") or 0),
            "rollout_gpu_seconds": float(state.get("gpu_seconds") or 0.0)}


def training_records(out: Path, study_out: Path) -> dict[str, Any]:
    """Per arm: the models it was trained as (the study's records for the reused three, this study's for the new
    three, with §4's retraining), DAgger's relabelling and the training GPU-hours."""

    study = read_json(study_out / "train" / "state.json").get("arms") or {}
    study_dagger = read_json(study_out / "dagger" / "state.json")
    ours = read_json(out / "train" / "state.json")
    trained = ours.get("arms") or {}
    study_rounds = {number: [item["request"] for item in study_dagger.get("requests") or ()
                             if item.get("round") == number] for number in (1, 2)}
    found: dict[str, Any] = {}

    def summary(models: Mapping[str, Mapping[str, Any]], rollouts: float = 0.0) -> dict[str, Any]:
        seconds = sum(float(item.get("training_seconds") or 0) + float(item.get("ready_seconds") or 0)
                      for item in models.values()) + rollouts
        return {"models": {key: {field: item.get(field) for field in _TRAINING_FIELDS} for key, item in models.items()},
                "gpu_hours": round(seconds / 3600, 3)}

    for arm, model in STUDY_ARM.items():
        models = {model: study.get(model) or {}}
        if arm == "dagger-a":
            models = {"dagger-r1": study.get("dagger-r1") or {}, **models}
            relabelled = _relabelling(study_dagger)
            found[arm] = {"from": "the study", **summary(models, relabelled["rollout_gpu_seconds"]),
                          "relabelling": relabelled}
        else:
            found[arm] = {"from": "the study", **summary(models)}
    chosen = ours.get("models") or {}
    retrained = ours.get("retrained") or {}
    restarted = ours.get("restarted") or {}
    for arm in NEW:
        # The arm's own model and its round-1 student, each perhaps retrained (``@<seed>``) or started again after a
        # crash (``+restart``), §4.
        mine = (arm, f"{arm}-r1")
        keys = [key for key in trained if key.split("@")[0].split("+")[0] in mine]
        models = {key: trained[key] for key in sorted(keys, key=lambda key: (not key.startswith(f"{arm}-r1"), key))}
        entry = {"from": "this study", "evaluated_as": chosen.get(arm),
                 "retrained": {key: value for key, value in retrained.items() if key in mine},
                 "restarted": {key: value for key, value in restarted.items() if key.split("@")[0] in mine}}
        if arm in DAGGER:
            relabelled = _relabelling(read_json(out / "train" / arm / "state.json"), study_rounds)
            entry.update(summary(models, relabelled["rollout_gpu_seconds"]))
            entry["relabelling"] = relabelled
        else:
            entry.update(summary(models))
        found[arm] = entry
    return found


# ── The stages ───────────────────────────────────────────────────────────────


class Seeds:
    """The study's stages (see the module's docstring)."""

    def __init__(self, args: argparse.Namespace, *, runner: Runner = _run_child,
                 say: Callable[[str], None] = lambda text: print(text, flush=True),
                 build: Callable[[], dict[str, str]] | None = None,
                 hasher: Callable[[Path], str] = sha256_file) -> None:
        self.args = args
        self.design = DRY_DESIGN if args.dry_run else DESIGN
        self.profile = Path(args.eval_profile) if getattr(args, "eval_profile", "") else None
        self.out = check_scratch(Path(args.out), self.profile)
        self.scratch = check_scratch(Path(args.scratch), self.profile)
        # Anything of Row-Bot this process imports (the generator's tasks, the analysis) binds a scratch folder.
        os.environ["ROW_BOT_DATA_DIR"] = str(check_scratch(self.scratch / "orchestrator", self.profile))
        self.runner = runner
        self.say = say
        self.build = build or (lambda: {"commit": commit(), "code_hash": code_hash()})
        self.hasher = hasher
        self.study_out: Path | None = None
        self.study_data: Path | None = None

    def data(self, child: str) -> Path:
        """A child's own scratch profile."""

        return self.scratch / child / "data"

    def folders(self) -> list[Path]:
        """Every folder the study writes."""

        return [self.out, *(self.data(child) for child in CHILDREN), self.scratch / "orchestrator"]

    def find_study(self) -> None:
        """The finished study it reads (a made-up one for the dry run); none of its folders is ever written."""

        if self.study_out is not None:
            return
        if self.args.dry_run:
            self.study_out, self.study_data = dry_study(self.scratch / "dry-study")
        else:
            missing = [flag for flag, value in (("--study-out", self.args.study_out),
                                                ("--study-data", self.args.study_data),
                                                ("--eval-profile", self.args.eval_profile),
                                                ("--gpu-booking", self.args.gpu_booking)) if not value]
            if missing:
                raise ScratchError(f"a real run names {', '.join(missing)}: the study's out folder and evaluation "
                                   "profile (read only), the ChatGPT lane's eval profile and the GPU booking file")
            self.study_out, self.study_data = Path(self.args.study_out).resolve(), Path(self.args.study_data).resolve()
        root = Path(os.path.commonpath([str(self.study_out), str(self.study_data)]))
        for folder in self.folders():
            check_scratch(folder, self.profile)
            if _inside(folder, root):
                raise ScratchError(f"{folder} is inside the study's own folders ({root}): they are read, never "
                                   "written")
        if not read_json(self.study_out / "design.json"):
            raise ScratchError(f"{self.study_out} holds no design.json: name the study's out folder")

    # ── The pin ─────────────────────────────────────────────────────────

    def pin(self) -> dict[str, Any]:
        """Record the build, the design, the study it reads and the fresh block before the first run; refuse another
        build, design or study afterwards (§8, §11)."""

        self.find_study()
        study_design = read_json(self.study_out / "design.json")
        study_build = read_json(self.study_out / "build.json")
        last = (list(study_design.get("held_out_days") or ()) or [None])[-1]
        if (int(study_design.get("seed", -1)) != self.design.seed or not set(self.design.people) <= set(
                study_design.get("people") or ()) or int(study_design.get("subset_seed", -1)) != 20261106
                or last is None or int(last) >= self.design.fresh_days[0]):
            raise ScratchError("the study's design isn't the one this study continues (its seed, people, subset seed "
                               f"or last day): {self.study_out / 'design.json'}")
        current = json.loads(json.dumps({
            **self.build(), "design": self.design.to_dict(), "prereg_sha256": PREREG_SHA256,
            "study": {"out": str(self.study_out), "data": str(self.study_data), "commit": study_build.get("commit"),
                      "code_hash": study_build.get("code_hash"), "design": study_design}}))
        path = self.out / "build.json"
        saved = read_json(path)
        if saved:
            changed = [key for key in ("commit", "code_hash", "design", "prereg_sha256", "study")
                       if saved.get(key) != current[key]]
            if changed:
                raise ScratchError(f"this study is pinned to build {str(saved.get('commit', ''))[:12]} (code "
                                   f"{str(saved.get('code_hash', ''))[:12]}); this one differs in {', '.join(changed)}: "
                                   "another build runs only under an amendment")
            return saved
        days = replicate.unchanged_days(self.design)
        if not days["unchanged"]:
            raise ScratchError(f"generating to day {self.design.fresh_days[1]} changed requests of days "
                               f"{self.design.study_days[0]}-{self.design.study_days[1]}: {days['by_person']}")
        fresh = replicate.fresh_block(self.design)
        write_json(self.out / "fresh.json", {"days": list(self.design.fresh_days), "study_days": days,
                                              "requests": fresh})
        write_json(self.out / "design.json", {"seeds": self.design.to_dict(), "study_design": study_design,
                                              "study_out": str(self.study_out), "study_data": str(self.study_data),
                                              "train_data": str(self.data("train"))})
        write_json(path, {**current, "pinned_at": time.strftime("%Y-%m-%d %H:%M:%S")})  # last: it marks the pin
        return current

    # ── The model files ─────────────────────────────────────────────────

    def check_models(self, before: str, arms: Sequence[str], *, record: bool = False) -> dict[str, Any]:
        found = study_files(self.study_out, self.study_data)
        if any(arm in NEW for arm in arms):
            found.update(new_files(self.out, self.data("train")))
        self.say(f"checking the model files' SHA-256 ({', '.join(arms)}) before {before}")
        return check_models(self.out, {arm: found[arm][2] for arm in arms}, before=before, hasher=self.hasher,
                            record=record)

    # ── Children ────────────────────────────────────────────────────────

    def spent(self, *, besides: str = "") -> int:
        """ChatGPT-lane tokens the study has spent (each child's own count), but ``besides``'s."""

        found = 0
        if besides != "train":
            found += sum(int(read_json(self.out / "train" / arm / "state.json").get("cloud_tokens") or 0)
                         for arm in NEW if arm in DAGGER)
        if besides != "local":
            found += int(read_json(self.out / "local" / "state.json").get("cloud_tokens") or 0)
        return found

    def child(self, task: str) -> None:
        data = self.data(task)
        data.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "tests.e2e.learning.scaling.seeds_worker", task, "--out", str(self.out),
                   "--data", str(data), "--lane", self.args.lane, "--cap-tokens", str(self.args.cap_tokens),
                   "--spent-before", str(self.spent(besides=task))]
        if task == "train" and self.args.eval_profile and not self.args.dry_run:
            command += ["--eval-profile", str(self.args.eval_profile)]
        if self.args.gpu_booking:
            command += ["--gpu-booking", str(self.args.gpu_booking)]
        self.say(f"── {task} (ChatGPT-lane tokens so far {self.spent():,})")
        log = self.out / "logs" / f"{task}.log"
        code = self.runner(command, child_env(data), log)
        if code == EXIT_TOKEN_CAP:
            raise Halt(f"{task}: the token cap ({int(self.args.cap_tokens):,}) was reached; it stopped cleanly. A cap "
                       "stop is reported, and nothing reruns without an amendment (§8)", EXIT_TOKEN_CAP)
        if code == EXIT_PROVIDER_LIMIT:
            raise Halt(f"{task}: the ChatGPT lane answered with a usage or rate limit; it stopped at once. Run the "
                       "stage again later to continue", EXIT_PROVIDER_LIMIT)
        if code != 0:
            raise Stopped(f"{task} stopped (exit {code}): see {log}; run the stage again to continue")

    def done(self, child: str) -> bool:
        return bool(read_json(self.out / child / "state.json").get("done"))

    def stage_train(self) -> None:
        self.check_models("train", list(STUDY_ARM), record=True)  # the study's three, before anything runs
        if not self.done("train"):
            self.child("train")
        self.check_models("evaluate", list(NEW), record=True)  # each new one once it is ready

    def stage_evaluate(self) -> None:
        if not self.done("train"):
            raise Stopped("evaluate: the new models are trained first (§4)")
        self.check_models("evaluate", list(ARMS))
        if not self.done("local"):
            self.child("local")

    def stage_analyse(self) -> dict[str, Any]:
        from tests.e2e.learning.scaling import seeds_analysis

        if not all(self.done(child) for child in CHILDREN):
            raise Stopped("analyse: the results are computed only when every arm has finished (§8)")
        self.check_models("analyse", list(ARMS))
        report = seeds_analysis.build_report(
            read_lines(self.out / "local" / "runs.jsonl"), design=self.design,
            fresh=read_json(self.out / "fresh.json")["requests"], local=read_json(self.out / "local" / "state.json"),
            training=training_records(self.out, self.study_out))
        report.update({"prereg": {"file": PREREG, "version": PREREG_VERSION, "sha256": PREREG_SHA256,
                                  "amendments": list(AMENDMENTS)},
                       "build": read_json(self.out / "build.json"), "models": read_json(self.out / "models.json"),
                       "teacher": read_json(self.out / "teacher.json") or {
                           "model_ref": read_json(self.out / "train" / "dagger-b" / "state.json").get("teacher")}})
        write_json(self.out / "analyse" / "report.json", report)
        (self.out / "analyse" / "report.md").write_text(seeds_analysis.markdown(report), encoding="utf-8",
                                                         newline="\n")
        self.say(f"Report: {self.out / 'analyse' / 'report.md'}")
        return report

    def run(self, stages: Sequence[str]) -> int:
        unknown = [stage for stage in stages if stage not in STAGES]
        if unknown or not stages:
            raise ScratchError(f"name the stages to run, from: {', '.join(STAGES)}")
        if not self.args.dry_run and PREREG_VERSION < 1:
            raise ScratchError(f"{PREREG} is a draft (version {PREREG_VERSION}): a real stage runs only once it is "
                               "fixed as version 1 and its SHA-256 is pinned in seeds.py")
        build = self.pin()
        self.say(f"build {str(build.get('commit', ''))[:12]}, code {str(build.get('code_hash', ''))[:12]}")
        try:
            for stage in STAGES:
                if stage in stages:
                    self.say(f"stage {stage}")
                    getattr(self, f"stage_{stage}")()
        except Halt as stopped:
            self.say(f"stopped: {stopped}")
            return stopped.code
        except Stopped as stopped:
            self.say(f"stopped: {stopped}")
            return 3
        return 0


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Row-Bot Learning's training-seed study (PREREG-SEEDS.md)")
    parser.add_argument("stages", nargs="*", help=f"the stages to run: {', '.join(STAGES)}")
    parser.add_argument("--out", required=True, help="where the study's state, runs and report go")
    parser.add_argument("--scratch", required=True, help="where its scratch profiles go")
    parser.add_argument("--study-out", default="", help="the finished study's out folder (read only)")
    parser.add_argument("--study-data", default="", help="the study's evaluation profile: its server lists the reused "
                                                         "GGUFs, and its engine, server and weights are linked (read "
                                                         "only)")
    parser.add_argument("--eval-profile", default=os.environ.get("ROW_BOT_LIVE_EVAL_CODEX_PROFILE", ""),
                        help="the ChatGPT lane's eval profile (its sign-in is read into DAgger's teacher's memory)")
    parser.add_argument("--gpu-booking", default="", help="the shared GPU booking file")
    parser.add_argument("--lane", default=LANE)
    parser.add_argument("--cap-tokens", type=int, default=150_000_000,
                        help="the ChatGPT-lane tokens the whole study may spend (§8: 150M)")
    parser.add_argument("--plan", action="store_true", help="print the plan and run nothing")
    parser.add_argument("--dry-run", action="store_true", help="a made-up study, the scripted model and the fake "
                                                               "trainer (no GPU)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # a log file on Windows is cp1252 otherwise
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    args = parse(argv)
    try:
        seeds = Seeds(args)
        if args.plan:
            print(json.dumps(plan(seeds.design), indent=1, ensure_ascii=False))
            return 0
        return seeds.run(args.stages or (list(STAGES) if args.dry_run else []))
    except ScratchError as error:
        print(f"scaling seeds: {error}", file=sys.stderr)
        return 2


__all__ = ["ARMS", "DAGGER", "DESIGN", "DRY_DESIGN", "EVALUATE_ORDER", "GGUFS", "NEW", "PREREG_SHA256", "SFT",
           "STAGES", "STUDY_ARM", "Seeds", "SeedsDesign", "TRAIN_ORDER", "arm_models", "check_models", "dry_study",
           "main", "new_files", "parse", "plan", "study_files", "training_records"]


if __name__ == "__main__":
    sys.exit(main())
