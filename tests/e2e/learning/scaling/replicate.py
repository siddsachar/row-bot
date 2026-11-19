"""The replication (``paper/PREREG-REPLICATION.md`` v1): does on-policy relabelling beat plain fine-tuning once its
runs fit? The controlled study's ``start``, ``sft-1000`` (study version 3) and ``dagger-1000`` (study version 7), never
retrained, on **fresh held-out tasks**: simulated days 291-370 of the same three seeded people, 479 requests in 240
person-days.

    python -m tests.e2e.learning.scaling.replicate --plan --out <folder> --scratch <folder>
    python -m tests.e2e.learning.scaling.replicate --study-out <study>/out --study-data <study>/scratch/study/data \\
        --eval-profile <folder> --gpu-booking <file> [--lane lane-paper] [--cap-tokens N] \\
        --out <folder> --scratch <folder> evaluate analyse
    python -m tests.e2e.learning.scaling.replicate --dry-run --out <folder> --scratch <folder>

**Arms** (§4), run in this order, each in full before the next, every fresh request 3 times:

- ``teacher``: GPT-6.1 Sol through the eval profile's ChatGPT sign-in (the newest Sol the catalog lists, logged in
  ``teacher.json``), as the study's teacher arm ran (``worker.LiveKit.teacher``); no GPU;
- ``dagger-64k``, ``sft-64k``, ``start-64k``: served at 65,536 tokens with the study's small-context prompt kept (the
  exploratory tool's ``--envelope study``);
- ``dagger-32k``, ``sft-32k``: served at 32,768, as the study served them (the product's own small-context profile).

**Stages** run only when named, each continuing from its own state under ``--out``:

- ``evaluate``: two child processes (``replicate_worker.py``), one after the other, each in its own scratch profile
  ``<scratch>/<child>/data``: ``teacher`` (the ChatGPT lane; it never books the card) and then ``local`` (the five
  local arms in Row-Bot's own llama.cpp server, one model loaded at a time; it never touches the eval profile).
- ``analyse``: R1, R2 with Holm and the secondaries (``replicate_analysis.py``), only once every arm has finished (§8):
  ``analyse/report.json`` and ``report.md``.

``--plan`` prints the fresh block, the arms, the runs and the estimates from the generator alone: it reads no folder,
starts nothing and reaches nothing. ``--dry-run`` makes a made-up finished study (stand-in GGUFs) and runs both stages on
one person and three fresh days, one run each, with the scripted model on loopback (ports 8140-8149): no GPU, no
network.

**Pinned before the first run** (§8, §11), refused afterwards when they change:

- ``build.json``: the commit, the code hash (``study.code_hash``: Row-Bot's source and the harness), the design, this
  pre-registration's hash and the study it reads (its build and design);
- ``fresh.json``: every fresh request (person, day, cluster, template) with its repeated-wording flag, after checking that
  generating to day 370 leaves every request of days 1-290 as the study's own generation made it;
- ``models.json``: the SHA-256 of the three GGUFs, read from the study's profile. Every later stage checks them first
  (and the ``local`` child again before serving each arm); a file that changed stops the replication before its next
  run, to resume only under an amendment.

It writes only under ``--out`` and ``--scratch`` (refusing the everyday profile, its backups, the eval profile, a
checkout and the study's own folders) and reads the study's folders without writing there: the GGUFs are served from
where they are, and Row-Bot's llama.cpp server files are hard-linked (never its ``run`` folder; ``explore_worker``).
One ``--cap-tokens`` (default 200M ChatGPT-lane tokens) covers both children: a child stops cleanly at the cap (exit
75) and at once on a usage or rate limit (exit 76); the replication then stops, says so, and exits with that code.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from tests.e2e.learning.scaling.explore import DRY_NAMES, DRY_PROVIDER, _inside, child_env, read_lines
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

PREREG = "PREREG-REPLICATION.md"
PREREG_VERSION = 1
PREREG_SHA256 = "56dbc5db6cb0f6a976e58b3e3548fe8c2b2b4accf0225e55d4949bcca22b7a2b"
AMENDMENTS: tuple[str, ...] = ()

STAGES = ("evaluate", "analyse")
#: The children of ``evaluate``, in order (§4: the teacher first).
CHILDREN = ("teacher", "local")
#: §4's arms in their fixed order.
ARMS = ("teacher", "dagger-64k", "sft-64k", "start-64k", "dagger-32k", "sft-32k")
LOCAL = ARMS[1:]
#: Each local arm's model (the study's arm whose file it serves) and its serving context.
MODEL_OF = {"dagger-64k": "dagger-1000", "sft-64k": "sft-1000", "start-64k": "start", "dagger-32k": "dagger-1000",
            "sft-32k": "sft-1000"}
CONTEXT_OF = {"dagger-64k": 65_536, "sft-64k": 65_536, "start-64k": 65_536, "dagger-32k": 32_768, "sft-32k": 32_768}
MODELS = ("start", "sft-1000", "dagger-1000")
#: The study's own files (§2), relative to its evaluation profile.
GGUFS = {"start": "learning_models/base/qwen3.5-9b/row-bot-personal-base-Q8_0.gguf",
         "sft-1000": "learning_models/versions/v3/row-bot-personal-v3-Q8_0.gguf",
         "dagger-1000": "learning_models/versions/v7/row-bot-personal-v7-Q8_0.gguf"}
TEACHER = "GPT-6.1 Sol through the eval profile's ChatGPT sign-in (the newest Sol the catalog lists, logged)"
LANE = "lane-paper"
LABEL = "replication"
FORMAT = "row_bot.learning.scaling_replication_report"

# ── What --plan estimates with (each says where it comes from) ───────────────
#: The study's teacher on its held-out block: 39,644,723 ChatGPT-lane tokens over its 771 runs (``runs.jsonl``).
TEACHER_TOKENS_PER_RUN = 39_644_723 / 771
#: The same runs' own time: 12,754.5 s over 771 runs.
TEACHER_SECONDS_PER_RUN = 12_754.5 / 771
#: The exploratory 64K run (``explore-64k``'s ``evaluate/state.json``): the card held for each model's 771 runs. A 32K
#: arm is estimated at its model's 64K rate.
GPU_SECONDS_PER_RUN = {"start": 3_142.7 / 771, "sft-1000": 3_363.0 / 771, "dagger-1000": 3_668.2 / 771}
#: The same run's serving and probing: 65.6 s for its three models.
SERVING_SECONDS_PER_ARM = 65.6 / 3


@dataclass(frozen=True)
class ReplicationDesign:
    """The replication's numbers (§2-§8); the defaults are the pre-registration's. Pinned with the build."""

    people: tuple[str, ...] = ("idris", "mara", "wren")
    seed: int = 20261105
    #: The study's own days (its training and held-out blocks): unchanged, the repeated-wording flag's reference.
    study_days: tuple[int, int] = (1, 290)
    fresh_days: tuple[int, int] = (291, 370)
    arms: tuple[str, ...] = ARMS
    runs: int = 3
    task_tokens: int = 150_000
    seconds: float = 900.0
    #: ``study``: the small-context profile the study served at 32K, kept up to 65,536 for the 64K arms.
    envelope: str = "study"
    analysis_seed: int = 20261022
    resamples: int = 10_000
    permutations: int = 10_000
    min_tasks: int = 60
    #: Gap closed's rule (A12, the study's implementation note 13).
    unbounded_share: float = 0.025
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ReplicationDesign:
        known = {item.name for item in fields(cls)}
        return cls(**{key: tuple(value) if isinstance(value, list) else value for key, value in data.items()
                      if key in known})

    def scaling(self) -> Any:
        """The study's own ``Design`` for its functions (``worker.owners``, ``analysis.paired``, ``gap_closed``): its
        days 1-290 as the first block and the fresh days as the held-out block, with this design's numbers."""

        from tests.e2e.learning.scaling.design import Design

        return Design(people=self.people, seed=self.seed, training_days=self.study_days,
                      held_out_days=self.fresh_days, runs=self.runs, task_tokens=self.task_tokens,
                      seconds=self.seconds, analysis_seed=self.analysis_seed, resamples=self.resamples,
                      permutations=self.permutations, min_tasks=self.min_tasks,
                      unbounded_share=self.unbounded_share, dry_run=self.dry_run)


