"""Comparing a personal-model version before anyone is asked to use it (L2c2; UX §5 "Versions").

When a version is ready (``training/releases.py``), Row-Bot compares three models **at the same
budget** (E12), through the real-runtime evaluation worker (O56, ``eval_worker.py``):

- the **new version**;
- the **version in use** (the newest one the person kept or is trying for any kind of work), or for a
  first version the **untouched starting model** (``training/base_model.py``);
- the **main model** (the person's default chat model). Its pack results are kept for 30 days per
  model, task, metric version and budget (O31), so a comparison doesn't spend tokens again on a
  result it already has. Local models are free (D25); the comparison stops cleanly at the monthly
  limit and says so.

On what:

- the **visible task packs** (the four kinds of work, D30), run as real Agent turns with the
  person's own tools, the bridge and the packs' fake apps;
- the **hidden split** through ``evaluation.audit_check`` (aggregates only, E10): the packs' hidden
  tasks and **replay** of the person's own held-out work (O18, O19);
- **coding tasks only with a container** (O21): without Docker or Podman they're left out and the
  result says "Coding tasks need Docker or Podman".

The result is counts with their denominators per kind of work, time per task and cloud tokens per
task, and a plain verdict per kind ("Better", "No clear difference", "Worse"). Training loss is never
evidence (L2c). **Every hidden problem runs 3 times per arm** (O84, O88; visible tasks once, a
parameter the paper's own runs set to 3), a problem counting as its pass fraction, and
``evaluation`` adds the pre-registered paired analysis (the mean paired difference with a 95% cluster
bootstrap interval and a permutation p-value) per kind and overall: visible, hidden, and both
together. The verdict rule is unchanged (a promotion rule, E4); what the interval would have said is
kept beside it (``interval_verdict``). It is saved beside the version (``comparison.json``: aggregates only, no hidden task
or its id) and its test runs in ``learning.db``; the Personal model view shows it as the versions
table and Overview's Needs you as "Personal model v1 is ready to compare".
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from row_bot import learning as gate
from row_bot.learning import kinds

logger = logging.getLogger(__name__)

COMPARISON_FILE = "comparison.json"
PROGRESS_FILE = "comparing.json"
FORMAT = "row_bot.learning.version_comparison"
ASKED_BY = "personal model comparison"
CODING_NOTE = "Coding tasks need Docker or Podman"
OWN_WORK = "own_work"
OWN_WORK_LABEL = "Your own work"
#: A version whose comparison failed is tried again after this long.
RETRY_SECONDS = 6 * 3600.0
#: Fewer tasks than this of a kind: "Too few tasks to tell".
MIN_TASKS = 5


@dataclass(frozen=True)
class Plan:
    """What a comparison runs (the product runs everything; the e2e may run fewer visible tasks)."""

    visible_per_pack: int = 16
    hidden: bool = True
    replay: bool = True
    task_tokens: int = 150_000
    # Runs per arm of each visible task and each hidden problem (hidden pack tasks and replay), O88.
    visible_repeats: int = 1
    hidden_repeats: int = 3


class CompareError(RuntimeError):
    """A comparison that can't run; ``code`` is stable (``not_ready``, ``no_main_model``, ``running``)."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _versions_dir(root: Path | None) -> Path:
    from row_bot.learning.training import releases

    return releases.versions_dir(root)


def comparison_path(version: int, root: Path | None = None) -> Path:
    return _versions_dir(root) / f"v{int(version)}" / COMPARISON_FILE


def progress_path(version: int, root: Path | None = None) -> Path:
    return _versions_dir(root) / f"v{int(version)}" / PROGRESS_FILE


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    from row_bot.learning import atomic

    atomic.write_text(path, json.dumps(value, indent=1, sort_keys=True))


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def read_comparison(version: int, root: Path | None = None) -> dict[str, Any] | None:
    found = _read_json(comparison_path(version, root))
    return found if found and found.get("format") == FORMAT else None


def read_progress(version: int, root: Path | None = None) -> dict[str, Any] | None:
    return _read_json(progress_path(version, root))


def mark_reviewed(version: int, *, root: Path | None = None, clock: Callable[[], float] = time.time) -> None:
    """The person looked at the comparison (Use, Not now): Needs you stops asking."""

    found = read_comparison(version, root)
    if found is not None and not found.get("reviewed_at"):
        found["reviewed_at"] = round(float(clock()), 3)
        _write_json(comparison_path(version, root), found)
        gate.changed()


# ── Verdicts ────────────────────────────────────────────────────────────────


def verdict(new: int, baseline: int, total: int) -> str:
    """``better`` / ``same`` / ``worse`` / ``too_few``: a difference counts only when it is at least
    two tasks and a tenth of them (small tests vary)."""

    if total < MIN_TASKS:
        return "too_few"
    margin = max(2, math.ceil(0.1 * total))
    if new - baseline >= margin:
        return "better"
    if baseline - new >= margin:
        return "worse"
    return "same"


