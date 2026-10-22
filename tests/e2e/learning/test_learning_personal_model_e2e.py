"""Opt-in: a personal model end to end on this computer's graphics card (L2c).

Real captured work, then the real loop: Row-Bot runs real Agent turns against a scripted "main
model" (a loopback llama-server look-alike, ``scripted_model.py``) with Learning on, so capture
records them as it records any turn; the dataset is compiled (the audit vault takes its share,
every row measured by the engine); the 8 GB recipe trains for real through Row-Bot's worker (one
short pass over the examples); the version is exported with llama.cpp's own tools, served through
Ollama and through llama.cpp's server, and each is ready only after Row-Bot's probe and one real
Agent turn that calls a tool. Times, sizes, peak graphics memory and connections are recorded.

Skipped without ``ROW_BOT_LEARNING_TRAINING_E2E=1`` or an NVIDIA card. The data folder must be a
scratch one (``ROW_BOT_LEARNING_E2E_DATA``, never a profile) and the build must have the preview flag::

    $env:ROW_BOT_LEARNING_E2E_DATA = "<scratch>\\data"; $env:ROW_BOT_LEARNING_PREVIEW = "1"
    $env:ROW_BOT_LEARNING_TRAINING_E2E = "1"
    $env:ROW_BOT_LEARNING_E2E_UV = "<uv.exe>"                       # builds the engine offline from
    $env:ROW_BOT_LEARNING_E2E_UV_CACHE = "<uv cache with the wheels>" # a cache that holds its wheels
    $env:ROW_BOT_LEARNING_E2E_PYTHON = "<standalone CPython 3.12.15>"
    $env:ROW_BOT_LEARNING_E2E_WEIGHTS = "<a folder with Qwen3.5-2B's pinned files>"
    $env:ROW_BOT_LEARNING_E2E_LLAMA_SOURCE = "<llama.cpp-b11669-src.zip>"   # optional: else downloaded
    $env:ROW_BOT_LEARNING_E2E_LLAMA_SERVER = "<llama-server with CUDA>"     # optional: the llama.cpp leg
    uv run python -m pytest tests/e2e/learning/test_learning_personal_model_e2e.py -m e2e -s

The Ollama model it creates is removed at the end. Results: ``<data>/../l2c_e2e_results.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from tests.helpers.learning_connections import Connections, GpuMemory

pytestmark = [pytest.mark.e2e]

ENABLED = os.environ.get("ROW_BOT_LEARNING_TRAINING_E2E") == "1"
CONVERSATIONS = int(os.environ.get("ROW_BOT_LEARNING_E2E_CONVERSATIONS", "45"))
TURNS = 4


def _expression(rng: random.Random) -> tuple[str, str]:
    """(what the person asks, the calculator expression) for one turn."""

    kind = rng.randrange(6)
    a, b, c = rng.randint(12, 980), rng.randint(3, 99), rng.randint(2, 40)
    month, day = rng.randint(1, 11), rng.randint(1, 28)
    if kind == 0:
        return rng.choice(["What is {e}?", "Calculate {e}.", "Can you work out {e} for me?"]).format(e=f"{a} * {b}"), \
            f"{a} * {b}"
    if kind == 1:
        expression = f"{a} + {b} * {c}"
        return f"What do you get for {expression}?", expression
    if kind == 2:
        expression = f"round({a} / {b}, 2)"
        return f"Divide {a} by {b} and round to two decimals.", expression
    if kind == 3:
        root = rng.randint(11, 99)
        return f"What is the square root of {root * root}?", f"sqrt({root * root})"
    if kind == 4:
        start = f"2026-{month:02d}-{day:02d}"
        return f"How many days are there from {start} to 2026-12-25?", f"days_between('{start}', '2026-12-25')"
    start = f"2026-{month:02d}-{day:02d}"
    return f"Which weekday is {start}?", f"weekday('{start}')"


def _agent_turn(thread_id: str, prompt: str, model_ref: str, tools: list[str]) -> dict:
    from row_bot import agent

    config = {"configurable": {"thread_id": thread_id, "model_override": model_ref, "runtime_mode": "agent",
                               "runtime_surface": "normal_chat", "approval_mode": "approve",
                               "generation_id": uuid.uuid4().hex}}
    answer, calls, errors = "", 0, []
    try:
        for event, payload in agent.stream_agent(prompt, tools, config):
            if event == "tool_call":
                calls += 1
            elif event == "done":
                answer = str(payload or "")
            elif event in {"error", "terminal", "interrupt"}:
                errors.append(event)
    finally:
        agent._set_active_runtime_context()  # noqa: SLF001
    return {"answer": answer, "calls": calls, "errors": errors}


def _copy_weights(source: Path, root: Path) -> Path:
    from row_bot.learning.training import weights

    model = weights.QWEN35_2B
    target = weights.model_dir(model, root)
    if weights.status(model, root).state == "downloaded":
        return target
    target.mkdir(parents=True, exist_ok=True)
    for item in model.files:
        shutil.copyfile(source / item.name, target / item.name)
        digest = hashlib.sha256((target / item.name).read_bytes()).hexdigest() if item.size < 64_000_000 else None
        assert digest in (None, item.sha256), f"{item.name} isn't the pinned file"
    (target / weights.MANIFEST).write_text(json.dumps(weights._manifest(model)), encoding="utf-8")  # noqa: SLF001
    assert weights.status(model, root).state == "downloaded"
    return target


@pytest.mark.skipif(not ENABLED, reason="opt-in: set ROW_BOT_LEARNING_TRAINING_E2E=1")
def test_a_personal_model_from_captured_work_to_a_ready_version(monkeypatch):
    data = os.environ.get("ROW_BOT_LEARNING_E2E_DATA", "")
    if not data:
        pytest.skip("set ROW_BOT_LEARNING_E2E_DATA to a scratch data folder")
    # Every module this test loads binds this folder (the suite resets the variable per test).
    monkeypatch.setenv("ROW_BOT_DATA_DIR", data)
    from row_bot import learning

    learning.invalidate()
    from row_bot.learning import capture
    from row_bot.learning.lease import ResourceLease
    from row_bot.learning.store import LearningStore
    from row_bot.learning.training import (
        engine,
        hardware,
        lockfile,
        planner as planner_module,
        recipes,
        releases,
        serving,
    )
    from row_bot.learning.training.supervisor import TrainingSupervisor
    from tests.e2e.learning.scripted_model import ALIAS, ScriptedModel
    from tests.fixtures.learning import save_learning_settings

    found = hardware.detect()
    if found.state not in ("gpu",) or found.gpu is None:
        pytest.skip("needs an NVIDIA graphics card that can train")
    assert learning.preview_enabled(), "start the run with ROW_BOT_LEARNING_PREVIEW=1"
    root = learning.data_dir()
    assert ".row-bot" not in str(root).lower(), "use a scratch data folder, never a profile"
    out = root.parent / "l2c_e2e_results.json"
    results: dict = {"gpu": found.gpu.label, "driver": found.gpu.driver, "steps": {}}

    def save() -> None:
        out.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")

    save_learning_settings(root, enabled=True, train_in_quiet_hours=True, train_where="this_computer",
                           model_download="waiting")
    LearningStore(root).create()
    recipe = recipes.load("8gb")
    smoke = int(os.environ.get("ROW_BOT_LEARNING_E2E_MIN_EXAMPLES", "0") or 0)
    if smoke:  # a quick plumbing run on fewer examples (the recorded run uses the recipe as it is)
        from dataclasses import replace

        recipe = replace(recipe, min_examples=smoke)
    results["recipe"] = {"name": recipe.name, "min_examples": recipe.min_examples, "smoke": bool(smoke)}
    hardware.check(refresh=True, detector=lambda: found)

    # ── The engine (with llama.cpp's tools) and the starting model ──────────
    started = time.monotonic()
    status = engine.status(root, refresh=True)
    if not status.installed:
        plan = lockfile.engine_plan(hardware.engine_variant(found.gpu))
        source = os.environ.get("ROW_BOT_LEARNING_E2E_LLAMA_SOURCE")
        if source:
            downloads = engine.downloads_dir(root)
            downloads.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, downloads / lockfile.LLAMA_CPP_SOURCE.filename)
        uv, cache, python = (os.environ.get(name) for name in (
            "ROW_BOT_LEARNING_E2E_UV", "ROW_BOT_LEARNING_E2E_UV_CACHE", "ROW_BOT_LEARNING_E2E_PYTHON"))
        with Connections(me=True) as watched:
            status = engine.EngineInstaller(plan, root=root, uv=[uv] if uv else None,
                                            uv_cache=Path(cache) if cache else None,
                                            python_source=Path(python) if python else None).install()
        results["steps"]["engine"] = {"seconds": round(time.monotonic() - started, 1), "bytes": status.bytes,
                                      "tools": [tool.filename for tool in plan.tools],
                                      "remote_hosts_during_install": len(watched.remote)}
    assert status.installed and status.llama is not None and not engine.llama_tools_missing(status.llama)
    _copy_weights(Path(os.environ["ROW_BOT_LEARNING_E2E_WEIGHTS"]), root)
    assert engine.LocalEngine(root).ready()
    save()

    # ── Real captured work: real Agent turns against the scripted main model ─
    from row_bot import models, threads
    from row_bot.providers import custom
    from row_bot.providers.selection import model_ref as make_ref
    from row_bot.tools import registry

    with ScriptedModel() as scripted:
        custom.save_custom_endpoint({"id": "e2e-main", "name": "Scripted main model", "base_url": scripted.base_url,
                                     "profile": "llama_cpp", "execution_location": "local", "auth_required": False})
        custom.refresh_custom_endpoint_models("e2e-main")
        probe = custom.probe_custom_endpoint("e2e-main", ALIAS)
        assert probe.get("classification") == "agent_ready", probe
        main_ref = make_ref(custom.custom_provider_id("e2e-main"), ALIAS)
        models.adopt_saved_default(main_ref)
        tools = [tool.name for tool in registry.get_enabled_tools()]
        rng = random.Random(20261016)
        started = time.monotonic()
        failures = 0
        for conversation in range(CONVERSATIONS):
            thread_id = f"e2e-chat-{conversation:03d}-{uuid.uuid4().hex[:6]}"
            threads.create_thread(f"Sums {conversation}", thread_id=thread_id, seed_default_skills=False)
            for _ in range(TURNS):
                prompt, expression = _expression(rng)
                scripted.ask(expression)
                turn = _agent_turn(thread_id, prompt, main_ref, tools)
                failures += bool(turn["errors"]) or turn["calls"] == 0
        assert capture.flush(60)
        results["steps"]["captured_work"] = {
            "seconds": round(time.monotonic() - started, 1), "conversations": CONVERSATIONS,
            "turns": CONVERSATIONS * TURNS, "failed_turns": failures, "model_requests": scripted.requests,
            "agent_requests": scripted.agent_requests, "calculator_bound": "calculate" in scripted.tools_seen,
            "tools_seen": len(scripted.tools_seen),
        }
        save()

    # ── The dataset, planned with the certified recipe ──────────────────────
    store = LearningStore(root)
    supervisor = TrainingSupervisor(root=root, engine=engine.LocalEngine(root), lease=ResourceLease())
    planner = planner_module.Planner(root, jobs=supervisor.jobs, engine=supervisor.engine, recipe_for=lambda: recipe)
    supervisor.dataset = planner_module.LearningDataset(planner)
    started = time.monotonic()
    outcome = planner.plan(force=True)
    assert outcome.job is not None, outcome
    job = outcome.job
    manifest = json.loads((supervisor.jobs.root / "learning_models" / "runs" / job.id / "data" / "dataset.json")
                          .read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (supervisor.jobs.root / "learning_models" / "runs" / job.id / "data"
                                          / "examples.jsonl").read_text(encoding="utf-8").splitlines() if line]
    results["steps"]["dataset"] = {
        "seconds": round(time.monotonic() - started, 1), "id": manifest["id"], "examples": manifest["examples"],
        "validation": manifest["validation"], "left_out": manifest["left_out"], "providers": manifest["providers"],
        "measured": manifest["measured"], "steps": job.steps_total, "rows_bytes": sum(len(json.dumps(r)) for r in rows),
        "held_out_in_vault": len(store.held_out_ids()),
    }
    save()
    assert manifest["measured"] and manifest["examples"] >= recipe.min_examples

    # ── Training for real: the 8 GB recipe, one pass ────────────────────────
    started = time.monotonic()
    with Connections() as watched, GpuMemory() as gpu:
        supervisor.train_now(job.id)
        finished = supervisor.wait_for(lambda found_job: found_job is not None and found_job.state in (
            "evaluating", "failed", "paused"), timeout=4 * 3600, job_id=job.id)
        supervisor.wait_idle(60)
    results["steps"]["training"] = {
        "seconds": round(time.monotonic() - started, 1), "state": finished.state, "steps": finished.steps_done,
        "seconds_per_step": finished.seconds_per_step, "loss": finished.loss, "error": finished.error_code,
        "peak_gpu_mib": gpu.peak, "gpu_samples": gpu.samples, "connections": watched.summary(),
    }
    save()
    assert finished.state == "evaluating", finished
    assert watched.remote == set()

    try:
        # ── Export and Ollama: the version made ready ────────────────────────────
        maker = releases.ReleaseMaker(root, jobs=supervisor.jobs, recipe_for=lambda: recipe, start_threads=False)
        started = time.monotonic()
        with Connections() as watched, GpuMemory() as gpu:
            maker.start(supervisor.jobs.get(job.id))
        state = releases.read_state(releases.version_dir(job.version, root))
        folder = releases.version_dir(job.version, root)
        results["steps"]["release_ollama"] = {
            "seconds": round(time.monotonic() - started, 1), "phase": state.phase, "failure": state.failure,
            "gguf": state.gguf, "gguf_bytes": state.gguf_bytes, "adapter_bytes": state.adapter_bytes,
            "model_ref": state.model_ref, "checks": dict(state.checks), "peak_gpu_mib": gpu.peak,
            "connections_of_the_export": watched.summary(),
            "files": sorted(item.name for item in folder.iterdir()),
        }
        save()
        assert watched.remote == set()  # the export (and Ollama's create) reached nothing but this computer
        assert state.phase == releases.READY, state
        assert (folder / releases.MANIFEST_FILE).is_file() and (folder / releases.MODEL_CARD_FILE).is_file()
        assert supervisor.jobs.get(job.id).state == "ready"
        release = store.get("release", state.release_id)
        assert release is not None and release.identity.runtime is not None

        # ── llama.cpp's server: the same version, through Row-Bot's llama_cpp profile ──
        server = os.environ.get("ROW_BOT_LEARNING_E2E_LLAMA_SERVER")
        if server:
            gguf = folder / str(state.gguf)
            argv = [server, "-m", str(gguf), "-a", serving.model_name(job.version), "--host", "127.0.0.1",
                    "--port", str(serving.PORTS["llama_cpp"]), "-c", str(recipe.serve_context), "-ngl", "99",
                    "--jinja", "--offline", "--no-webui", "--reasoning", "off", "-np", "1"]
            log = open(root.parent / "l2c_llama_server.log", "wb")  # noqa: SIM115 - closed below
            process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                started = time.monotonic()
                while time.monotonic() - started < 180 and serving.served_models(
                        f"http://127.0.0.1:{serving.PORTS['llama_cpp']}/v1") is None:
                    time.sleep(1)
                served = serving.serve("llama_cpp", version=job.version, gguf=gguf, serve_context=recipe.serve_context,
                                       folder=folder, root=root)
                results["steps"]["llama_cpp"] = {"seconds": round(time.monotonic() - started, 1),
                                                 **serving.describe(served)}
                save()
                assert served.state == "ready", served
            finally:
                process.terminate()
                process.wait(30)
                log.close()

    finally:
        # ── Tidy: whatever this test made in Ollama goes (the existing models stay) ──
        serving.withdraw("ollama", job.version, root=root)
        results["steps"]["tidy"] = {"ollama_removed": serving.ollama_tag(job.version, root)}
        save()
