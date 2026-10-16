"""Host ↔ training-worker messages (L2a, E15), versioned and validated.

Standard library only, with no Row-Bot imports: the worker runs under the training
engine's own interpreter (L2b) or, for tests, the app's Python running the fake trainer,
and loads this file by its path. Both sides speak it.

**Transport.** One JSON object per line (UTF-8, ``\\n``-terminated, at most
:data:`MAX_FRAME` bytes) on the worker's private stdin (host → worker) and stdout
(worker → host). Every message carries ``"v": 1`` (:data:`PROTOCOL_VERSION`) and a
``"type"``; each type has exactly its own keys. Anything else is rejected: another
version, an unknown type, a missing, extra or duplicate key, a wrong type, a value out
of range, NaN or infinity.

**Host → worker**

- ``start`` ``{spec}``: the job spec (:func:`validate_spec`). Always the first message.
- ``pause``: save at the next optimizer step, report ``checkpoint`` and ``paused``, exit.
- ``advance`` ``{steps}``: lets a fake-clocked worker take that many more steps (tests
  and the browser fixture drive the fake trainer step by step). Real engines ignore it.

**Worker → host**, in this order (:class:`Session` checks it):

- ``hello`` ``{worker}``: first, before anything else.
- ``started`` ``{step, steps_total}``: the step it starts after (0, or the resumed
  checkpoint's step).
- ``progress`` ``{step, steps_total, loss, seconds_per_step}``: after each step.
- ``checkpoint`` ``{step, name}``: a checkpoint folder ``checkpoint-<step>`` was written
  with its manifest (:func:`write_checkpoint_manifest`) last.
- ``artifacts`` ``{files}``: what the finished run produced (names, sizes, hashes).
- ``paused`` ``{step}`` (only after ``pause``), ``finished`` ``{step}`` (at the last
  step) or ``error`` ``{reason, step}``: the last message; the worker then exits.

``error.reason`` is a plain reason class (:data:`ERROR_REASONS`), never text from the
engine: the person sees words chosen by the app.

**The job spec** carries the job's id, version and step plan, the recipe's settings, a
**scoped dataset lease** (a path to the job's own prepared data inside its run folder,
never the chat databases) and the folder its checkpoints go to. The worker reads only
these two folders.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
MAX_FRAME = 64 * 1024
MAX_STEPS = 10_000_000
CHECKPOINT_MANIFEST = "checkpoint.json"
CHECKPOINT_FORMAT = "row-bot-checkpoint"
MAX_CHECKPOINT_FILES = 64
MAX_MANIFEST_BYTES = 64 * 1024

# Plain reason classes a worker may give for an error; the app words each one.
ERROR_REASONS = frozenset({
    "out_of_gpu_memory",  # the step didn't fit the graphics card
    "out_of_disk",  # a checkpoint couldn't be written
    "cannot_resume",  # the checkpoint it was asked to continue from can't be used
    "data_unreadable",  # the job's prepared data can't be read
    "engine_error",  # anything else inside the engine
})

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_CHECKPOINT = re.compile(r"checkpoint-(\d{1,8})\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ProtocolError(ValueError):
    """A message, spec or checkpoint that isn't valid; ``code`` is stable."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


# ── Values ──────────────────────────────────────────────────────────────────


def _int(value: Any, where: str, *, low: int = 0, high: int = MAX_STEPS) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ProtocolError("invalid_message", where)
    return value


def _number(value: Any, where: str, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise ProtocolError("invalid_message", where)
    return float(value)


def _text(value: Any, where: str, pattern: re.Pattern[str]) -> str:
    if type(value) is not str or not pattern.fullmatch(value):
        raise ProtocolError("invalid_message", where)
    return value


def _keys(message: dict[str, Any], expected: set[str], where: str) -> None:
    if set(message) != expected:
        raise ProtocolError("invalid_message", f"{where} keys")


def checkpoint_name(step: int) -> str:
    return f"checkpoint-{int(step)}"


def checkpoint_step(name: str) -> int | None:
    match = _CHECKPOINT.fullmatch(str(name or ""))
    return int(match.group(1)) if match else None


# ── Frames ──────────────────────────────────────────────────────────────────


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ProtocolError("invalid_frame", "duplicate key")
        value[key] = item
    return value


def _reject_constant(name: str) -> Any:
    raise ProtocolError("invalid_frame", f"{name} is not a number")


def decode(raw: bytes) -> dict[str, Any]:
    """One frame → a message dict (its shape is checked by :func:`check_message`)."""

    if type(raw) is not bytes or not raw.endswith(b"\n") or len(raw) > MAX_FRAME:
        raise ProtocolError("invalid_frame", "size or ending")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_reject_constant)
    except ProtocolError:
        raise
    except (ValueError, RecursionError) as error:
        raise ProtocolError("invalid_frame", "not JSON") from error
    if type(value) is not dict:
        raise ProtocolError("invalid_frame", "not an object")
    if value.get("v") != PROTOCOL_VERSION or type(value.get("v")) is not int:
        raise ProtocolError("unsupported_version", str(value.get("v"))[:16])
    return value


