"""Finite duplex JSON transport for a single owned plugin process.

This stdlib-only module has no application, storage, provider or plugin imports.
Each side owns monotonically increasing request IDs, bounded pending/admission
slots and one bounded writer queue. It never dispatches an object or method itself.
"""
from __future__ import annotations

from collections.abc import Callable
import base64
import json
import io
import queue
import re
import threading
from typing import Any
from uuid import uuid4

MAX_FRAME = 4 * 1024 * 1024
MAX_MESSAGE = 72 * 1024 * 1024  # Existing 50 MiB attachments plus base64/JSON.
MAX_BUFFERED = 96 * 1024 * 1024
MAX_PENDING = 16
MAX_INBOUND = 8
_SAFE_ERRORS = frozenset({
    "worker_protocol_invalid", "worker_payload_invalid", "worker_operation_failed",
    "worker_busy", "worker_closed", "worker_timeout", "worker_callback_denied",
    "worker_revoked", "worker_channels_unavailable", "worker_webhooks_unavailable",
    "worker_contribution_limit", "worker_duplicate_tool", "worker_arguments_invalid",
    "worker_method_unavailable", "worker_source_changed", "worker_result_invalid",
})


class ProtocolError(RuntimeError):
    pass


def pack(value: Any) -> Any:
    """Encode only JSON values and explicit binary content, never Python objects."""
    if type(value) is bytes:
        if len(value) > 50 * 1024 * 1024:
            raise ProtocolError("worker_payload_invalid")
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if type(value) in (list, tuple):
        return [pack(item) for item in value]
    if type(value) is dict:
        if "$bytes" in value:
            raise ProtocolError("worker_payload_invalid")
        return {key: pack(item) for key, item in value.items()}
    return value


def unpack(value: Any) -> Any:
    if type(value) is dict:
        if "$bytes" in value:
            if set(value) != {"$bytes"} or type(value["$bytes"]) is not str or len(value["$bytes"]) > 69905068:
                raise ProtocolError("worker_payload_invalid")
            try:
                result = base64.b64decode(value["$bytes"], validate=True)
            except ValueError:
                raise ProtocolError("worker_payload_invalid") from None
            if len(result) > 50 * 1024 * 1024:
                raise ProtocolError("worker_payload_invalid")
            return result
        return {key: unpack(item) for key, item in value.items()}
    if type(value) is list:
        return [unpack(item) for item in value]
    return value


