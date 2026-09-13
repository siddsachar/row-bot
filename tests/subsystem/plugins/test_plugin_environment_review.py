"""Independent fake-only checks of isolated preparation publication boundaries."""
import copy
import json
import os
from pathlib import Path
from uuid import UUID

import pytest

from tests.subsystem.plugins.test_plugin_environment import (
    OPERATION, prepare, setup as _setup, wheel,
)

setup = _setup
pytestmark = pytest.mark.subsystem


def test_in_place_interpreter_edit_after_plan_is_rejected(setup, monkeypatch):
    from row_bot.plugins import sandbox

    original = sandbox._run

    def run(argv, **kwargs):
        original(argv, **kwargs)
        if "--dry-run" in argv:
            target = Path(argv[0])
            before = target.stat()
            data = target.read_bytes()
            target.write_bytes(b"X" + data[1:])
            os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))

    monkeypatch.setattr(sandbox, "_run", run)
    result = prepare(setup)
    assert not result.ready
    assert len(setup[3]) == 2, "Changed interpreter must not run the installation command"


def test_shared_dependency_extras_reach_fixed_point(setup):
    setup[4][:] = [
        wheel(requires=["branch", "leaf[feature]"]),
        wheel("branch", "1", ["leaf"]),
        wheel("leaf", "1", ['tail; extra == "feature"']),
        wheel("tail", "1"),
    ]
    assert prepare(setup).ready


def test_missing_late_extra_dependency_fails_before_install(setup):
    setup[4][:] = [
        wheel(requires=["branch", "leaf[feature]"]),
        wheel("branch", "1", ["leaf"]),
        wheel("leaf", "1", ['tail; extra == "feature"']),
    ]
    assert not prepare(setup).ready
    assert len(setup[3]) == 2


def test_verified_retry_rejects_changed_candidate_without_reinstall(setup, monkeypatch):
    installer, state, _source, calls, _plan = setup
    original = state._atomic_json

    def fail_publish(path, data, restricted=False):
        if data.get("sample-plugin", {}).get("environment", {}).get("active_operation_id") == OPERATION:
            raise OSError("synthetic publication failure")
        return original(path, data, restricted)

    monkeypatch.setattr(state, "_atomic_json", fail_publish)
    assert not prepare(setup).ready
    assert state.get_plugin_environment_state("sample-plugin")["operations"][OPERATION]["stage"] == "verified"
    monkeypatch.setattr(state, "_atomic_json", original)
    environment = installer._generation_path("sample-plugin", OPERATION)
    (environment / "changed-package.py").write_text("synthetic = True\n")
    assert prepare(setup).error_code == "environment_changed"
    assert len(calls) == 3
    assert "active_operation_id" not in state.get_plugin_environment_state("sample-plugin")


def test_failed_replacement_publication_keeps_previous_active_and_new_verified(setup, monkeypatch):
    installer, state, _source, calls, _plan = setup
    assert prepare(setup).ready
    second = str(UUID(int=2))
    original = state._atomic_json

    def fail_publish(path, data, restricted=False):
        if data.get("sample-plugin", {}).get("environment", {}).get("active_operation_id") == second:
            raise OSError("synthetic publication failure")
        return original(path, data, restricted)

    monkeypatch.setattr(state, "_atomic_json", fail_publish)
    assert not prepare(setup, operation=second).ready
    saved = state.get_plugin_environment_state("sample-plugin")
    assert saved["active_operation_id"] == OPERATION
    assert saved["operations"][second]["stage"] == "verified"
    assert installer._generation_path("sample-plugin", OPERATION).is_dir()
    monkeypatch.setattr(state, "_atomic_json", original)
    count = len(calls)
    assert prepare(setup, operation=second).ready
    assert len(calls) == count


def test_loaded_state_external_edit_is_not_overwritten_by_environment_cas(setup):
    state = setup[1]
    state.set_plugin_config("sample-plugin", "keep", "before")
    saved = json.loads(state._STATE_PATH.read_text())
    external = copy.deepcopy(saved)
    external["sample-plugin"]["future"] = {"retained": True}
    state._STATE_PATH.write_text(json.dumps(external))
    with pytest.raises(ValueError, match="environment_state_changed"):
        state.set_plugin_environment_state("sample-plugin", {"operations": {}}, expected={})
    assert json.loads(state._STATE_PATH.read_text()) == external


def test_in_place_venv_config_edit_after_plan_is_rejected(setup, monkeypatch):
    from row_bot.plugins import sandbox

    original = sandbox._run

    def run(argv, **kwargs):
        original(argv, **kwargs)
        if "--dry-run" in argv:
            target = Path(argv[0]).parent.parent / "pyvenv.cfg"
            before = target.stat()
            data = target.read_bytes()
            # Semantically equivalent case change still changes reviewed bytes.
            target.write_bytes(data.replace(b"false", b"False"))
            os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))

    monkeypatch.setattr(sandbox, "_run", run)
    result = prepare(setup)
    assert not result.ready and len(setup[3]) == 2


def test_content_binding_rejects_oversized_file_before_read(setup, monkeypatch):
    from row_bot.plugins import sandbox

    root = setup[0].DATA_DIR
    path = root / "bounded-identity.bin"
    path.write_bytes(b"12345")
    original = Path.open

    def forbid_open(current, *args, **kwargs):
        if current == path:
            pytest.fail("Oversized target must be rejected before content allocation")
        return original(current, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbid_open)
    with pytest.raises(sandbox.EnvironmentError, match="environment_path_invalid"):
        sandbox._content_identity(root, path, 4)
