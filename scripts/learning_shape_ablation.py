"""Row-Bot Learning: the L2w ablation (O71, the paper's L2w row), in one command.

Does training a personal model on rows **in the shape it serves in** help, against rows **as
captured**? And is thinking on or off better, served as it serves? On a Row-Bot data folder that
holds Learning's captured work (a scratch profile, or a scratch copy of a real profile that you
made: never the profile itself), with the training engine and the starting model installed in it,
it runs Row-Bot's own code end to end and writes one results file of counts, rates and intervals:

1. ``rows``: compiles the dataset twice with the certified 8 GB recipe (Qwen3.5-2B, 16K): **A** as
   captured and **B** re-rendered in the serving envelope (32K: core tools bound, the rest behind
   ``tool_search`` / ``tool_invoke``); every row measured by the engine's own tokenizer. Reports
   how many rows fit the training length before and after, and their lengths. When too few rows
   fit as captured, **A32** is the only way captured rows train on 8 GB (O23's fallback: QLoRA at
   32K) and stands in for A.
2. ``train``: trains each arm through Row-Bot's own worker, the same recipe and one pass each, the
   graphics card capped to the 8 GB tier's memory (``--cap-gb``); times, and whether the cap held.
   When A32 doesn't fit the cap, ``--a32-cap-gb`` trains it again with more memory (off the tier, so
   the shapes can still be compared at equal rows and steps); every attempt is kept.
3. ``serve``: exports each version (Q8_0) and serves it in Ollama as Row-Bot does (32K, the bridge,
   thinking off), plus a thinking-on copy; the untouched starting model the same way.
4. ``compare``: Row-Bot's evaluation worker runs every arm on the visible packs (``--visible-repeats``
   runs a task), the hidden pack split and the folder's held-out work (``--hidden-repeats`` runs a
   problem; aggregates only, E10), at the same budget; the paired analysis of each arm against the
   starting model comes from ``evaluation``. Each pack result is kept as it comes, so a stopped
   comparison continues; ``--packs-only --thinking off`` (then ``on``) runs it in parts, and the
   full run reuses them.
5. ``tidy``: removes every model it made in Ollama (``--keep`` keeps them).

Each phase saves its results to ``<out>/l2w_ablation.json`` and is skipped on a second run when
done, so a stopped run continues. No conversation, task or reply text is written, and no id made
from the data: counts, rates, intervals, times and model names only.

PowerShell, from a checkout of this branch after ``uv sync``::

    uv run python scripts/learning_shape_ablation.py --data <scratch data folder>

A scratch copy of a profile that hasn't downloaded the training engine can borrow it (and the
starting model) from another data folder that has: ``--runtimes-from <that folder>`` copies its
``runtimes/learning`` in first (about 8 GB).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESULTS = "l2w_ablation.json"
PHASES = ("rows", "train", "serve", "compare", "tidy")
MIN_ROWS = 20  # fewer rows than this as captured: A32 stands in for A
SERVE_CONTEXT = 32_768  # the 8 GB recipe's serving context


def _say(text: str) -> None:
    print(text, flush=True)


def _refuse_profile(data: Path) -> None:
    everyday = (Path.home() / ".row-bot").resolve()
    if data == everyday or everyday in data.parents:
        raise SystemExit("Point --data at a scratch copy of a profile, never the profile itself.")
    if not (data / "learning.db").is_file():
        raise SystemExit(f"{data} holds no Learning data (learning.db).")


class Results:
    """The results file: read at start, saved after every step."""

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


class GpuPeak:
    """The card's memory in use while a block runs (nvidia-smi every 2 s), minus what was in use before."""

    def __init__(self) -> None:
        self.before = self._used()
        self.peak = self.before
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @staticmethod
    def _used() -> int:
        try:
            output = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                    capture_output=True, text=True, timeout=10,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            return max(int(float(line)) for line in output.split() if line.strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            return 0

    def _run(self) -> None:
        while not self._stop.wait(2.0):
            self.peak = max(self.peak, self._used())

    def __enter__(self) -> GpuPeak:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._stop.set()
        self._thread.join(5)

    @property
    def added_mib(self) -> int:
        return max(0, self.peak - self.before)


def _lengths(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"rows": 0}
    ordered = sorted(values)
    return {"rows": len(ordered), "median": int(statistics.median(ordered)),
            "p90": ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))], "max": ordered[-1]}


