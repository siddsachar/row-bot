from __future__ import annotations

import json
import os
import pathlib
import shlex
import subprocess
import collections
import logging
import signal
import sys
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from row_bot.developer import change_ledger
from row_bot.developer.change_ledger import FileChange
from row_bot.developer.sandbox import ApprovalDecision, decide_action
from row_bot.developer.state import ApprovalMode
from row_bot.process_cancellation import run_cancellable_subprocess


@dataclass(frozen=True)
class CommandSpec:
    label: str
    command: str
    kind: str = "test"


@dataclass(frozen=True)
class CommandResult:
    command: str
    cwd: str
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    decision: ApprovalDecision | None = None
    changed_files: list[str] = field(default_factory=list)
    execution_mode: str = "local"
    sandbox_backend: str = ""
    sandbox_pending_change_id: str = ""
    process_id: str = ""
    code: str = ""

    @property
    def ran(self) -> bool:
        return self.returncode is not None

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class ManagedProcess:
    workspace_path: str
    command: str
    pid: int


_SHELL_CONTROL_OPERATORS = ("&&", "||", "|", ">", "<")
_ACTIVE_PROCESSES: dict[str, list[subprocess.Popen]] = {}
_PROCESS_LOCK = threading.RLock()
_PROCESS_LIMIT = 32
_OUTPUT_LIMIT = 256 * 1024
_OUTPUT_ENTRY_LIMIT = 1024
_REMOTE_STOP_TIMEOUT = 8
logger = logging.getLogger(__name__)


def _close_owned_stream(stream) -> None:
    try:
        stream.close()
    except (OSError, ValueError):
        # A stopped Windows pipe can reject a buffered flush. Its underlying
        # handle still belongs to this owner and must be closed without retrying
        # the failed write or abandoning lifecycle finalization.
        raw = getattr(stream, "raw", None)
        if raw is not None:
            try:
                raw.close()
            except (OSError, ValueError):
                pass


@dataclass
class TrackedProcess:
    """Private state attached to the existing registry's Popen object."""
    process_id: str
    command: str
    metadata: dict[str, str]
    process: subprocess.Popen
    on_quiesced: Callable[["TrackedProcess"], None] | None = None
    state: str = "starting"
    exit_code: int | None = None
    code: str = ""
    quiesced: bool = False
    finalization_complete: bool = False
    output_incomplete: bool = False
    output: collections.deque = field(default_factory=collections.deque)
    output_bytes: int = 0
    sequence: int = 0
    truncated: bool = False
    stop_requested: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)
    lifetime_lock: threading.Lock = field(default_factory=threading.Lock)
    ready: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    job: object | None = None
    containment_closed: bool | None = None
    threads: list[threading.Thread] = field(default_factory=list)
    guard: Callable[[], None] | None = None
    changed: threading.Condition = field(init=False)
    verify_receipt: Callable[[dict], dict] | None = None
    remote_receipt: dict | None = None
    remote_done: threading.Event = field(default_factory=threading.Event)
    host_quiesced: bool = False

    def __post_init__(self):
        self.changed = threading.Condition(self.lock)

    def append(self, channel: str, value: str) -> None:
        if not value:
            return
        with self.lock:
            self.sequence += 1
            cost = len(value.encode("utf-8"))
            self.output.append((self.sequence, channel, value, cost))
            self.output_bytes += cost
            while self.output_bytes > _OUTPUT_LIMIT or len(self.output) > _OUTPUT_ENTRY_LIMIT:
                self.output_bytes -= self.output.popleft()[3]
                self.truncated = True
            self.changed.notify_all()


def tracked_processes(workspace_path: str) -> tuple[TrackedProcess, ...]:
    """Passive snapshot from the sole existing process registry."""
    key = str(pathlib.Path(workspace_path).resolve())
    with _PROCESS_LOCK:
        return tuple(state for proc in _ACTIVE_PROCESSES.get(key, ())
                     if isinstance(state := getattr(proc, "_row_bot_state", None), TrackedProcess))


def _retirable_tracked_process(process: subprocess.Popen) -> bool:
    state = getattr(process, "_row_bot_state", None)
    return bool(state and state.quiesced and
                (state.on_quiesced is None or state.finalization_complete))


