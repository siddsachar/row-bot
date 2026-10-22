"""What this computer can train a personal model on (D3, O23).

Row-Bot asks the NVIDIA driver's own ``nvidia-smi`` for the graphics cards and their memory
(no network, 15 s at most, no console window) and maps the largest card to a recipe
tier. Only the 8 GB tier is certified so far (O23: Qwen3.5 2B, bf16 LoRA at 16K, measured on an
RTX 5070 Laptop in L0d); a larger card can use it too, and its own larger tier shows as "measured
soon" until a recipe is certified for it (the RTX 5090 results, ``scripts/learning_gpu_check.py``).
The answer is kept for the life of the process: a graphics card doesn't change while Row-Bot runs,
and the setup sheet can ask again.

The driver picks the training engine's build (O25): CUDA 13.0 PyTorch with a driver of 580 or
newer, the CUDA 12.8 build (PyTorch 2.11) with 570-579, and below 570 "Update your NVIDIA driver
to train here".
"""

from __future__ import annotations

import ctypes
import logging
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from row_bot.learning.training import weights as _weights

logger = logging.getLogger(__name__)

# The card's memory in use too (O112): what it has free when setup asks decides the recommendation.
QUERY = ("--query-gpu=name,memory.total,driver_version,memory.used", "--format=csv,noheader,nounits")
# A laptop's graphics card asleep (Optimus) can take about 5 s to answer: measured
# 1.0-5.0 s on the RTX 5070 Laptop. The sheet shows "Checking..." meanwhile.
TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class StartingModel:
    """A model a personal model can start from, and what downloading it takes."""

    id: str
    name: str
    licence: str
    download_bytes: int


def _starting(pinned: _weights.PinnedModel) -> StartingModel:
    return StartingModel(pinned.id, pinned.name, pinned.licence, pinned.download_bytes)


# Qwen/Qwen3.5-2B at the revision L0d measured: 4.57 GB of weights (D5, D13), and the larger tiers'
# starting models (L2x, O90); every file pinned in ``weights.py``.
QWEN35_2B = _starting(_weights.QWEN35_2B)
QWEN35_9B = _starting(_weights.QWEN35_9B)
QWEN38_27B = _starting(_weights.QWEN38_27B)
# The training engine's download when its lock can't be read (CUDA PyTorch, Unsloth, TRL, PEFT,
# and llama.cpp's tools for the export): 2.3 GB on Windows (L0d, L2c). The real figure comes from
# the lock and the pinned tools (``engine_download_bytes``).
ENGINE_DOWNLOAD_BYTES = 2_280_000_000
STARTING_MODELS = {model.id: model for model in (QWEN35_2B, QWEN35_9B, QWEN38_27B)}
# Drivers (O25): CUDA 13.0 needs 580, CUDA 12.8 needs 570.
CUDA13_DRIVER = 580
MIN_DRIVER = 570


@dataclass(frozen=True)
class Tier:
    """A recipe tier by graphics memory. Uncertified tiers are shown as "measured soon".

    ``system_memory_mib``: the computer's memory (RAM and virtual memory) its recipe needs free on this
    system, as measured (O105); ``need_mib``: the graphics memory its recipe needs free (its measured peak on
    the card with a margin, O112). None where not certified."""

    id: str
    label: str
    min_memory_mib: int
    certified: bool
    summary: str
    models: tuple[StartingModel, ...] = ()
    system_memory_mib: int | None = None
    need_mib: int | None = None


def _tier(tier_id: str, label: str, min_memory_mib: int, summary: str, model: StartingModel) -> Tier:
    """A tier as its recipe file certifies it (``recipes/<tier>.json``): measured on a real card of that
    size or under that size's cap on a larger one (L0d, L2b for 8 GB; L2x on the RTX 5090 for the
    others), never promised (D3). Until then it reads "measured soon" and offers no model."""

    from row_bot.learning.training import recipes

    try:
        recipe = recipes.load(tier_id)
    except Exception:  # noqa: BLE001 - a recipe that can't be read certifies nothing
        recipe = None
    if recipe is None or not recipe.certified:
        return Tier(tier_id, label, min_memory_mib, False, f"Larger models for {label} cards: measured soon.")
    return Tier(tier_id, label, min_memory_mib, True, summary, (model,), recipe.system_memory_need(),
                int(recipe.memory_mib))