# ── 1. Rows: as captured and in the serving shape ───────────────────────────


def _visible_in(compiled: Any) -> set[str]:
    """Visible pack tasks (public ids) whose prompt a training row of ``compiled`` holds."""

    from row_bot.learning.packs import visible_tasks

    asked = [str(message.get("content") or "") for item in (*compiled.train, *compiled.validation)
             for message in item.row.get("messages") or () if message.get("role") == "user"]
    return {task.id for task in visible_tasks() if any(task.prompt.strip()[:200] in text for text in asked)}


def phase_rows(ctx: dict[str, Any], results: Results) -> None:
    from row_bot.learning.store import LearningStore
    from row_bot.learning.training import dataset, planner, recipes

    data: Path = ctx["data"]
    store = LearningStore(data)
    recipe = recipes.load("8gb")
    found: dict[str, Any] = {}
    seen: set[str] = set()
    for arm, shape, max_seq in (("A", "captured", recipe.max_seq_length), ("B", "serving", recipe.max_seq_length),
                                ("A32", "captured", 32_768)):
        measured: list[int] = []

        def measure(rows: Any, limit: int, _seen: list[int] = measured) -> Any:
            lengths = planner.engine_measure(rows, limit, root=data)
            _seen.extend(int(tokens) for tokens, _targets in lengths if tokens >= 0)
            return lengths

        arm_recipe = recipe if max_seq == recipe.max_seq_length else replace(
            recipe, settings={**recipe.settings, "max_seq_length": max_seq, "bits": 4})
        started = time.monotonic()
        compiled = dataset.compile_dataset(store, arm_recipe, measure=measure, workspace_roots=planner.workspace_roots(),
                                           refresh_vault=True, shape=shape)
        kept = compiled.examples + len(compiled.validation)
        too_long = int(compiled.left_out.get("too_long", 0))
        candidates = kept + too_long + int(compiled.left_out.get("no_target", 0)) + int(
            compiled.left_out.get("unrenderable", 0))
        found[arm] = {
            "shape": shape, "max_seq_length": max_seq, "examples": compiled.examples,
            "validation": len(compiled.validation), "rows_measured": candidates, "fit": kept, "too_long": too_long,
            "fit_share": round(kept / candidates, 4) if candidates else None, "left_out": dict(compiled.left_out),
            "lengths_all": _lengths(measured), "lengths_kept": _lengths([int(item.tokens or 0) for item in
                                                                         (*compiled.train, *compiled.validation)]),
            "rendering": dict(compiled.rendering), "derived": sum(item.derived for item in compiled.train),
            "providers": list(compiled.providers), "seconds": round(time.monotonic() - started, 1),
        }
        _say(f"{arm} ({shape}, {max_seq:,}): {kept} of {candidates} rows fit"
             f" ({found[arm]['fit_share']}), {compiled.examples} train")
        seen.update(_visible_in(compiled))
    # Visible pack tasks whose prompt is in a training row (a scratch capture that worked through them):
    # the comparison reports them apart from the visible tasks no arm trained on.
    found["seen_visible"] = sorted(seen)
    results.data["rows"] = found
    results.save()


# ── 2. Training ─────────────────────────────────────────────────────────────


def _arms_to_train(results: Results) -> list[str]:
    rows = results.data.get("rows") or {}
    arms = ["B"]
    a_rows = int((rows.get("A") or {}).get("examples") or 0)
    arms.append("A" if a_rows >= MIN_ROWS else "A32")
    return arms