def _combine(*tables: Mapping[str, Sequence[int]]) -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    families = {label: kind for kind, label in kinds.LABELS.items() if kind != kinds.OTHER}
    for table in tables:
        for label, (passed, total) in table.items():
            key = families.get(label, OWN_WORK)
            row = found.setdefault(key, [0, 0])
            row[0] += int(passed)
            row[1] += int(total)
    return found


def summarize(arms: Sequence[Mapping[str, Any]], tables: Mapping[str, Mapping[str, Sequence[int]]], *,
              new: str, baseline: str | None, baseline_label: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rows per kind of work (counts per arm, with the verdict of the new version against its
    baseline) and the overall line, in plain words."""

    rows: list[dict[str, Any]] = []
    order = [*kinds.FAMILIES, OWN_WORK]
    names = {item["key"]: item for item in arms}
    for kind in order:
        passed: dict[str, int] = {}
        counted: dict[str, int] = {}
        for key in names:
            found = tables.get(key, {}).get(kind)
            if found is None or int(found[1]) <= 0:
                continue
            passed[key] = int(found[0])
            counted[key] = int(found[1])
        if not passed or new not in passed:
            continue
        total = max(counted.values())
        if baseline and baseline in passed:
            # Equal tasks for every arm; a provider error leaves a task out of one arm's count (O19), so
            # the verdict compares rates scaled to the row's tasks.
            scaled_new = round(passed[new] / counted[new] * total)
            scaled_base = round(passed[baseline] / counted[baseline] * total)
            result = verdict(scaled_new, scaled_base, min(counted[new], counted[baseline]))
        else:
            result = "too_few"
        rows.append({"kind": kind, "label": kinds.LABELS.get(kind, OWN_WORK_LABEL), "total": total,
                     "passed": passed, "counted": counted, "verdict": result})
    better = [row for row in rows if row["verdict"] == "better" and row["kind"] in kinds.FAMILIES]
    worse = [row for row in rows if row["verdict"] == "worse"]
    recommend = max(better, key=lambda row: (row["passed"][new] - row["passed"][baseline]) / row["total"],
                    default=None) if baseline else None
    if not rows:
        overall = {"verdict": "unclear", "recommend": None, "line": "Nothing could be compared."}
    elif not baseline or not any(baseline in row["passed"] for row in rows):
        # Only the main model ran beside it: nothing says whether training helped (C1, 28 Oct).
        overall = {"verdict": "unclear", "recommend": None,
                   "line": f"Row-Bot couldn't run {baseline_label}, so it can't tell yet whether training helped. "
                           "Not used."}
    elif recommend is not None and not worse:
        overall = {"verdict": "better", "recommend": recommend["kind"],
                   "line": f"Better at {kinds.phrase(recommend['kind'])} than {baseline_label}: "
                           f"{recommend['passed'][new]} of {recommend['total']} passed, against "
                           f"{recommend['passed'][baseline]}."}
    elif recommend is not None:
        overall = {"verdict": "mixed", "recommend": recommend["kind"],
                   "line": f"Better at {kinds.phrase(recommend['kind'])}, worse at "
                           f"{', '.join(kinds.phrase(row['kind']) if row['kind'] in kinds.FAMILIES else 'your own work' for row in worse)}."}
    elif not worse and any(row["verdict"] == "better" and row["kind"] == OWN_WORK for row in rows):
        # Better at the person's own held-back work, with no kind of work to give it yet: said
        # plainly, and nothing is offered to use (the person can still choose a kind, D24).
        own = next(row for row in rows if row["kind"] == OWN_WORK)
        overall = {"verdict": "better", "recommend": None,
                   "line": f"Better at your own work than {baseline_label}: {own['passed'][new]} of {own['total']} "
                           f"passed, against {own['passed'][baseline]}. No kind of work stands out yet, so it "
                           "isn't used."}
    elif worse:
        first = worse[0]
        overall = {"verdict": "worse", "recommend": None,
                   "line": f"Worse than {baseline_label} at {kinds.phrase(first['kind']) if first['kind'] in kinds.FAMILIES else 'your own work'} "
                           f"({first['passed'][new]} of {first['total']}, against {first['passed'][baseline]}). Not used."}
    else:
        overall = {"verdict": "no_difference", "recommend": None,
                   "line": f"No clear difference from {baseline_label}. Not used."}
    return rows, overall


# ── Running one ─────────────────────────────────────────────────────────────


class Comparer:
    """Runs one version's comparison (see the module's docstring). Every collaborator is injectable
    for tests: the worker, the container check, the starting model, the main model, the meter."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        store: Any = None,
        clock: Callable[[], float] = time.time,
        plan: Plan = Plan(),
        worker_factory: Callable[..., Any] | None = None,
        gather: Callable[..., Any] | None = None,
        tools: Callable[[], list[str]] | None = None,
        container: Callable[[], Any] | None = None,
        baseline_model: Callable[..., tuple[str | None, str | None]] | None = None,
        main_ref: Callable[[], str] | None = None,
        model_name: Callable[[str], str] | None = None,
        meter: Callable[[], Any] | None = None,
        client: Any = None,
        record_usage: Callable[..., Any] | None = None,
        busy: Callable[[], bool] | None = None,
        serving_context: Callable[[str], int | None] | None = None,
        server_hold: Callable[[str], Any] | None = None,
        card_mib: Callable[[], int | None] | None = None,
        server_slots: Callable[[], int] | None = None,
    ) -> None:
        self._root = root
        self._store = store
        self.clock = clock
        self.plan = plan
        self._worker_factory = worker_factory
        self._gather = gather
        self._tools = tools
        self._container = container
        self._baseline_model = baseline_model
        self._main_ref = main_ref
        self._model_name = model_name
        self._meter = meter
        self._client = client
        self._record_usage = record_usage
        self._busy = busy
        self._serving_context = serving_context
        self._server_hold = server_hold
        self._card_mib = card_mib
        self._server_slots = server_slots

    @property
    def root(self) -> Path:
        return self._root if self._root is not None else gate.data_dir()

    @property
    def store(self) -> Any:
        if self._store is None:
            from row_bot.learning.store import LearningStore

            self._store = LearningStore(self._root)
        return self._store

    # ── Collaborators (the app's defaults) ─────────────────────────────

    def _main(self) -> str:
        if self._main_ref is not None:
            return self._main_ref()
        from row_bot.learning import routing

        return routing.main_model_ref()

    def _name(self, ref: str) -> str:
        if self._model_name is not None:
            return self._model_name(ref)
        from row_bot.learning import routing

        return routing.model_name(ref)

    def _has_container(self) -> bool:
        probe = self._container
        if probe is None:
            from row_bot.learning.sandbox import container_runtime as probe
        try:
            return bool(probe())
        except Exception:  # noqa: BLE001
            return False

    def _starting_model(self, runtime: str = "ollama", version: int | None = None) -> tuple[str | None, str | None]:
        """The version's own untouched starting model (O124: the person may have changed the starting model
        since it was trained), served in ``runtime`` (where the new version runs)."""

        if self._baseline_model is not None:
            return self._baseline_model(runtime)
        from row_bot.learning.training import base_model, engine, hardware, recipes, weights

        model = _trained_from(version, self._root) or weights.MODELS.get(
            gate.read_settings(self._root).starting_model, weights.QWEN35_9B)
        found = weights.status(model, self._root)
        built = base_model.read(model.id, self._root)
        # A version trained on Hugging Face brings its untouched starting model as a GGUF the job built (C1): it
        # runs here without the starting model's own files on this computer.
        built_here = built is not None and bool(built.gguf) and             (base_model.folder(model.id, self._root) / str(built.gguf)).is_file()
        if found.state != "downloaded" and not built_here:
            return None, "engine_missing"
        recipe = recipes.for_model(model.id, hardware.peek()) or recipes.current(self._root)
        return base_model.ensure(root=self._root, engine_status=engine.status(self._root), model=model,
                                 weights_folder=found.folder if found.state == "downloaded" else None,
                                 weights_bytes=model.weights_bytes or model.download_bytes,
                                 recipe=recipe, runtime=runtime)

    def _baseline(self, version: int, runtime: str = "ollama") -> tuple[str, str, str | None, str | None, str | None]:
        """(key, label, model_ref, release id, why it couldn't run) of the arm the new version is
        compared with: the newest other version in use, else the untouched starting model served where
        the new version runs (``runtime``)."""

        from row_bot.learning import use
        from row_bot.learning.training import releases

        in_use = sorted({item.version for item in use.read(self._root).assignments.values()
                         if item.version != int(version)}, reverse=True)
        for other in in_use:
            state = releases.read_state(releases.version_dir(other, self._root))
            if state is not None and state.phase == releases.READY and state.model_ref:
                return f"v{other}", f"v{other} (in use)", state.model_ref, state.release_id or None, None
        ref, reason = self._starting_model(runtime, version)
        return "base", "the starting model", ref, None, reason

    # ── The run ─────────────────────────────────────────────────────────

    def _progress(self, version: int, **values: Any) -> None:
        current = read_progress(version, self._root) or {}
        current.update(values)
        current["updated_at"] = round(float(self.clock()), 3)
        try:
            _write_json(progress_path(version, self._root), current)
        except OSError:
            logger.debug("Learning couldn't save the comparison's progress", exc_info=True)
        if "state" in values or "phase" in values:
            # Started, a new phase, or ended (done, paused, stopped, failed): the screens read it again (O127).
            gate.changed()

    def run(self, version: int, *, stop: threading.Event | None = None) -> dict[str, Any]:
        """Compare ``version`` now; returns (and saves) the result."""

        from row_bot.learning.training import releases

        stop = stop or threading.Event()
        state = releases.read_state(releases.version_dir(int(version), self._root))
        if state is None or state.phase != releases.READY or not state.model_ref:
            raise CompareError("not_ready")
        with self._server_held(state.runtime):
            return self._run(version, stop, state)

    def _card(self) -> int | None:
        if self._card_mib is not None:
            return self._card_mib()
        from row_bot.learning.training import hardware

        found = hardware.peek()
        return int(found.gpu.memory_mib) if found is not None and found.gpu is not None else None

    def _local_sizes(self, version: int, state: Any, base_key: str) -> list[int]:
        """The model files of the comparison's two local models (the new version and its baseline)."""

        from row_bot.learning.training import base_model, releases, weights

        sizes = [int(getattr(state, "gguf_bytes", 0) or 0)]
        if base_key == "base":
            model = _trained_from(version, self._root) or weights.MODELS.get(
                gate.read_settings(self._root).starting_model, weights.QWEN35_9B)
            built = base_model.read(model.id, self._root)
            sizes.append(int(built.gguf_bytes) if built is not None else 0)
        elif base_key.startswith("v") and base_key[1:].isdigit():
            other = releases.read_state(releases.version_dir(int(base_key[1:]), self._root))
            sizes.append(int(getattr(other, "gguf_bytes", 0) or 0) if other is not None else 0)
        return sizes

    def _slots(self, runtime: str) -> int | None:
        """How many models Row-Bot's own server keeps loaded side by side (its ``--models-max``, set by the
        largest model registered there and the card); None for another runtime."""

        if self._server_slots is not None:
            return self._server_slots()
        from row_bot.learning.training import llama_server, serving

        if runtime != serving.ROW_BOT:
            return None
        try:
            server = llama_server.get(self._root)
            return llama_server.models_max([llama_server._file_bytes(entry.gguf) for entry in server.models()],  # noqa: SLF001
                                           server._card_bytes())  # noqa: SLF001
        except Exception:  # noqa: BLE001 - unknown: the sizes decide
            return None

    def _order(self, sizes: list[int], runtime: str = "") -> str:
        """``model`` when the two local models can't stay loaded together, so each is loaded once rather than for
        every task (28 Oct: a 27B and its 27B starting model on a 32 GB card swapped each task, about 16 s a task;
        and the 9B pair swapped too, about 9 s a task, because Row-Bot's own server keeps one model at a time once
        a 27B is registered there). Row-Bot's own server says how many it keeps (its ``--models-max``); otherwise
        each model needs about 1.25 times its file and 1 GB (the 9B's and 27B's measured needs)."""

        known = [size for size in sizes if size > 0]
        if len(known) < 2:
            return "interleaved"
        slots = self._slots(runtime) if runtime else None
        if slots is not None:
            return "model" if slots < 2 else "interleaved"
        card = self._card()
        need = sum(int(size / 2**20 * 1.25) + 1024 for size in known)
        return "model" if card and need > card else "interleaved"

    def _server_held(self, runtime: str) -> Any:
        """Row-Bot's own model server (O75) stays running for the whole comparison when the version
        runs there (its starting model is served there too): idle unloading never stops it between
        tasks, and its key, handed to the evaluation worker, stays valid."""

        if self._server_hold is not None:
            return self._server_hold(runtime)
        from row_bot.learning.training import serving

        if runtime != serving.ROW_BOT:
            return contextlib.nullcontext()
        from row_bot.learning.training import llama_server

        return llama_server.get(self._root).hold()

    def _run(self, version: int, stop: threading.Event, state: Any) -> dict[str, Any]:
        from row_bot.learning import eval_worker, evaluation
        from row_bot.learning.packs import PACKS, visible_tasks

        started = float(self.clock())
        self._progress(version, state="running", phase="starting", done=0, total=0, started_at=started,
                       reason=None)
        main_ref = self._main()
        base_key, base_label, base_ref, base_release, base_reason = self._baseline(version, state.runtime)
        container = self._has_container()
        new_key = f"v{int(version)}"
        arms: list[evaluation.Arm] = []
        described: list[dict[str, Any]] = []
        if base_ref:
            arms.append(evaluation.Arm(base_key, model_ref=base_ref, subject_kind="release" if base_release else "baseline",
                                       subject_id=base_release, serve_context=self._serves_at(base_ref)))
            described.append({"key": base_key, "label": base_label, "role": "baseline", "model_ref": base_ref})
        arms.append(evaluation.Arm(new_key, model_ref=state.model_ref, subject_kind="release",
                                   subject_id=state.release_id or None, serve_context=self._serves_at(state.model_ref)))
        described.append({"key": new_key, "label": f"{new_key} (new)", "role": "new", "model_ref": state.model_ref})
        main_unavailable: str | None = None
        if main_ref:
            # The main model is asked as it was served: with the page's picture when it was sent one (A8).
            arms.append(evaluation.Arm("main", model_ref=main_ref, cache=True, serve_context=self._serves_at(main_ref),
                                       sees_images=True))
            described.append({"key": "main", "label": self._name(main_ref), "role": "main", "model_ref": main_ref})
        else:
            main_unavailable = "no_main_model"
        worker_arms = {arm.name: eval_worker.WorkerArm(arm.name, str(arm.model_ref), self._local(str(arm.model_ref)))
                       for arm in arms}
        gather = self._gather or eval_worker.gather
        credentials, providers, unsupported = gather(list(worker_arms.values()))
        if "main" in unsupported:
            main_unavailable = "sign_in_unsupported"
            arms = [arm for arm in arms if arm.name != "main"]
            described = [item for item in described if item["key"] != "main"]
        tools = eval_worker.safe_tools((self._tools or eval_worker.person_tools)())
        spec = {"arms": [{"name": arm.name, "model_ref": arm.model_ref} for arm in arms], "tools": tools,
                "providers": providers, "code_backend": "container"}
        factory = self._worker_factory or (lambda spec, credentials, root: eval_worker.EvalWorker(
            spec, credentials=credentials, person_data=root))
        visible = [task for task in visible_tasks() if container or task.pack != "small_coding"]
        per_pack: dict[str, int] = {}
        chosen = []
        for task in visible:
            per_pack[task.pack] = per_pack.get(task.pack, 0) + 1
            if per_pack[task.pack] <= self.plan.visible_per_pack:
                chosen.append(task)
        verifiers = None if container else [kind for kind in ("answer", "json_answer", "file", "app_state")]
        packs = [pack for pack in PACKS if container or pack != "small_coding"]
        hidden_total = evaluation.audit_size(asked_by=ASKED_BY, store=self.store, packs=packs, verifiers=verifiers,
                                             include_replay=self.plan.replay) if self.plan.hidden else 0
        order = self._order(self._local_sizes(version, state, base_key), state.runtime) if base_ref else "interleaved"
        total = (len(chosen) * max(1, self.plan.visible_repeats)
                 + hidden_total * max(1, self.plan.hidden_repeats)) * len(arms)
        self._progress(version, phase="visible", done=0, total=total)
        meter = (self._meter or evaluation.TokenMeter.for_month)()
        budget = evaluation.Budget(task_tokens=self.plan.task_tokens)
        cache = evaluation.ArmCache(self._root, clock=self.clock)
        done = [0]
        busy = self._busy

        def counted(_arm: str, _case: Any) -> None:
            done[0] += 1
            self._progress(version, done=done[0])
            if stop.is_set() or (busy is not None and busy()):
                raise _Interrupted("in_use" if not stop.is_set() else "stopped")

        worker = factory(spec, credentials, self.root)
        notes: list[str] = []
        write_backs: list[str] = []
        try:
            worker.start()
            notes = list(getattr(worker, "notes", []) or [])
            runner = eval_worker.RuntimeRunner(worker, worker_arms,
                                               record_usage=self._record_usage or eval_worker.ledger_recorder(),
                                               unsupported=set(unsupported) & {"main"})
            shown = evaluation.compare(arms, tasks=chosen, runner=runner, cache=cache, meter=meter, store=self.store,
                                       client=self._client, clock=self.clock, on_case=counted, budget=budget,
                                       repeats=self.plan.visible_repeats, kind_key=family_key, order=order)
            hidden = None
            if self.plan.hidden and not shown.stopped:
                self._progress(version, phase="hidden")

                def hidden_progress(count: int, _total: int) -> None:
                    counted("", None)

                hidden = evaluation.audit_check(
                    arms, asked_by=ASKED_BY, store=self.store, runner=runner, cache=cache, meter=meter,
                    client=self._client, clock=self.clock, packs=packs, verifiers=verifiers,
                    include_replay=self.plan.replay, budget=budget, on_progress=hidden_progress,
                    repeats=self.plan.hidden_repeats, combine_with=shown, kind_key=family_key, order=order)
            write_backs = list(getattr(worker, "write_backs", []) or [])
        except _Interrupted as interrupted:
            self._progress(version, state="paused" if interrupted.reason == "in_use" else "stopped",
                           reason=interrupted.reason)
            raise CompareError(interrupted.reason) from None
        except Exception as error:
            if isinstance(error, CompareError):
                raise
            if stop.is_set():
                # Stop ends the worker mid-task, so its run has no result: that is a stop, never a failure (C1).
                self._progress(version, state="stopped", reason="stopped")
                raise CompareError("stopped") from None
            self._progress(version, state="failed", reason=getattr(error, "code", type(error).__name__)[:64])
            raise
        finally:
            try:
                worker.stop()
            except Exception:  # noqa: BLE001
                logger.debug("Learning couldn't stop its evaluation worker", exc_info=True)
        result = self._result(version, state, arms, described, shown, hidden, started=started,
                              base_reason=base_reason, base_label=base_label, main_unavailable=main_unavailable,
                              container=container, notes=notes, write_backs=write_backs, cache=cache)
        _write_json(comparison_path(version, self._root), result)
        self._progress(version, state="done", done=done[0])
        self._record_tests(state, result)
        return result

    def _serves_at(self, ref: str | None) -> int | None:
        """The context a local model is served at (its saved one), so replayed decisions reach it in
        its serving shape (O71); None for a cloud model, asked as captured."""

        if not ref or not self._local(str(ref)):
            return None
        if self._serving_context is not None:
            return self._serving_context(str(ref))
        try:
            from row_bot import models

            return int(models.get_context_size(str(ref), allow_probe=False)) or None
        except Exception:  # noqa: BLE001 - unknown: asked as captured
            logger.debug("Learning couldn't read a local model's context", exc_info=True)
            return None

    @staticmethod
    def _local(ref: str) -> bool:
        from row_bot.learning.spending import is_local_provider
        from row_bot.providers.selection import parse_model_ref

        parsed = parse_model_ref(ref) if ref else None
        return bool(parsed) and is_local_provider(parsed[0])

    def _result(self, version: int, state: Any, arms: Sequence[Any], described: list[dict[str, Any]], shown: Any,
                hidden: Any, *, started: float, base_reason: str | None, base_label: str,
                main_unavailable: str | None, container: bool, notes: list[str], write_backs: list[str],
                cache: Any) -> dict[str, Any]:
        from row_bot.learning import evaluation

        by_arm: dict[str, dict[str, list[int]]] = {}
        details: dict[str, Any] = {"visible": {}, "hidden": {}}
        totals: dict[str, dict[str, float]] = {}
        for comparison, label in ((shown, "visible"), (hidden, "hidden")):
            if comparison is None:
                continue
            for item in comparison.arms:
                name = item.arm.name
                details[label][name] = {kind: list(value) for kind, value in item.by_kind.items()}
                tally = totals.setdefault(name, {"ran": 0, "seconds": 0.0, "cloud": 0, "cached": 0, "tokens": 0,
                                                 "causes": {}})
                tally["ran"] += item.ran
                tally["seconds"] += item.seconds
                tally["cloud"] += item.measured_cloud_tokens
                tally["cached"] += item.cached
                tally["tokens"] += item.tokens
                for cause, count in (getattr(item, "causes", None) or {}).items():
                    tally["causes"][cause] = tally["causes"].get(cause, 0) + int(count)
        new_key = f"v{int(version)}"
        # A model whose every task ended in an error (its runtime, its sign-in, not ready for Agent
        # tasks) is left out and the result says so: never a column of dashes beside real counts.
        for item in shown.arms:
            packs = [case for case in item.cases if case.suite == "pack"]
            if item.arm.name == new_key or not packs or any(case.status != "error" for case in packs):
                continue
            logger.info("Learning's comparison left out %s: every task failed to run (%s)", item.arm.name,
                        str(packs[0].detail or "")[:200])
            described[:] = [entry for entry in described if entry["key"] != item.arm.name]
            for table in details.values():
                table.pop(item.arm.name, None)
            if item.arm.name == "main":
                main_unavailable = main_unavailable or "main_failed"
            else:
                base_reason = base_reason or "failed"
        for name in {entry["key"] for entry in described}:
            by_arm[name] = _combine(details["visible"].get(name, {}), details["hidden"].get(name, {}))
        base_key = next((item["key"] for item in described if item["role"] == "baseline"), None)
        for item in described:
            tally = totals.get(item["key"], {})
            ran = int(tally.get("ran") or 0)
            item.update({
                "ran": ran, "cached": int(tally.get("cached") or 0),
                "seconds_per_task": round(float(tally.get("seconds") or 0) / ran, 1) if ran else None,
                "cloud_tokens_per_task": round(float(tally.get("cloud") or 0) / ran) if ran else None,
                "model_name": item["label"] if item["role"] == "main" else None,
                # Why its tasks didn't pass (codes and counts, visible and hidden together; L2v).
                "causes": dict(sorted((tally.get("causes") or {}).items())),
            })
        rows, overall = summarize(described, by_arm, new=new_key, baseline=base_key,
                                  baseline_label=next((item["label"] for item in described if item["role"] == "baseline"),
                                                      base_label).replace(" (in use)", ""))
        analysis = _analysis_out(shown, hidden)
        # What the interval would have said beside the product's own verdict (unchanged, E4): the new
        # version against its baseline on every problem of the kind, visible and hidden together.
        both = analysis.get("combined") or analysis.get("visible") or {}
        mine = both.get(new_key) or {}
        for row in rows:
            row["interval_verdict"] = (mine.get("by_kind", {}).get(row["kind"]) or {}).get("interval_verdict")
        overall["interval_verdict"] = (mine.get("overall") or {}).get("interval_verdict")
        notes_out: list[str] = []
        if not container:
            notes_out.append(CODING_NOTE)
        stopped = shown.stopped or (hidden.stopped if hidden is not None else "")
        tokens = shown.tokens + (hidden.tokens if hidden is not None else 0)
        local = shown.local_tokens + (hidden.local_tokens if hidden is not None else 0)
        used = f"Testing used {evaluation._short_tokens(tokens)} tokens"  # noqa: SLF001 - the shared wording
        if local:
            used += f" (and {evaluation._short_tokens(local)} on this computer)"  # noqa: SLF001
        if any(item.get("cached") for item in described if item["role"] == "main"):
            used += f"; {next(item['label'] for item in described if item['role'] == 'main')}’s results were reused"
        small = shown.small_sample or (hidden.small_sample if hidden is not None else False)
        note = used + (" · " + evaluation.VARIANCE_NOTE.rstrip(".") if small else "") + "."
        if stopped:
            note = f"{note} {stopped}"
        runs = {"visible": {"comparison_set": shown.comparison_set, "test_runs": [item.run.id for item in shown.arms]}}
        if hidden is not None:
            runs["hidden"] = {"comparison_set": hidden.comparison_set,
                              "test_runs": [item.run.id for item in hidden.arms]}
        return {
            "format": FORMAT, "schema_version": 1, "version": int(version), "release_id": state.release_id or None,
            "compared_at": round(float(self.clock()), 3), "seconds": round(float(self.clock()) - started, 1),
            "arms": described, "rows": rows, "overall": overall, "note": note, "stopped": stopped,
            "left_out": notes_out, "baseline_unavailable": base_reason, "main_unavailable": main_unavailable,
            "details": details, "runs": runs, "budget": {"task_tokens": self.plan.task_tokens},
            "visible_per_pack": self.plan.visible_per_pack, "hidden": bool(hidden is not None),
            "worker_notes": notes[:8], "write_backs": write_backs[:8], "metric_version": evaluation.METRIC_VERSION,
            "reviewed_at": None,
            "repeats": {"visible": shown.repeats, "hidden": hidden.repeats if hidden is not None else 0,
                        "identical": _identical(shown, hidden)},
            "analysis": analysis,
        }

    def _record_tests(self, state: Any, result: Mapping[str, Any]) -> None:
        """The version's identity names its test runs (O6), and its card says what the comparison
        found (no task, no hidden detail)."""

        from dataclasses import replace

        from row_bot.learning import contracts
        from row_bot.learning.training import releases

        ids = tuple(run for part in (result.get("runs") or {}).values() for run in part.get("test_runs") or ())
        release = self.store.get("release", state.release_id) if state.release_id and self.store.exists() else None
        if isinstance(release, contracts.Release) and ids:
            updated = replace(release, identity=replace(release.identity,
                                                        tests=tuple(dict.fromkeys((*release.identity.tests, *ids)))[:4096]))
            self.store.put(updated)
            folder = releases.version_dir(state.version, self._root)
            try:
                (folder / releases.MANIFEST_FILE).write_text(contracts.to_manifest(updated), encoding="utf-8")
                card = folder / releases.MODEL_CARD_FILE
                if card.is_file():
                    text = card.read_text(encoding="utf-8")
                    line = "- Not yet compared with your main model. Row-Bot compares them before asking you to use it."
                    summary = "\n".join(
                        f"- {row['label']} ({row['total']}): " + ", ".join(
                            f"{item['label']} {row['passed'].get(item['key'], '–')}" for item in result["arms"]
                            if item["key"] in row["passed"]) for row in result["rows"])
                    text = text.replace(line, f"- Compared on {_day(result['compared_at'])}: {result['overall']['line']}\n"
                                              + summary + f"\n- {result['note']}")
                    card.write_text(text, encoding="utf-8", newline="\n")
            except OSError:
                logger.debug("Learning couldn't update the version's files", exc_info=True)


