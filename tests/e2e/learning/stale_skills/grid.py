"""Phase 3's runs (Design A, Lane B): every cell of the pre-registered grid, one after another, resumed from its
checkpoint after any stop.

    python -m tests.e2e.learning.stale_skills.grid --queue main|local --root <phase 3 folder>
        --eval-profile <eval profile> [--gpu-booking <booking file>] [--modelfile-4b <the 4B's Modelfile>]

**The grid** (``PREREG-DESIGN-A.md`` §5): six people × 120 simulated days, paired (the same seed, people and changes
in every cell). The ``main`` queue: the main model on the ChatGPT lane, arms N, R and D. The ``local`` queue: Qwen3.5
9B with arms N, R, D, F and A; the Qwen3.5 4B (the precedence study's build) and ``qwen3.8:27b`` with N and R. Each
cell is its own ``sa3_<model>_<arm>_<person>`` profile under ``--root`` (``ROW_BOT_DATA_DIR`` set for it).

**One cell at a time per queue**, each a ``run.py`` process with ``--blind`` (no verdict in the notes, nothing
scored in ``stale.json``: no interim outcome before every run is done). On its exit:

- done (every step of the 120 days saved): the next cell;
- a usage or rate-limit error (exit 76): the whole queue waits (30 minutes, then 1, 2, 3 hours) and resumes the same
  cell from its checkpoint;
- the token cap (exit 75): the cell is marked ``capped`` and left for a decision (the cap is set well above need);
- a crash: resumed from the checkpoint at once (the interrupted step is logged as redone by the run); after three
  crashes without a new step done it waits 30 minutes; after six it is marked ``failed`` and left for a look.

**The confirmation** (``PREREG-DESIGN-A-CONFIRM.md``): ``--queue confirm``, the main model's arms N and R on the six
people at the fresh world seed ``CONFIRM_SEED``, each cell ``c1_main_<arm>_<person>`` under its own ``--root``.

Only the main and confirm queues' processes skip the GPU booking file (they never book the card, and a run's release would
otherwise close the local run's booking: both write as lane B). ``status-<queue>.json`` and ``status.md`` say, per
cell, its state, days done, ChatGPT-lane tokens and booked GPU-hours (no outcome).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.e2e.learning.stale_skills import world

HERE = Path(__file__).resolve()
CHECKOUT = HERE.parents[4]
EXIT_TOKEN_CAP, EXIT_LIMIT, EXIT_UNREACHABLE = 75, 76, 77
LIMIT_WAITS = (30 * 60, 60 * 60, 120 * 60, 180 * 60)
CRASH_PAUSE = 30 * 60
#: A local run keeps the card for simulated days up to this long a booking (lane A's blocks are 40-90 min); lane C
#: (paper-critical training) gets it at once when it writes that it waits.
BLOCK_MINUTES = 40
#: What each model label runs: the lane, the Ollama model, and from when no new simulated day starts on the card.
MODELS: dict[str, dict[str, str]] = {
    "main": {"lane": "main", "label": "Main model (ChatGPT lane, eval profile)"},
    "q9b": {"lane": "local", "local": "qwen3.5:9b", "cutoff": "00:25", "label": "Qwen3.5 9B"},
    # DA1: the precedence study's GGUF under a name in the qwen3.5 family, so the build reads its tool support as it
    # reads the 9B's (as "prec-qwen3.5-4b" every Agent turn was refused as not tool-capable).
    "q4b": {"lane": "local", "local": "qwen3.5:4b-prec", "cutoff": "00:25", "label": "Qwen3.5 4B"},
    "q27b": {"lane": "local", "local": "qwen3.8:27b", "cutoff": "23:50", "label": "qwen3.8:27b"},
}
#: Per-cell ChatGPT-lane caps: well above need (a main cell is expected at about 20-25M), so a cap is a stop to look.
CAPS = {"main": 80_000_000, "local": 4_000_000}
#: The confirmation's fresh worlds (``PREREG-DESIGN-A-CONFIRM.md`` §3), in place of Design A's ``world.SEED``.
CONFIRM_SEED = 20261121


@dataclass(frozen=True)
class Cell:
    model: str
    arm: str
    person: str
    #: ``sa3``: Design A's grid (``world.SEED``); ``c1``: the confirmation (``CONFIRM_SEED``).
    study: str = "sa3"

    @property
    def name(self) -> str:
        return f"{self.study}_{self.model}_{self.arm}_{self.person}"

    @property
    def seed(self) -> int:
        return CONFIRM_SEED if self.study == "c1" else world.SEED


def cells(queue: str) -> list[Cell]:
    """The queue's cells in the order they run (local: H-A1's 9B arms first, then D and A, then H-A2's models)."""

    if queue == "confirm":
        return [Cell("main", arm, person, "c1") for person in world.PEOPLE for arm in ("N", "R")]
    if queue == "main":
        return [Cell("main", arm, person) for person in world.PEOPLE for arm in ("N", "R", "D")]
    found = [Cell("q9b", arm, person) for person in world.PEOPLE for arm in ("N", "R", "F")]
    found += [Cell("q9b", arm, person) for person in world.PEOPLE for arm in ("D", "A")]
    found += [Cell("q4b", arm, person) for person in world.PEOPLE for arm in ("N", "R")]
    found += [Cell("q27b", arm, person) for person in world.PEOPLE for arm in ("N", "R")]
    return found


