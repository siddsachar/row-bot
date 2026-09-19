from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import threading
from uuid import UUID

import pytest

from tests.subsystem.plugins.conftest import write_plugin

pytestmark = pytest.mark.subsystem

OPERATION = str(UUID(int=1))


def fake_venv(environment: Path) -> None:
    from row_bot.plugins import sandbox

    python = sandbox._interpreter_path(environment)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"isolated-fake-interpreter-not-executed")
    (environment / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    site = environment / ("Lib/site-packages" if os.name == "nt" else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    site.mkdir(parents=True)
    put_distribution(environment, "pip", "25.0")


def put_distribution(environment: Path, name: str, version: str) -> None:
    site = environment / ("Lib/site-packages" if os.name == "nt" else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    metadata = site / f"{name.replace('-', '_')}-{version}.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")


def wheel(name="addon", version="2.0", requires=()):
    return {"metadata": {"name": name, "version": version, "requires_dist": list(requires)},
            "download_info": {"url": f"https://fixture.invalid/{name}-{version}-py3-none-any.whl",
                              "archive_info": {"hashes": {"sha256": "a" * 64}}}}


@pytest.fixture
def setup(plugin_modules, tmp_path, monkeypatch):
    from row_bot.plugins import sandbox

    installer, state = plugin_modules["installer"], plugin_modules["state"]
    source = write_plugin(installer.PLUGINS_DIR)
    calls = []
    plan = [wheel()]

    def run(argv, *, cwd, timeout):
        calls.append(list(argv))
        if "venv" in argv:
            assert argv[:4] == [sys.executable, "-I", "-m", "venv"]
            assert "--copies" in argv
            fake_venv(Path(argv[-1]))
            return
        assert Path(argv[0]).name in {"python", "python.exe"}
        assert Path(argv[0]) != Path(sys.executable)
        assert "--isolated" in argv and "--only-binary=:all:" in argv
        assert argv[argv.index("--keyring-provider") + 1] == "disabled"
        environment = Path(argv[0]).parent.parent
        constraints = Path(argv[argv.index("--constraint") + 1]).read_text()
        assert "host-package==1.0" in constraints
        if "--dry-run" in argv:
            assert "--ignore-installed" in argv
            Path(argv[argv.index("--report") + 1]).write_text(json.dumps({"version": "1", "install": plan}))
        else:
            assert "--no-deps" in argv and "--require-hashes" in argv
            requirements = Path(argv[argv.index("--requirement") + 1]).read_text()
            assert "addon==2.0 --hash=sha256:" in requirements
            assert "host-package" not in requirements
            for item in plan:
                put_distribution(environment, item["metadata"]["name"], item["metadata"]["version"])

    run.original = sandbox._run
    monkeypatch.setattr(sandbox, "_run", run)
    monkeypatch.setattr(sandbox, "_get_core_requirements", lambda: {"host_package": "1.0"})
    return installer, state, source, calls, plan


def prepare(setup, *, operation=OPERATION, requirements=None, revision=None):
    installer = setup[0]
    return installer.prepare_plugin_environment(
        "sample-plugin", ["addon"] if requirements is None else requirements,
        expected_plugin_revision=revision or installer.get_plugin_source_revision("sample-plugin"),
        operation_id=operation,
    )


def test_preparation_one_plan_no_host_mutation_and_ready_retry(setup):
    installer, state, _, calls, _ = setup
    before_path = list(sys.path)
    result = prepare(setup)
    assert result.ready and result.changed and result.error_code is None
    assert len(calls) == 3
    assert not state.is_plugin_enabled("sample-plugin")
    assert state.get_plugin_environment_state("sample-plugin")["active_operation_id"] == OPERATION
    retry = prepare(setup)
    assert retry.ready and not retry.changed
    assert retry.environment_revision == result.environment_revision and len(calls) == 3
    assert sys.path == before_path
    assert installer._generation_path("sample-plugin", OPERATION).parent.name == OPERATION


@pytest.mark.parametrize("requirements", [["addon @ https://fixture.invalid/x.whl"], ["--target=x"], [""], ["x"] * 129, "addon"])
def test_invalid_requirements_have_no_process_or_candidate(setup, requirements):
    result = prepare(setup, requirements=requirements)
    assert not result.ready and not setup[3]
    assert not (setup[0].DATA_DIR / "plugin_environments").exists()


@pytest.mark.parametrize("identifier", ["../sample-plugin", "sample-plugin/x", "C:/foreign", "a", "UPPER"])
def test_invalid_plugin_identity_never_reads_or_allocates(setup, identifier):
    result = setup[0].prepare_plugin_environment(identifier, ["addon"], expected_plugin_revision="sha256:" + "0" * 64, operation_id=OPERATION)
    assert not result.ready and result.error_code == "invalid_plugin_id" and not setup[3]


def test_stale_source_rejected_before_process(setup):
    revision = setup[0].get_plugin_source_revision("sample-plugin")
    (setup[2] / "plugin_main.py").write_text("changed = True\n")
    assert prepare(setup, revision=revision).error_code == "plugin_changed"
    assert not setup[3]


def test_source_change_during_install_never_activates(setup, monkeypatch):
    from row_bot.plugins import sandbox

    run = sandbox._run
    def change(argv, **kwargs):
        run(argv, **kwargs)
        if "--require-hashes" in argv:
            (setup[2] / "plugin_main.py").write_text("changed = True\n")
    monkeypatch.setattr(sandbox, "_run", change)
    result = prepare(setup)
    assert not result.ready and result.error_code == "plugin_changed"
    assert "active_operation_id" not in setup[1].get_plugin_environment_state("sample-plugin")


@pytest.mark.parametrize("mode", ["missing", "conflict", "transitive", "duplicate", "hash", "source", "unrelated", "malformed", "version", "oversized"])
def test_bad_plan_never_reaches_install(setup, monkeypatch, mode):
    from row_bot.plugins import sandbox

    plan = setup[4]
    if mode == "missing":
        plan.clear()
    elif mode == "conflict":
        plan[:] = [wheel(), wheel("host-package", "2.0")]
    elif mode == "transitive":
        plan[:] = [wheel(requires=["host-package>=2"])]
    elif mode == "duplicate":
        plan.append(copy.deepcopy(plan[0]))
    elif mode == "hash":
        plan[0]["download_info"]["archive_info"]["hashes"].clear()
    elif mode == "source":
        plan[0]["download_info"]["url"] = "https://fixture.invalid/addon.tar.gz"
    elif mode == "unrelated":
        plan.append(wheel("unrequested"))
    original = sandbox._run
    def run(argv, **kwargs):
        original(argv, **kwargs)
        if "--dry-run" in argv and mode in {"malformed", "version", "oversized"}:
            Path(argv[argv.index("--report") + 1]).write_text({"malformed": "[", "version": '{"version":"2","install":[]}', "oversized": " " * (sandbox._MAX_REPORT_BYTES + 1)}[mode])
    monkeypatch.setattr(sandbox, "_run", run)
    assert not prepare(setup).ready
    assert len(setup[3]) == 2
    assert not any("--require-hashes" in argv for argv in setup[3])


def test_markers_and_extras_require_complete_transitive_closure(setup):
    setup[4][:] = [wheel(requires=['leaf==1; extra == "feature"', 'ignored; python_version < "1"']), wheel("leaf", "1")]
    result = prepare(setup, requirements=["addon[feature]"])
    assert result.ready


def test_unrequested_transitive_url_rejected_even_in_inactive_marker(setup):
    setup[4][0]["metadata"]["requires_dist"] = ['leaf @ https://fixture.invalid/x.whl; python_version < "1"']
    assert not prepare(setup).ready and len(setup[3]) == 2


def test_failed_replacement_retains_ready_generation_and_outcome(setup, monkeypatch):
    from row_bot.plugins import sandbox

    original = prepare(setup)
    def failure(*args, **kwargs):
        raise sandbox.EnvironmentError("environment_process_failed")
    monkeypatch.setattr(sandbox, "_run", failure)
    failed = prepare(setup, operation=str(UUID(int=2)))
    assert not failed.ready
    saved = setup[1].get_plugin_environment_state("sample-plugin")
    assert saved["active_operation_id"] == original.operation_id
    assert setup[0]._generation_path("sample-plugin", OPERATION).is_dir()
    retry = prepare(setup, operation=str(UUID(int=2)))
    assert retry == failed


def test_missing_distribution_after_install_never_ready(setup, monkeypatch):
    from row_bot.plugins import sandbox

    actual = sandbox._run
    def run(argv, **kwargs):
        if "--require-hashes" not in argv:
            actual(argv, **kwargs)
    monkeypatch.setattr(sandbox, "_run", run)
    result = prepare(setup)
    assert not result.ready and result.error_code == "environment_verification_failed"


def test_verified_candidate_publication_retry_does_not_reinstall(setup, monkeypatch):
    state = setup[1]
    actual = state._atomic_json
    def failure(path, data, restricted=False):
        if data.get("sample-plugin", {}).get("environment", {}).get("active_operation_id") == OPERATION:
            raise OSError("synthetic replace failure")
        actual(path, data, restricted)
    monkeypatch.setattr(state, "_atomic_json", failure)
    first = prepare(setup)
    assert not first.ready
    assert state.get_plugin_environment_state("sample-plugin")["operations"][OPERATION]["stage"] == "verified"
    assert len(setup[3]) == 3
    monkeypatch.setattr(state, "_atomic_json", actual)
    result = prepare(setup)
    assert result.ready and result.changed and len(setup[3]) == 3


def test_interrupted_preparation_is_not_replayed(setup):
    state = setup[1]
    revision = setup[0].get_plugin_source_revision("sample-plugin")
    import hashlib

    request = hashlib.sha256(json.dumps([revision, ["addon"]], separators=(",", ":")).encode()).hexdigest()
    state.set_plugin_environment_state("sample-plugin", {"operations": {OPERATION: {"stage": "preparing", "request_revision": request}}}, expected={})
    assert prepare(setup).error_code == "operation_incomplete"
    assert not setup[3]


def test_operation_cannot_be_rebound_to_new_requirements(setup):
    assert prepare(setup).ready
    assert prepare(setup, requirements=["different"]).error_code == "operation_conflict"
    assert len(setup[3]) == 3


def test_edited_generation_not_adopted_on_retry(setup):
    assert prepare(setup).ready
    (setup[0]._generation_path("sample-plugin", OPERATION) / "edited.txt").write_text("new content")
    assert prepare(setup).error_code == "environment_changed"
    assert len(setup[3]) == 3


def test_preexisting_candidate_not_adopted(setup):
    path = setup[0].DATA_DIR / "plugin_environments/sample-plugin" / OPERATION
    path.mkdir(parents=True)
    (path / "sentinel").write_text("owned elsewhere")
    assert not prepare(setup).ready and not setup[3]
    assert (path / "sentinel").read_text() == "owned elsewhere"


def test_metadata_reads_never_probe_or_load_secrets(setup, monkeypatch):
    state = setup[1]
    def forbidden(*args, **kwargs):
        raise AssertionError("Metadata read performed effectful initialization")
    monkeypatch.setattr(state, "_ensure_loaded", forbidden)
    assert state.get_plugin_environment_state("sample-plugin") == {}
    assert setup[0].get_plugin_source_revision("sample-plugin").startswith("sha256:")
    assert not setup[3]


def test_atomic_environment_update_preserves_config_enabled_and_unknown_fields(setup):
    state = setup[1]
    state.set_plugin_config("sample-plugin", "choice", "retained")
    state.set_plugin_enabled("sample-plugin", True)
    state._state["sample-plugin"]["future_metadata"] = {"kept": 1}
    state._save_state()
    assert prepare(setup).ready
    state._reset()
    assert state.get_plugin_config("sample-plugin", "choice") == "retained"
    assert state.is_plugin_enabled("sample-plugin")
    assert state._state["sample-plugin"]["future_metadata"] == {"kept": 1}


def test_concurrent_config_enablement_and_preparation_keep_all_fields(setup, monkeypatch):
    from row_bot.plugins import sandbox

    started, release = threading.Event(), threading.Event()
    original = sandbox._run
    def run(argv, **kwargs):
        if "--dry-run" in argv:
            started.set()
            assert release.wait(5)
        original(argv, **kwargs)
    monkeypatch.setattr(sandbox, "_run", run)
    results = []
    worker = threading.Thread(target=lambda: results.append(prepare(setup)))
    worker.start()
    try:
        assert started.wait(5)
        setup[1].set_plugin_config("sample-plugin", "concurrent", 3)
        setup[1].set_plugin_enabled("sample-plugin", True)
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and results[0].ready
    setup[1]._reset()
    assert setup[1].get_plugin_config("sample-plugin", "concurrent") == 3
    assert setup[1].is_plugin_enabled("sample-plugin")
    assert setup[1].get_plugin_environment_state("sample-plugin")["active_operation_id"] == OPERATION


def test_atomic_replace_failure_retains_old_file_and_cache(setup, monkeypatch):
    state = setup[1]
    state.set_plugin_config("sample-plugin", "retained", 1)
    before = state._STATE_PATH.read_bytes()
    def fail(*args, **kwargs):
        raise OSError("synthetic replace failure")
    monkeypatch.setattr(state.os, "replace", fail)
    with pytest.raises(ValueError, match="environment_state_unavailable"):
        state.set_plugin_environment_state("sample-plugin", {"active_operation_id": OPERATION}, expected={})
    assert state._STATE_PATH.read_bytes() == before
    assert "environment" not in state._state["sample-plugin"]
    assert not list(state.DATA_DIR.glob(".plugin_state.json-*.tmp"))


def test_corrupt_state_is_retained_without_install(setup):
    state = setup[1]
    state._STATE_PATH.write_bytes(b"{broken")
    assert not prepare(setup).ready
    assert state._STATE_PATH.read_bytes() == b"{broken"
    assert not setup[3]


def test_host_and_foreign_environment_have_no_fallback(setup, tmp_path):
    from row_bot.plugins import sandbox

    assert not sandbox.install_dependencies(["addon"])[0]
    assert not sandbox.install_dependencies(["addon"], environment=Path(sys.prefix))[0]
    assert not sandbox.install_dependencies(["addon"], environment=tmp_path / "foreign")[0]
    assert not setup[3]


def test_empty_requirements_create_only_isolated_bootstrap(setup):
    assert prepare(setup, requirements=[]).ready
    assert len(setup[3]) == 1 and "venv" in setup[3][0]


def test_source_link_rejected_before_process(setup, monkeypatch):
    from row_bot.plugins import sandbox

    revision = setup[0].get_plugin_source_revision("sample-plugin")
    actual = sandbox._no_link
    def linked(path):
        if path.name == "plugin_main.py":
            raise sandbox.EnvironmentError("environment_path_invalid")
        return actual(path)
    monkeypatch.setattr(sandbox, "_no_link", linked)
    assert not prepare(setup, revision=revision).ready and not setup[3]


def test_published_generation_cannot_be_modified_by_compatibility_installer(setup):
    from row_bot.plugins import sandbox

    assert prepare(setup).ready
    environment = setup[0]._generation_path("sample-plugin", OPERATION)
    assert sandbox.install_dependencies(["different"], environment=environment) == (False, "environment_already_published")
    assert len(setup[3]) == 3


def test_actual_linked_owner_can_prepare_without_copying_source(setup, tmp_path):
    from row_bot.plugins import devtools

    linked = write_plugin(tmp_path / "linked", "sample-plugin")
    devtools.LINKS_PATH.write_text(json.dumps({"sample-plugin": str(linked)}))
    assert setup[0]._source_for_preparation("sample-plugin") == linked
    before = sorted(linked.rglob("*"))
    assert prepare(setup).ready
    assert sorted(linked.rglob("*")) == before
    assert setup[0]._generation_path("sample-plugin", OPERATION).is_relative_to(setup[0].DATA_DIR)


def test_environment_path_reparse_is_rejected_before_process(setup, monkeypatch):
    from row_bot.plugins import sandbox
    from types import SimpleNamespace

    root = setup[0].DATA_DIR / "plugin_environments"
    root.mkdir()
    actual = Path.lstat
    def lstat(path, *args, **kwargs):
        value = actual(path, *args, **kwargs)
        if path == root:
            return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400)
        return value
    monkeypatch.setattr(Path, "lstat", lstat)
    assert not prepare(setup).ready and not setup[3]
    with pytest.raises(sandbox.EnvironmentError, match="environment_path_invalid"):
        sandbox._no_link(root)


def test_target_interpreter_replaced_after_resolution_is_not_executed(setup, monkeypatch):
    from row_bot.plugins import sandbox

    original = sandbox._run
    def run(argv, **kwargs):
        original(argv, **kwargs)
        if "--dry-run" in argv:
            target = Path(argv[0])
            target.rename(target.with_name("retained-python"))
            target.write_bytes(b"replacement")
    monkeypatch.setattr(sandbox, "_run", run)
    assert not prepare(setup).ready and len(setup[3]) == 2


def test_host_constraint_change_after_plan_prevents_install(setup, monkeypatch):
    from row_bot.plugins import sandbox

    count = 0
    def core():
        nonlocal count
        count += 1
        return {"host-package": "1.0" if count == 1 else "1.1"}
    monkeypatch.setattr(sandbox, "_get_core_requirements", core)
    assert prepare(setup).error_code == "dependency_core_conflict"
    assert len(setup[3]) == 2


def test_failed_ordinary_save_is_not_overwritten_by_readiness_publication(setup, monkeypatch):
    state = setup[1]
    state.set_plugin_config("sample-plugin", "setting", "old")
    original = state._atomic_json
    def failed(*args, **kwargs):
        raise OSError("synthetic replace error")
    monkeypatch.setattr(state, "_atomic_json", failed)
    assert state.set_plugin_config("sample-plugin", "setting", "new") is None
    monkeypatch.setattr(state, "_atomic_json", original)
    with pytest.raises(ValueError, match="environment_state_changed"):
        state.set_plugin_environment_state("sample-plugin", {"operations": {}}, expected={})
    assert state.get_plugin_config("sample-plugin", "setting") == "new"
    assert json.loads(state._STATE_PATH.read_text())["sample-plugin"]["config"]["setting"] == "old"


def test_duplicate_concurrent_prepare_installs_only_once(setup, monkeypatch):
    from row_bot.plugins import sandbox

    started, release = threading.Event(), threading.Event()
    original = sandbox._run
    def run(argv, **kwargs):
        if "--dry-run" in argv:
            started.set()
            assert release.wait(5)
        original(argv, **kwargs)
    monkeypatch.setattr(sandbox, "_run", run)
    results = []
    workers = [threading.Thread(target=lambda: results.append(prepare(setup))) for _ in range(2)]
    workers[0].start()
    try:
        assert started.wait(5)
        workers[1].start()
    finally:
        release.set()
        for worker in workers:
            if worker.ident is not None:
                worker.join(5)
    assert all(not worker.is_alive() for worker in workers)
    assert len(results) == 2 and all(result.ready for result in results)
    assert sum(result.changed for result in results) == 1 and len(setup[3]) == 3


def test_explicit_preparation_cancellation_does_not_publish(setup, monkeypatch):
    import row_bot.cancellation as cancellation

    class Cancelled:
        def is_cancelled(self):
            return True
    monkeypatch.setattr(cancellation, "current_cancellation_scope", lambda: Cancelled())
    result = prepare(setup)
    assert not result.ready and result.error_code == "cancelled" and not setup[3]


@pytest.mark.parametrize("timeout", [False, True])
def test_process_adapter_uses_sanitized_environment_bounded_waits_and_no_output(setup, monkeypatch, timeout):
    from row_bot.plugins import sandbox
    import row_bot.process_cancellation as processes
    import subprocess

    # Use the real adapter retained by the fixture; Popen remains a fake.
    actual_run = sandbox._run.original
    calls, waits, stops = [], [], []
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-pass")
    class Process:
        done = False
        def wait(self, *, timeout):
            waits.append(timeout)
            if len(waits) == 1 and timeout == 60 and not self.done and timeout_mode:
                raise subprocess.TimeoutExpired("fake", timeout)
            self.done = True
            return 0
        def poll(self):
            return 0 if self.done else None
        def kill(self):
            self.done = True
    timeout_mode = timeout
    process = Process()
    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return process
    monkeypatch.setattr(sandbox.subprocess, "Popen", popen)
    monkeypatch.setattr(processes, "request_process_stop", lambda child: stops.append(child))
    if timeout:
        with pytest.raises(sandbox.EnvironmentError, match="environment_process_timeout"):
            actual_run(["fake-interpreter"], cwd=setup[0].DATA_DIR, timeout=60)
    else:
        actual_run(["fake-interpreter"], cwd=setup[0].DATA_DIR, timeout=60)
    assert all(value is not None and value <= 60 for value in waits)
    assert len(calls) == 1
    options = calls[0][1]
    assert options["shell"] is False
    assert options["stdout"] == options["stderr"] == subprocess.DEVNULL
    assert "UNRELATED_SECRET" not in options["env"]
    assert options["env"]["PIP_CONFIG_FILE"] == os.devnull
    assert options["env"]["HOME"] == options["env"]["USERPROFILE"] == str(setup[0].DATA_DIR)
    assert Path(options["env"]["NETRC"]).parent == setup[0].DATA_DIR
    assert bool(stops) is timeout


def test_source_enumeration_stops_at_budget_before_materializing_huge_directory(setup, monkeypatch):
    original = Path.iterdir
    reads = 0
    def entries(path):
        if path != setup[2]:
            yield from original(path)
            return
        nonlocal reads
        for _ in range(100000):
            reads += 1
            yield path / "plugin_main.py"
    monkeypatch.setattr(Path, "iterdir", entries)
    with pytest.raises(ValueError, match="environment_capacity_exceeded"):
        setup[0]._tree_revision(setup[2], source=True)
    assert reads == 8193
    assert not setup[3]
