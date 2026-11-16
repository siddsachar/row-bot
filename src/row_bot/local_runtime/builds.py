"""The pinned llama.cpp release: one official ggml-org build per computer and acceleration.

Every asset is pinned by name, size and SHA-256 (GitHub's own asset digests for the release,
``docs/LOCAL_RUNTIME.md``). A CUDA build comes with ggml-org's CUDA runtime archive, unpacked next
to the binaries. Pins move only with a Row-Bot release; nothing here looks for a newer build.
"""

from __future__ import annotations

import platform as _platform
from dataclasses import dataclass

# llama.cpp v1.0.0's assets are those of this nightly build (its release notes name it).
RELEASE = "b12359"
RELEASE_NAME = "v1.0.0"
RELEASE_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{RELEASE}"
LICENCE = "MIT"

WINDOWS_X64 = "windows-x64"
MACOS_ARM64 = "macos-arm64"
LINUX_X64 = "linux-x64"
PLATFORMS = (WINDOWS_X64, MACOS_ARM64, LINUX_X64)

CUDA_13 = "cuda-13"
CUDA_12 = "cuda-12"
VULKAN = "vulkan"
METAL = "metal"
CPU = "cpu"
ACCELERATIONS = (CUDA_13, CUDA_12, METAL, VULKAN, CPU)

ACCELERATION_LABELS = {
    CUDA_13: "NVIDIA (CUDA 13)",
    CUDA_12: "NVIDIA (CUDA 12)",
    METAL: "Apple silicon (Metal)",
    VULKAN: "Graphics card (Vulkan)",
    CPU: "Processor only",
}


@dataclass(frozen=True)
class Asset:
    """One release file: its name, size in bytes and SHA-256."""

    name: str
    size: int
    sha256: str

    @property
    def url(self) -> str:
        return f"{RELEASE_URL}/{self.name}"


@dataclass(frozen=True)
class Build:
    """What one computer and acceleration download: the build and, for CUDA, its runtime."""

    platform: str
    acceleration: str
    assets: tuple[Asset, ...]

    @property
    def id(self) -> str:
        """The build's folder name under ``runtimes/llamacpp/``."""

        return f"{RELEASE}-{self.acceleration}"

    @property
    def size(self) -> int:
        return sum(asset.size for asset in self.assets)

    @property
    def label(self) -> str:
        return ACCELERATION_LABELS[self.acceleration]

    @property
    def executable(self) -> str:
        return "llama-server.exe" if self.platform == WINDOWS_X64 else "llama-server"


def _asset(name: str, size: int, sha256: str) -> Asset:
    return Asset(name, size, sha256)


