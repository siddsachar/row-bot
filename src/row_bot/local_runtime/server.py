"""The llama.cpp server for Run on this computer: started when a chat needs it, gone when idle.

**One server, its models by name.** ``llama-server`` runs in its router mode: one process on one
loopback port, the downloaded models by their catalog id from a presets file Row-Bot writes (the
model file and the window it serves at, sized to this computer at each start: ``models.context_for``),
loaded when a request names one, one at a time (``--models-max 1``). Row-Bot's endpoint for it is an
ordinary local llama.cpp endpoint (``custom_openai_llamacpp``, marked ``managed``), so the transport
asks :func:`endpoint_connection` for the address and key at each request and the server starts then
(``providers/custom.py``).

**Loopback, offline, a key of its own.** It listens only on 127.0.0.1, on a free port the system
picks, ``--offline``, with no web page (``--no-webui``), ``--cors-origins localhost``, and none of its
tools, agent or MCP. Its API key is made at each start and given in its environment only (never on
its command line or on disk), so other programs on this computer can't use it. Its environment holds
nothing of the person's (``install.server_environment``).

**Owned.** On Windows a kill-on-close job object holds it and the model processes it starts, so
Stop, Row-Bot quitting or Row-Bot crashing ends the whole tree; on macOS and Linux its own process
group, plus a record (``owned_processes``) so the next start, or the next Row-Bot, stops one a crash
left behind. A health check runs every few seconds; a server that stopped answering is ended and
starts again on the next request.

**Idle, then gone.** After :data:`IDLE_SECONDS` (30 minutes, the ``keep_alive`` Row-Bot sends Ollama) without
a request and with nothing being answered, the server stops, so the graphics card's memory goes back. The
prompt cache lives in the server, so a reply after a shorter pause would read the whole conversation again
(two and a half minutes for a 32K prompt on a 16 GB Mac); training doesn't wait for it: :func:`release_gpu`
stops it at once.

**Sharing the card with Learning.** :func:`release_gpu` is for Learning's training: it stops the server
now unless a request is being answered (the person's chat comes first; Learning's lease pauses
training then). While training holds the card (``learning.lease.training_holds``, read through
``sys.modules`` so nothing of Learning is imported), a start for background work is refused; a start
while a chat is running goes ahead.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from row_bot import local_runtime
from row_bot.local_runtime import hardware, install, models

logger = logging.getLogger(__name__)

# Row-Bot's own idle stop, not llama.cpp's ``--sleep-idle-seconds`` (b12359 ``tools/server/README.md``, "Sleeping
# on Idle"): sleeping unloads the model but keeps the process, its port and its key, while stopping gives back
# all of it, and Learning's ``release_gpu`` and the "running" state need the process gone.
IDLE_SECONDS = 1800.0
CHECK_EVERY_SECONDS = 5.0
# Loading the router takes a second; a model loads on its first request (the transport waits for it).
START_SECONDS = 60.0
MISSED_HEALTH = 3
STOP_SECONDS = 10.0
RUN_FOLDER = "run"
PRESETS_FILE = "models.ini"
STATE_FILE = "server.json"
LEDGER_FILE = "local-runtime-server.json"
# The prompt cache each loaded model keeps in the computer's memory (MiB), as Learning's server keeps it;
# llama.cpp's default is 8192 (``--cache-ram``, b12359 ``common/arg.cpp:1687``, ``common/common.h:657``).
PROMPT_CACHE_MIB = 2048
# Learning's lease, read only when Learning already loaded it.
LEASE_MODULE = "row_bot.learning.lease"

State = Literal["stopped", "starting", "running", "failed"]


class ServerError(RuntimeError):
    """``code``: ``not_installed``, ``start_failed``, ``card_busy`` (training holds the card and this is
    background work), ``closed``."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Connection:
    base_url: str  # http://127.0.0.1:<port>/v1
    api_key: str
    port: int


def free_port() -> int:
    """A free TCP port on 127.0.0.1, picked by the system (never one of Row-Bot's own)."""

    for _ in range(32):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        if not reserved_port(port):
            return port
    raise ServerError("start_failed")


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", int(port)))
            return True
        except OSError:
            return False


