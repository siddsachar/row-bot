from __future__ import annotations

import json
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
from uuid import UUID

import pytest

from tests.subsystem.plugins.conftest import prepare_worker_environment, write_plugin
from tests.subsystem.plugins.test_plugin_environment import fake_venv, put_distribution, wheel

pytestmark = pytest.mark.subsystem


@pytest.mark.parametrize("mode", ["conflict", "transitive", "timeout", "failure", "missing", "malformed"])
def test_dependency_plan_fails_closed_before_install(plugin_modules, monkeypatch, mode):
    from row_bot.plugins import sandbox
    environment = plugin_modules["state"].DATA_DIR / "plugin_environments/phase-test" / str(UUID(int=1)) / "environment"
    fake_venv(environment)
    monkeypatch.setattr(sandbox, "_get_core_requirements", lambda: {"host_package": "1.0"})
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        assert "--dry-run" in argv
        assert "host-package==1.0" in Path(argv[argv.index("--constraint") + 1]).read_text()
        if mode == "timeout":
            raise subprocess.TimeoutExpired(argv, 60)
        if mode == "failure":
            return SimpleNamespace(returncode=1)
        report = Path(argv[argv.index("--report") + 1])
        if mode != "missing":
            data = {"version": "1", "install": [wheel(
                "host-package" if mode == "conflict" else "addon", "2.0",
                ["host-package>=2"] if mode == "transitive" else [])]}
            report.write_text("invalid" if mode == "malformed" else json.dumps(data))
        return SimpleNamespace(returncode=0)
    def checked_run(argv, **kwargs):
        result = run(argv, **kwargs)
        if result.returncode:
            raise sandbox.EnvironmentError("environment_process_failed")
    monkeypatch.setattr(sandbox, "_run", checked_run)
    ok, _ = sandbox.install_dependencies(["addon"], environment=environment)
    assert not ok and len(calls) == 1


def test_dependency_install_reuses_verified_complete_constraints(plugin_modules, monkeypatch):
    from row_bot.plugins import sandbox
    environment = plugin_modules["state"].DATA_DIR / "plugin_environments/phase-test" / str(UUID(int=1)) / "environment"
    fake_venv(environment)
    monkeypatch.setattr(sandbox, "_get_core_requirements", lambda: {"host_package": "1.0"})
    calls = []
    def run(argv, **kwargs):
        pins = Path(argv[argv.index("--constraint") + 1]).read_text()
        calls.append(pins)
        if "--dry-run" in argv:
            Path(argv[argv.index("--report") + 1]).write_text(json.dumps({"version": "1", "install": [
                wheel("addon", "2.0", ["host-package==1.0"]), wheel("host-package", "1.0")]}))
        else:
            requirements = Path(argv[argv.index("--requirement") + 1]).read_text()
            assert "--no-deps" in argv and "--require-hashes" in argv and "addon==2.0" in requirements
            put_distribution(environment, "addon", "2.0")
            put_distribution(environment, "host-package", "1.0")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(sandbox, "_run", run)
    assert sandbox.install_dependencies(["addon"], environment=environment)[0]
    assert len(calls) == 2 and "addon==2.0" in calls[1] and "host-package==1.0" in calls[1]


def test_late_api_registration_and_webhook_are_revoked(plugin_modules, tmp_path):
    from row_bot.plugins.api import PluginAPI
    plugin_modules["state"].set_plugin_enabled("fixture", True)
    api = PluginAPI("fixture", tmp_path, plugin_modules["state"], staged=True)
    api.register_webhook_route("fixture", lambda request: None)
    with pytest.raises(RuntimeError, match="not published"):
        api._check_dispatch()
    assert not plugin_modules["webhooks"]._webhooks
    api._revoke()
    with pytest.raises(RuntimeError):
        api.register_skill({"name": "late"})
    with pytest.raises(RuntimeError):
        api.register_webhook_route("late", lambda request: None)
    assert not api._registered_webhooks