TIERS: tuple[Tier, ...] = (
    _tier("8gb", "8 GB", 7_600, "Can train small models (up to about 2B) on your tasks.", QWEN35_2B),
    # O111: Qwen3.5 4B left 5.4 (its training diverged in this engine); the 9B is the 16 GB candidate, as
    # QLoRA there and on 24 GB cards, and 16-bit on 32 GB cards (O107: the 27B needs a card with nothing
    # else on it).
    _tier("16gb", "16 GB", 15_000, "Can train models up to about 9B on your tasks.", QWEN35_9B),
    _tier("24gb", "24 GB", 23_000, "Can train models up to about 9B on your tasks.", QWEN35_9B),
    _tier("32gb", "32 GB", 30_000, "Can train models up to about 9B on your tasks.", QWEN35_9B),
)


@dataclass(frozen=True)
class Gpu:
    name: str
    memory_mib: int
    driver: str = ""
    # In use when the check ran (the display, other apps); None when the driver didn't say.
    used_mib: int | None = None

    @property
    def free_mib(self) -> int | None:
        return None if self.used_mib is None else max(0, self.memory_mib - self.used_mib)

    @property
    def memory_gb(self) -> int:
        return max(1, round(self.memory_mib / 1024))

    @property
    def label(self) -> str:
        """"NVIDIA GeForce RTX 5070 Laptop (8 GB)" (the size kept with its unit)."""

        name = self.name[:-4] if self.name.endswith(" GPU") else self.name
        return f"{name} ({self.memory_gb}\u00a0GB)"

    @property
    def driver_major(self) -> int | None:
        """596 for "596.08" (Windows), 580 for "580.65.06" (Linux); None when not reported."""

        head = self.driver.strip().split(".", 1)[0]
        return int(head) if head.isdigit() and 0 < len(head) <= 4 else None


def engine_variant(gpu: Gpu | None) -> str | None:
    """The engine build this card's driver takes: ``cu130``, ``cu128``, or None (too old).

    A driver the check didn't report is taken as current (the CUDA 13.0 build); if it is
    older, the engine's own check says so after the download.
    """

    if gpu is None:
        return None
    major = gpu.driver_major
    if major is None or major >= CUDA13_DRIVER:
        return "cu130"
    if major >= MIN_DRIVER:
        return "cu128"
    return None


@dataclass(frozen=True)
class SystemMemory:
    """This computer's memory for training (O105), in MiB: ``limit_mib`` is the most RAM and virtual
    memory can hold (Windows' commit limit: RAM plus page files; Linux: RAM plus swap), ``free_mib`` what
    is free of it now. Windows charges what a run holds on the graphics card to it too."""

    limit_mib: int
    free_mib: int

    @property
    def limit_gb(self) -> int:
        return max(1, round(self.limit_mib / 1024))


@dataclass(frozen=True)
class HardwareCheck:
    """``gpu``: an NVIDIA card a certified recipe fits; ``old_driver``: such a card whose driver
    is older than 570 (O25); ``small_gpu``: one too small to train on; ``low_memory``: a card that
    could train, on a computer whose RAM and virtual memory can't hold even its smallest tier's run
    (O105); ``no_gpu``: none found; ``container``: Row-Bot's Docker image, which doesn't train in 5.4
    (O46); ``unknown``: the check itself failed."""

    state: Literal["gpu", "old_driver", "small_gpu", "low_memory", "no_gpu", "container", "unknown"]
    gpu: Gpu | None = None
    # The certified tier this card trains with, and the larger tiers it fits once measured.
    tier: Tier | None = None
    larger_tiers: tuple[Tier, ...] = ()
    # The computer's memory when it was checked, and the certified larger tiers the card fits but the
    # computer's memory can't hold (O105: offered once virtual memory is raised).
    memory: SystemMemory | None = None
    memory_short: tuple[Tier, ...] = ()
    # O112: the tier Row-Bot recommends, the largest one whose card need fits what the card had free when
    # checked (the display and other apps use the rest); None when that couldn't be read (then ``tier``).
    recommended: Tier | None = None

    @property
    def recommended_tier(self) -> Tier | None:
        return self.recommended or self.tier

    @property
    def can_train(self) -> bool:
        return self.state == "gpu"

    @property
    def variant(self) -> str | None:
        """The training engine's build for this card (None when it can't train here)."""

        return engine_variant(self.gpu) if self.can_train else None

    @property
    def starting_models(self) -> tuple[StartingModel, ...]:
        """Every certified starting model this card trains, its own tier's first (the recommendation)."""

        if self.tier is None or self.state == "low_memory":
            return ()  # O105: nothing is offered that this computer's memory can't hold
        fits = [tier for tier in TIERS if tier.certified and tier.min_memory_mib <= self.tier.min_memory_mib]
        found: dict[str, StartingModel] = {}
        first = self.recommended_tier
        for model in (*(first.models if first else ()), *(model for tier in reversed(fits) for model in tier.models)):
            found.setdefault(model.id, model)  # the recommendation first; one entry per model
        return tuple(found.values())


