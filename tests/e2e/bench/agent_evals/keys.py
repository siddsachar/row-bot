"""API keys for the paid calls the owner approved (judges, embeddings).

Two sources, in this order, read at run start only:

1. **The eval profile's own saved keys** (``ROW_BOT_LIVE_EVAL_CODEX_PROFILE``): the key the owner connected
   in that profile's Settings, read from its record file in ``secure-secrets/`` (Windows: DPAPI, the current
   user's key) without importing Row-Bot bound to that folder, so nothing in it is migrated or written.
   Only the one record per key is opened. The owner's everyday profile is never read.
2. **The keys file** (``ROW_BOT_BENCH_KEYS_FILE``): a plain ``NAME=value`` file the owner fills in. This
   module never creates it.

The orchestrator checks that every key the run needs is available (names and "found"/"missing", never
values); the worker puts the keys into its own process's environment, where Row-Bot's ``api_keys.get_key``
finds them and the harness's redaction scrubs them from every trajectory and log line. A key never goes into
a spec file, the ledger, a trajectory or the benchmark runner's environment (``worker.runner_env`` drops
every ``*KEY*`` name).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Iterable, MutableMapping

PROFILE_ENV = "ROW_BOT_LIVE_EVAL_CODEX_PROFILE"
#: Row-Bot's keyring service prefix (``brand.KEYRING_SERVICE_PREFIX``) and its record format
#: (``secret_store._read_windows_user_secret``): a magic prefix, then DPAPI-protected UTF-8 with the
#: record's identity as entropy.
SERVICE_PREFIX = "Row-Bot"
_DPAPI_MAGIC = b"ROWBOT-DPAPI-V1\x00"
_MAX_RECORD = 64 * 1024 + 1024


def profile_folder(env: MutableMapping[str, str] | None = None) -> Path | None:
    text = str((os.environ if env is None else env).get(PROFILE_ENV, "") or "").strip()
    return Path(text) if text else None


def record_path(profile: Path, name: str, *, prefix: str = SERVICE_PREFIX) -> tuple[Path, bytes]:
    """Where *profile* keeps the saved key *name*, and the record's identity (``secret_store``'s layout)."""
    service = f"{prefix}:{hashlib.sha256(str(Path(profile).resolve()).encode('utf-8')).hexdigest()[:12]}"
    identity = f"{service}\x00api_keys:{name}".encode("utf-8")
    return Path(profile) / "secure-secrets" / f"{hashlib.sha256(identity).hexdigest()}.secret", identity


def profile_has(profile: Path | None, name: str) -> bool:
    if profile is None:
        return False
    path, _ = record_path(profile, name)
    return path.is_file() and not path.is_symlink()


def read_profile_key(profile: Path, name: str, *, unprotect: object = None) -> str | None:
    """The saved key *name* of *profile*, or None. Reads that one record file and nothing else."""
    path, identity = record_path(profile, name)
    if not path.is_file() or path.is_symlink() or path.stat().st_size > _MAX_RECORD:
        return None
    payload = path.read_bytes()
    if not payload.startswith(_DPAPI_MAGIC):
        return None
    if unprotect is None:
        if os.name != "nt":
            return None
        from row_bot.secret_store import _unprotect_windows_user_data as unprotect  # noqa: N813 - DPAPI only
    try:
        value = unprotect(payload[len(_DPAPI_MAGIC):], identity).decode("utf-8")  # type: ignore[operator]
    except Exception:  # noqa: BLE001 - an unreadable record is a missing key, never an error message with bytes
        return None
    return value or None

KEYS_FILE_ENV = "ROW_BOT_BENCH_KEYS_FILE"
#: The environment variable Row-Bot reads for each paid provider the bench may use.
PROVIDER_KEYS = {"openrouter": "OPENROUTER_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


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


def problem(names: Iterable[str], path: Path | None, profile: Path | None = None) -> str:
    """Why the run can't have *names* (from the eval profile, else the keys file), or ''. No value is read."""
    wanted = [name for name in names if not profile_has(profile, name)]
    if not wanted:
        return ""
    where = "the eval profile has no saved key for it and " if profile is not None else ""
    if path is None:
        return f"{where}set {KEYS_FILE_ENV} to the keys file the owner fills in (it needs {', '.join(wanted)})"
    if not path.is_file():
        return f"{where}the keys file {path} does not exist (it needs {', '.join(wanted)}); the owner creates it"
    present = {name for name, value in _read(path).items() if value}
    missing = [name for name in wanted if name not in present]
    return f"{where}the keys file has no value for {', '.join(missing)}" if missing else ""


def status(names: Iterable[str], path: Path | None, profile: Path | None) -> dict[str, str]:
    """Per key: ``found (eval profile)``, ``found (keys file)`` or ``missing``. Never a value."""
    in_file = {name for name, value in _read(path).items() if value} if path is not None and path.is_file() else set()
    return {name: "found (eval profile)" if profile_has(profile, name) else "found (keys file)" if name in in_file
            else "missing" for name in names}


def load_into(environ: MutableMapping[str, str], names: Iterable[str], path: Path | None,
              profile: Path | None = None, *, unprotect: object = None) -> dict[str, str]:
    """Put *names* into *environ* (the worker's own process), each from the eval profile if it has it, else from
    the keys file. Returns where each came from."""
    wanted = list(names)
    found: dict[str, str] = {}
    for name in wanted:
        value = read_profile_key(profile, name, unprotect=unprotect) if profile_has(profile, name) else None
        if value:
            environ[name], found[name] = value, "eval profile"
    rest = [name for name in wanted if name not in found]
    if rest:
        why = problem(rest, path)
        if why:
            raise KeysError(why)
        assert path is not None
        values = _read(path)
        for name in rest:
            environ[name], found[name] = values[name], "keys file"
    return found
