"""Opt-in: the release check on seeded people, for real (S1).

Runs ``scripts/learning_release_check.py`` as a person would, in scratch folders, with the main model and
the simulated person on the ChatGPT lane (the eval profile's sign-in; refreshed tokens go back there). It
spends cloud tokens, and the second test trains on this computer's graphics card, so both are opt-in:

    $env:ROW_BOT_LEARNING_SEEDED_E2E = "1"
    $env:ROW_BOT_LIVE_EVAL_CODEX_PROFILE = "<the eval profile>"
    $env:ROW_BOT_LEARNING_SEEDED_SCRATCH = "<a scratch folder>"
    $env:ROW_BOT_LEARNING_SEEDED_RUNTIMES = "<a data folder with the training engine>"   # the GPU test
    $env:ROW_BOT_LEARNING_SEEDED_GPU = "1"                                              # the GPU test
    $env:ROW_BOT_LEARNING_SEEDED_BOOKING = "<the shared GPU booking file>"               # optional

The full run (``--people all --days 30``) is the release check itself; these are its small versions.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.live_provider]

ROOT = Path(__file__).resolve().parents[3]
ENABLED = os.environ.get("ROW_BOT_LEARNING_SEEDED_E2E") == "1"


def _scratch(name: str) -> Path:
    base = os.environ.get("ROW_BOT_LEARNING_SEEDED_SCRATCH", "")
    if not base:
        pytest.skip("set ROW_BOT_LEARNING_SEEDED_SCRATCH to a scratch folder")
    return Path(base) / name


def _run(folder: Path, *extra: str) -> dict:
    profile = os.environ.get("ROW_BOT_LIVE_EVAL_CODEX_PROFILE", "")
    if not profile:
        pytest.skip("set ROW_BOT_LIVE_EVAL_CODEX_PROFILE to the eval profile")
    env = {key: value for key, value in os.environ.items() if not key.startswith("ROW_BOT_")}
    env["ROW_BOT_DATA_DIR"] = str(folder / "orchestrator")
    command = [sys.executable, str(ROOT / "scripts" / "learning_release_check.py"), "--out", str(folder / "out"),
               "--scratch", str(folder / "scratch"), "--eval-profile", profile, *extra]
    finished = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=6 * 3600)
    assert finished.returncode == 0, finished.stdout[-4000:] + finished.stderr[-4000:]
    return json.loads((folder / "out" / "report.json").read_text(encoding="utf-8"))


@pytest.mark.skipif(not ENABLED, reason="opt-in: set ROW_BOT_LEARNING_SEEDED_E2E=1")
def test_one_seeded_person_works_through_a_day_and_its_night_on_the_chatgpt_lane():
    folder = _scratch("e2e-day")
    report = _run(folder, "--people", "mara", "--days", "1", "--no-gpu", "--no-crash", "--cap-tokens", "1500000")
    (person,) = report["people"]
    assert person["live"]["requests"] >= 1
    assert person["past"]["import"]["state"] == "ready" and person["past"]["historical"] > 100
    assert person["tokens"]["main_model_turns"] > 0 and report["tokens"]["within_cap"]
    state = json.loads((folder / "out" / "8gb" / "mara" / "state.json").read_text(encoding="utf-8"))
    night = state["nights"]["1"]["steps"]
    assert list(night) == ["dream", "vault", "learning"] and night["vault"]["outcome"]["summary"]["pack_tasks"] == 32


@pytest.mark.skipif(not (ENABLED and os.environ.get("ROW_BOT_LEARNING_SEEDED_GPU") == "1"),
                    reason="opt-in: set ROW_BOT_LEARNING_SEEDED_E2E=1 and ROW_BOT_LEARNING_SEEDED_GPU=1")
def test_the_loop_trains_survives_a_crash_and_use_and_compares_on_this_graphics_card():
    runtimes = os.environ.get("ROW_BOT_LEARNING_SEEDED_RUNTIMES", "")
    if not runtimes:
        pytest.skip("set ROW_BOT_LEARNING_SEEDED_RUNTIMES to a data folder with the training engine")
    folder = _scratch("e2e-loop")
    extra = ["--people", "mara", "--days", "2", "--runtimes-from", runtimes, "--pilot-compare",
             "--cap-tokens", "3000000"]
    if os.environ.get("ROW_BOT_LEARNING_SEEDED_BOOKING"):
        extra += ["--gpu-booking", os.environ["ROW_BOT_LEARNING_SEEDED_BOOKING"]]
    report = _run(folder, *extra)
    (person,) = report["people"]
    assert person["versions"] and person["versions"][0]["phase"] == "ready"
    survival = person["survival"]
    assert survival["crash_mid_training"]["state"] == "passed"
    assert survival["pause_on_use"]["state"] == "passed"
    assert survival["version_skew"]["state"] == "passed"
    assert survival["source_deletion"]["state"] == "passed"
    assert survival["trial_use_undo"]["state"] == "passed"
    assert survival["negative_result"]["state"] in ("passed", "not_observed")