DESIGN = ReplicationDesign()
#: The dry run's: one person, three fresh days, one run each, few resamples.
DRY_DESIGN = ReplicationDesign(people=("mara",), fresh_days=(291, 293), runs=1, resamples=200, permutations=200,
                               min_tasks=1, dry_run=True)


# ── The fresh block ──────────────────────────────────────────────────────────


@functools.lru_cache(maxsize=16)
def _history(person: str, seed: int, days: int) -> Any:
    from tests.e2e.learning.seeded import generator

    return generator.generate(person, seed=seed, days=days)


def blocks(design: ReplicationDesign, person: str) -> tuple[list[Any], list[Any]]:
    """The person's requests of the study's days (1-290) and of the fresh block, from one generation to the block's
    last day."""

    stream = _history(person, int(design.seed), int(design.fresh_days[1])).stream
    first, last = design.study_days
    start, end = design.fresh_days
    return ([item for item in stream if first <= item.day <= last],
            [item for item in stream if start <= item.day <= end])


def repeated_wording(earlier: Sequence[Any], fresh: Sequence[Any]) -> dict[str, bool]:
    """Per fresh request: whether its prompt text is exactly that of a request in ``earlier`` (§3)."""

    seen = {item.prompt for item in earlier}
    return {item.id: item.prompt in seen for item in fresh}