def _trained_from(version: int | None, root: Path | None) -> Any:
    """The pinned starting model ``version`` was trained from (its release's identity), else None."""

    from row_bot.learning.training import releases, weights

    if version is None:
        return None
    try:
        manifest = json.loads((releases.version_dir(int(version), root) / releases.MANIFEST_FILE)
                              .read_text(encoding="utf-8"))
        repo = str(((manifest.get("identity") or {}).get("base") or {}).get("name") or "")
    except (OSError, ValueError, AttributeError):
        return None
    return next((model for model in weights.MODELS.values() if model.repo == repo), None)


def family_key(label: str) -> str:
    """A kind of work's label as the rows key it: a pack family, else ``own_work``."""

    families = {name: kind for kind, name in kinds.LABELS.items() if kind != kinds.OTHER}
    return families.get(label, OWN_WORK)


def _analysis_out(shown: Any, hidden: Any) -> dict[str, Any]:
    """The paired analyses ``evaluation`` made (aggregates only), per arm against the baseline:
    ``visible``, ``hidden`` and ``combined`` (both), each overall and per kind of work."""

    found: dict[str, Any] = {}
    for label, comparison, key in (("visible", shown, "arms"), ("hidden", hidden, "arms"),
                                   ("combined", hidden, "combined")):
        part = (getattr(comparison, "analysis", None) or {}).get(key) if comparison is not None else None
        if not part:
            continue
        found[label] = {arm: {"overall": value["overall"], "by_kind": dict(value["by_kind"])}
                        for arm, value in part.items()}
        found.setdefault("seed", comparison.analysis.get("seed"))
        found.setdefault("resamples", comparison.analysis.get("resamples"))
        found.setdefault("permutations", comparison.analysis.get("permutations"))
    return found