def command(cell: Cell, root: Path, eval_profile: str, booking: str, night: str = "") -> list[str]:
    """A cell's run. ``night`` replaces a local model's cut-off (``05:00``: no night pause, for a night the person's
    Row-Bot stays closed and no other lane has the card)."""

    folder = root / cell.name
    model = MODELS[cell.model]
    found = [sys.executable, "-m", "tests.e2e.learning.stale_skills.run", "--arm", cell.arm, "--person", cell.person,
             "--model", model["lane"], "--data", str(folder / "data"), "--run-dir", str(folder / "run"),
             "--days", str(world.DAYS), "--phrasing", "main", "--sim-date", "--blind", "--eval-profile", eval_profile,
             "--cap-tokens", str(CAPS[model["lane"]])]
    if cell.seed != world.SEED:
        found += ["--seed", str(cell.seed)]
    if model["lane"] == "local":
        found += ["--local-model", model["local"], "--no-new-day-from", night or model["cutoff"],
                  "--block-minutes", str(BLOCK_MINUTES), "--priority-lanes", "lane-C"]
        if booking:
            found += ["--gpu-booking", booking]
    return found


# ── Progress (no outcome) ────────────────────────────────────────────────────


def progress(folder: Path) -> dict[str, Any]:
    """Days done, ChatGPT-lane tokens and booked GPU seconds of a cell, from its state and usage ledger."""

    try:
        state = json.loads((folder / "run" / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"days": 0, "steps": 0, "tokens": 0, "gpu_seconds": 0.0, "finished": False}
    return progress_from_state(state, folder / "data")


def progress_from_state(state: dict[str, Any], data: Path | None) -> dict[str, Any]:
    """:func:`progress` from a loaded ``state.json`` (and the cell's data folder, for the usage ledger)."""

    days = sum(1 for night in (state.get("nights") or {}).values() if night.get("done"))
    steps = sum(len(state.get(key) or ()) for key in ("runs", "tells", "probes", "evenings", "mornings")) + days
    sim = state.get("sim_tokens") or {}
    tokens = int(sim.get("input_tokens") or 0) + int(sim.get("output_tokens") or 0) + (
        ledger_cloud(data) if data is not None else 0)
    return {"days": days, "steps": steps, "tokens": tokens,
            "gpu_seconds": float((state.get("gpu") or {}).get("seconds") or 0.0),
            "finished": bool((state.get("stages") or {}).get("days"))}


def ledger_cloud(data: Path) -> int:
    path = data / "usage.db"
    if not path.is_file():
        return 0
    try:
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)) as connection:
            rows = connection.execute("SELECT provider, COALESCE(SUM(input_tokens),0) + COALESCE(SUM(output_tokens),0)"
                                      " FROM model_usage GROUP BY provider").fetchall()
    except sqlite3.Error:
        return 0
    local = ("ollama", "llama_cpp", "lmstudio", "vllm", "custom")
    return sum(int(total) for provider, total in rows if not str(provider).startswith(local))