def unchanged_days(design: ReplicationDesign) -> dict[str, Any]:
    """Whether generating to the fresh block's last day leaves every request of the study's days as the study's own
    generation (to day 290) made it, compared request by request, whole (§3)."""

    found: dict[str, Any] = {}
    for person in design.people:
        early, _fresh = blocks(design, person)
        study = [item for item in _history(person, int(design.seed), int(design.study_days[1])).stream
                 if design.study_days[0] <= item.day]
        same = len(early) == len(study) and all(
            json.dumps(a.to_dict(), sort_keys=True, ensure_ascii=False) ==
            json.dumps(b.to_dict(), sort_keys=True, ensure_ascii=False) for a, b in zip(early, study))
        found[person] = {"requests": len(study), "identical": same}
    return {"unchanged": all(item["identical"] for item in found.values()), "by_person": found}


def fresh_block(design: ReplicationDesign) -> dict[str, dict[str, Any]]:
    """Every fresh request, in order: its person, day, cluster (a person's simulated day, §7), template and
    repeated-wording flag (against every person's requests of the study's days)."""

    earlier = [item for person in design.people for item in blocks(design, person)[0]]
    found: dict[str, dict[str, Any]] = {}
    for person in design.people:
        fresh = blocks(design, person)[1]
        flags = repeated_wording(earlier, fresh)
        for item in fresh:
            found[item.id] = {"person": item.person, "day": item.day, "cluster": f"{item.person}/d{item.day}",
                              "template": item.template, "repeated": flags[item.id]}
    return found


