"""Export the locked all-extras runtime dependency set for pip installers."""

from __future__ import annotations

import argparse
import difflib
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_PATH = PROJECT_ROOT / "requirements.txt"
PYTORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
HEADER = (
    "# This file is generated from pyproject.toml and uv.lock.\n"
    "# Do not edit by hand.\n"
    "# Regenerate with: python scripts/export_locked_requirements.py\n"
    "# Export command: uv export --locked --all-extras --no-dev --no-hashes "
    "--no-emit-project --output-file requirements.txt\n"
)


def _run_uv_export(raw_output: Path) -> None:
    command = [
        "uv",
        "export",
        "--locked",
        "--all-extras",
        "--no-dev",
        "--no-hashes",
        "--no-emit-project",
        "--output-file",
        str(raw_output),
    ]
    completed = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True)
    if completed.returncode != 0:
        sys.stderr.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        raise subprocess.CalledProcessError(completed.returncode, command)


def _normalize_export(raw_text: str) -> str:
    lines = raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    body: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        body.append(stripped)

    if not any(PYTORCH_CPU_INDEX in line for line in body):
        body.insert(0, f"--extra-index-url {PYTORCH_CPU_INDEX}")

    return HEADER + "\n".join(body) + "\n"


def export_requirements() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        raw_output = Path(tmp) / "requirements.raw.txt"
        _run_uv_export(raw_output)
        return _normalize_export(raw_output.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if requirements.txt is stale")
    args = parser.parse_args(argv)

    generated = export_requirements()
    if args.check:
        current = REQUIREMENTS_PATH.read_text(encoding="utf-8") if REQUIREMENTS_PATH.exists() else ""
        if current != generated:
            diff = difflib.unified_diff(
                current.splitlines(keepends=True),
                generated.splitlines(keepends=True),
                fromfile="requirements.txt",
                tofile="generated requirements.txt",
            )
            sys.stderr.writelines(diff)
            return 1
        print("requirements.txt is current")
        return 0

    REQUIREMENTS_PATH.write_text(generated, encoding="utf-8", newline="\n")
    print(f"wrote {REQUIREMENTS_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