def phase_train(ctx: dict[str, Any], results: Results) -> None:
    from row_bot.learning.lease import ResourceLease
    from row_bot.learning.store import LearningStore
    from row_bot.learning.training import dataset, engine, hardware, planner, recipes
    from row_bot.learning.training.jobs import TrainingJobs
    from row_bot.learning.training.supervisor import TrainingSupervisor

    data: Path = ctx["data"]
    found = hardware.detect()
    if found.state != "gpu" or found.gpu is None:
        raise SystemExit("Training needs an NVIDIA graphics card.")
    hardware.check(refresh=True, detector=lambda: found)
    total = int(found.gpu.memory_mib)
    trained = results.data.setdefault("train", {})
    store = LearningStore(data)
    for arm in _arms_to_train(results):
        before = trained.get(arm) or {}
        if before.get("state") in ("evaluating", "no_rows"):
            continue
        cap_gb = ctx["a32_cap_gb"] if arm == "A32" and ctx.get("a32_cap_gb") else ctx["cap_gb"]
        if before:  # an attempt that failed (out of the cap's memory): kept, then tried again
            if before.get("cap_gb", ctx["cap_gb"]) == cap_gb:
                _say(f"{arm}: failed before at {cap_gb} GB ({before.get('error')}); not tried again")
                continue
            trained.setdefault(f"{arm}_attempts", []).append(before)
        share = round(min(0.95, cap_gb * 1024 * 0.95 / total), 4)
        recipe = recipes.load("8gb")
        shape = "serving" if arm == "B" else "captured"
        settings = {**recipe.settings, "vram_cap": share}
        if arm == "A32":
            settings.update({"max_seq_length": 32_768, "bits": 4})
        recipe = replace(recipe, settings=settings)
        compiled = dataset.compile_dataset(store, recipe, measure=lambda rows, limit: planner.engine_measure(
            rows, limit, root=data), workspace_roots=planner.workspace_roots(), refresh_vault=True, shape=shape)
        if compiled.examples <= 0:
            trained[arm] = {"state": "no_rows"}
            results.save()
            _say(f"{arm}: no rows to train on")
            continue
        supervisor = TrainingSupervisor(root=data, engine=engine.LocalEngine(data), lease=ResourceLease())
        jobs: TrainingJobs = supervisor.jobs
        steps = recipe.steps_for(compiled.examples)
        job = jobs.plan(steps_total=steps, checkpoint_every=recipe.checkpoint_interval(steps), recipe=recipe.spec(),
                        dataset={"id": compiled.id, "examples": compiled.examples}, trigger="train_now")
        planner.lease(job, compiled, data)
        _say(f"{arm}: training v{job.version} on {compiled.examples} rows, {steps} steps, card cap {share:.4f}")
        started = time.monotonic()
        with GpuPeak() as peak:
            supervisor.train_now(job.id)
            finished = supervisor.wait_for(lambda item: item is not None and item.state in (
                "evaluating", "failed", "paused", "stopped"), timeout=12 * 3600, job_id=job.id)
            supervisor.wait_idle(120)
        trained[arm] = {
            "version": job.version, "job": job.id, "state": finished.state if finished else "unknown",
            "examples": compiled.examples, "steps": finished.steps_done if finished else None,
            "seconds": round(time.monotonic() - started, 1),
            "seconds_per_step": finished.seconds_per_step if finished else None,
            "loss": finished.loss if finished else None, "error": finished.error_code if finished else None,
            "vram_cap": share, "cap_gb": cap_gb, "max_seq_length": recipe.max_seq_length, "bits": settings["bits"],
            "card_mib_added_at_peak": peak.added_mib, "gpu_hours": round((time.monotonic() - started) / 3600, 3),
        }
        results.save()
        _say(f"{arm}: {trained[arm]['state']} in {trained[arm]['seconds']} s")


# ── 3. Serving: each version, thinking off and on, and the starting model ──


def _serve_copy(source_tag: str, tag: str, gguf: Path, folder: Path, data: Path, *, thinking: bool) -> str | None:
    """Serve ``gguf`` again under ``tag`` (a thinking-on copy): the same Modelfile, 32K, the probe only."""

    from row_bot import models
    from row_bot.learning.training import serving

    folder.mkdir(parents=True, exist_ok=True)
    outcome = serving.serve("ollama", version=1, gguf=gguf, serve_context=32_768, folder=folder, root=data, tag=tag,
                            tool_turn=False)
    if outcome.state != "ready":
        _say(f"{tag}: couldn't be served ({outcome.reason})")
        return None
    if thinking:
        models.clear_local_model_settings(tag)
        models.set_local_model_settings(tag, context=32_768)  # thinking left to the model's own default
    return outcome.model_ref


