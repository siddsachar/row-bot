"""Row-Bot's own llama.cpp server for personal models (O75).

Personal models run here by default ("Runs in": Row-Bot). L2v found llama.cpp served the same GGUF
better than Ollama, with no tool-call errors: its grammar keeps a tool call well formed once the
model starts one. Ollama, LM Studio, a llama.cpp server the person starts and vLLM stay choices.

**One server, many models.** ``llama-server`` (``server_files.py``) runs in its router mode: one
process on one loopback port, a model per name from a presets file Row-Bot writes (the version's
GGUF, the context it serves at, reasoning off, O24), loaded when a request names it and unloaded when
it isn't used. So Row-Bot's endpoint for it is an ordinary local OpenAI-compatible endpoint (the
``llama_cpp`` profile) whose models are the personal-model versions, and a comparison's arms can be
loaded side by side where the card has room (``--models-max``).

**Started on demand, never left running.** Nothing starts it but a request for one of its models (a
routed turn, the model picker, a comparison, the ready check): the endpoint is marked ``managed``, so
the transport asks :func:`endpoint_connection` for the address and key at each request and the
server starts then (``providers/custom.py``). It listens only on 127.0.0.1, on a free port, with an
API key Row-Bot makes at each start (in its environment, never on its command line or on disk, so
other local programs can't use it; its model processes inherit it), offline (``--offline``), with
no web page (``--no-webui``) and none of its own tools, agent or MCP (all off by default and never
passed). Its environment carries nothing of the person's (no keys, no ``LLAMA_*`` settings).

**Owned like plugin workers (E15).** On Windows a kill-on-close job object holds it and the model
processes it starts, so Stop, Row-Bot quitting or Row-Bot crashing ends the whole tree; on macOS and
Linux its own process group, plus a record of it (``owned_processes``) so a later Row-Bot stops one a
crash left behind. A health check runs every few seconds: a server that stopped answering is ended
and starts again on the next request.

**Idle, then gone (O53's spirit).** A model that took no request for :data:`IDLE_SECONDS` is unloaded
(its graphics memory goes back), and once no model is loaded for :data:`SERVER_IDLE_SECONDS` the
server itself stops. Training wanting the card stops it at once when nothing holds it
(:meth:`ManagedServer.release_card`, the L2a lease: training starts only when no chat is running).

Nothing here phones home (E14): the server is given only local files and ``--offline``; its own
network use (Hugging Face downloads, the web page's MCP proxy, its tools) is never switched on.
``docs/LEARNING_ENGINE_TELEMETRY.md`` records it.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

RUNTIME = "row_bot"
NAME = "Row-Bot"
ENDPOINT_ID = "row-bot-personal-row-bot"
RUN_FOLDER = "run"
MODELS_FILE = "models.json"
PRESETS_FILE = "models.ini"
STATE_FILE = "server.json"
LEDGER_FILE = "learning-model-server.json"
OWNER = "learning"
# A model nobody asked anything for this long is unloaded (Ollama's own keep-alive is 5 minutes).
IDLE_SECONDS = 300
# With no model loaded for this long, the server itself stops.
SERVER_IDLE_SECONDS = 60.0
CHECK_EVERY_SECONDS = 5.0
START_SECONDS = 60.0
LOAD_SECONDS = 600.0
# A server that doesn't answer its health check this many times in a row is ended.
MISSED_HEALTH = 3
PROMPT_CACHE_MIB = 2048  # per loaded model, in the computer's memory
STOP_SECONDS = 10.0
# Graphics memory a loaded model needs besides its file (its context, compute buffers, CUDA itself).
MODEL_OVERHEAD_BYTES = 1_200_000_000
MODEL_FILE_FACTOR = 1.1

State = Literal["stopped", "starting", "running", "failed"]


class ServerError(RuntimeError):
    """The server can't serve now; ``code`` is stable: ``unavailable`` (no build for this computer),
    ``not_installed``, ``start_failed``, ``not_responding``, ``crashed``, ``unknown_model``,
    ``load_failed``."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Connection:
    """Where the running server answers now, and its key (valid until it stops)."""

    base_url: str  # http://127.0.0.1:<port>/v1
    api_key: str
    port: int