class Status:
    """``status-<name>.json`` (one per supervisor; the name starts with its queue) and the combined ``status.md``
    (atomic writes)."""

    def __init__(self, root: Path, queue: str, name: str = "") -> None:
        self.root = root
        self.queue = queue
        self.path = root / f"status-{name or queue}.json"
        try:
            self.data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("cells", {})
        self.data.setdefault("events", [])

    def cell(self, cell: Cell) -> dict[str, Any]:
        return self.data["cells"].setdefault(cell.name, {"state": "pending", "starts": 0, "limit_waits": 0,
                                                         "crashes": 0})

    def event(self, text: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}"
        self.data["events"].append(line)
        print(line, flush=True)
        self.save()

    def save(self) -> None:
        self.data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        for name, entry in self.data["cells"].items():
            entry.update(progress(self.root / name))
        _atomic(self.path, json.dumps(self.data, indent=1, sort_keys=True))
        _atomic(self.root / "status.md", render(self.root))


def _atomic(path: Path, text: str) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    for _ in range(20):
        try:
            temporary.replace(path)
            return
        except PermissionError:  # a reader holds it on Windows
            time.sleep(0.5)
    temporary.unlink(missing_ok=True)


def merged(root: Path, queue: str) -> dict[str, Any] | None:
    """Every supervisor's status of ``queue`` in one: per cell the entry that isn't pending (the last updated)."""

    found = []
    for path in sorted(root.glob(f"status-{queue}*.json")):
        try:
            found.append((path.stem.removeprefix("status-"), json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    if not found:
        return None
    found.sort(key=lambda item: str(item[1].get("updated") or ""))
    cells_: dict[str, Any] = {}
    for _, data in found:
        for name, entry in (data.get("cells") or {}).items():
            if entry.get("state") != "pending" or name not in cells_:
                cells_[name] = entry
    events = sorted(line for _, data in found for line in data.get("events") or ())
    return {"cells": cells_, "events": events, "updated": found[-1][1].get("updated"),
            "names": [name for name, _ in found]}


def render(root: Path) -> str:
    lines = ["# Stale skills phase 3: run status (no outcomes)", ""]
    total_tokens, total_gpu = 0, 0.0
    for queue in ("main", "local", "confirm"):
        data = merged(root, queue)
        if data is None:
            continue
        lines += [f"## Queue {queue} (updated {data.get('updated')}; supervisors {', '.join(data['names'])})", "",
                  "| Cell | State | Days done | Starts | Limit waits | Crashes | ChatGPT-lane tokens | GPU-hours |",
                  "|---|---|---|---|---|---|---|---|"]
        for cell in cells(queue):
            entry = (data.get("cells") or {}).get(cell.name) or progress(root / cell.name) | {"state": "pending"}
            tokens, gpu = int(entry.get("tokens") or 0), float(entry.get("gpu_seconds") or 0.0) / 3600
            total_tokens += tokens
            total_gpu += gpu
            lines.append(f"| {cell.name} | {entry.get('state')} | {entry.get('days', 0)}/{world.DAYS} | "
                         f"{entry.get('starts', 0)} | {entry.get('limit_waits', 0)} | {entry.get('crashes', 0)} | "
                         f"{tokens:,} | {gpu:.2f} |")
        done = sum(1 for cell in cells(queue) if ((data.get("cells") or {}).get(cell.name) or {}).get("state") == "done")
        lines += ["", f"Cells done: {done} of {len(cells(queue))}.", "", "Last events:", ""]
        lines += [f"- {line}" for line in (data.get("events") or [])[-8:]] + [""]
    lines += [f"**Totals:** ChatGPT-lane tokens {total_tokens:,}; GPU-hours booked {total_gpu:.2f}.", ""]
    return "\n".join(lines)


# ── Running ──────────────────────────────────────────────────────────────────


def _alive(pid: int) -> bool:
    import psutil

    try:
        return psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def lock(folder: Path) -> bool:
    """Take the cell for this supervisor (``grid.lock``: the pid that runs it). False while a live process holds it;
    a lock whose process ended is taken over."""

    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "grid.lock"
    for _ in range(2):
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                holder = int(json.loads(path.read_text(encoding="utf-8")).get("pid") or 0)
            except (OSError, ValueError):
                holder = 0
            if holder and holder != os.getpid() and _alive(holder):
                return False
            path.unlink(missing_ok=True)
            continue
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "at": time.strftime("%Y-%m-%d %H:%M:%S")}))
        return True
    return False