def _saved_port(folder: Path) -> int:
    """The port the last start used (kept while it is free, so the saved endpoint doesn't change)."""

    try:
        port = int(json.loads((folder / STATE_FILE).read_text(encoding="utf-8")).get("port") or 0)
    except (OSError, ValueError, AttributeError):
        return 0
    return port if 1024 < port < 65536 and not reserved_port(port) else 0


def reserved_port(port: int) -> bool:
    """Ports Row-Bot and its tools use themselves; the server never takes one."""

    return port in (8080, 8099) or 8121 <= port <= 8139 or 8151 <= port <= 8159


def presets_text(entries: Sequence[tuple[str, Path, int]]) -> str:
    """The presets file (keys are llama-server's own arguments, b12359 ``tools/server/README.md``, "Model
    presets"): shared settings, then one section per model: its catalog id, file and window.

    Shared: the model's own chat template; one request at a time; flash attention on (``-fa``,
    ``common/arg.cpp:1726``), which an 8-bit cache needs (``src/llama-context.cpp:4541``); the context cache in
    8-bit for every model (``-ctk``/``-ctv``, ``common/arg.cpp:2409,2422``), half of llama.cpp's 16-bit default
    (``models.py`` counts it so); the prompt cache in the computer's memory capped at :data:`PROMPT_CACHE_MIB`.

    Left to llama.cpp's defaults: ``--fit on`` with ``-ngl auto`` (``common/common.h:494,497``) already puts as
    many layers on the card as fit and the rest on the processor, which an explicit ``-ngl`` would stop
    (``common/fit.cpp:550``); prompt caching is on (``common/common.h:652``). ``--cache-reuse`` is not passed:
    it shifts the cache, which these models' multi-section positions (Qwen3.5's architecture) can't, so the
    server turns it off for them (``tools/server/server-context.cpp:1420``, ``src/llama-kv-cache.cpp:1245``)."""

    lines = ["version = 1", "", "[*]", "jinja = true", "parallel = 1", "flash-attn = on", "cache-type-k = q8_0",
             "cache-type-v = q8_0", f"cache-ram = {PROMPT_CACHE_MIB}", ""]
    for name, path, context in sorted(entries):
        text = str(Path(path).resolve())
        if any(ch in text for ch in "\r\n;#[]="):
            raise ServerError("start_failed")
        lines += [f"[{name}]", f"model = {text}", f"ctx-size = {int(context)}", ""]
    return "\n".join(lines)


def _training_holds() -> bool:
    lease = sys.modules.get(LEASE_MODULE)
    try:
        return bool(lease is not None and lease.training_holds())
    except Exception:  # noqa: BLE001
        return False


def _chat_running() -> bool:
    executions = sys.modules.get("row_bot.runtime.executions")
    try:
        return bool(executions is not None and executions.generation_registry.active())
    except Exception:  # noqa: BLE001
        return False


class _Process:
    """One owned ``llama-server`` process tree (job object on Windows, a process group elsewhere)."""

    def __init__(self, argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], log: Path) -> None:
        options: dict[str, Any] = {}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        cwd.mkdir(parents=True, exist_ok=True)
        self._log = open(log, "wb")  # noqa: SIM115 - closed in stop()
        try:
            self.process = subprocess.Popen(list(argv), cwd=str(cwd), env=dict(env), stdin=subprocess.DEVNULL,
                                            stdout=self._log, stderr=subprocess.STDOUT, shell=False, **options)
        except OSError as error:
            self._log.close()
            raise ServerError("start_failed") from error
        self.job: Any = None
        if os.name == "nt":
            try:
                from row_bot.plugins.worker_ownership import WindowsJob

                self.job = WindowsJob(self.process)
            except Exception:
                self.process.kill()
                self.process.wait(STOP_SECONDS)
                self._log.close()
                raise ServerError("start_failed") from None

    @property
    def pid(self) -> int:
        return int(self.process.pid)

    def alive(self) -> bool:
        return self.process.poll() is None

    def stop(self) -> bool:
        ended = True
        try:
            if self.job is not None:
                ended = bool(self.job.close())
            elif os.name != "nt":
                import signal

                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    ended = False
            elif self.process.poll() is None:
                self.process.kill()
            try:
                self.process.wait(STOP_SECONDS)
            except subprocess.TimeoutExpired:
                ended = False
        finally:
            with contextlib.suppress(OSError):
                self._log.close()
        return ended


