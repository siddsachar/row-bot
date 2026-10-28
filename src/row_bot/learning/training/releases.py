"""Personal-model versions: from a finished training run to a version made ready (O6, D14, O26).

When a run finishes (its job ``evaluating``), the version is made in its own folder,
``learning_models/versions/v<N>/``:

1. **The adapter** (HF/PEFT format) is checked against what the engine reported and copied in.
2. **The release record**: a ``Release`` of kind ``model`` with the full identity (starting model,
   tokenizer, chat template, adapter, quantization, recipe, data with the providers whose answers it
   holds (D7), the runtime once ready, tests so far) and its lineage to the previous version, saved in
   ``learning.db`` and **as a manifest next to the model files** (``release.json``, O6), so a version
   that stays until retrained keeps its identity after "Delete learning data".
3. **Export**: the GGUF at the recipe's quantization (``export.py``), and a **model card**
   (``MODEL_CARD.md``): what it was trained on, in plain words.
4. **Made ready** in the chosen runtime (``serving.py``): ready only when Row-Bot's probe and one real
   Agent turn with a tool pass. The job then moves to ``ready``. A ready version still waits for
   L2c2's comparison before anyone is asked to use it: nothing here offers it.

**A version trained on Hugging Face** (C1) takes the same steps: its adapters were downloaded and checked with
the run, and its export is the GGUF the job built for the pass being made ready, downloaded and checked against
the run's manifest (``remote/training.py`` ``download_gguf``; the last pass first, an earlier one only when the
last couldn't finish the ready check, O119). The recipe is the run's own Hugging Face recipe.

Each step's state is kept in ``state.json`` beside the files, so a restart picks up where it was: an
export or a check cut short runs again; a failure waits for **Try again**; a runtime the person
starts themselves (LM Studio, llama.cpp, vLLM) is checked again every few minutes until the model
answers there.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from row_bot import learning as gate
from row_bot.learning import contracts
from row_bot.learning.contracts import DataSpec, Job, Pin, Release, ReleaseIdentity

logger = logging.getLogger(__name__)

VERSIONS_FOLDER = "versions"
STATE_FILE = "state.json"
MANIFEST_FILE = "release.json"
MODEL_CARD_FILE = "MODEL_CARD.md"
DATASET_FILE = "dataset.json"
ARTIFACTS_FILE = "artifacts.json"
# Phases a version goes through (``VersionState.phase``).
EXPORTING, EXPORT_FAILED, MAKING_READY, READY, RUNTIME_FAILED, WAITING = (
    "exporting", "export_failed", "making_ready", "ready", "runtime_failed", "waiting_for_you")
RECHECK_SECONDS = 120.0


class StopExport(Exception):
    """Row-Bot is quitting (or Learning was turned off): the export ends now and runs again later."""


def versions_dir(root: Path | None = None) -> Path:
    from row_bot.learning.training import jobs as jobs_module

    return jobs_module.models_dir(root) / VERSIONS_FOLDER


def version_dir(version: int, root: Path | None = None) -> Path:
    return versions_dir(root) / f"v{int(version)}"


@dataclass(frozen=True)
class VersionState:
    """Where a version stands, as the Personal model view shows it."""

    version: int
    phase: str
    job_id: str = ""
    release_id: str = ""
    step: str | None = None
    failure: str | None = None
    runtime: str = "ollama"
    model_ref: str | None = None
    quantization: str = "Q8_0"
    gguf: str | None = None
    gguf_bytes: int = 0
    adapter_bytes: int = 0
    command: str | None = None
    needs_bytes: int | None = None
    free_bytes: int | None = None
    trained_at: float | None = None
    examples: int = 0
    kept_pass: Mapping[str, Any] = field(default_factory=dict)
    updated_at: float = 0.0
    verified_at: float | None = None
    checks: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {key: (dict(value) if isinstance(value, Mapping) else value) for key, value in self.__dict__.items()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VersionState | None:
        try:
            known = {key: data[key] for key in cls.__dataclass_fields__ if key in data}
            return cls(**known)
        except (TypeError, KeyError):
            return None


def read_state(folder: Path) -> VersionState | None:
    try:
        data = json.loads((Path(folder) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return VersionState.from_dict(data) if isinstance(data, dict) else None


def write_state(folder: Path, state: VersionState) -> VersionState:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / f".{STATE_FILE}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(json.dumps(state.to_dict(), indent=1, sort_keys=True), encoding="utf-8")
    os.replace(temporary, folder / STATE_FILE)
    gate.changed()
    return state


def couldnt_start(state: VersionState | None) -> bool:
    """A version its runtime couldn't start (a failed load, probe or tool turn) and that was never ready.
    It stays listed with Try again, Change where it runs and Delete, but it doesn't hold up the next
    version, and what it was trained on doesn't count as trained (it may have been trained another way,
    as the preview's first pilot was). A runtime that isn't there yet (``waiting_for_you``) isn't this."""

    return state is not None and state.phase == RUNTIME_FAILED and not state.verified_at


