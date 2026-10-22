"""The training engine's lock (``row_bot/learning_engine/uv.lock``): exactly what to download.

The engine is its own locked project (E1, O25). Row-Bot reads the lock with ``tomllib`` and
works out, for one variant (``cu130`` or ``cu128``) on this computer, every package to
install and the one wheel of each that fits CPython 3.12 here, with its address, SHA-256 and
size. Nothing is resolved at run time and nothing is downloaded that isn't in the lock.

The walk follows uv's own lock format: the root project's dependencies plus the variant's
extra, each dependency's marker evaluated for the engine's interpreter (not Row-Bot's), and
the extras a dependency asks for. uv writes the variants' conflict as markers on ``extra``
(``extra-23-row-bot-learning-engine-cu130``), so the active variant is that one extra.
"""

from __future__ import annotations

import hashlib
import platform as _platform
import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from packaging import tags
from packaging.markers import Marker, default_environment
from packaging.utils import canonicalize_name, parse_wheel_filename

ENGINE_DIR = Path(__file__).resolve().parents[2] / "learning_engine"
LOCK_FILE = ENGINE_DIR / "uv.lock"
PROJECT = "row-bot-learning-engine"
VARIANTS = ("cu130", "cu128")
# The engine's interpreter: uv's CPython build, the version L0d measured.
PYTHON_VERSION = "3.12.15"
PYTHON_TAG = (3, 12)
# uv downloads this interpreter itself (python-build-standalone, checked against uv's own hashes);
# about this much, counted in the download's estimate but not in its progress.
PYTHON_DOWNLOAD_BYTES = 32_000_000
# The PyTorch index publishes no sizes; measured with a HEAD request on 16 Oct 2026. Any other
# wheel without a size is asked for its size before the download starts.
PINNED_SIZES: Mapping[str, int] = {
    "torch-2.12.1+cu130-cp312-cp312-win_amd64.whl": 1_926_431_198,
    "torch-2.12.1+cu130-cp312-cp312-manylinux_2_28_x86_64.whl": 532_716_184,
    "torchvision-0.27.1+cu130-cp312-cp312-win_amd64.whl": 9_083_741,
    "torchvision-0.27.1+cu130-cp312-cp312-manylinux_2_28_x86_64.whl": 7_664_277,
    "torch-2.11.0+cu128-cp312-cp312-win_amd64.whl": 2_753_189_216,
    "torch-2.11.0+cu128-cp312-cp312-manylinux_2_28_x86_64.whl": 820_326_880,
    "torchvision-0.26.0+cu128-cp312-cp312-win_amd64.whl": 9_585_013,
    "torchvision-0.26.0+cu128-cp312-cp312-manylinux_2_28_x86_64.whl": 8_057_226,
}
# What a wheel whose size isn't known yet counts as in an estimate (before it is asked).
UNKNOWN_WHEEL_BYTES = 50_000_000
# Never in the engine (E14, O25): telemetry, remote caches and kernels from the Hub, and xformers.
FORBIDDEN = frozenset({"wandb", "kernels", "redis", "trackio", "xformers", "comet-ml", "mlflow", "neptune"})
# The packages whose import the engine's check proves (training needs each).
CORE = ("torch", "transformers", "trl", "peft", "accelerate", "unsloth", "unsloth-zoo", "bitsandbytes")

# llama.cpp's own tools build each personal model's GGUF (O26; never Unsloth's GGUF path, which
# clones, builds and installs): one pinned release, its CPU build for this computer (llama-quantize;
# converting and quantizing need no graphics card) and the same tag's converter
# (``convert_hf_to_gguf.py`` with its ``conversion`` and ``gguf-py`` packages, run by the engine's
# own interpreter). Sizes and SHA-256 as GitHub publishes them for the release's assets; the source
# archive's as downloaded and checked in L0d.
LLAMA_CPP_RELEASE = "b11669"
LLAMA_CPP_COMMIT = "620d2225cc436ab0b001e3200e590e3b286ef9ab"
_LLAMA_DOWNLOAD = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_CPP_RELEASE}"


