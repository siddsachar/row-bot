"""Row-Bot Learning: the paper's report, run by the participant on their own computer (P1, O83, O109).

The paper's real-data numbers come only from this script (pre-registration §3.4, §7, §10). It writes one
JSON file of counts, rates, intervals and p-values; per-problem results never leave Row-Bot's evaluation
module, and nothing from conversations, requests, replies, tasks, lessons, memories or tool arguments, no
id, path, file name, contact, account or app name ever appears in what it prints or writes (a strict
schema: fixed keys, numbers, dates at day resolution, versions, build commits and hashes; anything else
stops the run).

Modes (PowerShell, from the Row-Bot checkout the Learning chat names, after ``uv sync``)::

    uv run python scripts/learning_paper_report.py --health --profile $HOME\\.row-bot --scratch <folder> --out <folder>
    uv run python scripts/learning_paper_report.py --freeze --as-of 2026-11-02 --profile ... --scratch ... --out ...
    uv run python scripts/learning_paper_report.py --thinking --profile ... --scratch ... --out ...

- ``--health`` (any time between freezes, §10): counts and errors only, never a pass rate or a comparison.
- ``--freeze --as-of <date>`` (§7): the pre-registered freeze evaluation on every hidden problem up to the
  end of that day: H1 (the newest personal-model version that isn't a pilot vs its untouched starting
  model), H2 (or why it can't be tested yet), Holm across {H1, H2}, the minimum size, §7's sensitivity
  analyses. Needs the graphics card and Ollama; about 15-60 minutes.
- ``--thinking``: O97's recheck, ``scripts/learning_shape_ablation.py --thinking both`` on the copy, its
  results reduced to the same kind of aggregates. About 3 hours of the graphics card.
- ``--freeze ... --hugging-face-27b`` (amendment A10, exploratory, opt-in): the build's certified Hugging Face
  recipe for Qwen3.8 27B trains on exactly the rows the newest local version was trained on (rebuilt from the
  copy and checked against that version's own record of them, SHA-256), in the participant's own Hugging Face
  account, after they sign in there and type ``upload`` to agree to the upload the script shows them first
  (how many rows, how big, where, the price). The rows go to a private dataset there and are deleted, with the
  job's files, once the model is downloaded and checked. The version runs on this computer and is measured
  like the other arms (the same hidden problems, checker, budget and repeats), reported apart under
  ``exploratory_27b``, outside Holm. If it can't run, or the participant says no, it is reported as not run.

Every mode:

1. prints the SHA-256 of this script and of the product code it calls (log them in the Preview log);
2. refuses while Row-Bot runs on the profile (its launcher lock; for the everyday profile also the
   installed app), never opens the profile for writing, copies what the mode reads to a new folder in
   ``--scratch``, checks the copy (every file, the same bytes), and runs only on the copy;
3. prints exactly what it will write and asks (``--yes`` skips the question), then writes one JSON file to
   ``--out``;
4. deletes the copy (``--keep-copy`` keeps it) and says so.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FORMAT = "row_bot.learning.paper_report"
SCHEMA_VERSION = 1
COPY_PREFIX = "row-bot-paper-copy-"
#: The pre-registered confirmatory family of paper v1 (§7) and the decision rules.
FAMILY = ("H1", "H2")
# The confirmatory family of each paper version (§7): {H1, H2} in v1, {H1, H2, H3} in v2.
FAMILIES = {"v1": FAMILY, "v2": ("H1", "H2", "H3")}
ALPHA = 0.05
MARGIN = 0.10
#: What the freeze may spend on cloud models (the main model's reference, H2's arms), at most.
CLOUD_CAP = 5_000_000
#: A10: the exploratory arm's model (the build's certified Hugging Face recipe for it) and the word the
#: participant types to agree to the upload.
EXPLORATORY_MODEL = "qwen3.8-27b"
UPLOAD_WORD = "upload"
#: A10's three local arms run model by model (``evaluation.freeze_check(order=...)``); the results are the same.
EXPLORATORY_ORDER = "model"
#: How often the run on Hugging Face is read while the confirmatory evaluation and then the wait go on.
POLL_SECONDS = 60.0
#: The product code whose SHA-256 is printed before every run (with this script).
CODE_DIRS = ("src/row_bot/learning",)
CODE_FILES = ("scripts/learning_shape_ablation.py",)
#: What ``--health`` reads, so it copies only these (paths relative to the profile, POSIX style).
HEALTH_FILES = re.compile(r"^(learning\.db(-wal|-shm)?|usage\.db(-wal|-shm)?|learning_config\.json|providers\.json"
                          r"|learning/.*|learning_models/.*\.json)$")
INSTALL_DIR = ("Programs", "Row-Bot")
#: A checkout's own ignored scratch folders (where test runs keep their temporary folders).
IGNORED_IN_CHECKOUT = (".tmp", ".testtmp")


class Refused(SystemExit):
    """The run stops before touching anything, with a message that names no path."""

    def __init__(self, message: str) -> None:
        super().__init__(2)
        self.message = message


def say(text: str = "") -> None:
    print(text, flush=True)


# ── 1. The code's own hash ──────────────────────────────────────────────────


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def code_hashes(root: Path = ROOT) -> dict[str, Any]:
    """This script's SHA-256 and one SHA-256 over the product files it calls (each file's relative path
    and SHA-256, sorted), the build commit and whether those files differ from it."""

    files: list[Path] = []
    for folder in CODE_DIRS:
        files += [path for path in (root / folder).rglob("*.py") if "__pycache__" not in path.parts]
    files += [root / name for name in CODE_FILES]
    lines = sorted(f"{path.relative_to(root).as_posix()} {_sha(path)}" for path in files if path.is_file())
    product = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    build, clean = None, False
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        found = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=20)
        if found.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", found.stdout.strip()):
            build = found.stdout.strip()
            changed = subprocess.run(["git", "status", "--porcelain", "--", "scripts", *CODE_DIRS], cwd=root,
                                     capture_output=True, text=True, timeout=20)
            clean = changed.returncode == 0 and not changed.stdout.strip()
    return {"script_sha256": _sha(Path(__file__).resolve()), "code_sha256": product, "code_files": len(lines),
            "build": build, "build_clean": clean}


# ── 2. Safety: Row-Bot closed, a checked copy, nothing written to the profile ──


def everyday_profile() -> Path:
    return (Path.home() / ".row-bot").resolve()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def profile_in_use(profile: Path) -> bool:
    """Whether a Row-Bot launcher holds the profile's lock (the start script's check). The lock file is
    opened read-only, never created."""

    path = profile / "launcher.lock"
    if not path.is_file():
        return False
    try:
        handle = path.open("rb")
    except OSError:
        return True
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        handle.close()


def installed_app_running() -> bool:
    """Whether the installed Row-Bot app runs (Windows; it opens the everyday profile)."""

    if os.name != "nt" or not os.environ.get("LOCALAPPDATA"):
        return False
    import psutil

    folder = str(Path(os.environ["LOCALAPPDATA"], *INSTALL_DIR)).lower() + os.sep
    for process in psutil.process_iter(["exe"]):
        exe = str(process.info.get("exe") or "").lower()
        if exe.startswith(folder):
            return True
    return False


def check_folders(profile: Path, scratch: Path, out: Path) -> None:
    if not profile.is_dir() or not (profile / "learning.db").is_file():
        raise Refused("The --profile folder isn't a Row-Bot profile with Learning data (no learning.db).")
    for name, folder in (("--scratch", scratch), ("--out", out)):
        if _inside(folder, profile) or _inside(profile, folder):
            raise Refused(f"The {name} folder must be outside the profile (and not contain it).")
    if _inside(scratch, ROOT) and not any(_inside(scratch, ROOT / name) for name in IGNORED_IN_CHECKOUT):
        raise Refused("The --scratch folder must be outside the Row-Bot checkout (a copy there could be committed).")


def refuse_if_running(profile: Path, *, installed: Callable[[], bool] = installed_app_running) -> None:
    if profile_in_use(profile):
        raise Refused("Row-Bot is running on this profile. Quit it (right-click its icon by the clock, then Quit) "
                      "and run this again. Nothing was copied or written.")
    if profile.resolve() == everyday_profile() and installed():
        raise Refused("The installed Row-Bot app is running. Quit it and run this again. Nothing was copied or "
                      "written.")


@dataclass
class Tree:
    files: dict[str, int] = field(default_factory=dict)
    links: int = 0

    @property
    def bytes(self) -> int:
        return sum(self.files.values())


def _is_link(entry: os.DirEntry[str]) -> bool:
    if entry.is_symlink():
        return True
    return bool(getattr(os.path, "isjunction", lambda _path: False)(entry.path))


def measure(root: Path, select: Callable[[str], bool] | None = None) -> Tree:
    """Every regular file under ``root`` (relative POSIX path → size); links and junctions are counted,
    never followed or copied (a link could lead back into the profile)."""

    tree = Tree()
    pending = [root]
    while pending:
        folder = pending.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                if _is_link(entry):
                    tree.links += 1
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    relative = Path(entry.path).relative_to(root).as_posix()
                    if select is None or select(relative):
                        tree.files[relative] = entry.stat(follow_symlinks=False).st_size
    return tree


def copy_profile(profile: Path, target: Path, select: Callable[[str], bool] | None = None) -> Tree:
    """Copy the profile's files (reading only) to ``target`` and check the copy: the same files, each the
    same size, and Row-Bot still not running on the profile afterwards."""

    source = measure(profile, select)
    existing = next(folder for folder in (target, *target.parents) if folder.exists())
    if shutil.disk_usage(existing).free < source.bytes + (1 << 30):
        raise Refused("There isn't room for the copy in the --scratch folder (it needs the profile's size plus "
                      "1 GB). Nothing was copied.")
    target.mkdir(parents=True)
    for relative in sorted(source.files):
        destination = target / PurePosixPath(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with (profile / PurePosixPath(relative)).open("rb") as reading, destination.open("xb") as writing:
            shutil.copyfileobj(reading, writing, 1 << 22)
    copied = measure(target)
    if copied.files != source.files:
        raise CopyMismatch(len(copied.files), copied.bytes, len(source.files), source.bytes)
    if profile_in_use(profile):
        raise CopyMismatch(len(copied.files), copied.bytes, len(source.files), source.bytes, started=True)
    copied.links = source.links
    return copied


class CopyMismatch(Exception):
    def __init__(self, files: int, size: int, want_files: int, want_size: int, *, started: bool = False) -> None:
        self.text = ("Row-Bot started while the profile was copied" if started else
                     f"The copy doesn't match the profile: {files} files, {size} bytes copied of {want_files} "
                     f"and {want_size}")
        super().__init__(self.text)


def release_handles(target: Path) -> None:
    """Close what Row-Bot keeps open in the copy for the whole process (the conversations database that
    importing the Agent opens), so the copy can be deleted on Windows before the process ends."""

    import gc

    threads = sys.modules.get("row_bot.threads")
    if threads is not None and _inside(Path(str(getattr(threads, "DB_PATH", "") or ".")), target):
        with contextlib.suppress(Exception):
            threads.conn.close()
    gc.collect()


def remove_copy(target: Path) -> bool:
    def clear(function: Any, path: str, _error: Any) -> None:
        with contextlib.suppress(OSError):
            os.chmod(path, 0o700)
            function(path)

    for attempt in range(5):
        shutil.rmtree(target, onexc=clear) if sys.version_info >= (3, 12) else shutil.rmtree(target, onerror=clear)
        if not target.exists():
            return True
        time.sleep(1.0 + attempt)
    return not target.exists()


# ── 3. The report's schema: fixed keys, no free text ────────────────────────


class SchemaError(ValueError):
    """The report holds something the schema doesn't allow: the run writes nothing."""