def phase_serve(ctx: dict[str, Any], results: Results) -> None:
    from row_bot import models
    from row_bot.learning.training import base_model, engine, hardware, recipes, releases, serving, weights
    from row_bot.learning.training.jobs import TrainingJobs

    data: Path = ctx["data"]
    served = results.data.setdefault("serve", {})
    jobs = TrainingJobs(data)
    recipe = recipes.load("8gb")
    hardware.check(refresh=True)
    if "start" not in served:
        model = weights.QWEN35_2B
        found = weights.status(model, data)
        main = next((item for item in model.files if item.name.endswith(".safetensors")), None)
        ref, reason = base_model.ensure(root=data, engine_status=engine.status(data), model=model,
                                        weights_folder=found.folder, weights_bytes=main.size if main else 0,
                                        recipe=recipe)
        served["start"] = {"off": ref, "reason": reason}
        state = base_model.read(model.id, data)
        if ref and state is not None and getattr(state, "gguf", None):
            gguf = base_model.folder(model.id, data) / str(state.gguf)
            served["start"]["on"] = _serve_copy(base_model.tag(data), base_model.tag(data) + "-think", gguf,
                                                ctx["out"] / "serve" / "start-think", data, thinking=True)
        results.save()
    for arm, item in (results.data.get("train") or {}).items():
        if not isinstance(item, dict) or (item.get("state") != "evaluating" and not item.get("ready")):
            continue  # an attempt that failed, or an arm with nothing trained
        before = served.get(arm)
        if before and before.get("off"):
            continue
        if before:  # its export or serving failed before (a busy computer): kept, then tried again
            served.setdefault(f"{arm}_attempts", []).append(before)
        job = jobs.get(item["job"])
        maker = releases.ReleaseMaker(data, jobs=jobs, recipe_for=lambda: recipe, start_threads=False)
        started = time.monotonic()
        maker.start(job)
        version = int(item["version"])
        folder = releases.version_dir(version, data)
        state = releases.read_state(folder)
        entry: dict[str, Any] = {"version": version, "phase": state.phase if state else None,
                                 "ready_check": dict(state.checks) if state and state.checks else {},
                                 "seconds": round(time.monotonic() - started, 1),
                                 "gguf_bytes": state.gguf_bytes if state else None}
        if state is not None and state.gguf:
            gguf = folder / str(state.gguf)
            if state.phase == releases.READY and state.model_ref:
                entry["off"] = state.model_ref
            else:
                # It failed Row-Bot's ready check (its tool turn): served anyway, to be measured as it is.
                entry["off"] = _serve_copy("", serving.ollama_tag(version, data), gguf, folder, data, thinking=False)
                models.set_local_model_settings(serving.ollama_tag(version, data), context=32_768, reasoning="off")
            entry["on"] = _serve_copy("", serving.ollama_tag(version, data) + "-think", gguf,
                                      ctx["out"] / "serve" / f"{arm}-think", data, thinking=True)
        served[arm] = entry
        results.save()
        _say(f"{arm}: v{version} {entry['phase']}; off {entry.get('off')}, on {entry.get('on')}")


# ── 4. The comparison ───────────────────────────────────────────────────────


class _ServedClient:
    """Evaluation's client for replay, without its "lowest effort" (thinking off) for the thinking-on
    arms: each arm is asked as it serves."""

    def __init__(self, thinking: set[str]) -> None:
        from row_bot.learning.evaluation import LedgerModelClient

        self._inner = LedgerModelClient()
        self._thinking = thinking

    def is_local(self, model_ref: str | None) -> bool:
        return self._inner.is_local(model_ref)

    def invoke(self, messages: Any, *, tools: Any, model_ref: str | None, purpose: str) -> Any:
        if model_ref not in self._thinking:
            return self._inner.invoke(messages, tools=tools, model_ref=model_ref, purpose=purpose)
        from row_bot import models
        from row_bot.learning.evaluation import ModelReply, estimate_tokens
        from row_bot.providers.reasoning import canonical_reasoning_model_ref, request_plan_for
        from row_bot.providers.resolution import resolve_provider_config
        from row_bot.providers.usage_ledger import with_usage_purpose

        # The model's own reasoning plan, as an Agent turn would use it (not Learning's lowest effort).
        resolved = resolve_provider_config(str(model_ref), allow_legacy_local=True)
        plan = request_plan_for(None, canonical_reasoning_model_ref(resolved.provider_id, resolved.runtime_model),
                                use_snapshot=False)
        llm = models.get_llm_for(resolved.selection_ref, reasoning_plan=plan)
        selection_ref = resolved.selection_ref
        runnable = llm.bind_tools(list(tools)) if tools else llm
        message = with_usage_purpose(runnable, purpose).invoke(list(messages))
        usage = getattr(message, "usage_metadata", None) or {}
        sent = estimate_tokens(messages, tools)
        return ModelReply(message, int(usage.get("input_tokens") or sent), int(usage.get("output_tokens") or 0),
                          True, selection_ref)


