"""A throwaway Postgres cluster for the tests marked ``postgres`` (D14): no Docker, no shared server.

Where Postgres comes from, and nothing else is guessed:

- ``ROW_BOT_TEST_PG_BIN``: a folder holding ``initdb``, ``postgres`` and ``pg_ctl`` (Linux, macOS, CI).
- ``ROW_BOT_TEST_PG_WSL_DISTRO``: on Windows, a WSL distro with Postgres 17 at ``/usr/lib/postgresql/17/bin``.
  A cold WSL gets a few minutes to start before the programs are looked for.

With neither, or without the programs, or without psycopg (``uv sync ... --group org``), every test
using the cluster skips and says why; with ``ROW_BOT_TEST_PG_REQUIRED=1`` (CI's Org job) it fails instead.

The cluster lives for the session: ``initdb`` into a new temporary folder (WSL's own for the WSL
case), password sign-in (SCRAM) only, a random superuser password, no Unix socket, and TCP on a free
loopback port, ``localhost`` only. Postgres runs in the foreground as a child of the test process (so
a WSL distro stays up while it runs). It is stopped and its folder deleted at session end, also when a
test fails or the run is interrupted (and at interpreter exit, if the session never got that far).
"""

from __future__ import annotations

import atexit
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import NoReturn
from urllib.parse import quote

import pytest

PG_BIN_ENV = "ROW_BOT_TEST_PG_BIN"
PG_WSL_DISTRO_ENV = "ROW_BOT_TEST_PG_WSL_DISTRO"
PG_REQUIRED_ENV = "ROW_BOT_TEST_PG_REQUIRED"
WSL_PG_BIN = "/usr/lib/postgresql/17/bin"
SUPERUSER = "row_bot_test"
PROGRAMS = ("initdb", "postgres", "pg_ctl")
_START_TIMEOUT = 120.0  # a WSL distro's first start can take a minute
_COMMAND_TIMEOUT = 120.0
# A cold WSL (just restarted, or its VM idle-stopped) can fail or hang its first commands: the distro is
# asked to run `true` until it answers, for up to this long in all, each try at most _WSL_TRY_TIMEOUT.
_WSL_START_TIMEOUT = 240.0
_WSL_TRY_TIMEOUT = 60.0
_WSL_RETRY_PAUSE = 2.0


class PostgresUnavailable(Exception):
    """No Postgres to start; the message is the reason the tests skip."""


@dataclass(frozen=True)
class PostgresPrograms:
    """Postgres's programs, run natively from ``bin_dir`` or inside the WSL distro ``distro``."""

    bin_dir: str
    distro: str | None = None

    def argv(self, program: str, *args: str) -> list[str]:
        if self.distro:
            return ["wsl.exe", "-d", self.distro, "--exec", f"{self.bin_dir}/{program}", *args]
        return [str(Path(self.bin_dir) / program), *args]

    def _wsl(self, *argv: str) -> list[str]:
        return ["wsl.exe", "-d", self.distro or "", "--exec", *argv]

    def run(self, argv: list[str], *, stdin: str | None = None, timeout: float = _COMMAND_TIMEOUT) -> str:
        result = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"{Path(argv[0]).name} {' '.join(argv[1:3])} failed ({result.returncode}): "
                               f"{(result.stdout + result.stderr).strip()[-2000:]}")
        return result.stdout

    def join(self, folder: str, name: str) -> str:
        return f"{folder}/{name}" if self.distro else str(Path(folder) / name)

    def make_folder(self) -> str:
        if self.distro:
            return self.run(self._wsl("mktemp", "-d", "-t", "row-bot-test-pg-XXXXXX")).strip()
        return tempfile.mkdtemp(prefix="row-bot-test-pg-")

    def write_private_file(self, path: str, text: str) -> None:
        if self.distro:
            self.run(self._wsl("sh", "-c", 'umask 077 && cat > "$1"', "sh", path), stdin=text)
            return
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def remove(self, path: str) -> None:
        if self.distro:
            self.run(self._wsl("rm", "-rf", "--", path))
        else:
            shutil.rmtree(path, ignore_errors=True)


def _wait_for_wsl(programs: PostgresPrograms) -> None:
    """Return once the WSL distro runs a command; ``PostgresUnavailable`` if it doesn't in time."""
    deadline = time.monotonic() + _WSL_START_TIMEOUT
    while True:
        remaining = deadline - time.monotonic()
        try:
            programs.run(programs._wsl("true"), timeout=max(1.0, min(_WSL_TRY_TIMEOUT, remaining)))
            return
        except OSError:
            raise PostgresUnavailable("wsl.exe can't be run") from None
        except (RuntimeError, subprocess.TimeoutExpired):
            if time.monotonic() + _WSL_RETRY_PAUSE >= deadline:
                raise PostgresUnavailable(
                    f"WSL distro {programs.distro!r} didn't start within {_WSL_START_TIMEOUT:g} s") from None
        time.sleep(_WSL_RETRY_PAUSE)