BUILDS: dict[tuple[str, str], Build] = {
    (WINDOWS_X64, CUDA_13): Build(WINDOWS_X64, CUDA_13, (
        _asset(f"llama-{RELEASE}-bin-win-cuda-13.5-x64.zip", 179_546_112,
               "1de9ca1ff408d6e348ea0007c6880ad2393311a02e7799bcde26dc16daef03dd"),
        _asset("cudart-llama-bin-win-cuda-13.5-x64.zip", 426_314_199,
               "c2bf4cc202bfbaaa7a0b75f5082457c3811326947bb216059b72839d844e9cba"),
    )),
    (WINDOWS_X64, CUDA_12): Build(WINDOWS_X64, CUDA_12, (
        _asset(f"llama-{RELEASE}-bin-win-cuda-12.4-x64.zip", 318_976_332,
               "e9c0abf442e8339c06728366c1ad2c711332f9939c0c00603fb75d85fd44c914"),
        _asset("cudart-llama-bin-win-cuda-12.4-x64.zip", 391_443_627,
               "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6"),
    )),
    (WINDOWS_X64, VULKAN): Build(WINDOWS_X64, VULKAN, (
        _asset(f"llama-{RELEASE}-bin-win-vulkan-x64.zip", 38_090_643,
               "e8d18c6e4210cca87e7307c2ea78363ac8d158e4e1397358ec96ab70b53a0d5e"),
    )),
    (WINDOWS_X64, CPU): Build(WINDOWS_X64, CPU, (
        _asset(f"llama-{RELEASE}-bin-win-cpu-x64.zip", 25_097_509,
               "837c1655e87df3b66989f4be33a792a1399c785bf259038dc75c900096683e69"),
    )),
    (MACOS_ARM64, METAL): Build(MACOS_ARM64, METAL, (
        _asset(f"llama-{RELEASE}-bin-macos-arm64.tar.gz", 16_907_811,
               "82b00abc740160e1b9980e6cc79c117345fa34af058c80e6c0a69aa8ddccde75"),
    )),
    (LINUX_X64, CUDA_13): Build(LINUX_X64, CUDA_13, (
        _asset(f"llama-{RELEASE}-bin-ubuntu-cuda-13.5-x64.tar.gz", 178_419_847,
               "9440f417615bed0e191acef03bff0249117d2be74335358265a6d28475a9baf0"),
        _asset(f"cudart-llama-{RELEASE}-bin-ubuntu-cuda-13.5-x64.tar.gz", 444_078_822,
               "976a9146bb8a487a6ea701224d7fb14b05e494ab3c8f5c1799ffc4e5805d640c"),
    )),
    (LINUX_X64, CUDA_12): Build(LINUX_X64, CUDA_12, (
        _asset(f"llama-{RELEASE}-bin-ubuntu-cuda-12.8-x64.tar.gz", 199_223_370,
               "50c937bec5bbd9f74c99138034f7403283ee21f1943f2429d7b1e4c079eafc11"),
        _asset(f"cudart-llama-{RELEASE}-bin-ubuntu-cuda-12.8-x64.tar.gz", 594_371_571,
               "75a12d227f44a391608992329241765343bd143ee9445b90dc62e154ee726e5d"),
    )),
    (LINUX_X64, VULKAN): Build(LINUX_X64, VULKAN, (
        _asset(f"llama-{RELEASE}-bin-ubuntu-vulkan-x64.tar.gz", 36_153_377,
               "19b60f00395a3f4954e46a5037695bc1d843404fc9d3124f82a612ecd8fbe9f0"),
    )),
    (LINUX_X64, CPU): Build(LINUX_X64, CPU, (
        _asset(f"llama-{RELEASE}-bin-ubuntu-x64.tar.gz", 23_311_084,
               "0bc1f091e8916102b6d8f2cf944233a620e90a0f856c72d0ce41150c65c2e72e"),
    )),
}


def platform_key(system: str | None = None, machine: str | None = None) -> str | None:
    """This computer's platform as the builds name it, or None when no build runs here
    (Intel Macs, Windows and Linux on Arm)."""

    system = (system if system is not None else _platform.system()).lower()
    machine = (machine if machine is not None else _platform.machine()).lower()
    x64 = machine in {"amd64", "x86_64", "x64"}
    arm64 = machine in {"arm64", "aarch64"}
    if system == "windows" and x64:
        return WINDOWS_X64
    if system == "darwin" and arm64:
        return MACOS_ARM64
    if system == "linux" and x64:
        return LINUX_X64
    return None


def select_build(platform: str | None, acceleration: str) -> Build | None:
    """The build for ``platform`` and ``acceleration``; the CPU build when that acceleration has
    none here; None when nothing runs on this platform."""

    if platform is None:
        return None
    return BUILDS.get((platform, acceleration)) or BUILDS.get((platform, CPU))


__all__ = [
    "ACCELERATIONS",
    "Asset",
    "BUILDS",
    "Build",
    "CPU",
    "CUDA_12",
    "CUDA_13",
    "LICENCE",
    "LINUX_X64",
    "MACOS_ARM64",
    "METAL",
    "PLATFORMS",
    "RELEASE",
    "RELEASE_NAME",
    "VULKAN",
    "WINDOWS_X64",
    "platform_key",
    "select_build",
]