def phase_compare(ctx: dict[str, Any], results: Results) -> None:
    from row_bot.learning import eval_worker, evaluation, model_compare
    from row_bot.learning.packs import PACKS, visible_tasks
    from row_bot.learning.sandbox import container_runtime
    from row_bot.learning.spending import TokenMeter
    from row_bot.learning.store import LearningStore

    data: Path = ctx["data"]
    served = results.data.get("serve") or {}
    thinking = ctx["thinking"]
    arms: list[evaluation.Arm] = []
    names: dict[str, str] = {}
    think_refs: set[str] = set()
    for arm in ("start", "B", "A", "A32"):
        entry = served.get(arm) or {}
        for mode in ("off", "on"):
            ref = entry.get(mode)
            if not ref or (thinking != "both" and mode != thinking):
                continue
            name = f"{arm}-{mode}"
            # Every arm serves at 32K behind the bridge, so its replayed decisions come in that shape (O71).
            # ``cache``: each pack result is kept as it comes (counts and codes only, E10), so a stopped
            # comparison continues where it was and the parts run with --packs-only are reused.
            arms.append(evaluation.Arm(name, model_ref=ref, subject_kind="baseline" if arm == "start" else "release",
                                       serve_context=SERVE_CONTEXT, cache=True))
            names[name] = ref
            if mode == "on":
                think_refs.add(ref)
    if len(arms) < 2:
        raise SystemExit("Nothing to compare yet: run the serve phase first.")
    # The contrasts beyond each arm against the untouched starting model: rows in the serving shape
    # against rows as captured (as served, thinking off), and thinking on against off for each model.
    present = {arm.name for arm in arms}
    trained_a = next((name for name in ("A-off", "A32-off") if name in present), None)
    pairs = [(trained_a, "B-off")] if trained_a and "B-off" in present else []
    pairs += [(f"{arm}-off", f"{arm}-on") for arm in ("start", "B", "A", "A32")
              if f"{arm}-off" in present and f"{arm}-on" in present]
    store = LearningStore(data)
    container = bool(container_runtime())
    worker_arms = {arm.name: eval_worker.WorkerArm(arm.name, str(arm.model_ref), True) for arm in arms}
    credentials, providers, unsupported = eval_worker.gather(list(worker_arms.values()))
    tools = eval_worker.safe_tools(eval_worker.person_tools())
    spec = {"arms": [{"name": arm.name, "model_ref": arm.model_ref} for arm in arms], "tools": tools,
            "providers": providers, "code_backend": "container"}
    visible = [task for task in visible_tasks() if container or task.pack != "small_coding"]
    chosen, per_pack = [], {}
    for task in visible:
        per_pack[task.pack] = per_pack.get(task.pack, 0) + 1
        if per_pack[task.pack] <= ctx["visible_per_pack"]:
            chosen.append(task)
    verifiers = None if container else ["answer", "json_answer", "file", "app_state"]
    packs = [pack for pack in PACKS if container or pack != "small_coding"]
    budget = evaluation.Budget(task_tokens=150_000)
    meter = TokenMeter(10**12)
    cache = evaluation.ArmCache(data)
    packs_only = bool(ctx.get("packs_only"))
    done = [0]
    started = time.monotonic()

    def counted(_arm: str = "", _case: Any = None) -> None:
        done[0] += 1
        cache.save()
        if done[0] % 25 == 0:
            _say(f"compare: {done[0]} runs in {round((time.monotonic() - started) / 60)} min"
                 f" ({cache.hits} kept from before)")

    worker = eval_worker.EvalWorker(spec, credentials=credentials, person_data=data)
    try:
        worker.start()
        runner = eval_worker.RuntimeRunner(worker, worker_arms, record_usage=None, unsupported=set(unsupported))
        shown = evaluation.compare(arms, tasks=chosen, runner=runner, meter=meter, store=store, save=False,
                                   on_case=counted, budget=budget, repeats=ctx["visible_repeats"],
                                   kind_key=model_compare.family_key, pairs=pairs, cache=cache)
        hidden = evaluation.audit_check(arms, asked_by="the L2w ablation", store=store, runner=runner, meter=meter,
                                        client=_ServedClient(think_refs), packs=packs, verifiers=verifiers,
                                        budget=budget, save=False, repeats=ctx["hidden_repeats"], combine_with=shown,
                                        kind_key=model_compare.family_key, pairs=pairs, cache=cache,
                                        include_replay=not packs_only, on_progress=lambda *_done: counted())
    finally:
        worker.stop()
    out: dict[str, Any] = {"arms": names, "container": container, "visible_tasks": len(chosen),
                           "seconds": round(time.monotonic() - started, 1), "repeats": {
                               "visible": shown.repeats, "hidden": hidden.repeats,
                               "identical": {name: {key: shown.repeated.get(name, {}).get(key, 0)
                                                    + hidden.repeated.get(name, {}).get(key, 0)
                                                    for key in ("problems", "identical")} for name in names}},
                           "analysis": {"visible": shown.analysis, "hidden": hidden.analysis}}
    per_arm: dict[str, Any] = {}
    for label, comparison in (("visible", shown), ("hidden", hidden)):
        for item in comparison.arms:
            row = per_arm.setdefault(item.arm.name, {})
            row[label] = {"by_kind": {kind: list(value) for kind, value in item.by_kind.items()},
                          "runs_passed": item.run.passed, "runs_counted": item.run.total, "ran": item.ran,
                          "seconds_per_run": round(item.seconds / item.ran, 1) if item.ran else None,
                          "tokens": item.measured_tokens, "causes": dict(item.causes), "kept": item.cached}
    out["per_arm"] = per_arm
    out["product_verdict"] = product_verdicts(per_arm, arms[0].name, out["analysis"]["hidden"])
    seen = set((results.data.get("rows") or {}).get("seen_visible") or ())
    out["visible_seen_in_training"] = len([task for task in chosen if task.id in seen])
    out["visible_unseen"] = _unseen(shown, seen, [(arms[0].name, arm.name) for arm in arms[1:]] + pairs)
    if packs_only:  # a part (no replayed decisions): kept apart, the comparison itself still to run
        results.data.setdefault("compare_packs", {})[thinking] = out
    else:
        results.data["compare"] = out
    results.save()
    _say(f"compare: {done[0]} runs in {out['seconds']} s")


