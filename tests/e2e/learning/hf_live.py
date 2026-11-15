"""The live Hugging Face checks of ``test_learning_hugging_face_e2e.py``, run in a profile signed in to Hugging Face.

Run as ``python tests/e2e/learning/hf_live.py <check>`` with ``ROW_BOT_DATA_DIR`` set to that profile (the
keychain entry is the profile's own); prints one JSON line. Checks: ``round-trip`` (free: the account, Jobs'
prices, a private dataset made, written, read and deleted) and ``job`` (a CPU job of a few seconds, found again
by its label, its log read and its billed time). Nothing reads or uploads anyone's real data.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import uuid
from pathlib import Path


def _hub():
    from row_bot.learning.remote import auth
    from row_bot.learning.remote.hub import HfHub

    sign_in = auth.get()
    state = sign_in.status()
    if state.state != "signed_in":
        raise SystemExit(json.dumps({"skip": f"the profile isn't signed in to Hugging Face ({state.state})"}))
    return HfHub(sign_in.token, namespace=lambda: state.user), state.user


def round_trip() -> dict:
    hub, user = _hub()
    account = hub.account()
    prices = {flavor.name: flavor.price_per_hour for flavor in hub.flavors()}
    repo = f"{user}/row-bot-e2e-{uuid.uuid4().hex[:12]}"
    hub.create_private_repo(repo, "dataset")
    try:
        with tempfile.TemporaryDirectory() as folder:
            sample = Path(folder) / "sample.jsonl"
            sample.write_text('{"messages": []}\n', encoding="utf-8")
            hub.upload(repo, "dataset", {"data/sample.jsonl": sample})
        read = hub.read_text(repo, "dataset", "data/sample.jsonl")
        address = hub.download_address(repo, "dataset", "data/sample.jsonl")
    finally:
        deleted = hub.delete_repo(repo, "dataset")
    return {"user": account.name, "pro": account.pro, "prices": prices, "read": read,
            "address_https": address.startswith("https://"), "deleted": deleted,
            "exists_after": hub.repo_exists(repo, "dataset")}


def job() -> dict:
    from row_bot.learning.remote import training
    from row_bot.learning.remote.hub import labels_for

    hub, _user = _hub()
    price = {flavor.name: flavor.price_per_hour for flavor in hub.flavors()}["cpu-basic"]
    marker = f"e2e_{uuid.uuid4().hex[:12]}"
    line = "import json; print('ROWBOT ' + json.dumps({'type': 'stage', 'stage': %r}))" % marker
    submitted = hub.run_job(image="python:3.12-slim", flavor="cpu-basic", timeout_seconds=300, env={}, secrets={},
                            labels=labels_for(marker), command=["python", "-c", line])
    deadline = time.monotonic() + 600
    found = hub.job(submitted.id)
    while not found.terminal and time.monotonic() < deadline:
        time.sleep(10)
        found = hub.job(submitted.id)
    if not found.terminal:
        hub.cancel(submitted.id)
    by_label = [item.id for item in hub.find_jobs(labels_for(marker))]
    events = training.parse_lines(hub.log_lines(submitted.id))
    seconds = found.running_seconds or 0
    return {"stage": found.stage, "by_label": by_label == [submitted.id], "events": events,
            "marker": marker, "running_seconds": seconds, "cost_usd": training.cost(seconds, price),
            "price_per_hour": price}


if __name__ == "__main__":
    print(json.dumps({"round-trip": round_trip, "job": job}[sys.argv[1]]()))