class _Http:
    """The server's own API on loopback (a direct connection, never a proxy; one client, reused)."""

    def __init__(self) -> None:
        self._client: Any = None
        self._lock = threading.Lock()

    def request(self, method: str, url: str, *, key: str | None = None, timeout: float = 5.0) -> tuple[int, Any]:
        import httpx

        with self._lock:
            if self._client is None:
                self._client = httpx.Client(trust_env=False)
            client = self._client
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        response = client.request(method, url, headers=headers, timeout=timeout)
        try:
            payload = response.json() if response.content else None
        except ValueError:
            payload = None
        return response.status_code, payload


def _default_command(executable: Path) -> list[str]:
    return [str(executable)]


class LocalServer:
    """The one llama.cpp server for this data folder (see the module's docstring)."""

    def __init__(self, root: Path | None = None, *, command: Callable[[Path], list[str]] = _default_command,
                 http: Any = None, clock: Callable[[], float] = time.monotonic,
                 idle_seconds: float = IDLE_SECONDS, start_seconds: float = START_SECONDS,
                 monitor: bool = True, training_holds: Callable[[], bool] = _training_holds,
                 chat_running: Callable[[], bool] = _chat_running, environment: Mapping[str, str] | None = None) -> None:
        self.root = root
        self._command = command
        self._http = http or _Http()
        self._clock = clock
        self.idle_seconds = float(idle_seconds)
        self.start_seconds = float(start_seconds)
        self._monitor_enabled = monitor
        self._training_holds = training_holds
        self._chat_running = chat_running
        self._extra_environment = dict(environment or {})
        self._lock = threading.RLock()
        self._process: _Process | None = None
        self._connection: Connection | None = None
        self._state: State = "stopped"
        self.last_failure: str | None = None
        self._missed = 0
        self._last_active = 0.0
        self._monitor: threading.Thread | None = None
        self._wake = threading.Event()
        self._closed = False
        # A model's window changed (U1) while a reply was being answered: the server starts again once idle.
        self._reload_pending = False

    @property
    def state(self) -> State:
        return self._state

    @property
    def pid(self) -> int | None:
        process = self._process
        return process.pid if process is not None else None

    def _folder(self) -> Path:
        return install.runtime_dir(self.root) / RUN_FOLDER

    def ledger(self) -> Path:
        return install.data_dir(self.root) / "runtime" / LEDGER_FILE

    def _entries(self) -> list[tuple[str, Path, int]]:
        found = hardware.peek()
        return [(model.id, install.model_path(model, self.root),
                 models.context_for(model, found, local_runtime.chosen_context(model.id)))
                for model in install.installed_models(self.root)]

    # ── Running ─────────────────────────────────────────────────────────

    def connection(self, *, start: bool = True) -> Connection | None:
        """Where the server answers now; with ``start`` it starts when it isn't running (None when it
        can't run now: nothing installed, or the card is training's and this is background work)."""

        with self._lock:
            if self._reload_pending and self._process is not None and not self.busy():
                self.stop("settings")  # the next start serves the new window
            if self._connection is not None and self._process is not None and self._process.alive():
                self._last_active = self._clock()
                return self._connection
            if not start:
                return None
            try:
                return self._start()
            except ServerError as error:
                self.last_failure = error.code
                return None

    def _argv(self, executable: Path, port: int) -> list[str]:
        return [*self._command(executable), "--host", "127.0.0.1", "--port", str(int(port)), "--offline",
                "--no-webui", "--cors-origins", "localhost", "--models-preset", str(self._folder() / PRESETS_FILE),
                "--models-max", "1"]

    def ensure(self, model_id: str, *, while_training: bool = False) -> Connection:
        """The running server for ``model_id`` (started and healthy), or :class:`ServerError`:
        ``unknown_model`` (not downloaded here), ``card_busy`` (training holds the card, no chat runs and
        ``while_training`` is False), ``not_installed``, ``start_failed``. Calling it again while the
        server runs returns the same connection."""

        if model_id not in {model.id for model in install.installed_models(self.root)}:
            raise ServerError("unknown_model")
        with self._lock:
            found = self.connection(start=False)
            if found is not None:
                return found
            try:
                return self._start(while_training=while_training)
            except ServerError as error:
                self.last_failure = error.code
                raise

    def _start(self, *, while_training: bool = False) -> Connection:
        if self._closed:
            raise ServerError("closed")
        if not while_training and self._training_holds() and not self._chat_running():
            # Training has the card and nobody is chatting: background work waits (the person never does).
            raise ServerError("card_busy")
        build = install.installed_build(self.root)
        entries = self._entries()
        if build is None or not entries:
            raise ServerError("not_installed")
        self.cleanup_orphans()
        self._stop_process()
        folder = self._folder()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / PRESETS_FILE).write_text(presets_text(entries), encoding="utf-8", newline="\n")
        port = _saved_port(folder)
        if not port or not port_free(port):
            port = free_port()
        key = secrets.token_urlsafe(32)
        environment = {**install.server_environment(build.folder), **self._extra_environment,
                       "LLAMA_API_KEY": key}
        self._state = "starting"
        try:
            process = _Process(self._argv(build.executable, port), cwd=build.folder / "home", env=environment,
                               log=folder / "server.log")
        except ServerError:
            self._state = "failed"
            raise
        self._process = process
        self._record(process.pid)
        deadline = self._clock() + self.start_seconds
        while True:
            if not process.alive():
                self._fail("start_failed")
                raise ServerError("start_failed")
            try:
                status, _ = self._http.request("GET", f"http://127.0.0.1:{port}/health", timeout=2.0)
                if status == 200:
                    break
            except Exception:  # noqa: BLE001 - not listening yet
                pass
            if self._clock() > deadline:
                self._fail("start_failed")
                raise ServerError("start_failed")
            time.sleep(0.05)
        self._record(process.pid)  # again: on macOS the name settles once it runs
        self._connection = Connection(f"http://127.0.0.1:{port}/v1", key, port)
        (folder / STATE_FILE).write_text(json.dumps({"port": port}), encoding="utf-8")
        self._state = "running"
        self.last_failure = None
        self._missed = 0
        self._last_active = self._clock()
        try:
            save_endpoint(self.root, port=port)
        except Exception:  # noqa: BLE001
            logger.warning("Row-Bot couldn't save the local model server's address", exc_info=True)
        self._start_monitor()
        logger.info("Row-Bot started the local model server on 127.0.0.1:%d", port)
        return self._connection

    def _record(self, pid: int) -> None:
        try:
            from row_bot import owned_processes

            owned_processes.record(self.ledger(), pid, server="local_runtime")
        except Exception:  # noqa: BLE001
            logger.debug("The local model server wasn't recorded", exc_info=True)

    def _forget(self, pid: int | None) -> None:
        if pid is None:
            return
        try:
            from row_bot import owned_processes

            owned_processes.forget(self.ledger(), pid)
        except Exception:  # noqa: BLE001
            logger.debug("The local model server's record wasn't dropped", exc_info=True)

    def cleanup_orphans(self) -> int:
        """Stop a server (and its model processes) a Row-Bot that has since ended left running."""

        path = self.ledger()
        if not path.is_file():
            return 0
        try:
            from row_bot import owned_processes

            return owned_processes.cleanup(path, tree=True)
        except Exception:  # noqa: BLE001
            logger.debug("The check for a local model server left running failed", exc_info=True)
            return 0

    def _stop_process(self) -> bool:
        process = self._process
        self._process = None
        self._connection = None
        self._reload_pending = False
        if process is None:
            return True
        ended = process.stop()
        self._forget(process.pid)
        return ended

    def _fail(self, reason: str) -> None:
        self._stop_process()
        self._state, self.last_failure = "failed", reason
        logger.warning("The local model server stopped (%s)", reason)

    def stop(self, reason: str = "you") -> bool:
        """Stop the server and every model it loaded now (the next request starts it again)."""

        with self._lock:
            had = self._process is not None
            ended = self._stop_process()
            if self._state != "failed" or had:
                self._state = "stopped"
            if had:
                logger.info("Row-Bot stopped the local model server (%s)", reason)
            return ended

    def apply_settings(self) -> str:
        """A model's window changed (U1): its presets are rewritten at the next start, so a running server
        stops now when nothing is being answered, else as soon as it is idle (the next request starts it with
        the new window). ``"next_start"`` (not running), ``"restarted"`` or ``"when_idle"``."""

        with self._lock:
            if self._process is None:
                return "next_start"
            if self.busy():
                self._reload_pending = True
                return "when_idle"
            self.stop("settings")
            return "restarted"

    def busy(self) -> bool:
        """Whether a request is being answered now (its slots say so)."""

        connection = self._connection
        if connection is None:
            return False
        base = f"http://127.0.0.1:{connection.port}"
        try:
            status, listed = self._http.request("GET", f"{base}/models", key=connection.api_key)
        except Exception:  # noqa: BLE001
            return False
        names = [str(item.get("id") or "") for item in (listed or {}).get("data") or ()
                 if isinstance(item, Mapping) and _status(item) in ("loaded", "loading")] if status == 200 else []
        for name in names:
            if _status_of(name, listed) == "loading":
                return True
            try:
                code, slots = self._http.request("GET", f"{base}/slots?model={name}", key=connection.api_key)
            except Exception:  # noqa: BLE001
                continue
            if code == 200 and any(isinstance(slot, Mapping) and slot.get("is_processing") for slot in slots or ()):
                return True
        return False

    def release_gpu(self) -> bool:
        """Give the graphics card back now unless a request is being answered. True when it stopped."""

        with self._lock:
            if self._process is None:
                return True
            if self.busy():
                return False
            return self.stop("training")

    # ── Watching it ─────────────────────────────────────────────────────

    def check(self) -> None:
        """One health and idle pass (the monitor runs it every few seconds; tests call it)."""

        with self._lock:
            process, connection = self._process, self._connection
            if process is None or connection is None:
                return
            if not process.alive():
                self._fail("crashed")
                return
            try:
                status, _ = self._http.request("GET", f"http://127.0.0.1:{connection.port}/health", timeout=3.0)
                healthy = status == 200
            except Exception:  # noqa: BLE001
                healthy = False
            self._missed = 0 if healthy else self._missed + 1
            if self._missed >= MISSED_HEALTH:
                self._fail("not_responding")
                return
            now = self._clock()
            if healthy and self.busy():
                self._last_active = now
            elif self._reload_pending:
                self.stop("settings")
            elif now - self._last_active >= self.idle_seconds:
                self.stop("idle")

    def _start_monitor(self) -> None:
        if not self._monitor_enabled or (self._monitor is not None and self._monitor.is_alive()):
            return
        self._wake.clear()
        self._monitor = threading.Thread(target=self._watch, name="row-bot-local-model-server", daemon=True)
        self._monitor.start()

    def _watch(self) -> None:
        while not self._closed:
            if self._wake.wait(CHECK_EVERY_SECONDS):
                return
            if self._process is None:
                return
            try:
                self.check()
            except Exception:  # noqa: BLE001
                logger.debug("The local model server check failed", exc_info=True)

    def shutdown(self) -> None:
        self._closed = True
        self._wake.set()
        self.stop("app_closed")


