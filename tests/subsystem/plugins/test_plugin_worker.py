"""Prepared disposable interpreters and synthetic plugins; no pip/network/device IO."""
from __future__ import annotations

import os
import py_compile
import sys
import threading
from types import SimpleNamespace
from uuid import uuid4
import venv

import pytest

from tests.subsystem.plugins.conftest import write_plugin

pytestmark = pytest.mark.subsystem


@pytest.fixture
def worker_fixture(plugin_modules):
    installer, state, loader = (plugin_modules[key] for key in ("installer", "state", "loader"))
    source = write_plugin(installer.PLUGINS_DIR)
    operation = str(uuid4())
    environment = installer._generation_path("sample-plugin", operation, create=True)
    # This explicitly creates a disposable stdlib interpreter only; no pip,
    # dependency installation, network or inherited host site-packages.
    venv.EnvBuilder(with_pip=False, symlinks=False).create(environment)

    def publish():
        old = state.get_plugin_environment_state("sample-plugin")
        state.set_plugin_environment_state("sample-plugin", {
            "active_operation_id": operation,
            "operations": {operation: {"stage": "ready",
                "plugin_revision": installer.get_plugin_source_revision("sample-plugin"),
                "environment_revision": installer._tree_revision(environment, source=False)}}}, expected=old)
        state.set_plugin_enabled("sample-plugin", True)

    publish()
    return SimpleNamespace(installer=installer, state=state, loader=loader,
        registry=plugin_modules["registry"], source=source, environment=environment, publish=publish)


def _main(fixture, code):
    (fixture.source / "plugin_main.py").write_text(code, encoding="utf-8")
    fixture.publish()


def _load(fixture):
    result = fixture.loader._load_single_plugin(fixture.source)
    assert result.success, result.error
    return fixture.registry.get_langchain_tools(refresh_mcp=False)[0]


def test_real_prepared_worker_round_trip_and_host_import_isolation(worker_fixture):
    fixture = worker_fixture
    before_path = list(sys.path)
    before_modules = set(sys.modules)
    tool = _load(fixture)
    assert tool.name == "sample_tool"
    assert tool.invoke({"query": "fixture"}) == "sample:fixture"
    assert sys.path == before_path
    assert "_row_bot_plugin" not in set(sys.modules) - before_modules
    api = fixture.loader._registrations["sample-plugin"]
    process = api._worker._process
    assert process.pid != os.getpid() and process.poll() is None
    fixture.loader._cleanup_plugin_runtime("sample-plugin")
    assert process.poll() is not None
    with pytest.raises(RuntimeError, match="revoked"):
        tool.invoke({"query": "late"})


def test_missing_ready_generation_never_spawns_or_imports(worker_fixture, monkeypatch):
    from row_bot.plugins import worker
    fixture = worker_fixture
    old = fixture.state.get_plugin_environment_state("sample-plugin")
    fixture.state.set_plugin_environment_state("sample-plugin", {}, expected=old)
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **k: pytest.fail("Unprepared worker spawned"))
    result = fixture.loader._load_single_plugin(fixture.source)
    assert not result.success and "worker_environment_not_ready" in result.error
    assert not fixture.registry.get_plugin_tools("sample-plugin")


@pytest.mark.parametrize("changed", ["source", "environment"])
def test_ready_generation_changed_fails_before_spawn(worker_fixture, monkeypatch, changed):
    from row_bot.plugins import worker
    fixture = worker_fixture
    path = fixture.source if changed == "source" else fixture.environment
    (path / "changed.py").write_text("value = 1\n")
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **k: pytest.fail("Changed worker spawned"))
    result = fixture.loader._load_single_plugin(fixture.source)
    assert not result.success and f"worker_{changed}_changed" in result.error


def test_scoped_config_secret_callbacks_and_host_environment_removed(worker_fixture, monkeypatch):
    fixture = worker_fixture
    monkeypatch.setenv("PRIVATE_HOST_KEY", "must-not-inherit")
    fixture.state.set_plugin_config("other-plugin", "value", "foreign")
    fixture.state.set_plugin_config("sample-plugin", "value", "own")
    fixture.state.set_plugin_secret("sample-plugin", "key", "synthetic-secret")
    _main(fixture, '''from plugins.api import PluginTool
import os
class Tool(PluginTool):
    name = 'sample_tool'
    display_name = 'Sample'
    def execute(self, query):
        self.plugin_api.set_config('last', query)
        return '|'.join([self.plugin_api.get_config('value'), self.plugin_api.get_secret('key'), os.environ.get('PRIVATE_HOST_KEY', '')])
def register(api): api.register_tool(Tool(api))
''')
    assert _load(fixture).invoke({"query": "updated"}) == "own|synthetic-secret|"
    assert fixture.state.get_plugin_config("sample-plugin", "last") == "updated"
    assert fixture.state.get_plugin_config("other-plugin", "value") == "foreign"