def _close_process_containment(state: TrackedProcess) -> bool:
    with state.lifetime_lock:
        if state.containment_closed is not None:
            return state.containment_closed
        if state.verify_receipt and not state.remote_done.is_set():
            def request_remote_stop():
                try:
                    state.process.stdin.write(b"stop\n")
                    state.process.stdin.flush()
                except (OSError, ValueError):
                    pass
            sender = threading.Thread(target=request_remote_stop, daemon=True,
                                      name="row-bot-developer-remote-stop")
            state.threads.append(sender)
            sender.start()
            state.remote_done.wait(timeout=_REMOTE_STOP_TIMEOUT)
        if state.job is not None:
            closed = state.job.close()
        elif os.name != "nt":
            try:
                os.killpg(state.process.pid, signal.SIGKILL)
                closed = True
            except ProcessLookupError:
                closed = True
            except OSError:
                closed = False
        else:
            # The trusted bootstrap never receives a command before assignment.
            try:
                if state.process.poll() is None:
                    state.process.kill()
                closed = True
            except OSError:
                closed = False
        state.host_quiesced = bool(closed)
        state.containment_closed = bool(closed) and (not state.verify_receipt or state.remote_done.is_set())
        return state.containment_closed


def stop_tracked_process(state: TrackedProcess) -> None:
    """Request cleanup of this exact owned process, without arbitrary PID input."""
    with state.lock:
        if state.quiesced or state.stop_requested:
            return
        state.stop_requested = True
        state.state = "stopping"
    threading.Thread(target=_close_process_containment, args=(state,),
                     name="row-bot-developer-stop", daemon=True).start()


def _frame_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate frame field")
        result[key] = value
    return result


