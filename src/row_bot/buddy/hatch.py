"""Buddy Hatch job bookkeeping.

One Hatch runs at a time. ``client_hatch`` owns what a job does (painting,
expressions, the living pack) and its durable receipts; this module owns the
single in-memory job record, its lock and its cancellation.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from typing import Any, Callable

from row_bot.data_paths import get_row_bot_data_dir

_DATA_DIR = get_row_bot_data_dir() / "buddy_hatches"
_JOB_LOCK = threading.Lock()
_CURRENT_JOB: dict[str, Any] = {}
_RUNNING_JOB_STATES = {"queued", "running", "cancelling"}


def _job_snapshot_unlocked() -> dict[str, Any]:
    return {key: value for key, value in _CURRENT_JOB.items() if not key.startswith("_")}


def get_hatch_generation_status() -> dict[str, Any]:
    """Return the current background Buddy Hatch status."""

    with _JOB_LOCK:
        return _job_snapshot_unlocked()


def _update_hatch_job(job_id: str, **updates: Any) -> dict[str, Any]:
    with _JOB_LOCK:
        if _CURRENT_JOB.get("id") != job_id:
            return _job_snapshot_unlocked()
        _CURRENT_JOB.update(updates)
        _CURRENT_JOB["updated_at"] = time.time()
        return _job_snapshot_unlocked()


def _hatch_pack_name(prompt: str, draft_id: str) -> str:
    first_line = " ".join(str(prompt or "").strip().splitlines()[0:1]).strip()
    named = re.search(r"\bnamed\s+([A-Za-z0-9_-]+)", first_line, flags=re.IGNORECASE)
    if named:
        return named.group(1).replace("_", " ").replace("-", " ").title()
    words = first_line.split()
    if words:
        return "Generated " + " ".join(words[:4]).strip(" .,;:")
    return f"Generated {draft_id}"


def start_hatch_generation_job(
    prompt: str,
    *,
    mode: str,
    total_steps: int,
    _client_runner: Callable[[str, threading.Event], None],
    _command_id: str,
    _validate: Callable[[], None],
    _checkpoint: Callable[[str], None],
) -> dict[str, Any]:
    """Admit one Hatch and start its worker thread."""

    safe_mode = mode if mode in {"full", "motion", "still"} else "full"
    with _JOB_LOCK:
        _validate()
        if _CURRENT_JOB.get("status") in _RUNNING_JOB_STATES:
            raise RuntimeError("Buddy generation is already running")
        if str(uuid.UUID(_command_id)) != _command_id:
            raise ValueError("invalid_hatch_admission")
        job_id = f"buddy-hatch-{_command_id}"
        _checkpoint(job_id)
        cancellation = threading.Event()
        _CURRENT_JOB.clear()
        _CURRENT_JOB.update(
            {
                "id": job_id,
                "status": "queued",
                "mode": safe_mode,
                "phase": "queued",
                "message": "Queued Buddy generation",
                "pack_id": "",
                "completed_clips": 0,
                "total_clips": total_steps,
                "started_at": time.time(),
                "updated_at": time.time(),
                "finished_at": 0.0,
                "error": "",
                "_client_cancel": cancellation,
            }
        )
        snapshot = _job_snapshot_unlocked()

    thread = threading.Thread(target=_client_runner, args=(job_id, cancellation), daemon=True,
                              name=f"buddy-hatch-{safe_mode}")
    _update_hatch_job(job_id, status="running", phase="starting", message="Starting Buddy generation")
    try:
        thread.start()
    except Exception:
        _update_hatch_job(job_id, status="failed", phase="failed", message="Buddy worker could not start",
                          error="worker_unavailable")
        raise
    return snapshot


def cancel_client_hatch(job_id: str, *, validate: Callable[[], None]) -> dict[str, Any]:
    """Request exact owned worker cancellation; active provider work must drain."""
    with _JOB_LOCK:
        validate()
        if _CURRENT_JOB.get("id") != job_id or "_client_cancel" not in _CURRENT_JOB:
            raise ValueError("hatch_job_unavailable")
        _CURRENT_JOB["_client_cancel"].set()
        if _CURRENT_JOB.get("status") in _RUNNING_JOB_STATES:
            _CURRENT_JOB.update(status="cancelling", message="Stopping Buddy generation")
        return _job_snapshot_unlocked()