def _status(item: Mapping[str, Any]) -> str:
    value = item.get("status")
    value = value.get("value") if isinstance(value, Mapping) else value
    return str(value or "")


def _status_of(name: str, listed: Any) -> str:
    for item in (listed or {}).get("data") or ():
        if isinstance(item, Mapping) and item.get("id") == name:
            return _status(item)
    return ""


# ── The endpoint ────────────────────────────────────────────────────────────


def save_endpoint(root: Path | None = None, *, port: int | None = None) -> None:
    """The runtime's endpoint: an ordinary local llama.cpp endpoint marked managed, listing the
    installed models, each with the window its presets serve it at (``models.context_for``; no
    endpoint-wide window, which would win over each model's). Removed when no model is installed."""

    from row_bot import local_runtime
    from row_bot.providers import custom

    installed = install.installed_models(root)
    current = custom.get_custom_endpoint(local_runtime.ENDPOINT_ID)
    if not installed:
        if current is not None:
            custom.remove_managed_endpoint(local_runtime.ENDPOINT_ID)
        return
    # Its address is asked for at each request; the saved one only says it is on this computer.
    base_url = f"http://127.0.0.1:{int(port)}/v1" if port else str((current or {}).get("base_url") or
                                                                     "http://127.0.0.1/v1")
    found = hardware.peek()
    served = sorted((model.id, model.label, models.context_for(model, found, local_runtime.chosen_context(model.id)))
                    for model in installed)
    wanted = {
        "id": local_runtime.ENDPOINT_ID, "name": local_runtime.DISPLAY_NAME, "base_url": base_url,
        "profile": "llama_cpp", "execution_location": "local", "auth_required": False,
        "managed": local_runtime.OWNER, "manual_capabilities": {},
    }
    listed = sorted((str(item.get("model_id") or item.get("id") or ""), int(item.get("context_window") or 0))
                    for item in (current or {}).get("models") or () if isinstance(item, dict))
    if current is not None and all(current.get(key) == value for key, value in wanted.items()) and \
            listed == [(model_id, context) for model_id, _, context in served]:
        return
    custom.save_custom_endpoint(wanted, manage_secret=False)
    custom.store_managed_endpoint_models(local_runtime.ENDPOINT_ID, served)


