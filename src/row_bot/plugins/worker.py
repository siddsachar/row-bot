"""Owned plugin process, bounded protocol and existing-registry tool proxies.

No interpreter fallback or preparation occurs here. This process boundary removes
plugin execution from the host; it is not an OS filesystem or network sandbox.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
from typing import Any
from uuid import uuid4

from row_bot.plugins.api import PluginAPI, PluginTool
from row_bot.plugins.worker_protocol import Peer, ProtocolError, unpack
from row_bot.plugins.worker_ownership import WindowsJob as _WindowsJob

MAX_FRAME = 4 * 1024 * 1024
CALL_TIMEOUT = 60.0


class WorkerError(RuntimeError):
    """Only fixed privacy-safe codes cross the host runtime boundary."""


@dataclass(frozen=True)
class PreparedWorker:
    plugin_id: str
    plugin_dir: Path
    environment: Path
    interpreter: Path
    operation_id: str
    source_revision: str
    environment_revision: str


def prepared_worker(plugin_id: str, plugin_dir: Path) -> PreparedWorker:
    """Validate the existing preparation owner's current ready generation."""
    from row_bot.plugins import installer, sandbox, state

    try:
        records = state.get_plugin_environment_state(plugin_id)
        operation_id = records.get("active_operation_id")
        receipt = records.get("operations", {}).get(operation_id)
        if type(receipt) is not dict or receipt.get("stage") != "ready":
            raise WorkerError("worker_environment_not_ready")
        source = installer._source_for_preparation(plugin_id)
        if source != plugin_dir.resolve(strict=True):
            raise WorkerError("worker_source_changed")
        revision = installer.get_plugin_source_revision(plugin_id)
        if revision != receipt.get("plugin_revision"):
            raise WorkerError("worker_source_changed")
        environment = installer._generation_path(plugin_id, operation_id)
        interpreter, _ = sandbox._target(environment)
        environment_revision = installer._tree_revision(environment, source=False)
        if environment_revision != receipt.get("environment_revision"):
            raise WorkerError("worker_environment_changed")
        # Readiness/source admission must be the same cut at publication.
        if state.get_plugin_environment_state(plugin_id) != records:
            raise WorkerError("worker_environment_changed")
        return PreparedWorker(plugin_id, source, environment, interpreter, operation_id,
                              revision, environment_revision)
    except WorkerError:
        raise
    except Exception:
        raise WorkerError("worker_environment_not_ready") from None


def _frame(value: Any) -> bytes:
    try:
        raw = json.dumps(value, allow_nan=False, separators=(",", ":")).encode() + b"\n"
        if len(raw) > MAX_FRAME:
            raise ValueError
        return raw
    except (ValueError, TypeError, RecursionError):
        raise WorkerError("worker_payload_invalid") from None


def _worker_environment(directory: Path) -> dict[str, str]:
    allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "SYSTEMDRIVE"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment.update({"HOME": str(directory), "USERPROFILE": str(directory),
        "APPDATA": str(directory), "LOCALAPPDATA": str(directory), "TEMP": str(directory),
        "TMP": str(directory), "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_CONFIG_FILE": os.devnull, "NETRC": str(directory / ".disabled-netrc")})
    return environment


def _run_directory(plugin_id: str) -> Path:
    from row_bot.data_paths import get_row_bot_data_dir
    from row_bot.plugins.sandbox import _checked_path

    root = get_row_bot_data_dir(create=False).absolute()
    _checked_path(Path(root.anchor), root)
    directory = root
    for index, name in enumerate(("plugin_worker_runs", plugin_id, str(uuid4()))):
        directory /= name
        directory.mkdir(exist_ok=index < 2)
        _checked_path(root, directory)
    return directory


