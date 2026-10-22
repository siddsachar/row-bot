"""Opt-in: the whole L2 loop on this computer's graphics card (L2c2).

Captured work → dataset → training → export → served in Ollama → **compared** (the new version, the
untouched starting model and a *local* main model, so it costs no cloud tokens, through the
real-runtime evaluation worker) → a **trial** → a routed turn **answered by the personal model** →
a **forced fallback** finished by the main model → **Undo**. Plus two of the L2 gate's survival
checks the earlier work packages didn't run end to end: a **negative result** is said plainly and
never used by itself (D24), and a **source deletion** asks to "Retrain without it" (O6).

Skipped without ``ROW_BOT_LEARNING_USE_E2E=1`` or an NVIDIA card. The data folder must be a scratch
one; the environment is L2c's (``test_learning_personal_model_e2e.py``) plus::

    $env:ROW_BOT_LEARNING_USE_E2E = "1"
    $env:ROW_BOT_LEARNING_E2E_MAIN = "qwen3:8b"            # a local model already in Ollama (left as it is)
    $env:ROW_BOT_LEARNING_E2E_PER_PACK = "2"               # visible tasks per pack in the comparison
    $env:ROW_BOT_LEARNING_E2E_RESUME = "1"                 # optional: reuse a folder's ready, compared v1

The routed turns are document lookups on a pack's documents with Row-Bot's filesystem tool (bound
directly at a 32K context; tools behind the tool search are a separate finding, see the L2c2 handoff).
The main model is compared with thinking off (time); routed turns use its own default (thinking on).
The models it creates in Ollama are removed at the end; the main model is only unloaded.
Results: ``<data>/../l2c2_e2e_results.json`` (a resumed run adds ``resumed``).
"""

from __future__ import annotations

import json
import os
import random
import time
import uuid
from pathlib import Path

import pytest

from tests.helpers.learning_connections import Connections, GpuMemory

pytestmark = [pytest.mark.e2e]

ENABLED = os.environ.get("ROW_BOT_LEARNING_USE_E2E") == "1"
RESUME = os.environ.get("ROW_BOT_LEARNING_E2E_RESUME") == "1"
# Stream events left out of the recorded event list (one per token).
QUIET = frozenset({"token", "thinking_token", "context_usage"})
CONVERSATIONS = int(os.environ.get("ROW_BOT_LEARNING_E2E_CONVERSATIONS", "45"))
TURNS = 4


