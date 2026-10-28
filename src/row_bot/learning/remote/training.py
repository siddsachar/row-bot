"""Training a personal model on Hugging Face (C1, D4, O130-O136): beside the local backend, judged the same way.

A run on Hugging Face is a training job like any other (``training/jobs.py``, backend ``hugging_face``): planned
from the same dataset in the same serving shape (O71/O96) with a Hugging Face recipe (``recipes/hf-*.json``: the
same trainer settings and passes), and its finished version goes through the same release path
(``training/releases.py``: the adapter checked, the GGUF checked, Row-Bot's ready check in its own server, the
comparison and the person's OK before use, D24). Only where it trains differs:

1. **Quote** (:meth:`RemoteTrainer.quote`): the GPU flavour and its price (Hugging Face's live prices, else the
   recipe's), the time and cost from the recipe's measured times, the most it can cost (the job's timeout, which
   the month's cap also bounds), what is uploaded (how many examples, how big) and where (a private dataset in
   the person's account, deleted after, O133).
2. **Start** (:meth:`start`, on its own thread): the private dataset and model repositories are made, the rows and
   Row-Bot's engine files uploaded, and the job submitted with a label holding Row-Bot's job id **before** its
   answer is known, so a submission whose answer never came is found again by that label and never sent twice
   (:meth:`_reconcile`). Preparing → running (the job's durable id saved) or → unknown remote.
3. **While it runs** (:meth:`tick`, from the quiet-hours runner, about once a minute): the job is read and its log's
   ``ROWBOT`` lines give the stage and the training steps. No answer from Hugging Face moves it to **unknown
   remote** ("Checking whether Hugging Face is still training…", never "Failed" while unknown); the next answer
   settles it. Cancel cancels the job on Hugging Face first.
4. **After**: the manifest (``outputs.json``: every file's size and SHA-256) and each pass's adapter are downloaded
   and checked, then the job moves to evaluating; the version's GGUF is downloaded by the release maker
   (:func:`download_gguf`, the last pass first, an earlier one only if needed, O119) through Row-Bot's resumable
   downloader. Once the version is ready (or settled), the uploaded data and the run's files are deleted on
   Hugging Face (:meth:`_clean_up`), and Learning says so.

**Spend** (O132, O135): each run's cost (Hugging Face bills per minute while a job starts and runs; Row-Bot counts
the job's own running time at the flavour's price) goes into ``learning_models/remote/spend.json``; a run starts
only when the month's spend plus its quote fits the person's monthly cap, and its timeout never lets it pass the
cap. The first run always asks (the quote's Start); later ones run in quiet hours within the cap.

Nothing here runs unless the person turned on Learning, chose Hugging Face and signed in (``remote/auth.py``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from row_bot import learning as gate
from row_bot.learning import atomic
from row_bot.learning.contracts import Job
from row_bot.learning.remote import hub as hub_module
from row_bot.learning.remote.hub import HubError

logger = logging.getLogger(__name__)

REMOTE_FILE = "remote.json"
SPEND_FOLDER = "remote"
SPEND_FILE = "spend.json"
# The job's own files Row-Bot uploads beside the rows (what runs on Hugging Face's machine).
ENGINE_FILES = ("entry.py", "trainer.py")
# The bootstrap's pins (the job installs exactly these to fetch its inputs; the engine comes from its own lock).
HUB_PIN = "huggingface_hub==1.27.0"
XET_PIN = "hf_xet==1.6.0"
UV_PIN = "0.12.22"
# A run whose submission got no answer is "not started" only after this long with no job found by its label.
NOT_STARTED_SECONDS = 300.0
NOT_STARTED_CHECKS = 3
# The timeout: the quote's time with room for a slow day, at least 20 minutes more.
TIMEOUT_FACTOR = 1.5
TIMEOUT_MARGIN_SECONDS = 1200
# Model download speed inside a job (bytes a second), for the quote: measured on Hugging Face (C1).
MODEL_BYTES_PER_SECOND = 150_000_000
# Files up to this size are read whole (Hugging Face serves small files itself, without a storage address).
SMALL_FILE_BYTES = 20_000_000


class RemoteError(RuntimeError):
    """A refused Hugging Face action; ``code`` is stable: ``signed_out``, ``no_model`` (nothing this computer
    can run), ``over_cap`` (the month's cap can't hold the run), ``not_confirmed`` (the first run asks),
    ``busy`` (a run is already starting), ``nothing_to_train``, ``no_credit``, ``network``, ``unavailable``."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# ── Spend ───────────────────────────────────────────────────────────────────


def _month(at: float) -> str:
    return datetime.fromtimestamp(at, tz=timezone.utc).astimezone().strftime("%Y-%m")


def spend_path(root: Path | None = None) -> Path:
    from row_bot.learning.training import jobs as jobs_module

    return jobs_module.models_dir(root) / SPEND_FOLDER / SPEND_FILE


def read_spend(root: Path | None = None) -> list[dict[str, Any]]:
    try:
        value = json.loads(spend_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    runs = value.get("runs") if isinstance(value, dict) else None
    return [item for item in runs or [] if isinstance(item, dict) and isinstance(item.get("job_id"), str)]


def record_spend(job_id: str, *, cost_usd: float, seconds: int, flavor: str, at: float,
                 final: bool, root: Path | None = None) -> None:
    """A run's cost so far (or final): one entry per run, its month the run's start."""

    runs = read_spend(root)
    found = next((item for item in runs if item["job_id"] == job_id), None)
    if found is None:
        found = {"job_id": job_id, "month": _month(at)}
        runs.append(found)
    found.update({"cost_usd": round(max(0.0, float(cost_usd)), 4), "seconds": int(seconds), "flavor": flavor,
                  "final": bool(final)})
    path = spend_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps({"runs": runs[-500:]}, indent=1, sort_keys=True))


def month_spent(at: float, root: Path | None = None) -> float:
    month = _month(at)
    return round(sum(float(item.get("cost_usd") or 0) for item in read_spend(root) if item.get("month") == month), 4)