def plan(design: ReplicationDesign = DESIGN) -> dict[str, Any]:
    """What the replication would do (see the module's docstring): nothing runs, nothing is reached."""

    fresh = fresh_block(design)
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
    local_runs = {arm: runs_per_arm for arm in LOCAL}
    gpu = {arm: round((runs * GPU_SECONDS_PER_RUN[MODEL_OF[arm]] + SERVING_SECONDS_PER_ARM) / 3600, 2)
           for arm, runs in local_runs.items()}
    teacher_tokens = round(runs_per_arm * TEACHER_TOKENS_PER_RUN)
    return {
        "prereg": {"file": PREREG, "version": PREREG_VERSION, "sha256": PREREG_SHA256, "amendments": list(AMENDMENTS)},
        "design": design.to_dict(),
        "fresh_block": {"days": list(design.fresh_days), "requests": requests,
                        "person_days": sum(item["person_days"] for item in people.values()),
                        "by_person": {person: item["requests"] for person, item in people.items()},
                        "repeated_wording": repeated, "new_wording": requests - repeated,
                        "repeated_rule": f"the same prompt text as a request of days {design.study_days[0]}-"
                                         f"{design.study_days[1]} (any of the people)"},
        "study_days": {"days": list(design.study_days), **unchanged_days(design)},
        "people": people,
        "arms": [{"arm": arm, "order": number,
                  **({"model": TEACHER, "context": "its own", "gpu": False} if arm == "teacher" else
                     {"model": MODEL_OF[arm], "gguf": GGUFS[MODEL_OF[arm]], "context": CONTEXT_OF[arm],
                      "envelope": "the study's small-context prompt" + (" kept" if CONTEXT_OF[arm] > 32_768 else ""),
                      "gpu": True})}
                 for number, arm in enumerate(design.arms, 1)],
        "runs": {"per_task": design.runs, "per_arm": runs_per_arm, "arms": len(design.arms),
                 "total": runs_per_arm * len(design.arms), "model_call_error": "rerun once at once"},
        "hypotheses": {"R1": "dagger-64k − sft-64k, on the tasks counted in both",
                       "R2": "(dagger-64k − sft-64k) − (dagger-32k − sft-32k), on the tasks counted in all four",
                       "family": "Holm across R1 and R2 at 0.05", "min_tasks": design.min_tasks,
                       "sensitivity": f"R1 and R2 on the {requests - repeated} new-wording requests (unadjusted)"},
        "chatgpt_lane_tokens": {"teacher": teacher_tokens,
                                "per_run": round(TEACHER_TOKENS_PER_RUN),
                                "basis": "the study's teacher on its held-out block: 39,644,723 tokens over 771 runs",
                                "expected": "about 80-150M (§8)", "cap": 200_000_000},
        "gpu_hours": {**gpu, "total": round(sum(gpu.values()), 1),
                      "basis": "the exploratory 64K run: the card held for each model's 771 runs (start 3,142.7 s, "
                               "sft-1000 3,363.0 s, dagger-1000 3,668.2 s) and 65.6 s serving its three; a 32K arm "
                               "at its model's 64K rate",
                      "expected": "about 9 hours (§8)"},
        "wall_hours": {"teacher": round(runs_per_arm * TEACHER_SECONDS_PER_RUN / 3600, 1),
                       "local": round(sum(gpu.values()), 1),
                       "note": "the runs' own time; the card waits for quiet hours (no new GPU work 00:25-08:00), "
                               "the participant's own training and other lanes' bookings"},
        "budget_per_run": {"tokens": design.task_tokens, "steps": "the task's own step limit × 3 tool calls",
                           "seconds": design.seconds},
    }


# ── The model files ──────────────────────────────────────────────────────────


def sha256_file(path: Path) -> str:
    """SHA-256 of a file, read in 8 MiB pieces (one read-only pass)."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while piece := handle.read(8 << 20):
            digest.update(piece)
    return digest.hexdigest()


def model_files(study_out: Path, study_data: Path) -> dict[str, tuple[str, str, Path]]:
    """Each model as the study served it (its ref, its name on Row-Bot's server and its GGUF), checked to be the file
    §2 names in the study's own profile."""

    from tests.e2e.learning.scaling.explore_worker import study_arms

    found = study_arms(study_out, study_data, MODELS)
    for model in MODELS:
        wanted = (study_data / GGUFS[model]).resolve()
        if found[model][2].resolve() != wanted:
            raise ScratchError(f"the study serves {model} from {found[model][2]}, not {GGUFS[model]} (§2)")
    return found


class ModelChanged(ScratchError):
    """A model file differs from its logged hash (§8): the replication stops before its next run."""


def check_models(out: Path, files: Mapping[str, Path], *, before: str,
                 hasher: Callable[[Path], str] = sha256_file, record: bool = True) -> dict[str, Any]:
    """The first call records the SHA-256 of ``files`` in ``<out>/models.json`` (before the first run); every later
    call hashes them again and refuses a file whose path, size or hash changed. Each check is logged there.
    ``record=False`` (a child checking one file): refuse when nothing was recorded."""

    path = out / "models.json"
    saved = read_json(path)
    if not saved.get("models") and not record:
        raise ModelChanged(f"{path} holds no logged hashes: the model files are recorded before the first run")
    wanted = list(files)
    current = {}
    for model in wanted:
        file = Path(files[model])
        current[model] = {"path": str(file), "bytes": file.stat().st_size, "sha256": hasher(file)}
    if not saved.get("models"):
        saved = {"models": current, "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"), "recorded_before": before,
                 "checks": []}
        write_json(path, saved)
        return saved
    changed = [model for model in wanted if saved["models"].get(model) != current[model]]
    saved.setdefault("checks", []).append({"before": before, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                           "models": wanted, "changed": changed})
    write_json(path, saved)
    if changed:
        raise ModelChanged(f"{', '.join(changed)} differs from its logged hash ({path}): §8 stops the replication "
                           "before its next run; it resumes only under an amendment")
    return saved


