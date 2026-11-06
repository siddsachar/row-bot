"""The controlled study's fixed design (``paper/PREREG-SCALING.md`` v1 with amendment SA1) and its plan (SC1).

Every number of the pre-registration lives in :class:`Design`; :data:`DESIGN` is the study as fixed on Thu 5 Nov
2026 and amended by SA1 on Fri 6 Nov (``AMENDMENTS-SCALING.md``): the training block is days 1-250 and the held-out
block days 251-290, and every N counts **training targets** (the teacher's messages a row trains on, weight 1). A dry
run (``dry_run.py``) uses a smaller design of the same shape (one person, a few days, small subsets), so the same code
runs both. The design is part of the build pin (``study.py``): a run with another design refuses.

:func:`plan` says what the study would do, from the generator alone: per person the training-block and held-out
requests (the generator's own per-day counts), the expected pool (an estimate, in rows and targets) and the subset
sizes it is expected to allow, the runs per arm, and the ChatGPT-lane tokens and GPU-hours it expects. It runs nothing
and reaches nothing.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict, dataclass, fields
from typing import Any

PREREG = "PREREG-SCALING.md"
PREREG_VERSION = 1
PREREG_SHA256 = "2461e8cfb33532e6b2df7047e4ce2dd274202a7d8e836596a80a04bb70c484db"
AMENDMENTS = ("SA1",)

#: The arms of §4, in the order they are evaluated (the teacher last: its replay cases are built as it runs, and
#: replay agreement follows every arm's runs). ``start`` is first: the hidden packs' paired analysis is against it.
ARMS = ("start", "sft-100", "sft-300", "sft-1000", "sft-3000", "sft-1000b", "dagger-1000", "retrieval",
        "local-27b", "teacher")
SFT = ("sft-100", "sft-300", "sft-1000", "sft-3000")
TRAINED = (*SFT, "sft-1000b", "dagger-1000")
#: The round-1 DAgger model: trained (the student of round 2), never an evaluated arm.
DAGGER_ROUND_1 = "dagger-r1"
#: The confirmatory family (§5): (name, arm, against, sides).
HYPOTHESES = (("S1", "sft-3000", "start", "superiority"), ("S2", "dagger-1000", "sft-1000", "two-sided"),
              ("S3", "sft-1000", "retrieval", "two-sided"))

# ── What --plan estimates with (rough; each says where it comes from) ─────────
#: A training-block request with the simulated person (a turn or two, a correction now and then): the release
#: check's pilots (``release_check.PLAN_TOKENS_PER_REQUEST``).
TOKENS_PER_POOL_REQUEST = 50_000
#: One evaluation run of the teacher (the request's first message only): taken as a pool request, to err high.
TOKENS_PER_TEACHER_RUN = 50_000
#: One relabelling call at a student's prefix (Row-Bot's 32K-profile prompt is about 10.7K tokens, L0d) and its answer.
TOKENS_PER_RELABEL = 15_000
#: The teacher re-asked at its own captured prefix (the full-window envelope, about 18.5K tokens of schemas).
TOKENS_PER_TEACHER_REPLAY = 25_000
#: Turns a request can take: the first message and the simulated person's two corrections (``person.MAX_CORRECTIONS``).
MAX_TURNS = 3
#: Training targets a pool row holds: an estimate, from the earlier seeded runs (SA1); the pool says the real count.
TARGETS_PER_ROW = 2.7
#: Model calls per request (the seeded work reads, then writes or answers): sizes the replay set and the DAgger rounds.
DECISIONS_PER_REQUEST = 2.5
#: The 32 GB recipe's own evidence (L2x, O121): 8.14 s a step, the last pass ready in 218 s.
SECONDS_PER_STEP = 8.14
SECONDS_TO_READY = 218.0
#: §8's own figure: about 2 days for 9 local arms x 152 tasks x 3 runs (about 42 s a run). SA1 keeps it.
SECONDS_PER_LOCAL_RUN = 42.0
SECONDS_PER_LOCAL_REPLAY = 5.0


@dataclass(frozen=True)
class Design:
    """The study's numbers (§2-§8). The defaults are the pre-registration's, as amended."""

    people: tuple[str, ...] = ("idris", "mara", "wren")
    seed: int = 20261105
    #: SA1: days 1-250 train, days 251-290 are held out (§3 had 1-100 and 101-120).
    training_days: tuple[int, int] = (1, 250)
    held_out_days: tuple[int, int] = (251, 290)
    subset_seed: int = 20261106
    #: N of sft-100, sft-300, sft-1000 and sft-3000 in training targets (SA1), nested: 100 ⊂ 300 ⊂ 1,000 ⊂ 3,000.
    sizes: tuple[int, int, int, int] = (100, 300, 1000, 3000)
    #: sft-1000b's training seed (the recipe's own is 3407).
    second_seed: int = 3408
    #: Each DAgger round's targets (a DAgger row has one).
    dagger_targets: int = 500
    retrieval_k: int = 4
    runs: int = 3
    task_tokens: int = 150_000
    seconds: float = 900.0
    analysis_seed: int = 20261022
    resamples: int = 10_000
    permutations: int = 10_000
    min_tasks: int = 60
    margin: float = 0.10
    #: A12's rule: gap closed is unbounded when more than this share of resamples has a non-positive denominator.
    unbounded_share: float = 0.025
    hidden_tasks: int = 32
    #: The packs whose hidden tasks run (empty: all of them).
    hidden_packs: tuple[str, ...] = ()
    tier: str = "32gb"
    local_27b: str = "qwen3.8:27b"
    dry_run: bool = False

    def size_of(self, arm: str) -> int:
        """The training targets an arm trains on or retrieves from (as designed; the pool may hold fewer)."""

        by_arm = dict(zip(SFT, self.sizes))
        by_arm.update({"sft-1000b": self.sizes[2], "retrieval": self.sizes[2], "dagger-1000": 2 * self.dagger_targets,
                       DAGGER_ROUND_1: self.dagger_targets})
        return by_arm[arm]

    def subset_of(self, arm: str) -> int:
        """The nested subset an SFT arm (or retrieval) uses, by its designed N."""

        return {"sft-1000b": self.sizes[2], "retrieval": self.sizes[2]}.get(arm, dict(zip(SFT, self.sizes)).get(arm, 0))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Design:
        known = {item.name for item in fields(cls)}
        values = {key: tuple(value) if isinstance(value, list) else value for key, value in data.items() if key in known}
        return cls(**values)


DESIGN = Design()


def blocks(design: Design, person: str) -> tuple[list[Any], list[Any]]:
    """The person's training-block and held-out-block requests (the generator's own, after the 30-day past)."""

    from tests.e2e.learning.seeded import generator

    history = generator.generate(person, seed=design.seed, days=design.held_out_days[1])
    first, last = design.training_days
    start, end = design.held_out_days
    return ([item for item in history.stream if first <= item.day <= last],
            [item for item in history.stream if start <= item.day <= end])


def subset_sizes(design: Design, pool: int) -> dict[str, Any]:
    """What §3 allows for a pool of ``pool`` training targets: each N as drawn, and an N the pool can't reach (only the
    largest N falls back to the whole pool; a smaller one has no rule and needs an amendment)."""

    found: dict[str, Any] = {}
    largest = max(design.sizes)
    for arm, wanted in zip(SFT, design.sizes):
        if wanted <= pool:
            found[arm] = {"n": wanted, "state": "drawn"}
        elif wanted == largest:
            found[arm] = {"n": pool, "state": "whole_pool", "why": f"the pool holds {pool:,} targets, fewer than "
                          f"{wanted:,}: the largest N is the whole pool (§3)"}
        else:
            found[arm] = {"n": None, "state": "not_buildable",
                          "why": f"the pool holds {pool:,} targets, fewer than {wanted:,}; §3 covers only the largest "
                                 "N, so this subset needs an amendment"}
    return found


def plan(design: Design = DESIGN) -> dict[str, Any]:
    """The study's plan (see the module's docstring): nothing runs, nothing is reached."""

    people: dict[str, Any] = {}
    person_days = 0
    for person in design.people:
        training, held = blocks(design, person)
        person_days += len({item.day for item in held})
        people[person] = {
            "training_block": {"requests": len(training), "by_template": dict(Counter(item.template for item in training)),
                               "ids": [f"{item.id} d{item.day} {item.template}" for item in training]},
            "held_out_block": {"requests": len(held), "by_template": dict(Counter(item.template for item in held)),
                               "ids": [f"{item.id} d{item.day} {item.template}" for item in held]},
        }
    training_requests = sum(item["training_block"]["requests"] for item in people.values())
    held_requests = sum(item["held_out_block"]["requests"] for item in people.values())
    # The product's dataset makes one row per turn and leaves out a turn the person corrected, taught or stopped,
    # so a request gives about one row (its last turn); one whose correction Learning's rule doesn't recognise keeps
    # its corrected turns too (at most MAX_TURNS). The 30-day past's imported rows are kept out of the pool.
    pool = training_requests
    targets = round(pool * TARGETS_PER_ROW)
    sizes = subset_sizes(design, targets)
    local = [arm for arm in ARMS if arm != "teacher"]
    tasks = held_requests + design.hidden_tasks
    runs_per_arm = tasks * design.runs
    replay_cases = math.ceil(held_requests * DECISIONS_PER_REQUEST)
    dagger_requests = math.ceil(2 * design.dagger_targets / DECISIONS_PER_REQUEST)
    tokens = {
        "pool": training_requests * TOKENS_PER_POOL_REQUEST,
        "relabelling": 2 * design.dagger_targets * TOKENS_PER_RELABEL,
        "teacher_arm": runs_per_arm * TOKENS_PER_TEACHER_RUN,
        "teacher_replay": replay_cases * design.runs * TOKENS_PER_TEACHER_REPLAY,
    }
    from row_bot.learning.training import recipes

    recipe = recipes.load(design.tier)
    trained = {arm: design.size_of(arm) for arm in ("dagger-1000", DAGGER_ROUND_1)}
    trained.update({arm: sizes[arm]["n"] for arm in SFT})
    trained["sft-1000b"] = sizes["sft-1000"]["n"]
    # Steps follow rows: a pool row holds about TARGETS_PER_ROW targets, a DAgger row one.
    rows_of = {arm: (count if arm in ("dagger-1000", DAGGER_ROUND_1) else count / TARGETS_PER_ROW)
               for arm, count in trained.items() if count}
    training_seconds = {arm: recipe.steps_for(math.floor(rows * (1 - recipe.validation_share))) * SECONDS_PER_STEP
                        + SECONDS_TO_READY for arm, rows in rows_of.items()}
    gpu = {
        "training": sum(training_seconds.values()) / 3600,
        "dagger_student_runs": dagger_requests * SECONDS_PER_LOCAL_RUN / 3600,
        "evaluation_runs": len(local) * runs_per_arm * SECONDS_PER_LOCAL_RUN / 3600,
        "replay": len(local) * replay_cases * design.runs * SECONDS_PER_LOCAL_REPLAY / 3600,
    }
    return {
        "prereg": {"file": PREREG, "version": PREREG_VERSION, "sha256": PREREG_SHA256, "amendments": list(AMENDMENTS)},
        "design": design.to_dict(),
        "blocks": {"training": {"days": list(design.training_days), "requests": training_requests},
                   "held_out": {"days": list(design.held_out_days), "requests": held_requests,
                                "person_days": person_days}},
        "people": people,
        "hidden_pack_tasks": {"count": design.hidden_tasks, "note": "sealed (E10): counted here, run only through "
                              "the audit vault, never shown"},
        "pool": {"training_requests": training_requests, "expected_rows": pool,
                 "most_rows": training_requests * MAX_TURNS, "expected_targets": targets,
                 "targets_per_row": TARGETS_PER_ROW, "expected_to_reach": {str(n): targets >= n for n in design.sizes},
                 "note": "an estimate: about one row per training-block request (the product's dataset makes one row "
                         "per turn and leaves out the turns the person corrected, taught or stopped; a correction "
                         "Learning's rule doesn't recognise keeps its corrected turns in, at most three turns a "
                         "request), and about 2.7 training targets a row (the earlier seeded runs, SA1). The 30-day "
                         "past's imported rows are kept out. Which N the pool reaches is known once it is built: the "
                         "subsets stage refuses an N other than the largest that it can't reach"},
        "subsets": sizes,
        "arms": {arm: {"trains_on_targets": trained.get(arm), "local": arm != "teacher"} for arm in ARMS},
        "runs": {"held_out_requests": held_requests, "hidden_pack_tasks": design.hidden_tasks,
                 "runs_per_task": design.runs, "runs_per_arm": runs_per_arm, "arms": len(ARMS),
                 "runs_total": runs_per_arm * len(ARMS), "dagger_student_requests": dagger_requests,
                 "replay_cases": replay_cases, "replay_runs_total": replay_cases * design.runs * len(ARMS)},
        "chatgpt_lane_tokens": {**tokens, "total": sum(tokens.values()),
                                "expected": "150-200M (§8); about 180M (SA1)"},
        "gpu_hours": {**{key: round(value, 1) for key, value in gpu.items()},
                      "total": round(sum(gpu.values()), 1),
                      "expected": "about 10 h training and 2 days evaluation (§8); about 130 h (SA1)"},
        "budget_per_run": {"tokens": design.task_tokens, "steps": "the task's own step limit",
                           "seconds": design.seconds},
    }


__all__ = ["AMENDMENTS", "ARMS", "DAGGER_ROUND_1", "DESIGN", "Design", "HYPOTHESES", "SFT", "TARGETS_PER_ROW", "TRAINED",
           "blocks", "plan", "subset_sizes"]