@dataclass(frozen=True)
class ModelEntry:
    """One model the server can load by name."""

    name: str
    gguf: str
    context: int

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "gguf": self.gguf, "context": int(self.context)}


# ── Where it keeps things ───────────────────────────────────────────────────


def run_dir(root: Path | None = None) -> Path:
    from row_bot.learning.training import server_files

    return server_files.server_dir(root) / RUN_FOLDER


def ledger_path(root: Path | None = None) -> Path:
    """The record of a running server (pid and creation time) a later Row-Bot reads after a crash."""

    if root is None:
        from row_bot import owned_processes

        return owned_processes.ledger_path(LEDGER_FILE)
    return Path(root) / "runtime" / LEDGER_FILE


def _write_json(path: Path, value: Any) -> None:
    from row_bot.learning import atomic

    atomic.write_text(path, json.dumps(value, indent=1, sort_keys=True))


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _ini_value(value: str) -> str:
    # One line, nothing that would end the value or start a comment in the presets file.
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if not text or any(ch in text for ch in ";#[]="):
        raise ServerError("unknown_model")
    return text


def presets_text(models: Sequence[ModelEntry]) -> str:
    """The presets file: settings every model shares, then one section per model (its name, file and
    context). Reasoning is off (personal models are non-thinking, O24); Jinja templates parse the
    model's own tool calls; one request at a time per model; the prompt cache each model keeps in the
    computer's memory is capped at ``PROMPT_CACHE_MIB`` (llama.cpp's default is 8 GB per model)."""

    lines = ["version = 1", "", "[*]", "jinja = true", "reasoning = off", "parallel = 1",
             f"cache-ram = {PROMPT_CACHE_MIB}", ""]
    for entry in sorted(models, key=lambda item: item.name):
        lines += [f"[{_ini_value(entry.name)}]", f"model = {_ini_value(entry.gguf)}",
                  f"ctx-size = {int(entry.context)}", ""]
    return "\n".join(lines)


