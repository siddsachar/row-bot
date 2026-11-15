"""Planning personal-model training from the dataset (L2c).

The quiet-hours runner asks the planner, once a while, whether a run should be planned:

- the computer can train (the engine and the starting model are installed);
- the dataset (``dataset.py``) holds at least the recipe's minimum of examples, or the Personal
  model says "Needs more examples" (with the count so far);
- since the newest version, enough new examples arrived (the recipe's ``min_new_examples``), or
  the version is up to date.

Then the dataset is compiled for real (the audit vault takes its share first, and every row is
measured with the engine's own tokenizer and chat template, out of Row-Bot's process), the job is
planned with the recipe of this computer's tier (steps for one pass over the rows, checkpoints every
``checkpoint_every`` steps) and its rows are written into the job's own lease folder, so starting it
later needs no work under the supervisor's lock. The job then waits for quiet hours or Train now.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from row_bot import learning as gate
from row_bot.learning.training import dataset as dataset_module

logger = logging.getLogger(__name__)

SUMMARY_SECONDS = 600.0
PLAN_SECONDS = 1800.0
MEASURE_SECONDS = 900.0


@dataclass(frozen=True)
class PlanOutcome:
    """What planning did: a job, or why not (``no_data``, ``up_to_date``, ``no_engine``, ``failed``)."""

    job: Any = None
    reason: str | None = None
    summary: dataset_module.DatasetSummary | None = None


def engine_measure(rows: Sequence[Mapping[str, Any]], max_seq: int, *, root: Path | None = None,
                   engine_status: Any = None, weights_folder: Path | None = None) -> list[tuple[int, int]]:
    """Each row's tokens and target tokens, counted by the installed engine (``entry.py measure``):
    the starting model's own tokenizer and chat template, as training renders them."""

    from row_bot import learning
    from row_bot.learning.training import engine, weights

    found = engine_status or engine.status(root)
    if weights_folder is None:
        model = weights.MODELS.get(learning.read_settings(root).starting_model, weights.QWEN35_9B)
        weights_folder = weights.status(model, root).folder
    if found.python is None or found.site is None or weights_folder is None:
        raise dataset_module.DatasetError("no_engine")
    work = Path(tempfile.mkdtemp(prefix="measure-", dir=str(engine.cache_dir(root).parent)
                                 if engine.cache_dir(root).parent.is_dir() else None))
    try:
        rows_path, out = work / "rows.jsonl", work / "lengths.json"
        rows_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        env = engine._base_environment(work / "home")  # noqa: SLF001 - the engine's own clean environment
        env.update(engine.engine_variables(root))
        argv = [str(found.python), "-I", "-S", str(engine.ENTRY), "measure", "--site", str(found.site),
                "--model", str(weights_folder), "--rows", str(rows_path), "--out", str(out)]
        result = engine.run_owned(argv, cwd=work / "home", env=env, timeout=MEASURE_SECONDS, check=lambda: None)
        try:
            lengths = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            lengths = None
        if result.code != 0 or not isinstance(lengths, list) or len(lengths) != len(rows):
            logger.warning("Learning couldn't measure its training rows (exit %s)", result.code)
            raise dataset_module.DatasetError("measure_failed")
        return [(int(item[0]), int(item[1])) for item in lengths]
    finally:
        shutil.rmtree(work, ignore_errors=True)  # the rows are learning data: never left behind


def workspace_roots() -> tuple[str, ...]:
    """The workspace folders tools work in (paths in targets are kept relative to them, O33)."""

    try:
        from row_bot.tools import registry

        root = str(registry.get_tool_config("filesystem", "workspace_root", "") or "")
        return (root,) if root else ()
    except Exception:
        return ()