def test_disable_then_reenable_does_not_restore_registration_epoch(plugin_modules, tmp_path):
    from row_bot.plugins.api import PluginAPI
    state, loader = plugin_modules["state"], plugin_modules["loader"]
    state.set_plugin_enabled("fixture", True)
    old = PluginAPI("fixture", tmp_path, state, staged=True)
    loader._registrations["fixture"] = old
    state.set_plugin_enabled("fixture", False)
    state.set_plugin_enabled("fixture", True)
    with pytest.raises(RuntimeError):
        old.register_skill({"name": "late"})
    replacement = PluginAPI("fixture", tmp_path, state, staged=True)
    loader._registrations["fixture"] = replacement
    loader._cleanup_plugin_runtime("fixture", expected_api=old)
    assert loader._registrations["fixture"] is replacement
    replacement.register_skill({"name": "current"})


def test_replacement_cannot_publish_between_revocation_and_contribution_cleanup(plugin_modules, tmp_path, monkeypatch):
    from row_bot.plugins.api import PluginAPI
    state, loader, registry = (plugin_modules[k] for k in ("state", "loader", "registry"))
    state.set_plugin_enabled("fixture", True)
    old = PluginAPI("fixture", tmp_path, state, staged=True)
    loader._registrations["fixture"] = old
    observed = []
    original = registry.unregister_plugin
    def cleanup(plugin_id):
        def replacement_probe():
            acquired = loader._registration_lock.acquire(blocking=False)
            observed.append(acquired)
            if acquired:
                loader._registration_lock.release()
        worker = threading.Thread(target=replacement_probe)
        worker.start()
        worker.join(5)
        assert not worker.is_alive()
        original(plugin_id)
    monkeypatch.setattr(registry, "unregister_plugin", cleanup)
    loader._cleanup_plugin_runtime("fixture", expected_api=old)
    assert observed == [False]
    assert old._registration_revoked and "fixture" not in loader._registrations


def test_bound_plugin_tool_revoked_by_disable_and_reload(plugin_modules, tmp_path):
    plugin = write_plugin(tmp_path)
    loader, registry, state = (plugin_modules[k] for k in ("loader", "registry", "state"))
    state.set_plugin_enabled("sample-plugin", True)
    prepare_worker_environment(plugin_modules, plugin)
    assert loader._load_single_plugin(plugin).success
    bound = registry.get_langchain_tools(refresh_mcp=False)[0]
    assert bound.invoke({"query": "fixture"}) == "sample:fixture"
    state.set_plugin_enabled("sample-plugin", False)
    with pytest.raises(RuntimeError, match="revoked"):
        bound.invoke({"query": "fixture"})
    state.set_plugin_enabled("sample-plugin", True)
    loader._cleanup_plugin_runtime("sample-plugin")
    prepare_worker_environment(plugin_modules, plugin)
    assert loader._load_single_plugin(plugin).success
    with pytest.raises(RuntimeError, match="revoked"):
        bound.invoke({"query": "fixture"})


def test_registration_process_after_timeout_cannot_publish(plugin_modules, tmp_path, monkeypatch):
    from row_bot.plugins.worker import WorkerAPI
    state, loader, webhooks = (plugin_modules[k] for k in ("state", "loader", "webhooks"))
    plugin = write_plugin(tmp_path, "fixture-plugin", main=(
        "import threading\ndef register(api):\n"
        "    api.set_config('entered', True)\n"
        "    threading.Event().wait()\n"
        "    api.register_webhook_route('late', lambda request: None)\n"))
    state.set_plugin_enabled("fixture-plugin", True)
    prepare_worker_environment(plugin_modules, plugin)
    api = WorkerAPI("fixture-plugin", plugin, state, staged=True)
    monkeypatch.setattr(loader, "REGISTER_TIMEOUT", 1.0)
    try:
        with pytest.raises(TimeoutError):
            loader._call_register_with_timeout(plugin, api)
        assert state.get_plugin_config("fixture-plugin", "entered") is True
        assert api._worker._process.poll() is not None
        assert not webhooks._webhooks
        assert api._registration_revoked
    finally:
        api._revoke()