class PluginWorker:
    def __init__(self, prepared: PreparedWorker, api: PluginAPI):
        self.prepared = prepared
        self.api = api
        self._process: subprocess.Popen | None = None
        self._call_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._job: _WindowsJob | None = None
        self._closed = threading.Event()
        self._peer: Peer | None = None
        self._callbacks: set = set()
        self._channel_turns: dict = {}
        self._cancelled_turns = deque(maxlen=32)
        self._callbacks_drained = threading.Event()
        self._callbacks_drained.set()
        self._process_quiesced = threading.Event()
        self._process_quiesced.set()
        self.directory: Path | None = None

    def _check_prepared(self) -> None:
        """Revalidate the current receipt for every execution/callback boundary."""
        self.api._check_active()
        try:
            if prepared_worker(self.prepared.plugin_id, self.prepared.plugin_dir) != self.prepared:
                raise WorkerError("worker_environment_changed")
        except WorkerError:
            # Persistent listeners and queued callbacks lose authority together
            # with host dispatch. Retain the worker owner until actual cleanup.
            self.api._revoke()
            raise

    def start(self, timeout: float) -> dict:
        self.api._check_active()
        if prepared_worker(self.prepared.plugin_id, self.prepared.plugin_dir) != self.prepared:
            raise WorkerError("worker_environment_changed")
        directory = _run_directory(self.prepared.plugin_id)
        self.directory = directory
        entry = Path(__file__).with_name("worker_process.py")
        sdk = Path(__file__).with_name("api.py")
        channels = Path(__file__).parents[1] / "channels/base.py"
        arguments = [str(self.prepared.interpreter), "-I", "-S", "-B", str(entry),
                     str(self.prepared.environment), str(sdk), str(channels)]
        kwargs = {"cwd": str(directory), "env": _worker_environment(directory),
                  "stdin": subprocess.PIPE, "stdout": subprocess.PIPE,
                  "stderr": subprocess.DEVNULL, "shell": False, "bufsize": 0}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        with self._lifecycle_lock:
            if self._closed.is_set() or self._process is not None:
                raise WorkerError("worker_revoked")
            self._process = subprocess.Popen(arguments, **kwargs)
            self._process_quiesced.clear()
            if os.name == "nt":
                # The stdlib bootstrap is waiting for its first input; plugin
                # code cannot run before the non-breakaway job owns the process.
                self._job = _WindowsJob(self._process)
            self._peer = Peer(self._process.stdout, self._process.stdin, role="host", handler=self._handle_request)
            def observe_peer():
                self._peer.closed.wait()
                self.close()
            threading.Thread(target=observe_peer, name="plugin-peer-lifetime", daemon=True).start()
        try:
            result = self.call("register", {"plugin_id": self.prepared.plugin_id,
                                           "plugin_dir": str(self.prepared.plugin_dir)}, timeout=timeout)
            if prepared_worker(self.prepared.plugin_id, self.prepared.plugin_dir) != self.prepared:
                raise WorkerError("worker_environment_changed")
            return result
        except BaseException:
            self.close()
            raise

    def _callback(self, message: dict) -> Any:
        method, args = message.get("method"), message.get("args")
        if type(args) is not list or not args or type(args[0]) is not str or not 1 <= len(args[0]) <= 128:
            raise WorkerError("worker_callback_denied")
        arities = {"get_config": 2, "set_config": 2, "get_secret": 1, "set_secret": 2}
        if method not in arities or len(args) != arities[method]:
            raise WorkerError("worker_callback_denied")
        if method == "set_secret" and (type(args[1]) is not str or len(args[1]) > 65536):
            raise WorkerError("worker_callback_denied")
        # State's existing disable owner uses state -> registration ordering.
        # Recheck the epoch after waiting for it, then retain API admission
        # through the actual scoped read/write. A queued callback cannot mutate
        # configuration after disable/reload has revoked its worker.
        with self.api._state._state_lock, self.api._registration_lock:
            self._check_prepared()
            if self._closed.is_set():
                raise WorkerError("worker_revoked")
            return getattr(self.api, method)(*args)

    def _handle_request(self, method: str, value: dict) -> Any:
        if method != "api" or set(value) != {"method", "args", "kwargs"}:
            raise WorkerError("worker_callback_denied")
        if value["method"] in {"get_config", "set_config", "get_secret", "set_secret"}:
            if value["kwargs"]:
                raise WorkerError("worker_callback_denied")
            return self._callback(value)
        from row_bot.plugins.worker_channels import handle_callback
        from row_bot.cancellation import CancellationScope, use_cancellation_scope
        scope = CancellationScope()
        def cancel():
            scope.cancel("plugin_revoked")
        self.admit_callback(cancel)
        try:
            with use_cancellation_scope(scope):
                self._check_prepared()
                return handle_callback(self.api, value["method"], value["args"], value["kwargs"])
        finally:
            self.release_callback(cancel)

    def admit_callback(self, cancel) -> None:
        with self._lifecycle_lock:
            if self._closed.is_set():
                raise WorkerError("worker_revoked")
            self._callbacks.add(cancel)
            self._callbacks_drained.clear()

    def release_callback(self, cancel) -> None:
        with self._lifecycle_lock:
            self._callbacks.discard(cancel)
            if not self._callbacks:
                self._callbacks_drained.set()

    def admit_channel_turn(self, identity: str, cancel) -> None:
        with self._lifecycle_lock:
            if self._closed.is_set() or identity in self._cancelled_turns:
                raise WorkerError("worker_revoked")
            if identity in self._channel_turns or len(self._channel_turns) >= 4:
                raise WorkerError("worker_busy")
            self._channel_turns[identity] = cancel
            self._callbacks.add(cancel)
            self._callbacks_drained.clear()

    def release_channel_turn(self, identity: str, cancel) -> None:
        with self._lifecycle_lock:
            if self._channel_turns.get(identity) is cancel:
                self._channel_turns.pop(identity, None)
            self._callbacks.discard(cancel)
            if not self._callbacks:
                self._callbacks_drained.set()

    def cancel_channel_turn(self, identity: str) -> None:
        with self._lifecycle_lock:
            self._cancelled_turns.append(identity)
            cancel = self._channel_turns.get(identity)
        if cancel is not None:
            cancel()

    def has_pending_callbacks(self) -> bool:
        with self._lifecycle_lock:
            return bool(self._callbacks)

    def has_pending_work(self) -> bool:
        """Revocation/parent exit alone cannot acknowledge descendant cleanup."""
        with self._lifecycle_lock:
            return not self._process_quiesced.is_set() or bool(self._callbacks)

    def call(self, method: str, args: dict, *, timeout: float = CALL_TIMEOUT, control: bool = False) -> Any:
        from row_bot.cancellation import current_cancellation_scope

        self._check_prepared()
        if not control:
            _frame({"method": method, "args": args})
        acquired = not control and self._call_lock.acquire(blocking=False)
        if not control and not acquired:
            raise WorkerError("worker_busy")
        unregister = None
        scope = current_cancellation_scope()
        try:
            if self._closed.is_set() or self._process is None or self._process.poll() is not None or self._peer is None:
                raise WorkerError("worker_revoked")
            if scope is not None:
                unregister = scope.register(self.close, "plugin.worker.stop")
            if self._closed.is_set():
                raise WorkerError("worker_revoked")
            try:
                result = self._peer.request(method, args, timeout=timeout)
            except ProtocolError as exc:
                stopped = self.close()
                code = str(exc)
                if code == "worker_timeout" and not stopped:
                    code = "worker_stop_failed"
                if code == "worker_closed":
                    code = "worker_revoked"
                raise WorkerError(code) from None
            self.api._check_active()
            if self._closed.is_set():
                raise WorkerError("worker_revoked")
            return result
        finally:
            if unregister is not None:
                unregister()
            if acquired:
                self._call_lock.release()

    def close(self) -> bool:
        from row_bot.process_cancellation import request_process_stop

        with self._lifecycle_lock:
            self._closed.set()
            process = self._process
            callbacks = list(self._callbacks)
            if self._peer is not None:
                self._peer.close()
        for cancel in callbacks:
            try:
                cancel()
            except RuntimeError:
                pass  # A just-finished callback may already have closed its loop.
        if process is None:
            return not callbacks
        if not self._close_lock.acquire(timeout=3):
            return False
        try:
            tree_stopped = True
            if self._job is not None:
                tree_stopped = self._job.close()
            elif os.name != "nt":
                # start_new_session creates this owned process group. Kill the
                # group even when the direct child has already exited.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    tree_stopped = False
            elif process.poll() is None:
                request_process_stop(process)  # Failed pre-registration job setup.
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                return False
            for stream in (process.stdin, process.stdout):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
            with self._lifecycle_lock:
                if tree_stopped and process.poll() is not None:
                    self._process_quiesced.set()
                return self._process_quiesced.is_set() and not self._callbacks
        finally:
            self._close_lock.release()