def _read_process_frames(state: TrackedProcess) -> None:
    sequence = 0
    started = False
    ended = False
    try:
        while True:
            line = state.process.stdout.readline(32769)
            if not line:
                break
            if len(line) > 32768 or not line.endswith(b"\n"):
                raise ValueError("process_output_invalid")
            value = json.loads(line, object_pairs_hook=_frame_object,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite frame")))
            if (type(value) is not dict or type(value.get("version")) is not int or value.get("version") != 1
                    or type(value.get("sequence")) is not int or value["sequence"] != sequence + 1 or ended):
                raise ValueError("process_output_invalid")
            sequence += 1
            event = value.get("event")
            proof = None
            if state.verify_receipt and "receipt" in value:
                proof = state.verify_receipt(value.pop("receipt"))
                state.remote_receipt = proof
                if proof["payload"]["quiesced"]:
                    state.remote_done.set()
            if event == "started" and not started and set(value) == {"version", "sequence", "event", "pid"}:
                if type(value["pid"]) is not int or value["pid"] <= 0:
                    raise ValueError("process_output_invalid")
                if state.verify_receipt and (not proof or proof["payload"]["state"] != "running"):
                    raise ValueError("process_output_invalid")
                started = True
                with state.lock:
                    if not state.stop_requested:
                        state.state = "running"
                state.ready.set()
            elif event == "output" and started and set(value) == {"version", "sequence", "event", "channel", "text"}:
                if (value["channel"] not in {"stdout", "stderr"} or type(value["text"]) is not str
                        or len(value["text"]) > 2048 or len(json.dumps(value["text"], ensure_ascii=True)) > 8192):
                    raise ValueError("process_output_invalid")
                state.append(value["channel"], value["text"])
            elif event == "failed" and not started and set(value) == {"version", "sequence", "event", "code"}:
                if value["code"] not in {"process_start_failed", "process_containment_unavailable", "process_owner_unavailable"}:
                    raise ValueError("process_output_invalid")
                if state.verify_receipt and not proof:
                    if value["code"] not in {"process_containment_unavailable", "process_owner_unavailable"}:
                        raise ValueError("process_output_invalid")
                    state.remote_done.set()  # Trusted bootstrap rejected before command execution.
                with state.lock:
                    state.code = value["code"]
                    state.state = "failed"
                ended = True
                state.ready.set()
            elif event == "exited" and started and set(value) == {"version", "sequence", "event", "exit_code", "output_incomplete"}:
                if type(value["exit_code"]) is not int or type(value["output_incomplete"]) is not bool:
                    raise ValueError("process_output_invalid")
                if state.verify_receipt and (not proof or proof["payload"]["state"] != "complete"
                        or proof["payload"]["exit_code"] != value["exit_code"]
                        or proof["payload"]["output_incomplete"] != value["output_incomplete"]):
                    raise ValueError("process_output_invalid")
                with state.lock:
                    state.exit_code = value["exit_code"]
                    state.output_incomplete = value["output_incomplete"]
                ended = True
            else:
                raise ValueError("process_output_invalid")
    except (OSError, ValueError, TypeError, RecursionError):
        with state.lock:
            state.code = "process_output_invalid"
        stop_tracked_process(state)
    finally:
        _close_owned_stream(state.process.stdout)


def _read_bootstrap_errors(state: TrackedProcess) -> None:
    # Bootstrap diagnostics may include host runtime paths; expose only a stable
    # code. Continue draining so errors cannot block the owned process.
    try:
        while state.process.stderr.read(4096):
            with state.lock:
                state.code = "process_bootstrap_failed"
    except (OSError, ValueError):
        pass
    finally:
        _close_owned_stream(state.process.stderr)


def _watch_tracked_process(state: TrackedProcess) -> None:
    while True:
        try:
            state.process.wait(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            if state.guard and not state.stop_requested:
                try:
                    state.guard()
                except Exception:
                    with state.lock:
                        state.code = "process_revoked"
                    stop_tracked_process(state)
        except OSError:
            with state.lock:
                state.code = "process_cleanup_incomplete"
            break
    closed = _close_process_containment(state)
    try:
        state.process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        closed = False
    for thread in state.threads:
        thread.join(timeout=5)
    readers_done = all(not thread.is_alive() for thread in state.threads)
    if readers_done and not state.process.stdin.closed:
        _close_owned_stream(state.process.stdin)
    with state.lock:
        state.quiesced = closed and readers_done
        if not state.quiesced:
            state.state, state.code = "cleanup_incomplete", "process_cleanup_incomplete"
        elif state.code:
            state.state = "failed"
        else:
            state.state = "exited"
        if state.exit_code is None:
            state.exit_code = 130 if state.stop_requested else state.process.returncode
    state.ready.set()
    if state.quiesced and state.on_quiesced:
        try:
            state.on_quiesced(state)
            state.finalization_complete = True
        except Exception:
            with state.lock:
                state.code = "process_history_incomplete"
            logger.exception("Developer process history finalization failed")
    state.done.set()


def launch_tracked_process(root: pathlib.Path, argv: list[str], command: str, *,
        process_id: str | None = None, metadata: dict[str, str] | None = None,
        on_quiesced: Callable[[TrackedProcess], None] | None = None,
        validate: Callable[[], None] | None = None, startup_timeout: float = 5,
        bootstrap_argv: list[str] | None = None, request_fields: dict | None = None,
        verify_receipt: Callable[[dict], dict] | None = None,
        on_owner: Callable[[TrackedProcess], None] | None = None) -> TrackedProcess:
    """Start only after trusted bootstrap containment and current admission."""
    identity = process_id or str(uuid.uuid4())
    if bootstrap_argv is None and sys.platform.startswith("linux"):
        if verify_receipt is None:
            # Legacy NiceGUI callers use the same supervised owner without a
            # durable client command receipt. No detached-child group fallback.
            from row_bot.developer.process_worker import verify_receipt as check_receipt
            secret, target = os.urandom(32), "0" * 64
            request_fields = {"owner_id": identity, "container_id": target, "key": secret.hex()}
            def verify_receipt(receipt):
                return check_receipt(receipt, identity, target, secret)
    request = json.dumps({**(request_fields or {}), "argv": argv}, ensure_ascii=True).encode("ascii") + b"\n"
    if len(request) > 32768:
        raise ValueError("process_command_too_large")
    key = str(root.resolve())
    with _PROCESS_LOCK:
        while sum(len(values) for values in _ACTIVE_PROCESSES.values()) >= 128:
            old = next(((name, proc) for name, values in _ACTIVE_PROCESSES.items() for proc in values
                        if _retirable_tracked_process(proc)), None)
            if old is None:
                raise ValueError("process_limit")
            _ACTIVE_PROCESSES[old[0]].remove(old[1])
            if not _ACTIVE_PROCESSES[old[0]]:
                del _ACTIVE_PROCESSES[old[0]]
        entries = _ACTIVE_PROCESSES.setdefault(key, [])
        # Completed tail entries are discarded only when making room; active
        # and unquiesced entries always remain discoverable and stoppable.
        while len(entries) >= _PROCESS_LIMIT:
            old = next((proc for proc in entries if _retirable_tracked_process(proc)), None)
            if old is None:
                raise ValueError("process_limit")
            entries.remove(old)
        kwargs = {"cwd": key, "stdin": subprocess.PIPE, "stdout": subprocess.PIPE,
                  "stderr": subprocess.PIPE, "shell": False, "close_fds": True}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        process = subprocess.Popen(bootstrap_argv or [sys.executable, "-I", "-S", "-B",
            str(pathlib.Path(__file__).with_name("process_worker.py"))], **kwargs)
        state = TrackedProcess(identity, command, dict(metadata or {}), process, on_quiesced)
        state.guard = validate
        state.verify_receipt = verify_receipt
        process._row_bot_state = state
        entries.append(process)
    try:
        if os.name == "nt":
            from row_bot.plugins.worker_ownership import WindowsJob
            state.job = WindowsJob(process)
        if on_owner:
            on_owner(state)
        if validate:
            validate()
    except Exception:
        state.code = "process_admission_failed"
        _close_process_containment(state)
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            _close_owned_stream(stream)
        state.quiesced, state.state = bool(state.containment_closed), "failed"
        state.ready.set()
        try:
            if state.quiesced and on_quiesced:
                on_quiesced(state)
                state.finalization_complete = True
        finally:
            state.done.set()
        raise
    def admit():
        try:
            process.stdin.write(request)
            process.stdin.flush()
        except OSError:
            with state.lock:
                if not state.stop_requested:
                    state.code = "process_start_failed"
        finally:
            if not verify_receipt:
                _close_owned_stream(process.stdin)
    state.threads = [threading.Thread(target=target, args=args, daemon=True, name="row-bot-developer-process")
                     for target, args in ((_read_process_frames, (state,)), (_read_bootstrap_errors, (state,)), (admit, ()))]
    for thread in state.threads:
        thread.start()
    threading.Thread(target=_watch_tracked_process, args=(state,), daemon=True,
                     name="row-bot-developer-lifetime").start()
    if not state.ready.wait(startup_timeout):
        with state.lock:
            state.code = "process_start_timeout"
        stop_tracked_process(state)
    return state


def run_process_control_query(argv: list[str], payload: dict | None = None, *, timeout: float = 15) -> bytes:
    """Bounded explicit Docker inspect/recovery control, never a public shell."""
    request = json.dumps(payload, ensure_ascii=True).encode("ascii") + b"\n" if payload is not None else b""
    if len(request) > 8192:
        raise ValueError("process_control_unavailable")
    kwargs = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
              "shell": False, "close_fds": True}
    if os.name != "nt":
        kwargs["start_new_session"] = True
    process = subprocess.Popen(argv, **kwargs)
    job = None
    output, errors, overflow = bytearray(), bytearray(), threading.Event()
    def read(stream, buffer):
        try:
            while block := stream.read(1024):
                if len(buffer) + len(block) > 8192:
                    overflow.set()
                    process.kill()
                    return
                buffer.extend(block)
        except (OSError, ValueError):
            overflow.set()
        finally:
            _close_owned_stream(stream)
    def write():
        try:
            process.stdin.write(request)
            process.stdin.flush()
        except OSError:
            pass
        finally:
            _close_owned_stream(process.stdin)
    threads = []
    try:
        if os.name == "nt":
            from row_bot.plugins.worker_ownership import WindowsJob
            job = WindowsJob(process)
        threads = [threading.Thread(target=target, args=args, daemon=True)
            for target, args in ((read, (process.stdout, output)), (read, (process.stderr, errors)), (write, ()))]
        for thread in threads:
            thread.start()
        process.wait(timeout=timeout)
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        overflow.set()
    finally:
        if job:
            if not job.close():
                overflow.set()
        elif os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                overflow.set()
        elif process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            overflow.set()
        for thread in threads:
            thread.join(timeout=2)
        if not threads:
            for stream in (process.stdin, process.stdout, process.stderr):
                _close_owned_stream(stream)
    if overflow.is_set() or any(thread.is_alive() for thread in threads) or process.returncode != 0:
        raise ValueError("process_control_unavailable")
    return bytes(output)


def _unresolved_workspace_result(
    *,
    command: str,
    root: pathlib.Path,
    workspace_id: str,
) -> CommandResult:
    reason = f"Developer workspace could not be resolved: {workspace_id}"
    return CommandResult(
        command=command,
        cwd=str(root),
        returncode=None,
        stderr=reason,
        decision=ApprovalDecision("block", reason),
    )


def split_command(command: str) -> list[str]:
    """Split a simple command without invoking the platform shell."""
    return shlex.split(command, posix=True)


def _unquoted_text(command: str) -> str:
    chars: list[str] = []
    in_single = False
    in_double = False
    escaped = False
    for char in command:
        if escaped:
            escaped = False
            if not in_single and not in_double:
                chars.append(char)
            continue
        if char == "\\" and in_double:
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if not in_single and not in_double:
            chars.append(char)
    return "".join(chars)


def has_shell_control_operator(command: str) -> bool:
    text = _unquoted_text(command)
    return any(operator in text for operator in _SHELL_CONTROL_OPERATORS)


def detect_project_commands(workspace_path: str) -> list[CommandSpec]:
    root = pathlib.Path(workspace_path).expanduser()
    commands: list[CommandSpec] = []
    if (root / "package.json").exists():
        try:
            data = json.loads((root / "package.json").read_text(encoding="utf-8"))
            scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
            if isinstance(scripts, dict):
                package_runner = _detect_js_runner(root)
                if "test" in scripts:
                    commands.append(CommandSpec(f"{package_runner} test", f"{package_runner} test", "test"))
                if "lint" in scripts:
                    commands.append(CommandSpec(f"{package_runner} run lint", f"{package_runner} run lint", "lint"))
                if "typecheck" in scripts:
                    commands.append(CommandSpec(f"{package_runner} run typecheck", f"{package_runner} run typecheck", "typecheck"))
                for dev_name in ("dev", "start"):
                    if dev_name in scripts:
                        commands.append(CommandSpec(f"{package_runner} run {dev_name}", f"{package_runner} run {dev_name}", "server"))
        except Exception:
            pass
    if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists() or (root / "tests").exists():
        commands.append(CommandSpec("pytest", "python -m pytest"))
    if (root / "manage.py").exists():
        commands.append(CommandSpec("Django tests", "python manage.py test"))
    if (root / "Cargo.toml").exists():
        commands.append(CommandSpec("cargo test", "cargo test"))
    if (root / "go.mod").exists():
        commands.append(CommandSpec("go test", "go test ./..."))

    seen: set[str] = set()
    deduped: list[CommandSpec] = []
    for spec in commands:
        if spec.command in seen:
            continue
        seen.add(spec.command)
        deduped.append(spec)
    return deduped


def _detect_js_runner(root: pathlib.Path) -> str:
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    if (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        return "bun"
    return "npm"


def classify_command_action(command: str) -> str:
    raw_text = str(command or "")
    text = raw_text.lower()
    unquoted = _unquoted_text(raw_text).lower()
    try:
        tokens = [token.lower() for token in shlex.split(unquoted, posix=True)]
    except ValueError:
        tokens = unquoted.split()

    def has_sequence(*parts: str) -> bool:
        if not parts:
            return False
        width = len(parts)
        return any(tokens[idx:idx + width] == list(parts) for idx in range(0, max(len(tokens) - width + 1, 0)))

    first = tokens[0] if tokens else ""
    if has_shell_control_operator(raw_text):
        return "run_network"
    if (
        has_sequence("pip", "install")
        or has_sequence("uv", "pip", "install")
        or has_sequence("python", "-m", "pip", "install")
        or has_sequence("python3", "-m", "pip", "install")
        or has_sequence("py", "-m", "pip", "install")
        or has_sequence("pipx", "install")
        or has_sequence("npm", "install")
        or has_sequence("pnpm", "install")
        or has_sequence("yarn", "install")
        or has_sequence("bun", "install")
        or has_sequence("cargo", "install")
        or has_sequence("poetry", "add")
        or has_sequence("uv", "add")
    ):
        return "run_install"
    if first in {"rm", "del", "erase", "rmdir", "unlink"} or has_sequence("remove-item") or "shutil.rmtree" in text:
        return "delete"
    if has_sequence("git", "commit"):
        return "git_commit"
    if has_sequence("git", "push"):
        return "git_push"
    if has_sequence("gh", "pr", "create") or has_sequence("hub", "pull-request") or has_sequence("git", "request-pull"):
        return "git_pr"
    if first in {"curl", "wget"} or "http://" in unquoted or "https://" in unquoted:
        return "run_network"
    if any(token in text for token in ("urlopen(", "requests.get", "requests.post", "httpx.", "aiohttp.")):
        return "run_network"
    if first == "docker":
        return "run_network"
    if any(token in unquoted for token in (" run dev", " start", "serve", "uvicorn", "flask run")):
        return "start_server"
    return "run_safe_command"


def run_workspace_command(
    workspace_path: str,
    command: str,
    approval_mode: ApprovalMode,
    *,
    timeout: int = 120,
    workspace_id: str = "",
    thread_id: str = "",
) -> CommandResult:
    root = pathlib.Path(workspace_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Workspace folder does not exist: {workspace_path}")
    action = classify_command_action(command)
    workspace = None
    if workspace_id:
        try:
            from row_bot.developer.storage import get_workspace
            workspace = get_workspace(workspace_id)
        except Exception:
            workspace = None
        if workspace is None:
            return _unresolved_workspace_result(
                command=command,
                root=root,
                workspace_id=workspace_id,
            )
    decision = decide_action(approval_mode, action)  # type: ignore[arg-type]
    decision = _apply_docker_network_policy(workspace, action, decision)
    if decision.decision != "allow":
        return CommandResult(
            command=command,
            cwd=str(root),
            returncode=None,
            stderr=decision.reason,
            decision=decision,
        )
    if workspace is not None and workspace.execution_mode == "docker":
        from row_bot.developer.sandbox_runtime import run_docker_sandbox_command

        outcome = run_docker_sandbox_command(
            workspace,
            command,
            thread_id=thread_id,
            timeout=timeout,
        )
        return CommandResult(
            command=command,
            cwd=outcome.cwd,
            returncode=outcome.returncode,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            decision=decision,
            changed_files=outcome.changed_files,
            execution_mode="docker",
            sandbox_backend=outcome.sandbox_backend,
            sandbox_pending_change_id=outcome.pending_change_id,
        )
    args = split_command(command)
    completed = run_cancellable_subprocess(
        args,
        cwd=str(root),
        timeout=timeout,
        text=True,
    )
    if completed.timed_out:
        return CommandResult(
            command=command,
            cwd=str(root),
            returncode=124,
            stdout=completed.stdout[-20_000:],
            stderr=f"Command timed out after {timeout}s.",
            decision=decision,
        )
    if completed.cancelled:
        return CommandResult(
            command=command,
            cwd=str(root),
            returncode=130,
            stdout=completed.stdout[-20_000:],
            stderr=_append_command_note(completed.stderr, "Command stopped by user.")[-20_000:],
            decision=decision,
        )
    return CommandResult(
        command=command,
        cwd=str(root),
        returncode=completed.returncode,
        stdout=completed.stdout[-20_000:],
        stderr=completed.stderr[-20_000:],
        decision=decision,
    )


def run_workspace_shell_command(
    workspace_path: str,
    command: str,
    approval_mode: ApprovalMode,
    *,
    workspace_id: str,
    thread_id: str,
    timeout: int = 120,
    confirmed: bool = False,
) -> CommandResult:
    """Run a shell command in the workspace and ledger file side effects.

    This is the Developer-native escape hatch for repo-specific commands.
    Action-capable commands follow the shared thread approval mode.
    """
    root = pathlib.Path(workspace_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Workspace folder does not exist: {workspace_path}")

    action = classify_command_action(command)
    workspace = None
    if workspace_id:
        try:
            from row_bot.developer.storage import get_workspace
            workspace = get_workspace(workspace_id)
        except Exception:
            workspace = None
    if workspace_id and workspace is None:
        return _unresolved_workspace_result(
            command=command,
            root=root,
            workspace_id=workspace_id,
        )
    decision = decide_action(approval_mode, action)  # type: ignore[arg-type]
    decision = _apply_docker_network_policy(workspace, action, decision)
    if decision.requires_approval and confirmed:
        decision = ApprovalDecision("allow", "User explicitly approved this shell command.")
    decision = _apply_docker_network_policy(workspace, action, decision)
    if not decision.allowed:
        return CommandResult(
            command=command,
            cwd=str(root),
            returncode=None,
            stderr=decision.reason,
            decision=decision,
        )

    if workspace is not None and workspace.execution_mode == "docker":
        from row_bot.developer.sandbox_runtime import run_docker_sandbox_command

        outcome = run_docker_sandbox_command(
            workspace,
            command,
            thread_id=thread_id,
            timeout=timeout,
        )
        return CommandResult(
            command=command,
            cwd=outcome.cwd,
            returncode=outcome.returncode,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            decision=decision,
            changed_files=outcome.changed_files,
            execution_mode="docker",
            sandbox_backend=outcome.sandbox_backend,
            sandbox_pending_change_id=outcome.pending_change_id,
        )

    before = _snapshot_changed_files(root)
    completed = run_cancellable_subprocess(
        _platform_shell_args(command),
        cwd=str(root),
        timeout=timeout,
        text=True,
    )
    if completed.timed_out:
        return CommandResult(
            command=command,
            cwd=str(root),
            returncode=124,
            stdout=completed.stdout[-20_000:],
            stderr=f"Command timed out after {timeout}s.",
            decision=decision,
        )
    if completed.cancelled:
        return CommandResult(
            command=command,
            cwd=str(root),
            returncode=130,
            stdout=completed.stdout[-20_000:],
            stderr=_append_command_note(completed.stderr, "Command stopped by user.")[-20_000:],
            decision=decision,
        )
    after = _snapshot_changed_files(root)
    changed_paths = sorted(set(before) | set(after))
    file_changes: list[FileChange] = []
    for rel_path in changed_paths:
        before_text = before.get(rel_path)
        if rel_path not in before:
            before_text = _head_text(root, rel_path)
        after_text = after.get(rel_path)
        if change_ledger.text_hash(before_text) == change_ledger.text_hash(after_text):
            continue
        if before_text is None and after_text is not None:
            action_name = "create"
        elif before_text is not None and after_text is None:
            action_name = "delete"
        else:
            action_name = "update"
        file_changes.append(
            FileChange(
                path=rel_path,
                action=action_name,
                before_hash=change_ledger.text_hash(before_text),
                after_hash=change_ledger.text_hash(after_text),
                before_text=before_text,
            )
        )
    if file_changes:
        change_ledger.record_change_set(
            workspace_id=workspace_id,
            thread_id=thread_id,
            summary=f"Shell command: {command[:120]}",
            files=file_changes,
        )
    return CommandResult(
        command=command,
        cwd=str(root),
        returncode=completed.returncode,
        stdout=completed.stdout[-20_000:],
        stderr=completed.stderr[-20_000:],
        decision=decision,
        changed_files=[item.path for item in file_changes],
    )


def _apply_docker_network_policy(workspace, action: str, decision: ApprovalDecision) -> ApprovalDecision:
    if (
        workspace is not None
        and getattr(workspace, "execution_mode", "") == "docker"
        and action in {"run_network", "run_install"}
        and getattr(workspace, "sandbox_network", "off") == "off"
    ):
        label = "package install" if action == "run_install" else "network command"
        return ApprovalDecision(
            "block",
            f"Docker Sandbox network is Off. Switch the sandbox network policy to Ask or On before running this {label}.",
        )
    return decision


def _platform_shell_args(command: str) -> list[str]:
    if os.name == "nt":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command]
    return ["/bin/sh", "-lc", command]


def _append_command_note(output: str, note: str) -> str:
    text = str(output or "").rstrip()
    return f"{text}\n{note}" if text else note


def _snapshot_changed_files(root: pathlib.Path) -> dict[str, str | None]:
    paths = _git_status_paths(root)
    snapshot: dict[str, str | None] = {}
    for rel_path in paths:
        target = (root / rel_path).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            continue
        if not target.exists() or not target.is_file():
            snapshot[rel_path] = None
            continue
        if not _looks_text(target):
            snapshot[rel_path] = None
            continue
        try:
            snapshot[rel_path] = target.read_text(encoding="utf-8", errors="replace")
        except Exception:
            snapshot[rel_path] = None
    return snapshot


def _git_status_paths(root: pathlib.Path) -> set[str]:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return set()
    paths: set[str] = set()
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        rel = line[3:].strip()
        if " -> " in rel:
            old_path, new_path = rel.split(" -> ", 1)
            paths.add(old_path.strip())
            rel = new_path.strip()
        paths.add(rel)
    return paths


def _head_text(root: pathlib.Path, rel_path: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "show", f"HEAD:{rel_path}"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _looks_text(path: pathlib.Path, *, sniff_bytes: int = 4096) -> bool:
    try:
        data = path.read_bytes()[:sniff_bytes]
    except Exception:
        return False
    return b"\0" not in data


def start_workspace_process(
    workspace_path: str,
    command: str,
    approval_mode: ApprovalMode,
    *,
    workspace_id: str = "",
    thread_id: str = "",
) -> CommandResult:
    """Start a tracked long-running process for later cleanup.

    This is intentionally small for the first Developer phase. The UI does not
    expose arbitrary server starts yet, but this gives future preview/server
    flows one cleanup path instead of ad hoc subprocess management.
    """
    root = pathlib.Path(workspace_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Workspace folder does not exist: {workspace_path}")
    action = classify_command_action(command)
    if action == "run_safe_command":
        action = "start_server"
    decision = decide_action(approval_mode, action)  # type: ignore[arg-type]
    if decision.decision != "allow":
        return CommandResult(command=command, cwd=str(root), returncode=None, stderr=decision.reason, decision=decision)

    if workspace_id:
        try:
            from row_bot.developer.storage import get_workspace
            workspace = get_workspace(workspace_id)
        except Exception:
            workspace = None
        if workspace is None:
            return _unresolved_workspace_result(
                command=command,
                root=root,
                workspace_id=workspace_id,
            )
        if workspace is not None and workspace.execution_mode == "docker":
            from row_bot.developer.sandbox_runtime import start_docker_sandbox_process

            outcome = start_docker_sandbox_process(workspace, command, thread_id=thread_id)
            return CommandResult(
                command=command,
                cwd=outcome.cwd,
                returncode=outcome.returncode,
                stdout=outcome.stdout,
                stderr=outcome.stderr,
                decision=decision,
                execution_mode="docker",
                sandbox_backend=outcome.sandbox_backend,
            )

    state = launch_tracked_process(root, split_command(command), command)
    return CommandResult(command=command, cwd=str(root), returncode=0 if not state.code else None,
        stdout=f"Started PID {state.process.pid}" if not state.code else "", decision=decision,
        process_id=state.process_id, code=state.code)


def stop_workspace_processes(workspace_path: str, *, workspace_id: str = "") -> int:
    if workspace_id:
        try:
            from row_bot.developer.storage import get_workspace
            workspace = get_workspace(workspace_id)
        except Exception:
            workspace = None
        if workspace is not None and workspace.execution_mode == "docker":
            from row_bot.developer.sandbox_runtime import stop_docker_sandbox_processes

            return stop_docker_sandbox_processes(workspace)
    root = str(pathlib.Path(workspace_path).expanduser().resolve())
    with _PROCESS_LOCK:
        processes = list(_ACTIVE_PROCESSES.get(root, []))
    stopped = 0
    for proc in processes:
        state = getattr(proc, "_row_bot_state", None)
        if isinstance(state, TrackedProcess):
            was_active = not state.quiesced
            stop_tracked_process(state)
            state.done.wait(timeout=10)
            if not _retirable_tracked_process(proc):
                continue
            stopped += int(was_active)
            with _PROCESS_LOCK:
                if proc in _ACTIVE_PROCESSES.get(root, []):
                    _ACTIVE_PROCESSES[root].remove(proc)
            continue
        if proc.poll() is not None:
            continue
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        stopped += 1
        with _PROCESS_LOCK:
            if proc in _ACTIVE_PROCESSES.get(root, []):
                _ACTIVE_PROCESSES[root].remove(proc)
    return stopped