def run_cost(job_id: str, root: Path | None = None) -> dict[str, Any] | None:
    return next((item for item in read_spend(root) if item["job_id"] == job_id), None)


# ── The quote ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Quote:
    """What a run will be, in the person's terms (the quote sheet and the Personal model view)."""

    model_id: str
    model_name: str
    flavor: str
    flavor_label: str
    gpu_memory_gb: int | None
    price_per_hour: float
    seconds: int
    cost_usd: float
    # The job's timeout and what it could cost at most: Hugging Face stops the job there.
    timeout_seconds: int
    max_cost_usd: float
    examples: int
    upload_bytes: int
    download_bytes: int
    dataset_repo: str
    quantization: str
    measured: bool
    month_spent_usd: float
    cap_usd: int
    fits_cap: bool
    # Every card the recipe may train on, each with its own time and price (the quote sheet's choice).
    choices: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def cost(seconds: float, price_per_hour: float) -> float:
    """Hugging Face bills by the minute: ``seconds`` rounded up to whole minutes at the hourly price."""

    return round(math.ceil(max(0.0, float(seconds)) / 60.0) * float(price_per_hour) / 60.0, 4)


# ── Run records ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunRecord:
    """A run's Hugging Face side, beside its job (``runs/<job>/remote.json``): never a token."""

    job_id: str
    phase: str = "planned"  # uploading · submitting · running · downloading · downloaded · cleaning · cleaned
    namespace: str = ""
    dataset_repo: str = ""
    output_repo: str = ""
    remote_id: str | None = None
    flavor: str = ""
    price_per_hour: float = 0.0
    # Hugging Face's own stage for the job (``SCHEDULING`` while it waits for a free card).
    remote_stage: str = ""
    model_id: str = ""
    quantization: str = "Q8_0"
    timeout_seconds: int = 0
    submit_started_at: float | None = None
    not_found_checks: int = 0
    last_contact_at: float | None = None
    lost_since: float | None = None
    stage: str | None = None
    step: int = 0
    steps_total: int = 0
    running_seconds: int = 0
    cost_usd: float = 0.0
    outcome: str | None = None  # completed · error · canceled · not_started
    failure: str | None = None
    outputs: tuple[dict[str, Any], ...] = ()
    base_gguf: bool = False
    cleaned_at: float | None = None
    cleanup_failed: bool = False
    timings: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["outputs"] = list(self.outputs)
        data["timings"] = dict(self.timings)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RunRecord | None:
        try:
            known = {key: data[key] for key in cls.__dataclass_fields__ if key in data}
            known["outputs"] = tuple(item for item in known.get("outputs") or () if isinstance(item, dict))
            known["timings"] = dict(known.get("timings") or {})
            return cls(**known)
        except (TypeError, ValueError):
            return None


def record_path(job_id: str, root: Path | None = None) -> Path:
    from row_bot.learning.training import jobs as jobs_module

    return jobs_module.run_dir(job_id, root) / REMOTE_FILE


