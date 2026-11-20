"""What this computer has for running a model: its graphics cards, memory and platform.

No network, bounded time: NVIDIA cards through ``nvidia-smi`` (5 s at most; a sleeping laptop card
can take that long), other cards from the system's own records (the Windows registry's display
adapters, Linux's ``/sys/class/drm``), Apple silicon and memory through ``sysctl`` on macOS,
``GlobalMemoryStatusEx`` on Windows and ``/proc/meminfo`` on Linux. :func:`classify` turns those
facts into the build to use and the graphics memory a model may take; it is pure, so tests give it
any computer.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from row_bot.local_runtime import builds

logger = logging.getLogger(__name__)

GIB = 1024**3
MIB = 1024**2
# Graphics memory the system and the display keep for themselves.
CARD_RESERVE_BYTES = 512 * MIB
# Apple silicon shares the computer's memory with its graphics; Metal lets a program use about this much.
UNIFIED_SHARE = 0.70
# A card with less memory than this (an integrated one) is no faster than the processor.
MIN_CARD_BYTES = 2 * GIB
# NVIDIA's CUDA minor-version compatibility: CUDA 13.x needs driver 580 or newer, CUDA 12.x 525.
CUDA_13_DRIVER = 580
CUDA_12_DRIVER = 525
PROBE_SECONDS = 5.0

NVIDIA = "nvidia"
AMD = "amd"
INTEL = "intel"
APPLE = "apple"
OTHER = "other"
_VENDOR_IDS = {"10de": NVIDIA, "1002": AMD, "1022": AMD, "8086": INTEL, "106b": APPLE}


@dataclass(frozen=True)
class Gpu:
    vendor: str
    name: str
    memory_bytes: int
    driver: str | None = None

    @property
    def driver_major(self) -> int:
        try:
            return int(str(self.driver or "").split(".")[0])
        except ValueError:
            return 0


@dataclass(frozen=True)
class Hardware:
    """This computer, classified: which build runs best and how much graphics memory a model gets."""

    platform: str | None
    ram_bytes: int
    acceleration: str
    gpu: Gpu | None
    usable_gpu_bytes: int
    apple_silicon: bool = False
    gpus: tuple[Gpu, ...] = field(default_factory=tuple)
    # Why a faster build wasn't chosen ("driver_too_old"), for the person's fix line.
    note: str | None = None

    @property
    def supported(self) -> bool:
        return self.platform is not None

    def describe(self) -> str:
        """One plain line: "NVIDIA GeForce RTX 4070 (12 GB)", "Apple M3 (24 GB memory)", "16 GB memory"."""

        if self.apple_silicon:
            name = self.gpu.name if self.gpu else "Apple silicon"
            return f"{name} ({round(self.ram_bytes / GIB)} GB memory)"
        if self.gpu is not None and self.acceleration != builds.CPU:
            return f"{self.gpu.name} ({round(self.gpu.memory_bytes / GIB)} GB)"
        return f"{round(self.ram_bytes / GIB)} GB memory, no graphics card"


def classify(platform: str | None, ram_bytes: int, gpus: Sequence[Gpu] = (), *,
             apple_silicon: bool = False) -> Hardware:
    """The build and the graphics memory for a model on a computer with these facts."""

    ram = max(0, int(ram_bytes))
    cards = tuple(gpus)
    if platform is None:
        return Hardware(None, ram, builds.CPU, None, 0, apple_silicon, cards)
    if platform == builds.MACOS_ARM64 or apple_silicon:
        gpu = next((card for card in cards if card.vendor == APPLE), None) or Gpu(APPLE, "Apple silicon", ram)
        return Hardware(platform, ram, builds.METAL, gpu, int(ram * UNIFIED_SHARE), True, cards)
    nvidia = max((card for card in cards if card.vendor == NVIDIA), key=lambda card: card.memory_bytes,
                 default=None)
    other = max((card for card in cards if card.vendor in (AMD, INTEL, OTHER) and card.memory_bytes >= MIN_CARD_BYTES),
                key=lambda card: card.memory_bytes, default=None)
    if nvidia is not None and nvidia.memory_bytes >= MIN_CARD_BYTES:
        usable = max(0, nvidia.memory_bytes - CARD_RESERVE_BYTES)
        major = nvidia.driver_major
        if major >= CUDA_13_DRIVER:
            return Hardware(platform, ram, builds.CUDA_13, nvidia, usable, False, cards)
        if major >= CUDA_12_DRIVER:
            return Hardware(platform, ram, builds.CUDA_12, nvidia, usable, False, cards)
        return Hardware(platform, ram, builds.VULKAN, nvidia, usable, False, cards, note="driver_too_old")
    if other is not None:
        return Hardware(platform, ram, builds.VULKAN, other, max(0, other.memory_bytes - CARD_RESERVE_BYTES),
                        False, cards)
    return Hardware(platform, ram, builds.CPU, None, 0, False, cards)


# ── Reading the facts ───────────────────────────────────────────────────────


def _run(argv: Sequence[str], timeout: float = PROBE_SECONDS) -> str:
    options: dict = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        done = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, check=False,
                              stdin=subprocess.DEVNULL, **options)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def parse_nvidia_smi(text: str) -> list[Gpu]:
    """``nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits``."""

    found = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            memory = int(float(parts[1])) * MIB
        except ValueError:
            continue
        found.append(Gpu(NVIDIA, parts[0][:80], memory, parts[2][:32]))
    return found


def _nvidia() -> list[Gpu]:
    tool = shutil.which("nvidia-smi")
    if tool is None and os.name == "nt":
        candidate = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        tool = str(candidate) if candidate.is_file() else None
    if tool is None:
        return []
    return parse_nvidia_smi(_run([tool, "--query-gpu=name,memory.total,driver_version",
                                  "--format=csv,noheader,nounits"]))


def _vendor(device_id: str) -> str:
    text = device_id.lower()
    for key, vendor in _VENDOR_IDS.items():
        if f"ven_{key}" in text or text == f"0x{key}":
            return vendor
    return OTHER


def _windows_adapters() -> list[Gpu]:
    """Display adapters from the registry (its 64-bit memory size, unlike WMI's 4 GB cap)."""

    try:
        import winreg
    except ImportError:
        return []
    found = []
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return []
    with root:
        for index in range(32):
            try:
                name = winreg.EnumKey(root, index)
            except OSError:
                break
            if not name.isdigit():
                continue
            try:
                with winreg.OpenKey(root, name) as key:
                    values = {}
                    for value in ("DriverDesc", "MatchingDeviceId", "HardwareInformation.qwMemorySize",
                                  "HardwareInformation.MemorySize"):
                        try:
                            values[value] = winreg.QueryValueEx(key, value)[0]
                        except OSError:
                            continue
            except OSError:
                continue
            memory = values.get("HardwareInformation.qwMemorySize") or values.get("HardwareInformation.MemorySize")
            if isinstance(memory, bytes):
                memory = int.from_bytes(memory[:8], "little")
            if not isinstance(memory, int) or memory <= 0:
                continue
            found.append(Gpu(_vendor(str(values.get("MatchingDeviceId") or "")),
                             str(values.get("DriverDesc") or "Graphics card")[:80], int(memory)))
    return found


def _linux_adapters(root: Path = Path("/sys/class/drm")) -> list[Gpu]:
    found = []
    try:
        cards = sorted(path for path in root.glob("card[0-9]*") if "-" not in path.name)
    except OSError:
        return []
    for card in cards[:16]:
        device = card / "device"
        try:
            vendor = _vendor((device / "vendor").read_text(encoding="ascii").strip())
            memory = int((device / "mem_info_vram_total").read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            continue
        name = {AMD: "AMD graphics card", INTEL: "Intel graphics card"}.get(vendor, "Graphics card")
        found.append(Gpu(vendor, name, memory))
    return found


def _sysctl(name: str) -> str:
    return _run(["/usr/sbin/sysctl", "-n", name], timeout=2.0).strip()


def system_memory() -> int:
    """Bytes of memory in this computer (0 when it can't be read)."""

    if os.name == "nt":
        import ctypes

        class _Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("available", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong),
                        ("page_available", ctypes.c_ulonglong), ("virtual_total", ctypes.c_ulonglong),
                        ("virtual_available", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]

        status = _Status()
        status.length = ctypes.sizeof(_Status)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return int(status.total)
        except (AttributeError, OSError):
            return 0
        return 0
    if sys.platform == "darwin":
        try:
            return int(_sysctl("hw.memsize") or 0)
        except ValueError:
            return 0
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return 0
    return 0


def detect() -> Hardware:
    """Read this computer's facts now (a few seconds at most) and classify them."""

    platform = builds.platform_key()
    if platform is None and sys.platform == "darwin" and _sysctl("hw.optional.arm64") == "1":
        platform = builds.MACOS_ARM64  # Row-Bot itself running under Rosetta on Apple silicon
    ram = system_memory()
    if platform == builds.MACOS_ARM64:
        chip = _sysctl("machdep.cpu.brand_string") or "Apple silicon"
        return classify(platform, ram, [Gpu(APPLE, chip[:80], ram)], apple_silicon=True)
    if platform is None:
        return classify(None, ram)
    cards = _nvidia()
    others = _windows_adapters() if os.name == "nt" else _linux_adapters()
    cards += [card for card in others if card.vendor != NVIDIA]
    return classify(platform, ram, cards)


_lock = threading.Lock()
_cached: Hardware | None = None
# Replaced in tests and by the browser fixture: never asks the real computer there.
detector: Callable[[], Hardware] = detect


def peek(*, refresh: bool = False) -> Hardware:
    """This computer, read once per run (Repair reads it again)."""

    global _cached
    with _lock:
        if _cached is None or refresh:
            try:
                _cached = detector()
            except Exception:  # noqa: BLE001 - a probe that broke means "nothing found"
                logger.warning("Row-Bot couldn't read this computer's graphics card and memory", exc_info=True)
                _cached = classify(builds.platform_key(), 0)
        return _cached


def forget() -> None:
    global _cached
    with _lock:
        _cached = None


__all__ = [
    "AMD",
    "APPLE",
    "Gpu",
    "Hardware",
    "INTEL",
    "NVIDIA",
    "classify",
    "detect",
    "forget",
    "parse_nvidia_smi",
    "peek",
    "system_memory",
]