def test_registration_timeout_terminates_actual_owned_process(worker_fixture, monkeypatch):
    from row_bot.plugins import worker
    fixture = worker_fixture
    _main(fixture, "import threading\ndef register(api): threading.Event().wait()\n")
    monkeypatch.setattr(fixture.loader, "REGISTER_TIMEOUT", 0.3)
    processes = []
    original = worker.subprocess.Popen
    def record(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(worker.subprocess, "Popen", record)
    result = fixture.loader._load_single_plugin(fixture.source)
    assert not result.success and "timed out" in result.error
    assert len(processes) == 1 and processes[0].poll() is not None
    assert not fixture.registry.get_plugin_tools("sample-plugin")
    assert "sample-plugin" not in fixture.loader._registrations


def test_disable_and_replacement_revoke_old_callable(worker_fixture):
    fixture = worker_fixture
    tool = _load(fixture)
    old = fixture.loader._registrations["sample-plugin"]._worker._process
    fixture.state.set_plugin_enabled("sample-plugin", False)
    with pytest.raises(RuntimeError, match="revoked"):
        tool.invoke({"query": "no"})
    fixture.state.set_plugin_enabled("sample-plugin", True)
    fixture.loader._cleanup_plugin_runtime("sample-plugin")
    assert old.poll() is not None
    replacement = _load(fixture)
    assert replacement.invoke({"query": "yes"}) == "sample:yes"
    with pytest.raises(RuntimeError, match="revoked"):
        tool.invoke({"query": "old"})


def test_ignored_bytecode_and_pth_cannot_execute(worker_fixture):
    fixture = worker_fixture
    source = fixture.source / "plugin_main.py"
    original = source.read_bytes()
    timestamp = source.stat().st_mtime_ns
    # Compile different code with matching source size and timestamp.
    malicious = original.replace(b"sample:", b"poison:")
    assert len(malicious) == len(original)
    source.write_bytes(malicious)
    os.utime(source, ns=(timestamp, timestamp))
    py_compile.compile(str(source), doraise=True)
    source.write_bytes(original)
    os.utime(source, ns=(timestamp, timestamp))
    site = fixture.environment / ("Lib/site-packages" if os.name == "nt" else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    marker = fixture.environment.parent / "pth-executed"
    (site / "poison.pth").write_text(f"import pathlib; pathlib.Path({str(marker)!r}).write_text('bad')\n")
    fixture.publish()
    assert _load(fixture).invoke({"query": "safe"}) == "sample:safe"
    assert not marker.exists()


@pytest.mark.parametrize("kind", ["channel", "webhook"])
def test_invalid_contributions_fail_without_partial_publication(worker_fixture, kind):
    fixture = worker_fixture
    call = "api.register_channel(object())" if kind == "channel" else "api.register_webhook_route('../escape', lambda request: None)"
    _main(fixture, "def register(api):\n    api.register_skill({'name': 'must_not_publish', 'instructions': 'private'})\n    " + call + "\n")
    result = fixture.loader._load_single_plugin(fixture.source)
    assert not result.success and result.error
    assert not fixture.registry.get_plugin_skills("sample-plugin")


def test_skills_and_safety_names_preserved(worker_fixture):
    fixture = worker_fixture
    _main(fixture, '''from plugins.api import PluginTool
class Tool(PluginTool):
    name = 'sample_tool'
    display_name = 'Sample'
    destructive_tool_names = {'sample_tool'}
    background_allowed_tool_names = {'sample_tool'}
    def execute(self, query): return query
def register(api):
    api.register_tool(Tool(api))
    api.register_skill({'name': 'sample_skill', 'instructions': 'Approved instruction', 'tags': ['fixture']})
''')
    _load(fixture)
    assert fixture.registry.get_destructive_names() == {"sample_tool"}
    assert fixture.registry.get_background_allowed_names() == {"sample_tool"}
    assert "Approved instruction" in fixture.registry.get_skills_prompt()


def test_errors_do_not_echo_worker_content(worker_fixture):
    fixture = worker_fixture
    _main(fixture, "def register(api): raise RuntimeError('PRIVATE_WORKER_ERROR')\n")
    result = fixture.loader._load_single_plugin(fixture.source)
    assert not result.success and "worker_operation_failed" in result.error
    assert "PRIVATE_WORKER_ERROR" not in result.error


def test_custom_child_schemas_async_results_and_alias_injection(worker_fixture):
    fixture = worker_fixture
    _main(fixture, '''from plugins.api import PluginTool
class Child:
    description = 'Typed child'
    args_schema = {'type': 'object', 'properties': {'count': {'type': 'integer', 'minimum': 1}, '_name': {'type': 'string'}}, 'required': ['count']}
    return_direct = True
    response_format = 'content'
    func = None
    coroutine = True
    def __init__(self, name): self.name = name
    async def ainvoke(self, values):
        if type(values['count']) is not int or values['count'] < 1: raise ValueError('invalid')
        return self.name + ':' + str(values['count'])
class Tool(PluginTool):
    name = 'parent_tool'
    display_name = 'Parent'
    destructive_tool_names = {'dangerous_child'}
    background_allowed_tool_names = set()
    def as_langchain_tools(self): return [Child('safe_child'), Child('dangerous_child')]
def register(api): api.register_tool(Tool(api))
''')
    _load(fixture)
    children = fixture.registry.get_langchain_tools(refresh_mcp=False)
    assert [child.name for child in children] == ["safe_child", "dangerous_child"]
    assert children[0].args_schema["properties"]["count"] == {"type": "integer", "minimum": 1}
    assert children[0].return_direct
    assert fixture.registry.get_destructive_names() == {"dangerous_child"}
    assert children[0].invoke({"count": 3, "_name": "dangerous_child"}) == "safe_child:3"
    assert children[1].invoke({"count": 4}) == "dangerous_child:4"


def test_native_stdout_does_not_corrupt_control_stream(worker_fixture):
    fixture = worker_fixture
    original = (fixture.source / "plugin_main.py").read_text()
    _main(fixture, "import os\nos.write(1, b'not a control frame\\n')\nprint('also not a frame')\n" + original)
    assert _load(fixture).invoke({"query": "safe"}) == "sample:safe"


def test_invocation_timeout_kills_process_without_replay(worker_fixture):
    from row_bot.plugins.worker import WorkerError
    fixture = worker_fixture
    _main(fixture, '''from plugins.api import PluginTool
import threading
class Tool(PluginTool):
    name = 'sample_tool'
    display_name = 'Sample'
    def execute(self, query):
        self.plugin_api.set_config('admitted', query)
        threading.Event().wait()
def register(api): api.register_tool(Tool(api))
''')
    _load(fixture)
    worker = fixture.loader._registrations["sample-plugin"]._worker
    with pytest.raises(WorkerError, match="worker_timeout"):
        worker.call("invoke", {"name": "sample_tool", "values": {"query": "once"}}, timeout=0.3)
    assert fixture.state.get_plugin_config("sample-plugin", "admitted") == "once"
    assert worker._process.poll() is not None
    with pytest.raises(WorkerError, match="worker_revoked"):
        worker.call("invoke", {"name": "sample_tool", "values": {"query": "twice"}})
    assert fixture.state.get_plugin_config("sample-plugin", "admitted") == "once"


def test_pre_cancelled_call_never_executes(worker_fixture):
    from row_bot.cancellation import CancellationScope, use_cancellation_scope
    from row_bot.plugins.worker import WorkerError
    fixture = worker_fixture
    _load(fixture)
    worker = fixture.loader._registrations["sample-plugin"]._worker
    scope = CancellationScope()
    scope.cancel("test")
    with use_cancellation_scope(scope), pytest.raises(WorkerError, match="worker_revoked"):
        worker.call("invoke", {"name": "sample_tool", "values": {"query": "late"}})
    assert worker._process.poll() is not None


def test_large_payload_rejected_without_entering_worker(worker_fixture):
    from row_bot.plugins.worker import MAX_FRAME, WorkerError
    fixture = worker_fixture
    _load(fixture)
    worker = fixture.loader._registrations["sample-plugin"]._worker
    with pytest.raises(WorkerError, match="worker_payload_invalid"):
        worker.call("invoke", {"name": "sample_tool", "values": {"query": "x" * MAX_FRAME}})
    assert worker.call("invoke", {"name": "sample_tool", "values": {"query": "small"}}) == "sample:small"


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object runtime")
@pytest.mark.parametrize("phase", ["registered", "startup_timeout"])
def test_windows_owned_job_terminates_spawned_descendant(worker_fixture, phase):
    import ctypes
    from ctypes import wintypes
    from row_bot.plugins.worker import WorkerAPI, WorkerError

    fixture = worker_fixture
    _main(fixture, '''import subprocess
import sys
import threading
def register(api):
    child = subprocess.Popen([sys.executable, '-I', '-S', '-c', 'import threading; threading.Event().wait()'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    api.set_config('synthetic_child_pid', child.pid)
''' + ("    threading.Event().wait()\n" if phase == "startup_timeout" else ""))
    # Bypass only the AST policy in this direct worker lifecycle fixture so a
    # synthetic owned descendant can be created; production keeps that policy.
    api = WorkerAPI("sample-plugin", fixture.source, fixture.state, staged=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handles = []
    original = api.set_config
    def capture(key, value):
        handle = kernel.OpenProcess(0x100000, False, value)
        assert handle
        handles.append(handle)
        original(key, value)
    api.set_config = capture
    try:
        if phase == "startup_timeout":
            with pytest.raises(WorkerError, match="worker_timeout"):
                api.register_worker(0.5)
        else:
            api.register_worker(5)
        assert len(handles) == 1
        api._revoke()
        assert api._worker._process.poll() is not None
        assert kernel.WaitForSingleObject(handles[0], 3000) == 0
    finally:
        api._revoke()
        for handle in handles:
            kernel.CloseHandle(handle)


@pytest.mark.skipif(os.name == "nt", reason="Unix process-group runtime")
def test_unix_owned_group_terminates_spawned_descendant(worker_fixture):
    import time
    import psutil
    from row_bot.plugins.worker import WorkerAPI

    fixture = worker_fixture
    _main(fixture, '''import subprocess
import sys
def register(api):
    child = subprocess.Popen([sys.executable, '-I', '-S', '-c', 'import threading; threading.Event().wait()'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    api.set_config('synthetic_child_pid', child.pid)
''')
    api = WorkerAPI("sample-plugin", fixture.source, fixture.state, staged=True)
    try:
        api.register_worker(5)
        descendant = psutil.Process(fixture.state.get_plugin_config("sample-plugin", "synthetic_child_pid"))
        api._revoke()
        deadline = time.monotonic() + 3
        while descendant.is_running() and descendant.status() != psutil.STATUS_ZOMBIE:
            assert time.monotonic() < deadline
            threading.Event().wait(0.01)
        assert api._worker._process.poll() is not None
    finally:
        api._revoke()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object runtime")
def test_job_assignment_failure_refuses_plugin_execution(worker_fixture, monkeypatch):
    from row_bot.plugins import worker
    fixture = worker_fixture
    marker = fixture.source.parent / "must_not_execute"
    _main(fixture, f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\ndef register(api): pass\n")
    def deny(process):
        raise worker.WorkerError("worker_ownership_unavailable")
    monkeypatch.setattr(worker, "_WindowsJob", deny)
    result = fixture.loader._load_single_plugin(fixture.source)
    assert not result.success and "worker_ownership_unavailable" in result.error
    assert not marker.exists()


def test_callback_waiting_on_state_cannot_write_after_revocation(worker_fixture):
    fixture = worker_fixture
    _load(fixture)
    api = fixture.loader._registrations["sample-plugin"]
    entered, ended = threading.Event(), threading.Event()
    outcomes = []
    def callback():
        entered.set()
        try:
            api._worker._callback({"method": "set_config", "args": ["late", "must-not-write"]})
        except RuntimeError:
            outcomes.append("revoked")
        finally:
            ended.set()
    with fixture.state._state_lock:
        thread = threading.Thread(target=callback)
        thread.start()
        assert entered.wait(3)
        api._revoke()
    assert ended.wait(3)
    thread.join(3)
    assert outcomes == ["revoked"]
    assert fixture.state.get_plugin_config("sample-plugin", "late") is None


def test_busy_call_has_no_queue_and_cancel_drains_actual_worker(worker_fixture):
    from row_bot.plugins.worker import WorkerError
    fixture = worker_fixture
    _main(fixture, '''from plugins.api import PluginTool
import threading
class Tool(PluginTool):
    name = 'sample_tool'
    display_name = 'Sample'
    def execute(self, query):
        self.plugin_api.set_config('entered', True)
        threading.Event().wait()
def register(api): api.register_tool(Tool(api))
''')
    _load(fixture)
    api = fixture.loader._registrations["sample-plugin"]
    worker = api._worker
    entered = threading.Event()
    original = api.set_config
    def set_config(key, value):
        original(key, value)
        entered.set()
    api.set_config = set_config
    outcomes = []
    def run():
        try:
            worker.call("invoke", {"name": "sample_tool", "values": {"query": "first"}})
        except WorkerError as exc:
            outcomes.append(str(exc))
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(3)
        with pytest.raises(WorkerError, match="worker_busy"):
            worker.call("invoke", {"name": "sample_tool", "values": {"query": "second"}})
        assert worker.close()
    finally:
        worker.close()
        thread.join(3)
    assert not thread.is_alive() and outcomes == ["worker_revoked"]


def test_explicit_skill_root_cannot_escape_prepared_source(worker_fixture):
    fixture = worker_fixture
    _main(fixture, f"def register(api): api.register_skill({{'name': 'escape', 'instructions': 'text', 'root': {str(fixture.source.parent)!r}}})\n")
    result = fixture.loader._load_single_plugin(fixture.source)
    assert not result.success and "worker_skill_root_invalid" in result.error
    assert not fixture.registry.get_plugin_skills("sample-plugin")


def test_relative_parent_skill_root_cannot_escape_prepared_source(worker_fixture):
    fixture = worker_fixture
    _main(fixture, "def register(api): api.register_skill({'name': 'escape', 'instructions': 'text', 'root': '..'})\n")
    result = fixture.loader._load_single_plugin(fixture.source)
    assert not result.success and "worker_skill_root_invalid" in result.error


def test_owned_path_skill_and_background_context_preserved(worker_fixture):
    fixture = worker_fixture
    _main(fixture, '''from plugins.api import PluginTool
class Tool(PluginTool):
    name = 'sample_tool'
    display_name = 'Sample'
    def execute(self, query):
        return str(self.plugin_api.is_background_workflow()) + ':' + ','.join(self.plugin_api.get_allowed_recipients())
def register(api):
    api.register_tool(Tool(api))
    api.register_skill({'name': 'owned', 'instructions': 'text', 'root': api.plugin_dir})
''')
    tool = _load(fixture)
    api = fixture.loader._registrations["sample-plugin"]
    api.is_background_workflow = lambda: True
    api.get_allowed_recipients = lambda: ["approved@example.invalid"]
    assert tool.invoke({"query": "context"}) == "True:approved@example.invalid"
    assert fixture.registry.get_plugin_skills("sample-plugin")[0]["root"] == fixture.source


@pytest.mark.parametrize("response", ["stale", "malformed", "oversized", "duplicate_callback",
                                      "duplicate_key", "nonfinite", "bool_version", "deep"])
def test_protocol_rejects_stale_malformed_and_unbounded_frames(response):
    import json
    from row_bot.plugins.worker_protocol import MAX_FRAME, Peer
    read_fd, write_fd = os.pipe()
    output_read, output_write = os.pipe()
    reader, writer = os.fdopen(read_fd, "rb", buffering=0), os.fdopen(output_write, "wb", buffering=0)
    sender, sink = os.fdopen(write_fd, "wb", buffering=0), os.fdopen(output_read, "rb", buffering=0)
    called, values = threading.Event(), []
    def handle(method, args):
        values.append((method, args))
        called.set()
    peer = Peer(reader, writer, role="host", handler=handle)
    try:
        if response == "stale":
            data = json.dumps({"v": 1, "id": "host:999", "kind": "result", "ok": True, "result": None}).encode() + b"\n"
        elif response == "malformed":
            data = b"not-json\n"
        elif response == "oversized":
            data = b"x" * (MAX_FRAME + 1)
        elif response == "duplicate_key":
            data = b'{"v":1,"v":1}\n'
        elif response == "nonfinite":
            data = b'{"v":1,"number":NaN}\n'
        elif response == "bool_version":
            data = b'{"v":true}\n'
        elif response == "deep":
            data = b'{"v":1,"data":' + b'[' * 40 + b'0' + b']' * 40 + b'}\n'
        else:
            message = {"v": 1, "id": "worker:1", "kind": "request", "method": "api", "args": {"count": "once"}}
            data = json.dumps(message).encode() + b"\n"
            sender.write(data)
            assert called.wait(3)
        try:
            sender.write(data)
        except BrokenPipeError:
            pass
        assert peer.closed.wait(3)
        assert values == ([("api", {"count": "once"})] if response == "duplicate_callback" else [])
    finally:
        peer.close()
        sender.close()
        writer.close()
        peer._reader_thread.join(3)
        peer._writer_thread.join(3)
        reader.close()
        sink.close()


def test_duplex_peer_preserves_multipart_binary_and_nested_callback():
    from row_bot.plugins.worker_protocol import Peer, pack, unpack
    a, b = os.pipe()
    c, d = os.pipe()
    streams = [os.fdopen(a, "rb", buffering=0), os.fdopen(b, "wb", buffering=0),
               os.fdopen(c, "rb", buffering=0), os.fdopen(d, "wb", buffering=0)]
    first = Peer(streams[0], streams[3], role="host", handler=lambda method, args: args)
    second = Peer(streams[2], streams[1], role="worker",
                  handler=lambda method, args: second.request("callback", args, timeout=10))
    content = b"fixture" * (1024 * 1024)
    try:
        result = first.request("roundtrip", {"content": pack(content)}, timeout=15)
        assert unpack(result)["content"] == content
        assert not first.closed.is_set() and not second.closed.is_set()
    finally:
        first.close()
        second.close()
        streams[1].close()
        streams[3].close()
        for peer in (first, second):
            peer._reader_thread.join(3)
            peer._writer_thread.join(3)
        streams[0].close()
        streams[2].close()


_CHANNEL_SOURCE = """from plugins.api import Channel, ChannelCapabilities, ConfigField, PluginWebhookResponse
class Adapter(Channel):
    name = 'fixture_channel'
    display_name = 'Fixture channel'
    capabilities = ChannelCapabilities(buttons=True, streaming=True)
    config_fields = [ConfigField('token', 'Token', field_type='password')]
    def __init__(self, api): self.api, self.running = api, False
    async def start(self):
        self.running = True
        self.api.set_config('started', True)
        return True
    async def stop(self): self.running = False
    def is_configured(self): return True
    def is_running(self): return self.running
    def send_message(self, target, text): self.api.set_config('sent', [target, text])
    def get_default_target(self): return 'recipient'
    def send_approval_request(self, target, interrupt_data, config): return 'approval:fixture'
    def update_approval_message(self, message_ref, status, source=''):
        self.api.set_config('approval', [message_ref, status, source])
def register(api):
    api.register_channel(Adapter(api))
    async def handler(request):
        return PluginWebhookResponse(status_code=202, body=request.body, media_type='application/octet-stream', headers={'fixture': 'yes'})
    api.set_config('webhook_path', api.register_webhook_route('fixture', handler, max_body_bytes=10485760))
"""


def _channel_load(fixture, code=_CHANNEL_SOURCE):
    import json
    manifest_path = fixture.source / "plugin.json"
    value = json.loads(manifest_path.read_text())
    value["provides"]["channels"] = [{"id": "fixture_channel"}]
    manifest_path.write_text(json.dumps(value))
    _main(fixture, code)
    result = fixture.loader._load_single_plugin(fixture.source)
    assert result.success, result.error
    api = fixture.loader._registrations["sample-plugin"]
    return api, api._registered_channels[0]


def test_real_channel_lifecycle_and_webhook_multipart_parity(worker_fixture):
    import asyncio
    from row_bot.plugins import webhooks
    from row_bot.plugins.api import PluginWebhookRequest
    fixture = worker_fixture
    api, channel = _channel_load(fixture)
    assert channel.name == "fixture_channel" and channel.capabilities.buttons
    assert channel.config_fields[0].field_type == "password"
    assert channel.is_configured() and not channel.is_running()
    assert asyncio.run(channel.start()) and channel.is_running()
    assert fixture.state.get_plugin_config("sample-plugin", "started") is True
    channel.send_message(123, "hello")
    assert fixture.state.get_plugin_config("sample-plugin", "sent") == [123, "hello"]
    assert channel.get_default_target() == "recipient"
    assert channel.make_thread_id("thread") == "fixture_channel_thread"
    assert channel.send_approval_request(123, {"action": "delete"}, {}) == "approval:fixture"
    channel.update_approval_message("approval:fixture", "denied", "ui")
    assert fixture.state.get_plugin_config("sample-plugin", "approval") == ["approval:fixture", "denied", "ui"]
    record = webhooks._webhooks[("sample-plugin", "fixture")]
    content = b"x" * (10 * 1024 * 1024)
    request = PluginWebhookRequest("POST", "/fixture", {}, {}, content)
    response = asyncio.run(record.handler(request))
    assert response.status_code == 202 and response.body == content and response.headers == {"fixture": "yes"}
    asyncio.run(channel.stop())
    assert not channel.is_running()
    api._revoke()
    with pytest.raises(RuntimeError, match="no longer active"):
        channel.send_message(123, "late")
    with pytest.raises(RuntimeError, match="no longer active"):
        asyncio.run(record.handler(request))


def test_async_listener_persists_and_nested_stream_handles_stay_in_worker(worker_fixture, monkeypatch):
    import asyncio
    from row_bot.plugins import api as sdk
    from row_bot.cancellation import current_cancellation_scope
    fixture = worker_fixture
    entered, finished = threading.Event(), threading.Event()
    received = []
    async def fake_turn(self, message, callbacks, **kwargs):
        received.append((message, kwargs, current_cancellation_scope()))
        entered.set()
        handle = await callbacks.start_stream("begin")
        assert isinstance(handle, str) and len(handle) == 32
        await callbacks.update_stream(handle, "update")
        await callbacks.finish_stream(handle, "end")
        await callbacks.send_text("delivered")
        finished.set()
        return sdk.ChannelRunResult(thread_id="fixture_channel_thread", answer="done", handled=True)
    monkeypatch.setattr(sdk.PluginAPI, "handle_channel_message", fake_turn)
    code = _CHANNEL_SOURCE.replace("from plugins.api import Channel,", "from plugins.api import ChannelInboundMessage, ChannelOutboundCallbacks, Channel,")
    code = code.replace("        self.running = True", """        self.running = True
        import asyncio
        class Handle: pass
        marker = Handle()
        async def begin(text): return marker
        async def update(handle, text):
            if handle is not marker: raise RuntimeError('wrong handle')
            self.api.set_config('stream_' + text, True)
        async def send(text): self.api.set_config('delivered', text)
        async def listen():
            result = await self.api.handle_channel_message(
                ChannelInboundMessage('fixture_channel', 'thread', 'sender', text='hello'),
                ChannelOutboundCallbacks(send, start_stream=begin, update_stream=update, finish_stream=update), channel=self)
            self.api.set_config('result', result.answer)
        self.listener = asyncio.create_task(listen())""")
    api, channel = _channel_load(fixture, code)
    try:
        assert asyncio.run(channel.start())
        assert entered.wait(3) and finished.wait(5)
        # A FIFO callback after the async turn confirms persisted listener work.
        assert channel.is_running()
        assert fixture.state.get_plugin_config("sample-plugin", "stream_update") is True
        assert fixture.state.get_plugin_config("sample-plugin", "stream_end") is True
        assert fixture.state.get_plugin_config("sample-plugin", "delivered") == "delivered"
        assert received[0][0].channel_name == "fixture_channel"
        assert received[0][1]["channel"] is channel
        assert received[0][1]["enabled_tool_names"] is None
        assert received[0][2] is not None
    finally:
        api._revoke()


def test_worker_revocation_cancels_owned_host_channel_turn(worker_fixture, monkeypatch):
    import asyncio
    from row_bot.plugins import api as sdk
    from row_bot.cancellation import current_cancellation_scope
    entered, stopped = threading.Event(), threading.Event()
    scopes = []
    async def fake_turn(self, message, callbacks, **kwargs):
        scopes.append(current_cancellation_scope())
        entered.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()
    monkeypatch.setattr(sdk.PluginAPI, "handle_channel_message", fake_turn)
    code = _CHANNEL_SOURCE.replace("from plugins.api import Channel,", "from plugins.api import ChannelInboundMessage, ChannelOutboundCallbacks, Channel,")
    code = code.replace("        self.running = True", """        self.running = True
        import asyncio
        async def send(text): self.api.set_config('unexpected', text)
        self.listener = asyncio.create_task(self.api.handle_channel_message(
            ChannelInboundMessage('fixture_channel', 'thread', 'sender'), ChannelOutboundCallbacks(send), channel=self))""")
    api, channel = _channel_load(worker_fixture, code)
    assert asyncio.run(channel.start())
    assert entered.wait(3)
    api._revoke()
    assert stopped.wait(3) and scopes[0].is_cancelled()
    assert api._worker._process.poll() is not None
    assert not api._worker._callbacks
    assert worker_fixture.state.get_plugin_config("sample-plugin", "unexpected") is None



def _mcp_config(fixture, code, *, register_only=False):
    import json
    from row_bot.plugins.manifest import parse_manifest
    from row_bot.plugins.mcp import plugin_mcp_servers
    path = fixture.source / "plugin.json"
    value = json.loads(path.read_text())
    value["provides"] = {"native_tools": [], "channels": [], "skills": [], "mcp_servers": [
        {"id": "fixture", "command": "python", "args": ["-u", "-m", "fixture_mcp", "argument with spaces"]}]}
    path.write_text(json.dumps(value))
    (fixture.source / "fixture_mcp.py").write_text(code)
    if not register_only:
        (fixture.source / "plugin_main.py").unlink()
    fixture.publish()
    if register_only:
        # Synthetic descendant fixture bypasses only plugin static import policy;
        # the actual resolver, immutable environment and MCP SDK stay in use.
        fixture.registry.register_plugin(manifest=parse_manifest(fixture.source), tools=[], skills=[])
    else:
        result = fixture.loader._load_single_plugin(fixture.source)
        assert result.success, result.error
    config = plugin_mcp_servers()["plugin_sample_plugin_fixture"]
    return config


def test_manifest_only_mcp_uses_prepared_python_and_revokes_stale_launch(worker_fixture):
    from row_bot.plugins.mcp import resolve_prepared_plugin_mcp_launch
    from row_bot.plugins.worker import WorkerError
    fixture = worker_fixture
    config = _mcp_config(fixture, "value = 1\n")
    launch = resolve_prepared_plugin_mcp_launch("plugin_sample_plugin_fixture", config)
    assert launch is not None and launch.command.startswith(str(fixture.environment))
    assert launch.args[:4] == ("-I", "-S", "-B", "-u")
    assert launch.args[-3:] == ("-m", "fixture_mcp", "argument with spaces")
    assert config["enabled"] and fixture.loader._registrations["sample-plugin"]._worker is None
    forged = {**config, "command": "python"}
    with pytest.raises(WorkerError, match="changed"):
        resolve_prepared_plugin_mcp_launch("plugin_sample_plugin_fixture", forged)
    (fixture.source / "fixture_mcp.py").write_text("value = 2\n")
    with pytest.raises(WorkerError, match="changed"):
        resolve_prepared_plugin_mcp_launch("plugin_sample_plugin_fixture", config)
    fixture.state.set_plugin_enabled("sample-plugin", False)
    with pytest.raises(WorkerError, match="revoked"):
        resolve_prepared_plugin_mcp_launch("plugin_sample_plugin_fixture", config)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows Job Object verification")
@pytest.mark.parametrize("exit_mode", ["normal", "blocked"])
def test_actual_mcp_sdk_cleanup_terminates_prepared_worker_descendant(worker_fixture, exit_mode):
    import asyncio
    import ctypes
    from ctypes import wintypes
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client
    from row_bot.plugins.worker import _run_directory, _worker_environment
    fixture = worker_fixture
    config = _mcp_config(fixture, '''import json, subprocess, sys
child = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', 'import threading; threading.Event().wait()'],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(json.dumps({'jsonrpc': '2.0', 'method': 'fixture', 'params': {'pid': child.pid, 'args': sys.argv[1:]}}), flush=True)
for line in sys.stdin: pass
'''.replace("for line in sys.stdin: pass", "import threading; threading.Event().wait()" if exit_mode == "blocked" else "for line in sys.stdin: pass"), register_only=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handles = []
    async def run():
        params = StdioServerParameters(command=config["command"], args=config["args"], cwd=config["cwd"],
            env=_worker_environment(_run_directory("sample-plugin")))
        with open(os.devnull, "w") as errors:
            async with stdio_client(params, errlog=errors) as (reader, _writer):
                message = await asyncio.wait_for(reader.receive(), timeout=10)
                payload = message.message.root.params
                assert payload["args"] == ["argument with spaces"]
                handle = kernel.OpenProcess(0x00100000, False, payload["pid"])
                assert handle
                handles.append(handle)
                assert kernel.WaitForSingleObject(handle, 0) == 258
    try:
        asyncio.run(run())
        assert kernel.WaitForSingleObject(handles[0], 3000) == 0
    finally:
        for handle in handles:
            kernel.CloseHandle(handle)


def test_channel_extra_tool_schema_and_callable_preserved(worker_fixture):
    fixture = worker_fixture
    code = _CHANNEL_SOURCE.replace("    def get_default_target(self):", """    def extra_tools(self):
        class Extra:
            name = 'channel_fixture_extra'
            description = 'Fixture extra'
            args_schema = {'type': 'object', 'properties': {'count': {'type': 'integer', 'minimum': 1}}, 'required': ['count']}
            return_direct = False
            response_format = 'content'
            func = True
            def invoke(self, value): return 'extra:' + str(value['count'])
        return [Extra()]
    def get_default_target(self):""")
    api, channel = _channel_load(fixture, code)
    try:
        extra = channel.extra_tools()[0]
        assert extra.name == "channel_fixture_extra"
        assert extra.args_schema["properties"]["count"] == {"type": "integer", "minimum": 1}
        assert extra.invoke({"count": 3}) == "extra:3"
    finally:
        api._revoke()


def test_reload_waits_for_cancelled_host_callback_actual_return(worker_fixture, monkeypatch):
    import asyncio
    from row_bot.plugins import api as sdk
    entered, cancelled, release, completed = (threading.Event() for _ in range(4))
    async def fake_turn(self, message, callbacks, **kwargs):
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            # The existing core producer may need actual cleanup after cancel.
            await asyncio.to_thread(release.wait)
        finally:
            completed.set()
        return sdk.ChannelRunResult(thread_id="fixture_channel_thread")
    monkeypatch.setattr(sdk.PluginAPI, "handle_channel_message", fake_turn)
    code = _CHANNEL_SOURCE.replace("from plugins.api import Channel,", "from plugins.api import ChannelInboundMessage, ChannelOutboundCallbacks, Channel,")
    code = code.replace("        self.running = True", """        self.running = True
        import asyncio
        async def send(text): pass
        self.listener = asyncio.create_task(self.api.handle_channel_message(
            ChannelInboundMessage('fixture_channel', 'thread', 'sender'), ChannelOutboundCallbacks(send), channel=self))""")
    fixture = worker_fixture
    api, channel = _channel_load(fixture, code)
    try:
        assert asyncio.run(channel.start()) and entered.wait(3)
        fixture.loader._cleanup_plugin_runtime("sample-plugin")
        assert cancelled.wait(3)
        assert fixture.loader._registrations["sample-plugin"] is api
        result = fixture.loader._load_single_plugin(fixture.source)
        assert not result.success and "worker_busy" in result.error
        assert api._worker._process.poll() is not None
    finally:
        release.set()
        assert completed.wait(3)
        assert api._worker._callbacks_drained.wait(3)
        fixture.loader._cleanup_plugin_runtime("sample-plugin")
    result = fixture.loader._load_single_plugin(fixture.source)
    assert result.success, result.error
    assert fixture.loader._registrations["sample-plugin"] is not api


def test_stopping_listener_cancels_its_turn_without_revoking_worker(worker_fixture, monkeypatch):
    import asyncio
    from row_bot.plugins import api as sdk
    entered, stopped = threading.Event(), threading.Event()
    async def fake_turn(self, message, callbacks, **kwargs):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()
    monkeypatch.setattr(sdk.PluginAPI, "handle_channel_message", fake_turn)
    code = _CHANNEL_SOURCE.replace("from plugins.api import Channel,", "from plugins.api import ChannelInboundMessage, ChannelOutboundCallbacks, Channel,")
    code = code.replace("        self.running = True", """        self.running = True
        import asyncio
        async def send(text): pass
        self.listener = asyncio.create_task(self.api.handle_channel_message(
            ChannelInboundMessage('fixture_channel', 'thread', 'sender'), ChannelOutboundCallbacks(send), channel=self))""")
    code = code.replace("    async def stop(self): self.running = False", """    async def stop(self):
        import asyncio
        self.listener.cancel()
        try: await self.listener
        except asyncio.CancelledError: pass
        self.running = False""")
    api, channel = _channel_load(worker_fixture, code)
    try:
        assert asyncio.run(channel.start()) and entered.wait(3)
        asyncio.run(channel.stop())
        assert stopped.wait(3) and api._worker._callbacks_drained.wait(3)
        assert not channel.is_running() and api._worker._process.poll() is None
    finally:
        api._revoke()


def test_existing_fifty_mib_attachment_crosses_owned_boundary(worker_fixture, monkeypatch):
    from row_bot.plugins import channel_runtime
    from row_bot.plugins.api import ChannelAttachmentResult
    calls = []
    def process(attachment, **kwargs):
        calls.append((len(attachment.data), attachment.size_bytes, kwargs))
        return ChannelAttachmentResult(prompt_text="processed")
    monkeypatch.setattr(channel_runtime, "process_plugin_channel_attachment", process)
    _main(worker_fixture, '''from plugins.api import PluginTool, ChannelAttachment
class Tool(PluginTool):
    name = 'sample_tool'
    display_name = 'Sample'
    def execute(self, query):
        size = 50 * 1024 * 1024
        return self.plugin_api.process_channel_attachment(ChannelAttachment(data=b'x' * size, size_bytes=size)).prompt_text
def register(api): api.register_tool(Tool(api))
''')
    assert _load(worker_fixture).invoke({"query": "attachment"}) == "processed"
    assert calls == [(50 * 1024 * 1024, 50 * 1024 * 1024, {"question": "", "max_chars": 80000})]


def test_callbacks_reject_foreign_channel_and_unowned_attachment(worker_fixture, monkeypatch):
    from row_bot.plugins import channel_runtime
    from row_bot.plugins.worker_channels import handle_callback
    from row_bot.plugins.worker_protocol import ProtocolError
    calls = []
    monkeypatch.setattr(channel_runtime, "process_plugin_channel_attachment", lambda *args, **kwargs: calls.append(True))
    api, _channel = _channel_load(worker_fixture)
    try:
        with pytest.raises(ProtocolError, match="denied"):
            handle_callback(api, "handle_channel_message", [
                {"channel_name": "other_plugin", "external_conversation_id": "thread", "sender_id": "sender"},
                {"id": "a" * 32, "methods": ["send_text"]}], {})
        with pytest.raises(ProtocolError, match="denied"):
            handle_callback(api, "process_channel_attachment", [{"local_path": str(worker_fixture.source / "plugin.json")}], {})
        assert calls == []
    finally:
        api._revoke()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows startup containment")
def test_mcp_job_assignment_failure_precedes_any_plugin_import(worker_fixture, monkeypatch):
    from row_bot.plugins import mcp_process
    fixture = worker_fixture
    marker = fixture.environment.parent / "must-not-run"
    monkeypatch.setattr(sys, "argv", ["mcp_process", str(fixture.environment), str(fixture.source),
        "-c", f"open({str(marker)!r}, 'w').write('bad')"])
    def trusted(name, filename):
        assert filename == "worker_ownership.py"
        def refuse(process):
            raise RuntimeError("worker_ownership_unavailable")
        return SimpleNamespace(WindowsJob=refuse)
    monkeypatch.setattr(mcp_process, "_trusted", trusted)
    with pytest.raises(RuntimeError, match="worker_ownership_unavailable"):
        mcp_process.main()
    assert not marker.exists()