def read_record(job_id: str, root: Path | None = None) -> RunRecord | None:
    try:
        return RunRecord.from_dict(json.loads(record_path(job_id, root).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def write_record(record: RunRecord, root: Path | None = None) -> RunRecord:
    path = record_path(record.job_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps(record.to_dict(), indent=1, sort_keys=True))
    return record


def parse_lines(lines: list[str]) -> list[dict[str, Any]]:
    """The job's own ``ROWBOT {...}`` lines (``remote/hf_job.py``), in order; anything else is ignored."""

    found = []
    for line in lines:
        text = str(line)
        start = text.find("ROWBOT ")
        if start < 0:
            continue
        try:
            value = json.loads(text[start + len("ROWBOT "):].strip())
        except ValueError:
            continue
        if isinstance(value, dict) and isinstance(value.get("type"), str):
            found.append(value)
    return found


# ── The backend ─────────────────────────────────────────────────────────────


def _code_folder() -> Path:
    return Path(__file__).resolve().parent


def _engine_folder() -> Path:
    return Path(__file__).resolve().parents[2] / "learning_engine"


def bootstrap_command() -> list[str]:
    """The job's first command: fetch its private inputs (with the person's token, from the job's secrets) and
    run ``hf_job.py`` from them. Nothing else is installed before the engine's own lock."""

    code = (
        "import os,subprocess,sys;"
        f"subprocess.run([sys.executable,'-m','pip','install','-q','--no-input','{HUB_PIN}','{XET_PIN}'],check=True);"
        "os.environ['HF_HUB_DISABLE_TELEMETRY']='1';"
        "from huggingface_hub import snapshot_download;"
        "snapshot_download(os.environ['ROW_BOT_INPUT_REPO'],repo_type='dataset',local_dir='/work/in',"
        "token=os.environ['HF_TOKEN']);"
        "os.execv(sys.executable,[sys.executable,'/work/in/code/hf_job.py'])"
    )
    return ["python", "-c", code]


class RemoteTrainer:
    """The Hugging Face backend: one run at a time. ``hub`` is the Hub client (tests: a fake); ``fetcher`` the
    resumable downloader; ``card_mib`` the graphics card's size (the model must run on it after)."""

    def __init__(self, root: Path | None = None, *, jobs: Any = None, hub: Any = None, sign_in: Any = None,
                 clock: Callable[[], float] = time.time, fetcher: Any = None,
                 card_mib: Callable[[], int | None] | None = None, start_threads: bool = True,
                 flavors: Callable[[], list[Any]] | None = None) -> None:
        self._root = root
        self._jobs = jobs
        self._hub = hub
        self._sign_in = sign_in
        self.clock = clock
        self._fetcher = fetcher
        self._card_mib = card_mib
        self._start_threads = start_threads
        self._flavors = flavors
        self._lock = threading.RLock()
        self._work: threading.Thread | None = None
        self._prices: tuple[float, dict[str, Any]] | None = None
        # The dataset (``planner.LearningDataset`` over the Hugging Face planner, set by the runner): a run that
        # hasn't started takes the recipe as it is now, and gets its rows if it was planned without them (O117).
        self.dataset: Any = None

    # ── Wiring ──────────────────────────────────────────────────────────

    @property
    def root(self) -> Path:
        return self._root if self._root is not None else gate.data_dir()

    @property
    def jobs(self) -> Any:
        if self._jobs is None:
            from row_bot.learning.training.jobs import TrainingJobs

            self._jobs = TrainingJobs(self._root)
        return self._jobs

    def sign_in(self) -> Any:
        if self._sign_in is not None:
            return self._sign_in
        from row_bot.learning.remote import auth

        return auth.get()

    def hub(self) -> Any:
        if self._hub is not None:
            return self._hub
        sign_in = self.sign_in()
        return hub_module.HfHub(sign_in.token, namespace=lambda: sign_in.status().user)

    def card_mib(self) -> int | None:
        if self._card_mib is not None:
            return self._card_mib()
        from row_bot.learning.training import hardware

        # The card decides what can run here after (O131): asked once a process (``nvidia-smi``, at most a few
        # seconds), then kept.
        found = hardware.check()
        gpu = getattr(found, "gpu", None)
        return int(gpu.memory_mib) if gpu is not None else None

    def fetcher(self) -> Any:
        if self._fetcher is not None:
            return self._fetcher
        from row_bot.learning.training import fetch

        return fetch.Fetcher()

    # ── What it would train ─────────────────────────────────────────────

    def signed_in(self) -> bool:
        try:
            return self.sign_in().status().state == "signed_in"
        except Exception:  # noqa: BLE001
            return False

    def models(self) -> tuple[Any, ...]:
        from row_bot.learning.training import catalog

        return catalog.for_hugging_face(self.card_mib())

    def choice(self) -> Any:
        """The model the person chose (else the strongest this computer can run), or None."""

        offered = self.models()
        chosen = gate.read_settings(self._root).hf_model
        return next((model for model in offered if model.id == chosen), offered[0] if offered else None)

    def recipe(self) -> Any:
        """The Hugging Face recipe of the chosen model, with the person's Advanced choices (D9)."""

        from row_bot.learning.training import advanced, recipes

        model = self.choice()
        if model is None or not model.remote_recipe:
            raise RemoteError("no_model")
        return advanced.apply(recipes.load(model.remote_recipe), self._root)

    def measure(self, rows: list[Any], max_seq: int) -> list[tuple[int, int]]:
        """The Hugging Face planner's exact row lengths (``measure_rows``) for the chosen model."""

        model = self.choice()
        if model is None or model.pinned is None:
            raise RemoteError("no_model")
        return measure_rows(rows, max_seq, model=model.pinned, root=self._root)

    def ready(self) -> bool:
        """The planner's "engine" for a Hugging Face run: signed in, with a model this computer can run."""

        return self.signed_in() and self.choice() is not None

    # ── Prices and the quote ────────────────────────────────────────────

    def prices(self) -> dict[str, Any]:
        """Hugging Face's live Jobs prices by flavour (read at most every 6 hours; the recipe's when unreadable)."""

        with self._lock:
            if self._prices is not None and self.clock() - self._prices[0] < 6 * 3600:
                return self._prices[1]
        try:
            items = self._flavors() if self._flavors is not None else self._public_flavors()
            found = {item.name: item for item in items}
        except Exception:  # noqa: BLE001 - the recipe's price stands in
            logger.debug("Learning couldn't read Hugging Face's prices", exc_info=True)
            found = {}
        with self._lock:
            if found:
                self._prices = (self.clock(), found)
        return found

    def _public_flavors(self) -> list[Any]:
        """``GET /api/jobs/hardware`` needs no sign-in: the quote can show before the person signs in."""

        import httpx

        response = httpx.get(f"{hub_module.ENDPOINT}/api/jobs/hardware", timeout=15.0, trust_env=False,
                             headers={"User-Agent": "Row-Bot"})
        response.raise_for_status()
        items = []
        for item in response.json():
            accelerator = item.get("accelerator") or {}
            vram = "".join(ch for ch in str(accelerator.get("vram") or "") if ch.isdigit())
            unit = str(item.get("unitLabel") or "minute")
            unit_cost = float(item.get("unitCostUSD") or 0.0)
            items.append(hub_module.Flavor(str(item.get("name")), str(item.get("prettyName") or item.get("name")),
                                           int(vram) if vram else None,
                                           unit_cost / 60.0 if unit == "hour" else unit_cost))
        return items

    def flavors(self, recipe: Any = None) -> list[dict[str, Any]]:
        """The cards the recipe may train on: its own first, then its reviewed alternatives (each with the
        main card's times unless it gives its own)."""

        remote = dict((recipe or self.recipe()).remote or {})
        main = {key: remote[key] for key in ("flavor", "price_per_hour", "seconds_per_step")}
        return [main, *({**main, **dict(item)} for item in remote.get("alternatives") or ())]

    def flavor(self, recipe: Any = None) -> dict[str, Any]:
        """The card a run uses: the person's choice when the recipe offers it, else the recipe's own."""

        offered = self.flavors(recipe)
        chosen = gate.read_settings(self._root).hf_flavor
        return next((item for item in offered if item["flavor"] == chosen), offered[0])

    def quote(self, *, examples: int, upload_bytes: int, steps: int | None = None,
              flavor: str | None = None) -> Quote:
        """What a run of ``examples`` rows costs, in time and money, and what it uploads (no network but the
        public price list), on ``flavor`` or the chosen card; ``choices`` quotes every card it may use."""

        model = self.choice()
        if model is None:
            raise RemoteError("no_model")
        recipe = self.recipe()
        offered = self.flavors(recipe)
        card = next((item for item in offered if item["flavor"] == flavor), None) or self.flavor(recipe)
        remote = {**dict(recipe.remote or {}), **card}
        flavor_name = str(remote["flavor"])
        live = self.prices().get(flavor_name)
        price = live.price_per_hour if live is not None and live.price_per_minute > 0 else float(remote["price_per_hour"])
        steps = int(steps or recipe.steps_for(examples))
        model_bytes = model.pinned.download_bytes if model.pinned is not None else int(model.params_b * 2e9)
        option = model.serve_for(self.card_mib())
        quantization = option.quantization if option is not None else recipe.quantization
        seconds = int(float(remote["setup_seconds"]) + model_bytes / MODEL_BYTES_PER_SECOND
                      + steps * float(remote["seconds_per_step"])
                      + recipe.epochs * float(remote["export_seconds_per_pass"]) + float(remote["upload_seconds"]))
        settings = gate.read_settings(self._root)
        spent = month_spent(self.clock(), self._root)
        cap = int(settings.hf_monthly_cap_usd)
        timeout = int(seconds * TIMEOUT_FACTOR + TIMEOUT_MARGIN_SECONDS)
        if cap and price > 0:
            timeout = min(timeout, int(max(0.0, cap - spent) / price * 3600))
        user = self._namespace() or "you"
        return Quote(
            model_id=model.id, model_name=model.name, flavor=flavor_name,
            flavor_label=live.label if live is not None else flavor_name,
            gpu_memory_gb=live.gpu_memory_gb if live is not None else None, price_per_hour=round(price, 4),
            seconds=seconds, cost_usd=cost(seconds, price), timeout_seconds=max(0, timeout),
            max_cost_usd=cost(max(0, timeout), price), examples=int(examples), upload_bytes=int(upload_bytes),
            download_bytes=int(option.gguf_bytes if option is not None else 0), dataset_repo=f"{user}/row-bot-training-…",
            quantization=quantization, measured=bool(recipe.certified), month_spent_usd=spent, cap_usd=cap,
            fits_cap=bool(cap) and spent + cost(seconds, price) <= cap and timeout >= seconds,
            choices=tuple(self._choice(item, recipe, steps, model_bytes) for item in offered)
            if flavor is None else (),
        )

    def _choice(self, card: dict[str, Any], recipe: Any, steps: int, model_bytes: int) -> dict[str, Any]:
        remote = {**dict(recipe.remote or {}), **card}
        live = self.prices().get(str(card["flavor"]))
        price = live.price_per_hour if live is not None and live.price_per_minute > 0 else float(card["price_per_hour"])
        seconds = int(float(remote["setup_seconds"]) + model_bytes / MODEL_BYTES_PER_SECOND
                      + steps * float(remote["seconds_per_step"])
                      + recipe.epochs * float(remote["export_seconds_per_pass"]) + float(remote["upload_seconds"]))
        return {"flavor": str(card["flavor"]), "label": live.label if live is not None else str(card["flavor"]),
                "gpu_memory_gb": live.gpu_memory_gb if live is not None else None, "price_per_hour": round(price, 4),
                "seconds": seconds, "cost_usd": cost(seconds, price)}

    def _namespace(self) -> str:
        try:
            return str(self.sign_in().status().user or "")
        except Exception:  # noqa: BLE001
            return ""

    # ── Starting a run ──────────────────────────────────────────────────

    def busy(self) -> bool:
        with self._lock:
            return self._work is not None and self._work.is_alive()

    def start(self, job: Job, trigger: str, *, confirmed: bool = False) -> Job:
        """Start ``job`` on Hugging Face: checks first (signed in, a model this computer runs, the first run asked,
        the month's cap), then the upload and the submission on their own thread. Returns the job (preparing)."""

        settings = gate.read_settings(self._root)
        if not self.signed_in():
            raise RemoteError("signed_out")
        if self.choice() is None:
            raise RemoteError("no_model")
        if not settings.hf_runs_confirmed and not confirmed:
            raise RemoteError("not_confirmed")
        job = self._prepare(job)
        examples, upload_bytes = self._upload_size(job)
        quote = self.quote(examples=examples, upload_bytes=upload_bytes, steps=job.steps_total)
        if not quote.fits_cap:
            raise RemoteError("over_cap")
        with self._lock:
            if self.busy():
                raise RemoteError("busy")
            moved = self.jobs.move(job.id, "preparing", reason=trigger, trigger=trigger, steps_done=0,
                                   expect=("planned", "waiting"))
            user = self._namespace()
            short = job.id.removeprefix("job_")[:12]
            record = write_record(RunRecord(
                job_id=job.id, phase="uploading", namespace=user,
                dataset_repo=f"{user}/row-bot-training-{short}", output_repo=f"{user}/row-bot-personal-{short}",
                flavor=quote.flavor, price_per_hour=quote.price_per_hour, model_id=quote.model_id,
                quantization=quote.quantization, timeout_seconds=quote.timeout_seconds,
                steps_total=int(job.steps_total or 0), base_gguf=self._needs_base(quote.model_id),
            ), self._root)
            self._run_work(self._submit, moved, record)
        _changed()
        return moved

    def _prepare(self, job: Job) -> Job:
        """The run's rows in its own folder (its lease, written once) and the recipe as it is now."""

        from row_bot.learning.training import jobs as jobs_module

        if self.dataset is None:
            return job
        try:
            job = self.dataset.starting(job)
        except Exception:  # noqa: BLE001 - it starts as planned
            logger.warning("Learning couldn't bring a planned Hugging Face run up to date", exc_info=True)
        run = jobs_module.run_dir(job.id, self._root)
        data = run / "data"
        if not (data / "examples.jsonl").is_file():
            data.mkdir(parents=True, exist_ok=True)
            try:
                examples = int(self.dataset.prepare(job, data))
            except Exception as error:  # noqa: BLE001
                raise RemoteError("nothing_to_train") from error
            (run / "lease.json").write_text(json.dumps({"examples": examples}), encoding="utf-8")
        return self.jobs.get(job.id) or job

    def _run_work(self, target: Callable[..., None], *args: Any) -> None:
        if not self._start_threads:
            target(*args)
            return
        self._work = threading.Thread(target=target, args=args, name="learning-hugging-face", daemon=True)
        self._work.start()

    def _needs_base(self, model_id: str) -> bool:
        """Whether the job also builds the untouched starting model's GGUF (for v1's comparison, L2c2): when
        this computer has no copy of it."""

        from row_bot.learning.training import base_model

        state = base_model.read(model_id, self._root)
        folder = base_model.folder(model_id, self._root)
        return not (state is not None and state.gguf and (folder / state.gguf).is_file())

    def _upload_size(self, job: Job) -> tuple[int, int]:
        from row_bot.learning.training import jobs as jobs_module

        data = jobs_module.run_dir(job.id, self._root) / "data"
        files = [data / "examples.jsonl", data / "validation.jsonl"]
        rows = 0
        try:
            with open(files[0], "rb") as handle:
                rows = sum(1 for line in handle if line.strip())
        except OSError:
            rows = int((self.jobs.plan_of(job).get("dataset") or {}).get("examples") or 0)
        size = sum(path.stat().st_size for path in files if path.is_file())
        return rows, size

    def _plan(self, job: Job, record: RunRecord) -> dict[str, Any]:
        from row_bot.learning.training import catalog, lockfile

        model = catalog.get(record.model_id)
        if model is None or model.pinned is None:
            raise RemoteError("no_model")
        plan = self.jobs.plan_of(job)
        examples, _size = self._upload_size(job)
        tools = lockfile.LLAMA_CPP_TOOLS["linux-x64"]
        return {
            "job_id": job.id, "version": int(job.version or 1), "steps_total": int(job.steps_total or 1),
            "checkpoint_every": max(1, min(int(plan["checkpoint_every"]), int(job.steps_total or 1))),
            "examples": examples, "recipe": plan["recipe"], "quantization": record.quantization,
            "model": {"repo": model.pinned.repo, "revision": model.pinned.revision,
                      "files": [{"name": item.name, "size": item.size, "sha256": item.sha256}
                                for item in model.pinned.files]},
            "llama": {"tools": {"url": tools.url, "sha256": tools.sha256},
                      "source": {"url": lockfile.LLAMA_CPP_SOURCE.url, "sha256": lockfile.LLAMA_CPP_SOURCE.sha256}},
            "uv": UV_PIN, "output_repo": record.output_repo, "base_gguf": record.base_gguf,
        }

    def _files(self, job: Job, plan: dict[str, Any], scratch: Path) -> dict[str, Path]:
        from row_bot.learning.training import jobs as jobs_module
        from row_bot.learning.training import protocol

        data = jobs_module.run_dir(job.id, self._root) / "data"
        job_file = scratch / "job.json"
        job_file.write_text(json.dumps(plan, indent=1, sort_keys=True), encoding="utf-8")
        files = {"job.json": job_file, "data/examples.jsonl": data / "examples.jsonl",
                 "code/hf_job.py": _code_folder() / "hf_job.py", "code/protocol.py": Path(protocol.__file__),
                 "code/engine/pyproject.toml": _engine_folder() / "pyproject.toml",
                 "code/engine/uv.lock": _engine_folder() / "uv.lock"}
        for name in ENGINE_FILES:
            files[f"code/{name}"] = _engine_folder() / name
        if (data / "validation.jsonl").is_file():
            files["data/validation.jsonl"] = data / "validation.jsonl"
        return files

    def _submit(self, job: Job, record: RunRecord) -> None:
        """Upload, then submit with the job's label saved first (so an unanswered submission is found, not sent
        again). Runs on the trainer's own thread."""

        import tempfile

        hub = self.hub()
        try:
            plan = self._plan(job, record)
            if record.submit_started_at is None:
                hub.create_private_repo(record.dataset_repo, "dataset")
                hub.create_private_repo(record.output_repo, "model")
                with tempfile.TemporaryDirectory(prefix="row-bot-hf-") as folder:
                    hub.upload(record.dataset_repo, "dataset", self._files(job, plan, Path(folder)))
        except (HubError, RemoteError, OSError) as error:
            code = getattr(error, "code", "network")
            logger.warning("Learning couldn't upload a Hugging Face run's data (%s)", code)
            self._fail(job.id, "upload_failed" if code == "network" else code, record)
            self._clean_up(record)
            return
        record = write_record(replace(record, phase="submitting", submit_started_at=self.clock()), self._root)
        try:
            submitted = hub.run_job(
                image=self.recipe_for(record).remote["image"], command=bootstrap_command(),
                env={"ROW_BOT_INPUT_REPO": record.dataset_repo, **hub_module.SWITCHES},
                secrets={"HF_TOKEN": self.sign_in().token(valid_for=record.timeout_seconds + 1800)},
                flavor=record.flavor,
                timeout_seconds=record.timeout_seconds, labels=hub_module.labels_for(job.id))
        except HubError as error:
            if error.code == "network":
                # No answer: the job may or may not exist. Found by its label later; never sent again blindly.
                write_record(replace(record, phase="submitting", lost_since=self.clock()), self._root)
                self._move(job.id, "unknown_remote", reason="no_answer")
                return
            self._fail(job.id, error.code, record)
            self._clean_up(record)
            return
        except Exception:  # noqa: BLE001 - anything else: treat as unknown, never resubmit
            logger.warning("Learning's Hugging Face submission had no clear answer", exc_info=True)
            write_record(replace(record, phase="submitting", lost_since=self.clock()), self._root)
            self._move(job.id, "unknown_remote", reason="no_answer")
            return
        write_record(replace(record, phase="running", remote_id=submitted.id, last_contact_at=self.clock(),
                             stage=submitted.stage), self._root)
        self._move(job.id, "running", reason="submitted", remote_request_id=submitted.id, started_at=self.clock(),
                   progress_at=self.clock())

    def recipe_for(self, record: RunRecord) -> Any:
        from row_bot.learning.training import catalog, recipes

        model = catalog.get(record.model_id)
        return recipes.load(model.remote_recipe if model is not None and model.remote_recipe else "hf-9b")

    # ── Moves ───────────────────────────────────────────────────────────

    def _move(self, job_id: str, to: str, *, reason: str, **changes: Any) -> Job | None:
        from row_bot.learning.training.jobs import JobStateError

        try:
            moved = self.jobs.move(job_id, to, reason=reason, **changes)
        except JobStateError:
            logger.debug("Learning's Hugging Face run moved meanwhile", exc_info=True)
            return None
        _changed()
        return moved

    def _fail(self, job_id: str, reason: str, record: RunRecord | None = None) -> None:
        if record is not None:
            write_record(replace(read_record(job_id, self._root) or record, failure=reason), self._root)
        self._move(job_id, "failed", reason=reason, error_code=reason[:64])

    # ── While it runs ───────────────────────────────────────────────────

    def tick(self) -> None:
        """Read every unsettled Hugging Face run once (the runner calls this about once a minute)."""

        if not self.jobs.store.exists():
            return
        for job in self.jobs.all()[:20]:
            if job.backend != "hugging_face":
                continue
            record = read_record(job.id, self._root)
            if record is None:
                continue
            try:
                if job.state in ("running", "unknown_remote"):
                    self._read_job(job, record)
                elif job.state == "preparing" and not self.busy():
                    self._resume_preparing(job, record)
                elif record.cleaned_at is None and record.phase not in ("uploading", "submitting"):
                    self._maybe_clean(job, record)
            except Exception:  # noqa: BLE001 - one run's trouble never stops the others
                logger.exception("Learning couldn't read a Hugging Face run")

    def check_now(self, job_id: str) -> Job | None:
        """Check now: read the run at once (the "Checking whether Hugging Face is still training…" state)."""

        job = self.jobs.get(job_id)
        record = read_record(job_id, self._root)
        if job is None or record is None:
            return job
        if job.state in ("running", "unknown_remote"):
            self._read_job(job, record)
        return self.jobs.get(job_id)

    def _resume_preparing(self, job: Job, record: RunRecord) -> None:
        """Row-Bot closed while a run was starting: the upload again (repositories made idempotently), or, when
        its submission had begun, find it by its label (never a second job)."""

        if record.submit_started_at is None:
            with self._lock:
                self._run_work(self._submit, job, record)
            return
        self._move(job.id, "unknown_remote", reason="no_answer")

    def _read_job(self, job: Job, record: RunRecord) -> None:
        hub = self.hub()
        if record.remote_id is None:
            self._reconcile(job, record)
            return
        try:
            remote = hub.job(record.remote_id)
            lines = hub.log_lines(record.remote_id, tail=400)
        except HubError as error:
            if error.code in ("network", "rate_limited"):
                self._lost(job, record)
                return
            if error.code == "not_found":
                remote = hub_module.RemoteJob(record.remote_id, "DELETED")
                lines = []
            else:
                self._lost(job, record)
                return
        now = self.clock()
        events = parse_lines(lines)
        stage, step, total, failure = record.stage, record.step, record.steps_total, None
        for event in events:
            if event["type"] == "stage":
                stage = str(event.get("stage") or stage)[:32]
            elif event["type"] == "progress":
                step, total = int(event.get("step") or step), int(event.get("steps_total") or total)
            elif event["type"] == "error":
                failure = str(event.get("reason") or "remote_error")[:64]
            elif event["type"] == "done":
                stage = "done"
        seconds = int(remote.running_seconds or record.running_seconds or 0)
        spent = cost(seconds, record.price_per_hour)
        record = write_record(replace(record, last_contact_at=now, lost_since=None, stage=stage, step=step,
                                      remote_stage=str(remote.stage or "")[:32],
                                      steps_total=total or record.steps_total, running_seconds=seconds,
                                      cost_usd=spent), self._root)
        record_spend(job.id, cost_usd=spent, seconds=seconds, flavor=record.flavor,
                     at=job.started_at or job.created_at, final=remote.terminal, root=self._root)
        if job.state == "unknown_remote" and not remote.terminal:
            self._move(job.id, "running", reason="contact_back", remote_request_id=record.remote_id)
            job = self.jobs.get(job.id) or job
        if not remote.terminal:
            if step != job.steps_done:
                self.jobs.update(job.id, steps_done=min(step, int(job.steps_total or step)), progress_at=now)
                _changed()
            return
        if remote.stage == "COMPLETED" and stage == "done":
            write_record(replace(record, outcome="completed", phase="downloading"), self._root)
            with self._lock:
                if not self.busy():
                    self._run_work(self._download, job.id)
            return
        if remote.stage == "CANCELED":
            outcome = "canceled"
        else:
            outcome = "error"
        write_record(replace(record, outcome=outcome, failure=failure or outcome), self._root)
        if job.state == "unknown_remote":
            self._move(job.id, "running", reason="contact_back")
        self._move(job.id, "stopped" if outcome == "canceled" else "failed",
                   reason=failure or ("canceled" if outcome == "canceled" else "remote_error"),
                   **({} if outcome == "canceled" else {"error_code": (failure or "remote_error")[:64]}))
        self._clean_up(read_record(job.id, self._root) or record)

    def _lost(self, job: Job, record: RunRecord) -> None:
        """No answer from Hugging Face: the run is unknown (never failed) until the next answer."""

        if record.lost_since is None:
            write_record(replace(record, lost_since=self.clock()), self._root)
        if job.state == "running":
            self._move(job.id, "unknown_remote", reason="contact_lost")

    def _reconcile(self, job: Job, record: RunRecord) -> None:
        """A submission without an answer: the job is looked for by its label. Found: it runs on. Not found
        after a while (and several looks): Hugging Face never started it, nothing was charged, and the run
        ends as "not started" (the person can start a new one; it is never sent again on its own)."""

        try:
            found = self.hub().find_jobs(hub_module.labels_for(job.id))
        except HubError:
            return  # still no answer: stays unknown
        if found:
            remote = found[0]
            write_record(replace(record, remote_id=remote.id, phase="running", last_contact_at=self.clock(),
                                 lost_since=None, not_found_checks=0), self._root)
            self._move(job.id, "running", reason="found", remote_request_id=remote.id, started_at=self.clock())
            return
        checks = record.not_found_checks + 1
        record = write_record(replace(record, not_found_checks=checks, last_contact_at=self.clock()), self._root)
        began = record.submit_started_at or record.lost_since or self.clock()
        if checks >= NOT_STARTED_CHECKS and self.clock() - began >= NOT_STARTED_SECONDS:
            write_record(replace(record, outcome="not_started", failure="not_started"), self._root)
            self._move(job.id, "failed", reason="not_started", error_code="not_started")
            self._clean_up(read_record(job.id, self._root) or record)

    def cancel(self, job_id: str) -> Job | None:
        """Cancel the job on Hugging Face (billing stops there), then the run stops and its files are deleted."""

        job = self.jobs.get(job_id)
        record = read_record(job_id, self._root)
        if job is None:
            return None
        if record is not None and record.remote_id:
            try:
                self.hub().cancel(record.remote_id)
            except HubError as error:
                if error.code != "not_found":
                    raise RemoteError("network" if error.code == "network" else error.code) from error
        elif record is not None and record.submit_started_at is not None:
            found = self.hub().find_jobs(hub_module.labels_for(job_id))
            for remote in found:
                self.hub().cancel(remote.id)
        moved = self._move(job_id, "stopped", reason="you")
        if record is not None:
            self._clean_up(write_record(replace(record, outcome="canceled"), self._root))
        return moved

    # ── After ───────────────────────────────────────────────────────────

    def _download(self, job_id: str) -> None:
        """The run's manifest and each pass's adapter, checked file by file; then the job is evaluating (the
        release maker downloads the GGUF it makes ready)."""

        from row_bot.learning.training import fetch
        from row_bot.learning.training import jobs as jobs_module

        record = read_record(job_id, self._root)
        if record is None:
            return
        hub = self.hub()
        run = jobs_module.run_dir(job_id, self._root)
        try:
            manifest = json.loads(hub.read_text(record.output_repo, "model", "outputs.json"))
            report = json.loads(hub.read_text(record.output_repo, "model", "report.json"))
            files = [item for item in manifest.get("files") or [] if isinstance(item, dict)]
            for item in files:
                name = str(item.get("name") or "")
                if not name.startswith("adapters/"):
                    continue
                parts = name.split("/")
                folder = run / "checkpoints" / (parts[1] if parts[1] != "final" else "")
                self._fetch(hub, record, item, folder, parts[-1])
        except (HubError, fetch.FetchError, ValueError, KeyError, OSError) as error:
            code = getattr(error, "code", "download_failed")
            logger.warning("Learning couldn't download a Hugging Face run's adapters (%s)", code)
            write_record(replace(record, phase="downloading", failure=str(code)[:64]), self._root)
            return  # tried again at the next tick: the files stay on Hugging Face until it works
        passes = report.get("passes")
        if isinstance(passes, dict):
            (run / jobs_module.PASSES_FILE).write_text(json.dumps(passes, sort_keys=True), encoding="utf-8")
        artifacts = report.get("artifacts")
        if isinstance(artifacts, dict):
            (run / "artifacts.json").write_text(json.dumps(artifacts, sort_keys=True), encoding="utf-8")
        timings = {key: report.get(key) for key in ("times", "gpu", "card_peak_mib", "card_mib", "variant")}
        record = write_record(replace(record, phase="downloaded", outputs=tuple(files), failure=None,
                                      timings=timings), self._root)
        job = self.jobs.get(job_id)
        if job is not None and job.state == "running":
            self._move(job_id, "evaluating", reason="finished", finished_at=self.clock(),
                       steps_done=int(job.steps_total or job.steps_done))
        if record.base_gguf:
            self._download_base(record)

    def _fetch(self, hub: Any, record: RunRecord, item: Mapping[str, Any], folder: Path, name: str,
               check: Callable[[], None] = lambda: None) -> Path:
        """One file of the run's results into ``folder``, checked against the manifest: a small one read whole,
        a large one through Row-Bot's resumable downloader from Hugging Face's signed storage address."""

        from row_bot.learning.training import fetch

        size, digest = int(item["size"]), str(item["sha256"])
        if size <= SMALL_FILE_BYTES:
            data = hub.read_bytes(record.output_repo, "model", str(item["name"]))
            if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
                raise fetch.FetchError("mismatch")
            target = Path(folder) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{name}.part")
            temporary.write_bytes(data)
            atomic.replace(temporary, target)
            return target
        url = hub.download_address(record.output_repo, "model", str(item["name"]))
        return self.fetcher().fetch(fetch.Pinned(url, name, digest, size), Path(folder), check=check)

    def _download_base(self, record: RunRecord) -> None:
        """The untouched starting model's GGUF the job built (v1 is compared with it), into its usual folder."""

        from row_bot.learning.training import base_model, fetch

        item = next((entry for entry in record.outputs if entry.get("name") == "gguf/base.gguf"), None)
        if item is None:
            return
        target = base_model.folder(record.model_id, self._root)
        name = f"{record.model_id}-{record.quantization}.gguf"
        try:
            self._fetch(self.hub(), record, item, target, name)
        except (HubError, fetch.FetchError, OSError):
            logger.warning("Learning couldn't download the starting model's file a Hugging Face run built")
            return
        base_model.write(base_model.BaseState(model_id=record.model_id, phase="exported", gguf=name,
                                              gguf_bytes=int(item["size"]), quantization=record.quantization,
                                              at=self.clock(), runtime="row_bot"), self._root)

    def _maybe_clean(self, job: Job, record: RunRecord) -> None:
        """Once the version is made ready (or settled for good), the uploads and the run's files go (O133)."""

        from row_bot.learning.training import releases

        if record.outcome != "completed":
            return
        if job.state in ("failed", "stopped"):
            self._clean_up(record)
            return
        state = releases.read_state(releases.version_dir(int(job.version or 0), self._root)) if job.version else None
        settled = state is not None and state.job_id == job.id and (
            state.phase in (releases.READY, releases.WAITING) or releases.couldnt_start(state))
        if settled or job.state in ("ready", "trial", "in_use", "not_used"):
            self._clean_up(record)

    def _clean_up(self, record: RunRecord) -> None:
        """Delete the run's private dataset and model repositories on Hugging Face (and say so)."""

        hub = self.hub()
        failed = False
        for repo, kind in ((record.dataset_repo, "dataset"), (record.output_repo, "model")):
            if not repo:
                continue
            try:
                hub.delete_repo(repo, kind)
            except HubError:
                failed = True
                logger.warning("Learning couldn't delete a Hugging Face %s repository yet", kind)
        current = read_record(record.job_id, self._root) or record
        write_record(replace(current, phase="cleaned" if not failed else current.phase,
                             cleaned_at=None if failed else self.clock(), cleanup_failed=failed), self._root)
        _changed()


# ── Counting a run's tokens on this computer ────────────────────────────────

TOKENIZERS_FOLDER = "tokenizers"
MEASURE_SECONDS = 900.0


def tokenizer_folder(model: Any, root: Path | None = None, *, fetcher: Any = None) -> Path:
    """The chosen model's tokenizer and chat template (its small pinned files, never its weights: about 25 MB),
    downloaded once from Hugging Face and checked against their pins: a computer that trains on Hugging Face
    has no engine or starting model to count a row's tokens with, and rows are kept by their exact length,
    as training renders them (O71/O96), like on a computer that trains here."""

    from row_bot.learning.training import engine, fetch

    folder = engine.learning_runtimes(root) / TOKENIZERS_FOLDER / model.id
    fetcher = fetcher or fetch.Fetcher()
    for item in model.files:
        if item.name.endswith(".safetensors") or item.name in ("README.md", "LICENSE"):
            continue
        fetcher.fetch(fetch.Pinned(model.url(item), item.name, item.sha256, item.size), folder)
    return folder


def measure_rows(rows: list[Any], max_seq: int, *, model: Any, root: Path | None = None,
                 fetcher: Any = None) -> list[tuple[int, int]]:
    """Each row's tokens and target tokens, counted by the engine's own ``measure`` mode (its renderer, the
    model's own tokenizer and chat template) in Row-Bot's own interpreter and Hugging Face's ``transformers``
    from Row-Bot's full install, out of Row-Bot's process, under the engine's network guard."""

    import shutil
    import sys
    import sysconfig
    import tempfile

    from row_bot.learning.training import dataset as dataset_module
    from row_bot.learning.training import engine, lockfile

    folder = tokenizer_folder(model, root, fetcher=fetcher)
    work = Path(tempfile.mkdtemp(prefix="measure-"))
    try:
        rows_path, out = work / "rows.jsonl", work / "lengths.json"
        rows_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        env = engine._base_environment(work / "home")  # noqa: SLF001 - the engine's own clean environment
        env.update({key: value for key, value in hub_module.SWITCHES.items()})
        env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
        argv = [sys.executable, "-I", "-S", str(lockfile.ENGINE_DIR / "entry.py"), "measure", "--site",
                sysconfig.get_paths()["purelib"], "--model", str(folder), "--rows", str(rows_path), "--out", str(out)]
        result = engine.run_owned(argv, cwd=work / "home", env=env, timeout=MEASURE_SECONDS, check=lambda: None)
        try:
            lengths = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            lengths = None
        if result.code != 0 or not isinstance(lengths, list) or len(lengths) != len(rows):
            logger.warning("Learning couldn't count a Hugging Face run's rows (exit %s)", result.code)
            raise dataset_module.DatasetError("measure_failed")
        return [(int(item[0]), int(item[1])) for item in lengths]
    finally:
        shutil.rmtree(work, ignore_errors=True)  # the rows are learning data: never left behind


def download_gguf(job_id: str, number: int, folder: Path, *, trainer: RemoteTrainer | None = None,
                  check: Callable[[], None] = lambda: None) -> tuple[Path, int, str]:
    """The release maker's export for a Hugging Face version: the GGUF the job built for pass ``number`` (0: a
    run of one pass), downloaded and checked against the run's manifest. Returns (file, size, sha256)."""

    trainer = trainer or get()
    record = read_record(job_id, trainer._root)  # noqa: SLF001
    if record is None:
        raise RemoteError("nothing_to_train")
    name = f"gguf/{'pass-' + str(number) if number else 'final'}.gguf"
    item = next((entry for entry in record.outputs if entry.get("name") == name), None)
    if item is None:
        raise RemoteError("nothing_to_train")
    target = f"personal-model-{record.quantization}.gguf"
    found = trainer._fetch(trainer.hub(), record, item, Path(folder), target, check=check)  # noqa: SLF001
    return found, int(item["size"]), str(item["sha256"])


def _changed() -> None:
    try:
        gate.changed()
    except Exception:  # noqa: BLE001
        logger.debug("Learning couldn't announce a Hugging Face change", exc_info=True)


_lock = threading.Lock()
_trainer: RemoteTrainer | None = None


def get() -> RemoteTrainer:
    global _trainer
    with _lock:
        if _trainer is None:
            _trainer = RemoteTrainer()
        return _trainer


def configure(trainer: RemoteTrainer | None) -> None:
    global _trainer
    with _lock:
        _trainer = trainer


__all__ = ["Quote", "RemoteError", "RemoteTrainer", "RunRecord", "bootstrap_command", "configure", "cost",
           "download_gguf", "get", "month_spent", "parse_lines", "read_record", "read_spend", "record_spend",
           "run_cost"]
