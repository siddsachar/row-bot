"""A stand-in for uv in the training engine's install tests (L2b). Standard library only.

Run as ``python learning_fake_uv.py <uv arguments>``. It understands the two commands Row-Bot
runs and nothing else:

- ``python install <version> --install-dir <dir> --no-bin --no-registry``: makes
  ``<dir>/cpython-<version>-fake/python.exe`` (``bin/python3`` elsewhere): a placeholder, not a
  real interpreter (the install tests skip the engine's own check);
- ``pip install --python <p> --target <dir> --offline --no-index --find-links <wheels>
  --require-hashes --no-deps --no-build --compile-bytecode --requirement <file>``: for each pin
  ``name==version --hash=sha256:<h>`` it finds that wheel among ``<wheels>``, refuses it unless
  its SHA-256 is ``<h>`` (exit 1, as uv does), and unpacks it into ``<dir>``.

Its own options come first (uv's environment is Row-Bot's isolated one, so nothing reaches it
there): ``--fake-log <file>`` gets one JSON line per run (the arguments and the uv-related
environment); ``--fake-fail "<command>"`` makes that command fail (exit 2).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import zipfile
from pathlib import Path


def _log(path: str | None, argv: list[str]) -> None:
    if not path:
        return
    seen = {key: value for key, value in os.environ.items()
            if key.startswith(("UV_", "HF_", "PIP_", "ROW_BOT", "HUGGING")) or key in ("HOME", "USERPROFILE")}
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"argv": argv, "env": seen}) + "\n")


def _option(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def _normal(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def python_install(argv: list[str]) -> int:
    version = argv[2]
    folder = Path(_option(argv, "--install-dir")) / f"cpython-{version}-fake"
    executable = folder / ("python.exe" if os.name == "nt" else "bin/python3")
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(b"fake interpreter")
    return 0


def pip_install(argv: list[str]) -> int:
    for needed in ("--offline", "--no-index", "--require-hashes", "--no-deps", "--no-build"):
        if needed not in argv:
            sys.stderr.write(f"fake uv: expected {needed}\n")
            return 3
    target = Path(_option(argv, "--target"))
    wheels = Path(_option(argv, "--find-links"))
    lines = Path(_option(argv, "--requirement")).read_text(encoding="utf-8").splitlines()
    available = {}
    for wheel in wheels.glob("*.whl"):
        parts = wheel.name[:-4].split("-")
        available[(_normal(parts[0]), parts[1])] = wheel
    for line in lines:
        line = line.strip()
        if not line:
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==(\S+) --hash=sha256:([0-9a-f]{64})", line)
        if not match:
            sys.stderr.write(f"fake uv: can't read {line!r}\n")
            return 3
        name, version, digest = _normal(match.group(1)), match.group(2), match.group(3)
        wheel = available.get((name, version))
        if wheel is None:
            sys.stderr.write(f"fake uv: {name}=={version} isn't among the downloaded wheels\n")
            return 1
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != digest:
            sys.stderr.write(f"fake uv: hash mismatch for {wheel.name}\n")
            return 1
        with zipfile.ZipFile(wheel) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                relative = member.filename
                data_prefix = re.match(r"[^/]+\.data/(purelib|platlib)/", relative)
                if data_prefix:
                    relative = relative[data_prefix.end():]
                elif re.match(r"[^/]+\.data/", relative):
                    continue
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(member))
    return 0


def main() -> int:
    argv = sys.argv[1:]
    log = fail = None
    while argv and argv[0] in ("--fake-log", "--fake-fail"):
        if argv[0] == "--fake-log":
            log = argv[1]
        else:
            fail = argv[1]
        argv = argv[2:]
    _log(log, argv)
    command = " ".join(argv[:2])
    if fail == command:
        sys.stderr.write(f"fake uv: {command} failed\n")
        return 2
    if command == "python install":
        return python_install(argv)
    if command == "pip install":
        return pip_install(argv)
    sys.stderr.write(f"fake uv: unknown command {argv[:2]}\n")
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