def engine_download_bytes(variant: str | None) -> int:
    """About how much the training engine downloads for this computer (from its lock)."""

    if variant is None:
        return ENGINE_DOWNLOAD_BYTES
    try:
        from row_bot.learning.training import lockfile

        return lockfile.cached_plan(variant).download_bytes()
    except Exception:
        logger.debug("Learning could not read the training engine's lock", exc_info=True)
        return ENGINE_DOWNLOAD_BYTES


Runner = Callable[..., Any]


def _command(which: Callable[[str], str | None]) -> str | None:
    found = which("nvidia-smi")
    if found:
        return found
    if sys.platform == "win32":
        # The driver installs it here even when it isn't on PATH.
        for candidate in (r"C:\Windows\System32\nvidia-smi.exe",):
            try:
                from pathlib import Path

                if Path(candidate).is_file():
                    return candidate
            except OSError:
                continue
    return None


def _parse(output: str) -> list[Gpu]:
    found: list[Gpu] = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2 or not parts[0]:
            continue
        try:
            memory = int(float(parts[1]))
        except ValueError:
            continue
        try:
            used = int(float(parts[3])) if len(parts) > 3 else None
        except ValueError:
            used = None
        if memory > 0:
            found.append(Gpu(parts[0][:128], memory, parts[2][:32] if len(parts) > 2 else "", used))
    return found


def classify(gpus: Sequence[Gpu], memory: SystemMemory | None = None) -> HardwareCheck:
    """The largest card's tier (pure: no I/O): the largest certified tier its card fits and the
    computer's memory can hold (O105; a tier without a figure for this system, or a computer whose
    memory couldn't be read, isn't held back)."""

    if not gpus:
        return HardwareCheck("no_gpu", memory=memory)
    gpu = max(gpus, key=lambda item: item.memory_mib)
    fits = [tier for tier in TIERS if gpu.memory_mib >= tier.min_memory_mib]
    certified = [tier for tier in fits if tier.certified]
    if not certified:
        return HardwareCheck("small_gpu", gpu, memory=memory)
    held = [tier for tier in certified if memory is None or not tier.system_memory_mib
            or tier.system_memory_mib <= memory.limit_mib]
    if not held:
        return HardwareCheck("low_memory", gpu, certified[0], memory=memory)
    best = held[-1]
    # Tiers the memory can't hold, when they'd offer a model the held ones don't (the 9B trains on several tiers).
    offered = {model.id for tier in held for model in tier.models}
    short = tuple(tier for tier in certified if tier not in held and tier.min_memory_mib > best.min_memory_mib
                  and any(model.id not in offered for model in tier.models))
    larger = tuple(tier for tier in fits if not tier.certified and tier.min_memory_mib > best.min_memory_mib)
    # O112: recommend the largest held tier whose card need fits what the card has free (else the smallest:
    # it waits for room, and after two nights without it the Personal model view offers the next smaller).
    free = gpu.free_mib
    roomy = [tier for tier in held if free is None or not tier.need_mib or tier.need_mib <= free]
    recommended = (roomy[-1] if roomy else held[0]) if free is not None else None
    if engine_variant(gpu) is None:
        return HardwareCheck("old_driver", gpu, best, larger, memory, short, recommended)
    return HardwareCheck("gpu", gpu, best, larger, memory, short, recommended)


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total_phys", ctypes.c_ulonglong),
                ("avail_phys", ctypes.c_ulonglong), ("total_page_file", ctypes.c_ulonglong),
                ("avail_page_file", ctypes.c_ulonglong), ("total_virtual", ctypes.c_ulonglong),
                ("avail_virtual", ctypes.c_ulonglong), ("avail_extended_virtual", ctypes.c_ulonglong)]