# ── The dry run's made-up study ──────────────────────────────────────────────


def dry_study(folder: Path) -> tuple[Path, Path]:
    """A made-up finished study for the dry run (its ``out`` and its profile): the study's own design, its three
    models' stand-in GGUFs where §2 names them, on its server's list, and its training state. Made once."""

    from tests.e2e.learning.scaling.design import DESIGN as STUDY_DESIGN

    out, data = folder / "out", folder / "data"
    if (out / "design.json").is_file():
        return out.resolve(), data.resolve()
    listed = []
    for model in MODELS:
        gguf = data / GGUFS[model]
        gguf.parent.mkdir(parents=True, exist_ok=True)
        gguf.write_bytes(f"GGUF stand-in for {model}".encode())
        listed.append({"name": DRY_NAMES[model], "gguf": str(gguf), "context": 32_768})
    write_json(data / "runtimes" / "learning" / "server" / "run" / "models.json", {"models": listed})
    write_json(out / "build.json", {"commit": "dry-study", "code_hash": "dry-study"})
    write_json(out / "train" / "state.json", {
        "start": {"ref": f"model:{DRY_PROVIDER}:{DRY_NAMES['start']}"},
        "arms": {model: {"state": "ready", "model_ref": f"model:{DRY_PROVIDER}:{DRY_NAMES[model]}"}
                 for model in ("sft-1000", "dagger-1000")}})
    write_json(out / "design.json", STUDY_DESIGN.to_dict())  # last: it marks the study as made
    return out.resolve(), data.resolve()


# ── The stages ───────────────────────────────────────────────────────────────


