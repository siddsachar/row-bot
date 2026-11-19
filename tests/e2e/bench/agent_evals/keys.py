"""API keys for the paid calls the owner approved (judges, embeddings), from one file the owner fills in.

``ROW_BOT_BENCH_KEYS_FILE`` names a plain ``NAME=value`` file (``OPENAI_API_KEY=...``). It is read at run
start only: the orchestrator checks that every key the run needs is there (names, never values), and the
worker puts those keys into its own process's environment, where Row-Bot's ``api_keys.get_key`` finds them
and the harness's redaction scrubs them from every trajectory and log line. A key never goes into a spec
file, the ledger, a trajectory or the benchmark runner's environment (``worker.runner_env`` drops every
``*KEY*`` name). This module never creates the file.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, MutableMapping

KEYS_FILE_ENV = "ROW_BOT_BENCH_KEYS_FILE"
#: The environment variable Row-Bot reads for each paid provider the bench may use.
PROVIDER_KEYS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


class KeysError(RuntimeError):
    """A key the run needs is not available. The message names the key, never a value."""


def keys_file(env: MutableMapping[str, str] | None = None) -> Path | None:
    text = str((os.environ if env is None else env).get(KEYS_FILE_ENV, "") or "").strip()
    return Path(text) if text else None


def names_for(providers: Iterable[str]) -> list[str]:
    """The key names the paid *providers* need; a paid provider the bench has no key name for is refused."""
    names = []
    for provider in providers:
        name = PROVIDER_KEYS.get(str(provider).strip().lower())
        if name is None:
            raise KeysError(f"no bench key is known for the paid provider {provider!r}")
        if name not in names:
            names.append(name)
    return names


def _read(path: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        found[name] = value
    return found


def problem(names: Iterable[str], path: Path | None) -> str:
    """Why the run can't have *names* from the keys file, or '' (no value is read into the answer)."""
    wanted = list(names)
    if not wanted:
        return ""
    if path is None:
        return f"set {KEYS_FILE_ENV} to the keys file the owner fills in (it needs {', '.join(wanted)})"
    if not path.is_file():
        return f"the keys file {path} does not exist (it needs {', '.join(wanted)}); the owner creates it"
    present = {name for name, value in _read(path).items() if value}
    missing = [name for name in wanted if name not in present]
    return f"the keys file has no value for {', '.join(missing)}" if missing else ""


def load_into(environ: MutableMapping[str, str], names: Iterable[str], path: Path | None) -> list[str]:
    """Put *names* from the keys file into *environ* (the worker's own process). Returns the names loaded."""
    wanted = list(names)
    why = problem(wanted, path)
    if why:
        raise KeysError(why)
    assert path is not None
    values = _read(path)
    for name in wanted:
        environ[name] = values[name]
    return wanted