def free_port() -> int:
    """A free TCP port on 127.0.0.1 (the system picks it)."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", int(port)))
            return True
        except OSError:
            return False


def models_max(model_bytes: Sequence[int], card_bytes: int | None) -> int:
    """How many models may be loaded side by side: two when the card holds two of the largest with
    room to spare (a comparison's arms then stay loaded), else one at a time."""

    largest = max((int(size) for size in model_bytes), default=0)
    if not card_bytes or not largest:
        return 1
    need = largest * MODEL_FILE_FACTOR + MODEL_OVERHEAD_BYTES
    usable = 0.9 * card_bytes - MODEL_OVERHEAD_BYTES
    return 2 if 2 * need <= usable else 1


def _file_bytes(path: str) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def _card_bytes() -> int | None:
    from row_bot.learning.training import hardware

    found = hardware.peek()
    gpu = getattr(found, "gpu", None)
    return int(gpu.memory_mib) * 1024 * 1024 if gpu is not None else None


# ── The process ─────────────────────────────────────────────────────────────


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
        """End the server and every model process it started. True when all of them ended."""

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
    """The server's own API on loopback (replaced by a fake in tests)."""

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def request(self, method: str, url: str, *, key: str | None = None, body: Mapping[str, Any] | None = None,
                timeout: float | None = None) -> tuple[int, Any]:
        import httpx

        headers = {"Authorization": f"Bearer {key}"} if key else {}
        with httpx.Client(trust_env=False, timeout=timeout or self.timeout) as client:
            response = client.request(method, url, headers=headers, json=dict(body) if body is not None else None)
        try:
            payload = response.json() if response.content else None
        except ValueError:
            payload = None
        return response.status_code, payload


# ── The manager ─────────────────────────────────────────────────────────────


class ManagedServer:
    """Row-Bot's one llama.cpp server for this data folder (see the module's docstring)."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        files: Callable[[], Any] | None = None,
        command: Callable[[Path], list[str]] | None = None,
        environment: Callable[[Path, Path], dict[str, str]] | None = None,
        http: Any = None,
        clock: Callable[[], float] = time.monotonic,
        card_bytes: Callable[[], int | None] = _card_bytes,
        endpoint: Callable[[Connection, int], None] | None = None,
        idle_seconds: int = IDLE_SECONDS,
        server_idle_seconds: float = SERVER_IDLE_SECONDS,
        start_seconds: float = START_SECONDS,
        monitor: bool = True,
    ) -> None:
        self._root = root
        self._files = files
        self._command = command
        self._environment = environment
        self._http = http or _Http()
        self._clock = clock
        self._card_bytes = card_bytes
        self._endpoint = endpoint
        self.idle_seconds = int(idle_seconds)
        self.server_idle_seconds = float(server_idle_seconds)
        self.start_seconds = float(start_seconds)
        self._monitor_enabled = monitor
        self._lock = threading.RLock()
        self._process: _Process | None = None
        self._connection: Connection | None = None
        self._state: State = "stopped"
        self.last_failure: str | None = None
        self._holds = 0
        self._missed = 0
        self._last_active = 0.0
        self._monitor: threading.Thread | None = None
        self._wake = threading.Event()
        self._closed = False

    # ── Facts ───────────────────────────────────────────────────────────

    @property
    def state(self) -> State:
        return self._state

    @property
    def pid(self) -> int | None:
        process = self._process
        return process.pid if process is not None else None

    def files(self) -> Any:
        """The installed server files (``server_files.status`` for this computer's build)."""

        if self._files is not None:
            return self._files()
        from row_bot.learning.training import hardware, server_files

        found = hardware.peek()
        variant = found.variant if found is not None and found.can_train else None
        return server_files.status(self._root, variant=variant)

    def available(self) -> bool:
        """Whether the server can run here now (its files are installed)."""

        try:
            return bool(self.files().installed)
        except Exception:  # noqa: BLE001
            return False

    # ── Models it can load ──────────────────────────────────────────────

    def models(self) -> list[ModelEntry]:
        data = _read_json(run_dir(self._root) / MODELS_FILE)
        found = []
        for item in (data or {}).get("models") or () if isinstance(data, dict) else ():
            try:
                found.append(ModelEntry(str(item["name"]), str(item["gguf"]), int(item["context"])))
            except (KeyError, TypeError, ValueError):
                continue
        return found

    def _save_models(self, models: Sequence[ModelEntry]) -> None:
        folder = run_dir(self._root)
        folder.mkdir(parents=True, exist_ok=True)
        _write_json(folder / MODELS_FILE, {"models": [entry.to_dict() for entry in models]})
        (folder / PRESETS_FILE).write_text(presets_text(models), encoding="utf-8", newline="\n")

    def register(self, name: str, gguf: Path, context: int) -> None:
        """Make ``gguf`` loadable as ``name`` at ``context`` (a running server reads the list again)."""

        entry = ModelEntry(_ini_value(name), _ini_value(str(Path(gguf).resolve())), int(context))
        with self._lock:
            models = [item for item in self.models() if item.name != entry.name] + [entry]
            self._save_models(models)
            self._reload()
            if self._connection is not None and self._endpoint is not None:
                self._endpoint(self._connection, self._context())

    def unregister(self, name: str) -> None:
        with self._lock:
            models = self.models()
            if not any(item.name == name for item in models):
                return
            if self._connection is not None:
                self.unload(name)
            self._save_models([item for item in models if item.name != name])
            self._reload()

    def _reload(self) -> None:
        connection = self._connection
        if connection is None:
            return
        try:
            self._http.request("GET", f"http://127.0.0.1:{connection.port}/models?reload=1", key=connection.api_key)
        except Exception:  # noqa: BLE001 - it reads the file again when it next starts
            logger.debug("Learning's model server didn't reload its list", exc_info=True)

    # ── Running ─────────────────────────────────────────────────────────

    def connection(self, *, start: bool = True) -> Connection | None:
        """Where the server answers now; with ``start`` it is started when it isn't running (None
        when it can't run here)."""

        with self._lock:
            if self._connection is not None and self._process is not None and self._process.alive():
                self._last_active = self._clock()
                return self._connection
            if not start:
                return None
            try:
                return self._start()
            except ServerError:
                return None

    def ensure(self) -> Connection:
        """The running server (started if needed). Raises :class:`ServerError`."""

        with self._lock:
            found = self.connection(start=False)
            return found if found is not None else self._start()

    def _argv(self, executable: Path, port: int) -> list[str]:
        folder = run_dir(self._root)
        prefix = self._command(executable) if self._command is not None else [str(executable)]
        return [*prefix, "--host", "127.0.0.1", "--port", str(int(port)), "--offline", "--no-webui",
                "--models-preset", str(folder / PRESETS_FILE),
                "--models-max", str(models_max([_file_bytes(entry.gguf) for entry in self.models()],
                                               self._card_bytes())),
                "--sleep-idle-seconds", str(self.idle_seconds)]

    def _env(self, folder: Path, home: Path) -> dict[str, str]:
        if self._environment is not None:
            return self._environment(folder, home)
        from row_bot.learning.training import server_files

        return server_files.server_environment(folder, home)

    def _start(self) -> Connection:
        if self._closed:
            raise ServerError("start_failed")
        found = self.files()
        if not found.installed or found.executable is None:
            self.last_failure = "unavailable" if found.state == "unavailable" else "not_installed"
            raise ServerError(self.last_failure)
        self.cleanup_orphans()
        self._stop_process()
        folder = run_dir(self._root)
        folder.mkdir(parents=True, exist_ok=True)
        self._save_models(self.models())
        saved = _read_json(folder / STATE_FILE)
        port = int(saved.get("port") or 0) if isinstance(saved, dict) else 0
        if not port or not port_free(port):
            port = free_port()
        key = secrets.token_urlsafe(32)
        environment = self._env(found.folder or found.executable.parent, folder / "home")
        environment["LLAMA_API_KEY"] = key
        self._state = "starting"
        try:
            process = _Process(self._argv(found.executable, port), cwd=folder / "home", env=environment,
                               log=folder / "server.log")
        except ServerError:
            self._state, self.last_failure = "failed", "start_failed"
            raise
        self._process = process
        self._record(process.pid)
        connection = Connection(f"http://127.0.0.1:{port}/v1", key, port)
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
            time.sleep(0.1)
        self._connection = connection
        self._state = "running"
        self.last_failure = None
        self._missed = 0
        self._last_active = self._clock()
        _write_json(folder / STATE_FILE, {"port": port})
        if self._endpoint is not None:
            try:
                self._endpoint(connection, self._context())
            except Exception:  # noqa: BLE001
                logger.warning("Learning couldn't save its model server's address", exc_info=True)
        self._start_monitor()
        logger.info("Learning started its model server on 127.0.0.1:%d", port)
        return connection

    def _context(self) -> int:
        return max((entry.context for entry in self.models()), default=32_768)

    def _record(self, pid: int) -> None:
        try:
            from row_bot import owned_processes

            owned_processes.record(ledger_path(self._root), pid, server=OWNER)
        except Exception:  # noqa: BLE001
            logger.debug("Learning couldn't record its model server", exc_info=True)

    def _forget(self, pid: int | None) -> None:
        if pid is None:
            return
        try:
            from row_bot import owned_processes

            owned_processes.forget(ledger_path(self._root), pid)
        except Exception:  # noqa: BLE001
            logger.debug("Learning couldn't forget its model server's record", exc_info=True)

    def cleanup_orphans(self) -> int:
        """Stop a server (and its model processes) a Row-Bot that has since ended left running."""

        path = ledger_path(self._root)
        if not path.is_file():
            return 0
        try:
            from row_bot import owned_processes

            return owned_processes.cleanup(path, tree=True)
        except Exception:  # noqa: BLE001
            logger.debug("Learning couldn't check for a model server left running", exc_info=True)
            return 0

    def _stop_process(self) -> bool:
        process = self._process
        self._process = None
        self._connection = None
        if process is None:
            return True
        ended = process.stop()
        self._forget(process.pid)
        return ended

    def _fail(self, reason: str) -> None:
        self._stop_process()
        self._state, self.last_failure = "failed", reason
        logger.warning("Learning's model server stopped (%s)", reason)

    def stop(self, reason: str = "you") -> bool:
        """Stop the server and everything it loaded now (the next request starts it again)."""

        with self._lock:
            had = self._process is not None
            ended = self._stop_process()
            if self._state != "failed" or had:
                self._state = "stopped"
            if had:
                logger.info("Learning stopped its model server (%s)", reason)
            return ended

    def release_card(self) -> bool:
        """Training wants the graphics card: stop the server unless something holds it (a comparison
        or a ready check). True when it stopped."""

        with self._lock:
            if self._process is None or self._holds:
                return False
            return self.stop("training")

    @contextlib.contextmanager
    def hold(self) -> Iterator[Connection]:
        """Keep the server running (and started) while a comparison or a ready check uses it."""

        with self._lock:
            connection = self.ensure()
            self._holds += 1
        try:
            yield connection
        finally:
            with self._lock:
                self._holds = max(0, self._holds - 1)
                self._last_active = self._clock()

    def load(self, name: str, *, timeout: float = LOAD_SECONDS) -> None:
        """Load ``name`` now and wait until it answers (a big model takes a while; the probe's own
        requests then don't wait for it). Raises :class:`ServerError`."""

        connection = self.ensure()
        if not any(entry.name == name for entry in self.models()):
            raise ServerError("unknown_model")
        base = f"http://127.0.0.1:{connection.port}"
        try:
            self._http.request("POST", f"{base}/models/load", key=connection.api_key, body={"model": name},
                               timeout=30.0)
        except Exception:  # noqa: BLE001 - its status says what happened
            pass
        deadline = self._clock() + timeout
        while True:
            status = self.model_status(name)
            if status == "loaded":
                return
            process = self._process
            if process is None or not process.alive():
                raise ServerError("crashed")
            if status == "failed" or self._clock() > deadline:
                raise ServerError("load_failed")
            time.sleep(0.25)

    def model_status(self, name: str) -> str | None:
        """The server's word for a model now: loaded, loading, unloaded, sleeping, failed (None: it
        isn't running or doesn't list it)."""

        for item in self._listed() or ():
            if item.get("id") == name:
                status = item.get("status")
                value = status.get("value") if isinstance(status, Mapping) else status
                return str(value or "") or None
        return None

    def _listed(self) -> list[Mapping[str, Any]] | None:
        connection = self._connection
        if connection is None:
            return None
        try:
            status, payload = self._http.request("GET", f"http://127.0.0.1:{connection.port}/models",
                                                 key=connection.api_key, timeout=5.0)
        except Exception:  # noqa: BLE001
            return None
        if status != 200 or not isinstance(payload, Mapping):
            return None
        return [item for item in payload.get("data") or () if isinstance(item, Mapping)]

    def unload(self, name: str) -> None:
        """Give back the graphics memory ``name`` holds now (it loads again when next asked for)."""

        connection = self._connection
        if connection is None:
            return
        try:
            self._http.request("POST", f"http://127.0.0.1:{connection.port}/models/unload", key=connection.api_key,
                               body={"model": name}, timeout=STOP_SECONDS + 5)
        except Exception:  # noqa: BLE001
            logger.debug("Learning couldn't unload a personal model", exc_info=True)

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
            listed = self._listed() if healthy else None
            if listed is None:
                return
            now = self._clock()
            busy = False
            for item in listed:
                value = item.get("status")
                value = value.get("value") if isinstance(value, Mapping) else value
                if value == "sleeping":
                    self.unload(str(item.get("id") or ""))  # idle for IDLE_SECONDS: its memory goes back
                elif value in ("loaded", "loading"):
                    busy = True
            if busy or self._holds:
                self._last_active = now
            elif now - self._last_active >= self.server_idle_seconds:
                self.stop("idle")

    def _start_monitor(self) -> None:
        if not self._monitor_enabled or (self._monitor is not None and self._monitor.is_alive()):
            return
        self._wake.clear()
        self._monitor = threading.Thread(target=self._watch, name="learning-model-server", daemon=True)
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
                logger.debug("Learning's model server check failed", exc_info=True)

    def shutdown(self) -> None:
        """Row-Bot is quitting: the server and everything it loaded end now."""

        self._closed = True
        self._wake.set()
        self.stop("app_closed")

    def describe(self) -> dict[str, Any]:
        """For the Personal model view: running or not, and why it couldn't start (no paths)."""

        return {"state": self._state, "failure": self.last_failure, "models": len(self.models())}


# ── The app's server ────────────────────────────────────────────────────────

_lock = threading.Lock()
_servers: dict[str, ManagedServer] = {}


def _save_endpoint(connection: Connection, context: int) -> None:
    """Row-Bot's endpoint for the server: an ordinary local ``llama_cpp`` endpoint marked managed, so
    its address and key are asked for at each request (the port only changes when the old one is taken)."""

    from row_bot.providers import custom

    current = custom.get_custom_endpoint(ENDPOINT_ID)
    wanted = connection.base_url
    manual = {"context_window": int(context)}
    if current is not None and current.get("base_url") == wanted and current.get("managed") == OWNER and \
            (current.get("manual_capabilities") or {}).get("context_window") == manual["context_window"]:
        return
    custom.save_custom_endpoint({
        "id": ENDPOINT_ID, "name": f"Personal model ({NAME})", "base_url": wanted, "profile": "llama_cpp",
        "execution_location": "local", "auth_required": False, "reasoning_mode": "off", "managed": OWNER,
        "manual_capabilities": manual,
    })


def get(root: Path | None = None) -> ManagedServer:
    """The server for this data folder (made on first use; nothing starts until a request)."""

    from row_bot import learning

    folder = str((root if root is not None else learning.data_dir()).resolve())
    with _lock:
        found = _servers.get(folder)
        if found is None:
            found = _servers[folder] = ManagedServer(root, endpoint=_save_endpoint)
        return found


def loaded() -> list[ManagedServer]:
    with _lock:
        return list(_servers.values())


def endpoint_connection(*, start: bool = True) -> tuple[str, str] | None:
    """For ``providers/custom.py``: the running server's address and key, started on demand."""

    found = get().connection(start=start)
    return (found.base_url, found.api_key) if found is not None else None


def release_card() -> bool:
    """Training is about to start: stop any server nothing holds."""

    return any([server.release_card() for server in loaded()])


def shutdown(timeout: float = 10.0) -> None:
    """Row-Bot is quitting: every server it started ends with its model processes."""

    for server in loaded():
        try:
            server.shutdown()
        except Exception:  # noqa: BLE001
            logger.debug("Learning couldn't stop its model server", exc_info=True)


def remove(root: Path | None = None) -> int:
    """**Remove engine**: the server stops with everything it loaded and its files go. Returns the
    bytes freed. Versions that ran there need to be made ready somewhere else (or the server
    downloaded again)."""

    from row_bot import learning
    from row_bot.learning.training import server_files

    folder = str((root if root is not None else learning.data_dir()).resolve())
    with _lock:
        server = _servers.pop(folder, None)
    if server is not None:
        server.shutdown()
    return server_files.remove(root)


def cleanup_orphans(root: Path | None = None) -> int:
    """At start-up (only when a record exists): stop a server a crashed Row-Bot left running."""

    path = ledger_path(root)
    if not path.is_file():
        return 0
    from row_bot import owned_processes

    return owned_processes.cleanup(path, tree=True)


__all__ = [
    "Connection",
    "ENDPOINT_ID",
    "IDLE_SECONDS",
    "ManagedServer",
    "ModelEntry",
    "NAME",
    "RUNTIME",
    "SERVER_IDLE_SECONDS",
    "ServerError",
    "cleanup_orphans",
    "endpoint_connection",
    "free_port",
    "get",
    "ledger_path",
    "loaded",
    "models_max",
    "presets_text",
    "release_card",
    "remove",
    "run_dir",
    "shutdown",
]