def _windows_memory() -> SystemMemory | None:
    status = _MemoryStatus()
    status.length = ctypes.sizeof(_MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        return None
    # The "page file" figures are the commit limit and what's left of it (RAM plus every page file).
    return SystemMemory(int(status.total_page_file // 1_048_576), int(status.avail_page_file // 1_048_576))


def _linux_memory(text: str) -> SystemMemory | None:
    values: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[name.strip()] = int(parts[0])  # kB
    if "MemTotal" not in values or "MemAvailable" not in values:
        return None
    limit = values["MemTotal"] + values.get("SwapTotal", 0)
    free = values["MemAvailable"] + values.get("SwapFree", 0)
    return SystemMemory(limit // 1024, free // 1024)


def system_memory(*, platform: str | None = None, read_text: Callable[[str], str] | None = None
                  ) -> SystemMemory | None:
    """This computer's memory for training now, or None where it can't be read (never raises)."""

    platform = platform or sys.platform
    try:
        if platform == "win32":
            return _windows_memory()
        if platform.startswith("linux"):
            from pathlib import Path

            return _linux_memory((read_text or (lambda path: Path(path).read_text(encoding="ascii")))("/proc/meminfo"))
    except (OSError, ValueError, AttributeError):
        logger.debug("Learning could not read this computer's memory", exc_info=True)
    return None


def detect(
    *,
    run: Runner = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    memory: Callable[[], SystemMemory | None] = system_memory,
) -> HardwareCheck:
    """Ask ``nvidia-smi`` once. Never raises; ``unknown`` when the check itself failed. In Row-Bot's
    Docker image training is unavailable (GPU containers are out of scope for 5.4, O46)."""

    import os

    if os.environ.get("ROW_BOT_CONTAINERIZED", "").strip() == "1":
        return HardwareCheck("container")
    command = _command(which)
    if command is None:
        return HardwareCheck("no_gpu")
    options: dict[str, Any] = {}
    if sys.platform == "win32":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = run(
            [command, *QUERY],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
            **options,
        )
    except (OSError, subprocess.SubprocessError) as error:
        logger.info("Learning could not check the graphics card (%s)", type(error).__name__)
        return HardwareCheck("unknown")
    if getattr(result, "returncode", 1) != 0:
        # The tool is there but has no card to report (or no working driver).
        return HardwareCheck("no_gpu")
    return classify(_parse(str(getattr(result, "stdout", "") or "")), memory())


_lock = threading.Lock()
_cached: HardwareCheck | None = None


def check(*, refresh: bool = False, detector: Callable[[], HardwareCheck] = detect) -> HardwareCheck:
    """This computer's check, kept for the life of the process (``refresh`` asks again)."""

    global _cached
    with _lock:
        if _cached is None or refresh:
            _cached = detector()
        return _cached


def peek() -> HardwareCheck | None:
    """The kept answer if the check has run, without running it (reads that mustn't wait)."""

    with _lock:
        return _cached


def forget() -> None:
    """Drop the kept answer (tests)."""

    global _cached, _memory_seen
    with _lock:
        _cached = None
        _memory_seen = None


# ── Whether the card's memory is free to train now ──────────────────────────
# A recipe uses nearly all of the card (W8: capped at 95%). Another app holding its memory (a local
# model Ollama keeps loaded for half an hour after a chat, a game) would make Windows spill training
# into system memory and crawl (L2c's e2e: no step in 12 minutes beside a 2.9 GB Ollama model), so
# training doesn't start until the card is free.
MEMORY_QUERY = ("--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits")
TRAINING_FREE_SHARE = 0.9
# What another program may hold on a card whose tier the recipe fills (the 2B on an 8 GB card: its 95% cap
# plus the CUDA context leave about 160 MiB of an 8,151 MiB card, L0d).
IDLE_CARD_MIB = 160
MEMORY_SECONDS = 30.0
_memory_seen: tuple[float, bool] | None = None


def gpu_memory(*, run: Runner = subprocess.run, which: Callable[[str], str | None] = shutil.which
               ) -> tuple[int, int] | None:
    """(used, total) MiB of the largest NVIDIA card now, or None when it can't be read."""

    command = _command(which)
    if command is None:
        return None
    options: dict[str, Any] = {}
    if sys.platform == "win32":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = run([command, *MEMORY_QUERY], capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
                     check=False, **options)
    except (OSError, subprocess.SubprocessError):
        return None
    cards = []
    for line in str(getattr(result, "stdout", "") or "").splitlines():
        parts = [part.strip() for part in line.split(",")]
        try:
            cards.append((int(float(parts[0])), int(float(parts[1]))))
        except (ValueError, IndexError):
            continue
    if getattr(result, "returncode", 1) != 0 or not cards:
        return None
    return max(cards, key=lambda card: card[1])


def forget_memory_reading() -> None:
    """Read the card's memory afresh next time (something was just unloaded from it)."""

    global _memory_seen
    with _lock:
        _memory_seen = None


def training_need_mib(found: HardwareCheck | None = None) -> int | None:
    """The graphics memory the recipe this computer trains with needs free: its measured peak on the card
    with a margin (O112), or None when there is no recipe for it."""

    from row_bot.learning.training import recipes

    try:
        recipe = recipes.current(check=found if found is not None else peek())
    except Exception:  # noqa: BLE001 - no recipe: the share of the card decides
        return None
    need = int(getattr(recipe, "memory_mib", 0) or 0)
    return need or None


def training_system_need_mib(found: HardwareCheck | None = None) -> int | None:
    """The computer's memory (RAM and virtual memory) the recipe this computer trains with needs free on
    this system (O105), or None when it wasn't measured here or there is no recipe."""

    from row_bot.learning.training import recipes

    try:
        recipe = recipes.current(check=found if found is not None else peek())
    except Exception:  # noqa: BLE001 - no recipe: nothing to wait for
        return None
    return recipe.system_memory_need()


def memory_free_for_training(*, reader: Callable[[], SystemMemory | None] = system_memory) -> bool:
    """Whether the computer has the memory free that training needs now (O105): Row-Bot waits, and
    never closes or touches another app. True when it can't be read or wasn't measured here."""

    found = peek()
    if found is None or found.state != "gpu":
        return True
    need = training_system_need_mib(found)
    if not need:
        return True
    reading = reader()
    if reading is None:
        return True
    free = reading.free_mib >= need
    if not free:
        logger.info("Learning waits for memory: training needs %s MiB free, %s MiB is", need, reading.free_mib)
    return free


def gpu_free_for_training(*, memory: Callable[[], tuple[int, int] | None] | None = None,
                          clock: Callable[[], float] | None = None) -> bool:
    """Whether the card has the memory free to train now (read at most every 30 s). True when this
    computer has no card the check found, or its memory can't be read: nothing to wait for."""

    global _memory_seen
    import time

    found = peek()
    if found is None or found.state != "gpu":
        return True
    now = (clock or time.monotonic)()
    with _lock:
        if memory is None and _memory_seen is not None and now - _memory_seen[0] < MEMORY_SECONDS:
            return _memory_seen[1]
    reading = (memory or gpu_memory)()
    free = True
    if reading is not None:
        used, total = reading
        # The recipe's own need (O112: its measured peak on the card with a margin), never a share of the
        # card: on a 32 GB card that also drives the desktop (browsers and apps hold 3-6 GB) the 2B trains
        # beside them (L2w), and a recipe that needs more than is free waits instead of spilling.
        need = training_need_mib(found) or TRAINING_FREE_SHARE * total
        # A recipe that needs its whole tier (the 2B on an 8 GB card) starts when the card is all but empty.
        need = min(need, total - IDLE_CARD_MIB)
        free = total > 0 and total - used >= need
    with _lock:
        _memory_seen = (now, free)
    if not free:
        logger.info("Learning waits for the graphics card: another app is using %s of its %s MiB", *reading)
    return free


__all__ = [
    "CUDA13_DRIVER",
    "ENGINE_DOWNLOAD_BYTES",
    "MIN_DRIVER",
    "Gpu",
    "HardwareCheck",
    "SystemMemory",
    "QWEN35_2B",
    "STARTING_MODELS",
    "StartingModel",
    "TIERS",
    "Tier",
    "check",
    "classify",
    "detect",
    "engine_download_bytes",
    "engine_variant",
    "forget",
    "gpu_free_for_training",
    "gpu_memory",
    "memory_free_for_training",
    "system_memory",
    "training_system_need_mib",
]