class Planner:
    """Decides when a run is planned, and plans it (see the module's docstring)."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        jobs: Any,
        engine: Any,
        clock: Callable[[], float] = time.time,
        recipe_for: Callable[[], Any] | None = None,
        measure: Callable[..., Any] | None = None,
        lookup: Any = None,
        roots: Callable[[], Sequence[str]] = workspace_roots,
        trained_ids: Callable[[], set[str]] | None = None,
        backend: str = "local",
    ) -> None:
        self._root = root
        self.jobs = jobs
        self.engine = engine
        self.clock = clock
        self._recipe_for = recipe_for
        self._measure = measure
        self._lookup = lookup
        self._roots = roots
        self._trained_ids = trained_ids
        # C1: "hugging_face" plans runs for the Hugging Face backend (``engine`` is the remote trainer). This
        # computer may have no engine to count tokens with, so rows are kept by their estimated lengths and the
        # engine leaves out any that don't fit when it trains (its own rule, the same as here).
        self.backend = backend
        self._lock = threading.Lock()
        self._summary: dataset_module.DatasetSummary | None = None
        self._planned_at = 0.0

    @property
    def root(self) -> Path:
        return self._root if self._root is not None else gate.data_dir()

    def recipe(self) -> Any:
        """The recipe the next run trains with: the tier's certified one, with Advanced's choices (D9)."""

        from row_bot.learning.training import advanced

        if self._recipe_for is not None:
            return advanced.apply(self._recipe_for(), self._root)
        from row_bot.learning.training import recipes

        return advanced.apply(recipes.current(self._root), self._root)

    def refresh(self, job: Any) -> Any:
        """A run that hasn't trained a step starts with the recipe as it is now (O117): this computer's
        recipe with the person's own Advanced choices, not the one current when it was planned (a recipe
        update would otherwise wait a run). Its steps follow the recipe's passes; its rows stay."""

        plan = self.jobs.plan_of(job)
        examples = int((plan.get("dataset") or {}).get("examples") or 0)
        if examples <= 0:
            return job
        recipe = self.recipe()
        steps = recipe.steps_for(examples)
        spec = recipe.spec(None if self.backend == "hugging_face" else _card_mib())
        if spec == plan["recipe"] and steps == job.steps_total:
            return job
        logger.info("Learning starts personal model v%s with the recipe as it is now (%s, %d steps)", job.version,
                    recipe.name, steps)
        return self.jobs.replan(job, steps_total=steps, checkpoint_every=recipe.checkpoint_interval(steps),
                                recipe=spec)

    def trained_ids(self) -> set[str]:
        if self._trained_ids is not None:
            return self._trained_ids()
        from row_bot.learning.training import releases

        return releases.trained_example_ids(self._root)

    def _measure_rows(self, rows: Sequence[Mapping[str, Any]], max_seq: int) -> Any:
        if self._measure is not None:
            return self._measure(rows, max_seq)
        return engine_measure(rows, max_seq, root=self._root)

    def _measurer(self) -> Any:
        return None if self.backend == "hugging_face" and self._measure is None else self._measure_rows

    # ── The count the Personal model view shows ─────────────────────────

    def summary(self, *, compute: bool = True) -> dataset_module.DatasetSummary | None:
        """How many examples there are (estimated lengths; nothing is written), cached for a while."""

        with self._lock:
            cached = self._summary
            if cached is not None and (not compute or self.clock() - cached.at < SUMMARY_SECONDS):
                return cached
            if not compute or not self.jobs.store.exists():
                return cached
            recipe = self.recipe()
            try:
                compiled = dataset_module.compile_dataset(self.jobs.store, recipe, lookup=self._lookup,
                                                          workspace_roots=self._roots(), refresh_vault=False)
            except dataset_module.DatasetError:
                return cached
            except Exception:
                logger.warning("Learning couldn't count its examples", exc_info=True)
                return cached
            self._summary = dataset_module.summarize(compiled, recipe, trained_ids=self.trained_ids(),
                                                     at=float(self.clock()))
            return self._summary

    def forget(self) -> None:
        with self._lock:
            self._summary = None
            self._planned_at = 0.0

    # ── Planning ─────────────────────────────────────────────────────────

    def due(self) -> bool:
        return self.clock() - self._planned_at >= PLAN_SECONDS

    def plan(self, *, force: bool = False, retrain: bool = False) -> PlanOutcome:
        """Plan a run when there is enough (new) work to train on. Never while one is current.

        ``retrain``: "Retrain without it" (O6): a deleted chat's examples are already gone from the
        data, so the next version is planned now, without waiting for new examples."""

        if not force and not self.due():
            return PlanOutcome(reason="later", summary=self._summary)
        self._planned_at = float(self.clock())
        if not self.engine.ready():
            return PlanOutcome(reason="no_engine")
        if self.jobs.store.exists():
            from row_bot.learning.training import releases

            if releases.holds_back(self.jobs.current(), self._root):
                return PlanOutcome(reason="current")
        summary = self.summary(compute=True)
        if summary is None:
            return PlanOutcome(reason="no_data")
        recipe = self.recipe()
        if not summary.enough:
            return PlanOutcome(reason="no_data", summary=summary)
        trained = self.trained_ids()
        if trained and summary.new_examples < recipe.min_new_examples and not retrain:
            return PlanOutcome(reason="up_to_date", summary=summary)
        try:
            compiled = dataset_module.compile_dataset(
                self.jobs.store, recipe, lookup=self._lookup, measure=self._measurer(),
                workspace_roots=self._roots(), refresh_vault=True)
        except dataset_module.DatasetError as error:
            logger.warning("Learning couldn't compile its training data (%s)", error.code)
            return PlanOutcome(reason="failed", summary=summary)
        with self._lock:
            self._summary = dataset_module.summarize(compiled, recipe, trained_ids=trained, at=float(self.clock()))
            summary = self._summary
        if compiled.examples < recipe.min_examples:
            return PlanOutcome(reason="no_data", summary=summary)
        if trained and summary.new_examples < recipe.min_new_examples and not retrain:
            return PlanOutcome(reason="up_to_date", summary=summary)
        steps = recipe.steps_for(compiled.examples)
        job = self.jobs.plan(steps_total=steps, checkpoint_every=recipe.checkpoint_interval(steps),
                             recipe=recipe.spec(None if self.backend == "hugging_face" else _card_mib()),
                             dataset={"id": compiled.id, "examples": compiled.examples}, backend=self.backend)
        lease(job, compiled, self.jobs.root)
        logger.info("Learning planned personal model v%s: %d examples, %d steps (%s)", job.version,
                    compiled.examples, steps, recipe.name)
        return PlanOutcome(job=job, summary=summary)


