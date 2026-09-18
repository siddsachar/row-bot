"""Trusted stdlib process bootstrap; host assigns containment before admission.

Launched with -I -S -B. Only an explicit bounded stdin request starts a command.
Program output is data framed by this owner, never a callable host instruction.
"""
from __future__ import annotations

import codecs
import json
import hashlib
import hmac
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid

_WRITE_LOCK = threading.Lock()
_SEQUENCE = 0


def _emit(event: str, **fields) -> None:
    global _SEQUENCE
    with _WRITE_LOCK:
        _SEQUENCE += 1
        value = {"version": 1, "sequence": _SEQUENCE, "event": event, **fields}
        sys.stdout.buffer.write(json.dumps(value, ensure_ascii=True, allow_nan=False).encode("ascii") + b"\n")
        sys.stdout.buffer.flush()


def _pump(stream, channel: str) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    try:
        while True:
            block = stream.read1(1024)
            if not block:
                break
            text = decoder.decode(block)
            if text:
                _emit("output", channel=channel, text=text)
        tail = decoder.decode(b"", final=True)
        if tail:
            _emit("output", channel=channel, text=tail)
    finally:
        stream.close()


def _owner_directory(owner_id: str, *, create: bool = False) -> Path:
    if str(uuid.UUID(owner_id)) != owner_id:
        raise ValueError("invalid owner")
    parent = Path("/tmp/.row-bot-process-owners")
    if create:
        parent.mkdir(mode=0o700, exist_ok=True)
    for path in (parent, parent / owner_id):
        if create and path != parent:
            path.mkdir(mode=0o700)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("invalid owner directory")
    return parent / owner_id


def _process_identity(pid: int) -> tuple[int, str]:
    if type(pid) is not int or not 0 < pid < 2**31:
        raise ValueError("invalid process identity")
    with open(f"/proc/{pid}/stat", "rb") as stream:
        value = stream.read(4097)
    if len(value) > 4096:
        raise ValueError("process identity unavailable")
    fields = value.rsplit(b") ", 1)[1].split()
    return int(fields[1]), fields[19].decode("ascii")


def _signed_receipt(payload: dict, key: bytes) -> dict:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return {"payload": payload, "signature": hmac.new(key, canonical, hashlib.sha256).hexdigest()}


def verify_receipt(receipt: dict, owner_id: str, container_id: str, key: bytes) -> dict:
    """Validate the same bounded completion proof for local and container owners."""
    if type(receipt) is not dict or set(receipt) != {"payload", "signature"}:
        raise ValueError("process_receipt_invalid")
    payload, signature = receipt["payload"], receipt["signature"]
    fields = {"version", "owner_id", "container_id", "supervisor_pid", "start_time", "state",
              "quiesced", "exit_code", "output_incomplete"}
    if (type(payload) is not dict or set(payload) != fields or type(signature) is not str or len(signature) != 64
            or type(payload["version"]) is not int or payload["version"] != 1
            or payload["owner_id"] != owner_id or payload["container_id"] != container_id
            or type(payload["supervisor_pid"]) is not int or not 0 < payload["supervisor_pid"] < 2**31
            or type(payload["start_time"]) is not str or not payload["start_time"].isascii()
            or not payload["start_time"].isdigit() or len(payload["start_time"]) > 24
            or type(payload["state"]) is not str or payload["state"] not in {"running", "complete"}
            or type(payload["quiesced"]) is not bool or type(payload["output_incomplete"]) is not bool
            or (payload["exit_code"] is not None and type(payload["exit_code"]) is not int)
            or (payload["quiesced"] and (payload["state"] != "complete" or payload["output_incomplete"]))):
        raise ValueError("process_receipt_invalid")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    try:
        valid = hmac.compare_digest(signature, hmac.new(key, canonical, hashlib.sha256).hexdigest())
    except TypeError:
        valid = False
    if not valid:
        raise ValueError("process_receipt_invalid")
    return receipt


