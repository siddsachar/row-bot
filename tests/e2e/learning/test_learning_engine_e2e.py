"""Opt-in: the real training engine and starting model on this computer (L2b).

One real engine install into a scratch data folder, the real Qwen3.5-2B download from Hugging Face,
the engine's own check (PyTorch finds the graphics card; every engine package imports under its
network guard) with the whole process tree's connections sampled, then Remove engine and Delete
starting model. Skipped without ``ROW_BOT_LEARNING_ENGINE_E2E=1`` or an NVIDIA graphics card.

It downloads only what the product would (the pinned model, and the engine's locked wheels unless a
uv cache that already holds them is given)::

    $env:ROW_BOT_LEARNING_ENGINE_E2E = "1"
    $env:ROW_BOT_LEARNING_E2E_DATA = "<scratch folder>"            # never a real profile
    $env:ROW_BOT_LEARNING_E2E_UV = "<uv.exe>"                        # optional: else the lock's pinned uv
    $env:ROW_BOT_LEARNING_E2E_UV_CACHE = "<uv cache with the wheels>" # optional: build offline from it
    $env:ROW_BOT_LEARNING_E2E_PYTHON = "<standalone CPython 3.12.15 folder>"  # optional: else uv installs one
    uv run python -m pytest tests/e2e/learning -m e2e -s

Results (times, sizes, connections) are written to ``<scratch>/l2b_e2e_results.json``.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from tests.helpers.learning_connections import Connections

pytestmark = [pytest.mark.e2e]

ENABLED = os.environ.get("ROW_BOT_LEARNING_ENGINE_E2E") == "1"


def _gpu():
    from row_bot.learning.training import hardware

    return hardware.detect()


@pytest.mark.skipif(not ENABLED, reason="opt-in: set ROW_BOT_LEARNING_ENGINE_E2E=1")
def test_the_real_engine_and_starting_model_install_check_and_remove(tmp_path):
    from row_bot.learning.training import engine, fetch, hardware, lockfile, weights

    found = _gpu()
    if found.state not in ("gpu", "old_driver") or found.gpu is None:
        pytest.skip("needs an NVIDIA graphics card")
    variant = hardware.engine_variant(found.gpu)
    assert variant is not None, "the NVIDIA driver is older than 570"
    root = Path(os.environ.get("ROW_BOT_LEARNING_E2E_DATA") or tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    assert not (root / "learning.db").exists(), "use a scratch data folder, never a profile"
    results: dict = {"gpu": found.gpu.label, "driver": found.gpu.driver, "variant": variant, "steps": {}}
    out = root / "l2b_e2e_results.json"

    def save() -> None:
        out.write_text(json.dumps(results, indent=1), encoding="utf-8")

    plan = lockfile.engine_plan(variant)
    results["plan"] = {"wheels": len(plan.wheels), "download_bytes": plan.download_bytes(), "identity": plan.identity}
    uv = os.environ.get("ROW_BOT_LEARNING_E2E_UV")
    cache = os.environ.get("ROW_BOT_LEARNING_E2E_UV_CACHE")
    python = os.environ.get("ROW_BOT_LEARNING_E2E_PYTHON")
    phases: list[tuple[float, str, int]] = []
    started = time.monotonic()
    installer = engine.EngineInstaller(
        plan, root=root, uv=[uv] if uv else None, uv_cache=Path(cache) if cache else None,
        python_source=Path(python) if python else None,
        on_progress=lambda progress: phases.append((round(time.monotonic() - started, 1), progress.phase,
                                                    progress.done)))
    with Connections() as watched:
        installed = installer.install()
    results["steps"]["install"] = {
        "seconds": round(time.monotonic() - started, 1), "bytes_on_disk": installed.bytes,
        "from_uv_cache": bool(cache), "phases": [(at, phase) for at, phase, _ in phases if phase != "engine"][:10],
        "connections": {"ticks": watched.ticks, "samples": watched.samples, "sockets": len(watched.sockets),
                        "remote": sorted(map(list, watched.remote))},
    }
    save()
    assert installed.state == "installed"
    if cache:  # built offline: nothing may have left this computer
        assert watched.remote == set()

    started = time.monotonic()
    with Connections() as watched:
        report = engine.engine_check(installed.python, installed.site, cwd=root / "l2b_check", check=lambda: None)
    results["steps"]["check"] = {
        "seconds": round(time.monotonic() - started, 1), "report": report,
        "connections": {"ticks": watched.ticks, "samples": watched.samples, "sockets": len(watched.sockets),
                        "remote": sorted(map(list, watched.remote))},
    }
    save()
    assert report["cuda"] is True and report["torch_cuda"]
    assert report["blocked_connections"] == 0
    assert watched.remote == set()  # zero non-loopback connections while every package imported

    model = weights.QWEN35_2B
    seen: list[int] = []
    started = time.monotonic()
    with Connections(me=True) as watched:  # this download runs in the test process itself
        folder = weights.download(model, root=root, fetcher=fetch.Fetcher(), progress=seen.append)
    seconds = time.monotonic() - started
    hosts = sorted({host for host, _ in watched.remote})
    results["steps"]["model_download"] = {"seconds": round(seconds, 1), "bytes": model.download_bytes,
                                          "mb_per_second": round(model.download_bytes / 1e6 / max(seconds, 0.1), 1),
                                          "remote_addresses": len(hosts)}
    save()
    assert weights.status(model, root).state == "downloaded"
    assert (folder / "LICENSE").read_text(encoding="utf-8").lstrip().startswith("Apache License")
    assert json.loads((folder / "model.json").read_text(encoding="utf-8"))["licence"] == "Apache 2.0"
    assert engine.LocalEngine(root).ready() is True

    started = time.monotonic()
    freed_engine = engine.remove(root)
    freed_model = weights.delete(model, root)
    results["steps"]["remove"] = {"seconds": round(time.monotonic() - started, 1), "engine_freed": freed_engine,
                                  "model_freed": freed_model}
    save()
    assert freed_engine >= installed.bytes and freed_model >= model.download_bytes
    assert not (root / "runtimes" / "learning").exists()
    shutil.rmtree(root / "l2b_check", ignore_errors=True)