Spec = Any
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH = re.compile(r"^\d{4}-\d{2}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MODEL_ORDINAL = re.compile(r"^model_\d{1,2}$")


@dataclass(frozen=True)
class Num:
    """A number: ``integer`` (not a bool), within [low, high] when given."""

    integer: bool = False
    low: float | None = None
    high: float | None = None


@dataclass(frozen=True)
class Text:
    """A string from a fixed set, or matching a fixed pattern (dates, commits, hashes)."""

    values: frozenset[str] = frozenset()
    pattern: re.Pattern[str] | None = None


@dataclass(frozen=True)
class Obj:
    fields: Mapping[str, Spec]
    optional: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Map:
    """A mapping whose keys come from a fixed set (or a fixed pattern) and whose values follow ``value``."""

    keys: Text
    value: Spec


@dataclass(frozen=True)
class Seq:
    item: Spec
    most: int = 1000


@dataclass(frozen=True)
class Maybe:
    spec: Spec


BOOL = "bool"
COUNT = Num(integer=True, low=0)
SHARE = Num(low=0, high=1)
SIGNED = Num(low=-1, high=1)
HOURS = Num(low=0)


def enum(*values: str) -> Text:
    return Text(frozenset(values))


def validate(value: Any, spec: Spec, where: str = "report") -> None:
    """Raise :class:`SchemaError` unless ``value`` is exactly what ``spec`` allows. The message names where,
    never the offending key or value (they could be anything)."""

    if isinstance(spec, Maybe):
        if value is not None:
            validate(value, spec.spec, where)
        return
    if spec == BOOL:
        if not isinstance(value, bool):
            raise SchemaError(f"{where}: not a yes/no")
        return
    if isinstance(spec, Num):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (spec.integer and not isinstance(value, int)):
            raise SchemaError(f"{where}: not a number of the right kind")
        if isinstance(value, float) and not math.isfinite(value):
            raise SchemaError(f"{where}: not a finite number")
        if (spec.low is not None and value < spec.low) or (spec.high is not None and value > spec.high):
            raise SchemaError(f"{where}: out of range")
        return
    if isinstance(spec, Text):
        if not isinstance(value, str) or not _text_ok(value, spec):
            raise SchemaError(f"{where}: text that isn't allowed")
        return
    if isinstance(spec, Obj):
        if not isinstance(value, dict):
            raise SchemaError(f"{where}: not an object")
        extra = [key for key in value if key not in spec.fields]
        if extra:
            raise SchemaError(f"{where}: {len(extra)} key(s) the schema doesn't allow")
        missing = [key for key in spec.fields if key not in value and key not in spec.optional]
        if missing:
            raise SchemaError(f"{where}: missing {', '.join(sorted(missing))}")
        for key, item in value.items():
            validate(item, spec.fields[key], f"{where}.{key}")
        return
    if isinstance(spec, Map):
        if not isinstance(value, dict):
            raise SchemaError(f"{where}: not an object")
        for key, item in value.items():
            if not isinstance(key, str) or not _text_ok(key, spec.keys):
                raise SchemaError(f"{where}: a key the schema doesn't allow")
            validate(item, spec.value, f"{where}.{key}")
        return
    if isinstance(spec, Seq):
        if not isinstance(value, list) or len(value) > spec.most:
            raise SchemaError(f"{where}: not a list of the right size")
        for index, item in enumerate(value):
            validate(item, spec.item, f"{where}[{index}]")
        return
    raise SchemaError(f"{where}: unknown schema")


def _text_ok(value: str, spec: Text) -> bool:
    return value in spec.values or bool(spec.pattern is not None and spec.pattern.fullmatch(value))


# The fixed vocabularies (each from Row-Bot's own contracts or constants, never from data).
KINDS = enum("document_lookups", "structured_transforms", "small_coding", "tool_workflows", "other")
FAMILY_KEYS = enum("document_lookups", "structured_transforms", "small_coding", "tool_workflows", "own_work")
FIDELITY = enum("exact", "redacted", "partial")
DECISION_KINDS = enum("model_call", "tool_call", "approval")
DECISION_STATUS = enum("pending", "ok", "error", "denied", "cancelled", "stopped", "timeout")
EPISODE_SOURCES = enum("my_chat", "channel_chat", "workflow", "goal")
EPISODE_STATUS = enum("running", "completed", "failed", "stopped", "cancelled")
SIGNAL_KINDS = enum("teach", "next_message_correction", "approval_denied", "stop", "retry")
PURPOSES = enum("learning_diagnosis", "learning_test", "learning_teacher", "learning_meta", "other")
REPLAY_SKIPS = enum("not_ok", "held_out", "untrusted", "partial_without_correction", "partial_needs_exact_prefix",
                    "known_bad", "no_reference", "purged", "picture_dropped", "other")
# What the one-off orphan sweep found, by kind (``store.ORPHAN_KINDS``).
ORPHAN_KINDS = enum("message", "tool_list", "model_request", "conversation", "past_chat_plan", "tool_call", "approval",
                    "test_results", "pack_task", "picture", "unfinished_write", "other")
RUN_STATUS = enum("passed", "failed", "budget", "error", "unverified", "skipped", "not_run")
CAUSES = enum("provider_error", "malformed_call", "harness", "budget", "no_tool", "tool_not_found", "bad_call",
              "file_write", "no_answer", "wrong_answer", "other")
VERSION_STATES = enum("ready", "couldnt_start", "not_ready")
SPLITS = enum("real_uncorrected", "real_corrected", "packs")
ARMS = enum("start", "personal", "main", "learning")
CONTRASTS = enum("personal vs start", "main vs start", "learning vs main")
# A10 (exploratory): the 27B trained on Hugging Face, its untouched 27B, and the local personal model.
EXPLORATORY_ARMS = enum("start27", "personal27", "personal")
EXPLORATORY_CONTRASTS = enum("personal27 vs start27", "personal27 vs personal")
EXPLORATORY_REASONS = enum("declined", "rows_not_reproducible", "rows_changed", "source_deleted", "no_version",
                           "not_signed_in", "unavailable",
                           "no_model", "over_cap", "upload_failed", "not_started", "remote_error", "no_credit",
                           "signed_out", "export_failed", "download_failed", "couldnt_start", "no_starting_model",
                           "runtime_unavailable", "stopped", "too_few_problems")
RUNTIMES = enum("row_bot", "ollama", "lmstudio", "llama_cpp", "vllm")

RUN = Obj({
    "on": Text(pattern=DAY), "build": Maybe(Text(pattern=COMMIT)), "build_clean": BOOL,
    "script_sha256": Text(pattern=SHA256), "code_sha256": Text(pattern=SHA256), "code_files": COUNT,
    "copy": Obj({"files": COUNT, "bytes": COUNT, "links_skipped": COUNT}), "minutes": HOURS,
})
VERSION_ROW = Obj({"version": COUNT, "trained_on": Maybe(Text(pattern=DAY)), "state": VERSION_STATES,
                   "pilot": BOOL, "examples": COUNT, "kept_pass": Maybe(COUNT), "passes": Maybe(COUNT),
                   "training_hours": Maybe(HOURS)})
HEALTH = Obj({
    "format": enum(FORMAT), "schema_version": Num(integer=True, low=SCHEMA_VERSION, high=SCHEMA_VERSION),
    "mode": enum("health"), "run": RUN,
    "recording": Obj({"first_day": Maybe(Text(pattern=DAY)), "last_day": Maybe(Text(pattern=DAY)), "days": COUNT,
                      "live_episodes": COUNT, "imported_episodes": COUNT, "sources": Map(EPISODE_SOURCES, COUNT)}),
    "decisions": Obj({"total": COUNT, "fidelity": Map(FIDELITY, COUNT), "kind": Map(DECISION_KINDS, COUNT),
                      "status": Map(DECISION_STATUS, COUNT)}),
    "signals": Obj({"total": COUNT, "withdrawn": COUNT, "kind": Map(SIGNAL_KINDS, COUNT)}),
    "hidden_problems": Obj({key: COUNT for key in ("vault", "pack_tasks", "uncorrected", "corrected", "exact",
                                                   "redacted", "retired", "corrections_waiting", "trusted",
                                                   "untrusted", "with_picture", "without_picture")}),
    # Pictures kept as their own blobs (P2): how many, their bytes, how many the size cap dropped, the cap.
    "pictures": Obj({"stored": COUNT, "bytes": COUNT, "dropped": COUNT, "cap_bytes": COUNT}),
    # The one-off sweep of blobs no record references (P2): whether it ran on the profile and what it removed,
    # and what a sweep would find now (counted on the copy).
    "blob_sweep": Obj({"done": BOOL, "removed_files": COUNT, "removed_bytes": COUNT,
                       "orphans": Obj({"files": COUNT, "bytes": COUNT,
                                       "kinds": Map(ORPHAN_KINDS, Obj({"files": COUNT, "bytes": COUNT}))})}),
    "corrections": Obj({"confirmed_live": COUNT, "held_out": COUNT, "counted_for_holding_out": COUNT}),
    "versions": Obj({"total": COUNT, "ready": COUNT, "couldnt_start": COUNT, "not_ready": COUNT, "pilots": COUNT,
                     "list": Seq(VERSION_ROW, 500)}),
    "learning_tokens": Map(Text(pattern=MONTH), Obj({"cloud": COUNT, "local": COUNT,
                                                     "purposes": Map(PURPOSES, Obj({"cloud": COUNT,
                                                                                     "local": COUNT}))})),
    "exclusions": Obj({"partial_decisions": COUNT, "opted_out_chats": COUNT, "pilot_versions": COUNT,
                       "not_replayable": Map(REPLAY_SKIPS, COUNT)}),
    "capture": Obj({"incomplete_episodes": COUNT, "incomplete_decisions": COUNT, "failed_calls": COUNT,
                    "episodes": Map(EPISODE_STATUS, COUNT)}),
    "lessons": Obj({"total": COUNT, "in_use": COUNT}),
    "ways_of_working": Obj({"total": COUNT, "in_use": COUNT}),
})

ANALYSIS = Obj({
    "problems": COUNT, "clusters": COUNT, "resamples": COUNT, "permutations": COUNT, "seed": COUNT,
    "difference": Maybe(SIGNED), "low": Maybe(SIGNED), "high": Maybe(SIGNED), "p_value": Maybe(SHARE),
    "exact": BOOL, "baseline": Maybe(SHARE), "arm": Maybe(SHARE), "enough": BOOL,
    "interval_verdict": enum("better", "worse", "unclear"), "not_worse": BOOL,
})
SPLIT_ANALYSIS = Obj({
    "primary": ANALYSIS, "metric_v2": ANALYSIS, "majority_vote": ANALYSIS, "unclustered": ANALYSIS, "left_pair": COUNT,
    "by_kind": Map(KINDS, ANALYSIS), "exact_only": ANALYSIS, "per_main_model": Map(Text(pattern=MODEL_ORDINAL), ANALYSIS),
    "trusted_only": ANALYSIS, "without_pictures": ANALYSIS,
}, optional=frozenset({"exact_only", "per_main_model", "trusted_only", "without_pictures"}))
ARM_SPLIT = Obj({"problems": COUNT, "counted": COUNT, "mean": Maybe(SHARE), "mean_metric_v2": Maybe(SHARE),
                 "guarded_runs": COUNT, "runs": Map(RUN_STATUS, COUNT), "causes": Map(CAUSES, COUNT)})
TEST_PART = Obj({"split": SPLITS, "problems": COUNT, "clusters": COUNT, "difference": Maybe(SIGNED),
                 "low": Maybe(SIGNED), "high": Maybe(SIGNED), "p_value": Maybe(SHARE), "p_holm": Maybe(SHARE),
                 "margin": Maybe(SIGNED), "met": Maybe(BOOL)})
HYPOTHESIS = Obj({
    "state": enum("tested", "not_tested", "not_testable"),
    "reason": Maybe(enum("too_few_problems", "no_version", "no_starting_model", "runtime_unavailable", "stopped",
                         "no_lessons_or_ways_of_working", "arms_not_built_yet", "not_run")),
    "superiority": Maybe(TEST_PART), "non_inferiority": Maybe(TEST_PART), "supported": Maybe(BOOL),
    # H2's second non-inferiority part: the packs (its first is the hidden uncorrected work).
    "non_inferiority_packs": Maybe(TEST_PART),
    "sensitivity": Maybe(Obj({"exact_only": ANALYSIS, "majority_vote": ANALYSIS, "unclustered": ANALYSIS,
                              "per_main_model": Map(Text(pattern=MODEL_ORDINAL), ANALYSIS),
                              "corrected_problems": ANALYSIS, "checker_versions": COUNT, "metric_v2": ANALYSIS,
                              "trusted_only": ANALYSIS, "without_pictures": ANALYSIS})),
    "by_kind": Maybe(Map(KINDS, ANALYSIS)),
    "counts": Maybe(Obj({"lessons_in_use": COUNT, "ways_of_working_in_use": COUNT, "corrected_problems": COUNT})),
}, optional=frozenset({"non_inferiority_packs"}))
# H3 (§5, §7): improvement yield per 1M tokens, the inherited method minus the frozen control, per generation.
YIELD = Num(low=0)
YIELD_DIFFERENCE = Num()
H3_SIDE = Obj({"accepted": COUNT, "better": COUNT, "tokens": COUNT, "yield": YIELD})
H3_GENERATION = Obj({"problems": COUNT, "clusters": COUNT, "inherited": H3_SIDE, "control": H3_SIDE,
                     "difference": YIELD_DIFFERENCE})
H3 = Obj({
    "state": enum("tested", "not_tested", "not_testable"),
    "reason": Maybe(enum("no_trials", "too_few_generations", "too_few_problems", "final_set_not_run",
                         "final_set_too_small", "stopped")),
    "trials": COUNT, "generations": Seq(H3_GENERATION, 50), "estimate": Maybe(YIELD_DIFFERENCE),
    "low": Maybe(YIELD_DIFFERENCE), "high": Maybe(YIELD_DIFFERENCE), "p_value": Maybe(SHARE), "p_holm": Maybe(SHARE),
    "met": Maybe(BOOL), "resamples": COUNT, "seed": COUNT, "minimum": COUNT,
    # The final set (§7): every generation and the frozen control at equal budget, reported descriptively.
    "final_set": Maybe(Obj({"problems": COUNT, "arm_tokens": COUNT,
                            "arms": Seq(Obj({"role": enum("incumbent", "generation", "control"), "version": COUNT,
                                             "accepted": COUNT, "better": COUNT, "tokens": COUNT,
                                             "yield": YIELD}), 20)})),
})
FREEZE = Obj({
    "format": enum(FORMAT), "schema_version": Num(integer=True, low=SCHEMA_VERSION, high=SCHEMA_VERSION),
    "mode": enum("freeze"), "run": RUN,
    "design": Obj({"as_of": Text(pattern=DAY), "paper": enum("v1", "v2"), "metric_version": COUNT,
                   "previous_metric_version": COUNT, "repeats": COUNT, "seed": COUNT,
                   "resamples": COUNT, "permutations": COUNT, "task_tokens": COUNT, "runtime": RUNTIMES,
                   "serve_context": Maybe(COUNT), "container": BOOL, "main_reference": BOOL, "stopped": BOOL,
                   "hours": HOURS, "cloud_tokens": COUNT, "local_tokens": COUNT,
                   "vault_refresh": Obj({"considered": COUNT, "added": COUNT, "retired": COUNT, "held": COUNT})}),
    "versions": Obj({"total": COUNT, "pilots": COUNT, "candidates": COUNT, "evaluated": Maybe(COUNT),
                     "trained_on": Maybe(Text(pattern=DAY)), "kept_pass": Maybe(COUNT), "passes": Maybe(COUNT)}),
    "problems": Obj({"real_uncorrected": COUNT, "real_corrected": COUNT, "packs": COUNT, "after_as_of": COUNT,
                     "main_models": COUNT, "untrusted": COUNT, "with_picture": COUNT}),
    "arms": Map(ARMS, Obj({"tokens": COUNT, "cloud_tokens": COUNT, "seconds": HOURS, "ran": COUNT, "cached": COUNT,
                           "identical": Obj({"problems": COUNT, "identical": COUNT}),
                           "splits": Map(SPLITS, ARM_SPLIT)})),
    "contrasts": Map(CONTRASTS, Map(SPLITS, SPLIT_ANALYSIS)),
    "hypotheses": Obj({"H1": HYPOTHESIS, "H2": HYPOTHESIS, "H3": H3}),
    "holm": Obj({"family": Seq(enum(*FAMILIES["v2"]), 3), "alpha": SHARE, "untested_enter_as": SHARE,
                 "adjusted": Map(enum(*FAMILIES["v2"]), Maybe(SHARE))}),
    # A10: reported apart, exploratory, outside the confirmatory family and without Holm.
    "exploratory_27b": Obj({
        "state": enum("not_requested", "not_run", "run"), "reason": Maybe(EXPLORATORY_REASONS),
        "model": enum(EXPLORATORY_MODEL), "recipe": Maybe(Text(pattern=re.compile(r"^hf-[0-9a-z]+-v\d+$"))),
        "rows": Obj({"examples": COUNT, "validation": COUNT, "matched": BOOL}),
        "upload_bytes": COUNT, "cost_usd": Maybe(HOURS), "minutes": Maybe(COUNT), "uploads_deleted": Maybe(BOOL),
        "kept_pass": Maybe(COUNT), "passes": Maybe(COUNT),
        "arms": Map(EXPLORATORY_ARMS, Obj({"tokens": COUNT, "cloud_tokens": COUNT, "seconds": HOURS, "ran": COUNT,
                                           "cached": COUNT, "identical": Obj({"problems": COUNT, "identical": COUNT}),
                                           "splits": Map(SPLITS, ARM_SPLIT)})),
        "contrasts": Map(EXPLORATORY_CONTRASTS, Map(SPLITS, SPLIT_ANALYSIS)),
    }),
}, optional=frozenset({"exploratory_27b"}))

THINKING_MODELS = enum("start", "B", "A", "A32")
THINKING_PART = Obj({"overall": ANALYSIS, "by_kind": Map(FAMILY_KEYS, ANALYSIS)})
THINKING = Obj({
    "format": enum(FORMAT), "schema_version": Num(integer=True, low=SCHEMA_VERSION, high=SCHEMA_VERSION),
    "mode": enum("thinking"), "run": RUN,
    "design": Obj({"visible_repeats": COUNT, "hidden_repeats": COUNT, "container": BOOL, "hours": HOURS,
                   "complete": BOOL}),
    "rows": Map(THINKING_MODELS, Obj({"fit": COUNT, "rows_measured": COUNT, "too_long": COUNT, "examples": COUNT})),
    "training": Map(THINKING_MODELS, Obj({"trained": BOOL, "examples": COUNT, "steps": Maybe(COUNT),
                                          "gpu_hours": Maybe(HOURS)})),
    "arms": Map(Text(pattern=re.compile(r"^(start|B|A|A32)-(on|off)$")),
                Obj({"seconds_per_task": Map(enum("visible", "hidden"), HOURS), "tokens": COUNT})),
    "thinking_on_vs_off": Map(THINKING_MODELS, Map(enum("visible", "hidden", "combined"), THINKING_PART)),
})
SCHEMAS = {"health": HEALTH, "freeze": FREEZE, "thinking": THINKING}


# ── 4. Small helpers over the copy (it is the only data folder Row-Bot sees) ──


def _day(at: float | None) -> str | None:
    if not at:
        return None
    return datetime.fromtimestamp(float(at)).strftime("%Y-%m-%d")


def _counts(values: Iterable[str], allowed: Text) -> dict[str, int]:
    """A Counter over fixed labels; anything else counts as "other" (or is dropped if "other" isn't one)."""

    found: Counter[str] = Counter()
    for value in values:
        key = value if value in allowed.values else "other"
        if key in allowed.values:
            found[key] += 1
    return dict(sorted(found.items()))


def _versions(copy: Path) -> list[dict[str, Any]]:
    """Every version folder's facts (no file name, no model name, no id)."""

    from row_bot.learning.store import LearningStore
    from row_bot.learning.training import releases

    store = LearningStore(copy)
    rows = []
    folder = releases.versions_dir(copy)
    for entry in sorted(folder.iterdir() if folder.is_dir() else (), key=lambda item: item.name):
        if not (entry.is_dir() and entry.name.startswith("v") and entry.name[1:].isdigit()):
            continue
        state = releases.read_state(entry)
        if state is None:
            continue
        try:
            dataset = json.loads((entry / releases.DATASET_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            dataset = {}
        rendering = str(((dataset or {}).get("rendering") or {}).get("name") or "")
        if state.phase == releases.READY:
            phase = "ready"
        elif releases.couldnt_start(state):
            phase = "couldnt_start"
        else:
            phase = "not_ready"
        kept = dict(state.kept_pass or {})
        hours = None
        job = store.get("job", state.job_id) if state.job_id and store.exists() else None
        if job is not None and getattr(job, "steps_done", 0) and getattr(job, "seconds_per_step", None):
            hours = round(job.steps_done * job.seconds_per_step / 3600, 3)
        rows.append({
            "version": int(state.version), "trained_on": _day(state.trained_at), "state": phase,
            # §8.4: versions trained before the rows took the serving shape are pilots.
            "pilot": not rendering.startswith("serving-"), "examples": int(state.examples or 0),
            "kept_pass": int(kept["pass"]) if isinstance(kept.get("pass"), int) else None,
            "passes": int(kept["of"]) if isinstance(kept.get("of"), int) else None, "training_hours": hours,
            # Kept apart (never written): what the freeze serves.
            "_gguf": (entry / state.gguf) if state.gguf else None, "_runtime": state.runtime,
            "_ready_once": bool(state.phase == releases.READY or state.verified_at),
            "_serve_context": int(((dataset or {}).get("rendering") or {}).get("serve_context") or 0) or None,
            "_base": str((((_read_json(entry / releases.MANIFEST_FILE).get("identity") or {}).get("base") or {})
                          .get("name") or "")),
        })
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _public(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


# ── 5. --health: counts and errors only (§10) ───────────────────────────────


def health_report(copy: Path) -> dict[str, Any]:
    """Counts and errors from the copy: never a pass rate, a comparison or anything a check decided."""

    from row_bot.learning import evaluation, holdout
    from row_bot.learning.contracts import Decision, Episode, LessonRecord, Release, Signal
    from row_bot.learning.store import LearningStore

    store = LearningStore(copy)
    episodes = [item for item in store.find("episode", limit=10_000_000) if isinstance(item, Episode)]
    decisions = [item for item in store.find("decision", limit=10_000_000) if isinstance(item, Decision)]
    signals = [item for item in store.find("signal", limit=10_000_000) if isinstance(item, Signal)]
    live = [item for item in episodes if not item.historical]
    days = sorted({_day(item.started_at) for item in live if item.started_at})
    historical = {item.id for item in episodes if item.historical}
    corrections = [item for item in signals if item.kind in holdout.CORRECTION_KINDS and item.decision_id
                   and item.withdrawn_at is None]
    by_id = {item.id: item for item in decisions}
    confirmed_live = sum(1 for item in corrections if (found := by_id.get(str(item.decision_id))) is not None
                         and found.fidelity in ("exact", "redacted") and found.episode_id not in historical)
    try:
        counted = len(json.loads(store.meta_value("held_out_corrections.counted", "[]")))
    except ValueError:
        counted = 0
    versions = _versions(copy)
    lessons = [item for item in store.find("lesson", limit=1_000_000) if isinstance(item, LessonRecord)]
    behaviours = [item for item in store.find("release", limit=1_000_000)
                  if isinstance(item, Release) and item.kind == "behaviour"]
    replay = evaluation.collect_replay(store, refresh=False, limit=10_000_000)
    pictures = store.picture_stats()
    swept = store.sweep_result() or {}
    # What a sweep would find now, counted on the copy (nothing is in progress there: no grace period).
    orphans = store.sweep_orphans(remove=False, grace_seconds=0).to_dict()
    return {
        "format": FORMAT, "schema_version": SCHEMA_VERSION, "mode": "health",
        "recording": {
            "first_day": days[0] if days else None, "last_day": days[-1] if days else None, "days": len(days),
            "live_episodes": len(live), "imported_episodes": len(episodes) - len(live),
            "sources": _counts((item.source for item in live), EPISODE_SOURCES),
        },
        "decisions": {"total": len(decisions), "fidelity": _counts((item.fidelity for item in decisions), FIDELITY),
                      "kind": _counts((item.kind for item in decisions), DECISION_KINDS),
                      "status": _counts((item.status for item in decisions), DECISION_STATUS)},
        "signals": {"total": len(signals), "withdrawn": sum(1 for item in signals if item.withdrawn_at is not None),
                    "kind": _counts((item.kind for item in signals), SIGNAL_KINDS)},
        "hidden_problems": evaluation.audit_counts(asked_by="the paper's health check", store=store),
        "corrections": {"confirmed_live": confirmed_live, "held_out": len(store.held_out_correction_ids()),
                        "counted_for_holding_out": counted},
        "versions": {
            "total": len(versions), "ready": sum(1 for row in versions if row["state"] == "ready"),
            "couldnt_start": sum(1 for row in versions if row["state"] == "couldnt_start"),
            "not_ready": sum(1 for row in versions if row["state"] == "not_ready"),
            "pilots": sum(1 for row in versions if row["pilot"]), "list": [_public(row) for row in versions],
        },
        "learning_tokens": learning_tokens(copy),
        "exclusions": {"partial_decisions": sum(1 for item in decisions if item.fidelity == "partial"),
                       "opted_out_chats": len(store.opted_out_chats()),
                       "pilot_versions": sum(1 for row in versions if row["pilot"]),
                       "not_replayable": _counts(replay.skipped.elements(), REPLAY_SKIPS)},
        "capture": {"incomplete_episodes": sum(1 for item in episodes if item.incomplete),
                    "incomplete_decisions": sum(1 for item in decisions if item.incomplete),
                    "failed_calls": sum(1 for item in decisions if item.status in ("error", "timeout")),
                    "episodes": _counts((item.status for item in episodes), EPISODE_STATUS)},
        "lessons": {"total": len(lessons), "in_use": sum(1 for item in lessons if item.status == "in_use")},
        "ways_of_working": {"total": len(behaviours),
                            "in_use": sum(1 for item in behaviours if item.status in ("in_use", "trial"))},
        "pictures": {"stored": pictures.stored, "bytes": pictures.bytes, "dropped": pictures.dropped,
                     "cap_bytes": pictures.cap_bytes},
        "blob_sweep": {"done": bool(swept), "removed_files": int(swept.get("files") or 0),
                       "removed_bytes": int(swept.get("bytes") or 0),
                       "orphans": {"files": int(orphans["files"]), "bytes": int(orphans["bytes"]),
                                   "kinds": {kind: found for kind, found in dict(orphans["kinds"]).items()
                                             if kind in ORPHAN_KINDS.values}}},
    }


def learning_tokens(copy: Path) -> dict[str, Any]:
    """Learning's own model calls in the usage ledger, per calendar month: cloud and local tokens."""

    import sqlite3

    from row_bot.learning.spending import LEARNING_PURPOSES, is_local_provider

    ledger = copy / "usage.db"
    if not ledger.is_file():
        return {}
    marks = ", ".join("?" for _ in LEARNING_PURPOSES)
    with contextlib.closing(sqlite3.connect(f"{ledger.resolve().as_uri()}?mode=ro", uri=True)) as connection:
        rows = connection.execute(
            "SELECT created_at, provider, purpose, COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)"
            f" FROM model_usage WHERE purpose IN ({marks})", LEARNING_PURPOSES).fetchall()
    months: dict[str, Any] = {}
    local_cache: dict[str, bool] = {}
    for created_at, provider, purpose, tokens in rows:
        month = datetime.fromtimestamp(float(created_at or 0)).strftime("%Y-%m")
        where = "local" if local_cache.setdefault(str(provider), is_local_provider(str(provider or ""))) else "cloud"
        entry = months.setdefault(month, {"cloud": 0, "local": 0, "purposes": {}})
        entry[where] += int(tokens or 0)
        name = purpose if purpose in PURPOSES.values else "other"
        part = entry["purposes"].setdefault(name, {"cloud": 0, "local": 0})
        part[where] += int(tokens or 0)
    return dict(sorted(months.items()))


# ── 6. --freeze: the pre-registered freeze evaluation (§7) ──────────────────


@dataclass
class Prepared:
    """The arms the freeze runs, served from the copy (model references never leave this script)."""

    refs: dict[str, str]
    serve_context: int | None
    runtime: str
    cleanup: Callable[[], None] = lambda: None
    reason: str | None = None


def serve_arms(copy: Path, version: Mapping[str, Any]) -> Prepared:
    """Serve the version and its untouched starting model from the copy's own files, under the copy's own
    names (never the profile's), the same way and in the same runtime, thinking off (§4)."""

    from row_bot.learning.training import base_model, recipes, serving, weights

    runtime = str(version.get("_runtime") or "ollama")
    context = version.get("_serve_context") or recipes.default().serve_context
    model = next((item for item in weights.MODELS.values() if item.repo == version.get("_base")), None)
    base_gguf = None
    if model is not None:
        state = base_model.read(model.id, copy)
        if state is not None and state.gguf and (base_model.folder(model.id, copy) / state.gguf).is_file():
            base_gguf = base_model.folder(model.id, copy) / state.gguf
    if base_gguf is None:
        return Prepared({}, context, runtime, reason="no_starting_model")
    number = int(version["version"])
    made: list[Callable[[], None]] = []

    def cleanup() -> None:
        for undo in reversed(made):
            with contextlib.suppress(Exception):
                undo()

    start = serving.serve(runtime, version=number, gguf=base_gguf, serve_context=int(context),
                          folder=base_model.folder(model.id, copy), root=copy, tag=base_model.tag(copy),
                          tool_turn=False)
    if start.state == "ready":
        made.append(lambda: base_model.withdraw(model.id, root=copy))
    personal = serving.serve(runtime, version=number, gguf=Path(version["_gguf"]), serve_context=int(context),
                             folder=Path(version["_gguf"]).parent, root=copy, tool_turn=False)
    if personal.state == "ready":
        made.append(lambda: serving.withdraw(runtime, number, root=copy))
    if start.state != "ready" or personal.state != "ready" or not start.model_ref or not personal.model_ref:
        cleanup()
        reasons = {start.reason, personal.reason}
        reason = ("runtime_unavailable" if reasons & {"not_running", "not_installed", "not_local"} else
                  "no_starting_model" if start.state != "ready" else "no_version")
        return Prepared({}, context, runtime, reason=reason)
    return Prepared({"start": start.model_ref, "personal": personal.model_ref}, int(context), runtime, cleanup)


@dataclass
class Hooks:
    """What a test replaces: serving the arms, the pack runner and the replay client (fakes, no GPU)."""

    prepare: Callable[[Path, Mapping[str, Any]], Prepared] = serve_arms
    runner: Callable[[Sequence[Any]], Any] | None = None
    client: Any = None
    container: Callable[[], bool] | None = None
    main_ref: Callable[[], str] | None = None
    ask: Callable[[str], str] = input
    installed: Callable[[], bool] = installed_app_running
    # The eval profile's sign-in bridge (``tests.e2e.live_eval.eval_profile.run_bridge``); a test passes a fake.
    bridge: Callable[..., dict[str, Any]] | None = None
    # A10: the Hugging Face backend on the copy (a test passes one with a fake Hub and sign-in), how the made
    # version is served for its ready check, and the wait between reads of the run.
    hugging_face: Callable[[Path], Any] | None = None
    release_server: Callable[..., Any] | None = None
    sleep: Callable[[float], None] = time.sleep
    # Which conversations still exist (``scope``'s lookup over the copy's own chats; a test passes a fake).
    lookup: Any = None
    # H3's final set: a trial arm's tester for a learning method (``changes.Tester``); a test passes a fake.
    tester: Callable[[Any], Any] | None = None


class EvalSignIn:
    """``--main-reference``: the main model signs in with the eval profile's ChatGPT sign-in (O126), never the
    profile copy's, whose refresh would rotate the participant's own tokens and sign their Row-Bot out.

    Only the eval profile's codex tokens are read (through its own bridge process); they live in this
    process's memory only, and a token the run refreshes is saved back to the eval profile at once, so its
    sign-in stays valid. Nothing of the profile copy's sign-in is used."""

    def __init__(self, profile: Path, *, bridge: Callable[..., dict[str, Any]] | None = None) -> None:
        self.profile = profile
        self.bridge = bridge
        self.current: Any = None
        self.write_backs: list[str] = []

    def _runner(self) -> dict[str, Any]:
        return {"runner": self.bridge} if self.bridge is not None else {}

    def install(self) -> None:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from tests.e2e.live_eval import eval_profile

        from row_bot import secret_store
        from row_bot.providers import codex

        bundle = eval_profile.read_bundle(self.profile, **self._runner())
        if bundle is None:
            raise _ModeFailed("the eval profile has no ChatGPT sign-in (run its sign-in script)")
        self.current = bundle
        secret_store.use_memory_backend()  # this process's secrets: memory only, the copy's never read
        original = codex.save_codex_oauth_tokens
        original(codex.CodexTokenSet(access_token=bundle.access_token, refresh_token=bundle.refresh_token,
                                     id_token=bundle.id_token, expires_at=bundle.expires_at,
                                     account_id=bundle.account, plan_type=bundle.plan_type))

        def saved_and_written_back(token_set: Any, *args: Any, **kwargs: Any) -> Any:
            result = original(token_set, *args, **kwargs)
            before = self.current
            after = eval_profile.CodexBundle(
                token_set.access_token, token_set.refresh_token or before.refresh_token,
                token_set.id_token or before.id_token, token_set.account_id or before.account,
                token_set.expires_at or before.expires_at, token_set.plan_type or before.plan_type, before.revision)
            try:
                self.write_backs.append(eval_profile.write_back(self.profile, before, after, **self._runner()))
                self.current = eval_profile.read_bundle(self.profile, **self._runner()) or after
            except Exception:  # noqa: BLE001 - the run goes on with the tokens it holds
                self.write_backs.append("failed")
                self.current = after
            return result

        codex.save_codex_oauth_tokens = saved_and_written_back


@contextlib.contextmanager
def _worker_runner(arms: Sequence[Any], copy: Path) -> Iterator[Any]:
    """Row-Bot's evaluation worker running pack tasks as real Agent turns (O56), as the product does."""

    from row_bot.learning import eval_worker

    worker_arms = {arm.name: eval_worker.WorkerArm(arm.name, str(arm.model_ref), arm.serve_context is not None)
                   for arm in arms}
    credentials, providers, unsupported = eval_worker.gather(list(worker_arms.values()))
    spec = {"arms": [{"name": arm.name, "model_ref": arm.model_ref, "lessons": [list(item) for item in arm.lessons],
                      "ways": [list(item) for item in arm.ways]} for arm in arms],
            "tools": eval_worker.safe_tools(eval_worker.person_tools()), "providers": providers,
            "code_backend": "container"}
    worker = eval_worker.EvalWorker(spec, credentials=credentials, person_data=copy)
    try:
        worker.start()
        yield eval_worker.RuntimeRunner(worker, worker_arms, record_usage=None, unsupported=set(unsupported))
    finally:
        worker.stop()


# ── A10: the exploratory 27B trained on Hugging Face ────────────────────────


def rebuild_rows(copy: Path, newest: Mapping[str, Any], *, lookup: Any = None) -> tuple[list[Any], list[Any], bool]:
    """The rows the newest local version was trained on, rebuilt from the copy's records by the product's own
    compiler and its recipe, chosen by the version's own list of them, and checked against the SHA-256 the
    version recorded: (train, validation, matched). Unmatched rows are never trained on."""

    from row_bot.learning.store import LearningStore
    from row_bot.learning.training import dataset as dataset_module
    from row_bot.learning.training import planner, recipes, releases

    folder = releases.version_dir(int(newest["version"]), copy)
    manifest = _read_json(folder / releases.DATASET_FILE)
    wanted_train = list(manifest.get("example_ids") or ())
    wanted_validation = set(manifest.get("validation_ids") or ())
    name = str(manifest.get("recipe") or "")
    try:
        recipe = recipes.load(name.rsplit("-v", 1)[0])
    except recipes.RecipeError:
        return [], [], False
    from dataclasses import replace as _replace

    max_seq = int(manifest.get("max_seq_length") or recipe.max_seq_length)
    recipe = _replace(recipe, settings={**dict(recipe.settings), "max_seq_length": max_seq})
    every = lambda rows, _max: [(1, 1)] * len(rows)  # noqa: E731 - every candidate: the version's list chooses
    compiled = dataset_module.compile_dataset(LearningStore(copy), recipe, lookup=lookup, measure=every,
                                              workspace_roots=planner.workspace_roots(), refresh_vault=False)
    candidates = (*compiled.train, *compiled.validation)
    by_id = {item.id: item for item in candidates}
    if not wanted_train or any(item not in by_id for item in (*wanted_train, *wanted_validation)):
        return [], [], False
    keep = set(wanted_train) | wanted_validation
    ordered = [item for item in candidates if item.id in keep]  # the compiler's own order
    train = [item for item in ordered if item.id not in wanted_validation]
    validation = [item for item in ordered if item.id in wanted_validation]
    body = {"compiler": dataset_module.COMPILER_VERSION, "recipe": name, "max_seq_length": max_seq,
            "rendering": compiled.rendering.get("name"),
            "train": [item.row for item in train], "validation": [item.row for item in validation]}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()
    return train, validation, digest == manifest.get("sha256")


def rows_for_upload(copy: Path, newest: Mapping[str, Any], *, lookup: Any = None
                    ) -> tuple[list[Any], list[Any], bool, str | None]:
    """The rows A10 uploads: the newest version's own kept rows (``kept_rows``), when they still match its
    SHA-256 and every conversation they came from still exists; a version from before rows were kept is rebuilt
    (``rebuild_rows``), which only matches on the build and settings it trained with. ``(train, validation,
    matched, reason)``; the reason names why nothing can be uploaded (``rows_changed``, ``source_deleted``,
    ``rows_not_reproducible``)."""

    from types import SimpleNamespace

    from row_bot.learning import scope
    from row_bot.learning.training import kept_rows, releases

    folder = releases.version_dir(int(newest["version"]), copy)
    manifest = _read_json(folder / releases.DATASET_FILE)
    source = lookup or scope.SavedThreads()
    state, train, validation = kept_rows.read(folder, manifest, exists=lambda item: bool(source.thread(item).exists))
    if state == "kept":
        return ([SimpleNamespace(row=row) for row in train], [SimpleNamespace(row=row) for row in validation],
                True, None)
    if state != "not_kept":
        return [], [], False, "rows_changed" if state == "changed" else "source_deleted"
    train, validation, matched = rebuild_rows(copy, newest, lookup=lookup)
    return train, validation, matched, None if matched else "rows_not_reproducible"


@dataclass
class Exploratory:
    """A10's run, from its typed agreement to its report section (counts, a cost, verdicts; never an id)."""

    state: str = "not_requested"
    reason: str | None = None
    recipe: str | None = None
    examples: int = 0
    validation: int = 0
    matched: bool = False
    upload_bytes: int = 0
    job_id: str | None = None
    trainer: Any = None
    section: dict[str, Any] = field(default_factory=dict)

    def fail(self, reason: str) -> Exploratory:
        self.state, self.reason = "not_run", reason
        return self


def _hugging_face_trainer(copy: Path) -> Any:
    """The product's Hugging Face backend on the copy, signed in for this run only: the participant signs in on
    Hugging Face's own page with a code; the tokens live in this process's memory, never in a file, the copy's
    keychain or the profile's."""

    from row_bot import secret_store
    from row_bot.learning.remote import auth
    from row_bot.learning.remote import training as remote_training
    from row_bot.learning.training import jobs as jobs_module

    secret_store.use_memory_backend()
    sign_in = auth.HuggingFaceSignIn()
    state = sign_in.start()
    say(f"Sign in to Hugging Face: open {state.code.verification_uri} and enter the code {state.code.user_code},")
    say("then click Authorize. Waiting ...")
    while True:
        outcome = sign_in.poll()
        if outcome == "signed_in":
            break
        if outcome in ("expired", "denied", "refused", "none"):
            raise _ModeFailed("the Hugging Face sign-in didn't finish")
        time.sleep(state.code.interval)
    return remote_training.RemoteTrainer(copy, jobs=jobs_module.TrainingJobs(copy), sign_in=sign_in)


def start_exploratory(copy: Path, newest: Mapping[str, Any] | None, *, hooks: Hooks,
                      rows: tuple[list[Any], list[Any], bool, str | None] | None = None) -> Exploratory:
    """Rebuild the rows, show exactly what would be uploaded and its price, take the typed ``upload``, then
    submit the run on Hugging Face (it trains while the confirmatory evaluation runs here)."""

    from row_bot.application.settings_commands import write_learning_settings
    from row_bot.learning.remote.training import RemoteError
    from row_bot.learning.training import dataset as dataset_module
    from row_bot.learning.training import jobs as jobs_module
    from row_bot.learning.training import recipes

    found = Exploratory(state="not_run")
    if newest is None:
        return found.fail("no_version")
    train, validation, matched, reason = rows if rows is not None else rows_for_upload(copy, newest,
                                                                                      lookup=hooks.lookup)
    found.examples, found.validation, found.matched = len(train), len(validation), matched
    if not matched:
        return found.fail(reason or "rows_not_reproducible")
    recipe = recipes.load("hf-27b")
    found.recipe = recipe.name
    write_learning_settings({"hugging_face.model": EXPLORATORY_MODEL})
    trainer = (hooks.hugging_face or _hugging_face_trainer)(copy)
    found.trainer = trainer
    from row_bot.learning.remote import training as remote_training

    remote_training.configure(trainer)  # the release maker downloads the version's file through this one
    if trainer.choice() is None or trainer.choice().id != EXPLORATORY_MODEL:
        return found.fail("no_model")
    steps = recipe.steps_for(len(train))
    job = trainer.jobs.plan(steps_total=steps, checkpoint_every=recipe.checkpoint_interval(steps),
                            recipe=recipe.spec(None), dataset={"id": "ds_exploratory", "examples": len(train)},
                            backend="hugging_face")
    data = jobs_module.run_dir(job.id, copy) / "data"
    data.mkdir(parents=True, exist_ok=True)
    for name, items in ((dataset_module.EXAMPLES_FILE, train), (dataset_module.VALIDATION_FILE, validation)):
        (data / name).write_text("".join(json.dumps(item.row, ensure_ascii=False, sort_keys=True) + "\n"
                                         for item in items), encoding="utf-8")
    examples, size = trainer._upload_size(job)  # noqa: SLF001
    found.upload_bytes = int(size)
    quote = trainer.quote(examples=examples, upload_bytes=size, steps=steps)
    write_learning_settings({"hugging_face.monthly_cap_usd": max(1, math.ceil(quote.max_cost_usd) + 1)})
    quote = trainer.quote(examples=examples, upload_bytes=size, steps=steps)
    say()
    say("The exploratory 27B (A10) uploads, to a new private dataset in your own Hugging Face account:")
    say(f"  {examples} training rows and {len(validation)} validation rows ({size:,} bytes): exactly the rows your")
    say(f"  newest local personal model (v{int(newest['version'])}) was trained on, checked against its own record.")
    say(f"  It trains Qwen3.8 27B there on {quote.flavor_label}: about {round(quote.seconds / 60)} minutes and about "
        f"${quote.cost_usd:.2f} (at most ${quote.max_cost_usd:.2f}), billed to your Hugging Face account.")
    say("  The upload and the job's files are deleted once the model is downloaded and checked.")
    try:
        answer = hooks.ask(f"Type {UPLOAD_WORD} to agree to this upload (anything else: not run): ")
    except EOFError:
        answer = ""
    if answer.strip().lower() != UPLOAD_WORD:
        trainer.jobs.move(job.id, "stopped", reason="you")
        jobs_module.remove_run(job.id, copy)
        return found.fail("declined")
    try:
        trainer.start(job, "train_now", confirmed=True)
    except RemoteError as error:
        return found.fail(error.code if error.code in ("over_cap", "no_model", "signed_out") else "unavailable")
    found.job_id = job.id
    say("Uploaded; the run is on Hugging Face. The confirmatory evaluation runs here meanwhile.")
    return found


def finish_exploratory(copy: Path, found: Exploratory, *, as_of: float, hooks: Hooks, client: Any,
                       budget: Any, meter: Any, packs: Sequence[str], verifiers: Any, repeats: int,
                       container: bool) -> Exploratory:
    """Wait for the run, make its version ready like the product does (download, check, Row-Bot's own server),
    measure it on the same hidden problems, checker, budget and repeats, delete the uploads, report apart."""

    from row_bot.learning import evaluation
    from row_bot.learning.remote import training as remote_training
    from row_bot.learning.store import LearningStore
    from row_bot.learning.training import releases

    trainer, job_id = found.trainer, found.job_id
    if trainer is None or job_id is None:
        return found
    while True:
        trainer.tick()
        job = trainer.jobs.get(job_id)
        if job is None or job.state in ("evaluating", "failed", "stopped"):
            break
        hooks.sleep(POLL_SECONDS)
    record = remote_training.read_record(job_id, copy)
    if job is None or job.state != "evaluating":
        found.section.update(_cost(record))
        return found.fail(str((job.error_code if job is not None else None) or "remote_error"))
    maker = releases.ReleaseMaker(copy, jobs=trainer.jobs, server=hooks.release_server, runtime=lambda: "row_bot",
                                  start_threads=False)
    maker.start(job)
    trainer.tick()  # the version is settled: the uploads and the job's files go (O133)
    state = releases.read_state(releases.version_dir(int(job.version), copy))
    record = remote_training.read_record(job_id, copy)
    found.section.update(_cost(record))
    if state is None or state.phase != releases.READY:
        return found.fail("couldnt_start")
    row = next((item for item in _versions(copy) if item["version"] == int(job.version)), None)
    prepared = hooks.prepare(copy, row) if row is not None else Prepared({}, None, "row_bot", reason="no_version")
    try:
        if not prepared.refs:
            return found.fail(prepared.reason or "runtime_unavailable")
        local = hooks.prepare(copy, found.section["_newest"])
        try:
            if not local.refs:
                return found.fail(local.reason or "runtime_unavailable")
            arms = [evaluation.Arm("start27", model_ref=prepared.refs["start"], subject_kind="baseline",
                                   serve_context=prepared.serve_context),
                    evaluation.Arm("personal27", model_ref=prepared.refs["personal"], subject_kind="release",
                                   serve_context=prepared.serve_context),
                    evaluation.Arm("personal", model_ref=local.refs["personal"], subject_kind="release",
                                   serve_context=local.serve_context)]
            with (contextlib.nullcontext(hooks.runner(arms)) if hooks.runner is not None
                  else _worker_runner(arms, copy)) as runner:
                # Model by model: two 27Bs and the 9B can't stay loaded together on a 32 GB card, so interleaved
                # runs would load a model for every run (28 Oct: about 16 s a run). The same runs, budget,
                # checker, repeats and pairing; local models spend no tokens, so no limit can stop it midway.
                result = evaluation.freeze_check(
                    arms, as_of=as_of, asked_by="the paper's exploratory 27B (A10)",
                    contrasts=[("start27", "personal27"), ("personal", "personal27")], client=client,
                    budget=budget, meter=meter, store=LearningStore(copy), runner=runner,
                    cache=evaluation.ArmCache(copy), packs=packs, verifiers=verifiers, repeats=repeats,
                    on_progress=_Progress(time.monotonic()), order=EXPLORATORY_ORDER)
        finally:
            local.cleanup()
    finally:
        prepared.cleanup()
    found.state, found.reason = "run", ("stopped" if result.get("stopped") else None)
    kept = dict(state.kept_pass or {})
    found.section.update({
        "kept_pass": int(kept["pass"]) if isinstance(kept.get("pass"), int) else None,
        "passes": int(kept["of"]) if isinstance(kept.get("of"), int) else None,
        "_result": result,
    })
    return found


def _cost(record: Any) -> dict[str, Any]:
    if record is None:
        return {}
    return {"cost_usd": round(float(record.cost_usd), 4), "minutes": math.ceil(int(record.running_seconds) / 60),
            "uploads_deleted": record.cleaned_at is not None}


def exploratory_section(found: Exploratory) -> dict[str, Any]:
    """A10's report section: exploratory, apart from the confirmatory family."""

    result = found.section.get("_result") or {}
    arms = {}
    for name, row in (result.get("arms") or {}).items():
        arms[name] = {key: row[key] for key in ("tokens", "cloud_tokens", "seconds", "ran", "cached", "identical")}
        arms[name]["splits"] = {split: {**part, "runs": _counts(Counter(part["runs"]).elements(), RUN_STATUS),
                                        "causes": _counts(Counter(part["causes"]).elements(), CAUSES)}
                                for split, part in row["splits"].items()}
    return {
        "state": found.state, "reason": found.reason if found.reason in EXPLORATORY_REASONS.values else (
            "remote_error" if found.reason else None),
        "model": EXPLORATORY_MODEL, "recipe": found.recipe,
        "rows": {"examples": found.examples, "validation": found.validation, "matched": found.matched},
        "upload_bytes": found.upload_bytes, "cost_usd": found.section.get("cost_usd"),
        "minutes": found.section.get("minutes"), "uploads_deleted": found.section.get("uploads_deleted"),
        "kept_pass": found.section.get("kept_pass"), "passes": found.section.get("passes"),
        "arms": arms,
        "contrasts": {name: {split: _split(part) for split, part in value.items()}
                      for name, value in (result.get("contrasts") or {}).items()},
    }


def freeze_report(copy: Path, as_of: date, *, hooks: Hooks, main_reference: bool = False,
                  eval_profile: Path | None = None, cap_tokens: int = CLOUD_CAP, repeats: int = 3,
                  paper: str = "v1", h2: bool = False, h3_final: bool = False,
                  hugging_face_27b: bool = False) -> dict[str, Any]:
    from row_bot.learning import evaluation
    from row_bot.learning.contracts import LessonRecord, Release
    from row_bot.learning.packs import PACKS
    from row_bot.learning.spending import TokenMeter
    from row_bot.learning.store import LearningStore

    started = time.monotonic()
    store = LearningStore(copy)
    # The vault takes its share of decisions it hasn't considered yet, by the product's own rule (on the copy).
    versions = _versions(copy)
    candidates = [row for row in versions if not row["pilot"] and row["_ready_once"] and row["_gguf"] is not None
                  and Path(row["_gguf"]).is_file()]
    newest = max(candidates, key=lambda row: row["version"]) if candidates else None
    # A10 rebuilds the newest version's rows before the vault takes anything new.
    exploratory_rows = (rows_for_upload(copy, newest, lookup=hooks.lookup) if hugging_face_27b and newest is not None
                        else None)
    refreshed = evaluation.refresh_audit(store=store, asked_by="the paper's freeze evaluation")
    lessons = sum(1 for item in store.find("lesson", limit=1_000_000)
                  if isinstance(item, LessonRecord) and item.status == "in_use")
    behaviours = sum(1 for item in store.find("release", limit=1_000_000)
                     if isinstance(item, Release) and item.kind == "behaviour" and item.status in ("in_use", "trial"))
    cutoff = datetime.combine(as_of + timedelta(days=1), datetime.min.time()).timestamp() - 1e-6
    container = bool((hooks.container or _container)())
    packs = [pack for pack in PACKS if container or pack != "small_coding"]
    verifiers = None if container else ["answer", "json_answer", "file", "app_state"]
    # A10 (exploratory, opt-in): the run on Hugging Face starts first, after the participant's typed agreement,
    # and trains while the confirmatory evaluation runs here (rebuilt before the vault takes anything new).
    exploratory = (start_exploratory(copy, newest, hooks=hooks, rows=exploratory_rows) if hugging_face_27b
                   else Exploratory())
    prepared = hooks.prepare(copy, newest) if newest is not None else Prepared({}, None, "ollama", reason="no_version")
    result: dict[str, Any] | None = None
    try:
        arms = []
        if prepared.refs:
            arms = [evaluation.Arm("start", model_ref=prepared.refs["start"], subject_kind="baseline",
                                   serve_context=prepared.serve_context),
                    evaluation.Arm("personal", model_ref=prepared.refs["personal"], subject_kind="release",
                                   serve_context=prepared.serve_context)]
            contrasts = [("start", "personal")]
            if main_reference or h2:
                from row_bot.learning import routing

                main = (hooks.main_ref or routing.main_model_ref)()
                if main:
                    if eval_profile is None:
                        raise _ModeFailed("--main-reference and --h2 need --eval-profile")
                    _sign_in(eval_profile, hooks)
                    # Asked as it was served: with the page's picture when it was sent one (A8).
                    arms.append(evaluation.Arm("main", model_ref=main, cache=True, sees_images=True))
                    if main_reference:
                        contrasts.append(("start", "main"))
                    if h2 and (lessons or behaviours):
                        arms.append(_learning_arm(main))
                        contrasts.append(("main", "learning"))
            progress = _Progress(started)
            with (contextlib.nullcontext(hooks.runner(arms)) if hooks.runner is not None
                  else _worker_runner(arms, copy)) as runner:
                result = evaluation.freeze_check(
                    arms, as_of=cutoff, asked_by="the paper's freeze evaluation", contrasts=contrasts,
                    client=hooks.client, budget=evaluation.Budget(task_tokens=150_000), meter=TokenMeter(cap_tokens),
                    store=store, runner=runner, cache=evaluation.ArmCache(copy), packs=packs, verifiers=verifiers,
                    repeats=repeats, on_progress=progress)
    finally:
        prepared.cleanup()
    if exploratory.job_id is not None:
        # A10's uploads are deleted as soon as its version is made, before anything else may stop the run.
        exploratory.section["_newest"] = newest
        finish_exploratory(copy, exploratory, as_of=cutoff, hooks=hooks, client=hooks.client,
                           budget=evaluation.Budget(task_tokens=150_000), meter=TokenMeter(cap_tokens), packs=packs,
                           verifiers=verifiers, repeats=repeats, container=container)
    final = None
    if h3_final:
        if eval_profile is None:
            raise _ModeFailed("--h3-final needs --eval-profile")
        _sign_in(eval_profile, hooks)
        say("Running H3's final set: every generation and the frozen control at the same budget ...")
        final = _final_set(copy, store, hooks=hooks, cap_tokens=cap_tokens)
    report = assemble_freeze(result, as_of=as_of, versions=versions, newest=newest, prepared=prepared,
                             refreshed=refreshed, container=container, main_reference=main_reference,
                             lessons=lessons, behaviours=behaviours, hours=(time.monotonic() - started) / 3600,
                             paper=paper, h3=h3_report(store, final_state=final, ran_final=h3_final))
    if hugging_face_27b:
        report["exploratory_27b"] = exploratory_section(exploratory)
    return report


_SIGNED_IN: list[Path] = []


def _sign_in(eval_profile: Path, hooks: Hooks) -> None:
    """The eval profile's ChatGPT sign-in, once a run (the main model's arms and H3's final set share it)."""

    if eval_profile not in _SIGNED_IN:
        EvalSignIn(eval_profile, bridge=hooks.bridge).install()
        _SIGNED_IN.append(eval_profile)


def _learning_arm(main: str) -> Any:
    """H2's arm (§5): the main model with the lessons and ways of working in use on the copy (in the real runtime
    as recall and the turn's overlay; replayed problems get them as the guidance a test gives)."""

    from row_bot.learning import changes, evaluation

    lessons, ways = changes.context_in_use()
    guidance = tuple(text for _title, text in lessons) + tuple(text for _slot, text in ways)
    return evaluation.Arm("learning", model_ref=main, guidance=guidance, lessons=lessons, ways=ways, sees_images=True)


def _final_set(copy: Path, store: Any, *, hooks: Hooks, cap_tokens: int) -> str:
    """H3's final set (§7) on the copy: every generation and the frozen control at the same budget on the fresh
    problems no trial used. Returns how it ended (``done``, ``fewer_fresh``, ``stopped``)."""

    from row_bot.learning import changes
    from row_bot.learning.calls import LedgerModelClient
    from row_bot.learning.improver import archive, meta
    from row_bot.learning.spending import TokenMeter

    def tester(method: Any) -> Any:
        if hooks.tester is not None:
            return hooks.tester(method)
        return changes.Tester(copy, store=store, client=hooks.client, plan=changes.TestPlan.of(method),
                              purpose=meta.PURPOSE)

    from row_bot.learning import evaluation

    done = [item for item in archive.trials(store) if not item.get("final")]
    # The same budget per method as the trials gave (the last one's), and every method gets all of it.
    budget = int(done[-1].get("arm_tokens") or meta.ARM_TOKENS_MAX) if done else meta.ARM_TOKENS_MAX
    arms = len({str(item.get("winner")) for item in done} | {archive.frozen_control(store).id})
    per_run = int(evaluation.fresh_problems(asked_by="the paper's freeze evaluation", store=store)["tokens_per_run"])
    needed = arms * budget + (1 + arms * meta.MAX_ACCEPTED) * per_run * meta.REPEATS
    if needed > int(cap_tokens):
        raise _ModeFailed(f"--cloud-cap is too small for H3's final set: it needs about {needed:,} tokens")
    found = meta.run_final(store, label="freeze", client=hooks.client or LedgerModelClient(), tester_factory=tester,
                           arm_tokens=budget, meter=TokenMeter(int(cap_tokens)))
    return found.state


def h3_report(store: Any, *, final_state: str | None, ran_final: bool) -> dict[str, Any]:
    """H3 (§5, §7) from the trials the product ran (kept in the copy) and the final set: the per-generation yield
    differences, the estimate with its bootstrap stratified by trial, and "not tested" below the minimum (three
    inherited generations, each judged on at least 30 fresh problems)."""

    from row_bot.learning import evaluation
    from row_bot.learning.improver import archive

    trials = archive.trials(store)
    generations = archive.generations(store)
    found = evaluation.h3_estimate(generations, store=store)
    rows = [{"problems": int(row["problems"]), "clusters": int(row["clusters"]),
             "inherited": {key: row["inherited"][key] for key in ("accepted", "better", "tokens", "yield")},
             "control": {key: row["control"][key] for key in ("accepted", "better", "tokens", "yield")},
             "difference": row["difference"]} for row in found["generations"]]
    final_trial = next((item for item in reversed(trials) if item.get("final")), None)
    final_set = None
    if final_trial is not None:
        final_set = {"problems": int(final_trial.get("problems") or 0), "arm_tokens": int(final_trial.get("arm_tokens") or 0),
                     "arms": [{"role": "generation" if str(arm["role"]).startswith("generation") else str(arm["role"]),
                               "version": int(arm["version"]), "accepted": int(arm["accepted"]),
                               "better": int(arm["better"]), "tokens": int(arm["tokens"]), "yield": float(arm["yield"])}
                              for arm in final_trial.get("arms") or ()]}
    if not trials:
        state, reason = "not_testable", "no_trials"
    elif final_trial is None:
        state, reason = "not_tested", "stopped" if ran_final and final_state == "stopped" else (
            "final_set_too_small" if ran_final and final_state == "fewer_fresh" else "final_set_not_run")
    elif len(rows) < 3:
        state, reason = "not_tested", "too_few_generations"
    elif not found["enough"]:
        state, reason = "not_tested", "too_few_problems"
    else:
        state, reason = "tested", None
    return {"state": state, "reason": reason, "trials": sum(1 for item in trials if not item.get("final")),
            "generations": rows, "estimate": found["estimate"], "low": found["low"], "high": found["high"],
            "p_value": found["p_value"] if state == "tested" else None, "p_holm": None, "met": None,
            "resamples": int(found["resamples"]), "seed": int(found["seed"]), "minimum": int(found["minimum"]),
            "final_set": final_set}


def _container() -> bool:
    from row_bot.learning.sandbox import container_runtime

    return container_runtime() is not None


class _Progress:
    """Counts only: how many runs are done, every few minutes."""

    def __init__(self, started: float) -> None:
        self.started = started
        self.last = 0.0

    def __call__(self, done: int, total: int) -> None:
        now = time.monotonic()
        if done == total or now - self.last >= 120:
            self.last = now
            say(f"  {done} of {total} runs ({round((now - self.started) / 60)} min)")


def _summary(found: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return None if found is None else {key: found[key] for key in ANALYSIS.fields}


def assemble_freeze(result: Mapping[str, Any] | None, *, as_of: date, versions: Sequence[Mapping[str, Any]],
                    newest: Mapping[str, Any] | None, prepared: Prepared, refreshed: Mapping[str, int],
                    container: bool, main_reference: bool, lessons: int, behaviours: int,
                    hours: float, paper: str = "v1", h3: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The freeze's JSON from ``evaluation.freeze_check``'s aggregates: the design, the arms, every contrast,
    H1, H2 and H3 with the pre-registered rules (§5, §7), and Holm across the paper version's family ({H1, H2} in
    v1, {H1, H2, H3} in v2; an untested member enters as 1, A6)."""

    from row_bot.learning import evaluation

    result = dict(result or {})
    contrast = (result.get("contrasts") or {}).get("personal vs start") or {}
    corrected = int((result.get("problems") or {}).get("real_corrected") or 0)
    h1: dict[str, Any] = {"state": "not_testable", "reason": prepared.reason or "no_version", "superiority": None,
                          "non_inferiority": None, "supported": None, "sensitivity": None, "by_kind": None,
                          "counts": None}
    p_h1 = None
    if contrast:
        primary = contrast["real_uncorrected"]["primary"]
        packs = contrast["packs"]["primary"]
        enough = primary["problems"] >= evaluation.MIN_PROBLEMS
        stopped = bool(result.get("stopped"))
        p_h1 = primary["p_value"] if enough and not stopped else None
        h1.update({
            "state": "tested" if p_h1 is not None else "not_tested",
            "reason": None if p_h1 is not None else ("stopped" if stopped else "too_few_problems"),
            "superiority": _part("real_uncorrected", primary),
            "non_inferiority": {**_part("packs", packs), "margin": -MARGIN,
                                "met": packs["low"] is not None and packs["low"] > -MARGIN},
            "sensitivity": {
                "exact_only": _summary(contrast["real_uncorrected"]["exact_only"]),
                "majority_vote": _summary(contrast["real_uncorrected"]["majority_vote"]),
                "unclustered": _summary(contrast["real_uncorrected"]["unclustered"]),
                "per_main_model": {key: _summary(value) for key, value in
                                   contrast["real_uncorrected"]["per_main_model"].items()},
                "corrected_problems": _summary(contrast["real_corrected"]["primary"]),
                # §7: H1 under both checker versions (METRIC_VERSION 3's guard, and 2 before it), the same runs.
                "checker_versions": 2,
                "metric_v2": _summary(contrast["real_uncorrected"]["metric_v2"]),
                # A7: trusted input only. A8: without the problems that carry a page picture.
                "trusted_only": _summary(contrast["real_uncorrected"]["trusted_only"]),
                "without_pictures": _summary(contrast["real_uncorrected"]["without_pictures"]),
            },
            "by_kind": {key: _summary(value) for key, value in contrast["real_uncorrected"]["by_kind"].items()},
        })
    h2, p_h2 = _h2(result, lessons=lessons, behaviours=behaviours, corrected=corrected)
    h3 = dict(h3 or h3_report_empty())
    p_h3 = h3.get("p_value") if h3.get("state") == "tested" else None
    family = FAMILIES[paper]
    adjusted = evaluation.holm({"H1": p_h1, "H2": p_h2, "H3": p_h3}, family=family)
    if h1["superiority"] is not None:
        h1["superiority"]["p_holm"] = adjusted["H1"] if p_h1 is not None else None
        met = p_h1 is not None and adjusted["H1"] < ALPHA and (h1["superiority"]["low"] or 0) > 0
        h1["superiority"]["met"] = met if p_h1 is not None else None
        h1["supported"] = (met and bool(h1["non_inferiority"]["met"])) if p_h1 is not None else None
    if h2["superiority"] is not None:
        h2["superiority"]["p_holm"] = adjusted["H2"] if p_h2 is not None else None
        met = p_h2 is not None and adjusted["H2"] < ALPHA and (h2["superiority"]["low"] or 0) > 0
        h2["superiority"]["met"] = met if p_h2 is not None else None
        h2["supported"] = (met and bool(h2["non_inferiority"]["met"]) and bool(h2["non_inferiority_packs"]["met"])
                           if p_h2 is not None else None)
    if "H3" in family and p_h3 is not None:
        h3["p_holm"] = adjusted["H3"]
        h3["met"] = adjusted["H3"] < ALPHA and (h3.get("low") or 0) > 0
    arms = {}
    for name, row in (result.get("arms") or {}).items():
        arms[name] = {key: row[key] for key in ("tokens", "cloud_tokens", "seconds", "ran", "cached", "identical")}
        arms[name]["splits"] = {split: {**part, "runs": _counts(Counter(part["runs"]).elements(), RUN_STATUS),
                                        "causes": _counts(Counter(part["causes"]).elements(), CAUSES)}
                                for split, part in row["splits"].items()}
    contrasts = {name: {split: _split(part) for split, part in value.items()}
                 for name, value in (result.get("contrasts") or {}).items()}
    problems = result.get("problems") or {}
    candidates = [row for row in versions if not row["pilot"] and row["_ready_once"]]
    return {
        "format": FORMAT, "schema_version": SCHEMA_VERSION, "mode": "freeze",
        "design": {
            "as_of": as_of.isoformat(), "paper": paper, "metric_version": evaluation.METRIC_VERSION,
            "previous_metric_version": evaluation.PREVIOUS_METRIC_VERSION,
            "repeats": int(result.get("repeats") or 0), "seed": evaluation.ANALYSIS_SEED,
            "resamples": evaluation.RESAMPLES, "permutations": evaluation.PERMUTATIONS,
            "task_tokens": int(((result.get("budget") or {}).get("task_tokens")) or 150_000),
            "runtime": prepared.runtime, "serve_context": prepared.serve_context, "container": container,
            "main_reference": main_reference, "stopped": bool(result.get("stopped")), "hours": round(hours, 3),
            "cloud_tokens": sum(row["cloud_tokens"] for row in arms.values()),
            "local_tokens": sum(row["tokens"] - row["cloud_tokens"] for row in arms.values()),
            "vault_refresh": {key: int(refreshed.get(key, 0)) for key in ("considered", "added", "retired", "held")},
        },
        "versions": {"total": len(versions), "pilots": sum(1 for row in versions if row["pilot"]),
                     "candidates": len(candidates), "evaluated": int(newest["version"]) if newest and contrast else None,
                     "trained_on": newest["trained_on"] if newest and contrast else None,
                     "kept_pass": newest["kept_pass"] if newest and contrast else None,
                     "passes": newest["passes"] if newest and contrast else None},
        "problems": {"real_uncorrected": int(problems.get("real_uncorrected") or 0), "real_corrected": corrected,
                     "packs": int(problems.get("packs") or 0), "after_as_of": int(problems.get("after_as_of") or 0),
                     "main_models": int(result.get("main_models") or 0),
                     "untrusted": int(problems.get("untrusted") or 0),
                     "with_picture": int(problems.get("with_picture") or 0)},
        "arms": arms, "contrasts": contrasts, "hypotheses": {"H1": h1, "H2": h2, "H3": h3},
        "holm": {"family": list(family), "alpha": ALPHA, "untested_enter_as": 1.0,
                 "adjusted": {name: (adjusted[name] if {"H1": p_h1, "H2": p_h2, "H3": p_h3}[name] is not None
                                     else None) for name in family}},
    }


def h3_report_empty() -> dict[str, Any]:
    from row_bot.learning import evaluation

    return {"state": "not_testable", "reason": "no_trials", "trials": 0, "generations": [], "estimate": None,
            "low": None, "high": None, "p_value": None, "p_holm": None, "met": None,
            "resamples": evaluation.RESAMPLES, "seed": evaluation.ANALYSIS_SEED, "minimum": evaluation.MIN_FRESH,
            "final_set": None}


def _h2(result: Mapping[str, Any], *, lessons: int, behaviours: int, corrected: int) -> tuple[dict[str, Any], Any]:
    """H2 (§5): the main model with the lessons and ways of working in use against the same model with none, on the
    hidden corrected problems (superiority) and not worse on the uncorrected ones and the packs (non-inferiority);
    its sensitivity analyses include version 2 of the checker (§7). Returns it and its p-value (None: untested)."""

    from row_bot.learning import evaluation

    counts = {"lessons_in_use": lessons, "ways_of_working_in_use": behaviours, "corrected_problems": corrected}
    contrast = (result.get("contrasts") or {}).get("learning vs main") or {}
    h2: dict[str, Any] = {"state": "not_testable", "superiority": None, "non_inferiority": None, "supported": None,
                          "sensitivity": None, "by_kind": None, "counts": counts}
    if not (lessons or behaviours):
        return {**h2, "reason": "no_lessons_or_ways_of_working"}, None
    if not contrast:
        return {**h2, "reason": "not_run"}, None
    primary = contrast["real_corrected"]["primary"]
    stopped = bool(result.get("stopped"))
    enough = primary["problems"] >= evaluation.MIN_PROBLEMS
    p_h2 = primary["p_value"] if enough and not stopped else None

    def ni(split: str) -> dict[str, Any]:
        found = contrast[split]["primary"]
        return {**_part(split, found), "margin": -MARGIN, "met": found["low"] is not None and found["low"] > -MARGIN}

    h2.update({
        "state": "tested" if p_h2 is not None else "not_tested",
        "reason": None if p_h2 is not None else ("stopped" if stopped else "too_few_problems"),
        "superiority": _part("real_corrected", primary), "non_inferiority": ni("real_uncorrected"),
        "non_inferiority_packs": ni("packs"),
        "sensitivity": {
            "exact_only": _summary(contrast["real_corrected"]["exact_only"]),
            "majority_vote": _summary(contrast["real_corrected"]["majority_vote"]),
            "unclustered": _summary(contrast["real_corrected"]["unclustered"]),
            "per_main_model": {key: _summary(value) for key, value in contrast["real_corrected"]["per_main_model"].items()},
            "corrected_problems": _summary(primary), "checker_versions": 2,
            "metric_v2": _summary(contrast["real_corrected"]["metric_v2"]),
            "trusted_only": _summary(contrast["real_corrected"]["trusted_only"]),
            "without_pictures": _summary(contrast["real_corrected"]["without_pictures"]),
        },
        "by_kind": {key: _summary(value) for key, value in contrast["real_corrected"]["by_kind"].items()},
    })
    return h2, p_h2


def _part(split: str, found: Mapping[str, Any]) -> dict[str, Any]:
    return {"split": split, "problems": found["problems"], "clusters": found["clusters"],
            "difference": found["difference"], "low": found["low"], "high": found["high"],
            "p_value": found["p_value"], "p_holm": None, "margin": None, "met": None}


def _split(part: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"primary": _summary(part["primary"]), "metric_v2": _summary(part["metric_v2"]),
                           "majority_vote": _summary(part["majority_vote"]),
                           "unclustered": _summary(part["unclustered"]), "left_pair": int(part["left_pair"]),
                           "by_kind": {key: _summary(value) for key, value in part["by_kind"].items()}}
    if "exact_only" in part:
        out["exact_only"] = _summary(part["exact_only"])
        out["per_main_model"] = {key: _summary(value) for key, value in part["per_main_model"].items()}
        out["trusted_only"] = _summary(part["trusted_only"])
        out["without_pictures"] = _summary(part["without_pictures"])
    return out


# ── 7. --thinking: O97's recheck through the L2w ablation script ────────────


def run_ablation(copy: Path, out: Path) -> int:
    """``learning_shape_ablation.py --thinking both`` on the copy, its own output kept out of sight (it
    names models and files); only its results file is read, by :func:`thinking_report`."""

    env = {**os.environ, "ROW_BOT_DATA_DIR": str(copy), "ROW_BOT_LEARNING_PREVIEW": "1",
           "ROW_BOT_DESKTOP_NOTIFICATIONS": "off", "ROW_BOT_AUTO_START_OLLAMA": "0"}
    log = out / "ablation.log"
    with log.open("wb") as handle:
        process = subprocess.run([sys.executable, str(ROOT / "scripts" / "learning_shape_ablation.py"), "--data",
                                  str(copy), "--out", str(out), "--thinking", "both"], cwd=ROOT, env=env,
                                 stdout=handle, stderr=subprocess.STDOUT, check=False)
    return process.returncode


def thinking_report(results: Mapping[str, Any]) -> dict[str, Any]:
    """The ablation's results (``l2w_ablation.json``) as aggregates: rows, training, time per task and
    tokens per arm, and thinking on vs off per model (visible, hidden, both), overall and per kind."""

    rows = {}
    for name, row in (results.get("rows") or {}).items():
        if name in THINKING_MODELS.values and isinstance(row, dict):
            rows[name] = {key: int(row.get(key) or 0) for key in ("fit", "rows_measured", "too_long", "examples")}
    training = {}
    for name, row in (results.get("train") or {}).items():
        if name in THINKING_MODELS.values and isinstance(row, dict):
            training[name] = {"trained": row.get("state") == "evaluating", "examples": int(row.get("examples") or 0),
                              "steps": int(row["steps"]) if isinstance(row.get("steps"), int) else None,
                              "gpu_hours": float(row["gpu_hours"]) if isinstance(row.get("gpu_hours"), (int, float))
                              else None}
    compare = results.get("compare") or {}
    arms = {}
    for name, row in (compare.get("per_arm") or {}).items():
        if not isinstance(row, dict) or not _text_ok(str(name), THINKING.fields["arms"].keys):
            continue
        # Time per task as served: pack tasks (visible) apart from the hidden part, where replays take a second.
        seconds = {part: float(row[part]["seconds_per_run"]) for part in ("visible", "hidden")
                   if isinstance(row.get(part), dict) and isinstance(row[part].get("seconds_per_run"), (int, float))}
        arms[name] = {"seconds_per_task": seconds,
                      "tokens": sum(int(part.get("tokens") or 0) for part in row.values() if isinstance(part, dict))}
    contrasts: dict[str, Any] = {}
    analysis = compare.get("analysis") or {}
    sources = (("visible", (analysis.get("visible") or {}).get("pairs") or {}),
               ("hidden", (analysis.get("hidden") or {}).get("pairs") or {}),
               ("combined", (analysis.get("hidden") or {}).get("combined_pairs") or {}))
    for part, pairs in sources:
        for model in THINKING_MODELS.values:
            found = pairs.get(f"{model}-on vs {model}-off")
            if not isinstance(found, dict):
                continue
            contrasts.setdefault(model, {})[part] = {
                "overall": _summary(found["overall"]),
                "by_kind": {key: _summary(value) for key, value in (found.get("by_kind") or {}).items()
                            if key in FAMILY_KEYS.values}}
    return {"format": FORMAT, "schema_version": SCHEMA_VERSION, "mode": "thinking",
            "design": {"visible_repeats": int((compare.get("repeats") or {}).get("visible") or 0),
                       "hidden_repeats": int((compare.get("repeats") or {}).get("hidden") or 0),
                       "container": bool(compare.get("container")),
                       "hours": round(float(compare.get("seconds") or 0) / 3600, 3), "complete": bool(compare)},
            "rows": rows, "training": training, "arms": arms, "thinking_on_vs_off": contrasts}


# ── 8. Showing, asking, writing ─────────────────────────────────────────────


def render(report: Mapping[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False)


def output_name(report: Mapping[str, Any]) -> str:
    stamp = report["run"]["on"].replace("-", "")
    if report["mode"] == "freeze":
        return f"paper-freeze-asof-{report['design']['as_of'].replace('-', '')}-run-{stamp}.json"
    return f"paper-{report['mode']}-{stamp}.json"


def write_report(report: Mapping[str, Any], out: Path) -> str:
    out.mkdir(parents=True, exist_ok=True)
    name = output_name(report)
    target = out / name
    number = 2
    while target.exists():
        target = out / name.replace(".json", f"-{number}.json")
        number += 1
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(render(report) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(target)
    return target.name


@contextlib.contextmanager
def _quiet() -> Iterator[None]:
    """Row-Bot's own log lines and Python's warnings could name models, files or folders: none of them
    reach the console while the run works on the copy."""

    import warnings

    before = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            yield
    finally:
        logging.disable(before)


# ── 9. The run ──────────────────────────────────────────────────────────────


def parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--health", action="store_true", help="counts and errors only (any time)")
    mode.add_argument("--freeze", action="store_true", help="the pre-registered freeze evaluation (§7)")
    mode.add_argument("--thinking", action="store_true", help="O97's thinking recheck (the L2w ablation)")
    parser.add_argument("--as-of", help="--freeze: the freeze date, YYYY-MM-DD (problems up to the end of that day)")
    parser.add_argument("--profile", required=True, help="the Row-Bot profile folder (read only, never written)")
    parser.add_argument("--scratch", required=True, help="where the copy goes (outside the profile and the checkout)")
    parser.add_argument("--out", required=True, help="where the JSON goes")
    parser.add_argument("--yes", action="store_true", help="write without asking")
    parser.add_argument("--keep-copy", action="store_true", help="keep the scratch copy (it is deleted otherwise)")
    parser.add_argument("--main-reference", action="store_true",
                        help="--freeze: also score the main model (a reference, not a hypothesis arm; cloud tokens)")
    parser.add_argument("--hugging-face-27b", action="store_true",
                        help="--freeze: also the exploratory 27B on Hugging Face (A10; you sign in there and type "
                             "'upload' to agree to the upload first)")
    parser.add_argument("--eval-profile",
                        help="--main-reference, --h2, --h3-final: the eval profile whose ChatGPT sign-in the main "
                             "model uses (never the profile's own)")
    parser.add_argument("--paper", choices=("v1", "v2"), default="v1",
                        help="--freeze: the paper version (its confirmatory family: v1 H1 and H2, v2 adds H3)")
    parser.add_argument("--h2", action="store_true",
                        help="--freeze: run H2's arms (the main model with and without the lessons and ways of "
                             "working in use; cloud tokens)")
    parser.add_argument("--h3-final", action="store_true",
                        help="--freeze: run H3's final set (every learning method generation and the frozen control "
                             "at the same budget on the problems no trial used; cloud tokens)")
    parser.add_argument("--cloud-cap", type=int, default=CLOUD_CAP,
                        help="--freeze: most cloud tokens each cloud part may use (the main model's arms, H3's final "
                             "set)")
    args = parser.parse_args(argv)
    cloud = args.main_reference or args.h2 or args.h3_final
    if cloud and not args.eval_profile:
        parser.error("--main-reference, --h2 and --h3-final need --eval-profile (the eval profile's ChatGPT sign-in, "
                     "never yours)")
    if args.eval_profile and not cloud:
        parser.error("--eval-profile goes with --main-reference, --h2 or --h3-final")
    if args.freeze:
        try:
            args.as_of_date = date.fromisoformat(str(args.as_of or ""))
        except ValueError:
            parser.error("--freeze needs --as-of YYYY-MM-DD")
    elif args.as_of:
        parser.error("--as-of goes with --freeze")
    if args.hugging_face_27b and not args.freeze:
        parser.error("--hugging-face-27b goes with --freeze")
    return args


def main(argv: Sequence[str] | None = None, *, hooks: Hooks | None = None) -> int:
    args = parse(argv)
    hooks = hooks or Hooks()
    mode = "health" if args.health else "freeze" if args.freeze else "thinking"
    profile, scratch, out = Path(args.profile).resolve(), Path(args.scratch).resolve(), Path(args.out).resolve()
    started = time.monotonic()
    say("Row-Bot Learning: the paper's report")
    code = code_hashes()
    say(f"  Script SHA-256:  {code['script_sha256']}")
    say(f"  Product SHA-256: {code['code_sha256']} ({code['code_files']} files)")
    say(f"  Build:           {code['build'] or 'unknown'}{'' if code['build_clean'] else ' (with local changes)'}")
    say("  Log these three lines in the Preview log before the run.")
    say()
    try:
        check_folders(profile, scratch, out)
        if args.eval_profile and (_inside(Path(args.eval_profile), profile) or _inside(profile, Path(args.eval_profile))
                                  or Path(args.eval_profile).resolve() == everyday_profile()):
            raise Refused("The --eval-profile folder must be the eval profile, not your profile.")
        refuse_if_running(profile, installed=hooks.installed)
        scratch.mkdir(parents=True, exist_ok=True)
        target = scratch / (COPY_PREFIX + datetime.now().strftime("%Y%m%d-%H%M%S"))
        if target.exists():
            raise Refused("A copy from this second already exists in --scratch; wait a second and run it again.")
    except Refused as refused:
        say(refused.message)
        return 2
    copy = target / "data"
    report: dict[str, Any] | None = None
    status = 1
    try:
        say(f"Copying {'the Learning records the health check counts' if mode == 'health' else 'the profile'} "
            "to a new folder in --scratch ...")
        try:
            tree = copy_profile(profile, copy, (lambda path: bool(HEALTH_FILES.match(path))) if mode == "health"
                                else None)
        except CopyMismatch as mismatch:
            say(f"{mismatch.text}. Nothing was written.")
            return 1
        except Refused as refused:
            say(refused.message)
            return 2
        say(f"Copy checked: {len(tree.files)} files, {tree.bytes:,} bytes, the same as the profile"
            + (f" ({tree.links} links not copied)" if tree.links else "") + ".")
        try:
            with _quiet():
                report = _run_mode(mode, copy, target, args, hooks)
        except _ModeFailed:
            raise
        except Exception as error:  # noqa: BLE001 - its message or traceback could name data: the type only
            if os.environ.get("ROW_BOT_PAPER_TRACEBACK"):  # for runs on seeded data only, never the profile
                raise
            raise _ModeFailed(f"{type(error).__name__} (run again with --keep-copy to keep the copy)") from None
        report["run"] = {"on": date.today().isoformat(), "build": code["build"], "build_clean": code["build_clean"],
                         "script_sha256": code["script_sha256"], "code_sha256": code["code_sha256"],
                         "code_files": code["code_files"], "copy": {"files": len(tree.files), "bytes": tree.bytes,
                                                                    "links_skipped": tree.links},
                         "minutes": round((time.monotonic() - started) / 60, 1)}
        try:
            validate(report, SCHEMAS[mode])
        except SchemaError as error:
            say(f"The report didn't pass its own schema ({error}). Nothing was written.")
            return 1
        say()
        say("This is exactly what will be written:")
        say(render(report))
        say()
        if not args.yes:
            try:
                answer = hooks.ask("Write it to the --out folder? [y/N] ")
            except EOFError:
                answer = ""
            if answer.strip().lower() not in ("y", "yes"):
                say("Nothing was written.")
                return 0
        say(f"Written: {write_report(report, out)} in the --out folder.")
        status = 0
        return status
    except _ModeFailed as failed:
        say(f"The {mode} run stopped: {failed.reason}. Nothing was written.")
        return 1
    finally:
        if args.keep_copy:
            say("The scratch copy was kept (--keep-copy); delete it from the --scratch folder when done.")
        elif target.exists():
            release_handles(target)
            if remove_copy(target):
                say("The scratch copy was deleted.")
            else:
                say(f"The scratch copy couldn't be deleted: remove the folder {target.name} from --scratch by hand.")


class _ModeFailed(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _run_mode(mode: str, copy: Path, target: Path, args: argparse.Namespace, hooks: Hooks) -> dict[str, Any]:
    # Every Row-Bot module reads this folder from here on: the copy, never the profile (O68: no notifications).
    os.environ["ROW_BOT_DATA_DIR"] = str(copy)
    os.environ["ROW_BOT_LEARNING_PREVIEW"] = "1"
    os.environ["ROW_BOT_DESKTOP_NOTIFICATIONS"] = "off"
    os.environ.setdefault("ROW_BOT_AUTO_START_OLLAMA", "0")
    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))
    from row_bot import learning

    learning.invalidate()
    if mode == "thinking":
        out = target / "ablation"
        out.mkdir(parents=True, exist_ok=True)
        say("Running the thinking recheck (the L2w ablation, --thinking both); this takes hours ...")
        code = run_ablation(copy, out)
        results = _read_json(out / "l2w_ablation.json")
        if code != 0 and not results.get("compare"):
            raise _ModeFailed(f"the ablation ended with exit code {int(code)} before its comparison")
        return thinking_report(results)
    if mode == "health":
        say("Counting ...")
        return health_report(copy)
    if not learning.read_settings(copy).enabled:
        raise _ModeFailed("Learning is off in this profile")
    say(f"Running the freeze evaluation on every hidden problem up to {args.as_of_date.isoformat()} ...")
    return freeze_report(copy, args.as_of_date, hooks=hooks, main_reference=bool(args.main_reference),
                         eval_profile=Path(args.eval_profile).resolve() if args.eval_profile else None,
                         cap_tokens=int(args.cloud_cap), paper=str(args.paper), h2=bool(args.h2),
                         h3_final=bool(args.h3_final), hugging_face_27b=bool(args.hugging_face_27b))


if __name__ == "__main__":
    raise SystemExit(main())