def _save_receipt(directory: int, receipt: dict) -> None:
    path = uuid.uuid4().hex + ".tmp"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(fd, "wb") as stream:
        stream.write(json.dumps(receipt, ensure_ascii=True, separators=(",", ":")).encode("ascii"))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(path, "receipt.json", src_dir_fd=directory, dst_dir_fd=directory)


def _read_receipt(directory: Path) -> dict:
    fd = os.open(directory / "receipt.json", os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_size > 8192:
            raise ValueError("receipt unavailable")
        data = stream.read(8193)
    if len(data) > 8192:
        raise ValueError("receipt unavailable")
    return json.loads(data)


def _owned_children() -> list[int]:
    with open(f"/proc/self/task/{os.getpid()}/children", "rb") as stream:
        data = stream.read(65537)
    parts = data.split()
    if len(data) > 65536 or len(parts) > 4096:
        raise ValueError("child limit")
    return [int(part) for part in parts]


def _signal_owned_child(pid: int, value: int) -> None:
    try:
        descriptor = os.pidfd_open(pid)
    except ProcessLookupError:
        return
    try:
        try:
            parent, _ = _process_identity(pid)
        except FileNotFoundError:
            return
        if parent != os.getpid():
            return
        try:
            signal.pidfd_send_signal(descriptor, value)
        except ProcessLookupError:
            pass
    finally:
        os.close(descriptor)


def _cleanup_children() -> bool:
    """Subreaper adoption plus pidfds include detached grandchildren."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            children = _owned_children()
            for pid in children:
                _signal_owned_child(pid, signal.SIGKILL)
            for _ in range(4096):
                try:
                    reaped, _ = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    reaped = 0
                if not reaped:
                    break
            if not _owned_children():
                return True
        except (OSError, ValueError, IndexError):
            return False
        threading.Event().wait(0.01)
    return False


def _remote_process(argv: list[str], payload: dict) -> int:
    # These Linux primitives are mandatory, not an optional best-effort fallback.
    if (not sys.platform.startswith("linux") or not hasattr(os, "pidfd_open")
            or not hasattr(signal, "pidfd_send_signal")):
        _emit("failed", code="process_containment_unavailable")
        return 126
    import ctypes
    library = ctypes.CDLL(None, use_errno=True)
    try:
        if library.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise OSError
        probe = os.pidfd_open(os.getpid())
        try:
            signal.pidfd_send_signal(probe, 0)
        finally:
            os.close(probe)
    except OSError:
        _emit("failed", code="process_containment_unavailable")
        return 126
    try:
        owner_id, container_id = payload["owner_id"], payload["container_id"]
        key = bytes.fromhex(payload["key"])
        if len(key) != 32 or type(container_id) is not str or len(container_id) != 64 or any(c not in "0123456789abcdef" for c in container_id):
            raise ValueError("invalid owner")
        directory = _owner_directory(owner_id, create=True)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        _, start_time = _process_identity(os.getpid())
        record = {"version": 1, "owner_id": owner_id, "container_id": container_id,
            "supervisor_pid": os.getpid(), "start_time": start_time, "state": "running",
            "quiesced": False, "exit_code": None, "output_incomplete": False}
        running = _signed_receipt(record, key)
        _save_receipt(directory_fd, running)
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        _emit("failed", code="process_owner_unavailable")
        return 126
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    def control():
        # EOF requests cleanup; it is never itself a completion proof.
        # A daemon must not hold a buffered-reader lock at Python shutdown.
        # After admission the host sends only cancellation or EOF here.
        os.read(sys.stdin.fileno(), 16)
        stopped.set()
    threading.Thread(target=control, daemon=True).start()
    pumps = []
    process = None
    code = 127
    try:
        if not stopped.is_set():
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, shell=False, close_fds=True, start_new_session=True)
            _emit("started", pid=process.pid, receipt=running)
            pumps = [threading.Thread(target=_pump, args=(stream, channel), daemon=True)
                for stream, channel in ((process.stdout, "stdout"), (process.stderr, "stderr"))]
            for pump in pumps:
                pump.start()
            while process.poll() is None and not stopped.wait(0.05):
                pass
            code = 130 if stopped.is_set() else int(process.returncode or 0)
    except (OSError, ValueError):
        code = 127
    quiesced = _cleanup_children()
    for pump in pumps:
        pump.join(timeout=1)
    incomplete = any(pump.is_alive() for pump in pumps)
    receipt = _signed_receipt({**record, "state": "complete", "quiesced": quiesced and not incomplete,
                              "exit_code": code, "output_incomplete": incomplete}, key)
    try:
        _save_receipt(directory_fd, receipt)
    except OSError:
        # Live authenticated proof still works; lost-transport recovery fails
        # closed if its retained file could not be durably published.
        pass
    if process is None:
        _emit("failed", code="process_start_failed", receipt=receipt)
    else:
        _emit("exited", exit_code=code, output_incomplete=incomplete, receipt=receipt)
    os.close(directory_fd)
    return 0 if quiesced and not incomplete else 125


def _recover_owner(payload: dict) -> int:
    try:
        if sys.platform != "linux" or type(payload) is not dict or payload.get("action") not in {"read", "stop"}:
            raise ValueError("recovery unavailable")
        directory = _owner_directory(payload["owner_id"])
        receipt = _read_receipt(directory)
        if payload["action"] == "stop" and receipt.get("payload", {}).get("state") != "complete":
            pid, expected = payload["supervisor_pid"], payload["start_time"]
            descriptor = os.pidfd_open(pid)
            try:
                if _process_identity(pid)[1] != expected:
                    raise ValueError("owner identity changed")
                signal.pidfd_send_signal(descriptor, signal.SIGTERM)
            finally:
                os.close(descriptor)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                receipt = _read_receipt(directory)
                if receipt.get("payload", {}).get("state") == "complete":
                    break
                threading.Event().wait(0.025)
        sys.stdout.buffer.write(json.dumps({"receipt": receipt}, separators=(",", ":")).encode("ascii") + b"\n")
        sys.stdout.buffer.flush()
        return 0
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        sys.stdout.buffer.write(b'{"error":"process_recovery_unavailable"}\n')
        sys.stdout.buffer.flush()
        return 125


def main() -> int:
    request = sys.stdin.buffer.readline(32769)
    if len(request) > 32768 or not request.endswith(b"\n"):
        return 126
    try:
        payload = json.loads(request)
        if "--recover-owner" in sys.argv:
            return _recover_owner(payload)
        argv = payload["argv"]
        if (type(payload) is not dict or set(payload) not in ({"argv"}, {"argv", "owner_id", "container_id", "key"}) or type(argv) is not list
                or not 1 <= len(argv) <= 256 or any(type(arg) is not str or "\0" in arg for arg in argv)
                or sum(len(arg.encode("utf-8")) for arg in argv) > 16384):
            return 126
    except (ValueError, KeyError, TypeError):
        return 126
    if "owner_id" in payload:
        return _remote_process(argv, payload)
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, shell=False, close_fds=True)
    except (OSError, ValueError):
        _emit("failed", code="process_start_failed")
        return 127
    _emit("started", pid=process.pid)
    pumps = [threading.Thread(target=_pump, args=(stream, channel), daemon=True)
             for stream, channel in ((process.stdout, "stdout"), (process.stderr, "stderr"))]
    for pump in pumps:
        pump.start()
    code = process.wait()
    # A descendant can keep these handles open after its parent exits. The host
    # terminates the entire owned job/group after this bounded drain period.
    for pump in pumps:
        pump.join(timeout=1)
    _emit("exited", exit_code=code, output_incomplete=any(pump.is_alive() for pump in pumps))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