def _identical(shown: Any, hidden: Any) -> dict[str, dict[str, int]]:
    """Per arm, how many repeated problems ran the same every time (greedy decoding shows here)."""

    found: dict[str, dict[str, int]] = {}
    for comparison in (shown, hidden):
        for arm, record in (getattr(comparison, "repeated", None) or {}).items():
            tally = found.setdefault(arm, {"problems": 0, "identical": 0})
            tally["problems"] += int(record.get("problems") or 0)
            tally["identical"] += int(record.get("identical") or 0)
    return found


def _day(at: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(float(at), tz=timezone.utc).astimezone().strftime("%d %b %Y")


class _Interrupted(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


# ── When it runs ────────────────────────────────────────────────────────────


class Comparisons:
    """Starts a ready version's comparison when the computer is free, one at a time, on its own thread.

    The quiet-hours runner calls :meth:`tick` once a minute. A comparison runs when Learning is on and
    not paused, the newest version is ready with no comparison yet, nothing is training, and nobody
    used Row-Bot for a few minutes (the lease, as training's quiet start); **Compare now** starts it at
    once. Someone using Row-Bot meanwhile stops it at the next task (it starts again later: the main
    model's results are kept). Learning turned off, paused or Row-Bot quitting stops it and its worker.
    """

    def __init__(self, root: Path | None = None, *, comparer: Comparer | None = None,
                 lease: Any = None, training: Callable[[], bool] | None = None,
                 clock: Callable[[], float] = time.time, start_threads: bool = True) -> None:
        self._root = root
        self.comparer = comparer or Comparer(root)
        self._lease = lease
        self._training = training
        self.clock = clock
        self._start_threads = start_threads
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.version: int | None = None
        self.last_error: str | None = None

    def _lease_state(self) -> Any:
        if self._lease is not None:
            return self._lease.read()
        from row_bot.learning.lease import ResourceLease

        return ResourceLease().read()

    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def due(self) -> int | None:
        """The version a comparison is due for (the newest ready one without a result), else None."""

        from row_bot.learning.training import releases

        state = releases.latest_state(self._root)
        if state is None or state.phase != releases.READY or not state.model_ref:
            return None
        if read_comparison(state.version, self._root) is not None:
            return None
        progress = read_progress(state.version, self._root) or {}
        if progress.get("state") == "failed" and self.clock() - float(progress.get("updated_at") or 0) < RETRY_SECONDS:
            return None
        return state.version

    def tick(self) -> bool:
        if not gate.is_enabled(self._root) or self.running():
            return False
        version = self.due()
        if version is None:
            return False
        if self._training is not None and self._training():
            return False
        lease = self._lease_state()
        if not getattr(lease, "free", True):
            return False
        return self.start(version)

    def start(self, version: int) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self.version = int(version)
            if not self._start_threads:
                self._thread = None
                self._run(int(version))
                return True
            self._thread = threading.Thread(target=self._run, args=(int(version),), name="learning-compare",
                                            daemon=True)
            self._thread.start()
            return True

    def _busy(self) -> bool:
        try:
            return bool(getattr(self._lease_state(), "busy", False))
        except Exception:  # noqa: BLE001
            return False

    def _run(self, version: int) -> None:
        self.comparer._busy = self._busy  # noqa: SLF001 - the service owns its comparer
        try:
            self.comparer.run(version, stop=self._stop)
            self.last_error = None
        except CompareError as error:
            self.last_error = error.code
            logger.info("Learning's comparison of personal model v%s stopped (%s)", version, error.code)
        except Exception as error:  # noqa: BLE001
            self.last_error = type(error).__name__
            logger.exception("Learning couldn't compare personal model v%s", version)

    def stop(self, timeout: float = 15.0) -> None:
        self._stop.set()
        from row_bot.learning import eval_worker

        eval_worker.stop_all()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def wait(self, timeout: float = 30.0) -> bool:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True


__all__ = [
    "ASKED_BY",
    "CODING_NOTE",
    "COMPARISON_FILE",
    "CompareError",
    "Comparer",
    "Comparisons",
    "OWN_WORK",
    "Plan",
    "comparison_path",
    "mark_reviewed",
    "read_comparison",
    "read_progress",
    "summarize",
    "verdict",
]
