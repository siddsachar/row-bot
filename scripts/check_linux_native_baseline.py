#!/usr/bin/env python3
"""Verify Linux package native wheels do not require a newer x86_64 baseline."""

from __future__ import annotations

import argparse
import platform
import re
import shutil
import site
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

_X86_BASELINE_MARKERS = {"X86_V2", "X86_V3", "X86_V4"}


def _normalized_arch(value: str | None = None) -> str:
    arch = (value or platform.machine() or "").lower().replace("-", "_")
    if arch in {"amd64", "x64"}:
        return "x86_64"
    if arch == "arm64":
        return "aarch64"
    return arch


def _numpy_cpu_baseline() -> list[str]:
    try:
        try:
            import numpy._core._multiarray_umath as umath
        except ModuleNotFoundError:
            import numpy.core._multiarray_umath as umath  # type: ignore[no-redef]
    except Exception as exc:
        raise RuntimeError(f"could not import NumPy CPU metadata: {exc}") from exc

    baseline = getattr(umath, "__cpu_baseline__", None)
    if baseline is None:
        raise RuntimeError("NumPy does not expose __cpu_baseline__ metadata")
    return [str(feature).upper().replace("-", "_") for feature in baseline]


def _blocked_x86_baselines(features: Iterable[str]) -> list[str]:
    normalized = {str(feature).upper().replace("-", "_") for feature in features}
    return sorted(feature for feature in normalized if feature in _X86_BASELINE_MARKERS)


def _blocked_readelf_baselines(output: str) -> list[str]:
    normalized = output.upper().replace("-", "_")
    blocked: set[str] = set()
    for marker in _X86_BASELINE_MARKERS:
        if marker in normalized:
            blocked.add(marker)
    for version in re.findall(r"X86_64_V([234])", normalized):
        blocked.add(f"X86_V{version}")
    return sorted(blocked)


def _native_search_roots() -> list[Path]:
    roots = {Path(sys.prefix).resolve()}
    for path in site.getsitepackages():
        try:
            roots.add(Path(path).resolve())
        except OSError:
            continue
    return sorted(roots)


def _native_binaries(roots: Iterable[Path]) -> list[Path]:
    binaries: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for pattern in ("*.so", "*.so.*"):
            binaries.update(path for path in root.rglob(pattern) if path.is_file())
    return sorted(binaries)


def _scan_elf_baselines(readelf: str, binaries: Iterable[Path]) -> list[tuple[Path, list[str]]]:
    failures: list[tuple[Path, list[str]]] = []
    for binary in binaries:
        result = subprocess.run(
            [readelf, "-n", str(binary)],
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
        output = f"{result.stdout}\n{result.stderr}"
        blocked = _blocked_readelf_baselines(output)
        if blocked:
            failures.append((binary, blocked))
    return failures


def _print_failures(failures: Iterable[tuple[Path, list[str]]]) -> None:
    print("ERROR: Linux package contains native binaries requiring newer x86_64 CPU baselines:", file=sys.stderr)
    for binary, blocked in failures:
        print(f"  {binary}: {', '.join(blocked)}", file=sys.stderr)
    print("Use baseline-compatible wheels or pin the dependency before publishing the Linux package.", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--arch",
        default=None,
        help="Package architecture to validate. Defaults to platform.machine().",
    )
    parser.add_argument(
        "--skip-elf-scan",
        action="store_true",
        help="Skip readelf scanning and only run package-specific metadata checks.",
    )
    args = parser.parse_args(argv)

    arch = _normalized_arch(args.arch)
    if arch != "x86_64":
        print(f"Linux native CPU baseline check skipped for arch={arch}")
        return 0

    failures: list[tuple[Path, list[str]]] = []

    try:
        numpy_baseline = _numpy_cpu_baseline()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    numpy_blocked = _blocked_x86_baselines(numpy_baseline)
    if numpy_blocked:
        failures.append((Path("numpy.__cpu_baseline__"), numpy_blocked))

    if not args.skip_elf_scan:
        readelf = shutil.which("readelf")
        if not readelf:
            print("ERROR: readelf is required for Linux native baseline verification", file=sys.stderr)
            return 1
        failures.extend(_scan_elf_baselines(readelf, _native_binaries(_native_search_roots())))

    if failures:
        _print_failures(failures)
        return 1

    print(f"Linux native CPU baseline OK for x86_64 package; NumPy baseline={', '.join(numpy_baseline) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())