"""Exporting a trained personal model: the HF-format adapter and a ready-to-run GGUF (D14, O26).

The adapter is the trainer's own output (``adapter_model.safetensors`` and ``adapter_config.json``),
checked against what the training engine reported when it finished, and copied beside the version's
other files. The GGUF is built by the training engine's ``export`` mode, out of Row-Bot's process:
the adapter merged into its 16-bit starting model on the CPU, converted with llama.cpp's own
converter and quantized with llama.cpp's own ``llama-quantize`` (never Unsloth's GGUF path, which
clones, builds and installs). Q8_0 for 2B-class models (Q4_K_M lost 2 of 24 tasks in L0d), Q4_K_M
where a larger tier needs it: the recipe says which. The whole export runs under the engine's
switches and network guard, as one owned process tree Row-Bot can stop.

Disk is checked first: the merged model and the 16-bit GGUF sit side by side at the peak (L2b: 3.8
GB merged plus 3.8 GB BF16 for the 2B), then the quantized file (2.0 GB at Q8_0).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ADAPTER_FILES = ("adapter_model.safetensors", "adapter_config.json")
WORK = ".export"
EXPORT_SECONDS = 3600.0
MARGIN_BYTES = 1_000_000_000
# Sizes as a share of the starting model's 16-bit weights (L2b, Qwen3.5 2B: 4.55 GB of weights, a
# 4.45 GB merged folder, a 3.78 GB BF16 GGUF, 2.01 GB at Q8_0 and 1.27 GB at Q4_K_M).
MERGED_SHARE = 1.0
BF16_SHARE = 0.85
QUANT_SHARE = {"Q8_0": 0.46, "Q4_K_M": 0.29}
STEPS = ("merge", "convert", "quantize")


class ExportError(RuntimeError):
    """The export didn't finish; ``code`` is stable: ``not_enough_disk`` (with ``needs`` and ``free``),
    ``adapter_missing``, ``engine_missing``, ``tools_missing``, ``export_failed`` (``step`` says
    where) or ``stopped``."""

    def __init__(self, code: str, *, needs: int | None = None, free: int | None = None,
                 step: str | None = None) -> None:
        self.code = code
        self.needs = needs
        self.free = free
        self.step = step
        super().__init__(code)


@dataclass(frozen=True)
class ExportResult:
    gguf: Path
    quantization: str
    gguf_bytes: int
    gguf_sha256: str
    adapter: Path
    adapter_bytes: int
    adapter_sha256: str
    seconds: float
    steps: dict[str, Any] = field(default_factory=dict)
    blocked_connections: int = 0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def disk_needed(weights_bytes: int, quantization: str) -> int:
    """The most room an export takes at once (the merged model and its BF16 GGUF side by side,
    then the BF16 GGUF and the quantized one), plus a margin."""

    weights = max(0, int(weights_bytes))
    merged = math.ceil(weights * MERGED_SHARE)
    bf16 = math.ceil(weights * BF16_SHARE)
    quant = math.ceil(weights * QUANT_SHARE.get(quantization, 0.5))
    return max(merged + bf16, bf16 + quant) + MARGIN_BYTES


def copy_adapter(source: Path, target: Path, expected: dict[str, str] | None = None) -> tuple[Path, int, str]:
    """The trained adapter's two files into ``target`` (checked against the engine's report when it
    gave one). Returns (folder, bytes, the weights file's SHA-256)."""

    source, target = Path(source), Path(target)
    target.mkdir(parents=True, exist_ok=True)
    total = 0
    weights_sha = ""
    for name in ADAPTER_FILES:
        path = source / name
        if not path.is_file():
            raise ExportError("adapter_missing")
        sha = sha256_file(path)
        if expected and expected.get(name) and expected[name] != sha:
            raise ExportError("adapter_missing")
        shutil.copyfile(path, target / name)
        total += path.stat().st_size
        if name == ADAPTER_FILES[0]:
            weights_sha = sha
    return target, total, weights_sha


def free_bytes(path: Path) -> int | None:
    current = Path(path)
    while not current.exists() and current.parent != current:
        current = current.parent
    try:
        return int(shutil.disk_usage(current).free)
    except OSError:
        return None


def export_gguf(
    *,
    engine: Any,
    weights: Path,
    weights_bytes: int,
    adapter: Path,
    folder: Path,
    name: str,
    quantization: str,
    check: Callable[[], None] = lambda: None,
    on_step: Callable[[str], None] = lambda _step: None,
    runner: Callable[..., Any] | None = None,
    disk_free: Callable[[Path], int | None] = free_bytes,
    untouched: bool = False,
) -> ExportResult:
    """Build ``<name>-<quantization>.gguf`` in ``folder`` from the starting model and ``adapter`` with
    the installed engine (``engine``: its status, with ``python``, ``site`` and ``llama``).

    ``untouched``: the starting model as it is, with no adapter (the baseline a first version is
    compared with, L2c2); nothing is merged."""

    from row_bot.learning.training import engine as engine_module

    if getattr(engine, "python", None) is None or getattr(engine, "site", None) is None:
        raise ExportError("engine_missing")
    llama = getattr(engine, "llama", None)
    if llama is None or engine_module.llama_tools_missing(Path(llama)):
        raise ExportError("tools_missing")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    needs = disk_needed(weights_bytes, quantization)
    free = disk_free(folder)
    if free is not None and free < needs:
        raise ExportError("not_enough_disk", needs=needs, free=free)
    work = folder / WORK
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    home = folder / ".export-home"
    shutil.rmtree(home, ignore_errors=True)
    env = engine_module._base_environment(home)  # noqa: SLF001 - the engine's own clean environment
    env.update(engine_module.engine_variables())
    argv = [str(engine.python), "-I", "-S", str(engine_module.ENTRY), "export", "--site", str(engine.site),
            "--model", str(weights), "--adapter", "none" if untouched else str(adapter), "--out", str(work),
            "--llama", str(llama), "--name", name, "--quant", quantization]
    seen = {"step": "merge"}  # said below, before the engine starts

    def watched() -> None:
        check()
        try:
            step = (work / "export.step").read_text(encoding="utf-8").strip()
        except OSError:
            return
        if step and step != seen["step"]:
            seen["step"] = step
            on_step("quantize" if step.startswith("quantize") else step)

    started = time.monotonic()
    on_step("merge")
    try:
        result = (runner or engine_module.run_owned)(argv, cwd=home / "work", env=env, timeout=EXPORT_SECONDS,
                                                     check=watched)
    except engine_module.EngineError as error:
        raise ExportError("export_failed", step=seen["step"] or "merge") from error
    except BaseException as error:
        if type(error).__name__ in {"Stopped", "StopExport"}:
            raise ExportError("stopped") from error
        raise
    finally:
        shutil.rmtree(home, ignore_errors=True)
    try:
        report = json.loads((work / "export.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = {}
    produced = work / f"{name}-{quantization}.gguf"
    if getattr(result, "code", 1) != 0 or not produced.is_file() or report.get("failed"):
        failed = report.get("failed") if isinstance(report.get("failed"), dict) else {}
        step = str(failed.get("step") or seen["step"] or "merge")
        logger.warning("Learning's export failed at %s (%s)", step, failed.get("error_type"))
        shutil.rmtree(work, ignore_errors=True)
        if failed.get("error_type") == "OSError" and (disk_free(folder) or 0) < MARGIN_BYTES:
            raise ExportError("not_enough_disk", needs=needs, free=disk_free(folder))
        raise ExportError("export_failed", step="quantize" if step.startswith("quantize") else step)
    final = folder / produced.name
    final.unlink(missing_ok=True)
    produced.replace(final)
    shutil.rmtree(work, ignore_errors=True)
    adapter_path = Path(adapter)
    adapter_bytes = 0 if untouched else sum((adapter_path / item).stat().st_size for item in ADAPTER_FILES
                                            if (adapter_path / item).is_file())
    return ExportResult(
        gguf=final, quantization=quantization, gguf_bytes=final.stat().st_size, gguf_sha256=sha256_file(final),
        adapter=adapter_path, adapter_bytes=adapter_bytes,
        adapter_sha256=sha256_file(adapter_path / ADAPTER_FILES[0])
        if not untouched and (adapter_path / ADAPTER_FILES[0]).is_file() else "",
        seconds=round(time.monotonic() - started, 1), steps=dict(report.get("steps") or {}),
        blocked_connections=int(report.get("blocked_connections") or 0),
    )


__all__ = [
    "ADAPTER_FILES",
    "ExportError",
    "ExportResult",
    "copy_adapter",
    "disk_needed",
    "export_gguf",
    "free_bytes",
    "sha256_file",
]