def encode(message: dict[str, Any]) -> bytes:
    """A message → one frame. The message is checked first."""

    check_message(message)
    raw = json.dumps(message, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
    if len(raw) > MAX_FRAME:
        raise ProtocolError("invalid_frame", "too large")
    return raw


# ── Messages ────────────────────────────────────────────────────────────────

HOST_TYPES = frozenset({"start", "pause", "advance"})
WORKER_TYPES = frozenset({"hello", "started", "progress", "checkpoint", "artifacts", "paused", "finished", "error"})


def check_message(message: Any) -> dict[str, Any]:
    """Validate one message's shape (either direction); returns it."""

    if type(message) is not dict or message.get("v") != PROTOCOL_VERSION or type(message.get("v")) is not int:
        raise ProtocolError("unsupported_version", "v")
    kind = message.get("type")
    if kind == "start":
        _keys(message, {"v", "type", "spec"}, kind)
        if type(message["spec"]) is not dict:
            raise ProtocolError("invalid_message", "spec")
        check_spec_shape(message["spec"])
    elif kind == "pause":
        _keys(message, {"v", "type"}, kind)
    elif kind == "advance":
        _keys(message, {"v", "type", "steps"}, kind)
        _int(message["steps"], "steps", low=1)
    elif kind == "hello":
        _keys(message, {"v", "type", "worker"}, kind)
        _text(message["worker"], "worker", _NAME)
    elif kind == "started":
        _keys(message, {"v", "type", "step", "steps_total"}, kind)
        _int(message["step"], "step")
        _int(message["steps_total"], "steps_total", low=1)
    elif kind == "progress":
        _keys(message, {"v", "type", "step", "steps_total", "loss", "seconds_per_step"}, kind)
        _int(message["step"], "step", low=1)
        _int(message["steps_total"], "steps_total", low=1)
        _number(message["loss"], "loss", optional=True)
        _number(message["seconds_per_step"], "seconds_per_step", optional=True)
    elif kind == "checkpoint":
        _keys(message, {"v", "type", "step", "name"}, kind)
        _int(message["step"], "step", low=1)
        if checkpoint_step(message["name"]) != message["step"]:
            raise ProtocolError("invalid_message", "checkpoint name")
    elif kind == "artifacts":
        _keys(message, {"v", "type", "files"}, kind)
        files = message["files"]
        if type(files) is not list or len(files) > MAX_CHECKPOINT_FILES:
            raise ProtocolError("invalid_message", "files")
        for item in files:
            if type(item) is not dict:
                raise ProtocolError("invalid_message", "file")
            _keys(item, {"name", "bytes", "sha256"}, "file")
            _text(item["name"], "file name", _NAME)
            _int(item["bytes"], "file bytes", high=1 << 50)
            _text(item["sha256"], "file sha256", _SHA256)
    elif kind in ("paused", "finished"):
        _keys(message, {"v", "type", "step"}, kind)
        _int(message["step"], "step")
    elif kind == "error":
        _keys(message, {"v", "type", "reason", "step"}, kind)
        if message["reason"] not in ERROR_REASONS:
            raise ProtocolError("invalid_message", "reason")
        if message["step"] is not None:
            _int(message["step"], "step")
    else:
        raise ProtocolError("unknown_type", str(kind)[:32])
    return message


def start(spec: dict[str, Any]) -> dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "type": "start", "spec": spec}


def pause() -> dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "type": "pause"}


def advance(steps: int) -> dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "type": "advance", "steps": int(steps)}


def message(kind: str, **fields: Any) -> dict[str, Any]:
    """A worker → host message (the worker builds its messages with this)."""

    return check_message({"v": PROTOCOL_VERSION, "type": kind, **fields})


# ── The job spec and its scoped lease ───────────────────────────────────────

_SETTING_KEY = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")