class Halt(Stopped):
    """A stop with its own exit code (the token cap, a usage or rate limit)."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


class Replication:
    """The replication's stages (see the module's docstring)."""

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
        """Every folder the replication writes."""

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
        if int(study_design.get("seed", -1)) != self.design.seed or not set(self.design.people) <= set(
                study_design.get("people") or ()) or list(study_design.get("held_out_days") or ())[-1:] != [
                self.design.study_days[1]]:
            raise ScratchError("the study's design isn't the one this replication continues (its seed, people or "
                               f"last day): {self.study_out / 'design.json'}")
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
                raise ScratchError(f"this replication is pinned to build {str(saved.get('commit', ''))[:12]} (code "
                                   f"{str(saved.get('code_hash', ''))[:12]}); this one differs in {', '.join(changed)}: "
                                   "another build runs only under an amendment")
            return saved
        days = unchanged_days(self.design)
        if not days["unchanged"]:
            raise ScratchError(f"generating to day {self.design.fresh_days[1]} changed requests of days "
                               f"{self.design.study_days[0]}-{self.design.study_days[1]}: {days['by_person']}")
        fresh = fresh_block(self.design)
        write_json(self.out / "fresh.json", {"days": list(self.design.fresh_days), "study_days": days,
                                              "requests": fresh})
        write_json(self.out / "design.json", {"replication": self.design.to_dict(), "study_design": study_design,
                                              "study_out": str(self.study_out), "study_data": str(self.study_data)})
        write_json(path, {**current, "pinned_at": time.strftime("%Y-%m-%d %H:%M:%S")})  # last: it marks the pin
        return current

    def check_models(self, before: str) -> dict[str, Any]:
        files = {model: item[2] for model, item in model_files(self.study_out, self.study_data).items()}
        self.say(f"checking the three model files' SHA-256 before {before}")
        return check_models(self.out, files, before=before, hasher=self.hasher)

    # ── Children ────────────────────────────────────────────────────────

    def spent(self, *, besides: str = "") -> int:
        """ChatGPT-lane tokens the replication has spent (each child's own count), but ``besides``'s."""

        return sum(int(read_json(self.out / child / "state.json").get("cloud_tokens") or 0)
                   for child in CHILDREN if child != besides)

    def child(self, task: str) -> None:
        data = self.data(task)
        data.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "tests.e2e.learning.scaling.replicate_worker", task, "--out", str(self.out),
                   "--data", str(data), "--lane", self.args.lane, "--cap-tokens", str(self.args.cap_tokens),
                   "--spent-before", str(self.spent(besides=task))]
        if task == "teacher" and self.args.eval_profile and not self.args.dry_run:
            command += ["--eval-profile", str(self.args.eval_profile)]
        if task == "local" and self.args.gpu_booking:
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

    def stage_evaluate(self) -> None:
        self.check_models("evaluate")
        for child in CHILDREN:
            if not self.done(child):
                self.child(child)

    def stage_analyse(self) -> dict[str, Any]:
        from tests.e2e.learning.scaling import replicate_analysis

        self.check_models("analyse")
        if not all(self.done(child) for child in CHILDREN):
            raise Stopped("analyse: the results are computed only when every arm has finished (§8)")
        runs = [item for child in CHILDREN for item in read_lines(self.out / child / "runs.jsonl")]
        report = replicate_analysis.build_report(
            runs, design=self.design, fresh=read_json(self.out / "fresh.json")["requests"],
            states={child: read_json(self.out / child / "state.json") for child in CHILDREN})
        report.update({"prereg": {"file": PREREG, "version": PREREG_VERSION, "sha256": PREREG_SHA256,
                                  "amendments": list(AMENDMENTS)},
                       "build": read_json(self.out / "build.json"), "models": read_json(self.out / "models.json"),
                       "teacher": read_json(self.out / "teacher.json")
                       or {"model_ref": read_json(self.out / "teacher" / "state.json").get("model_ref")}})
        write_json(self.out / "analyse" / "report.json", report)
        (self.out / "analyse" / "report.md").write_text(replicate_analysis.markdown(report), encoding="utf-8",
                                                         newline="\n")
        self.say(f"Report: {self.out / 'analyse' / 'report.md'}")
        return report

    def run(self, stages: Sequence[str]) -> int:
        unknown = [stage for stage in stages if stage not in STAGES]
        if unknown or not stages:
            raise ScratchError(f"name the stages to run, from: {', '.join(STAGES)}")
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
    parser = argparse.ArgumentParser(description="Row-Bot Learning's replication (PREREG-REPLICATION.md v1)")
    parser.add_argument("stages", nargs="*", help=f"the stages to run: {', '.join(STAGES)}")
    parser.add_argument("--out", required=True, help="where the replication's state, runs and report go")
    parser.add_argument("--scratch", required=True, help="where its scratch profile goes")
    parser.add_argument("--study-out", default="", help="the finished study's out folder (read only)")
    parser.add_argument("--study-data", default="", help="the study's evaluation profile, whose server lists the "
                                                         "three GGUFs (read only)")
    parser.add_argument("--eval-profile", default=os.environ.get("ROW_BOT_LIVE_EVAL_CODEX_PROFILE", ""),
                        help="the ChatGPT lane's eval profile (its sign-in is read into the teacher's memory)")
    parser.add_argument("--gpu-booking", default="", help="the shared GPU booking file")
    parser.add_argument("--lane", default=LANE)
    parser.add_argument("--cap-tokens", type=int, default=200_000_000,
                        help="the ChatGPT-lane tokens the whole replication may spend (§8: 200M)")
    parser.add_argument("--plan", action="store_true", help="print the plan and run nothing")
    parser.add_argument("--dry-run", action="store_true", help="a made-up study and the scripted model (no GPU)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # a log file on Windows is cp1252 otherwise
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    args = parse(argv)
    try:
        replication = Replication(args)
        if args.plan:
            print(json.dumps(plan(replication.design), indent=1, ensure_ascii=False))
            return 0
        return replication.run(args.stages or (list(STAGES) if args.dry_run else []))
    except ScratchError as error:
        print(f"scaling replication: {error}", file=sys.stderr)
        return 2


__all__ = ["ARMS", "CONTEXT_OF", "DESIGN", "DRY_DESIGN", "GGUFS", "Halt", "LOCAL", "MODELS", "MODEL_OF",
           "ModelChanged", "PREREG_SHA256", "Replication", "ReplicationDesign", "STAGES", "blocks", "check_models",
           "dry_study", "fresh_block", "main", "model_files", "parse", "plan", "repeated_wording", "sha256_file",
           "unchanged_days"]


if __name__ == "__main__":
    sys.exit(main())
