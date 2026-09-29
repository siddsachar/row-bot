from __future__ import annotations

import subprocess

import pytest

import scripts.run_test_matrix as matrix


pytestmark = [pytest.mark.subsystem, pytest.mark.installer]


def test_the_pr_lane_runs_every_app_test_once() -> None:
    pytest_runs = [spec for spec in matrix.commands_for_tier("pr") if "pytest" in spec.argv]

    assert [spec.name for spec in pytest_runs] == ["python"]
    argv = pytest_runs[0].argv
    assert argv[argv.index("pytest") + 1] == "tests"
    assert "not slow and not live_provider and not e2e" in argv


def test_shards_split_the_test_files_without_dropping_any() -> None:
    from tests.conftest import shard_files

    files = {f"tests/area_{area}/test_{number}.py" for area in "abc" for number in range(7)}
    shards = [shard_files(files, f"{index}/3") for index in (1, 2, 3)]

    assert set().union(*shards) == files
    assert sum(len(shard) for shard in shards) == len(files)
    assert max(map(len, shards)) - min(map(len, shards)) <= 1
    with pytest.raises(ValueError):
        shard_files(files, "4/3")


def test_changed_tier_expands_source_test_map() -> None:
    specs = matrix.commands_for_tier("changed", changed_files=["src/row_bot/providers/runtime.py"])

    changed = next(spec for spec in specs if spec.name == "changed-tests")
    assert "tests/contracts/test_provider_contract.py" in changed.argv
    assert "tests/subsystem/providers" in changed.argv
    assert changed.env["ROW_BOT_TEST_MODE"] == "1"


def test_changed_frontend_selects_node_checks_and_backend_contracts() -> None:
    specs = matrix.commands_for_tier("changed", changed_files=["frontend/src/api/http.ts"])
    assert "client-foundation" in [spec.name for spec in specs]
    changed = next(spec for spec in specs if spec.name == "changed-tests")
    assert "tests/subsystem/client_host" in changed.argv
    assert "tests/subsystem/client_protocol" in changed.argv


def test_client_checks_never_install_and_fail_fast(tmp_path, monkeypatch) -> None:
    import scripts.run_client_checks as client

    frontend = tmp_path / "frontend"
    compiler = frontend / "node_modules/typescript/bin/tsc"
    compiler.parent.mkdir(parents=True)
    compiler.touch()
    monkeypatch.setattr(client, "FRONTEND", frontend)
    monkeypatch.setattr(client.shutil, "which", lambda _name: "fixture-node")
    monkeypatch.setenv("VITE_ENABLE_FIXTURES", "1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://invalid.example")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 7)

    monkeypatch.setattr(client.subprocess, "run", run)
    assert client.main([]) == 7
    assert len(calls) == 1
    assert "VITE_ENABLE_FIXTURES" not in calls[0][1]["env"]
    assert not any(key.startswith("OTEL_") for key in calls[0][1]["env"])
    assert not any("install" in argument or "npm" in argument for command in client.check_commands() for argument in command)
    assert len(client.check_commands(True)) < len(client.check_commands())


def test_changed_files_include_committed_worktree_and_untracked_changes(monkeypatch) -> None:
    outputs = {
        ("git", "diff", "--name-only", "origin/main...HEAD"): "committed.py\nshared.py\n",
        ("git", "diff", "--name-only", "HEAD"): "working.py\nshared.py\n",
        ("git", "ls-files", "--others", "--exclude-standard"): "untracked.py\n",
    }

    def fake_run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=outputs[tuple(argv)], stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert matrix.changed_files_from_git("origin/main") == [
        "committed.py",
        "shared.py",
        "working.py",
        "untracked.py",
    ]


def test_dry_run_main_does_not_execute(monkeypatch, capsys) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("dry-run should not execute commands"))

    assert matrix.main(["contracts", "--dry-run"]) == 0
    assert "tests/contracts" in capsys.readouterr().out


def test_run_commands_stops_on_first_failure(monkeypatch) -> None:
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 7)

    monkeypatch.setattr(subprocess, "run", fake_run)

    code = matrix.run_commands([matrix.COMMANDS["contracts"], matrix.COMMANDS["subsystem"]], continue_on_failure=False)

    assert code == 7
    assert len(calls) == 1