def check_spec_shape(spec: dict[str, Any]) -> dict[str, Any]:
    """The spec's keys and value types (paths are checked by :func:`check_spec_paths`)."""

    _keys(spec, {"job_id", "version", "steps_total", "checkpoint_every", "resume_from", "lease", "output",
                 "recipe"}, "spec")
    _text(spec["job_id"], "job_id", _ID)
    _int(spec["version"], "version", low=1, high=1_000_000)
    total = _int(spec["steps_total"], "steps_total", low=1)
    _int(spec["checkpoint_every"], "checkpoint_every", low=1, high=total)
    resume = spec["resume_from"]
    if resume is not None and (checkpoint_step(resume) is None or checkpoint_step(resume) > total):
        raise ProtocolError("invalid_spec", "resume_from")
    lease = spec["lease"]
    if type(lease) is not dict:
        raise ProtocolError("invalid_spec", "lease")
    _keys(lease, {"dataset", "examples"}, "lease")
    _int(lease["examples"], "examples", high=100_000_000)
    for where, value in (("lease.dataset", lease["dataset"]), ("output", spec["output"])):
        if type(value) is not str or not 0 < len(value) <= 4096 or "\0" in value or not os.path.isabs(value):
            raise ProtocolError("invalid_spec", where)
    recipe = spec["recipe"]
    if type(recipe) is not dict:
        raise ProtocolError("invalid_spec", "recipe")
    _keys(recipe, {"name", "settings"}, "recipe")
    _text(recipe["name"], "recipe name", _NAME)
    settings = recipe["settings"]
    if type(settings) is not dict or len(settings) > 32:
        raise ProtocolError("invalid_spec", "recipe settings")
    for key, value in settings.items():
        if not _SETTING_KEY.fullmatch(key):
            raise ProtocolError("invalid_spec", "recipe setting name")
        if type(value) is str:
            if len(value) > 128:
                raise ProtocolError("invalid_spec", "recipe setting")
        elif type(value) in (int, bool):
            pass
        elif type(value) is float:
            if not math.isfinite(value):
                raise ProtocolError("invalid_spec", "recipe setting")
        else:
            raise ProtocolError("invalid_spec", "recipe setting")
    return spec


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def check_spec_paths(spec: dict[str, Any], run_dir: Path) -> None:
    """The lease and the output are the job's own folders: ``<run>/data`` and ``<run>/checkpoints``.

    Resolved through any link, so a lease can never name the chat databases, memory,
    settings or anything else in the profile.
    """

    run = Path(run_dir).resolve()
    dataset = Path(spec["lease"]["dataset"]).resolve()
    output = Path(spec["output"]).resolve()
    if dataset != run / "data" or not _inside(dataset, run):
        raise ProtocolError("lease_outside_job", "lease.dataset")
    if output != run / "checkpoints" or not _inside(output, run):
        raise ProtocolError("lease_outside_job", "output")


def validate_spec(spec: Any, run_dir: Path) -> dict[str, Any]:
    if type(spec) is not dict:
        raise ProtocolError("invalid_spec", "spec")
    check_spec_shape(spec)
    check_spec_paths(spec, run_dir)
    return spec


# ── Checkpoint manifests ────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_checkpoint_manifest(folder: Path, step: int) -> dict[str, Any]:
    """Describe a written checkpoint folder (every file's size and hash), written last.

    A checkpoint without its manifest, or whose files don't match it, is never used.
    """

    folder = Path(folder)
    files = []
    for path in sorted(folder.iterdir()):
        if path.name == CHECKPOINT_MANIFEST or path.name.endswith(".tmp"):
            continue
        if not path.is_file() or path.is_symlink() or not _NAME.fullmatch(path.name):
            raise ProtocolError("invalid_checkpoint", "only plain files")
        files.append({"name": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)})
    if len(files) > MAX_CHECKPOINT_FILES:
        raise ProtocolError("invalid_checkpoint", "too many files")
    manifest = {"format": CHECKPOINT_FORMAT, "version": 1, "step": int(step), "files": files}
    temporary = folder / (CHECKPOINT_MANIFEST + ".tmp")
    temporary.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    os.replace(temporary, folder / CHECKPOINT_MANIFEST)
    return manifest


