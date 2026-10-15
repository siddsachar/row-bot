"""Background processes: programs the agent starts, and commands that run long (F1, F6).

One manager over the existing tracked-process owner (``developer.runtime``): every process
starts through the trusted worker inside its own containment (a Windows job object, a process
group, or the Linux supervisor), so Stop ends the whole tree. Each process belongs to the
conversation that started it; the agent sees and stops only that conversation's, a person sees
and stops all of them. Output goes to a bounded, rotating log file per process: the agent reads
its tail, and the whole of what is kept through ``read_tool_output``.

``run_command`` runs every command here too. A command that finishes within the inline limit
returns as before and leaves nothing behind; one still running then (or started with
``background=True``) carries on as a background process, so the turn isn't blocked.

Everything stops when Row-Bot quits; anything a crash left running is stopped, and reported,
on the next start. Processes that print or listen on a local address record it
(``local_servers``), so the browser can tell servers Row-Bot itself started (decision D5).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

logger = logging.getLogger(__name__)

# At most this many run at once in one conversation, and in all of Row-Bot (D1).
MAX_RUNNING_PER_CONVERSATION = 5
MAX_RUNNING_TOTAL = 12
# run_command waits this long before a command carries on in the background (F6).
INLINE_SECONDS = 120
# Each log file; one older file is kept, so a process keeps at most twice this.
LOG_ROTATE_BYTES = 2 * 1024 * 1024
# Finished processes kept (with their logs) for the lists and "View output".
KEEP_FINISHED = 100
KEEP_DAYS = 7
# One page of output for a reader.
READ_LIMIT = 64 * 1024
MAX_ENV_VARIABLES = 64
MAX_ENV_VALUE_CHARS = 8 * 1024
MAX_NAME_CHARS = 80

REGISTRY_KEY = "row-bot:background-processes"
# The worker refused before running anything: no process containment on this computer.
_CONTAINMENT_UNAVAILABLE = frozenset({"process_containment_unavailable", "process_owner_unavailable"})
PROCESS_ID = re.compile(r"proc-[0-9a-f]{8}")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_FOLDER = "processes"
_RECORD = "record.json"

Kind = Literal["process", "command"]
Status = Literal["running", "stopping", "finished", "failed", "stopped"]
StoppedBy = Literal["", "you", "agent", "quit", "restart", "conversation", "limit"]


class ProcessLimitError(RuntimeError):
    """Too many background processes are running; the message says so in plain words."""


class ProcessStartError(RuntimeError):
    """The process could not be started; the message says why in plain words."""


# ── The log ─────────────────────────────────────────────────────────────────


def _utf8_boundary(data: bytes) -> int:
    """The length of the longest prefix of *data* that ends on a whole character."""
    size = len(data)
    for back in range(1, min(4, size) + 1):
        byte = data[size - back]
        if byte & 0xC0 == 0x80:
            continue
        need = 1 if byte < 0x80 else 2 if byte >> 5 == 0b110 else 3 if byte >> 4 == 0b1110 else 4
        return size if back >= need else size - back
    return size


class ProcessLog:
    """One process's output: ``output.log`` and, after it fills, ``output.1.log`` before it.

    Positions are counted from the first byte the process wrote, so a reader can carry on
    from where it stopped across rotations; output older than both files is gone.
    """

    def __init__(self, folder: Path, *, dropped: int = 0) -> None:
        self.folder = folder
        self._lock = threading.Lock()
        self._handle: Any = None
        self._closed = False
        previous = self.previous_path.stat().st_size if self.previous_path.exists() else 0
        current = self.current_path.stat().st_size if self.current_path.exists() else 0
        self.previous_start: int | None = dropped if self.previous_path.exists() else None
        self.current_start = dropped + previous
        self.written = self.current_start + current

    @property
    def current_path(self) -> Path:
        return self.folder / "output.log"

    @property
    def previous_path(self) -> Path:
        return self.folder / "output.1.log"

    @property
    def available_start(self) -> int:
        return self.previous_start if self.previous_start is not None else self.current_start

    def write(self, text: str) -> None:
        data = str(text).encode("utf-8", "replace")
        if not data:
            return
        with self._lock:
            if self._closed:
                return
            try:
                if self._handle is None:
                    self.folder.mkdir(parents=True, exist_ok=True)
                    self._handle = open(self.current_path, "ab")  # noqa: SIM115 - kept open while it runs
                if self.written > self.current_start and self.written - self.current_start + len(data) > LOG_ROTATE_BYTES:
                    self._handle.close()
                    os.replace(self.current_path, self.previous_path)
                    self.previous_start = self.current_start
                    self.current_start = self.written
                    self._handle = open(self.current_path, "ab")  # noqa: SIM115
                self._handle.write(data)
                self._handle.flush()
                self.written += len(data)
            except OSError:
                logger.warning("Could not write a background process log", exc_info=True)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._handle is not None:
                try:
                    self._handle.close()
                except OSError:
                    pass
                self._handle = None

    def read(self, after: int = 0, limit: int = READ_LIMIT) -> tuple[str, int, int]:
        """Text from position *after* (or the oldest kept), at most *limit* bytes: (text, start, end)."""
        with self._lock:
            start = min(max(int(after or 0), self.available_start), self.written)
            end = min(self.written, start + max(1, int(limit)))
            parts: list[bytes] = []
            position = start
            try:
                if self.previous_start is not None and position < self.current_start:
                    upto = min(end, self.current_start)
                    with open(self.previous_path, "rb") as stream:
                        stream.seek(position - self.previous_start)
                        parts.append(stream.read(upto - position))
                    position = upto
                if position < end:
                    with open(self.current_path, "rb") as stream:
                        stream.seek(position - self.current_start)
                        parts.append(stream.read(end - position))
            except OSError:
                return "", start, start
        data = b"".join(parts)
        if start + len(data) < self.written:
            data = data[:_utf8_boundary(data)]
        return data.decode("utf-8", "replace"), start, start + len(data)

    def text(self, limit: int = 2 * LOG_ROTATE_BYTES) -> tuple[str, bool]:
        """Everything kept, up to *limit* bytes from the end: (text, whether earlier output is gone)."""
        start = max(self.available_start, self.written - limit)
        text, first, _end = self.read(start, limit)
        return text, first > 0

    def tail(self, max_bytes: int) -> tuple[str, bool]:
        """The last *max_bytes* of output: (text, whether there is earlier output before it)."""
        start = max(self.available_start, self.written - max_bytes)
        text, first, _end = self.read(start, max_bytes)
        if first > 0 and "\n" in text:
            text = text.split("\n", 1)[1]  # Start on a whole line.
        return text, first > 0

    @property
    def dropped(self) -> int:
        return self.available_start


# ── Records ─────────────────────────────────────────────────────────────────


@dataclass
class BackgroundProcess:
    """One process the manager started, running or finished."""

    process_id: str
    conversation_id: str
    command: str
    folder: str
    kind: Kind
    log: ProcessLog
    name: str = ""
    env_names: tuple[str, ...] = ()
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    exit_code: int | None = None
    outcome: str = ""
    stopped_by: str = ""
    # run_command still waiting for it in the turn: not listed, not counted.
    inline: bool = False
    pid: int | None = None
    pid_birth: float | None = None
    worker_pid: int | None = None
    worker_birth: float | None = None
    urls: list[str] = field(default_factory=list)
    ports: list[int] = field(default_factory=list)
    tracked: Any = None
    lock: threading.RLock = field(default_factory=threading.RLock)
    _scan_tail: str = ""
    _ports_checked: float = 0.0

    @property
    def status(self) -> Status:
        if self.outcome in {"finished", "failed", "stopped"}:
            return self.outcome  # type: ignore[return-value]
        tracked = self.tracked
        if tracked is not None and getattr(tracked, "stop_requested", False):
            return "stopping"
        return "running"

    @property
    def running(self) -> bool:
        return not self.outcome

    def runtime_seconds(self, now: float | None = None) -> float:
        end = self.ended_at if self.ended_at is not None else (now or time.time())
        return max(0.0, end - self.started_at)


_LOCK = threading.RLock()
_RECORDS: dict[str, BackgroundProcess] = {}
_LOADED_ROOT: Path | None = None
_LEFTOVERS: list[dict[str, Any]] = []


def processes_root() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir() / _FOLDER


def _conversation_folder(conversation_id: str) -> Path:
    from row_bot.tool_outputs import thread_folder_name

    return processes_root() / thread_folder_name(conversation_id)


def _iso(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _record_data(record: BackgroundProcess) -> dict[str, Any]:
    return {
        "version": 1,
        "process_id": record.process_id,
        "conversation_id": record.conversation_id,
        "command": record.command,
        "folder": record.folder,
        "kind": record.kind,
        "name": record.name,
        "env_names": list(record.env_names),
        "started_at": record.started_at,
        "ended_at": record.ended_at,
        "exit_code": record.exit_code,
        "outcome": record.outcome,
        "stopped_by": record.stopped_by,
        "pid": record.pid,
        "pid_birth": record.pid_birth,
        "worker_pid": record.worker_pid,
        "worker_birth": record.worker_birth,
        "urls": record.urls[:8],
        "ports": record.ports[:8],
        "log_dropped": record.log.dropped,
    }


def _save(record: BackgroundProcess) -> None:
    if record.inline:
        return
    folder = record.log.folder
    try:
        folder.mkdir(parents=True, exist_ok=True)
        partial = folder / f"{_RECORD}.{os.getpid()}.part"
        partial.write_text(json.dumps(_record_data(record)), encoding="utf-8")
        os.replace(partial, folder / _RECORD)
    except OSError:
        logger.warning("Could not save a background process record", exc_info=True)


def _load_record(path: Path) -> BackgroundProcess | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != 1:
        return None
    process_id = str(data.get("process_id") or "")
    if not PROCESS_ID.fullmatch(process_id) or path.parent.name != process_id:
        return None
    kind = data.get("kind") if data.get("kind") in {"process", "command"} else "process"

    def number(name: str) -> float | None:
        value = data.get(name)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def integer(name: str) -> int | None:
        value = data.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    dropped = integer("log_dropped") or 0
    return BackgroundProcess(
        process_id=process_id,
        conversation_id=str(data.get("conversation_id") or "default"),
        command=str(data.get("command") or "")[:4096],
        folder=str(data.get("folder") or "")[:4096],
        kind=kind,
        log=ProcessLog(path.parent, dropped=max(0, dropped)),
        name=str(data.get("name") or "")[:MAX_NAME_CHARS],
        env_names=tuple(str(item) for item in data.get("env_names") or () if isinstance(item, str))[:MAX_ENV_VARIABLES],
        started_at=number("started_at") or path.stat().st_mtime,
        ended_at=number("ended_at"),
        exit_code=integer("exit_code"),
        outcome=str(data.get("outcome") or "") if data.get("outcome") in {"finished", "failed", "stopped"} else "",
        stopped_by=str(data.get("stopped_by") or ""),
        pid=integer("pid"),
        pid_birth=number("pid_birth"),
        worker_pid=integer("worker_pid"),
        worker_birth=number("worker_birth"),
        urls=[str(item) for item in data.get("urls") or () if isinstance(item, str)][:8],
        ports=[item for item in data.get("ports") or () if isinstance(item, int)][:8],
    )


def _remove_folder(folder: Path) -> None:
    try:
        from row_bot.thread_cleanup import resolve_managed_path

        path = resolve_managed_path(processes_root(), folder)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    except (OSError, ValueError):
        logger.debug("Could not remove a background process folder", exc_info=True)


def _ensure_loaded() -> None:
    """Read the processes saved under this data folder once; anything a crash left running is stopped."""
    global _LOADED_ROOT
    root = processes_root()
    with _LOCK:
        if _LOADED_ROOT == root:
            return
        # A new data folder (tests re-point it): what belonged to the old one is not listed here.
        _RECORDS.clear()
        _LEFTOVERS.clear()
        _LOADED_ROOT = root
        if not root.exists():
            return
        cutoff = time.time() - KEEP_DAYS * 86400
        for folder in root.glob("*/proc-*"):
            # A command that ran in a turn when Row-Bot stopped left only its log.
            try:
                if not (folder / _RECORD).exists() and folder.stat().st_mtime < time.time() - 86400:
                    _remove_folder(folder)
            except OSError:
                continue
        loaded: list[BackgroundProcess] = []
        for path in root.glob(f"*/proc-*/{_RECORD}"):
            record = _load_record(path)
            if record is None:
                continue
            if record.outcome == "":
                _stop_left_over(record)
                _save(record)
            if (record.ended_at or record.started_at) < cutoff:
                _remove_folder(record.log.folder)
                continue
            loaded.append(record)
        loaded.sort(key=lambda item: item.started_at)
        for record in loaded[:-KEEP_FINISHED] if len(loaded) > KEEP_FINISHED else ():
            _remove_folder(record.log.folder)
        for record in loaded[-KEEP_FINISHED:]:
            _RECORDS[record.process_id] = record


def _same_process(pid: int | None, birth: float | None):
    """The process with this pid if it is the one that was started then, else None."""
    if not pid or birth is None:
        return None
    try:
        import psutil

        process = psutil.Process(pid)
        return process if abs(process.create_time() - birth) < 0.01 else None
    except Exception:
        return None


def _kill_tree(process) -> bool:
    import psutil

    try:
        family = process.children(recursive=True) + [process]
    except psutil.Error:
        family = [process]
    for member in family:
        try:
            member.kill()
        except psutil.NoSuchProcess:
            pass
        except psutil.Error:
            pass
    _gone, alive = psutil.wait_procs(family, timeout=3)
    return not alive


def _stop_left_over(record: BackgroundProcess) -> None:
    """A process a crash left running: stop it if it is still the one Row-Bot started."""
    alive = [process for process in (_same_process(record.pid, record.pid_birth),
                                     _same_process(record.worker_pid, record.worker_birth)) if process]
    stopped = all(_kill_tree(process) for process in alive) if alive else True
    if alive and record.worker_pid and os.name != "nt" and _same_process(record.worker_pid, record.worker_birth):
        try:
            import signal

            os.killpg(record.worker_pid, signal.SIGKILL)
        except OSError:
            pass
    record.outcome = "stopped"
    record.stopped_by = "restart" if alive else "quit"
    record.ended_at = record.ended_at or time.time()
    if alive:
        _LEFTOVERS.append({"process_id": record.process_id, "command": record.command,
                           "name": record.name, "stopped": stopped})


# ── Starting ────────────────────────────────────────────────────────────────


def _new_id() -> str:
    while True:
        candidate = "proc-" + uuid.uuid4().hex[:8]
        if candidate not in _RECORDS:
            return candidate


def _running(conversation_id: str | None = None) -> list[BackgroundProcess]:
    return [record for record in _RECORDS.values()
            if record.running and not record.inline
            and (conversation_id is None or record.conversation_id == conversation_id)]


def _limit_message(conversation_id: str) -> str | None:
    if len(_running(conversation_id)) >= MAX_RUNNING_PER_CONVERSATION:
        return (f"{MAX_RUNNING_PER_CONVERSATION} background processes are already running in this conversation, "
                "the most it can have at once. Stop one with stop_process first.")
    if len(_running()) >= MAX_RUNNING_TOTAL:
        return (f"{MAX_RUNNING_TOTAL} background processes are already running across Row-Bot, the most it runs at "
                "once. Stop one first (Home › Monitor lists them all).")
    return None


def check_environment(env: dict[str, Any] | None) -> dict[str, str]:
    """Validated extra environment variables: names like PORT or NODE_ENV, text values."""
    if not env:
        return {}
    if not isinstance(env, dict):
        raise ValueError("Environment variables must be names with text values, such as {\"PORT\": \"3000\"}.")
    if len(env) > MAX_ENV_VARIABLES:
        raise ValueError(f"At most {MAX_ENV_VARIABLES} environment variables can be set.")
    clean: dict[str, str] = {}
    for name, value in env.items():
        name = str(name)
        if not _ENV_NAME.fullmatch(name):
            raise ValueError(f"{name!r} isn't a valid environment variable name.")
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError(f"The value of {name} must be text.")
        text = str(value)
        if "\0" in text or len(text) > MAX_ENV_VALUE_CHARS:
            raise ValueError(f"The value of {name} is too long or contains a null character.")
        clean[name] = text
    return clean


def _birth(pid: int | None) -> float | None:
    if not pid:
        return None
    try:
        import psutil

        return psutil.Process(pid).create_time()
    except Exception:
        return None


def start(
    conversation_id: str,
    argv: list[str],
    *,
    command: str,
    folder: str,
    kind: Kind = "process",
    env: dict[str, str] | None = None,
    name: str = "",
    inline: bool = False,
) -> BackgroundProcess:
    """Start *argv* in *folder* for a conversation, inside its own containment.

    *command* is what the person and the agent see. A ``command`` (run_command) that ends on
    its own leaves what it deliberately opened running, as a plain subprocess did; a
    ``process`` (start_process) takes everything it started with it.
    Raises ProcessLimitError at the caps and ProcessStartError when it can't start.
    """
    from row_bot.developer import runtime

    _ensure_loaded()
    conversation = str(conversation_id or "default")
    root = Path(folder)
    if not root.is_dir():
        raise ProcessStartError(f"The folder {folder} doesn't exist.")
    with _LOCK:
        _retire_finished()
        if not inline:
            refusal = _limit_message(conversation)
            if refusal:
                raise ProcessLimitError(refusal)
        process_id = _new_id()
        record = BackgroundProcess(
            process_id=process_id,
            conversation_id=conversation,
            command=command[:4096],
            folder=str(root),
            kind=kind,
            log=ProcessLog(_conversation_folder(conversation) / process_id),
            name=" ".join(str(name or "").split())[:MAX_NAME_CHARS],
            env_names=tuple(sorted(env or {})),
            inline=inline,
        )
        _RECORDS[process_id] = record
    _save(record)

    def on_output(_channel: str, text: str) -> None:
        record.log.write(text)
        _scan_urls(record, text)

    def on_quiesced(state) -> None:
        _finish(record, state)

    try:
        tracked = runtime.launch_tracked_process(
            root, list(argv), command[:4096],
            process_id=str(uuid.uuid4()),
            metadata={"background_process": process_id, "conversation_id": conversation},
            on_quiesced=on_quiesced,
            env=env or None,
            on_output=on_output,
            release_on_exit=kind == "command",
            registry_key=REGISTRY_KEY,
        )
    except Exception as exc:
        with _LOCK:
            _RECORDS.pop(process_id, None)
        record.log.close()
        _remove_folder(record.log.folder)
        if str(exc) == "process_limit":
            raise ProcessLimitError("Too many processes are running in Row-Bot right now. Stop one first.") from exc
        logger.warning("Could not start a background process", exc_info=True)
        raise ProcessStartError("Row-Bot couldn't start the process on this computer.") from exc
    with record.lock:
        record.tracked = tracked
        record.worker_pid = tracked.process.pid
        record.pid = tracked.child_pid
    record.worker_birth = _birth(record.worker_pid)
    record.pid_birth = _birth(record.pid)
    _save(record)
    return record


def _finish(record: BackgroundProcess, state) -> None:
    with record.lock:
        if record.outcome:
            return
        record.ended_at = time.time()
        record.exit_code = state.exit_code
        if state.stop_requested:
            record.outcome = "stopped"
            record.stopped_by = record.stopped_by or "you"
        elif state.code or state.exit_code not in (0,):
            record.outcome = "failed"
        else:
            record.outcome = "finished"
        if record.pid is None:
            record.pid = state.child_pid
    record.log.close()
    _save(record)


def _retire_finished() -> None:
    from row_bot.developer import runtime

    for record in list(_RECORDS.values()):
        tracked = record.tracked
        if tracked is not None and record.outcome and tracked.done.is_set():
            if runtime.retire_tracked_process(tracked):
                record.tracked = None
    finished = [record for record in _RECORDS.values() if not record.running and not record.inline]
    if len(finished) > KEEP_FINISHED:
        finished.sort(key=lambda item: item.ended_at or item.started_at)
        for record in finished[:-KEEP_FINISHED]:
            _RECORDS.pop(record.process_id, None)
            _remove_folder(record.log.folder)


# ── Local addresses (F4) ────────────────────────────────────────────────────

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
_LOCAL_URL = re.compile(r"\b(https?)://(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|\[::\]):(\d{2,5})\b", re.IGNORECASE)
_LOCAL_BINDS = frozenset({"127.0.0.1", "::1", "0.0.0.0", "::", "localhost"})


def local_origin(scheme: str, host: str, port: int) -> str:
    """The address a person would open for a server bound to *host*: all-interfaces reads localhost."""
    host = host.lower()
    if host in {"0.0.0.0", "[::]"}:
        host = "localhost"
    return f"{scheme.lower()}://{host}:{port}"


def _scan_urls(record: BackgroundProcess, text: str) -> None:
    with record.lock:
        scan = record._scan_tail + _ANSI.sub("", text)
        record._scan_tail = scan[-256:]
        for match in _LOCAL_URL.finditer(scan):
            port = int(match.group(3))
            if not 0 < port < 65536:
                continue
            origin = local_origin(match.group(1), match.group(2), port)
            if origin not in record.urls and len(record.urls) < 8:
                record.urls.append(origin)


def _listening_ports(record: BackgroundProcess) -> list[int]:
    """Ports the process (or anything it started) listens on locally; checked at most every 2 seconds."""
    now = time.monotonic()
    if not record.running or now - record._ports_checked < 2:
        return record.ports
    record._ports_checked = now
    process = _same_process(record.pid, record.pid_birth)
    if process is None:
        return record.ports
    import psutil

    ports: set[int] = set()
    try:
        family = [process] + process.children(recursive=True)
    except psutil.Error:
        family = [process]
    for member in family:
        try:
            for connection in member.net_connections(kind="inet"):
                address = getattr(connection, "laddr", None)
                if (connection.status == psutil.CONN_LISTEN and address
                        and str(address.ip) in _LOCAL_BINDS):
                    ports.add(int(address.port))
        except psutil.Error:
            continue
        except Exception:
            logger.debug("Could not read a background process's listening ports", exc_info=True)
    with record.lock:
        record.ports = sorted(ports)[:8]
    return record.ports


def addresses(record: BackgroundProcess) -> list[str]:
    """The local addresses a process serves: the ones it printed, then any other port it listens on."""
    ports = _listening_ports(record) if record.running else record.ports
    found = list(record.urls)
    for port in ports:
        if not any(url.endswith(f":{port}") for url in found):
            found.append(local_origin("http", "localhost", port))
    return found[:8]


def local_servers(conversation_id: str | None = None) -> list[dict[str, Any]]:
    """Running servers Row-Bot itself started, for the browser (decision D5).

    ``verified`` means the process tree listens on that port now; a printed address alone is
    not, since a program can print any address.
    """
    _ensure_loaded()
    with _LOCK:
        records = [record for record in _RECORDS.values() if record.running and not record.inline
                   and (conversation_id is None or record.conversation_id == conversation_id)]
    servers: list[dict[str, Any]] = []
    for record in records:
        listening = set(_listening_ports(record))
        for origin in addresses(record):
            port = int(origin.rsplit(":", 1)[1])
            servers.append({"origin": origin, "port": port, "process_id": record.process_id,
                            "conversation_id": record.conversation_id, "verified": port in listening})
    return servers


def owns_local_origin(origin: str, conversation_id: str | None = None) -> bool:
    """Whether a running server Row-Bot started listens at *origin* (``http://localhost:5173``)."""
    match = re.fullmatch(r"(https?)://(localhost|127\.0\.0\.1|\[::1\]):(\d{1,5})/?", str(origin or "").strip(),
                         re.IGNORECASE)
    if not match:
        return False
    port = int(match.group(3))
    return any(server["verified"] and server["port"] == port for server in local_servers(conversation_id))


# ── Reading, stopping, listing ──────────────────────────────────────────────


def get(process_id: str) -> BackgroundProcess | None:
    _ensure_loaded()
    with _LOCK:
        record = _RECORDS.get(str(process_id or "").strip())
    return None if record is None or record.inline else record


def list_processes(conversation_id: str | None = None) -> list[BackgroundProcess]:
    """Processes, newest first: running ones, then finished ones."""
    _ensure_loaded()
    with _LOCK:
        _retire_finished()
        records = [record for record in _RECORDS.values() if not record.inline
                   and (conversation_id is None or record.conversation_id == conversation_id)]
    return sorted(records, key=lambda item: (not item.running, -item.started_at))


def running_commands() -> list[BackgroundProcess]:
    """Long commands carrying on in the background now (not servers): Buddy shows them as work."""
    with _LOCK:
        return [record for record in _RECORDS.values()
                if record.running and not record.inline and record.kind == "command"]


def wait(record: BackgroundProcess, timeout: float | None) -> bool:
    """Wait for a process to end and its cleanup to finish; True when it has."""
    tracked = record.tracked
    if tracked is None:
        return not record.running
    return tracked.done.wait(timeout)


def _kill_descendants(record: BackgroundProcess) -> None:
    """On macOS a process group can be left by a child; end the tree as it stands too."""
    if sys.platform != "darwin":
        return
    process = _same_process(record.worker_pid, record.worker_birth)
    if process is None:
        return
    try:
        import psutil

        for child in process.children(recursive=True):
            try:
                child.kill()
            except psutil.Error:
                pass
    except Exception:
        logger.debug("Could not end a background process's children", exc_info=True)


def stop(process_id: str, *, by: StoppedBy = "you") -> BackgroundProcess | None:
    """Stop a process and everything it started; nothing happens if it already ended."""
    from row_bot.developer import runtime

    _ensure_loaded()
    with _LOCK:
        record = _RECORDS.get(str(process_id or "").strip())
    if record is None:
        return None
    with record.lock:
        if not record.running or record.tracked is None:
            return record
        record.stopped_by = record.stopped_by or by
    _kill_descendants(record)
    runtime.stop_tracked_process(record.tracked)
    return record


def stop_many(records: Iterable[BackgroundProcess], *, by: StoppedBy, timeout: float = 10.0) -> int:
    """Stop these and wait (up to *timeout* in all) for them to end; returns how many were running."""
    running = [record for record in records if record.running]
    for record in running:
        stop(record.process_id, by=by)
    deadline = time.monotonic() + timeout
    for record in running:
        wait(record, max(0.0, deadline - time.monotonic()))
    return len(running)


def stop_conversation(conversation_id: str) -> int:
    """Stop a conversation's processes (it is being deleted)."""
    _ensure_loaded()
    with _LOCK:
        records = [record for record in _RECORDS.values() if record.conversation_id == conversation_id]
    return stop_many(records, by="conversation", timeout=10.0)


def stop_all(*, by: StoppedBy = "quit", timeout: float = 10.0) -> int:
    """Stop every process (Row-Bot is quitting); returns how many were running."""
    with _LOCK:
        records = list(_RECORDS.values())
    if not records:
        return 0
    return stop_many(records, by=by, timeout=timeout)


def delete_conversation_logs(conversation_id: str) -> int:
    """Forget a deleted conversation's processes and remove their logs; returns 1 when a folder was removed."""
    with _LOCK:
        for process_id in [key for key, record in _RECORDS.items() if record.conversation_id == conversation_id]:
            _RECORDS.pop(process_id, None)
    folder = _conversation_folder(conversation_id)
    if not folder.exists():
        return 0
    _remove_folder(folder)
    return 1


def take_leftovers() -> list[dict[str, Any]]:
    """What the last start found still running from before (stopped then); read once."""
    _ensure_loaded()
    with _LOCK:
        found = list(_LEFTOVERS)
        _LEFTOVERS.clear()
    return found


def report_leftovers() -> str:
    """Read the saved processes at start-up; a notice for any a crash left running, else ''."""
    found = take_leftovers()
    if not found:
        return ""
    stopped = [item for item in found if item["stopped"]]
    failed = [item for item in found if not item["stopped"]]
    parts: list[str] = []
    if stopped:
        noun = "process" if len(stopped) == 1 else "processes"
        parts.append(f"{len(stopped)} background {noun} Row-Bot started before it last closed "
                     f"{'was' if len(stopped) == 1 else 'were'} still running; Row-Bot stopped "
                     f"{'it' if len(stopped) == 1 else 'them'}.")
    if failed:
        names = ", ".join((item["name"] or item["command"])[:60] for item in failed[:3])
        parts.append(f"Row-Bot couldn't stop {names}, left running from before it last closed; "
                     "end it in Task Manager or Activity Monitor.")
    return " ".join(parts)


# ── run_command (F6) ────────────────────────────────────────────────────────


@dataclass
class CommandRun:
    """A run_command's outcome: finished in the turn, or carrying on in the background."""

    record: BackgroundProcess
    finished: bool
    exit_code: int | None = None
    output: str = ""
    earlier_output_dropped: bool = False
    cancelled: bool = False
    # Still running, but the caps were reached: it was stopped instead.
    limit_message: str = ""


def _forget(record: BackgroundProcess) -> None:
    from row_bot.developer import runtime

    with _LOCK:
        _RECORDS.pop(record.process_id, None)
    record.log.close()
    if record.tracked is not None and record.tracked.done.is_set():
        runtime.retire_tracked_process(record.tracked)
    _remove_folder(record.log.folder)


def _promote(record: BackgroundProcess) -> str | None:
    """Carry an inline command on in the background; the refusal when the caps are reached."""
    with _LOCK:
        if not record.running:
            return None
        refusal = _limit_message(record.conversation_id)
        if refusal:
            return refusal
        record.inline = False
    _save(record)
    return None


def run_command(
    conversation_id: str,
    argv: list[str],
    *,
    command: str,
    folder: str,
    timeout: float = INLINE_SECONDS,
    background: bool = False,
) -> CommandRun:
    """Run a command for run_command: in the turn when it ends within *timeout*, else in the background.

    The turn's Stop ends it while the turn waits; once it carries on in the background it
    belongs to the conversation (check_process, stop_process, the lists).
    """
    from row_bot.cancellation import current_cancellation_scope

    scope = current_cancellation_scope()
    if background:
        record = start(conversation_id, argv, command=command, folder=folder, kind="command")
        return CommandRun(record, finished=False)
    record = start(conversation_id, argv, command=command, folder=folder, kind="command", inline=True)
    unregister = scope.register(lambda: stop(record.process_id, by="you"), "subprocess.terminate") if scope else None
    try:
        done = wait(record, timeout)
        if not done and scope is not None and scope.is_cancelled():
            done = wait(record, 15)
    finally:
        if unregister is not None:
            unregister()
    cancelled = bool(scope is not None and scope.is_cancelled())
    if not done:
        refusal = _promote(record)
        if refusal:
            stop(record.process_id, by="limit")
        elif not record.inline:
            return CommandRun(record, finished=False)
        wait(record, 15)  # Stopped at the caps, or it ended just as the limit passed.
        text, dropped = record.log.text()
        _forget(record)
        return CommandRun(record, finished=True, exit_code=record.exit_code, output=text,
                          earlier_output_dropped=dropped, limit_message=refusal or "")
    text, dropped = record.log.text()
    _forget(record)
    if not text and getattr(record.tracked, "code", "") in _CONTAINMENT_UNAVAILABLE:
        # This computer can't contain processes (an old Linux kernel): the caller runs it plainly.
        raise ProcessStartError("Row-Bot can't keep processes in the background on this computer.")
    exit_code = 130 if cancelled else record.exit_code
    return CommandRun(record, finished=True, exit_code=exit_code, output=text, earlier_output_dropped=dropped,
                      cancelled=cancelled)


# ── Views ───────────────────────────────────────────────────────────────────


def duration_words(seconds: float) -> str:
    seconds = int(max(0, round(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def view(record: BackgroundProcess, *, conversation_title: str = "") -> dict[str, Any]:
    """The public view of one process (the client's lists and its card in the conversation)."""
    return {
        "process_id": record.process_id,
        "conversation_id": record.conversation_id,
        "conversation_title": conversation_title[:200],
        "command": record.command[:4096],
        "name": record.name,
        "folder": record.folder[:4096],
        "kind": record.kind,
        "status": record.status,
        "exit_code": record.exit_code,
        "started_at": _iso(record.started_at),
        "ended_at": _iso(record.ended_at),
        "runtime_seconds": round(record.runtime_seconds(), 1),
        "local_urls": addresses(record),
        "pid": record.pid,
        "stopped_by": record.stopped_by,
        "env_names": list(record.env_names)[:MAX_ENV_VARIABLES],
        "output_size": record.log.written,
    }