# ── The app's server ────────────────────────────────────────────────────────

_lock = threading.Lock()
_servers: dict[str, LocalServer] = {}


def get(root: Path | None = None) -> LocalServer:
    """The server for this data folder (made on first use; nothing starts until a request)."""

    folder = str(install.data_dir(root).resolve())
    with _lock:
        found = _servers.get(folder)
        if found is None:
            found = _servers[folder] = LocalServer(root)
        return found


def loaded() -> list[LocalServer]:
    with _lock:
        return list(_servers.values())


def endpoint_connection(*, start: bool = True) -> tuple[str, str] | None:
    """For ``providers/custom.py``: the running server's address and key, started on demand."""

    found = get().connection(start=start)
    return (found.base_url, found.api_key) if found is not None else None


def ensure_connection(model_ref: str, *, while_training: bool = False) -> Connection:
    """For a caller outside chat that needs the server running now, such as Learning's evaluation worker
    (another process, which can't start it itself): starts the server if needed, waits until it answers
    its health check, and returns where it answers and its key (valid until it stops: after
    :data:`IDLE_SECONDS` with no request, or when :func:`release_gpu` or Stop ends it). Idempotent.

    ``model_ref`` is ``model:custom_openai_llamacpp:<id>`` of a downloaded model. This is background
    work, so while Learning's training holds the card it raises ``ServerError("card_busy")`` unless
    ``while_training`` says the caller has the card itself (Learning's own evaluation, run inside its
    lease). Raises :class:`ServerError` (``unknown_model``, ``card_busy``, ``not_installed``,
    ``start_failed``)."""

    from row_bot import local_runtime
    from row_bot.providers.selection import parse_model_ref

    parsed = parse_model_ref(model_ref)
    if parsed is None or not local_runtime.is_local_runtime_provider(parsed[0]):
        raise ServerError("unknown_model")
    return get().ensure(parsed[1], while_training=while_training)