def unlock(folder: Path) -> None:
    path = folder / "grid.lock"
    try:
        if int(json.loads(path.read_text(encoding="utf-8")).get("pid") or 0) == os.getpid():
            path.unlink()
    except (OSError, ValueError):
        pass


def ensure_model(cell: Cell, status: Status, modelfile: str = "") -> None:
    """The 4B in Ollama before a 4B cell: registered from the precedence study's GGUF with Row-Bot's Modelfile
    (``--modelfile-4b``) when it isn't there yet."""

    if cell.model != "q4b":
        return
    listed = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=120).stdout
    if MODELS["q4b"]["local"] in listed:
        return
    if not modelfile:
        raise SystemExit(f"{MODELS['q4b']['local']} isn't in Ollama: pass --modelfile-4b")
    status.event(f"registering {MODELS['q4b']['local']} in Ollama from {modelfile}")
    subprocess.run(["ollama", "create", MODELS["q4b"]["local"], "-f", modelfile], check=True,
                   capture_output=True, text=True, timeout=1800)


def unload(cell: Cell) -> None:
    model = MODELS[cell.model]
    if model["lane"] != "local":
        return
    subprocess.run(["ollama", "stop", model["local"]], capture_output=True, text=True, timeout=120)


def run_cell(cell: Cell, args: argparse.Namespace, status: Status) -> str:
    """Run one cell to its end (or a mark that needs a look): ``done``, ``capped`` or ``failed``."""

    root = Path(args.root)
    folder = root / cell.name
    folder.mkdir(parents=True, exist_ok=True)
    entry = status.cell(cell)
    if progress(folder)["finished"]:
        entry["state"] = "done"
        status.save()
        return "done"
    env = {**os.environ, "ROW_BOT_DATA_DIR": str(folder / "data"), "ROW_BOT_LEARNING_PREVIEW": "1",
           "PYTHONIOENCODING": "utf-8"}
    crashes_without_step = 0
    waits = 0
    while True:
        ensure_model(cell, status, args.modelfile_4b)
        before = progress(folder)["steps"]
        entry["state"] = "running"
        entry["starts"] += 1
        entry.setdefault("first_start", time.strftime("%Y-%m-%d %H:%M:%S"))
        status.event(f"{cell.name}: start {entry['starts']} (day {progress(folder)['days']} done)")
        with (folder / "log.txt").open("a", encoding="utf-8") as log:
            log.write(f"\n--- grid start {entry['starts']} {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            log.flush()
            code = subprocess.call(command(cell, root, args.eval_profile, args.gpu_booking, args.no_new_day_from),
                                   cwd=str(CHECKOUT),
                                   env=env, stdout=log, stderr=subprocess.STDOUT)
        unload(cell)
        now = progress(folder)
        if code == 0 and now["finished"]:
            entry["state"] = "done"
            entry["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            status.event(f"{cell.name}: done ({now['days']} days, {now['tokens']:,} tokens)")
            return "done"
        if code == EXIT_TOKEN_CAP:
            entry["state"] = "capped"
            status.event(f"{cell.name}: token cap reached ({now['tokens']:,}); left for a decision")
            return "capped"
        if code == EXIT_UNREACHABLE:
            entry["state"] = "failed"
            status.event(f"{cell.name}: every request of day 1 failed before reaching the model; left for a look")
            return "failed"
        if code == EXIT_LIMIT:
            wait = LIMIT_WAITS[min(waits, len(LIMIT_WAITS) - 1)]
            waits += 1
            entry["limit_waits"] += 1
            entry["state"] = "waiting (usage or rate limit)"
            status.event(f"{cell.name}: usage or rate-limit error; waiting {wait // 60} min, then resuming")
            time.sleep(wait)
            continue
        waits = 0
        entry["crashes"] += 1
        crashes_without_step = 0 if now["steps"] > before else crashes_without_step + 1
        status.event(f"{cell.name}: exit {code} at day {now['days']} (crash {entry['crashes']}; "
                     f"{crashes_without_step} without a new step)")
        if crashes_without_step >= 6:
            entry["state"] = "failed"
            status.event(f"{cell.name}: failed six times without a new step; left for a look")
            return "failed"
        if crashes_without_step >= 3:
            entry["state"] = "paused after crashes"
            status.save()
            time.sleep(CRASH_PAUSE)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 3's grid of stale-skills runs, one queue")
    parser.add_argument("--queue", choices=("main", "local", "confirm"), required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--eval-profile", required=True)
    parser.add_argument("--gpu-booking", default="")
    parser.add_argument("--modelfile-4b", default="", help="the 4B's Ollama Modelfile (the precedence study's "
                        "GGUF), needed only while the 4B isn't registered in Ollama")
    parser.add_argument("--rest-minutes", type=float, default=0.0, help="a pause between cells (spreads the use)")
    parser.add_argument("--only", default="", help="comma-separated cell names (default: the whole queue)")
    parser.add_argument("--name", default="", help="this supervisor's status name (several may share a queue: "
                        "each cell is locked by the one running it)")
    parser.add_argument("--no-new-day-from", default="", help="HH:MM for every local cell instead of its model's "
                        "cut-off (05:00: no night pause, only when the person's Row-Bot is closed for the night)")
    args = parser.parse_args(argv)
    if args.queue in ("main", "confirm"):
        args.gpu_booking = ""  # see the module's docstring
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    name = args.name if args.name.startswith(args.queue) else f"{args.queue}{args.name}"
    status = Status(root, args.queue, name)
    wanted = {item.strip() for item in args.only.split(",") if item.strip()}
    todo = [cell for cell in cells(args.queue) if not wanted or cell.name in wanted]
    status.event(f"queue {args.queue} ({name}): {len(todo)} cells (pid {os.getpid()})")
    final = ("done", "capped", "failed")
    while True:
        ran = waiting = 0
        for cell in todo:
            folder = root / cell.name
            if status.data["cells"].get(cell.name, {}).get("state") in final or progress(folder)["finished"]:
                continue
            if not lock(folder):
                waiting += 1  # another supervisor (or a run left from before) holds it
                continue
            try:
                outcome = run_cell(cell, args, status)
            finally:
                unlock(folder)
            ran += 1
            status.save()
            if outcome == "done" and args.rest_minutes:
                time.sleep(args.rest_minutes * 60)
        if not ran and not waiting:
            break
        if not ran:
            time.sleep(300)
    status.event(f"queue {args.queue} ({name}): finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