@pytest.mark.skipif(not ENABLED, reason="opt-in: set ROW_BOT_LEARNING_USE_E2E=1")
def test_the_l2_loop_from_captured_work_to_trial_fallback_and_undo(monkeypatch):
    data = os.environ.get("ROW_BOT_LEARNING_E2E_DATA", "")
    if not data:
        pytest.skip("set ROW_BOT_LEARNING_E2E_DATA to a scratch data folder")
    monkeypatch.setenv("ROW_BOT_DATA_DIR", data)
    from row_bot import learning

    learning.invalidate()
    from row_bot.learning import capture, kinds, model_compare, routing, use, versions
    from row_bot.learning.lease import ResourceLease
    from row_bot.learning.store import LearningStore
    from row_bot.learning.training import base_model, engine, hardware, lockfile, planner as planner_module
    from row_bot.learning.training import recipes, releases, serving, weights
    from row_bot.learning.training.supervisor import TrainingSupervisor
    from tests.e2e.learning.scripted_model import ALIAS, ScriptedModel
    from tests.e2e.learning.test_learning_personal_model_e2e import _agent_turn, _copy_weights, _expression
    from tests.fixtures.learning import save_learning_settings

    found = hardware.detect()
    if found.state != "gpu" or found.gpu is None:
        pytest.skip("needs an NVIDIA graphics card that can train")
    assert learning.preview_enabled(), "start the run with ROW_BOT_LEARNING_PREVIEW=1"
    root = learning.data_dir()
    assert ".row-bot" not in str(root).lower(), "use a scratch data folder, never a profile"
    out = root.parent / "l2c2_e2e_results.json"
    main_model = os.environ.get("ROW_BOT_LEARNING_E2E_MAIN", "qwen3:8b")
    main_ref = f"model:ollama:{main_model}"
    results: dict = {"gpu": found.gpu.label, "driver": found.gpu.driver, "main_model": main_ref, "steps": {}}
    from row_bot.learning.training import releases as _releases

    kept = _releases.read_state(_releases.version_dir(1, root)) if RESUME else None
    resume = kept is not None and kept.phase == _releases.READY and model_compare.read_comparison(1, root) is not None
    if resume and out.is_file():
        results = json.loads(out.read_text(encoding="utf-8"))
        results["resumed"] = {"at": time.time(), "from": "a ready, compared v1 in this folder"}

    def save() -> None:
        out.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")

    ollama = serving.OllamaRuntime()
    assert ollama.version(), "Ollama must be running on this computer"
    names_before = {item["name"] for item in (ollama.loaded() or [])}
    results["ollama_loaded_before"] = sorted(names_before)
    save_learning_settings(root, enabled=True, train_in_quiet_hours=True, train_where="this_computer",
                           model_download="waiting")
    LearningStore(root).create()
    recipe = recipes.load("8gb")
    hardware.check(refresh=True, detector=lambda: found)

    from row_bot import models, threads

    if not resume:
        # ── The engine and the starting model (as L2c's run) ────────────────────
        started = time.monotonic()
        status = engine.status(root, refresh=True)
        if not status.installed:
            plan = lockfile.engine_plan(hardware.engine_variant(found.gpu))
            source = os.environ.get("ROW_BOT_LEARNING_E2E_LLAMA_SOURCE")
            if source:
                import shutil

                downloads = engine.downloads_dir(root)
                downloads.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, downloads / lockfile.LLAMA_CPP_SOURCE.filename)
            uv, cache, python = (os.environ.get(name) for name in (
                "ROW_BOT_LEARNING_E2E_UV", "ROW_BOT_LEARNING_E2E_UV_CACHE", "ROW_BOT_LEARNING_E2E_PYTHON"))
            status = engine.EngineInstaller(plan, root=root, uv=[uv] if uv else None,
                                            uv_cache=Path(cache) if cache else None,
                                            python_source=Path(python) if python else None).install()
        results["steps"]["engine"] = {"seconds": round(time.monotonic() - started, 1), "bytes": status.bytes}
        assert status.installed and status.llama is not None
        _copy_weights(Path(os.environ["ROW_BOT_LEARNING_E2E_WEIGHTS"]), root)
        save()

        # ── Captured work: real Agent turns against the scripted main model ─────
        from row_bot import models, threads
        from row_bot.providers import custom
        from row_bot.providers.selection import model_ref as make_ref
        from row_bot.tools import registry

        trained_on: list[str] = []
        with ScriptedModel() as scripted:
            custom.save_custom_endpoint({"id": "e2e-main", "name": "Scripted main model", "base_url": scripted.base_url,
                                         "profile": "llama_cpp", "execution_location": "local", "auth_required": False})
            custom.refresh_custom_endpoint_models("e2e-main")
            assert custom.probe_custom_endpoint("e2e-main", ALIAS).get("classification") == "agent_ready"
            capture_ref = make_ref(custom.custom_provider_id("e2e-main"), ALIAS)
            models.adopt_saved_default(capture_ref)
            tools = [tool.name for tool in registry.get_enabled_tools()]
            rng = random.Random(20261017)
            started = time.monotonic()
            for conversation in range(CONVERSATIONS):
                thread_id = f"e2e-chat-{conversation:03d}-{uuid.uuid4().hex[:6]}"
                trained_on.append(thread_id)
                threads.create_thread(f"Sums {conversation}", thread_id=thread_id, seed_default_skills=False)
                for _ in range(TURNS):
                    prompt, expression = _expression(rng)
                    scripted.ask(expression)
                    _agent_turn(thread_id, prompt, capture_ref, tools)
            assert capture.flush(60)
            results["steps"]["captured_work"] = {"seconds": round(time.monotonic() - started, 1),
                                                 "turns": CONVERSATIONS * TURNS, "model_requests": scripted.requests}
            save()

        # ── Dataset, training, export, Ollama ───────────────────────────────────
        supervisor = TrainingSupervisor(root=root, engine=engine.LocalEngine(root), lease=ResourceLease())
        planner = planner_module.Planner(root, jobs=supervisor.jobs, engine=supervisor.engine, recipe_for=lambda: recipe)
        supervisor.dataset = planner_module.LearningDataset(planner)
        outcome = planner.plan(force=True)
        assert outcome.job is not None, outcome
        job = outcome.job
        results["steps"]["dataset"] = {"steps": job.steps_total,
                                       "held_out_in_vault": len(LearningStore(root).held_out_ids())}
        save()
        started = time.monotonic()
        with Connections() as watched, GpuMemory() as gpu:
            supervisor.train_now(job.id)
            finished = supervisor.wait_for(lambda item: item is not None and item.state in (
                "evaluating", "failed", "paused"), timeout=4 * 3600, job_id=job.id)
            supervisor.wait_idle(60)
        results["steps"]["training"] = {"seconds": round(time.monotonic() - started, 1), "state": finished.state,
                                        "steps": finished.steps_done, "peak_gpu_mib": gpu.peak,
                                        "remote_hosts": sorted(watched.remote)}
        save()
        assert finished.state == "evaluating" and watched.remote == set()
    personal_tag = (serving.ollama_tag(job.version, root) if not resume
                    else kept.model_ref.split(":", 2)[-1])
    try:
        if not resume:
            maker = releases.ReleaseMaker(root, jobs=supervisor.jobs, recipe_for=lambda: recipe, start_threads=False)
            started = time.monotonic()
            maker.start(supervisor.jobs.get(job.id))
            state = releases.read_state(releases.version_dir(job.version, root))
            results["steps"]["ready"] = {"seconds": round(time.monotonic() - started, 1), "phase": state.phase,
                                         "model_ref": state.model_ref, "gguf_bytes": state.gguf_bytes}
            save()
            assert state.phase == releases.READY, state

            # ── The comparison: new version, untouched starting model, local main model ──
            # The main model as a person uses it in Agent Mode (at least 32K tokens), thinking off.
            models.set_local_model_settings(main_model, context=32768, reasoning="off")
            serving.refresh_ollama_catalog()
            models.adopt_saved_default(main_ref)
            per_pack = int(os.environ.get("ROW_BOT_LEARNING_E2E_PER_PACK", "2"))
            comparer = model_compare.Comparer(root, plan=model_compare.Plan(visible_per_pack=per_pack),
                                              main_ref=lambda: main_ref)
            started = time.monotonic()
            with Connections() as watched, GpuMemory() as gpu:
                result = comparer.run(job.version)
            results["steps"]["comparison"] = {
                "seconds": round(time.monotonic() - started, 1), "peak_gpu_mib": gpu.peak,
                "remote_hosts": sorted(watched.remote), "arms": result["arms"], "rows": result["rows"],
                "overall": result["overall"], "note": result["note"], "left_out": result["left_out"],
                "baseline_unavailable": result["baseline_unavailable"], "details": result["details"],
                "worker_notes": result["worker_notes"],
            }
            save()
            assert result["rows"], result
            # Three different models, each run for real (the starting model never written over v1).
            assert len({arm["model_ref"] for arm in result["arms"]}) == len(result["arms"]) == 3, result["arms"]
            assert result["main_unavailable"] is None and result["baseline_unavailable"] is None, result
            assert watched.remote == set()  # local main model: nothing left this computer
            assert use.read(root).assignments == {}  # D24: compared, never used by itself
            results["steps"]["negative_result"] = {
                "verdict": result["overall"]["verdict"], "line": result["overall"]["line"],
                "used_by_itself": bool(use.read(root).assignments)}
            for name in {state.model_ref.split(":", 2)[-1], base_model.tag(root), main_model}:
                ollama.unload(name)
        else:
            # ── Resumed: v1 (and its starting model) back in Ollama from this folder's own files ──
            state = kept
            job = next(item for item in TrainingSupervisor(root=root, engine=engine.LocalEngine(root),
                                                           lease=ResourceLease()).jobs.all() if item.version == 1)
            try:
                ollama.show(personal_tag)
            except Exception:
                folder = releases.version_dir(1, root)
                served = serving.serve("ollama", version=1, gguf=folder / state.gguf, serve_context=recipe.serve_context,
                                       folder=folder, root=root)
                assert served.state == "ready", served
            result = model_compare.read_comparison(1, root)
            trained_on = sorted(row[0] for row in threads._list_threads()  # noqa: SLF001
                                if str(row[0]).startswith("e2e-chat-"))
            for name in (use.USE_FILE, "needs_you.json"):  # this folder's earlier trial and dismissals
                (root / learning.MODELS_FOLDER / name).unlink(missing_ok=True)
            ollama.unload(personal_tag)
        # ── Trial, a routed turn answered by the personal model, a forced fallback, Undo ──
        kind = kinds.DOCUMENT_LOOKUPS
        use.start_trial(job.version, kind, state.model_ref)  # the person said "Use v1 for …" (D24)
        from row_bot import agent
        from row_bot.application.client_platform import project_checkpoint_records
        from row_bot.learning.packs import visible_tasks
        from row_bot.learning.packs.verify import Outcome, verify
        from row_bot.runtime.checkpoint_reader import open_checkpoint
        from row_bot.tools import registry

        models.clear_local_model_settings(main_model)
        models.set_local_model_settings(main_model, context=32768)  # as the person runs it: thinking on
        models.adopt_saved_default(main_ref)
        packs = {task.id: task for task in visible_tasks()}
        workspace = root.parent / "l2c2_e2e_workspace"
        for task in (packs["doc_01"], packs["doc_02"]):
            for name, content in task.files.items():
                (workspace / name).parent.mkdir(parents=True, exist_ok=True)
                (workspace / name).write_text(content, encoding="utf-8")
        registry.set_tool_config("filesystem", "workspace_root", str(workspace))
        routed_tools = ["filesystem"]

        def routed_turn(question: str, factory=agent.stream_agent) -> dict:
            thread_id = f"e2e-routed-{uuid.uuid4().hex[:8]}"
            threads.create_thread("Routed", thread_id=thread_id, seed_default_skills=False)
            route = routing.route(conversation_id=thread_id, text=question, model_ref=main_ref, root=root,
                                  main_ref=main_ref)
            assert route is not None and route.kind == kind, (question, route)
            config = {"configurable": {"thread_id": thread_id, "model_override": main_ref, "runtime_mode": "agent",
                                       "runtime_surface": "normal_chat", "approval_mode": "approve",
                                       "generation_id": uuid.uuid4().hex}}
            routed = route.with_configurable({**config["configurable"], "model_override": route.personal_ref})
            started_turn = time.monotonic()
            try:
                events = list(routing.stream(routed, factory, question, routed_tools, config, root=root))
            finally:
                agent._set_active_runtime_context()  # noqa: SLF001
            with open_checkpoint(thread_id) as reader:
                rows = [row for _, row in project_checkpoint_records(reader, list(reader.records()))] if reader else []
            return {"seconds": round(time.monotonic() - started_turn, 1), "kind": route.kind,
                    "main_name": route.main_name, "events": [event[0] for event in events if event[0] not in QUIET][-16:],
                    "fell_back": any(event[0] == routing.FALLBACK_EVENT for event in events),
                    "tool_calls": sum(event[0] == "tool_call" for event in events),
                    "answer": next((str(event[1])[:300] for event in reversed(events) if event[0] == "done"), ""),
                    "answered_by": [row.get("answered_by") for row in rows if row.get("role") == "assistant"],
                    "notes": [[row.get("note"), "".join(str(block.get("text", "")) for block in row.get("blocks", []))]
                              for row in rows if row.get("note")]}

        asks = {kind: (packs["doc_01"].prompt, packs["doc_02"].prompt)}

        def correct(task_id: str, answer: str) -> bool:
            return verify(packs[task_id], Outcome(answer, {}, {}), backend="container").passed

        answered = routed_turn(asks[kind][0])
        answered["correct"] = correct("doc_01", answered["answer"])
        results["steps"]["routed_turn"] = answered
        save()
        assert answered["answer"] and "personal_model" in answered["answered_by"], answered
        tally = use.read(root).assignments[kind].trial
        assert (tally.requests, tally.answered, tally.fallbacks) == (1, 1, 0), tally

        # Forced: the personal model counts as stuck at its first tool call. Should it answer without
        # one, it is forced to fail instead (its run raises after its first event); either way the
        # main model finishes the turn for real.
        watch = routing.StuckWatch
        monkeypatch.setattr(routing, "StuckWatch", lambda: watch(max_calls=0))
        forced = routed_turn(asks[kind][1])
        forced["forced_by"] = "first tool call"
        if not forced["fell_back"]:

            routed_ref = state.model_ref

            def failing(text, enabled, config, stop_event=None):
                if config["configurable"].get("model_override") != routed_ref:
                    yield from agent.stream_agent(text, enabled, config, stop_event=stop_event)
                    return
                for event in agent.stream_agent(text, enabled, config, stop_event=stop_event):
                    yield event
                    raise RuntimeError("forced")


            results["steps"]["forced_fallback_first_try"] = forced
            forced = routed_turn(asks[kind][1], factory=failing)
            forced["forced_by"] = "a failure after its first step"
        forced["correct"] = correct("doc_02", forced["answer"])
        results["steps"]["forced_fallback"] = forced
        save()
        tally = use.read(root).assignments[kind].trial
        assert tally.fallbacks == 1, tally
        assert forced["fell_back"] and forced["answer"], forced
        assert any(note == "personal_model_fallback" and text.startswith("Your personal model got stuck;")
                   for note, text in forced["notes"]), forced

        back = use.undo(kind, still_ready=lambda version: versions.ready_ref(version, root))
        assert back is None and routing.route(conversation_id="after-undo", text=asks[kind][0], model_ref=main_ref,
                                              root=root, main_ref=main_ref) is None
        results["steps"]["undo"] = {"answers_now": "main model"}
        save()

        # ── Survival: a source deletion asks to retrain without it (O6) ──
        from row_bot.thread_cleanup import delete_thread

        deleted = delete_thread(trained_on[0])
        needs = versions.needs_you(root)
        results["steps"]["source_deletion"] = {"deleted": deleted.deleted,
                                               "needs_you": [item["title"] for item in needs]}
        save()
        assert any(item["type"] == "retrain" and item["version"] == job.version for item in needs)
    finally:
        # ── Tidy: what this run made in Ollama goes; the main model is only unloaded ──
        serving.withdraw("ollama", 1 if resume else job.version, root=root)
        base_model.withdraw(weights.QWEN35_2B.id, root=root)
        ollama.unload(main_model)
        __import__("shutil").rmtree(root.parent / "l2c2_e2e_workspace", ignore_errors=True)
        results["steps"]["tidy"] = {"removed": [personal_tag, base_model.tag(root)], "unloaded": main_model,
                                    "loaded_after": sorted(item["name"] for item in (ollama.loaded() or []))}
        save()
