"""What this computer can train a personal model on (D3, O23).

Row-Bot asks the NVIDIA driver's own ``nvidia-smi`` for the graphics cards and their memory
(no network, 15 s at most, no console window) and maps the largest card to a recipe
tier. Only the 8 GB tier is certified so far (O23: Qwen3.5 2B, bf16 LoRA at 16K, measured on an
RTX 5070 Laptop in L0d); a larger card can use it too, and its own larger tier shows as "measured
soon" until a recipe is certified for it. The answer is kept for the life of the process: a
graphics card doesn't change while Row-Bot runs, and the setup sheet can ask again.

L2b extends this module (driver checks, engine wheels, the download).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

QUERY = ("--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits")
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


# Qwen/Qwen3.5-2B at the revision L0d measured: 4.57 GB of weights (D5, D13).
QWEN35_2B = StartingModel("qwen3.5-2b", "Qwen3.5 2B", "Apache 2.0", 4_570_000_000)
# The training engine's wheels (CUDA PyTorch, Unsloth, TRL, PEFT): 2.17 GB downloaded in L0d.
ENGINE_DOWNLOAD_BYTES = 2_170_000_000
STARTING_MODELS = {QWEN35_2B.id: QWEN35_2B}


@dataclass(frozen=True)
class Tier:
    """A recipe tier by graphics memory. Uncertified tiers are shown as "measured soon"."""

    id: str
    label: str
    min_memory_mib: int
    certified: bool
    summary: str
    models: tuple[StartingModel, ...] = ()


TIERS: tuple[Tier, ...] = (
    Tier("8gb", "8 GB", 7_600, True, "Can train small models (up to about 2B) on your tasks.", (QWEN35_2B,)),
    Tier("12gb", "12–16 GB", 11_500, False, "Larger models for 12–16 GB cards: measured soon."),
    Tier("24gb", "24 GB", 23_000, False, "Larger models for 24 GB cards: measured soon."),
    Tier("32gb", "32 GB", 30_000, False, "Larger models for 32 GB cards: measured soon."),
)


@dataclass(frozen=True)
class Gpu:
    name: str
    memory_mib: int
    driver: str = ""

    @property
    def memory_gb(self) -> int:
        return max(1, round(self.memory_mib / 1024))

    @property
    def label(self) -> str:
        """"NVIDIA GeForce RTX 5070 Laptop (8 GB)" (the size kept with its unit)."""

        name = self.name[:-4] if self.name.endswith(" GPU") else self.name
        return f"{name} ({self.memory_gb}\u00a0GB)"


@dataclass(frozen=True)
class HardwareCheck:
    """``gpu``: an NVIDIA card a certified recipe fits; ``small_gpu``: one too small to train on;
    ``no_gpu``: none found; ``unknown``: the check itself failed."""

    state: Literal["gpu", "small_gpu", "no_gpu", "unknown"]
    gpu: Gpu | None = None
    # The certified tier this card trains with, and the larger tiers it fits once measured.
    tier: Tier | None = None
    larger_tiers: tuple[Tier, ...] = ()

    @property
    def can_train(self) -> bool:
        return self.state == "gpu"


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
        if memory > 0:
            found.append(Gpu(parts[0][:128], memory, parts[2][:32] if len(parts) > 2 else ""))
    return found


def classify(gpus: Sequence[Gpu]) -> HardwareCheck:
    """The largest card's tier (pure: no I/O)."""

    if not gpus:
        return HardwareCheck("no_gpu")
    gpu = max(gpus, key=lambda item: item.memory_mib)
    fits = [tier for tier in TIERS if gpu.memory_mib >= tier.min_memory_mib]
    certified = [tier for tier in fits if tier.certified]
    if not certified:
        return HardwareCheck("small_gpu", gpu)
    best = certified[-1]
    larger = tuple(tier for tier in fits if not tier.certified and tier.min_memory_mib > best.min_memory_mib)
    return HardwareCheck("gpu", gpu, best, larger)


def detect(
    *,
    run: Runner = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> HardwareCheck:
    """Ask ``nvidia-smi`` once. Never raises; ``unknown`` when the check itself failed."""

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
    return classify(_parse(str(getattr(result, "stdout", "") or "")))


_lock = threading.Lock()
_cached: HardwareCheck | None = None


def check(*, refresh: bool = False, detector: Callable[[], HardwareCheck] = detect) -> HardwareCheck:
    """This computer's check, kept for the life of the process (``refresh`` asks again)."""

    global _cached
    with _lock:
        if _cached is None or refresh:
            _cached = detector()
        return _cached


def forget() -> None:
    """Drop the kept answer (tests)."""

    global _cached
    with _lock:
        _cached = None


__all__ = [
    "ENGINE_DOWNLOAD_BYTES",
    "Gpu",
    "HardwareCheck",
    "QWEN35_2B",
    "STARTING_MODELS",
    "StartingModel",
    "TIERS",
    "Tier",
    "check",
    "classify",
    "detect",
    "forget",
]