def ensure_started(model_ref: str, *, while_training: bool = False) -> str:
    """:func:`ensure_connection`, returning only the loopback ``base_url`` (``http://127.0.0.1:<port>/v1``).
    Requests to it need the key :func:`ensure_connection` gives (``Authorization: Bearer <key>``)."""

    return ensure_connection(model_ref, while_training=while_training).base_url


def state() -> str:
    return get().state


def failure() -> str | None:
    return get().last_failure


def stop(reason: str = "you") -> bool:
    return all([server.stop(reason) for server in loaded()])


def apply_settings(root: Path | None = None) -> str:
    """A model's window changed in its settings (U1): the endpoint lists the new window now and the server
    serves it from its next start (:meth:`LocalServer.apply_settings`; ``"next_start"`` when none runs)."""

    save_endpoint(root)
    applied = [local.apply_settings() for local in loaded()]
    return next((state for state in applied if state != "next_start"), "next_start")


def release_gpu() -> bool:
    """For Learning's training: give the graphics card back now unless the person's chat is being
    answered. True when no local model server holds it any more."""

    return all([server.release_gpu() for server in loaded()])


def shutdown(timeout: float = 10.0) -> None:
    for server in loaded():
        try:
            server.shutdown()
        except Exception:  # noqa: BLE001
            logger.debug("The local model server didn't stop", exc_info=True)


__all__ = [
    "Connection",
    "IDLE_SECONDS",
    "apply_settings",
    "LocalServer",
    "ServerError",
    "endpoint_connection",
    "ensure_connection",
    "ensure_started",
    "failure",
    "free_port",
    "get",
    "loaded",
    "presets_text",
    "release_gpu",
    "save_endpoint",
    "shutdown",
    "state",
    "stop",
]
