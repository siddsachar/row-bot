"""Trusted prepared-Python MCP entry point; no dependency or SDK installation.

The existing MCP client owns stdio/protocol and approvals. This bootstrap owns
source-only imports and descendant lifetime before executing the declared entry.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import runpy
import signal
import sys
import types


def invocation(arguments: list[str]) -> tuple[list[str], list[str]]:
    """Separate explicit interpreter flags from module/code/script arguments."""
    flags, args = [], list(arguments)
    while args and args[0].startswith("-") and args[0] not in {"-m", "-c", "--", "-"}:
        flag = args.pop(0)
        if flag in {"-O", "-OO", "-u", "-B", "-s", "-E", "-I", "-S", "-q", "-b", "-bb"}:
            flags.append(flag)
        elif flag in {"-W", "-X"} and args:
            flags.extend((flag, args.pop(0)))
        elif flag.startswith(("-W", "-X")) and len(flag) > 2:
            flags.append(flag)
        else:
            raise RuntimeError("worker_arguments_invalid")
    if args and args[0] == "--":
        args.pop(0)
    if not args or args[0] == "-" or (args[0] in {"-m", "-c"} and len(args) < 2):
        raise RuntimeError("worker_arguments_invalid")
    return flags, args


def _trusted(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


def main() -> None:
    environment, root = (Path(value).resolve(strict=True) for value in sys.argv[1:3])
    _, args = invocation(sys.argv[3:])
    # Private noninheritable self-owned handle survives until process death,
    # including SDK terminate/kill, so no plugin entry can escape startup timing.
    job = None
    if os.name == "nt":
        ownership = _trusted("_row_bot_worker_ownership", "worker_ownership.py")
        job = ownership.WindowsJob(types.SimpleNamespace(_handle=-1))
    elif os.getsid(0) != os.getpid() or os.getpgrp() != os.getpid():
        raise RuntimeError("worker_ownership_unavailable")
    bootstrap = _trusted("_row_bot_source_bootstrap", "worker_process.py")
    bootstrap._source_imports(environment)
    sys.path.insert(0, str(root))
    try:
        if args[0] == "-m":
            sys.argv = [args[1], *args[2:]]
            runpy.run_module(args[1], run_name="__main__", alter_sys=True)
        elif args[0] == "-c":
            sys.argv = ["-c", *args[2:]]
            exec(compile(args[1], "<string>", "exec"), {"__name__": "__main__", "__builtins__": __builtins__})
        else:
            path = Path(args[0])
            if not path.is_absolute():
                path = Path.cwd() / path
            if ".." in path.parts or path.suffix in {".pyc", ".pyo"} or {".git", "__pycache__"}.intersection(path.parts):
                raise RuntimeError("worker_source_changed")
            path = path.resolve(strict=True)
            if not path.is_relative_to(root) and not path.is_relative_to(environment):
                raise RuntimeError("worker_source_changed")
            if path.is_dir():
                path = path / "__main__.py"
            sys.argv = [str(path), *args[1:]]
            sys.path.insert(0, str(path.parent))
            namespace = {"__name__": "__main__", "__file__": str(path), "__builtins__": __builtins__}
            exec(compile(path.read_bytes(), str(path), "exec"), namespace)
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        if os.name != "nt":
            # The SDK creates this process's private session. Normal entry
            # return also retires remaining descendants, not just timeout.
            os.killpg(os.getpid(), signal.SIGKILL)
        # Keep the native Windows handle reachable until interpreter exit.
        _ = job


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        raise SystemExit(exc.code if type(exc.code) is int else 1) from None
    except BaseException:
        # Private plugin exceptions/paths must not become host diagnostics.
        sys.stderr.write("Prepared plugin MCP process ended.\n")
        raise SystemExit(1) from None