def _card_mib() -> int | None:
    """The graphics card's size, when the hardware check has run (the job's cap follows it, O112)."""

    from row_bot.learning.training import hardware

    found = hardware.peek()
    gpu = getattr(found, "gpu", None)
    return int(gpu.memory_mib) if gpu is not None else None


def lease(job: Any, compiled: dataset_module.CompiledDataset, root: Path | None = None) -> int:
    """The job's own data folder holds its rows (``examples.jsonl``) before it ever starts."""

    from row_bot.learning.training import jobs as jobs_module

    run = jobs_module.run_dir(job.id, root)
    count = dataset_module.write(compiled, run / "data")
    (run / "lease.json").write_text(json.dumps({"examples": count, "dataset": compiled.id}), encoding="utf-8")
    return count


class LearningDataset:
    """The supervisor's dataset (L2a's ``Dataset``): a job planned without its rows (after "Use
    recommended settings", say) gets them compiled now; a planned one already has them. A run that
    hasn't trained a step takes the recipe as it is now (:meth:`Planner.refresh`)."""

    def __init__(self, planner: Planner) -> None:
        self.planner = planner

    def starting(self, job: Any) -> Any:
        return self.planner.refresh(job)

    def prepare(self, job: Any, folder: Path) -> int:
        from row_bot.learning.training.supervisor import TrainingError

        recipe = self.planner.recipe()
        try:
            compiled = dataset_module.compile_dataset(self.planner.jobs.store, recipe, lookup=self.planner._lookup,
                                                      workspace_roots=self.planner._roots(), refresh_vault=True)
        except dataset_module.DatasetError as error:
            raise TrainingError("nothing_to_train") from error
        if compiled.examples <= 0:
            raise TrainingError("nothing_to_train")
        return dataset_module.write(compiled, Path(folder))


__all__ = ["LearningDataset", "PlanOutcome", "Planner", "engine_measure", "lease", "workspace_roots"]