class LockError(ValueError):
    """The lock can't give an install plan here; ``code`` is stable."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class LockedWheel:
    """One wheel to download and install."""

    name: str
    version: str
    url: str
    filename: str
    sha256: str
    size: int | None

    @property
    def known_size(self) -> int | None:
        return self.size if self.size is not None else PINNED_SIZES.get(self.filename)

    def requirement(self) -> str:
        return f"{self.name}=={self.version} --hash=sha256:{self.sha256}"


# The CPU build of llama.cpp's tools for each platform the engine installs on (by wheel platform).
LLAMA_CPP_TOOLS: dict[str, LockedWheel] = {
    "windows-x64": LockedWheel(
        "llama.cpp-tools", LLAMA_CPP_RELEASE, f"{_LLAMA_DOWNLOAD}/llama-{LLAMA_CPP_RELEASE}-bin-win-cpu-x64.zip",
        f"llama-{LLAMA_CPP_RELEASE}-bin-win-cpu-x64.zip",
        "37b3efcf962847ca869745fd4ccf7f5e7e8dacfeff9fa3523b4ca8cdc66d632b", 19_537_886),
    "linux-x64": LockedWheel(
        "llama.cpp-tools", LLAMA_CPP_RELEASE, f"{_LLAMA_DOWNLOAD}/llama-{LLAMA_CPP_RELEASE}-bin-ubuntu-x64.tar.gz",
        f"llama-{LLAMA_CPP_RELEASE}-bin-ubuntu-x64.tar.gz",
        "cf07c1c112d709bd67e522017471de57ed0ec8bea031d732c5422351c07f65ac", 17_851_611),
}
# The converter: the release tag's source archive (only its Python converter is unpacked).
LLAMA_CPP_SOURCE = LockedWheel(
    "llama.cpp-converter", LLAMA_CPP_RELEASE,
    f"https://github.com/ggml-org/llama.cpp/archive/refs/tags/{LLAMA_CPP_RELEASE}.zip",
    f"llama.cpp-{LLAMA_CPP_RELEASE}-src.zip",
    "bf22d4717daf6db1ecff37bbe702bb53fcca2b7ab63a103c6d679aa65d5d932e", 40_098_144)


# The server Row-Bot runs personal models in (O75): the same release's CUDA build of ``llama-server``
# and the CUDA runtime it needs, unpacked side by side. The driver picks the CUDA line as it picks the
# engine's (O25): CUDA 13 with a driver of 580 or newer (the ``cu130`` engine), CUDA 12 with 570-579.
# Sizes and SHA-256 as GitHub publishes them for the release's assets.
LLAMA_CPP_SERVER: dict[tuple[str, str], tuple[LockedWheel, LockedWheel]] = {
    ("windows-x64", "cu130"): (
        LockedWheel("llama.cpp-server", LLAMA_CPP_RELEASE,
                    f"{_LLAMA_DOWNLOAD}/llama-{LLAMA_CPP_RELEASE}-bin-win-cuda-13.4-x64.zip",
                    f"llama-{LLAMA_CPP_RELEASE}-bin-win-cuda-13.4-x64.zip",
                    "3b92d5c064f5032e603e531d3ec8961f3bd6613e5dfad047399a92dec8e95dbc", 154_582_227),
        LockedWheel("llama.cpp-cuda-runtime", LLAMA_CPP_RELEASE,
                    f"{_LLAMA_DOWNLOAD}/cudart-llama-bin-win-cuda-13.4-x64.zip",
                    "cudart-llama-bin-win-cuda-13.4-x64.zip",
                    "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668", 423_535_356)),
    ("windows-x64", "cu128"): (
        LockedWheel("llama.cpp-server", LLAMA_CPP_RELEASE,
                    f"{_LLAMA_DOWNLOAD}/llama-{LLAMA_CPP_RELEASE}-bin-win-cuda-12.4-x64.zip",
                    f"llama-{LLAMA_CPP_RELEASE}-bin-win-cuda-12.4-x64.zip",
                    "c2da1f73ed176ae093ac5aceb978a7b167a16113f6d74c6789eeaee2135cb8dc", 266_582_698),
        LockedWheel("llama.cpp-cuda-runtime", LLAMA_CPP_RELEASE,
                    f"{_LLAMA_DOWNLOAD}/cudart-llama-bin-win-cuda-12.4-x64.zip",
                    "cudart-llama-bin-win-cuda-12.4-x64.zip",
                    "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6", 391_443_627)),
    ("linux-x64", "cu130"): (
        LockedWheel("llama.cpp-server", LLAMA_CPP_RELEASE,
                    f"{_LLAMA_DOWNLOAD}/llama-{LLAMA_CPP_RELEASE}-bin-ubuntu-cuda-13.4-x64.tar.gz",
                    f"llama-{LLAMA_CPP_RELEASE}-bin-ubuntu-cuda-13.4-x64.tar.gz",
                    "80878473d27278dbe9c38fb7e704f292a51e889d3c28a1e863137cdee1e2ba4e", 153_679_828),
        LockedWheel("llama.cpp-cuda-runtime", LLAMA_CPP_RELEASE,
                    f"{_LLAMA_DOWNLOAD}/cudart-llama-{LLAMA_CPP_RELEASE}-bin-ubuntu-cuda-13.4-x64.tar.gz",
                    f"cudart-llama-{LLAMA_CPP_RELEASE}-bin-ubuntu-cuda-13.4-x64.tar.gz",
                    "1108bd9300a281e8eabb18d3fa028d4240ecd983f9f90dbbbc3297671016d697", 440_236_547)),
    ("linux-x64", "cu128"): (
        LockedWheel("llama.cpp-server", LLAMA_CPP_RELEASE,
                    f"{_LLAMA_DOWNLOAD}/llama-{LLAMA_CPP_RELEASE}-bin-ubuntu-cuda-12.8-x64.tar.gz",
                    f"llama-{LLAMA_CPP_RELEASE}-bin-ubuntu-cuda-12.8-x64.tar.gz",
                    "4703ed1a3cd6e8504affb6cb9866f501e96d634d68a002dc9a8251c9e2819b49", 172_525_113),
        LockedWheel("llama.cpp-cuda-runtime", LLAMA_CPP_RELEASE,
                    f"{_LLAMA_DOWNLOAD}/cudart-llama-{LLAMA_CPP_RELEASE}-bin-ubuntu-cuda-12.8-x64.tar.gz",
                    f"cudart-llama-{LLAMA_CPP_RELEASE}-bin-ubuntu-cuda-12.8-x64.tar.gz",
                    "ab3eda89a82096bcba00b6448218c3c11eec2016b0f38d64884dc29736549f26", 594_377_811)),
}


def server_files(variant: str, platforms: Iterable[str] | None = None) -> tuple[LockedWheel, ...]:
    """The pinned server build and its CUDA runtime for ``variant`` on these platform tags (this
    computer's by default); empty where Row-Bot has no build (macOS, Arm)."""

    return LLAMA_CPP_SERVER.get((tools_platform(platforms) or "", variant), ())


def tools_platform(platforms: Iterable[str] | None = None) -> str | None:
    """Which llama.cpp build fits these wheel platform tags (this computer's by default)."""

    found = list(platforms) if platforms is not None else list(tags.platform_tags())
    for tag in found:
        if tag == "win_amd64":
            return "windows-x64"
        if tag.endswith("_x86_64") and ("linux" in tag):
            return "linux-x64"
    return None


def llama_tools(platforms: Iterable[str] | None = None) -> tuple[LockedWheel, ...]:
    """The pinned llama.cpp files to download for these platform tags: the CPU build, then the converter."""

    platform_id = tools_platform(platforms)
    build = LLAMA_CPP_TOOLS.get(platform_id or "")
    if build is None:
        raise LockError("no_wheel", "llama.cpp tools")
    return (build, LLAMA_CPP_SOURCE)


@dataclass(frozen=True)
class EnginePlan:
    """Everything one engine install needs on this computer: the locked wheels, uv, and llama.cpp's
    tools for the export."""

    variant: str
    python_version: str
    wheels: tuple[LockedWheel, ...]
    uv: LockedWheel
    tools: tuple[LockedWheel, ...] = ()

    @property
    def identity(self) -> str:
        """Changes whenever what would be installed changes (an app update's new lock or tools)."""

        text = "\n".join([self.variant, self.python_version, *sorted(w.requirement() for w in self.wheels),
                          *sorted(f"{tool.filename} {tool.sha256}" for tool in self.tools)])
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @property
    def names(self) -> frozenset[str]:
        return frozenset(wheel.name for wheel in self.wheels)

    def tools_bytes(self) -> int:
        return sum(tool.known_size or 0 for tool in self.tools)

    def download_bytes(self) -> int:
        """About how much downloads: every wheel, uv itself, the interpreter and llama.cpp's tools."""

        sizes = [w.known_size or UNKNOWN_WHEEL_BYTES for w in (*self.wheels, self.uv)]
        return sum(sizes) + PYTHON_DOWNLOAD_BYTES + self.tools_bytes()

    def requirements(self) -> str:
        """The pins uv installs (with ``--no-deps --require-hashes``), one per line."""

        return "".join(f"{wheel.requirement()}\n" for wheel in sorted(self.wheels, key=lambda w: w.name))


def load(path: Path | None = None) -> dict[str, Any]:
    raw = Path(path or LOCK_FILE).read_bytes()
    if len(raw) > 16 * 1024 * 1024:
        raise LockError("lock_too_large")
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise LockError("lock_unreadable") from error
    if document.get("version") != 1 or not isinstance(document.get("package"), list):
        raise LockError("lock_unreadable", "format")
    return document


def lock_sha256(path: Path | None = None) -> str:
    return hashlib.sha256(Path(path or LOCK_FILE).read_bytes()).hexdigest()


def target_environment(python_version: str = PYTHON_VERSION,
                       overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """Marker values for the engine's interpreter on this computer (not Row-Bot's own)."""

    environment = dict(default_environment())
    major_minor = ".".join(python_version.split(".")[:2])
    environment.update({
        "python_version": major_minor,
        "python_full_version": python_version,
        "implementation_name": "cpython",
        "implementation_version": python_version,
        "platform_python_implementation": "CPython",
    })
    environment.update(dict(overrides or {}))
    return environment


def target_tags(platforms: Iterable[str] | None = None) -> list[tags.Tag]:
    """Wheel tags CPython 3.12 accepts here, best first."""

    found = list(platforms) if platforms is not None else list(tags.platform_tags())
    return [
        *tags.cpython_tags(PYTHON_TAG, abis=[f"cp{PYTHON_TAG[0]}{PYTHON_TAG[1]}"], platforms=found),
        *tags.compatible_tags(PYTHON_TAG, f"cp{PYTHON_TAG[0]}{PYTHON_TAG[1]}", found),
    ]


def _extra_marker(variant: str) -> str:
    return f"extra-{len(PROJECT)}-{PROJECT}-{variant}"


def _applies(marker: str | None, environment: Mapping[str, str]) -> bool:
    if not marker:
        return True
    try:
        return bool(Marker(marker).evaluate(dict(environment)))
    except Exception as error:  # an unreadable marker: the lock is wrong, never guess
        raise LockError("lock_unreadable", "marker") from error


def _filename(url: str) -> str:
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1])