def _text(value: Any, maximum: int, *, name: bool = False) -> str:
    if type(value) is not str or len(value) > maximum or (name and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value)):
        raise WorkerError("worker_descriptor_invalid")
    return value


class WorkerTool(PluginTool):
    def __init__(self, api: WorkerAPI, value: dict):
        super().__init__(api)
        self._value = value
        self.name = value["name"]
        self.display_name = value["display_name"]
        self.description = value["description"]
        self.destructive_tool_names = set(value["destructive"])
        self.background_allowed_tool_names = set(value["background_allowed"])

    # Plain instance fields make passive catalog reads nonexecuting.
    name = ""
    display_name = ""
    description = ""
    destructive_tool_names = frozenset()
    background_allowed_tool_names = frozenset()

    def as_langchain_tools(self) -> list:
        from langchain_core.tools import StructuredTool

        result = []
        def callable_for(child):
            def invoke(**values):
                value = self.plugin_api.invoke(child["name"], values)
                if child["response_format"] == "content_and_artifact":
                    if type(value) is not list or len(value) != 2:
                        raise WorkerError("worker_result_invalid")
                    return tuple(value)
                return value
            return invoke
        for child in self._value["children"]:
            result.append(StructuredTool(name=child["name"], description=child["description"],
                args_schema=child["schema"], func=callable_for(child), return_direct=child["return_direct"],
                response_format=child["response_format"]))
        return result

    def execute(self, query: str) -> str:
        return self.plugin_api.invoke(self.name, {"query": query})