def holds_back(job: Any, root: Path | None = None) -> bool:
    """Whether the unsettled ``job`` keeps the next version from being planned: every one does, except a
    finished run whose version couldn't start."""

    if job is None:
        return False
    if job.state != "evaluating" or not job.version:
        return True
    state = read_state(version_dir(int(job.version), root))
    return not (state is not None and state.job_id == job.id and couldnt_start(state))


def latest_state(root: Path | None = None, *, started: bool = False) -> VersionState | None:
    """The newest version's state (the Personal model view follows it); with ``started``, the newest
    one that didn't fail to start (what new examples are counted against)."""

    folder = versions_dir(root)
    if not folder.is_dir():
        return None
    found = []
    for entry in folder.iterdir():
        if entry.is_dir() and entry.name.startswith("v") and entry.name[1:].isdigit():
            state = read_state(entry)
            if state is not None and not (started and couldnt_start(state)):
                found.append(state)
    return max(found, key=lambda item: item.version) if found else None


def trained_example_ids(root: Path | None = None) -> set[str]:
    """The examples the newest version was trained on (ids only), to count what is new since. A version
    that couldn't start doesn't count."""

    state = latest_state(root, started=True)
    if state is None:
        return set()
    try:
        manifest = json.loads((version_dir(state.version, root) / DATASET_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(manifest, dict):
        return set()
    return set(manifest.get("example_ids") or ()) | set(manifest.get("validation_ids") or ())


# ── The model card ──────────────────────────────────────────────────────────


def _day(at: float | None) -> str:
    if not at:
        return "unknown"
    return datetime.fromtimestamp(float(at), tz=timezone.utc).astimezone().strftime("%d %b %Y")


def _size(count: int) -> str:
    if count >= 1_000_000_000:
        return f"{count / 1e9:.1f} GB"
    if count >= 1_000_000:
        return f"{count / 1e6:.0f} MB"
    return f"{max(1, round(count / 1e3))} KB"


_REASONS = {
    "held_out": "kept back as hidden tests", "corrected_or_stopped": "corrected, stopped, retried or denied",
    "not_failed": "failed", "not_stopped": "stopped", "not_cancelled": "cancelled", "not_finished": "unfinished",
    "reply_failed": "a reply failed", "tool_failed": "a tool failed", "too_long": "too long for the recipe",
    "opted_out": "from chats you switched off", "conversation_gone": "from deleted chats",
    "someone_else": "from chats with other people", "source_off": "from sources you switched off",
    "purged": "deleted", "personal_model": "answered by a personal model", "tool_content_in_answer":
    "repeated web or tool content", "unsupported_content": "with images or files", "partial": "incomplete records",
    "incomplete": "incomplete records", "no_target": "nothing to learn from", "past_chat_tool_step":
    "past-chat tool steps", "unrenderable": "the model's chat format couldn't hold them",
    "past_chat_waiting": "past chats waiting for a first chat here that uses tools",
}


_LOCAL_PROVIDERS = {"ollama": "Ollama (on this computer)"}


def _provider_name(provider: str, terms: Mapping[str, str]) -> str:
    if provider in terms:
        return terms[provider]
    if provider in _LOCAL_PROVIDERS:
        return _LOCAL_PROVIDERS[provider]
    if provider.startswith("custom_openai_"):
        return "your own endpoint"
    return provider


# A version whose ready check failed for one of these reasons falls back to the pass before (O119): it
# couldn't finish a short task (it looped until the step budget ended the turn, or made no call). A runtime
# that is missing or can't load it is no reason to: every pass would fail the same way.
WALK_BACK = frozenset({"tool_turn_failed"})


def pass_words(kept: Mapping[str, Any], ready: bool) -> list[str]:
    """Which pass a version of several passes kept and why, in plain words (O119), and each pass's
    validation loss (for information: it doesn't choose)."""

    trained = int(kept.get("of") or 0)
    if trained < 2:
        return []
    number = int(kept.get("pass") or trained)
    lines = []
    failed = [item for item in kept.get("failed") or () if isinstance(item, Mapping)]
    if failed:
        words = "; ".join(f"pass {int(item['pass'])} couldn't finish a short task"
                          + (f" ({int(item['tool_calls'])} tool calls)" if item.get("tool_calls") else "")
                          for item in failed)
        kept_part = f"; kept pass {number} of {trained}" if ready else ""
        lines.append(f"- {words[0].upper()}{words[1:]}{kept_part}.")
    elif ready:
        lines.append(f"- Kept pass {number} of {trained}, the last.")
    losses = list(kept.get("losses") or ())
    if any(value is not None for value in losses):
        shown = ", ".join("-" if value is None else f"{float(value):.3f}" for value in losses)
        lines.append(f"- Loss on the examples held back, by pass (for information): {shown}.")
    return lines


def _adapter_pin(recipe: Any, state: VersionState, sha: str) -> Pin:
    """The version's adapter: with several passes, the pin names the one kept (O119)."""

    kept = state.kept_pass or {}
    method = (f"{recipe.method}, pass {int(kept['pass'])} of {int(kept['of'])}"
              if int(kept.get("of") or 0) > 1 and kept.get("pass") else recipe.method)
    return Pin("adapter_model.safetensors", method, sha or None)


def model_card(release: Release, dataset: Mapping[str, Any], state: VersionState, recipe: Any,
               engine_versions: Mapping[str, str]) -> str:
    """What the version is, in plain words (D14): what it was trained on, from which providers'
    answers (D7), the versions and recipe that made it, and its tests so far."""

    from row_bot.learning.training import dataset as dataset_module
    from row_bot.learning.training import serving

    identity = release.identity
    lines = [f"# Personal model v{release.version}", ""]
    lines.append(f"Trained on this computer on {_day(state.trained_at)} by Row-Bot Learning (preview).")
    lines.append("")
    lines.append("## What it was trained on")
    examples = int(dataset.get("examples") or 0)
    conversations = len(dataset.get("conversations") or ())
    first, last = _day(dataset.get("first_at")), _day(dataset.get("last_at"))
    when = f"all on {first}" if first == last else f"{first} to {last}"
    lines.append(f"- {examples:,} examples of your own work with Row-Bot, from {conversations:,} "
                 f"conversation{'s' if conversations != 1 else ''} ({when}).")
    historical = int(dataset.get("historical") or 0)
    if historical:
        lines.append(f"- {historical:,} of them are replies from past chats.")
    if dataset.get("validation"):
        lines.append(f"- {int(dataset['validation']):,} newer examples were held back to check it.")
    left = {key: value for key, value in (dataset.get("left_out") or {}).items() if value}
    if left:
        words = ", ".join(f"{count:,} {_REASONS.get(key, key.replace('_', ' '))}" for key, count in sorted(left.items()))
        lines.append(f"- Left out: {words}.")
    lines.append("- Only the assistant's own answers and tool calls were learned from; tool and web results "
                 "were context only.")
    rendering = dataset.get("rendering") if isinstance(dataset.get("rendering"), Mapping) else {}
    derived = int(dataset.get("derived") or 0)
    if rendering.get("serve_context"):
        lines.append(f"- Examples were shown as this model is served ({int(rendering['serve_context']):,}-token "
                     "context, the tools it reaches through tool search); "
                     f"{derived:,} were rewritten that way, so they are not the main model's own words.")
    providers = [str(item) for item in dataset.get("providers") or ()]
    named = sorted({_provider_name(item, dataset_module.TERMS_PROVIDERS) for item in providers})
    lines.append(f"- Answers from: {', '.join(named) if named else 'unknown'}.")
    if dataset.get("terms_note"):
        lines.append("- Training on answers from OpenAI, Anthropic or Google models may be limited by their terms. "
                     "Check your provider's terms.")
    lines.append("")
    lines.append("## How it was made")
    if identity.base is not None:
        lines.append(f"- Starting model: {identity.base.name} at revision {identity.base.version}.")
    lines.append(f"- Recipe: {recipe.title} ({recipe.name}): LoRA rank {recipe.settings.get('rank')}, "
                 f"{recipe.max_seq_length:,}-token examples, "
                 f"{'one pass' if recipe.epochs == 1 else f'{recipe.epochs} passes'}, no reasoning.")
    lines += pass_words(state.kept_pass, state.phase == READY)
    if engine_versions:
        lines.append("- Training engine: " + ", ".join(f"{key} {value}" for key, value in sorted(engine_versions.items()))
                     + ".")
    from row_bot.learning.training import lockfile
    from row_bot.version import __version__

    lines.append(f"- Exported with llama.cpp {lockfile.LLAMA_CPP_RELEASE}; made by Row-Bot {__version__}.")
    lines.append("")
    lines.append("## Files")
    if state.gguf:
        lines.append(f"- `{state.gguf}`: ready to run ({state.quantization}, {_size(state.gguf_bytes)}).")
    lines.append(f"- `adapter/`: the adapter in Hugging Face PEFT format ({_size(state.adapter_bytes)}).")
    lines.append(f"- `{MANIFEST_FILE}`: the version's full identity.")
    lines.append("")
    lines.append("## Tests so far")
    if state.phase == READY:
        lines.append(f"- Runs in {serving.RUNTIME_NAMES.get(state.runtime, state.runtime)}: Row-Bot's check and one "
                     "real task with a tool passed.")
    lines.append("- Not yet compared with your main model. Row-Bot compares them before asking you to use it.")
    lines.append("")
    return "\n".join(lines)


# ── Making a version ────────────────────────────────────────────────────────


def _pin_file(name: str, folder: Path, file: str, version: str = "") -> Pin | None:
    from row_bot.learning.training.export import sha256_file

    path = Path(folder) / file
    if not path.is_file():
        return None
    return Pin(name=name, version=version, sha256=sha256_file(path))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


class ReleaseMaker:
    """Makes the newest finished run a version (one at a time, on its own thread)."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        jobs: Any = None,
        clock: Callable[[], float] = time.time,
        exporter: Callable[..., Any] | None = None,
        server: Callable[..., Any] | None = None,
        engine_status: Callable[[], Any] | None = None,
        weights_folder: Callable[[], tuple[Any, Path | None, int]] | None = None,
        recipe_for: Callable[[], Any] | None = None,
        runtime: Callable[[], str] | None = None,
        start_threads: bool = True,
        withdraw: Callable[..., None] | None = None,
    ) -> None:
        self._root = root
        self._jobs = jobs
        self.clock = clock
        self.exporter = exporter
        self.server = server
        self._engine_status = engine_status
        self._weights = weights_folder
        self._recipe_for = recipe_for
        self._runtime = runtime
        self._withdraw = withdraw
        self._start_threads = start_threads
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._checked: dict[int, float] = {}

    # ── Wiring (the app's defaults; tests pass their own) ───────────────

    @property
    def root(self) -> Path:
        return self._root if self._root is not None else gate.data_dir()

    @property
    def jobs(self) -> Any:
        if self._jobs is None:
            from row_bot.learning.training.jobs import TrainingJobs

            self._jobs = TrainingJobs(self._root)
        return self._jobs

    def engine(self) -> Any:
        if self._engine_status is not None:
            return self._engine_status()
        from row_bot.learning.training import engine

        return engine.status(self._root)

    def starting_model(self, job: Job | None = None) -> tuple[Any, Path | None, int]:
        if self._weights is not None:
            return self._weights()
        from row_bot.learning.training import weights

        model = weights.MODELS.get(gate.read_settings(self._root).starting_model, weights.QWEN35_9B)
        remote = _remote_record(job, self._root)
        if remote is not None:  # the model the Hugging Face run trained (C1); its files may not be here
            model = weights.MODELS.get(remote.model_id, model)
        found = weights.status(model, self._root)
        return (model, found.folder if found.state == "downloaded" else None,
                model.weights_bytes or model.download_bytes)

    def recipe(self, job: Job | None = None) -> Any:
        if self._recipe_for is not None:
            return self._recipe_for()
        from row_bot.learning.training import recipes

        remote = _remote_record(job, self._root)
        if remote is not None:
            from row_bot.learning.remote import training as remote_training

            found = remote_training.get().recipe_for(remote)
            # The GGUF the job built: the quantization this computer's card runs (catalog.serve_for).
            return replace(found, quantization=remote.quantization)
        return recipes.current(self._root)

    def chosen_runtime(self) -> str:
        if self._runtime is not None:
            return self._runtime()
        from row_bot.learning.training import serving

        return serving.effective_runtime(gate.read_settings(self._root).serve_runtime)

    # ── What is due ─────────────────────────────────────────────────────

    def busy(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def due(self) -> tuple[Job, VersionState | None] | None:
        """The finished run whose version should be made now (or checked again), if any."""

        if not self.jobs.store.exists():
            return None
        job = self.jobs.current()
        if job is None or job.state != "evaluating" or not job.version:
            return None
        state = read_state(version_dir(job.version, self.root))
        if state is None or state.job_id != job.id:
            return job, None
        if state.phase in (EXPORTING, MAKING_READY):
            return job, state  # cut short (Row-Bot closed): runs again
        if state.phase == WAITING and self.clock() - self._checked.get(job.version, 0.0) >= RECHECK_SECONDS:
            return job, state
        return None

    def tick(self) -> bool:
        """Start what is due (the runner calls this once a minute). True when something started."""

        if not gate.is_enabled(self._root) or self.busy():
            return False
        found = self.due()
        if found is None:
            return False
        job, state = found
        quiet = state is not None and state.phase == WAITING
        return self.start(job, quiet=quiet)

    def start(self, job: Job, *, quiet: bool = False, runtime: str | None = None) -> bool:
        """Make (or make again) ``job``'s version on its own thread."""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            if job.version:
                self._checked[job.version] = self.clock()
            if not self._start_threads:
                self._thread = None
                self._make(job, quiet=quiet, runtime=runtime)
                return True
            self._thread = threading.Thread(target=self._make, args=(job,), kwargs={"quiet": quiet, "runtime": runtime},
                                            name="learning-release", daemon=True)
            self._thread.start()
            return True

    def wait(self, timeout: float = 30.0) -> bool:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def shutdown(self) -> None:
        self._stop.set()
        self.wait(15.0)

    def _check(self) -> None:
        if self._stop.is_set() or not gate.is_enabled(self._root):
            raise StopExport()

    # ── The steps ───────────────────────────────────────────────────────

    def _save(self, folder: Path, state: VersionState, **changes: Any) -> VersionState:
        return write_state(folder, replace(state, updated_at=float(self.clock()), **changes))

    def _make(self, job: Job, *, quiet: bool = False, runtime: str | None = None) -> None:
        try:
            self._steps(job, quiet=quiet, runtime=runtime)
        except StopExport:
            logger.info("Learning stopped making personal model v%s; it continues later", job.version)
        except Exception:
            logger.exception("Learning couldn't make personal model v%s", job.version)

    def _steps(self, job: Job, *, quiet: bool, runtime: str | None) -> None:
        from row_bot.learning.training import jobs as jobs_module

        version = int(job.version or 1)
        folder = version_dir(version, self.root)
        recipe = self.recipe(job)
        runtime = runtime or self.chosen_runtime()
        previous = read_state(folder)
        run = jobs_module.run_dir(job.id, self.root)
        passes = _read_json(run / jobs_module.PASSES_FILE)
        if previous is None or previous.job_id != job.id:
            trained = len(passes.get("files") or ())
            kept = {"pass": trained, "of": trained, "losses": passes.get("losses") or [], "failed": []} if trained else {}
            previous = VersionState(version=version, phase=EXPORTING, job_id=job.id, runtime=runtime,
                                    quantization=recipe.quantization, trained_at=job.finished_at or job.updated_at,
                                    kept_pass=kept)
        state = previous
        while True:
            outcome, state = self._make_ready(job, state, previous, recipe, runtime, quiet, run, passes)
            previous = state  # moved to another runtime: withdrawn once, on the first try
            kept = dict(state.kept_pass or {})
            number = int(kept.get("pass") or 0)
            if outcome is None or outcome.state != "failed" or outcome.reason not in WALK_BACK or not number:
                return
            # It couldn't finish a short task: the pass before is exported and checked (O119).
            failed = [*kept.get("failed", []), {"pass": number,
                                                "tool_calls": int((outcome.tool_turn or {}).get("tool_calls") or 0)}]
            if number < 2:  # no pass could: the version couldn't start (O106)
                state = self._save(folder, read_state(folder) or state, kept_pass={**kept, "failed": failed})
                self._write_card(folder, state, recipe, self.engine())
                return
            state = self._save(folder, read_state(folder) or state, phase=EXPORTING, step="merge",
                               kept_pass={**kept, "pass": number - 1, "failed": failed})
            logger.info("Personal model v%d's pass %d couldn't finish a short task; trying pass %d", version, number,
                        number - 1)
            self._check()

    def _make_ready(self, job: Job, state: VersionState, previous: VersionState, recipe: Any, runtime: str,
                    quiet: bool, run: Path, passes: Mapping[str, Any]) -> tuple[Any, VersionState]:
        """Export the version's current pass (unless it is exported) and make it ready in ``runtime``.
        Returns the runtime's outcome (None when the export failed) and the state."""

        from row_bot.learning.training import export, serving

        version = state.version
        folder = version_dir(version, self.root)
        engine_status = self.engine()
        model, weights_folder, weights_bytes = self.starting_model(job)
        kept = state.kept_pass or {}
        number = int(kept.get("pass") or 0)
        remote = _remote_record(job, self._root)
        exported = state.gguf is not None and (folder / state.gguf).is_file() and state.phase not in (EXPORTING,)
        if not exported:
            state = self._save(folder, state, phase=EXPORTING, step="merge", failure=None, runtime=runtime,
                               needs_bytes=None, free_bytes=None, command=None)
            if number:  # the pass being made ready: the adapter saved at its end, as the engine reported it
                source = run / "checkpoints" / f"pass-{number}"
                files = (passes.get("files") or [])[number - 1]
            else:
                source = run / "checkpoints"
                files = _read_json(run / ARTIFACTS_FILE).get("files") or ()
            expected = {item.get("name"): item.get("sha256") for item in files if isinstance(item, dict)}
            try:
                adapter, adapter_bytes, adapter_sha = export.copy_adapter(source, folder / "adapter", expected)
                release = self._record(job, state, recipe, model, weights_folder, adapter_sha, run)
                state = self._save(folder, state, release_id=release.id, adapter_bytes=adapter_bytes,
                                   examples=int(_read_json(folder / DATASET_FILE).get("examples") or 0))
                if kept.get("failed"):
                    self._update_release(state, adapter=_adapter_pin(recipe, state, adapter_sha))
                if weights_folder is None and remote is None:
                    raise export.ExportError("engine_missing")
                exporter = self.exporter or (_remote_exporter(job.id, number) if remote is not None
                                             else export.export_gguf)
                result = exporter(
                    engine=engine_status, weights=weights_folder, weights_bytes=weights_bytes, adapter=adapter,
                    folder=folder, name=serving.model_name(version), quantization=recipe.quantization,
                    check=self._check, on_step=lambda step: self._step(folder, step),
                )
            except export.ExportError as error:
                if error.code == "stopped":
                    raise StopExport() from error
                current = read_state(folder) or state
                self._save(folder, current, phase=EXPORT_FAILED, failure=error.code, step=error.step,
                           needs_bytes=error.needs, free_bytes=error.free)
                logger.warning("Learning couldn't export personal model v%d (%s)", version, error.code)
                return None, state
            state = self._save(folder, read_state(folder) or state, phase=MAKING_READY, step=None,
                               gguf=result.gguf.name, gguf_bytes=result.gguf_bytes, quantization=result.quantization)
            self._update_release(state, quantization=result.quantization)
            self._write_card(folder, state, recipe, engine_status)
        self._check()
        if previous.phase == READY and previous.runtime in serving.AUTOMATED and runtime != previous.runtime:
            # Moved to another runtime: the copy Row-Bot made in its own server or Ollama goes (the file
            # stays here).
            try:
                (self._withdraw or serving.withdraw)(previous.runtime, version, root=self._root,
                                                     catalog=serving.refresh_ollama_catalog)
            except Exception:
                logger.warning("Learning couldn't take personal model v%d out of %s", version, previous.runtime,
                               exc_info=True)
        if not quiet:
            state = self._save(folder, read_state(folder) or state, phase=MAKING_READY, runtime=runtime, failure=None,
                               command=None)
        outcome = (self.server or serving.serve)(
            runtime, version=version, gguf=folder / str(state.gguf), serve_context=recipe.serve_context, folder=folder,
            base_model=weights_folder, adapter=folder / "adapter", root=self._root,
        )
        current = read_state(folder) or state
        checks = serving.describe(outcome)
        if outcome.state == "ready":
            state = self._save(folder, current, phase=READY, runtime=runtime, model_ref=outcome.model_ref, failure=None,
                               command=None, verified_at=float(self.clock()), checks=checks)
            self._update_release(state, runtime=Pin(runtime, outcome.runtime_version or ""))
            self._write_card(folder, state, recipe, engine_status)
            fresh = self.jobs.get(job.id)
            if fresh is not None and fresh.state == "evaluating":
                self.jobs.move(job.id, "ready", reason="made_ready", release_id=state.release_id or None)
            logger.info("Learning made personal model v%d ready in %s", version, runtime)
        elif outcome.state == "waiting_for_you":
            state = self._save(folder, current, phase=WAITING, runtime=runtime, failure=outcome.reason,
                               command=outcome.command, checks=checks)
        else:
            state = self._save(folder, current, phase=RUNTIME_FAILED, runtime=runtime, failure=outcome.reason,
                               command=None, checks=checks)
            logger.warning("Personal model v%d couldn't start in %s (%s)", version, runtime, outcome.reason)
        return outcome, state

    def _step(self, folder: Path, step: str) -> None:
        state = read_state(folder)
        if state is not None and state.step != step:
            self._save(folder, state, step=step)

    # ── The release record, its manifest and the card ───────────────────

    def _store(self) -> Any:
        return self.jobs.store

    def _record(self, job: Job, state: VersionState, recipe: Any, model: Any, weights_folder: Path | None,
                adapter_sha: str, run: Path) -> Release:
        """The ``Release`` (saved, and its manifest beside the files), made once per version."""

        from row_bot.learning.training import dataset as dataset_module

        store = self._store()
        folder = version_dir(state.version, self.root)
        manifest = dataset_module.read_manifest(run / "data") or _read_json(folder / DATASET_FILE)
        if manifest:
            content_free = {key: value for key, value in manifest.items()}
            (folder / DATASET_FILE).write_text(json.dumps(content_free, indent=1, sort_keys=True), encoding="utf-8")
            if (run / "data").is_dir():
                from row_bot.learning.training import kept_rows

                # The exact rows it trained on, with it (A10 uploads these, never a rebuild by a later build).
                kept_rows.keep(run / "data", folder, manifest)
        existing = store.get("release", state.release_id) if state.release_id else None
        if isinstance(existing, Release):
            return existing
        previous = [item for item in store.find("release", subkind="model", limit=10_000)
                    if isinstance(item, Release) and item.version < state.version]
        parent = max(previous, key=lambda item: item.version) if previous else None
        base = template = tokenizer = None
        if weights_folder is not None:
            shards = [item.name for item in model.files if item.name.endswith(".safetensors")]
            # One file of weights is pinned as it is; a model in shards by its index (which names them all).
            base_file = shards[0] if len(shards) == 1 else "model.safetensors.index.json" if shards else ""
            base = _pin_file(model.repo, weights_folder, base_file, model.revision)
            tokenizer = _pin_file(f"{model.repo} tokenizer", weights_folder, "tokenizer.json", model.revision)
            template = _pin_file(f"{model.repo} chat template", weights_folder, "chat_template.jinja", model.revision)
        elif _remote_record(job, self._root) is not None and model is not None:
            # Trained on Hugging Face (C1): the pins the job checked every file against, not a local hash.
            by_name = {item.name: item.sha256 for item in model.files}
            shards = [item.name for item in model.files if item.name.endswith(".safetensors")]
            base_file = shards[0] if len(shards) == 1 else "model.safetensors.index.json"
            base = Pin(name=model.repo, version=model.revision, sha256=by_name.get(base_file) or None)
            tokenizer = Pin(name=f"{model.repo} tokenizer", version=model.revision,
                            sha256=by_name.get("tokenizer.json") or None)
            template = Pin(name=f"{model.repo} chat template", version=model.revision,
                           sha256=by_name.get("chat_template.jinja") or None)
        data = None
        if manifest.get("id"):
            rendering = manifest.get("rendering") if isinstance(manifest.get("rendering"), dict) else {}
            data = DataSpec(dataset_id=str(manifest["id"]), sha256=manifest.get("sha256"),
                            examples=int(manifest.get("examples") or 0),
                            sources=("main_model_work",) if manifest.get("examples") else (),
                            providers=tuple(str(item)[:64] for item in manifest.get("providers") or ())[:64],
                            rendering=str(rendering.get("name"))[:64] if rendering.get("name") else None)
        recipe_pin = Pin(recipe.name, str(recipe.version), recipe.sha256)
        release = Release(
            id=contracts.new_id("rel"), kind="model", version=state.version, created_at=float(self.clock()),
            label=f"Personal model v{state.version}", parent_ids=(parent.id,) if parent else (),
            status="candidate",
            identity=ReleaseIdentity(
                base=base, tokenizer=tokenizer, template=template,
                adapter=_adapter_pin(recipe, state, adapter_sha),
                quantization=recipe.quantization, data=data, recipe=recipe_pin,
            ),
            source_thread_ids=tuple(str(item) for item in manifest.get("conversations") or ())[:50_000],
        )
        store.put(release)
        self.jobs.update(job.id, release_id=release.id)
        self._manifest(folder, release)
        return release

    def _manifest(self, folder: Path, release: Release) -> None:
        temporary = folder / f".{MANIFEST_FILE}.tmp"
        temporary.write_text(contracts.to_manifest(release), encoding="utf-8")
        os.replace(temporary, folder / MANIFEST_FILE)

    def _update_release(self, state: VersionState, **identity: Any) -> None:
        store = self._store()
        release = store.get("release", state.release_id) if state.release_id else None
        folder = version_dir(state.version, self.root)
        if not isinstance(release, Release):
            found = _read_json(folder / MANIFEST_FILE)
            try:
                release = contracts.from_manifest(json.dumps(found)) if found else None
            except contracts.ContractError:
                release = None
        if not isinstance(release, Release):
            return
        updated = replace(release, identity=replace(release.identity, **identity))
        if store.exists():
            store.put(updated)
        self._manifest(folder, updated)

    def _write_card(self, folder: Path, state: VersionState, recipe: Any, engine_status: Any) -> None:
        release_data = _read_json(folder / MANIFEST_FILE)
        try:
            release = contracts.from_manifest(json.dumps(release_data))
        except contracts.ContractError:
            return
        if not isinstance(release, Release):
            return
        versions: dict[str, str] = {}
        record = _read_json(Path(engine_status.folder) / "engine.json") if getattr(engine_status, "folder", None) else {}
        for name in ("torch", "transformers", "trl", "peft", "unsloth"):
            value = (record.get("packages") or {}).get(name)
            if value:
                versions[name] = str(value)
        text = model_card(release, _read_json(folder / DATASET_FILE), state, recipe, versions)
        (folder / MODEL_CARD_FILE).write_text(text, encoding="utf-8", newline="\n")


def _remote_record(job: Job | None, root: Path | None) -> Any:
    """A Hugging Face run's record (C1), or None for a run on this computer."""

    if job is None or getattr(job, "backend", "local") != "hugging_face":
        return None
    from row_bot.learning.remote import training as remote_training

    return remote_training.read_record(job.id, root)


def _remote_exporter(job_id: str, number: int) -> Callable[..., Any]:
    """The export step of a Hugging Face version: the job's GGUF for this pass, downloaded and checked."""

    def exporter(*, adapter: Path, folder: Path, quantization: str, check: Callable[[], None] = lambda: None,
                 on_step: Callable[[str], None] = lambda _step: None, **_ignored: Any) -> Any:
        from row_bot.learning.remote import training as remote_training
        from row_bot.learning.training import export
        from row_bot.learning.training.fetch import FetchError

        on_step("download")
        started = time.monotonic()
        try:
            gguf, size, sha = remote_training.download_gguf(job_id, number, folder, check=check)
        except FetchError as error:
            raise export.ExportError("stopped" if error.code == "stopped" else "download_failed",
                                     step="download") from error
        except Exception as error:  # noqa: BLE001 - signed out, gone, no answer: the person can try again
            raise export.ExportError("download_failed", step="download") from error
        adapter_bytes = sum(item.stat().st_size for item in Path(adapter).iterdir() if item.is_file())
        return export.ExportResult(gguf=gguf, quantization=quantization, gguf_bytes=size, gguf_sha256=sha,
                                   adapter=Path(adapter), adapter_bytes=adapter_bytes,
                                   adapter_sha256=export.sha256_file(Path(adapter) / export.ADAPTER_FILES[0]),
                                   seconds=round(time.monotonic() - started, 1))

    return exporter


__all__ = [
    "EXPORTING",
    "EXPORT_FAILED",
    "MAKING_READY",
    "MANIFEST_FILE",
    "MODEL_CARD_FILE",
    "READY",
    "RUNTIME_FAILED",
    "ReleaseMaker",
    "StopExport",
    "VersionState",
    "WAITING",
    "latest_state",
    "model_card",
    "read_state",
    "trained_example_ids",
    "version_dir",
    "versions_dir",
    "write_state",
]
