"""Opt-in: training on Hugging Face against the real Hub, in a scratch profile signed in to Hugging Face (C1).

Sign the profile in first through Row-Bot (Settings › Learning › Train on Hugging Face › Sign in), then:

    $env:ROW_BOT_LEARNING_HF_E2E = "1"
    $env:ROW_BOT_LEARNING_HF_PROFILE = "<the signed-in scratch data folder>"
    $env:ROW_BOT_LEARNING_HF_E2E_PAID = "1"   # the job test: a CPU job of a few seconds (well under $0.01)

The round trip is free (a private dataset made, written, read and deleted). The whole paid path (quote, run,
lost contact, download, serve, compare, cleanup) is the live end to end in the C1 hand-off, run from the app.
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
ENABLED = os.environ.get("ROW_BOT_LEARNING_HF_E2E") == "1"


def _check(name: str) -> dict:
    profile = os.environ.get("ROW_BOT_LEARNING_HF_PROFILE", "")
    if not profile:
        pytest.skip("set ROW_BOT_LEARNING_HF_PROFILE to a scratch profile signed in to Hugging Face")
    env = {key: value for key, value in os.environ.items() if not key.startswith("ROW_BOT_")}
    env["ROW_BOT_DATA_DIR"] = profile
    finished = subprocess.run([sys.executable, str(Path(__file__).with_name("hf_live.py")), name], cwd=ROOT,
                              env=env, capture_output=True, text=True, timeout=900)
    found = json.loads((finished.stdout.strip().splitlines() or ["{}"])[-1])
    if "skip" in found:
        pytest.skip(found["skip"])
    assert finished.returncode == 0, finished.stderr[-4000:]
    return found


@pytest.mark.skipif(not ENABLED, reason="opt-in: set ROW_BOT_LEARNING_HF_E2E=1")
def test_the_hub_round_trip_makes_reads_and_deletes_a_private_dataset():
    found = _check("round-trip")

    assert found["user"] and found["read"] == '{"messages": []}\n' and found["address_https"]
    assert found["deleted"] and not found["exists_after"]
    assert found["prices"]["l40sx1"] > 0  # the quote's hardware is priced by Hugging Face itself


@pytest.mark.skipif(not (ENABLED and os.environ.get("ROW_BOT_LEARNING_HF_E2E_PAID") == "1"),
                    reason="opt-in: set ROW_BOT_LEARNING_HF_E2E=1 and ROW_BOT_LEARNING_HF_E2E_PAID=1")
def test_a_job_runs_is_found_by_its_label_and_reports_its_lines_and_billed_time():
    found = _check("job")

    assert found["stage"] == "COMPLETED" and found["by_label"]
    assert {"type": "stage", "stage": found["marker"]} in found["events"]
    assert found["running_seconds"] >= 0 and found["cost_usd"] < 0.05