def _best_wheel(package: dict[str, Any], ranking: Mapping[tags.Tag, int]) -> LockedWheel:
    best: tuple[int, dict[str, Any], str] | None = None
    for wheel in package.get("wheels") or ():
        url = str(wheel.get("url") or "")
        filename = _filename(url)
        try:
            wheel_tags = parse_wheel_filename(filename)[3]
        except Exception:
            continue
        ranks = [ranking[tag] for tag in wheel_tags if tag in ranking]
        if ranks and (best is None or min(ranks) < best[0]):
            best = (min(ranks), wheel, filename)
    if best is None:
        raise LockError("no_wheel", f"{package.get('name')} {package.get('version')}")
    _, wheel, filename = best
    digest = str(wheel.get("hash") or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise LockError("lock_unreadable", "hash")
    url = str(wheel["url"])
    if urlsplit(url).scheme != "https":
        raise LockError("lock_unreadable", "url")
    size = wheel.get("size")
    return LockedWheel(
        name=canonicalize_name(str(package["name"])),
        version=str(package["version"]),
        url=url,
        filename=filename,
        sha256=digest.removeprefix("sha256:"),
        size=int(size) if isinstance(size, int) and size > 0 else None,
    )


def _find(packages: list[dict[str, Any]], dependency: Mapping[str, Any]) -> dict[str, Any]:
    name = canonicalize_name(str(dependency["name"]))
    found = [item for item in packages if canonicalize_name(str(item.get("name"))) == name]
    if "version" in dependency:
        found = [item for item in found if str(item.get("version")) == str(dependency["version"])]
    if "source" in dependency:
        found = [item for item in found if item.get("source") == dependency["source"]]
    if len(found) != 1:
        raise LockError("lock_unreadable", f"ambiguous {name}")
    return found[0]


def engine_plan(
    variant: str,
    *,
    lock: dict[str, Any] | None = None,
    platforms: Iterable[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> EnginePlan:
    """The engine to install for ``variant`` on this computer (or the given platform tags and
    marker values, which tests use to plan for another computer)."""

    if variant not in VARIANTS:
        raise LockError("unknown_variant", variant)
    document = lock if lock is not None else load()
    packages: list[dict[str, Any]] = list(document["package"])
    env = target_environment(overrides=environment)
    env["extra"] = _extra_marker(variant)
    ranking = {tag: index for index, tag in enumerate(target_tags(platforms))}
    roots = [item for item in packages if item.get("name") == PROJECT and "virtual" in (item.get("source") or {})]
    if len(roots) != 1:
        raise LockError("lock_unreadable", "project")
    root = roots[0]
    pending: list[tuple[Mapping[str, Any], tuple[str, ...]]] = []
    for dependency in root.get("dependencies") or ():
        pending.append((dependency, ()))
    extra_dependencies = (root.get("optional-dependencies") or {}).get(variant)
    if not extra_dependencies:
        raise LockError("unknown_variant", variant)
    for dependency in extra_dependencies:
        pending.append((dependency, ()))
    chosen: dict[str, dict[str, Any]] = {}
    extras_seen: dict[str, set[str]] = {}
    while pending:
        dependency, _ = pending.pop()
        if not _applies(dependency.get("marker"), env):
            continue
        package = _find(packages, dependency)
        key = canonicalize_name(str(package["name"]))
        previous = chosen.get(key)
        if previous is not None and previous is not package:
            raise LockError("lock_unreadable", f"two versions of {key}")
        requested = set(dependency.get("extra") or ())
        seen = extras_seen.setdefault(key, set())
        if previous is not None and requested <= seen:
            continue
        new_extras = requested - seen
        seen.update(requested)
        if previous is None:
            chosen[key] = package
            pending.extend((item, ()) for item in package.get("dependencies") or ())
        for extra in sorted(new_extras):
            pending.extend((item, ()) for item in (package.get("optional-dependencies") or {}).get(extra) or ())
    forbidden = FORBIDDEN & set(chosen)
    if forbidden:
        raise LockError("forbidden_package", ",".join(sorted(forbidden)))
    wheels = tuple(sorted((_best_wheel(item, ranking) for item in chosen.values()), key=lambda w: w.name))
    return EnginePlan(variant, PYTHON_VERSION, wheels, uv_wheel(document, platforms=platforms),
                      llama_tools(platforms))


_plans: dict[tuple[str, Any], EnginePlan] = {}


def cached_plan(variant: str) -> EnginePlan:
    """``engine_plan(variant)`` for this computer, read again only when the lock file changes."""

    try:
        info = LOCK_FILE.stat()
        key = (variant, (str(LOCK_FILE), info.st_mtime_ns, info.st_size))
    except OSError as error:
        raise LockError("lock_unreadable", "missing") from error
    found = _plans.get(key)
    if found is None:
        found = engine_plan(variant)
        if len(_plans) > 8:
            _plans.clear()
        _plans[key] = found
    return found


def uv_wheel(lock: dict[str, Any] | None = None, *, platforms: Iterable[str] | None = None) -> LockedWheel:
    """The pinned uv (the ``bootstrap`` group) for this computer: Row-Bot fetches it first."""

    document = lock if lock is not None else load()
    found = [item for item in document["package"] if item.get("name") == "uv"]
    if len(found) != 1:
        raise LockError("lock_unreadable", "uv")
    ranking = {tag: index for index, tag in enumerate(target_tags(platforms))}
    return _best_wheel(found[0], ranking)


def every_package_name(lock: dict[str, Any] | None = None) -> frozenset[str]:
    """Every package the lock names (for the telemetry checks)."""

    document = lock if lock is not None else load()
    return frozenset(canonicalize_name(str(item.get("name"))) for item in document["package"])


def machine() -> str:
    return _platform.machine().lower()


__all__ = [
    "CORE",
    "ENGINE_DIR",
    "EnginePlan",
    "FORBIDDEN",
    "LLAMA_CPP_RELEASE",
    "LLAMA_CPP_SERVER",
    "LLAMA_CPP_SOURCE",
    "LLAMA_CPP_TOOLS",
    "llama_tools",
    "tools_platform",
    "LOCK_FILE",
    "LockError",
    "LockedWheel",
    "PINNED_SIZES",
    "PYTHON_VERSION",
    "VARIANTS",
    "cached_plan",
    "engine_plan",
    "every_package_name",
    "load",
    "lock_sha256",
    "server_files",
    "target_environment",
    "target_tags",
    "uv_wheel",
]