def read_checkpoint_manifest(folder: Path) -> dict[str, Any]:
    """A checkpoint folder's manifest, checked against the files. Raises when it can't be used."""

    folder = Path(folder)
    step = checkpoint_step(folder.name)
    if step is None or folder.is_symlink() or not folder.is_dir():
        raise ProtocolError("invalid_checkpoint", "folder")
    path = folder / CHECKPOINT_MANIFEST
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ProtocolError("invalid_checkpoint", "manifest size")
        manifest = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_object,
                              parse_constant=_reject_constant)
    except ProtocolError:
        raise
    except (OSError, ValueError, RecursionError) as error:
        raise ProtocolError("invalid_checkpoint", "manifest") from error
    if (type(manifest) is not dict or set(manifest) != {"format", "version", "step", "files"}
            or manifest["format"] != CHECKPOINT_FORMAT or manifest["version"] != 1 or manifest["step"] != step
            or type(manifest["files"]) is not list or not 0 < len(manifest["files"]) <= MAX_CHECKPOINT_FILES):
        raise ProtocolError("invalid_checkpoint", "manifest shape")
    for item in manifest["files"]:
        if type(item) is not dict or set(item) != {"name", "bytes", "sha256"}:
            raise ProtocolError("invalid_checkpoint", "file entry")
        name = item["name"]
        if type(name) is not str or not _NAME.fullmatch(name) or name == CHECKPOINT_MANIFEST:
            raise ProtocolError("invalid_checkpoint", "file name")
        file = folder / name
        try:
            if file.is_symlink() or not file.is_file() or file.stat().st_size != item["bytes"]:
                raise ProtocolError("invalid_checkpoint", "file size")
            if _sha256(file) != item["sha256"]:
                raise ProtocolError("invalid_checkpoint", "file hash")
        except OSError as error:
            raise ProtocolError("invalid_checkpoint", "file") from error
    return manifest


# ── The order of a worker's messages ────────────────────────────────────────


class Session:
    """What the host accepts from one worker, in order.

    ``hello`` first; ``started`` once, at the resumed step; then steps that only move
    forward (never past the plan); checkpoints at or behind the current step and
    after the previous one; ``paused`` only after the host asked, ``finished`` only at
    the last step; and nothing after the last message.
    """

    def __init__(self, spec: dict[str, Any]) -> None:
        self.steps_total = int(spec["steps_total"])
        self.resume_step = checkpoint_step(spec["resume_from"]) if spec["resume_from"] else 0
        self.phase = "hello"
        self.step = 0
        self.checkpoint = 0
        self.pause_requested = False
        self.worker = ""

    @property
    def ended(self) -> bool:
        return self.phase == "ended"

    def request_pause(self) -> None:
        self.pause_requested = True

    def accept(self, message: Any) -> dict[str, Any]:
        check_message(message)
        kind = message["type"]
        if kind not in WORKER_TYPES:
            raise ProtocolError("out_of_order", f"{kind} from a worker")
        if self.phase == "ended":
            raise ProtocolError("out_of_order", f"{kind} after the last message")
        if kind == "hello":
            if self.phase != "hello":
                raise ProtocolError("out_of_order", "a second hello")
            self.phase = "spec"
            self.worker = message["worker"]
            return message
        if self.phase == "hello":
            raise ProtocolError("out_of_order", f"{kind} before hello")
        if kind == "error":
            self.phase = "ended"
            return message
        if kind == "started":
            if self.phase != "spec":
                raise ProtocolError("out_of_order", "a second start")
            if message["steps_total"] != self.steps_total or message["step"] != self.resume_step:
                raise ProtocolError("out_of_order", "started at another step")
            self.phase = "running"
            self.step = self.checkpoint = message["step"]
            return message
        if self.phase != "running":
            raise ProtocolError("out_of_order", f"{kind} before started")
        if kind == "progress":
            if message["steps_total"] != self.steps_total or not self.step < message["step"] <= self.steps_total:
                raise ProtocolError("out_of_order", "a step that doesn't move forward")
            self.step = message["step"]
        elif kind == "checkpoint":
            if not self.checkpoint < message["step"] <= self.step:
                raise ProtocolError("out_of_order", "a checkpoint out of order")
            self.checkpoint = message["step"]
        elif kind == "artifacts":
            if self.step != self.steps_total:
                raise ProtocolError("out_of_order", "artifacts before the last step")
        elif kind == "paused":
            if not self.pause_requested or message["step"] != self.step or self.checkpoint != self.step:
                raise ProtocolError("out_of_order", "paused without a pause or a checkpoint")
            self.phase = "ended"
        elif kind == "finished":
            if message["step"] != self.steps_total or self.step != self.steps_total:
                raise ProtocolError("out_of_order", "finished before the last step")
            self.phase = "ended"
        return message


__all__ = [
    "CHECKPOINT_MANIFEST",
    "ERROR_REASONS",
    "MAX_FRAME",
    "PROTOCOL_VERSION",
    "ProtocolError",
    "Session",
    "advance",
    "check_message",
    "check_spec_paths",
    "check_spec_shape",
    "checkpoint_name",
    "checkpoint_step",
    "decode",
    "encode",
    "message",
    "pause",
    "read_checkpoint_manifest",
    "start",
    "validate_spec",
    "write_checkpoint_manifest",
]