def product_verdicts(per_arm: dict[str, Any], base: str, hidden_analysis: dict[str, Any]) -> dict[str, Any]:
    """What the product's own rule (E4: at least two tasks and a tenth of them) says for each arm
    against ``base`` per kind of work (visible and hidden together, as the product counts them),
    beside what the interval says (``evaluation``'s combined analysis)."""

    from row_bot.learning import model_compare

    def table(arm: str) -> dict[str, list[int]]:
        found: dict[str, list[int]] = {}
        for part in ("visible", "hidden"):
            for label, (passed, total) in ((per_arm.get(arm) or {}).get(part, {}).get("by_kind") or {}).items():
                row = found.setdefault(model_compare.family_key(label), [0, 0])
                row[0] += int(passed)
                row[1] += int(total)
        return found

    baseline = table(base)
    out: dict[str, Any] = {}
    for arm in per_arm:
        if arm == base:
            continue
        mine = table(arm)
        interval = (hidden_analysis.get("combined") or {}).get(arm) or {}
        out[arm] = {kind: {"passed": mine[kind][0], "baseline": baseline[kind][0], "of": mine[kind][1],
                           "verdict": model_compare.verdict(mine[kind][0], baseline[kind][0],
                                                            min(mine[kind][1], baseline[kind][1])),
                           "interval_verdict": ((interval.get("by_kind") or {}).get(kind) or {}).get(
                               "interval_verdict")}
                    for kind in sorted(set(mine) & set(baseline))}
        out[arm]["overall_interval_verdict"] = (interval.get("overall") or {}).get("interval_verdict")
    return out


def _unseen(shown: Any, seen: set[str], pairs: list[tuple[str, str]]) -> dict[str, Any]:
    """Visible tasks no arm trained on: passes per arm and each pair's paired analysis (a pack task
    is its own cluster)."""

    from row_bot.learning import evaluation

    cases = {item.arm.name: [case for case in item.cases if case.task_id not in seen] for item in shown.arms}
    fractions = {name: evaluation.pass_fractions(found) for name, found in cases.items()}
    out: dict[str, Any] = {"per_arm": {name: {"problems": len(found), "passed": round(sum(f for _, f in found.values()), 3)}
                                       for name, found in fractions.items()}, "pairs": {}}
    for base, other in pairs:
        both = [task for task in fractions.get(base, {}) if task in fractions.get(other, {})]
        differences = [(task, fractions[other][task][1] - fractions[base][task][1]) for task in both]
        out["pairs"][f"{other} vs {base}"] = evaluation.paired_analysis(differences)
    return out


