"""The fake trainer (L2a): a deterministic training worker that speaks the real protocol.

Run as ``python -I learning_trainer.py <protocol.py> <options JSON>`` by the training
worker host, exactly like the real engine's entry point: it gets its job spec on stdin,
reads only its dataset lease, writes fake checkpoints (with real manifests) into the
spec's output folder and reports progress. Standard library only; no GPU, no network,
no Row-Bot import.

Options (all optional):

- ``pace``: ``"driven"`` (default) takes a step only when the host sends ``advance``,
  so tests move it step by step without sleeping; ``"free"`` runs through on its own,
  ``delay`` seconds a step (0: as fast as it can).
- ``seconds_per_step`` (3.6) and ``loss_start`` (2.0): what it reports (a fake clock).
- ``crash_at_step``: dies in the middle of that step (no message, exit 3).
- ``oom_at_step``: reports ``out_of_gpu_memory`` at that step and exits.
- ``refuse_resume``: reports ``cannot_resume`` when asked to continue from a checkpoint.
- ``spawn_child``: starts a child process that waits forever (like Triton's ``ptxas``)
  and writes its pid to ``child.pid`` in its working folder, so tests can see that
  stopping the worker ends it too.
- ``checkpoint_bytes`` (2048): the size of each checkpoint's fake weights.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


def _load_protocol(path: str):
    spec = importlib.util.spec_from_file_location("_row_bot_training_protocol", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _weights(step: int, size: int) -> bytes:
    out = bytearray()
    seed = hashlib.sha256(f"fake-weights:{step}".encode()).digest()
    while len(out) < size:
        seed = hashlib.sha256(seed).digest()
        out.extend(seed)
    return bytes(out[:size])


def main() -> None:
    protocol = _load_protocol(sys.argv[1])
    options = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    # Private pipes: anything printed by accident goes to stderr, never into the protocol.
    reader = io.BufferedReader(os.fdopen(os.dup(0), "rb", buffering=0))
    writer = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    write_lock = threading.Lock()

    def send(kind: str, **fields) -> None:
        raw = protocol.encode(protocol.message(kind, **fields))
        with write_lock:
            writer.write(raw)
            writer.flush()

    def fail(reason: str, step) -> None:
        send("error", reason=reason, step=step)
        os._exit(1)

    send("hello", worker="fake")
    first = protocol.check_message(protocol.decode(reader.readline(protocol.MAX_FRAME + 1)))
    if first["type"] != "start":
        os._exit(2)
    spec = first["spec"]
    total = spec["steps_total"]
    every = spec["checkpoint_every"]
    dataset = Path(spec["lease"]["dataset"])
    output = Path(spec["output"])

    # The lease is the only data it reads.
    try:
        examples = sum(1 for _ in (dataset / "examples.jsonl").open(encoding="utf-8"))
    except OSError:
        fail("data_unreadable", None)
    step = 0
    if spec["resume_from"]:
        if options.get("refuse_resume"):
            fail("cannot_resume", None)
        try:
            manifest = protocol.read_checkpoint_manifest(output / spec["resume_from"])
            json.loads((output / spec["resume_from"] / "state.json").read_text(encoding="utf-8"))
        except (protocol.ProtocolError, OSError, ValueError):
            fail("cannot_resume", None)
        step = manifest["step"]

    if options.get("spawn_child"):
        child = subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", "import threading; threading.Event().wait()"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        Path("child.pid").write_text(str(child.pid), encoding="utf-8")

    credits = 0
    paused = False
    changed = threading.Condition()

    def control() -> None:
        nonlocal credits, paused
        while True:
            raw = reader.readline(protocol.MAX_FRAME + 1)
            if not raw:
                # The host is gone (Row-Bot crashed): nothing is left running. On Windows the
                # job object already ends the tree; elsewhere the worker ends its own group.
                if os.name != "nt":
                    os.killpg(os.getpgid(0), signal.SIGKILL)
                os._exit(0)
            message = protocol.check_message(protocol.decode(raw))
            with changed:
                if message["type"] == "advance":
                    credits += message["steps"]
                elif message["type"] == "pause":
                    paused = True
                changed.notify_all()

    threading.Thread(target=control, daemon=True).start()
    send("started", step=step, steps_total=total)
    driven = options.get("pace", "driven") == "driven"
    delay = float(options.get("delay", 0))
    seconds_per_step = float(options.get("seconds_per_step", 3.6))
    loss_start = float(options.get("loss_start", 2.0))
    size = int(options.get("checkpoint_bytes", 2048))

    while step < total:
        if driven:
            with changed:
                while credits <= 0:
                    changed.wait()
                credits -= 1
        elif delay:
            time.sleep(delay)
        current = step + 1
        if options.get("crash_at_step") == current:
            os._exit(3)
        if options.get("oom_at_step") == current:
            fail("out_of_gpu_memory", step)
        step = current
        loss = round(loss_start * (0.97 ** step), 6)
        send("progress", step=step, steps_total=total, loss=loss, seconds_per_step=seconds_per_step)
        with changed:
            pause_now = paused
        if step % every == 0 or step == total or pause_now:
            folder = output / protocol.checkpoint_name(step)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "state.json").write_text(json.dumps({"step": step, "loss": loss, "examples": examples}),
                                               encoding="utf-8")
            (folder / "weights.bin").write_bytes(_weights(step, size))
            protocol.write_checkpoint_manifest(folder, step)
            send("checkpoint", step=step, name=folder.name)
        if pause_now and step < total:
            send("paused", step=step)
            os._exit(0)

    adapter = output / "adapter.bin"
    adapter.write_bytes(_weights(-1, size))
    send("artifacts", files=[{"name": adapter.name, "bytes": adapter.stat().st_size,
                              "sha256": hashlib.sha256(adapter.read_bytes()).hexdigest()}])
    send("finished", step=step)
    os._exit(0)


if __name__ == "__main__":
    main()