def find_postgres(environ: dict[str, str] | os._Environ[str] | None = None) -> PostgresPrograms:
    """The configured Postgres programs; ``PostgresUnavailable`` (with the reason) when there are none."""
    env = os.environ if environ is None else environ
    bin_dir = (env.get(PG_BIN_ENV) or "").strip()
    distro = (env.get(PG_WSL_DISTRO_ENV) or "").strip()
    if bin_dir:
        missing = [name for name in PROGRAMS if shutil.which(name, path=bin_dir) is None]
        if missing:
            raise PostgresUnavailable(f"{PG_BIN_ENV} has no {', '.join(missing)}")
        return PostgresPrograms(bin_dir)
    if distro:
        if sys.platform != "win32":
            raise PostgresUnavailable(f"{PG_WSL_DISTRO_ENV} is for Windows; set {PG_BIN_ENV} instead")
        programs = PostgresPrograms(WSL_PG_BIN, distro)
        _wait_for_wsl(programs)
        checks = [arg for name in PROGRAMS for arg in ("-a", "-x", f"{WSL_PG_BIN}/{name}")][1:]
        try:
            programs.run(programs._wsl("test", *checks))
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            raise PostgresUnavailable(f"WSL distro {distro!r} has no Postgres in {WSL_PG_BIN}") from None
        return programs
    raise PostgresUnavailable(
        f"no Postgres for the postgres tests: set {PG_BIN_ENV} (a folder with initdb, postgres and pg_ctl) "
        f"or, on Windows, {PG_WSL_DISTRO_ENV} (a WSL distro with Postgres 17)"
    )


def _free_loopback_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class PostgresCluster:
    """One running throwaway cluster. ``url`` builds connection addresses for it."""

    def __init__(self, programs: PostgresPrograms) -> None:
        self.programs = programs
        self.superuser_password = secrets.token_urlsafe(24)
        self.port = 0
        self._folder: str | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._log = tempfile.TemporaryFile()

    @property
    def _data(self) -> str:
        assert self._folder is not None
        return self.programs.join(self._folder, "data")

    def url(self, dbname: str = "postgres", user: str = SUPERUSER, password: str | None = None) -> str:
        secret = self.superuser_password if password is None else password
        # SCRAM or nothing: libpq won't fall back to a weaker method the server offers.
        return (f"postgresql://{quote(user, safe='')}:{quote(secret, safe='')}@127.0.0.1:{self.port}/{quote(dbname)}"
                "?require_auth=scram-sha-256")

    def start(self) -> None:
        self._folder = self.programs.make_folder()
        password_file = self.programs.join(self._folder, "superuser-password")
        self.programs.write_private_file(password_file, self.superuser_password + "\n")
        try:
            self.programs.run(self.programs.argv(
                "initdb", "-D", self._data, "-U", SUPERUSER, "-A", "scram-sha-256", f"--pwfile={password_file}",
                "-E", "UTF8", "--locale=C", "--no-sync", "--no-instructions",
            ))
        finally:
            self.programs.remove(password_file)
        self.port = _free_loopback_port()
        self._process = subprocess.Popen(
            self.programs.argv(
                "postgres", "-D", self._data,
                "-c", "listen_addresses=localhost", "-c", f"port={self.port}", "-c", "unix_socket_directories=",
                # A throwaway cluster: nothing needs to survive a crash.
                "-c", "fsync=off", "-c", "synchronous_commit=off", "-c", "full_page_writes=off",
            ),
            stdin=subprocess.DEVNULL, stdout=self._log, stderr=subprocess.STDOUT,
        )
        self._wait_until_accepting()

    def _log_tail(self) -> str:
        self._log.seek(0)
        return self._log.read().decode("utf-8", "replace")[-2000:]

    def _wait_until_accepting(self) -> None:
        import psycopg

        deadline = time.monotonic() + _START_TIMEOUT
        while True:
            assert self._process is not None
            if self._process.poll() is not None:
                raise RuntimeError(f"postgres exited ({self._process.returncode}): {self._log_tail()}")
            try:
                with psycopg.connect(self.url(), connect_timeout=2):
                    return
            except psycopg.OperationalError:
                if time.monotonic() > deadline:
                    raise RuntimeError(f"postgres didn't accept connections: {self._log_tail()}") from None
                time.sleep(0.1)

    def stop(self) -> None:
        """Stop the cluster and delete its folder; safe to call more than once."""
        process, self._process = self._process, None
        if process is not None:
            if process.poll() is None:
                try:
                    self.programs.run(self.programs.argv("pg_ctl", "-D", self._data, "-m", "immediate", "-w", "stop"))
                except (OSError, RuntimeError, subprocess.TimeoutExpired):
                    pass
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=30)
        folder, self._folder = self._folder, None
        if folder is not None:
            self.programs.remove(folder)
        self._log.close()


def _unavailable(reason: str) -> NoReturn:
    if os.environ.get(PG_REQUIRED_ENV) == "1":
        pytest.fail(f"{PG_REQUIRED_ENV}=1, but {reason}", pytrace=False)
    pytest.skip(reason, allow_module_level=True)


def import_psycopg() -> ModuleType:
    """psycopg, for a test module's top: without it (no ``org`` group), the module skips."""
    try:
        import psycopg
    except ImportError:
        _unavailable("psycopg is not installed: uv sync --locked --all-extras --group test --group org")
    return psycopg


@pytest.fixture(scope="session")
def postgres_cluster() -> Iterator[PostgresCluster]:
    """The session's throwaway cluster; skips the test when there is no Postgres or no psycopg."""
    import_psycopg()
    try:
        programs = find_postgres()
    except PostgresUnavailable as reason:
        _unavailable(str(reason))
    cluster = PostgresCluster(programs)
    atexit.register(cluster.stop)
    try:
        cluster.start()
        yield cluster
    finally:
        cluster.stop()
        atexit.unregister(cluster.stop)


__all__ = ["PG_BIN_ENV", "PG_REQUIRED_ENV", "PG_WSL_DISTRO_ENV", "SUPERUSER", "PostgresCluster",
           "PostgresPrograms", "PostgresUnavailable", "find_postgres", "import_psycopg", "postgres_cluster"]