# ── 5. Tidy ─────────────────────────────────────────────────────────────────


def phase_tidy(ctx: dict[str, Any], results: Results) -> None:
    from row_bot import models
    from row_bot.learning.training import serving

    ollama = serving.OllamaRuntime()
    removed = []
    for entry in (results.data.get("serve") or {}).values():
        if not isinstance(entry, dict):
            continue  # attempts that failed
        for mode in ("off", "on"):
            ref = str(entry.get(mode) or "")
            if not ref.startswith("model:ollama:"):
                continue
            tag = ref.split(":", 2)[2]
            ollama.unload(tag)
            ollama.delete(tag)
            models.clear_local_model_settings(tag)
            removed.append(tag)
    results.data["tidy"] = {"removed": len(removed)}
    results.save()
    _say(f"tidy: removed {len(removed)} model(s) from Ollama")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", required=True, help="a Row-Bot data folder with Learning data (a scratch copy)")
    parser.add_argument("--out", help="where the results go (default: <data>/../shape-ablation)")
    parser.add_argument("--phases", default=",".join(PHASES))
    parser.add_argument("--cap-gb", type=float, default=8.0, help="graphics memory training may use (the tier)")
    parser.add_argument("--a32-cap-gb", type=float, help="graphics memory for A32 when it doesn't fit --cap-gb")
    parser.add_argument("--visible-per-pack", type=int, default=16)
    parser.add_argument("--visible-repeats", type=int, default=3)
    parser.add_argument("--hidden-repeats", type=int, default=3)
    parser.add_argument("--thinking", choices=("both", "off", "on"), default="both")
    parser.add_argument("--packs-only", action="store_true",
                        help="compare: run only the pack tasks (kept for the full comparison), so it can run in parts")
    parser.add_argument("--runtimes-from", help="a data folder whose training engine and starting model to copy in")
    args = parser.parse_args(argv)
    data = Path(args.data).resolve()
    _refuse_profile(data)
    if args.runtimes_from:
        import shutil

        source = Path(args.runtimes_from).resolve() / "runtimes" / "learning"
        target = data / "runtimes" / "learning"
        if not source.is_dir():
            raise SystemExit(f"{source} holds no training engine.")
        if not target.exists():
            _say(f"Copying the training engine and starting model from {source} …")
            shutil.copytree(source, target, symlinks=True)
    out = Path(args.out).resolve() if args.out else data.parent / "shape-ablation"
    # Every Row-Bot module binds this folder; no desktop notification from a headless run (O68).
    os.environ["ROW_BOT_DATA_DIR"] = str(data)
    os.environ["ROW_BOT_LEARNING_PREVIEW"] = "1"
    os.environ["ROW_BOT_DESKTOP_NOTIFICATIONS"] = "off"
    os.environ.setdefault("ROW_BOT_AUTO_START_OLLAMA", "0")
    sys.path.insert(0, str(ROOT / "src"))
    from row_bot import learning

    learning.invalidate()
    if not learning.is_enabled(data):
        raise SystemExit("Learning is off in that folder.")
    results = Results(out / RESULTS)
    results.data.setdefault("started_at", time.strftime("%Y-%m-%d %H:%M"))
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout
    results.data["build"] = commit.strip()
    ctx = {"data": data, "out": out, "cap_gb": args.cap_gb, "a32_cap_gb": args.a32_cap_gb, "visible_per_pack": args.visible_per_pack,
           "visible_repeats": args.visible_repeats, "hidden_repeats": args.hidden_repeats, "thinking": args.thinking,
           "packs_only": args.packs_only}
    wanted = [phase.strip() for phase in args.phases.split(",") if phase.strip()]
    for phase in wanted:
        if phase not in PHASES:
            raise SystemExit(f"Unknown phase {phase}")
        if phase in results.data and phase not in ("train", "serve"):
            _say(f"{phase}: done before (delete it from {RESULTS} to run it again)")
            continue
        _say(f"── {phase}")
        globals()[f"phase_{phase}"](ctx, results)
    _say(f"Results: {results.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