def _finite(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> None:
    if budget is None:
        budget = [100000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 32:
        raise ProtocolError("worker_payload_invalid")
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if value != value or abs(value) == float("inf"):
            raise ProtocolError("worker_payload_invalid")
        return
    if type(value) is list:
        for item in value:
            _finite(item, depth=depth + 1, budget=budget)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _finite(item, depth=depth + 1, budget=budget)
        return
    raise ProtocolError("worker_payload_invalid")


def frame(value: dict, *, maximum: int = MAX_FRAME) -> bytes:
    try:
        _finite(value)
        raw = json.dumps(value, allow_nan=False, separators=(",", ":")).encode() + b"\n"
        if len(raw) > maximum:
            raise ValueError
        return raw
    except (ValueError, TypeError, RecursionError):
        raise ProtocolError("worker_payload_invalid") from None


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ProtocolError("worker_protocol_invalid")
        value[key] = item
    return value


def read_frame(stream, *, with_size: bool = False):
    raw = stream.readline(MAX_FRAME + 1)
    if not raw or len(raw) > MAX_FRAME or not raw.endswith(b"\n"):
        raise ProtocolError("worker_protocol_invalid")
    try:
        value = json.loads(raw, object_pairs_hook=_object)
        _finite(value)
    except (ValueError, TypeError, RecursionError):
        raise ProtocolError("worker_protocol_invalid") from None
    if type(value) is not dict or (type(value.get("v")) is not int or value["v"] != 1):
        raise ProtocolError("worker_protocol_invalid")
    return (value, len(raw)) if with_size else value


def _read_message(stream):
    value, size = read_frame(stream, with_size=True)
    if value.get("kind") != "part":
        return value, size
    length = value.get("size")
    transfer = value.get("transfer")
    if (type(length) is not int or not MAX_FRAME < length <= MAX_MESSAGE
            or type(transfer) is not str or not re.fullmatch(r"[a-f0-9]{32}", transfer)):
        raise ProtocolError("worker_protocol_invalid")
    content = bytearray()
    index = 0
    while True:
        if (set(value) != {"v", "kind", "transfer", "size", "index", "data"}
                or value["kind"] != "part" or value["transfer"] != transfer
                or value["size"] != length or type(value["index"]) is not int
                or value["index"] != index or type(value["data"]) is not str):
            raise ProtocolError("worker_protocol_invalid")
        try:
            block = base64.b64decode(value["data"], validate=True)
        except ValueError:
            raise ProtocolError("worker_protocol_invalid") from None
        if not block or len(block) > 2 * 1024 * 1024 or len(content) + len(block) > length:
            raise ProtocolError("worker_protocol_invalid")
        content.extend(block)
        if len(content) == length:
            break
        index += 1
        value = read_frame(stream)
    try:
        value = json.loads(content, object_pairs_hook=_object)
        _finite(value)
    except (ValueError, TypeError, RecursionError):
        raise ProtocolError("worker_protocol_invalid") from None
    if type(value) is not dict or (type(value.get("v")) is not int or value["v"] != 1) or value.get("kind") == "part":
        raise ProtocolError("worker_protocol_invalid")
    return value, length


class _Pending:
    def __init__(self):
        self.event = threading.Event()
        self.value = None
        self.error = None


class Peer:
    def __init__(self, reader, writer, *, role: str, handler: Callable[[str, dict], Any]):
        if role not in {"host", "worker"}:
            raise ValueError("Invalid peer role")
        self.reader = io.BufferedReader(reader, buffer_size=65536) if isinstance(reader, io.RawIOBase) else reader
        self.writer = writer
        self.role, self.remote = role, "worker" if role == "host" else "host"
        self.handler = handler
        self.closed = threading.Event()
        self._lock = threading.RLock()
        self._pending = {}
        self._sequence = self._received = 0
        self._queued_bytes = self._handling_bytes = 0
        self._writes = queue.Queue(maxsize=32)
        self._inbound = threading.BoundedSemaphore(MAX_INBOUND)
        self._reader_thread = threading.Thread(target=self._read, name="plugin-rpc-reader", daemon=True)
        self._writer_thread = threading.Thread(target=self._write, name="plugin-rpc-writer", daemon=True)
        self._writer_thread.start()
        self._reader_thread.start()

    def _send(self, value: dict) -> None:
        if self.closed.is_set():
            raise ProtocolError("worker_closed")
        try:
            with self._lock:
                if self.closed.is_set():
                    raise ProtocolError("worker_closed")
                raw = frame(value, maximum=MAX_MESSAGE)
                if self._queued_bytes + len(raw) > MAX_BUFFERED:
                    raise queue.Full
                self._writes.put_nowait(raw)
                self._queued_bytes += len(raw)
        except queue.Full:
            self.close("worker_busy")
            raise ProtocolError("worker_busy") from None

    def request(self, method: str, args: dict, *, timeout: float | None) -> Any:
        # Validate before reserving an ID; rejected local payloads don't create
        # sequence gaps or close a healthy peer.
        frame({"v": 1, "kind": "request", "id": f"{self.role}:1", "method": method, "args": args}, maximum=MAX_MESSAGE)
        with self._lock:
            if self.closed.is_set():
                raise ProtocolError("worker_closed")
            if len(self._pending) >= MAX_PENDING:
                raise ProtocolError("worker_busy")
            self._sequence += 1
            identity = f"{self.role}:{self._sequence}"
            pending = _Pending()
            self._pending[identity] = pending
            try:
                self._send({"v": 1, "kind": "request", "id": identity, "method": method, "args": args})
            except BaseException:
                self._pending.pop(identity, None)
                raise
        if not pending.event.wait(timeout):
            self.close("worker_timeout")
            raise ProtocolError("worker_timeout")
        if pending.error is not None:
            raise ProtocolError(pending.error)
        return pending.value

    def _write(self):
        try:
            while not self.closed.is_set():
                raw = self._writes.get()
                if raw is None:
                    break
                def write(block):
                    pending = memoryview(block)
                    while pending:
                        count = self.writer.write(pending)
                        if not count:
                            raise ProtocolError("worker_protocol_invalid")
                        pending = pending[count:]
                    self.writer.flush()
                if len(raw) <= MAX_FRAME:
                    write(raw)
                else:
                    transfer = uuid4().hex
                    for index, offset in enumerate(range(0, len(raw), 2 * 1024 * 1024)):
                        data = base64.b64encode(raw[offset:offset + 2 * 1024 * 1024]).decode("ascii")
                        write(frame({"v": 1, "kind": "part", "transfer": transfer,
                                     "size": len(raw), "index": index, "data": data}))
                with self._lock:
                    self._queued_bytes -= len(raw)
        except BaseException:
            self.close("worker_protocol_invalid")

    def _read(self):
        try:
            while not self.closed.is_set():
                value, size = _read_message(self.reader)
                identity = value.get("id")
                if type(identity) is not str or len(identity) > 32:
                    raise ProtocolError("worker_protocol_invalid")
                if value.get("kind") == "result":
                    if type(value.get("ok")) is not bool:
                        raise ProtocolError("worker_protocol_invalid")
                    field = "result" if value["ok"] else "error"
                    if set(value) != {"v", "kind", "id", "ok", field}:
                        raise ProtocolError("worker_protocol_invalid")
                    if not value["ok"] and type(value["error"]) is not str:
                        raise ProtocolError("worker_protocol_invalid")
                    with self._lock:
                        pending = self._pending.pop(identity, None)
                    if pending is None:
                        raise ProtocolError("worker_protocol_invalid")
                    if value["ok"]:
                        pending.value = value.get("result")
                    else:
                        code = value.get("error")
                        pending.error = code if code in _SAFE_ERRORS else "worker_operation_failed"
                    pending.event.set()
                    continue
                if set(value) != {"v", "kind", "id", "method", "args"} or value["kind"] != "request":
                    raise ProtocolError("worker_protocol_invalid")
                self._received += 1
                if identity != f"{self.remote}:{self._received}":
                    raise ProtocolError("worker_protocol_invalid")
                method = value.get("method")
                if type(method) is not str or not re.fullmatch(r"[a-z_]{1,64}", method) or type(value.get("args")) is not dict:
                    raise ProtocolError("worker_protocol_invalid")
                with self._lock:
                    admitted = self._handling_bytes + size <= MAX_BUFFERED and self._inbound.acquire(blocking=False)
                    if admitted:
                        self._handling_bytes += size
                if not admitted:
                    self._send({"v": 1, "kind": "result", "id": identity, "ok": False, "error": "worker_busy"})
                    continue
                threading.Thread(target=self._handle, args=(identity, method, value["args"], size),
                                 name="plugin-rpc-handler", daemon=True).start()
        except BaseException:
            self.close("worker_protocol_invalid")

    def _handle(self, identity, method, args, size):
        try:
            try:
                result = self.handler(method, args)
                value = {"ok": True, "result": result}
                frame({"v": 1, "kind": "result", "id": identity, **value}, maximum=MAX_MESSAGE)
            except BaseException as exc:
                code = str(exc)
                value = {"ok": False, "error": code if code in _SAFE_ERRORS else "worker_operation_failed"}
            self._send({"v": 1, "kind": "result", "id": identity, **value})
        except BaseException:
            self.close("worker_protocol_invalid")
        finally:
            with self._lock:
                self._handling_bytes -= size
            self._inbound.release()

    def close(self, reason: str = "worker_closed") -> None:
        self.closed.set()
        with self._lock:
            values = list(self._pending.values())
            self._pending.clear()
        for pending in values:
            pending.error = reason
            pending.event.set()
        try:
            while True:
                self._writes.get_nowait()
        except queue.Empty:
            pass
        try:
            self._writes.put_nowait(None)
        except queue.Full:
            pass