class WorkerAPI(PluginAPI):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._worker: PluginWorker | None = None

    def _revoke(self) -> None:
        super()._revoke()
        worker = self._worker
        if worker is not None:
            worker.close()

    def register_worker(self, timeout: float) -> None:
        prepared = prepared_worker(self.plugin_id, self.plugin_dir)
        with self._registration_lock:
            self._check_active(registration=True)
            self._worker = PluginWorker(prepared, self)
        value = self._worker.start(timeout)
        if type(value) is not dict or set(value) != {"tools", "skills", "channels", "webhooks"}:
            raise WorkerError("worker_descriptor_invalid")
        if type(value["tools"]) is not list or len(value["tools"]) > 256 or type(value["skills"]) is not list or len(value["skills"]) > 256:
            raise WorkerError("worker_descriptor_invalid")
        parents, children = set(), set()
        for tool in value["tools"]:
            if type(tool) is not dict:
                raise WorkerError("worker_descriptor_invalid")
            name = _text(tool.get("name"), 128, name=True)
            if name in parents:
                raise WorkerError("worker_descriptor_invalid")
            parents.add(name)
            _text(tool.get("display_name"), 256)
            _text(tool.get("description"), 16384)
            for field in ("destructive", "background_allowed"):
                if type(tool.get(field)) is not list or len(tool[field]) > 1024:
                    raise WorkerError("worker_descriptor_invalid")
                for item in tool[field]:
                    _text(item, 128, name=True)
            items = tool.get("children")
            if type(items) is not list or not 1 <= len(items) <= 1024:
                raise WorkerError("worker_descriptor_invalid")
            for child in items:
                if type(child) is not dict:
                    raise WorkerError("worker_descriptor_invalid")
                name = _text(child.get("name"), 128, name=True)
                if name in children or len(children) >= 1024:
                    raise WorkerError("worker_descriptor_invalid")
                children.add(name)
                _text(child.get("description"), 16384)
                if (type(child.get("schema")) is not dict or type(child.get("return_direct")) is not bool
                        or child.get("response_format") not in {"content", "content_and_artifact"}):
                    raise WorkerError("worker_descriptor_invalid")
            self.register_tool(WorkerTool(self, tool))
        for skill in value["skills"]:
            if type(skill) is not dict:
                raise WorkerError("worker_descriptor_invalid")
            _text(skill.get("name"), 128, name=True)
            _text(skill.get("instructions", ""), 262144)
            if "root" in skill:
                from row_bot.plugins.sandbox import _checked_path
                try:
                    root = Path(_text(skill["root"], 4096))
                    if ".." in root.parts:
                        raise ValueError("worker_skill_root_invalid")
                    if not root.is_absolute():
                        root = prepared.plugin_dir / root
                    _checked_path(prepared.plugin_dir, root)
                    skill["root"] = root
                except Exception:
                    raise WorkerError("worker_skill_root_invalid") from None
            self.register_skill(skill)
        from row_bot.plugins.worker_channels import register_contributions
        register_contributions(self, value)

    def _publish_webhooks(self) -> None:
        if self._worker is not None:
            self._worker.call("publish", {})
        super()._publish_webhooks()

    def invoke(self, name: str, values: dict) -> Any:
        self._check_dispatch()
        if self._worker is None:
            raise WorkerError("worker_revoked")
        context = {"background": self.is_background_workflow(), "recipients": self.get_allowed_recipients()}
        result = self._worker.call("invoke", {"name": name, "values": values, "context": context})
        self._check_dispatch()
        return unpack(result)
